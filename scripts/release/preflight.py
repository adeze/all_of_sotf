#!/usr/bin/env python3
"""Check aggregate source layout and generated inputs before starting Cargo."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from workspaces import source_names, vendor_names, workspace_names
from checkout_sources import gitlink_revision, read_manifest


def check_layout(root: Path) -> list[str]:
    errors = []
    for name in workspace_names(local_only=True):
        if not (root / name / "Cargo.toml").is_file():
            errors.append(f"{name}: missing submodule Cargo.toml; initialize the submodule")
    for name in workspace_names(local_only=True) + vendor_names():
        if gitlink_revision(root, name) is None:
            errors.append(f"{name}: inventory requires a tracked submodule")
    return errors


def check_recipes(root: Path) -> list[str]:
    text = (root / "Justfile").read_text(encoding="utf-8")
    paths = re.findall(
        r"(?:--justfile|--manifest-path)\s+([\w.-]+/(?:Justfile|Cargo\.toml))", text
    )
    return [f"Justfile: missing recipe input {path}"
            for path in sorted(set(paths)) if not (root / path).is_file()]


def check_generated_manifest(root: Path) -> list[str]:
    spec = importlib.util.spec_from_file_location("mono_generate", root / "scripts/mono/generate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    generated, _ = module.generate(root)
    path = root / "Cargo.toml"
    if not path.is_file() or path.read_text(encoding="utf-8") != generated:
        return ["root Cargo.toml is stale; run python3 scripts/mono/generate.py"]
    return []


def check_release_pins(root: Path) -> list[str]:
    _, _, pins = read_manifest(root / "scripts/release/sources.json")
    errors = []
    for name in source_names():
        expected = pins[name]
        path = root / name
        if not path.is_dir():
            errors.append(f"{name}: release source is absent; materialize the pinned source candidate")
            continue
        actual = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=False)
        if actual.returncode or actual.stdout.strip() != expected:
            errors.append(f"{name}: checkout differs from release pin {expected}")
        status = subprocess.run(["git", "-C", str(path), "status", "--porcelain", "--untracked-files=normal"],
                                capture_output=True, text=True, check=False)
        if status.returncode or status.stdout:
            errors.append(f"{name}: release source is dirty or its status is unavailable")
        indexed = gitlink_revision(root, name)
        if indexed is not None and indexed != expected:
            errors.append(f"{name}: root gitlink differs from release pin {expected}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", action="store_true",
                        help="also require every pinned release source, including external dependencies")
    args = parser.parse_args(argv)
    try:
        errors = check_layout(ROOT) + check_recipes(ROOT) + check_generated_manifest(ROOT)
        if args.release:
            errors += check_release_pins(ROOT)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"preflight: {exc}", file=sys.stderr)
        return 1
    for error in errors:
        print(f"preflight: {error}", file=sys.stderr)
    if errors:
        return 1
    scope = "release candidate" if args.release else "local submodule checkout"
    print(f"preflight: {scope} inputs are consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
