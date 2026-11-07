#!/usr/bin/env bash
#
# Run one cargo subcommand in parallel in every Rust workspace of this repo.
#
# Usage: cargo-all.sh <cargo-args...>
#   e.g. cargo-all.sh check --all-targets
#
# Each workspace keeps its own cwd so its rust-toolchain.toml, .cargo/config
# and target dir are honored. Output is buffered per workspace and printed
# sequentially (fixed workspace order) so bacon's analyzer sees coherent
# cargo output. Diagnostic paths are rewritten from workspace-relative to
# repo-root-relative so bacon navigation resolves to the right file.
#
# Env:
#   BACON_WORKSPACES  space-separated subset to run (default: all), e.g.
#                     BACON_WORKSPACES="sotf autoeq" bacon check
#   CARGO_BUILD_JOBS  optionally caps rustc parallelism per workspace; left
#                     untouched by default so each cargo uses its own setting.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
workspace_list="$(python3 "$ROOT/scripts/workspaces.py" --local)" || exit 2
ALL_WORKSPACES=()
while IFS= read -r ws; do
    ALL_WORKSPACES+=("$ws")
done <<< "$workspace_list"

WORKSPACES=("${ALL_WORKSPACES[@]}")
if [ -n "${BACON_WORKSPACES:-}" ]; then
    # shellcheck disable=SC2206
    WORKSPACES=(${BACON_WORKSPACES})
fi

for ws in "${WORKSPACES[@]}"; do
    case " ${ALL_WORKSPACES[*]} " in
        *" $ws "*) ;;
        *) echo "unknown workspace: $ws" >&2; exit 2 ;;
    esac
done

if [ "$#" -eq 0 ]; then
    echo "usage: cargo-all.sh <cargo-args...>" >&2
    exit 2
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/bacon-cargo-all.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

for ws in "${WORKSPACES[@]}"; do
    : >"$TMP_DIR/$ws.log"
    echo 127 >"$TMP_DIR/$ws.code"
    (
        cd "$ROOT/$ws" && cargo "$@" >"$TMP_DIR/$ws.log" 2>&1
        echo "$?" >"$TMP_DIR/$ws.code"
    ) &
done
wait

fail=0
for ws in "${WORKSPACES[@]}"; do
    code="$(cat "$TMP_DIR/$ws.code")"
    echo "===== $ws: cargo $* (exit $code) ====="
    sed -e "s|^\\( *--> \\)\\([^/ ]\\)|\\1$ws/\\2|" \
        -e "s|panicked at \\([^ /][^:]*:[0-9][0-9]*:[0-9][0-9]*\\)|panicked at $ws/\\1|g" \
        "$TMP_DIR/$ws.log"
    if [ "$code" -ne 0 ]; then
        fail=1
    fi
done
exit "$fail"
