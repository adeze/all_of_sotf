#!/usr/bin/env bash
set -Eeuo pipefail

platform=${1:?macos or linux required}
output=${2:?evidence directory required}
candidate_rev=${3:?immutable downloader commit required}
case "$platform" in macos|linux) ;; *) echo "unsupported platform: $platform" >&2; exit 2 ;; esac
[[ "$candidate_rev" =~ ^[0-9a-f]{40}$ ]] || { echo 'candidate revision must be a full SHA' >&2; exit 2; }

readonly base_rev=be243f5bb82b7a0d79e3c9ff886f469729397171
readonly reviewed_patch_sha=787fc007c481858115d664bc69f780f3de00a68534f7df1598505d2470e6fd6a
readonly fork_url=https://github.com/pierreaubert/webdriver-downloader.git
readonly fork_branch=ci/webdriver-downloader-compat-20261004
readonly browser_sha=ff43322f335e436b2f4dcdfeeec5db032299e335a7e8c1c618b326e100ce8732
readonly browser_bytes=196202491

mkdir -p "$output"
output=$(cd "$output" && pwd)
cp scripts/release/sources.json "$output/sources.json"
{
    date -u
    uname -a
    python3 --version
    rustc --version
    cargo --version
} >"$output/toolchain.txt" 2>&1

source_state() {
    python3 - "$output" "$platform" "$1" <<'PY'
import json
from pathlib import Path
import sys

from scripts.release.qa import ROOT, source_issues, source_state, workspace_map

output, platform, phase = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
state = source_state(ROOT, list(workspace_map()), platform)
(output / f"sources-{phase}.json").write_text(json.dumps(state, indent=2) + "\n")
before = json.loads((output / "sources-before.json").read_text()) if phase == "after" else state
issues = source_issues(before, state, require_clean=True)
manifest = json.loads((ROOT / "scripts/release/sources.json").read_text())["sources"]
for name, pinned in manifest.items():
    if state.get(name, {}).get("revision") != pinned["revision"]:
        issues.append(f"{name}: checkout differs from pinned sources.json revision")
(output / f"source-issues-{phase}.json").write_text(json.dumps(issues, indent=2) + "\n")
for issue in issues:
    print(f"SOURCE GUARD: {issue}", file=sys.stderr)
raise SystemExit(bool(issues))
PY
}

source_state before
work=$(mktemp -d "${TMPDIR:-/tmp}/webdriver-candidate.XXXXXX")
candidate="$work/fork-candidate"
active_supervisor=
finish() {
    status=$?
    trap - EXIT
    if [ -n "$active_supervisor" ]; then
        kill -TERM "$active_supervisor" 2>/dev/null || true
        for _ in {1..120}; do
            kill -0 "$active_supervisor" 2>/dev/null || break
            sleep 0.1
        done
        kill -KILL "$active_supervisor" 2>/dev/null || true
        wait "$active_supervisor" 2>/dev/null || true
        if kill -0 "$active_supervisor" 2>/dev/null; then
            echo 'owned command supervisor did not terminate' >&2
            status=1
        fi
    fi
    if [ -d "$work/plotly-smoke" ]; then
        if [ -f "$work/plotly-smoke/plot.png" ]; then
            cp "$work/plotly-smoke/plot.png" "$output/plot-at-exit.png" || status=1
        fi
        python3 - "$work/plotly-smoke/plot.png" "$work/private-home/bin/chromedriver" \
            "$output/smoke-progress.json" <<'PY' || status=1
import json
from pathlib import Path
import sys

image, driver, report = map(Path, sys.argv[1:])
report.write_text(json.dumps({
    "png_exists": image.is_file(),
    "png_bytes": image.stat().st_size if image.is_file() else None,
    "private_chromedriver_exists": driver.is_file(),
    "private_chromedriver_bytes": driver.stat().st_size if driver.is_file() else None,
}, indent=2) + "\n")
PY
    fi
    python3 - "$output" "$status" <<'PY' || status=1
import json
from pathlib import Path
import sys

output, exit_code = Path(sys.argv[1]), int(sys.argv[2])
commands = {}
for path in sorted(output.glob("*.status.json")):
    commands[path.name] = json.loads(path.read_text())
(output / "supervisor-report.json").write_text(json.dumps({
    "script_exit_before_reporting": exit_code,
    "commands": commands,
}, indent=2) + "\n")
PY
    if [ -d "$candidate/.git" ]; then
        if [ "$(git -C "$candidate" rev-parse HEAD)" != "$candidate_rev" ] ||
            [ -n "$(git -C "$candidate" status --porcelain)" ]; then
            echo 'candidate fork revision or clean-state guard failed' >&2
            status=1
        fi
    fi
    source_state after || status=1
    rm -rf -- "$work" || status=1
    exit "$status"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cat >"$work/supervise.py" <<'PY'
import os
import json
import ctypes
from pathlib import Path
import signal
import subprocess
import sys
import time

log_path, workdir, timeout_text, *command = sys.argv[1:]
timeout_seconds = int(timeout_text)
status_path = Path(log_path).with_suffix(".status.json")
started = time.monotonic()
status = {"timeout_seconds": timeout_seconds, "timed_out": False,
          "child_exit_code": None, "cleanup_ok": None, "events": [], "reaped_descendants": []}

if sys.platform.startswith("linux"):
    # ChromeDriver starts Chrome grandchildren. Become their private reaper so
    # container PID 1 cannot leave killed descendants as permanent zombies.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        error = ctypes.get_errno()
        status_path.write_text(json.dumps({"subreaper_error": os.strerror(error)}) + "\n")
        raise OSError(error, "PR_SET_CHILD_SUBREAPER failed")

def group_members(pgid):
    members = []
    if not Path("/proc").is_dir():
        return members
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            raw = (entry / "stat").read_text()
            tail = raw[raw.rfind(")") + 2:].split()
            state, ppid, group = tail[0], int(tail[1]), int(tail[2])
            if group == pgid:
                members.append({"pid": int(entry.name), "ppid": ppid,
                                "state": state, "comm": raw[raw.find("(") + 1:raw.rfind(")")]})
        except (OSError, ValueError, IndexError):
            continue
    return sorted(members, key=lambda member: member["pid"])

def snapshot(label, child):
    status["events"].append({"phase": label,
                             "elapsed_seconds": round(time.monotonic() - started, 3),
                             "child_exit_code": child.poll(),
                             "members": group_members(child.pid)})

with Path(log_path).open("wb") as log:
    child = subprocess.Popen(
        command, cwd=workdir, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
    )

    def group_alive():
        try:
            os.killpg(child.pid, 0)
        except ProcessLookupError:
            return False
        return True

    def reap_descendants():
        # subprocess owns the direct child and its exit code. Reap adopted
        # grandchildren only after poll() confirms that child was reaped.
        if not sys.platform.startswith("linux") or child.poll() is None:
            return
        while True:
            try:
                pid, wait_status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                return
            status["reaped_descendants"].append({"pid": pid, "wait_status": wait_status})

    def stop_group():
        snapshot("before_cleanup", child)
        for kind in (signal.SIGTERM, signal.SIGKILL):
            child.poll()
            reap_descendants()
            if not group_alive():
                snapshot("group_gone", child)
                return True
            try:
                os.killpg(child.pid, kind)
            except ProcessLookupError:
                snapshot("group_gone", child)
                return True
            snapshot("after_SIGTERM" if kind == signal.SIGTERM else "after_SIGKILL", child)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                child.poll()
                reap_descendants()
                if not group_alive():
                    snapshot("group_gone", child)
                    return True
                time.sleep(0.1)
        child.poll()
        reap_descendants()
        snapshot("cleanup_deadline", child)
        return not group_alive()

    def on_signal(signum, _frame):
        signal.signal(signum, signal.SIG_IGN)
        status["signal"] = signum
        status["cleanup_ok"] = stop_group()
        if not status["cleanup_ok"]:
            log.write(b"owned process group did not terminate after SIGKILL\n")
            log.flush()
        status["child_exit_code"] = child.poll()
        status["elapsed_seconds"] = round(time.monotonic() - started, 3)
        status_path.write_text(json.dumps(status, indent=2) + "\n")
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    try:
        result = child.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        status["timed_out"] = True
        log.write(f"owned command exceeded {timeout_seconds}s\n".encode())
        log.flush()
        status["cleanup_ok"] = stop_group()
        status["child_exit_code"] = child.poll()
        status["elapsed_seconds"] = round(time.monotonic() - started, 3)
        status_path.write_text(json.dumps(status, indent=2) + "\n")
        if not status["cleanup_ok"]:
            log.write(b"owned process group did not terminate after SIGKILL\n")
            sys.exit(1)
        sys.exit(124)
    status["child_exit_code"] = result
    status["cleanup_ok"] = stop_group()
    if not status["cleanup_ok"]:
        log.write(b"owned process group did not terminate after SIGKILL\n")
        result = 1
    status["elapsed_seconds"] = round(time.monotonic() - started, 3)
    status_path.write_text(json.dumps(status, indent=2) + "\n")
    sys.exit(result if result >= 0 else 128 - result)
PY

run_owned() {
    local log=$1
    local cwd=$2
    local timeout_seconds=$3
    shift 3
    python3 "$work/supervise.py" "$log" "$cwd" "$timeout_seconds" "$@" &
    active_supervisor=$!
    local status=0
    wait "$active_supervisor" || status=$?
    active_supervisor=
    if [ "$status" -ne 0 ]; then
        echo "owned command failed ($status); full log: $log" >&2
        tail -n 80 "$log" >&2
    fi
    return "$status"
}

run_owned "$output/fork-clone.log" "$PWD" 300 git clone --branch "$fork_branch" --single-branch "$fork_url" "$candidate"
actual_rev=$(git -C "$candidate" rev-parse HEAD)
[ "$actual_rev" = "$candidate_rev" ] || { echo "fork revision differs: $actual_rev" >&2; exit 1; }
git -C "$candidate" merge-base --is-ancestor "$base_rev" "$candidate_rev" || {
    echo 'candidate is not descended from reviewed fork base' >&2
    exit 1
}
git -C "$candidate" diff --binary "$base_rev" "$candidate_rev" >"$output/candidate.patch"
actual_patch_sha=$(python3 - "$output/candidate.patch" <<'PY'
from hashlib import sha256
from pathlib import Path
import sys

print(sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)
[ "$actual_patch_sha" = "$reviewed_patch_sha" ] || {
    echo "fork patch hash differs: $actual_patch_sha" >&2
    exit 1
}
git -C "$candidate" status --porcelain >"$output/fork-status.txt"
[ ! -s "$output/fork-status.txt" ] || { echo 'fork checkout is dirty' >&2; exit 1; }
printf '%s\n' "$candidate_rev" >"$output/fork-revision.txt"

test_source="$work/fork-test-source"
mkdir -p "$test_source"
run_owned "$output/fork-archive.log" "$candidate" 300 git archive "$candidate_rev" -o "$work/fork.tar"
run_owned "$output/fork-extract.log" "$test_source" 300 tar -xf "$work/fork.tar"
run_owned "$output/fork-resolve.log" "$test_source" 900 cargo generate-lockfile
cp "$test_source/Cargo.lock" "$output/fork-Cargo.lock"

run_check() {
    name=$1
    shift
    echo "[$name] $*"
    if run_owned "$output/$name.log" "$test_source" 3600 "$@"; then
        echo "[$name] passed"
    else
        status=$?
        return "$status"
    fi
}

run_check native-tests cargo test -p webdriver-downloader --lib traits:: \
    --no-default-features --features native-tls
for name in test_extract_zip_finds_nested_driver test_extract_zip_rejects_missing_driver \
    test_extract_tarball_success test_extract_tarball_executable_not_found; do
    grep -E "^test .*${name} \\.\\.\\. ok$" "$output/native-tests.log" >/dev/null || {
        echo "required native-TLS test did not pass: $name" >&2
        exit 1
    }
done
run_check native-check cargo check -p webdriver-downloader --all-targets \
    --no-default-features --features native-tls
run_check rustls-tests cargo test -p webdriver-downloader --lib traits:: \
    --no-default-features --features rustls-tls
for name in test_extract_zip_finds_nested_driver test_extract_zip_rejects_missing_driver; do
    grep -E "^test .*${name} \\.\\.\\. ok$" "$output/rustls-tests.log" >/dev/null || {
        echo "required Rustls test did not pass: $name" >&2
        exit 1
    }
done
run_check rustls-check cargo check -p webdriver-downloader --all-targets \
    --no-default-features --features rustls-tls

if [ "$platform" = linux ]; then
    browser_url=https://storage.googleapis.com/chrome-for-testing-public/154.0.8037.92/linux64/chrome-linux64.zip
    printf '%s\n' "$browser_url" >"$output/browser-url.txt"
    printf '%s\n' "$browser_sha" >"$output/browser-sha256.txt"
    run_owned "$output/browser-download.log" "$work" 900 curl --fail --location --silent \
        --show-error --retry 3 --max-time 600 "$browser_url" --output "$work/chrome-linux64.zip"
    printf '%s  %s\n' "$browser_sha" "$work/chrome-linux64.zip" | sha256sum --check --status || {
        echo 'Chrome for Testing archive SHA-256 differs from reviewed preflight' >&2
        exit 1
    }
    actual_browser_bytes=$(python3 - "$work/chrome-linux64.zip" <<'PY'
from pathlib import Path
import sys

print(Path(sys.argv[1]).stat().st_size)
PY
)
    [ "$actual_browser_bytes" = "$browser_bytes" ] || {
        echo "Chrome for Testing archive length differs: $actual_browser_bytes" >&2
        exit 1
    }
    mkdir -p "$work/browser"
    run_owned "$output/browser-extract.log" "$work" 300 unzip -q "$work/chrome-linux64.zip" -d "$work/browser"
    BROWSER_PATH="$work/browser/chrome-linux64/chrome"
    [ -x "$BROWSER_PATH" ] || { echo 'Chrome for Testing executable is missing' >&2; exit 1; }
    private_home="$work/private-home"
    mkdir -p "$private_home/bin" "$work/tmp"
    WEBDRIVER_INSTALL_PATH="$private_home/bin"
    smoke_env=(env -u WEBDRIVER_PATH
        HOME="$private_home" TMPDIR="$work/tmp"
        XDG_CACHE_HOME="$private_home/.cache"
        XDG_CONFIG_HOME="$private_home/.config"
        XDG_DATA_HOME="$private_home/.local/share"
        BROWSER_PATH="$BROWSER_PATH"
        WEBDRIVER_INSTALL_PATH="$WEBDRIVER_INSTALL_PATH")
    run_owned "$output/browser-version.txt" "$work" 60 "${smoke_env[@]}" "$BROWSER_PATH" --version
    grep -F '154.0.8037.92' "$output/browser-version.txt" >/dev/null || {
        echo 'Chrome for Testing executable has the wrong version' >&2
        exit 1
    }
    [ ! -e "$WEBDRIVER_INSTALL_PATH/chromedriver" ] || {
        echo 'private driver install path was not empty' >&2
        exit 1
    }
    smoke="$work/plotly-smoke"
    mkdir -p "$smoke/src"
    cat >"$smoke/Cargo.toml" <<EOF
[package]
name = "webdriver-plotly-smoke"
version = "0.0.0"
edition = "2021"

[dependencies]
anyhow = "1"
plotly_static = { version = "=0.1.0", features = ["chromedriver", "webdriver_download"] }
serde_json = "1"

[patch.crates-io]
webdriver-downloader = { path = "$test_source/webdriver-downloader" }
EOF
    cat >"$smoke/src/main.rs" <<'EOF'
use plotly_static::{ImageFormat, StaticExporterBuilder};
use serde_json::json;
use std::path::Path;

fn main() -> anyhow::Result<()> {
    let plot = json!({
        "data": [{"type": "scatter", "x": [1, 2, 3], "y": [2, 4, 3]}],
        "layout": {"title": "WebDriver candidate smoke"}
    });
    let mut exporter = StaticExporterBuilder::default()
        .webdriver_browser_caps(vec![
            "--headless".to_string(),
            "--no-sandbox".to_string(),
            "--disable-dev-shm-usage".to_string(),
        ])
        .build()?;
    exporter.write_fig(Path::new("plot"), &plot, ImageFormat::PNG, 320, 240, 1.0)
        .map_err(|error| anyhow::anyhow!("{error}"))?;
    Ok(())
}
EOF
    run_owned "$output/plotly-resolve.log" "$smoke" 900 "${smoke_env[@]}" cargo generate-lockfile
    cp "$smoke/Cargo.lock" "$output/plotly-Cargo.lock"
    python3 - "$output/plotly-Cargo.lock" <<'PY'
from pathlib import Path
import sys
import tomllib

packages = tomllib.loads(Path(sys.argv[1]).read_text())["package"]
versions = lambda name: [p["version"] for p in packages if p["name"] == name]
downloader = [p for p in packages if p["name"] == "webdriver-downloader"]
assert len(downloader) == 1 and downloader[0]["version"] == "0.16.4"
assert "source" not in downloader[0], "smoke did not use the candidate path patch"
for name, prefix in (("fantoccini", "0.22."), ("reqwest", "0.12."), ("zip", "4.")):
    selected = versions(name)
    assert len(selected) == 1 and selected[0].startswith(prefix), (name, selected)
print("Plotly smoke lock selects the candidate and aligned dependency families")
PY
    echo '[plotly-export] build and render PNG with automatic driver download'
    if run_owned "$output/plotly-export.log" "$smoke" 3600 "${smoke_env[@]}" cargo run; then
        echo '[plotly-export] passed'
    else
        status=$?
        exit "$status"
    fi
    [ -x "$WEBDRIVER_INSTALL_PATH/chromedriver" ] || {
        echo 'automatic download did not install an executable private ChromeDriver' >&2
        exit 1
    }
    python3 - "$smoke/plot.png" <<'PY'
from pathlib import Path
import struct
import sys

image = Path(sys.argv[1]).read_bytes()
assert image[:8] == b"\x89PNG\r\n\x1a\n", "export did not produce PNG bytes"
assert struct.unpack(">II", image[16:24]) == (320, 240), "export dimensions differ"
assert len(image) > 100, "exported PNG is unexpectedly short"
print(f"Rendered PNG: {len(image)} bytes, 320x240")
PY
    cp "$smoke/plot.png" "$output/plot.png"
    run_owned "$output/chromedriver-version.txt" "$work" 60 \
        "${smoke_env[@]}" "$WEBDRIVER_INSTALL_PATH/chromedriver" --version
    grep -F '154.0.8037.92' "$output/chromedriver-version.txt" >/dev/null || {
        echo 'automatically downloaded ChromeDriver has the wrong version' >&2
        exit 1
    }
fi
