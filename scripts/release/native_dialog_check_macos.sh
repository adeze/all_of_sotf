#!/usr/bin/env bash
# Exercise only the smoke example's native panels in a logged-in macOS CI session.
set -uo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd) || exit 1
cd "$root" || exit 1
output="$root/release-native-dialog-evidence-macos"
mkdir -p "$output/screenshots" "$output/logs" || exit 1
cp scripts/release/sources.json "$output/sources.json" || exit 1
fixture=$(mktemp -d /private/tmp/sotf-rfd-macos.XXXXXXXX) || exit 1
active_pid=
watchdog_pid=
cleanup() {
    if [[ -n $watchdog_pid ]]; then kill "$watchdog_pid" 2>/dev/null || :; fi
    if [[ -n $active_pid ]]; then
        kill -TERM "$active_pid" 2>/dev/null || :
        (sleep 3; kill -KILL "$active_pid" 2>/dev/null || :) &
        local reaper_pid=$!
        wait "$active_pid" 2>/dev/null || :
        kill "$reaper_pid" 2>/dev/null || :
        wait "$reaper_pid" 2>/dev/null || :
    fi
    rm -rf "$fixture"
}
trap cleanup EXIT

if [[ $(uname -s) != Darwin ]]; then
    echo 'A logged-in macOS CI session is required' >&2
    exit 1
fi
for tool in swiftc osascript screencapture python3; do
    command -v "$tool" >/dev/null 2>&1 || { echo "Missing $tool" >&2; exit 1; }
done

{
    date -u
    uname -a
    rustc --version
    cargo --version
    printf 'runner_user=%s console_user=%s uid=%s\n' \
        "$(id -un)" "$(stat -f %Su /dev/console)" "$(id -u)"
    if launchctl print "gui/$(id -u)" >/dev/null 2>&1; then
        echo 'runner_gui_session=present'
    else
        echo 'runner_gui_session=missing'
    fi
} >"$output/toolchain.txt" 2>&1
if [[ $(stat -f %Su /dev/console) != "$(id -un)" ]] ||
    ! launchctl print "gui/$(id -u)" >/dev/null 2>&1; then
    echo 'The CI runner does not own the active macOS GUI session' >&2
    exit 1
fi

source_guard() {
    python3 - "$1" "$output" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path.cwd()
sys.path.insert(0, str(root / "scripts" / "release"))
from qa import source_issues, source_state

mode, output = sys.argv[1], pathlib.Path(sys.argv[2])
sources = json.loads((root / "scripts/release/sources.json").read_text())["sources"]
state = source_state(root, list(sources), "macos")
path = output / f"source-{mode}.json"
path.write_text(json.dumps(state, indent=2) + "\n")
if mode == "before":
    for name, entry in sources.items():
        actual = state[name]
        if actual.get("revision") != entry["revision"] or actual.get("dirty") is not False:
            raise SystemExit(f"{name}: source differs from clean pinned revision")
else:
    before = json.loads((output / "source-before.json").read_text())
    issues = source_issues(before, state, require_clean=True)
    if issues:
        raise SystemExit("; ".join(issues))
PY
}
source_guard before || exit 1

helper="$fixture/native-dialog-window"
if ! swiftc scripts/release/native_dialog_macos_window.swift -o "$helper" \
    >"$output/logs/window-helper-build.log" 2>&1; then
    tail -n 80 "$output/logs/window-helper-build.log"
    exit 1
fi
# These are non-mutating checks for this helper's identity. AppleScript and
# screencapture have separate TCC identities; their bounded actions below are
# the authoritative permission checks.
"$helper" prereq >"$output/tcc-preflight.txt" 2>&1 || exit 1

cat >"$fixture/drive.applescript" <<'APPLESCRIPT'
on run argv
    set targetPid to (item 1 of argv) as integer
    set action to item 2 of argv
    set requestedPath to item 3 of argv
    set expectedMessage to item 4 of argv
    tell application "System Events"
        set targetProcess to first application process whose unix id is targetPid
        if not (exists targetProcess) then error "smoke example process is absent"
        set frontmost of targetProcess to true
        tell targetProcess
            if (count of windows) is not 1 then
                error "expected exactly one native panel owned by the smoke example"
            end if
            set messageFound to false
            repeat with uiItem in (entire contents of first window)
                try
                    if (role of uiItem) is "AXStaticText" and (value of uiItem) is expectedMessage then
                        set messageFound to true
                        exit repeat
                    end if
                end try
            end repeat
            if not messageFound then error "owned native panel does not show the expected mode message"
        end tell
        if (unix id of (first application process whose frontmost is true)) is not targetPid then
            error "focus moved away from the smoke example"
        end if
        if action is "open" or action is "folder" then
            keystroke "g" using {command down, shift down}
            delay 0.3
            if (unix id of (first application process whose frontmost is true)) is not targetPid then
                error "focus moved before entering the private fixture path"
            end if
            keystroke requestedPath
            key code 36
            delay 0.4
            if (unix id of (first application process whose frontmost is true)) is not targetPid then
                error "focus moved before confirming the native selection"
            end if
            key code 36
        else if action is "save" then
            key code 36
        else if action is "cancel" then
            key code 53
        else if action is not "focus" then
            error "unknown dialog action"
        end if
    end tell
end run
APPLESCRIPT

drive() {
    python3 - "$fixture/drive.applescript" "$active_pid" "$2" "$3" "SOTF RFD $1" <<'PY'
import subprocess
import sys

try:
    result = subprocess.run(["osascript", *sys.argv[1:]], text=True,
                            capture_output=True, timeout=15)
except subprocess.TimeoutExpired:
    raise SystemExit("AppleScript timed out while driving the owned native panel")
if result.returncode:
    raise SystemExit(f"AppleScript could not control the owned native panel: {result.stderr.strip()}")
PY
}

mkdir "$fixture/selected-folder" || exit 1
printf 'sotf-rfd-fixture\n' >"$fixture/open.txt"
printf 'cargo build -p sotf-gpui --example rfd_native_smoke\n' >"$output/commands.txt"
failed=0
if ! (cd sotf && cargo build -p sotf-gpui --example rfd_native_smoke) \
    >"$output/logs/build.log" 2>&1; then
    tail -n 80 "$output/logs/build.log"
    failed=1
fi
binary="$root/sotf/target/debug/examples/rfd_native_smoke"
if [[ ! -x $binary ]]; then
    echo "Missing example binary: $binary" >&2
    failed=1
fi

run_dialog() {
    local mode=$1 expected=$2 window= stats= attempt= status=0 painted=0 last_capture_error=
    printf '%s %s %s\n' "$binary" "$mode" "$expected" >>"$output/commands.txt"
    "$binary" "$mode" "$expected" >"$output/logs/$mode.log" 2>&1 &
    active_pid=$!
    (sleep 75; kill -TERM "$active_pid" 2>/dev/null || :; sleep 5; kill -KILL "$active_pid" 2>/dev/null || :) &
    watchdog_pid=$!
    for attempt in {1..100}; do
        if grep -Fqx "READY SOTF RFD $mode" "$output/logs/$mode.log"; then
            window=$("$helper" window "$active_pid" 2>/dev/null) && break
        fi
        if ! kill -0 "$active_pid" 2>/dev/null; then break; fi
        sleep 0.2
    done
    if [[ -z $window ]]; then
        "$helper" window "$active_pid" >"$output/logs/$mode-owned-windows.log" 2>&1 || :
        cat "$output/logs/$mode-owned-windows.log" >&2
        echo "No visible panel owned by PID $active_pid for $mode" >&2
        status=1
    elif ! drive "$mode" focus "$expected" >"$output/logs/$mode-control.log" 2>&1; then
        cat "$output/logs/$mode-control.log" >&2
        status=1
    else
        for attempt in {1..35}; do
            if [[ $("$helper" window "$active_pid" 2>/dev/null) != "$window" ]]; then
                last_capture_error='the owned native panel changed before capture'
                break
            fi
            if screencapture -x -o -l "$window" "$output/screenshots/$mode.png" \
                >"$output/logs/$mode-screenshot.log" 2>&1 &&
                stats=$("$helper" stats "$output/screenshots/$mode.png" 2>&1); then
                painted=1
                break
            fi
            last_capture_error=$stats
            sleep 0.2
        done
        if [[ $painted != 1 ]]; then
            echo "Owned $mode panel did not paint: $last_capture_error" >&2
            cat "$output/logs/$mode-screenshot.log" >&2
            status=1
        else
            printf 'pid=%s window=%s %s\n' "$active_pid" "$window" "$stats" \
                >"$output/logs/$mode-window.log"
        fi
        if [[ $status == 0 ]] &&
            ! drive "$mode" "$mode" "$expected" >"$output/logs/$mode-control.log" 2>&1; then
            cat "$output/logs/$mode-control.log" >&2
            status=1
        fi
    fi
    if [[ $status != 0 ]]; then kill -KILL "$active_pid" 2>/dev/null || :; fi
    if ! wait "$active_pid"; then status=1; fi
    active_pid=
    kill "$watchdog_pid" 2>/dev/null || :
    wait "$watchdog_pid" 2>/dev/null || :
    watchdog_pid=
    if ! grep -Fqx "PASS SOTF RFD $mode" "$output/logs/$mode.log"; then status=1; fi
    if grep -Fq 'fallback to sync dialog' "$output/logs/$mode.log"; then
        echo "The $mode dialog used rfd's synchronous fallback" >&2
        status=1
    fi
    return "$status"
}

if [[ $failed == 0 ]]; then
    run_dialog open "$fixture/open.txt" || failed=1
    run_dialog folder "$fixture/selected-folder" || failed=1
    run_dialog save "$fixture/saved-$(date +%s)-$$.txt" || failed=1
    run_dialog cancel "$fixture/open.txt" || failed=1
fi
source_guard after || failed=1
exit "$failed"
