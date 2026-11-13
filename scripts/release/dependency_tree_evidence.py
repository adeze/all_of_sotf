#!/usr/bin/env python3
"""Capture target-specific Cargo dependency evidence without changing release gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKSPACES = (
    "math-audio",
    "gpui-toolkit",
    "sofa-reader",
    "autoeq",
    "sotf-daw",
    "sotf-capture",
    "sotf",
    "sotf-systemwide",
)
DESKTOP_FEATURES = "onnx,hal,gpu-2d,gpu-3d,iamf,streaming,hls"
NIH_RECIPE = Path("crates/sotf-plugins/crates/plugins-nih/Justfile")


def run_capture(argv: list[str], *, cwd: Path, log: Path) -> int:
    with log.open("w", encoding="utf-8") as output:
        output.write("argv: " + json.dumps(argv) + "\n")
        output.flush()
        result = subprocess.run(argv, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, check=False)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "scripts/release/sources.json", output / "sources.json")
    rustc = subprocess.run(["rustc", "-vV"], text=True, capture_output=True, check=True).stdout
    (output / "rustc-vV.txt").write_text(rustc)
    host_match = re.search(r"^host: (\S+)$", rustc, re.MULTILINE)
    if host_match is None:
        raise SystemExit("rustc -vV did not report a host triple")
    target = host_match.group(1)
    lock_before = {
        workspace: hashlib.sha256((ROOT / workspace / "Cargo.lock").read_bytes()).hexdigest()
        for workspace in WORKSPACES
    }
    records: list[dict[str, object]] = []
    recipe_bytes = (ROOT / "sotf-daw" / NIH_RECIPE).read_bytes()
    feature_match = re.search(r'^NIH_FEATURES := "([a-z0-9 -]+)"$', recipe_bytes.decode(), re.MULTILINE)
    if feature_match is None:
        raise SystemExit(f"Cannot read NIH_FEATURES from {NIH_RECIPE}")
    nih_features = feature_match.group(1).split()
    if len(nih_features) != len(set(nih_features)):
        raise SystemExit("NIH_FEATURES contains duplicates")
    commands = [
        (
            "sotf-desktop-production",
            "sotf",
            [
                "cargo", "tree", "--target", target,
                "--edges", "normal,build", "--duplicates", "-p", "sotf-gpui",
                "--features", DESKTOP_FEATURES,
            ],
            "sotf/Justfile desktop release: sotf-desktop binary; default features enabled",
        )
    ]
    for feature in nih_features:
        commands.append(
            (
                f"daw-plugins-nih-{feature}",
                "sotf-daw",
                [
                    "cargo", "tree", "--target", target,
                    "--edges", "normal,build", "--duplicates", "-p", "plugins-nih",
                    "--no-default-features", "--features", feature,
                ],
                f"plugins-nih/Justfile production VST3/CLAP feature: {feature}",
            )
        )
    for workspace in WORKSPACES:
        commands.append(
            (
                f"{workspace}-workspace-all-features",
                workspace,
                ["cargo", "tree", "--target", target, "--edges", "normal,build", "--duplicates", "--workspace", "--all-features"],
                "Diagnostic upper-bound inventory; not a production recipe",
            )
        )
    report = {
        "schema": 1, "read_only": True, "release_qa": False,
        "host": platform.platform(), "target": target,
        "daw_recipe": str(NIH_RECIPE),
        "daw_recipe_sha256": hashlib.sha256(recipe_bytes).hexdigest(),
        "daw_features": nih_features,
        "lock_before_sha256": lock_before,
        "commands": records,
    }

    def checkpoint() -> None:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")

    checkpoint()
    failed = False
    for name, workspace, argv, scope in commands:
        log = output / f"{name}.log"
        code = run_capture(argv, cwd=ROOT / workspace, log=log)
        records.append({"name": name, "workspace": workspace, "scope": scope, "target": target, "argv": argv, "exit_code": code, "log": log.name})
        checkpoint()
        print(f"[{name}] exit {code}; log: {log}", flush=True)
        if code:
            failed = True
            print("\n".join(log.read_text(errors="replace").splitlines()[-40:]), flush=True)
    lock_after = {
        workspace: hashlib.sha256((ROOT / workspace / "Cargo.lock").read_bytes()).hexdigest()
        for workspace in WORKSPACES
    }
    report["lock_after_sha256"] = lock_after
    report["lock_changed"] = [workspace for workspace in WORKSPACES if lock_before[workspace] != lock_after[workspace]]
    failed |= bool(report["lock_changed"])
    checkpoint()
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
