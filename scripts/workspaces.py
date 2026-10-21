#!/usr/bin/env python3
"""Read the shared inventory used by workspace checks and release tooling."""

import argparse
import json
from pathlib import Path


INVENTORY = Path(__file__).resolve().parent / "quality-matrix" / "repos.json"


def workspace_names() -> list[str]:
    """Return the Rust workspace paths in stable inventory order."""
    entries = json.loads(INVENTORY.read_text(encoding="utf-8"))["repositories"]
    names = [entry["path"] for entry in entries if entry["primary_language"] == "Rust"]
    if len(names) != len(set(names)):
        raise ValueError("duplicate workspace paths in repository inventory")
    for name in names:
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError(f"workspace must be a direct child directory: {name!r}")
    return names


def vendor_names() -> list[str]:
    """Return source repositories containing shared vendored crates."""
    names = json.loads(INVENTORY.read_text(encoding="utf-8")).get("vendored_repositories", [])
    if len(names) != len(set(names)):
        raise ValueError("duplicate vendored repository paths in inventory")
    for name in names:
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError(f"vendored repository must be a direct child directory: {name!r}")
    return names


def source_names() -> list[str]:
    """Return all source repositories needed to materialize a release."""
    names = workspace_names() + vendor_names()
    if len(names) != len(set(names)):
        raise ValueError("source repository paths must be unique")
    return names


def application_paths() -> list[str]:
    """Return companion sibling checkouts without adding them to Rust releases."""
    entries = json.loads(INVENTORY.read_text(encoding="utf-8")).get("companion_applications", [])
    paths = [entry["path"] for entry in entries]
    if len(paths) != len(set(paths)):
        raise ValueError("duplicate companion application paths in inventory")
    for path in paths:
        parts = Path(path).parts
        if len(parts) != 2 or parts[0] != ".." or parts[1] in {".", ".."}:
            raise ValueError(f"application must be a sibling checkout: {path!r}")
    return paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--applications", action="store_true", help="List companion Swift/Python checkout paths")
    mode.add_argument("--all-sources", action="store_true", help="List Rust, vendor and companion checkout paths")
    args = parser.parse_args()
    paths = application_paths() if args.applications else (
        source_names() + application_paths() if args.all_sources else workspace_names()
    )
    print("\n".join(paths))
