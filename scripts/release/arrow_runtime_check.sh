#!/usr/bin/env bash
set -u

platform=${1:?macos or linux required}
output=${2:?evidence directory required}
if [ "$platform" != macos ] && [ "$platform" != linux ]; then
    printf 'unsupported platform: %s\n' "$platform" >&2
    exit 2
fi
mkdir -p "$output" || exit 1
output=$(cd "$output" && pwd) || exit 1
cp scripts/release/sources.json "$output/sources.json" || exit 1
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
(output / f"source-issues-{phase}.json").write_text(json.dumps(issues, indent=2) + "\n")
for issue in issues:
    print(f"SOURCE GUARD: {issue}")
raise SystemExit(bool(issues))
PY
}

source_state before || exit 1
failed=0
run_check() {
    local name=$1
    shift
    local log="$output/$name.log"
    printf '[%s] %s\n' "$name" "$*"
    (cd gpui-toolkit && "$@") >"$log" 2>&1
    local status=$?
    printf '[%s] exit %s; log: %s\n' "$name" "$status" "$log"
    if [ "$status" -ne 0 ]; then
        tail -n 80 "$log"
        failed=1
    fi
}

run_check arrow-runtime-lib cargo test -p gpui-python-runtime --lib
for test in arrow_ipc_ingest_stores_bytes_for_preview arrow_ipc_ingest_rejects_non_arrow_bytes \
    preview_reads_only_requested_arrow_rows_and_fields \
    grouping_and_aggregation_execute_over_arrow_without_unbounded_output \
    million_row_preview_materializes_only_the_requested_window; do
    if ! grep -E "^test .*${test} \\.\\.\\. ok$" "$output/arrow-runtime-lib.log" >/dev/null; then
        printf 'required Arrow test did not run and pass: %s\n' "$test" >&2
        failed=1
    fi
done
run_check arrow-runtime-all-features cargo check -p gpui-python-runtime --all-targets --all-features
source_state after || failed=1
exit "$failed"
