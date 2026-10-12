#!/usr/bin/env bash
#
# Syntax + test check for the Python maintenance scripts (scripts/*).
# Runs under bacon's python_pytest analyzer; exits nonzero on any failure.
#
# Extra arguments are forwarded to pytest, e.g.
#   scripts-check.sh -x -k version_snapshot

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# 1. Syntax gate over everything (covers files without tests too).
# Third-party venvs are excluded: slow to traverse and not our code.
python3 -m compileall -q -x '/venv/' scripts
compile_status=$?
if [ "$compile_status" -ne 0 ]; then
    exit "$compile_status"
fi

# Lets the buildbot tests `from ci_matrix import ...` resolve.
export PYTHONPATH="$ROOT/scripts/buildbot${PYTHONPATH:+:$PYTHONPATH}"

# 2. Tests: require pytest so every maintenance suite runs.
if [ -x "$ROOT/venv/bin/python" ] && "$ROOT/venv/bin/python" -m pytest --version >/dev/null 2>&1; then
    exec "$ROOT/venv/bin/python" -m pytest scripts/align-crates scripts/buildbot/tests scripts/release scripts/mono -q -rf "$@"
elif python3 -m pytest --version >/dev/null 2>&1; then
    exec python3 -m pytest scripts/align-crates scripts/buildbot/tests scripts/release scripts/mono -q -rf "$@"
else
    echo "pytest not found; install scripts/requirements.txt to run all maintenance tests" >&2
    exit 2
fi
