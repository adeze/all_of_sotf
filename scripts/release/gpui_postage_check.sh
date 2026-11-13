#!/usr/bin/env bash
set -u

platform=${1:?macos or linux required}
output=${2:?evidence directory required}
if [ "$platform" != macos ] && [ "$platform" != linux ]; then
    printf 'unsupported platform: %s\n' "$platform" >&2
    exit 2
fi
mkdir -p "$output" || exit 1
cp scripts/release/sources.json "$output/sources.json" || exit 1
{
    date -u
    uname -a
    python3 --version
    rustc --version
    cargo --version
} >"$output/toolchain.txt" 2>&1
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
    tracked = subprocess.check_output(["git", "-C", str(path), "diff", "--name-only", "HEAD"], text=True).splitlines()
    state[name] = {
        "revision": revision,
        "tracked_status": tracked,
        "lock_sha256": hashlib.sha256((path / "Cargo.lock").read_bytes()).hexdigest(),
    }
    if mode == "before" and (revision != entry["revision"] or tracked):
        raise SystemExit(f"{name}: source differs from clean pinned revision")
destination = before_path if mode == "before" else after_path
destination.write_text(json.dumps(state, indent=2) + "\n")
if mode == "after" and state != json.loads(before_path.read_text()):
    raise SystemExit("Pinned source revision, tracked status, or Cargo.lock changed")
PY
}

source_state before || exit 1

run_check() {
    local name=$1
    local workspace=$2
    shift 2
    local log="$output/$name.log"
    printf '[%s] %s\n' "$name" "$*"
    (cd "$workspace" && "$@") 2>&1 | tee "$log"
    local -a pipeline_status=("${PIPESTATUS[@]}")
    local status=${pipeline_status[0]}
    if [ "${pipeline_status[1]}" -ne 0 ]; then
        printf '[%s] could not retain complete output in %s\n' "$name" "$log" >&2
        failed=1
    fi
    printf '[%s] exit %s; log: %s\n' "$name" "$status" "$log"
    if [ "$status" -ne 0 ]; then
        failed=1
    elif [ "$name" = toolkit-importer-tests ] && ! grep -Eq '^Ran [1-9][0-9]* tests? in ' "$log"; then
        printf '[%s] no tests executed\n' "$name" >&2
        failed=1
    elif [ "$name" != gpui-all-targets ] && [ "$name" != toolkit-importer-tests ] && ! grep -Eq '^running [1-9][0-9]* tests?$' "$log"; then
        printf '[%s] no tests executed\n' "$name" >&2
        failed=1
    fi
}

run_check capture-full-tests sotf-capture cargo test
run_check toolkit-util-tests gpui-toolkit cargo test -p gpui-toolkit-util
run_check gpui-notification-condition gpui-toolkit cargo test -p gpui-toolkit-gpui --features test-support --lib test_entity_notification_and_condition_wake
run_check gpui-app-tests gpui-toolkit cargo test -p gpui-toolkit-gpui --features test-support --lib
run_check gpui-all-targets gpui-toolkit cargo check -p gpui-toolkit-gpui --features test-support --all-targets
run_check toolkit-vendored-inventory gpui-toolkit cargo test -p gpui-release-gates vendored_patches
run_check toolkit-importer-tests gpui-toolkit env PYTHONPATH=scripts python3 -m unittest discover -s scripts/tests -p test_import_gpui_upstream.py

source_state after || failed=1
exit "$failed"
