#!/usr/bin/env bash
set -u

platform=${1:?platform required}
output="release-ui-evidence-$platform"
mkdir -p "$output"
cp scripts/release/sources.json "$output/sources.json" || exit 1
cp sotf/crates/sotf-dev-driver/suites/plugin_workflow_ui.toml "$output/plugin_workflow_ui.toml" || exit 1
{
    date -u
    uname -a
    printf 'DISPLAY=%s\n' "${DISPLAY:-}"
    printf 'WAYLAND_DISPLAY=%s\n' "${WAYLAND_DISPLAY:-}"
    python3 --version
    rustc --version
    cargo --version
    just --version
} >"$output/toolchain-display.txt" 2>&1
failed=0

source_state() {
    python3 - "$output/source-before.json" "$output/source-after.json" "$1" <<'PY'
import hashlib
import json
import pathlib
import subprocess
import sys

root = pathlib.Path.cwd()
before_path, after_path = map(pathlib.Path, sys.argv[1:3])
mode = sys.argv[3]
sources = json.loads((root / "scripts/release/sources.json").read_text())["sources"]
state = {}
for name, entry in sources.items():
    path = root / name
    revision = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    working_status = subprocess.check_output(
        ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=all"],
        text=True,
    ).splitlines()
    state[name] = {
        "revision": revision,
        "working_status": working_status,
        "lock_sha256": hashlib.sha256((path / "Cargo.lock").read_bytes()).hexdigest(),
    }
    if name == "autoeq":
        nested_lock = path / "crates" / "autoeq-gpui-examples" / "Cargo.lock"
        if not nested_lock.is_file():
            raise SystemExit("autoeq: nested GPUI examples Cargo.lock is missing")
        state[name]["nested_lock_sha256"] = hashlib.sha256(nested_lock.read_bytes()).hexdigest()
    if mode == "before" and (revision != entry["revision"] or working_status):
        raise SystemExit(f"{name}: source differs from clean pinned revision")
destination = before_path if mode == "before" else after_path
destination.write_text(json.dumps(state, indent=2) + "\n")
if mode == "after" and state != json.loads(before_path.read_text()):
    raise SystemExit("Pinned source revision, working status, or Cargo.lock changed")
PY
}

source_state before || exit 1

run_check() {
    local name=$1 workspace=$2 required_test=$3
    shift 3
    local log="$output/$name.log"
    printf '[%s] %s\n' "$name" "$*"
    (cd "$workspace" && "$@") >"$log" 2>&1
    local status=$?
    if [ "$status" -eq 0 ] && [ "$required_test" = yes ] && ! grep -Eq 'test result: ok\. [1-9][0-9]* passed' "$log"; then
        printf 'No matching test executed\n' >>"$log"
        status=1
    fi
    printf '[%s] exit %s; log: %s\n' "$name" "$status" "$log"
    if [ "$status" -ne 0 ]; then
        tail -n 80 "$log"
        failed=1
    fi
    return "$status"
}

run_check toolkit-horizontal-accordion gpui-toolkit yes cargo test -p gpui-ui-kit --test integration_tests horizontal_accordion_content_spans_header_width
run_check toolkit-side-accordion gpui-toolkit yes cargo test -p gpui-ui-kit --test integration_tests side_accordion_places_tabs_on_both_sides_of_content
run_check toolkit-wizard-width gpui-toolkit yes cargo test -p gpui-ui-kit --test integration_tests wizard_step_labels_follow_rendered_viewport_width
if [ "$platform" = linux ]; then
    run_check sotf-linux-screenshot-fallback sotf yes cargo test -p sotf-dev-driver --bin sotf-dev-driver linux_screenshot_fallback_tests
fi
if run_check sotf-dev-driver-build sotf no just dev-driver-build-gpui; then
    run_check sotf-plugin-workflow-ui sotf no cargo run -p sotf-dev-driver -- run-suite crates/sotf-dev-driver/suites/plugin_workflow_ui.toml -v
    if [ "$platform" = linux ]; then
        run_check sotf-atspi-session-bus sotf no /usr/bin/python3 ../scripts/release/atspi_smoke.py
    fi
else
    printf 'NOT_RUN: sotf-dev-driver-build failed\n' | tee "$output/sotf-plugin-workflow-ui.log"
fi

git -C sotf status --porcelain -- Cargo.lock >"$output/sotf-lock-status.txt"
if [ -s "$output/sotf-lock-status.txt" ]; then
    printf 'SOTF Cargo.lock changed during UI QA\n' | tee -a "$output/sotf-lock-status.txt"
    failed=1
fi
git -C gpui-toolkit status --porcelain -- Cargo.lock >"$output/toolkit-lock-status.txt"
if [ -s "$output/toolkit-lock-status.txt" ]; then
    printf 'GPUI toolkit Cargo.lock changed during UI QA\n' | tee -a "$output/toolkit-lock-status.txt"
    failed=1
fi

source_state after || failed=1

exit "$failed"
