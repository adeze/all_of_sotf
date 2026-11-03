#!/usr/bin/env bash
# Drive actual rfd portal windows in a disposable Linux display session.
set -uo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd) || exit 1
cd "$root" || exit 1
export PATH="/usr/libexec:$PATH"
output="$root/release-native-dialog-evidence-linux"
mkdir -p "$output/screenshots" "$output/logs" || exit 1
cp scripts/release/sources.json "$output/sources.json" || exit 1
fixture=$(mktemp -d "${TMPDIR:-/tmp}/sotf-rfd.XXXXXXXX") || exit 1
active_pid=
cleanup() {
    if [[ -n $active_pid ]]; then kill "$active_pid" 2>/dev/null || :; fi
    rm -rf "$fixture"
}
trap cleanup EXIT

if [[ -z ${DISPLAY:-} || -z ${DBUS_SESSION_BUS_ADDRESS:-} ]]; then
    echo 'A real X display and D-Bus session are required' >&2
    exit 1
fi
for tool in xdotool import convert xdg-desktop-portal xdg-desktop-portal-gtk timeout; do
    command -v "$tool" >/dev/null 2>&1 || { echo "Missing $tool" >&2; exit 1; }
done

{
    date -u || exit 1
    uname -a || exit 1
    rustc --version || exit 1
    cargo --version || exit 1
    printf 'DISPLAY=%s\nXDG_CURRENT_DESKTOP=%s\n' "$DISPLAY" "${XDG_CURRENT_DESKTOP:-}"
    command -v xdg-desktop-portal xdg-desktop-portal-gtk xdotool import || exit 1
} >"$output/toolchain.txt" 2>&1 || exit 1

python3 - "$output/source-before.json" <<'PY' || exit 1
import hashlib, json, pathlib, subprocess, sys
root = pathlib.Path.cwd()
sources = json.loads((root / 'scripts/release/sources.json').read_text())['sources']
state = {}
for name, entry in sources.items():
    path = root / name
    revision = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
    tracked = subprocess.check_output(['git', '-C', str(path), 'diff', '--name-only', 'HEAD'], text=True).splitlines()
    if revision != entry['revision'] or tracked:
        raise SystemExit(f'{name}: source differs from clean pinned revision')
    state[name] = {
        'revision': revision,
        'tracked_status': tracked,
        'lock_sha256': hashlib.sha256((path / 'Cargo.lock').read_bytes()).hexdigest(),
    }
pathlib.Path(sys.argv[1]).write_text(json.dumps(state, indent=2) + '\n')
PY

mkdir "$fixture/selected-folder" || exit 1
printf 'sotf-rfd-fixture\n' >"$fixture/open.txt" || exit 1
printf 'cargo build -p sotf-gpui --example rfd_native_smoke\n' >"$output/commands.txt"
failed=0
built=1
if ! (cd sotf && cargo build -p sotf-gpui --example rfd_native_smoke) >"$output/logs/build.log" 2>&1; then
    tail -n 80 "$output/logs/build.log"
    failed=1
    built=0
fi
binary="$root/sotf/target/debug/examples/rfd_native_smoke"
if [[ $built == 1 && ! -x $binary ]]; then
    echo "Missing example binary: $binary" >&2
    failed=1
    built=0
fi

abort_dialog() {
    kill "$active_pid" 2>/dev/null || :
    wait "$active_pid" 2>/dev/null || :
    active_pid=
}

run_dialog() {
    local mode=$1 expected=$2 window= attempt mean painted=0
    printf '%s %s %s\n' "$binary" "$mode" "$expected" >>"$output/commands.txt"
    timeout 75 "$binary" "$mode" "$expected" >"$output/logs/$mode.log" 2>&1 &
    active_pid=$!
    for attempt in {1..100}; do
        window=$(xdotool search --onlyvisible --name "^SOTF RFD $mode$" 2>/dev/null | head -n 1)
        [[ -n $window ]] && break
        if ! kill -0 "$active_pid" 2>/dev/null; then break; fi
        sleep 0.2
    done
    if [[ -z $window ]]; then
        echo "No visible native portal dialog for $mode" | tee -a "$output/logs/$mode.log" >&2
        wait "$active_pid" || :
        active_pid=
        return 1
    fi
    printf 'visible-window-id=%s\n' "$window" >"$output/logs/$mode-window.log"
    xdotool windowactivate --sync "$window" || { abort_dialog; return 1; }
    for attempt in {1..50}; do
        import -window "$window" "$output/screenshots/.$mode-probe.png" || { abort_dialog; return 1; }
        mean=$(convert "$output/screenshots/.$mode-probe.png" -colorspace gray -format '%[fx:mean]' info:) || { abort_dialog; return 1; }
        if awk -v mean="$mean" 'BEGIN { exit !(mean > 0.12) }'; then
            painted=1
            break
        fi
        sleep 0.2
    done
    rm -f "$output/screenshots/.$mode-probe.png"
    printf 'painted-dialog=%s brightness=%s\n' "$painted" "$mean" >>"$output/logs/$mode-window.log"
    import -window root "$output/screenshots/$mode.png" || { abort_dialog; return 1; }
    if [[ $mode == cancel ]]; then
        xdotool key --clearmodifiers Escape || { abort_dialog; return 1; }
    elif [[ $mode == save ]]; then
        xdotool key --clearmodifiers Return || { abort_dialog; return 1; }
    else
        xdotool key --clearmodifiers ctrl+l || { abort_dialog; return 1; }
        xdotool type --clearmodifiers --delay 2 "$expected" || { abort_dialog; return 1; }
        xdotool key --clearmodifiers Return || { abort_dialog; return 1; }
        sleep 0.3
        xdotool key --clearmodifiers Return || { abort_dialog; return 1; }
    fi
    wait "$active_pid" || { active_pid=; return 1; }
    active_pid=
    grep -Fqx "PASS SOTF RFD $mode" "$output/logs/$mode.log" && [[ $painted == 1 ]]
}

if [[ $built == 1 ]]; then
    run_dialog open "$fixture/open.txt" || failed=1
    run_dialog folder "$fixture/selected-folder" || failed=1
    run_dialog save "$fixture/saved-$(date +%s)-$$.txt" || failed=1
    run_dialog cancel "$fixture/open.txt" || failed=1
fi

python3 - "$output/source-before.json" "$output/source-after.json" <<'PY' || failed=1
import hashlib, json, pathlib, subprocess, sys
root = pathlib.Path.cwd()
before = json.loads(pathlib.Path(sys.argv[1]).read_text())
after = {}
for name in before:
    path = root / name
    after[name] = {
        'revision': subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip(),
        'tracked_status': subprocess.check_output(['git', '-C', str(path), 'diff', '--name-only', 'HEAD'], text=True).splitlines(),
        'lock_sha256': hashlib.sha256((path / 'Cargo.lock').read_bytes()).hexdigest(),
    }
pathlib.Path(sys.argv[2]).write_text(json.dumps(after, indent=2) + '\n')
if after != before:
    raise SystemExit('Pinned source revision or Cargo.lock changed')
PY
exit "$failed"
