#!/usr/bin/env bash
set -u

platform=${1:?macos or linux required}
output=${2:?evidence directory required}
phase=${3:?diagnostic or full phase required}
if [ "$platform" != macos ] && [ "$platform" != linux ]; then
    printf 'unsupported platform: %s\n' "$platform" >&2
    exit 2
fi
if [ "$phase" != diagnostic ] && [ "$phase" != full ]; then
    printf 'unsupported lab phase: %s\n' "$phase" >&2
    exit 2
fi
mkdir -p "$output" || exit 1
cp scripts/release/sources.json "$output/sources.json" || exit 1
if [ "$phase" = diagnostic ]; then
    printf 'NOT_FULL_QA: focused isolated lab diagnostics only; full systemwide lab recipe was not run.\n' >"$output/NOT_FULL_QA" || exit 1
fi
{
    date -u
    uname -a
    python3 --version
    rustc --version
    cargo --version
    just --version
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
    local name=$1 workspace=$2
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
        tail -n 80 "$log"
        failed=1
    elif [ "$name" = daw-engine-lab-tests ] || [ "$name" = systemwide-ipc-lab-tests ]; then
        if ! grep -Eq '^running [1-9][0-9]* tests?$' "$log"; then
            printf '[%s] no filtered tests executed\n' "$name" >&2
            failed=1
        fi
    fi
    return "$status"
}

run_bounded_check() {
    local name=$1 workspace=$2 seconds=$3
    shift 3
    local log="$output/$name.log"
    printf '[%s] timeout %ss: %s\n' "$name" "$seconds" "$*"
    (cd "$workspace" && exec python3 - "$seconds" "$@" <<'PY'
import os
import signal
import subprocess
import sys

seconds = int(sys.argv[1])
process = subprocess.Popen(sys.argv[2:], start_new_session=True)
interrupted = None

def forward_signal(signum, _frame):
    global interrupted
    interrupted = signum
    raise KeyboardInterrupt

def signal_group(signum):
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass

def cleanup_group():
    signal_group(signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    # The group can retain grandchildren after its leader exits.
    signal_group(signal.SIGKILL)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        print("owned test process did not reap after SIGKILL", file=sys.stderr, flush=True)
        return False
    return True

signal.signal(signal.SIGTERM, forward_signal)
signal.signal(signal.SIGINT, forward_signal)
try:
    status = process.wait(timeout=seconds)
except subprocess.TimeoutExpired:
    print(f"process exceeded {seconds}s: {sys.argv[2:]}", file=sys.stderr, flush=True)
    status = 124
except KeyboardInterrupt:
    print(f"test wrapper interrupted by signal {interrupted}", file=sys.stderr, flush=True)
    status = 128 + (interrupted or signal.SIGINT)
finally:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if not cleanup_group() and status == 0:
        status = 1
raise SystemExit(status)
PY
    ) >"$log" 2>&1
    local status=$?
    printf '[%s] exit %s; log: %s\n' "$name" "$status" "$log"
    if [ "$status" -ne 0 ]; then
        tail -n 80 "$log"
        failed=1
    fi
}

run_check daw-engine-lab-tests sotf-daw cargo test -p sotf-engine --lib lab_tests
run_check daw-engine-all-targets sotf-daw cargo check -p sotf-engine --all-targets
if run_check systemwide-ipc-lab-compile sotf-systemwide cargo test -p sotf-daemon --test ipc_line_tests --no-run; then
    for test_name in systemwide_lab_restarts_with_a_fresh_coherent_snapshot systemwide_lab_scenario_matrix_over_unix_socket; do
        run_bounded_check "$test_name" sotf-systemwide 180 \
            cargo test -p sotf-daemon --test ipc_line_tests "$test_name" -- --exact --nocapture --test-threads=1
        if ! grep -Fq "test ${test_name} ..." "$output/$test_name.log" || \
            ! grep -Eq '^test result: ok\. 1 passed; 0 failed; 0 ignored;' "$output/$test_name.log"; then
            printf '[%s] required lab test did not pass\n' "$test_name" >&2
            failed=1
        fi
    done
fi
if [ "$platform" = macos ] && [ "$phase" = full ] && [ "$failed" -eq 0 ]; then
    run_bounded_check systemwide-isolated-lab sotf-systemwide 1200 just systemwide-lab
fi

source_state after || failed=1
exit "$failed"
