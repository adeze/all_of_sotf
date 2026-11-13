#!/usr/bin/env bash
# Build one release artifact group from pinned sibling checkouts. Never install it.
set -uo pipefail

group=${1:?select desktop, plugins, autoeq, systemwide, or au}
platform=${2:?select macos or linux}
case "$group:$platform" in
    desktop:macos|desktop:linux|plugins:macos|plugins:linux|autoeq:macos|autoeq:linux|systemwide:macos|au:macos) ;;
    *) echo "Unsupported artifact group/platform: $group/$platform" >&2; exit 2 ;;
esac

root=$(cd "$(dirname "$0")/../.." && pwd) || exit 1
output="$root/release-artifact-evidence-$group-$platform"
mkdir -p "$output/artifacts" "$output/logs" || exit 1
cp "$root/scripts/release/sources.json" "$output/sources.json" || exit 1
cd "$root" || exit 1

# These recipes may conditionally sign when release credentials are present.
unset DEVELOPER_ID INSTALLER_DEVELOPER_ID APPLE_ID APP_SPECIFIC_PASSWORD
unset APPLE_TEAM_ID CODESIGN_IDENTITY
shopt -s nullglob
failed=0

python3 - "$output/source-before.json" <<'PY'
import hashlib, json, pathlib, subprocess, sys
root = pathlib.Path.cwd()
names = json.loads((root / 'scripts/release/sources.json').read_text())['sources']
state = {}
for name in names:
    path = root / name
    state[name] = {
        'revision': subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip(),
        'tracked_status': subprocess.check_output(['git', '-C', str(path), 'diff', '--name-only', 'HEAD'], text=True).splitlines(),
        'lock_sha256': hashlib.sha256((path / 'Cargo.lock').read_bytes()).hexdigest(),
    }
    expected = names[name]['revision']
    if state[name]['revision'] != expected or state[name]['tracked_status']:
        raise SystemExit(f'{name}: source is not the clean pinned revision {expected}')
pathlib.Path(sys.argv[1]).write_text(json.dumps(state, indent=2) + '\n')
PY
if (( $? != 0 )); then exit 1; fi

if [[ $group == plugins ]]; then
    for tool in pluginval clap-validator; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            printf 'Required validator is missing: %s\n' "$tool" | tee -a "$output/prerequisites.log" >&2
            failed=1
        fi
    done
fi
if ! (
    date -u || exit 1
    uname -a || exit 1
    python3 --version || exit 1
    rustc --version || exit 1
    cargo --version || exit 1
    just --version || exit 1
    if [[ $platform == macos ]]; then
        swift --version || exit 1
        xcodebuild -version || exit 1
        if [[ $group == systemwide || $group == au ]]; then
            for tool in pkgutil codesign productbuild pkgbuild lsbom; do
                command -v "$tool" || exit 1
            done
        fi
    fi
    if [[ $group == plugins ]]; then command -v pluginval || :; command -v clap-validator || :; fi
) >"$output/toolchain.txt" 2>&1; then
    cat "$output/toolchain.txt" >&2
    exit 1
fi

run_step() {
    local label=$1 workspace=$2
    shift 2
    printf '%s\t%s\t%s\n' "$label" "$workspace" "$*" >>"$output/commands.tsv"
    (cd "$workspace" && "$@") >"$output/logs/$label.log" 2>&1
    local status=$?
    printf '%s\t%s\n' "$label" "$status" >>"$output/results.tsv"
    if (( status != 0 )); then
        tail -n 80 "$output/logs/$label.log"
        failed=1
    fi
    return "$status"
}

stage() {
    local relative=$1
    if [[ ! -e $relative ]]; then
        printf 'Missing artifact: %s\n' "$relative" >&2
        failed=1
        return 1
    fi
    mkdir -p "$output/artifacts/$(dirname "$relative")"
    cp -R "$relative" "$output/artifacts/$relative"
    local status=$?
    if (( status != 0 )); then failed=1; fi
    return "$status"
}

case "$group" in
    desktop)
        if run_step desktop-build sotf cargo build --release -p sotf-gpui --bin sotf-desktop --features onnx,hal,gpu-2d,gpu-3d,iamf,streaming,hls &&
            stage sotf/target/release/sotf-desktop; then
            run_step desktop-runtime-smoke . python3 scripts/release/desktop_runtime_smoke.py \
                "$platform" "$output/artifacts/sotf/target/release/sotf-desktop" \
                "$root/sotf/target/release/sotf-desktop" "$output"
        fi
        ;;
    plugins)
        if [[ $platform == macos ]]; then
            run_step vst3-build sotf-daw just prod-vst3 && stage sotf-daw/dist/vst3
            if [[ -d sotf-daw/dist/nih/build-logs ]] &&
                cp -R sotf-daw/dist/nih/build-logs "$output/logs/nih-vst3-features"; then
                :
            else
                echo 'Failed to retain VST3 feature build logs' >&2
                failed=1
            fi
            run_step clap-build sotf-daw just prod-clap && stage sotf-daw/dist/clap
            if [[ -d sotf-daw/dist/nih/build-logs ]] &&
                cp -R sotf-daw/dist/nih/build-logs "$output/logs/nih-clap-features"; then
                :
            else
                echo 'Failed to retain CLAP feature build logs' >&2
                failed=1
            fi
            run_step vst3-validate sotf-daw bash ../scripts/release/validate_macos_plugins.sh vst3 "$output"
            run_step clap-validate sotf-daw bash ../scripts/release/validate_macos_plugins.sh clap "$output"
        else
            if run_step plugin-formats-build sotf-daw just prod-plugin-formats-linux; then
                stage sotf-daw/dist/vst3-linux
                stage sotf-daw/dist/clap-linux
            fi
            if [[ -d sotf-daw/dist/nih-linux/build-logs ]]; then
                if ! cp -R sotf-daw/dist/nih-linux/build-logs "$output/logs/nih-features"; then
                    echo 'Failed to retain Linux feature build logs' >&2
                    failed=1
                fi
            else
                echo 'Missing Linux feature build logs' >&2
                failed=1
            fi
            run_step vst3-validate sotf-daw bash ../scripts/release/validate_linux_plugins.sh vst3 "$output"
            run_step clap-validate sotf-daw bash ../scripts/release/validate_linux_plugins.sh clap "$output"
        fi
        ;;
    autoeq)
        run_step autoeq-dist autoeq just dist
        for binary in autoeq roomeq benchmark-autoeq-speaker autoeq-download-speakers roomeq-fuzzer; do
            stage "autoeq/target/dist/$binary"
        done
        ;;
    systemwide)
        run_step systemwide-package sotf-systemwide just build-systemwide
        packages=(sotf-systemwide/target/daemon-dmg/sotf-systemwide-*-macos-universal.pkg)
        if [[ ${#packages[@]} -ne 1 || ! -f ${packages[0]:-} ]]; then
            echo 'Expected exactly one fresh systemwide pkg' >&2
            failed=1
        else
            stage "${packages[0]}"
            run_step systemwide-payload sotf-systemwide bash -c '
                set -euo pipefail
                pkg=$1
                scratch=$(mktemp -d)
                trap '\''rm -rf "$scratch"'\'' EXIT
                expanded="$scratch/expanded"
                pkgutil --expand "$pkg" "$expanded"
                app="$expanded/SotFSystemwide.pkg/Bom"
                hal="$expanded/SotFHAL.pkg/Bom"
                test -f "$app" && test -f "$hal"
                lsbom -s "$app" >"$scratch/app-payload.txt"
                lsbom -s "$hal" >"$scratch/hal-payload.txt"
                cat "$scratch/app-payload.txt" "$scratch/hal-payload.txt"
                grep -Fqx "./Applications/sotf-systemwide.app/Contents/MacOS/sotf-systemwide" "$scratch/app-payload.txt"
                grep -Fqx "./Applications/sotf-systemwide.app/Contents/Helpers/sotf-daemon" "$scratch/app-payload.txt"
                grep -Fqx "./Library/Audio/Plug-Ins/HAL/SotFHAL.driver/Contents/MacOS/SotFHAL" "$scratch/hal-payload.txt"
            ' _ "${packages[0]#sotf-systemwide/}"
        fi
        ;;
    au)
        run_step au-build sotf-daw just build-au-all
        run_step au-package sotf-daw just dist-au
        packages=(sotf-daw/dist/au/*.pkg)
        if [[ ${#packages[@]} -ne 2 || ! -f ${packages[0]:-} ]]; then
            echo 'Expected two fresh AU pkg artifacts' >&2
            failed=1
        else
            for package in "${packages[@]}"; do
                stage "$package"
                run_step "au-payload-$(basename "$package" .pkg)" sotf-daw bash -c '
                    set -euo pipefail
                    pkg=$1
                    payload=$(pkgutil --payload-files "$pkg")
                    printf "%s\n" "$payload"
                    grep -Eq "(^|/)SOTFAudioUnits\\.app/Contents/Info\\.plist$" <<<"$payload"
                    grep -Eq "(^|/)SOTFAudioUnits\\.app/Contents/MacOS/[^/]+$" <<<"$payload"
                ' _ "${package#sotf-daw/}"
            done
        fi
        ;;
esac

python3 - "$output" <<'PY'
import hashlib, json, pathlib, subprocess, sys
root = pathlib.Path.cwd()
output = pathlib.Path(sys.argv[1])
before = json.loads((output / 'source-before.json').read_text())
after = {}
for name in before:
    path = root / name
    after[name] = {
        'revision': subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip(),
        'tracked_status': subprocess.check_output(['git', '-C', str(path), 'diff', '--name-only', 'HEAD'], text=True).splitlines(),
        'lock_sha256': hashlib.sha256((path / 'Cargo.lock').read_bytes()).hexdigest(),
    }
(output / 'source-after.json').write_text(json.dumps(after, indent=2) + '\n')
inventory = []
for path in sorted((output / 'artifacts').rglob('*')):
    if path.is_file():
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        inventory.append({'path': str(path.relative_to(output)), 'size': path.stat().st_size,
                          'sha256': digest.hexdigest()})
(output / 'artifact-inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
if before != after or not inventory:
    sys.exit('Source/lock changed or no artifacts were produced')
PY
status=$?
if (( status != 0 )); then failed=1; fi
exit "$failed"
