#!/usr/bin/env bash
# Focus the pinned DAW lock on native CLAP stream behavior and one host validator.
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
evidence="$root/release-clap-state-evidence-linux"
mkdir -p "$evidence/logs" "$evidence/artifacts"
cd "$root"

snapshot() {
    local destination=$1
    python3 - "$destination" <<'PY'
import json
from pathlib import Path
import sys

from scripts.release.qa import ROOT, source_state
from scripts.buildbot.ci_matrix import workspace_map

Path(sys.argv[1]).write_text(
    json.dumps(source_state(ROOT, list(workspace_map()), "linux"), indent=2) + "\n",
    encoding="utf-8",
)
PY
}

snapshot "$evidence/sources-before.json"
git rev-parse HEAD >"$evidence/root-revision.txt"
cp scripts/release/sources.json "$evidence/sources.json"
rustc --version >"$evidence/rustc-version.txt"
cargo --version >"$evidence/cargo-version.txt"

failed=0
finish() {
    local original=$?
    trap - EXIT
    cd "$root" || exit 1
    snapshot "$evidence/sources-after.json" || original=1
    if ! python3 - "$evidence" <<'PY'
import json
from pathlib import Path
import sys

from scripts.release.qa import source_issues

evidence = Path(sys.argv[1])
before = json.loads((evidence / "sources-before.json").read_text())
after = json.loads((evidence / "sources-after.json").read_text())
issues = source_issues(before, after, require_clean=True)
(evidence / "source-issues.json").write_text(json.dumps(issues, indent=2) + "\n")
for issue in issues:
    print(f"SOURCE GUARD: {issue}")
raise SystemExit(bool(issues))
PY
    then
        original=1
    fi
    if (( failed != 0 )); then original=1; fi
    exit "$original"
}
trap finish EXIT

python3 - "$evidence/sources-before.json" <<'PY'
import json
from pathlib import Path
import sys

from scripts.release.qa import source_issues

before = json.loads(Path(sys.argv[1]).read_text())
issues = source_issues(before, before, require_clean=True)
for issue in issues:
    print(f"SOURCE GUARD: {issue}")
raise SystemExit(bool(issues))
PY

run_logged() {
    local name=$1
    shift
    local log="$evidence/logs/$name.log"
    printf 'RUN %s:' "$name"
    printf ' %q' "$@"
    printf '\n'
    if "$@" >"$log" 2>&1; then
        echo "PASS $name (full log: $log)"
    else
        local status=$?
        echo "FAIL $name (exit $status; full log: $log)" >&2
        tail -n 80 "$log" >&2
        failed=$((failed + 1))
        return "$status"
    fi
}

run_named_test() {
    local name=$1 filter=$2
    shift 2
    if run_logged "$name" "$@"; then
        if ! grep -E "^test .*${filter} \\.\\.\\. ok$" "$evidence/logs/$name.log" >/dev/null; then
            echo "FAIL $name: expected test did not run and pass" >&2
            failed=$((failed + 1))
        fi
    fi
}

cd "$root/sotf-daw"
run_named_test native-stream clap_state_stream_handles_short_reads_and_rejects_untrusted_lengths \
    cargo test -p plugins-nih --lib \
    --features convolution clap_state_stream_handles_short_reads_and_rejects_untrusted_lengths \
    -- --nocapture
run_named_test native-resource clap_state_restore_stages_true_stereo_resource_and_survives_rejections \
    cargo test -p plugins-nih --lib \
    --features convolution clap_state_restore_stages_true_stereo_resource_and_survives_rejections \
    -- --nocapture

built="$root/sotf-daw/target/release/libplugins_nih.so"
rm -f "$built"
if run_logged gain-build cargo build --release -p plugins-nih \
    --features gain --no-default-features; then
    if [[ -s "$built" ]]; then
        cp "$built" "$evidence/artifacts/sotf_gain.clap"
        validator_dir="$root/release-artifact-evidence-plugins-linux/validators"
        if [[ -x "$validator_dir/bin/clap-validator" ]]; then
            run_logged gain-validator "$validator_dir/bin/clap-validator" validate \
                "$evidence/artifacts/sotf_gain.clap" || true
        else
            echo "Pinned CLAP validator missing: $validator_dir/bin/clap-validator" >&2
            failed=$((failed + 1))
        fi
    else
        echo "Gain build produced no shared library: $built" >&2
        failed=$((failed + 1))
    fi
fi

echo "Focused CLAP state gate failures: $failed"
