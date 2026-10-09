#!/usr/bin/env python3
"""Hide Cargo lint warnings whose primary source is a local third-party crate."""

import json
from pathlib import Path
import subprocess
import sys

THIRD_PARTIES = Path(__file__).resolve().parents[2] / "sotf-3rdparties"
BUILD_COMMANDS = {"build", "check", "clippy", "run", "test", "rustc"}
CARGO_METADATA = {"compiler-artifact", "build-script-executed", "build-finished"}


def is_third_party_warning(message: dict) -> bool:
    """Suppress warnings only when every primary span belongs to third parties."""
    if message.get("level") != "warning":
        return False
    spans = [span for span in message.get("spans", []) if span.get("is_primary")]
    return bool(spans) and all(
        Path(span["file_name"]).resolve().is_relative_to(THIRD_PARTIES)
        for span in spans
    )


def main() -> int:
    command = sys.argv[1:]
    if len(command) < 2:
        raise SystemExit("Usage: quiet-thirdparty-lints.py CARGO COMMAND [ARGUMENTS...]")
    if command[1] not in BUILD_COMMANDS or any(
        arg.startswith("--message-format") for arg in command[2:]
    ):
        return subprocess.call(command)
    # Insert before a possible `--` separating executable/test arguments.
    command.insert(2, "--message-format=json")
    with subprocess.Popen(command, stdout=subprocess.PIPE, text=True) as child:
        assert child.stdout is not None
        try:
            for line in child.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    print(line, end="", flush=True)
                    continue
                if not isinstance(event, dict):
                    print(line, end="", flush=True)
                elif event.get("reason") == "compiler-message":
                    message = event["message"]
                    if not is_third_party_warning(message):
                        rendered = message.get("rendered")
                        print(rendered if rendered is not None else line,
                              end="", file=sys.stderr, flush=True)
                elif event.get("reason") not in CARGO_METADATA:
                    print(line, end="", flush=True)
            return child.wait()
        except KeyboardInterrupt:
            # Cargo shares the terminal process group and receives Ctrl-C too.
            child.wait()
            return 130


if __name__ == "__main__":
    raise SystemExit(main())
