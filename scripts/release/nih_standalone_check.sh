#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
evidence=${1:?absolute evidence directory required}
mkdir -p "$evidence/nih-standalone"
evidence="$evidence/nih-standalone"

if [ "$(uname -s)" != Linux ]; then
    echo 'NIH standalone container check requires the Linux Gitea runner' >&2
    exit 1
fi
python3 - "$root" "$evidence/source-before.json" <<'PY'
import hashlib, json, pathlib, subprocess, sys
root = pathlib.Path(sys.argv[1])
sources = json.loads((root / 'scripts/release/sources.json').read_text())['sources']
state = {}
for name, entry in sources.items():
    path = root / name
    revision = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
    tracked = subprocess.check_output(['git', '-C', str(path), 'diff', '--name-only', 'HEAD'], text=True).splitlines()
    if revision != entry['revision'] or tracked:
        raise SystemExit(f'{name}: source differs from clean pinned revision')
    state[name] = {'revision': revision, 'tracked_status': tracked,
                   'lock_sha256': hashlib.sha256((path / 'Cargo.lock').read_bytes()).hexdigest()}
pathlib.Path(sys.argv[2]).write_text(json.dumps(state, indent=2) + '\n')
PY

base_tag=rust:1.99.0-bookworm
docker info --format '{{.ServerVersion}} {{.OSType}}/{{.Architecture}}'
docker pull --platform linux/amd64 "$base_tag"
base_digest=$(docker image inspect "$base_tag" --format '{{index .RepoDigests 0}}')
test -n "$base_digest"
printf '%s\n' "$base_digest" >"$evidence/rust-base-digest.txt"
docker build --platform linux/amd64 \
    --build-arg RUST_BASE_IMAGE="$base_digest" \
    --iidfile "$evidence/image-id.txt" \
    -f "$root/scripts/release/Dockerfile.nih-standalone" "$root/scripts/release"
image_id=$(cat "$evidence/image-id.txt")
test "$(docker image inspect "$image_id" --format '{{.Os}}/{{.Architecture}}')" = linux/amd64
docker run --rm --platform linux/amd64 "$image_id" bash -ec 'rustc --version; command -v clang mold cargo; pkg-config --modversion jack'

status=0
docker run --rm --platform linux/amd64 \
    --mount "type=bind,src=$root,dst=/all_of_sotf,readonly" \
    -v sotf-nih-standalone-cargo-registry:/usr/local/cargo/registry \
    -v sotf-nih-standalone-cargo-git:/usr/local/cargo/git \
    -v sotf-nih-standalone-target:/target \
    -w /all_of_sotf/sotf-daw \
    -e CARGO_TARGET_DIR=/target \
    "$image_id" \
    cargo check -p plugins-nih --features nih_plug/standalone || status=$?

python3 - "$root" "$evidence/source-before.json" "$evidence/source-after.json" <<'PY' || status=1
import hashlib, json, pathlib, subprocess, sys
root = pathlib.Path(sys.argv[1])
before = json.loads(pathlib.Path(sys.argv[2]).read_text())
after = {}
for name in before:
    path = root / name
    after[name] = {
        'revision': subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip(),
        'tracked_status': subprocess.check_output(['git', '-C', str(path), 'diff', '--name-only', 'HEAD'], text=True).splitlines(),
        'lock_sha256': hashlib.sha256((path / 'Cargo.lock').read_bytes()).hexdigest(),
    }
pathlib.Path(sys.argv[3]).write_text(json.dumps(after, indent=2) + '\n')
if after != before:
    raise SystemExit('Pinned source revision, tracked status, or Cargo.lock changed')
PY
exit "$status"
