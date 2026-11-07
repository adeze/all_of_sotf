#!/usr/bin/env python3
"""Read the shared inventory used by workspace checks and release tooling."""

import argparse
import json
from pathlib import Path


INVENTORY = Path(__file__).resolve().parent / "quality-matrix" / "repos.json"


def workspace_names(*, local_only: bool = False) -> list[str]:
    """Return the Rust workspace paths in stable inventory order."""
    entries = json.loads(INVENTORY.read_text(encoding="utf-8"))["repositories"]
    for entry in entries:
        if entry.get("checkout", "submodule") not in {"submodule", "git-dependency"}:
            raise ValueError(f"unknown checkout kind for {entry['path']!r}")
    names = [entry["path"] for entry in entries if entry["primary_language"] == "Rust"
             and (not local_only or entry.get("checkout", "submodule") == "submodule")]
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true", help="list submodule workspaces only")
    args = parser.parse_args()
    print("\n".join(workspace_names(local_only=args.local)))
