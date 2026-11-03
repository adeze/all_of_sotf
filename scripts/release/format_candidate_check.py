#!/usr/bin/env python3
"""Guarded ten-workspace formatting and original nine-workspace Clippy inventory."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.release import all_features_candidate_check as owned
from scripts.release.checkout_sources import read_manifest
from scripts.release.qa import source_issues, source_state, workspace_map


CLIPPY_RECIPES = {
    "autoeq": "clippy",
    "gpui-toolkit": "lint-host",
    "math-audio": "lint",
    "sofa-reader": "clippy",
    "sotf": "lint",
    "sotf-capture": "clippy",
    "sotf-daw": "lint",
    "sotf-systemwide": "lint",
}


def snapshot(pins: dict[str, str]) -> dict:
    names = list(workspace_map())
    state = source_state(ROOT, names, "macos" if sys.platform == "darwin" else "linux")
    issues = source_issues(state, state, True)
    for name, revision in pins.items():
        if state.get(name, {}).get("revision") != revision:
            issues.append(f"{name}: source revision differs from manifest")
        if not state.get(name, {}).get("lock_sha256"):
            issues.append(f"{name}: canonical Cargo.lock absent")
    if not state.get("autoeq", {}).get("nested_lock_sha256"):
        issues.append("AutoEQ nested GPUI examples Cargo.lock absent")
    layout = owned.root_status(pins)
    if layout["missing"] or layout["unexpected"] or layout["allowed_siblings"]:
        issues.append("root checkout layout invalid")
    return {"workspaces": state, "layout": layout, "issues": issues}


def safe_to_start_next_phase(report: dict) -> bool:
    """Source failures may continue; interruption or owned cleanup failure may not."""
    return (report.get("interrupted") is False
            and all(command.get("cleanup_ok") is True
                    and command.get("interrupted") is False
                    and not command.get("survivors")
                    for command in report.get("commands", [])))


def main() -> int:
    signal.signal(signal.SIGINT, owned.interrupt)
    signal.signal(signal.SIGTERM, owned.interrupt)
    if len(sys.argv) != 2:
        print("usage: format_candidate_check.py OUTPUT", file=sys.stderr)
        return 2
    if sys.flags.optimize or os.environ.get("PYTHONOPTIMIZE") not in (None, "", "0"):
        raise RuntimeError("optimized Python is not allowed for release evidence")
    output = (ROOT / sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report: dict = {"status": "FAIL", "scope": "ten fmt --check and nine original Clippy recipes",
                    "commands": [], "errors": []}
    before = None
    root_revision = None
    manifest_bytes = None
    pins = None
    try:
        if sys.platform == "linux":
            import ctypes
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(36, 1, 0, 0, 0) != 0:
                raise RuntimeError("could not register Linux child subreaper")
        manifest = ROOT / "scripts/release/sources.json"
        manifest_bytes = manifest.read_bytes()
        (output / "sources.json").write_bytes(manifest_bytes)
        _, _, pins = read_manifest(manifest)
        root_revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                                text=True).strip()
        before = snapshot(pins)
        (output / "sources-before.json").write_text(json.dumps(before, indent=2) + "\n")
        report["errors"].extend(before["issues"])
        if report["errors"]:
            raise RuntimeError("source preflight failed")
        names = list(workspace_map())
        if set(names) != set(CLIPPY_RECIPES):
            raise RuntimeError("workspace inventory differs from original Clippy recipes")
        commands = [(f"fmt-{name}", name, ["cargo", "fmt", "--all", "--", "--check"])
                    for name in names]
        commands.append(("fmt-autoeq-gpui-examples", "autoeq",
                         ["cargo", "fmt", "--manifest-path",
                          "crates/autoeq-gpui-examples/Cargo.toml", "--all", "--", "--check"]))
        commands.extend((f"clippy-{name}", name, ["just", recipe])
                        for name, recipe in CLIPPY_RECIPES.items())
        for label, workspace, argv in commands:
            if owned.STOP:
                raise KeyboardInterrupt("interrupted before next command")
            result = owned.run(label, argv, output, cwd_name=workspace)
            result["recipe_workspace"] = workspace
            report["commands"].append(result)
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            if result["exit_code"] != 0:
                report["errors"].append(f"{label}: command failed")
            if not result["cleanup_ok"] or owned.STOP:
                raise RuntimeError(f"{label}: cleanup failed or interrupted")
    except (Exception, KeyboardInterrupt) as error:
        report["errors"].append(str(error))
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            if pins is not None:
                after = snapshot(pins)
                (output / "sources-after.json").write_text(json.dumps(after, indent=2) + "\n")
                report["errors"].extend(after["issues"])
                if before is not None:
                    report["errors"].extend(source_issues(before["workspaces"], after["workspaces"], True))
                    if before["layout"] != after["layout"]:
                        report["errors"].append("root checkout layout changed")
            if root_revision is not None and subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip() != root_revision:
                report["errors"].append("root revision changed")
            if manifest_bytes is not None and (ROOT / "scripts/release/sources.json").read_bytes() != manifest_bytes:
                report["errors"].append("source manifest changed")
        except Exception as error:
            report["errors"].append(f"final source guard: {error}")
        report["interrupted"] = bool(owned.STOP)
        if owned.STOP:
            report["errors"].append("interrupted during final source guard")
        report["status"] = "PASS" if not report["errors"] and len(report["commands"]) == 19 else "FAIL"
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
