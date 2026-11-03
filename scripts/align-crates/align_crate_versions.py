#!/usr/bin/env python3
"""Align external dependency versions across the inventoried Rust workspaces.

The repository contains independent Cargo workspaces (no top-level
Cargo.toml). This script scans every Cargo.toml under those roots, finds
external crates that are declared with different version requirements, and
optionally rewrites them all to use a single chosen version.

Usage:
    python3 scripts/align-crates/align_crate_versions.py            # report only
    python3 scripts/align-crates/align_crate_versions.py --apply    # rewrite files

Path dependencies and git dependencies without a `version` key are ignored,
because their effective version is determined by the source, not the version
requirement string. Packages that are referenced from both crates.io and git
sources are reported as skipped, because aligning their version strings would
change the effective source for at least one workspace.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from functools import total_ordering
from collections import defaultdict
from pathlib import Path
from typing import Any

import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workspaces import workspace_names

try:
    import tomlkit
except ImportError:  # pragma: no cover
    tomlkit = None

ROOTS = workspace_names()

# Directories that are git worktrees, experiment worktrees, build output, or
# vendored forks used as [patch] sources. Their dependency versions are
# intentionally independent of the main workspace declarations.
IGNORED_DIR_PARTS = {
    "target", "target-static", ".docker-target", ".git", ".worktrees",
    "worktrees", ".muse", ".evo", "3rdparties", "vendor", "node_modules",
    "venv", ".venv", "data_generated", "dist", "audit", ".tokensave",
}

# These crates are coupled to platform/audio/UI behavior and should not be
# bumped by the automatic fixer unless the caller explicitly opts in.
DEFAULT_RISKY_PACKAGES = {"cpal", "gpui"}


class InvalidVersion(ValueError):
    pass


@total_ordering
class Version:
    """Small SemVer-ish sorter for Cargo version requirements."""

    def __init__(self, value: str):
        self.original = value
        base = value.split("+", 1)[0].split("-", 1)[0]
        parts = base.split(".")
        if not parts or not all(part.isdigit() for part in parts):
            raise InvalidVersion(value)
        padded = [int(part) for part in parts]
        while len(padded) < 3:
            padded.append(0)
        self.major, self.minor, self.patch = padded[:3]
        self.parts = tuple(padded)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self.parts < other.parts

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self.parts == other.parts

    def __str__(self) -> str:
        return self.original


def require_tomlkit() -> Any:
    if tomlkit is None:
        raise SystemExit(
            "tomlkit is required for write operations; install it with: pip install tomlkit"
        )
    return tomlkit


def find_cargo_tomls(repo_root: Path) -> list[Path]:
    """Find manifests without traversing worktrees, snapshots, or build output."""
    files: list[Path] = []
    for name in ROOTS:
        root = repo_root / name
        if not root.is_dir():
            continue
        for directory, subdirs, filenames in os.walk(root, followlinks=False):
            base = Path(directory)
            subdirs[:] = [
                name for name in subdirs
                if name not in IGNORED_DIR_PARTS
                and not (base / name).is_symlink()
                and not (base / name / ".git").exists()
            ]
            if "Cargo.toml" in filenames:
                path = base / "Cargo.toml"
                if not path.is_symlink():
                    files.append(path)
    files.sort()
    return files


def dependency_sections(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return a flat mapping of section-name -> deps-dict."""
    sections: dict[str, dict[str, Any]] = {}
    if "dependencies" in doc:
        sections["dependencies"] = doc["dependencies"]
    if "dev-dependencies" in doc:
        sections["dev-dependencies"] = doc["dev-dependencies"]
    if "build-dependencies" in doc:
        sections["build-dependencies"] = doc["build-dependencies"]
    if "workspace" in doc and isinstance(doc["workspace"], dict):
        ws = doc["workspace"]
        if "dependencies" in ws:
            sections["workspace.dependencies"] = ws["dependencies"]
    for target, target_doc in doc.get("target", {}).items():
        if not isinstance(target_doc, dict):
            continue
        for kind in ("dependencies", "dev-dependencies", "build-dependencies"):
            if kind in target_doc:
                sections[f"target.{target}.{kind}"] = target_doc[kind]
    return sections


def package_info(key: str, value: Any) -> tuple[str, str, str] | None:
    """Return (package_name, version_string, source_key) for a dependency.

    Returns None for path-only dependencies or for workspace/git references
    that do not carry an explicit version requirement.

    source_key is "crates.io" for registry dependencies or "git:<url>" for
    git dependencies. Mixed source_keys for the same package prevent alignment.
    """
    if isinstance(value, str):
        return key, value, "crates.io"
    if not isinstance(value, Mapping):
        return None
    if "path" in value:
        return None
    pkg = value.get("package", key)
    version = value.get("version")
    if version is None:
        return None
    if "git" in value:
        source_key = f"git:{value['git']}"
    else:
        source_key = "crates.io"
    return pkg, version, source_key


def collect_versions(
    repo_root: Path,
) -> dict[str, list[tuple[Path, str, str, str]]]:
    """Map package name -> list of (file, section, version, source_key)."""
    by_package: dict[str, list[tuple[Path, str, str, str]]] = defaultdict(list)
    for path in find_cargo_tomls(repo_root):
        with path.open("rb") as f:
            doc = tomllib.load(f)
        for section, deps in dependency_sections(doc).items():
            for key, value in deps.items():
                info = package_info(key, value)
                if info is None:
                    continue
                pkg, version, source_key = info
                by_package[pkg].append((path, section, version, source_key))
    return by_package


def normalize_version(version: str) -> Version:
    """Turn a Cargo version requirement like '=21.0.0' into a sortable Version."""
    v = version.lstrip("=^~<>!")
    v = v.split(",")[0].strip()
    return Version(v)


def version_sort_key(v: str) -> tuple[int, Version]:
    try:
        return 0, normalize_version(v)
    except InvalidVersion:
        return 1, Version("0")


def compatibility_key(version: str) -> tuple[int, int | None] | None:
    """Return the SemVer compatibility group Cargo can unify safely."""
    try:
        parsed = normalize_version(version)
    except InvalidVersion:
        return None
    if parsed.major == 0:
        return (0, parsed.minor)
    return (parsed.major, None)


def is_risky_package(pkg: str, risky_packages: set[str]) -> bool:
    return pkg in risky_packages or any(pkg.startswith(f"{risky}-") for risky in risky_packages)


def group_versions_by_compatibility(
    versions: dict[str, list[tuple[Path, str]]],
    allow_major: bool,
) -> list[dict[str, list[tuple[Path, str]]]]:
    if allow_major:
        return [versions]

    groups: dict[tuple[int, int | None], dict[str, list[tuple[Path, str]]]] = defaultdict(dict)
    for version, locations in versions.items():
        key = compatibility_key(version)
        if key is None:
            continue
        groups[key][version] = locations
    return list(groups.values())


def find_conflicts(
    by_package: dict[str, list[tuple[Path, str, str, str]]],
) -> tuple[dict[str, dict[str, list[tuple[Path, str]]]], dict[str, set[str]]]:
    """Return (conflicts, skipped) where conflicts are alignable mismatches.

    skipped maps package name -> set of source keys for packages that appear
    with mixed sources (e.g. crates.io and git) and therefore cannot be safely
    aligned by changing version strings alone.
    """
    conflicts: dict[str, dict[str, list[tuple[Path, str]]]] = {}
    skipped: dict[str, set[str]] = {}
    for pkg, entries in by_package.items():
        sources = {source_key for _path, _section, _version, source_key in entries}
        if len(sources) > 1:
            skipped[pkg] = sources
            continue

        versions: dict[str, list[tuple[Path, str]]] = defaultdict(list)
        for path, section, version, _source_key in entries:
            versions[version].append((path, section))
        if len(versions) > 1:
            conflicts[pkg] = dict(versions)
    return conflicts, skipped


def pick_target(version_entries: dict[str, list[tuple[Path, str]]]) -> str:
    """Choose the highest normalized SemVer version as the alignment target."""
    return max(version_entries.keys(), key=version_sort_key)


def report_conflicts(
    conflicts: dict[str, dict[str, list[tuple[Path, str]]]],
    skipped: dict[str, set[str]],
) -> int:
    if skipped:
        print(f"Skipped {len(skipped)} package(s) with mixed sources:\n")
        for pkg in sorted(skipped):
            print(f"  {pkg}: {', '.join(sorted(skipped[pkg]))}")
        print()

    if not conflicts:
        if not skipped:
            print("No version mismatches found across the inventoried workspaces.")
        else:
            print("No additional alignable version mismatches found.")
        return 0

    print(f"Found {len(conflicts)} package(s) with mismatched versions:\n")
    for pkg in sorted(conflicts):
        versions = conflicts[pkg]
        target = pick_target(versions)
        print(f"{pkg}  -> align to {target}")

        for version in sorted(versions, key=version_sort_key):
            marker = " *" if version == target else ""
            try:
                normalize_version(version)
            except InvalidVersion:
                marker += " (unparseable)"
            print(f"    {version}{marker}")
            for path, section in versions[version]:
                rel = path.relative_to(Path.cwd())
                print(f"        {rel}  [{section}]")
        print()
    return 1


def build_change_map(
    conflicts: dict[str, dict[str, list[tuple[Path, str]]]],
    risky_packages: set[str] | None = None,
    allow_major: bool = False,
) -> dict[Path, dict[str, dict[str, str]]]:
    """Map file -> section -> package-name -> target-version."""
    if risky_packages is None:
        risky_packages = DEFAULT_RISKY_PACKAGES
    changes: dict[Path, dict[str, dict[str, str]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for pkg, versions in conflicts.items():
        if is_risky_package(pkg, risky_packages):
            continue

        for group in group_versions_by_compatibility(versions, allow_major):
            if len(group) <= 1:
                continue
            target = pick_target(group)
            for version, locations in group.items():
                if version == target:
                    continue
                for path, section in locations:
                    changes[path][section][pkg] = target
    return changes


def report_change_plan(
    repo_root: Path,
    conflicts: dict[str, dict[str, list[tuple[Path, str]]]],
    skipped: dict[str, set[str]],
    risky_packages: set[str],
    allow_major: bool,
) -> int:
    """Report the exact safe changes that --apply would make."""
    changes = build_change_map(
        conflicts,
        risky_packages=risky_packages,
        allow_major=allow_major,
    )
    report_skipped_conflicts(conflicts, skipped, risky_packages, allow_major)

    if not changes:
        print("No safe version mismatches to fix.")
        return 0

    print(f"Planned updates in {len(changes)} Cargo.toml file(s):\n")
    for path in sorted(changes):
        print(path.relative_to(repo_root))
        for section in sorted(changes[path]):
            for pkg in sorted(changes[path][section]):
                print(f"  {section}.{pkg} -> {changes[path][section][pkg]}")
        print()

    print("Run again with --apply to rewrite these direct dependency declarations.")
    return 1


def report_skipped_conflicts(
    conflicts: dict[str, dict[str, list[tuple[Path, str]]]],
    skipped: dict[str, set[str]],
    risky_packages: set[str],
    allow_major: bool,
) -> None:
    """Show mismatches that automatic alignment leaves untouched."""
    if skipped:
        print(f"Skipped {len(skipped)} package(s) with mixed sources:\n")
        for pkg in sorted(skipped):
            print(f"  {pkg}: {', '.join(sorted(skipped[pkg]))}")
        print()

    risky_skipped = sorted(
        pkg for pkg in conflicts if is_risky_package(pkg, risky_packages)
    )
    if risky_skipped:
        print(f"Skipped {len(risky_skipped)} risky package(s):")
        for pkg in risky_skipped:
            print(f"  {pkg}: {', '.join(sorted(conflicts[pkg], key=version_sort_key))}")
        print()

    unsafe_major = []
    for pkg, versions in conflicts.items():
        if pkg in risky_skipped:
            continue
        groups = group_versions_by_compatibility(versions, allow_major=False)
        if len(groups) > 1:
            unsafe_major.append(pkg)
    if unsafe_major and not allow_major:
        print("Left major-version splits unchanged:")
        for pkg in sorted(unsafe_major):
            print(f"  {pkg}: {', '.join(sorted(conflicts[pkg], key=version_sort_key))}")
        print()


def align_versions(
    repo_root: Path,
    conflicts: dict[str, dict[str, list[tuple[Path, str]]]],
    risky_packages: set[str] | None = None,
    allow_major: bool = False,
    skipped: dict[str, set[str]] | None = None,
) -> int:
    if risky_packages is None:
        risky_packages = DEFAULT_RISKY_PACKAGES
    report_skipped_conflicts(conflicts, skipped or {}, risky_packages, allow_major)
    if not conflicts:
        print("No version mismatches to fix.")
        return 0

    changes = build_change_map(
        conflicts,
        risky_packages=risky_packages,
        allow_major=allow_major,
    )
    if not changes:
        print("No safe version mismatches to fix.")
        return 0

    tk = require_tomlkit()
    for path, sections in changes.items():
        doc = tk.parse(path.read_text(encoding="utf-8"))
        for section, deps in sections.items():
            container: tk.items.Table
            if section == "workspace.dependencies":
                ws = doc.setdefault("workspace", tk.table())
                container = ws.setdefault("dependencies", tk.table())
            elif section.startswith("target."):
                target_section, _, kind = section.rpartition(".")
                target = target_section.removeprefix("target.")
                container = doc["target"][target][kind]
            else:
                container = doc.setdefault(section, tk.table())

            for key, value in list(container.items()):
                info = package_info(key, value)
                if info is None or info[0] not in deps:
                    continue
                target = deps[info[0]]
                if isinstance(value, tk.items.String):
                    container[key] = target
                elif isinstance(value, (tk.items.InlineTable, tk.items.Table)):
                    if "version" in value:
                        value["version"] = target
                    # If the table only has `package = "..."` and no version,
                    # there is nothing to align.
                # else: unexpected shape, leave untouched
        path.write_text(tk.dumps(doc), encoding="utf-8")
        print(f"updated {path.relative_to(repo_root)}")

    changed_packages = {pkg for sections in changes.values() for deps in sections.values() for pkg in deps}
    print(f"\nAligned {len(changed_packages)} package(s) across {len(changes)} file(s).")
    return 0


def collect_sotf_duplicates(
    repo_root: Path,
) -> tuple[dict[str, set[str]], set[str]]:
    """Run cargo metadata on the sotf workspace and return duplicate external crates.

    Returns a tuple of:
    - duplicates: {crate_name: {version, ...}} for crates resolved in more
      than one version
    - workspace_member_names: set of package names that are workspace members
    """
    sotf_manifest = repo_root.resolve() / "sotf" / "Cargo.toml"
    if not sotf_manifest.exists():
        return {}, set()

    cmd = [
        "cargo",
        "metadata",
        "--format-version",
        "1",
        "--manifest-path",
        str(sotf_manifest),
    ]
    result = subprocess.run(
        cmd,
        cwd=sotf_manifest.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"cargo metadata failed:\n{result.stderr}")

    metadata = json.loads(result.stdout)
    member_ids = set(metadata.get("workspace_members", []))
    workspace_member_names = {
        package["name"] for package in metadata.get("packages", [])
        if package["id"] in member_ids
    }

    versions_by_name: dict[str, set[str]] = defaultdict(set)
    for package in metadata.get("packages", []):
        name = package["name"]
        version = package["version"]
        if package["id"] in member_ids:
            continue
        versions_by_name[name].add(version)

    duplicates = {
        name: versions
        for name, versions in versions_by_name.items()
        if len(versions) > 1
    }
    return duplicates, workspace_member_names


ALLOWLIST_PATH = Path("scripts") / "align-crates" / "sotf_duplicate_allowlist.toml"


def load_allowlist(path: Path) -> dict[str, set[str]]:
    """Load duplicate allowlist from a TOML file.

    Expected format:
        [bitflags]
        versions = ["1.3.2", "2.13.0"]
    """
    if not path.exists():
        return {}
    with path.open("rb") as f:
        doc = tomllib.load(f)
    allowlist: dict[str, set[str]] = {}
    for name, entry in doc.items():
        if isinstance(entry, dict) and "versions" in entry:
            allowlist[name] = {str(v) for v in entry["versions"]}
    return allowlist


def save_allowlist(path: Path, duplicates: dict[str, set[str]]) -> None:
    """Write duplicate set to allowlist TOML file, sorted for stable diffs."""
    tk = require_tomlkit()
    doc = tk.document()
    for name in sorted(duplicates):
        table = tk.table()
        table.add("versions", sorted(duplicates[name], key=Version))
        doc.add(name, table)
    path.write_text(tk.dumps(doc), encoding="utf-8")


def report_sotf_duplicates(repo_root: Path, update_allowlist: bool) -> int:
    """Detect duplicate crate versions in sotf and report or update allowlist."""
    duplicates, _workspace_members = collect_sotf_duplicates(repo_root)
    allowlist_path = repo_root / ALLOWLIST_PATH

    if update_allowlist:
        save_allowlist(allowlist_path, duplicates)
        print(f"Updated allowlist: {allowlist_path.relative_to(repo_root)}")
        print(f"Recorded {len(duplicates)} duplicate crate(s).")
        return 0

    allowlist = load_allowlist(allowlist_path)
    unallowed: dict[str, set[str]] = {}
    for name, versions in duplicates.items():
        allowed = allowlist.get(name, set())
        if not versions.issubset(allowed):
            unallowed[name] = versions

    if not unallowed:
        print("No unallowed duplicate crate versions in sotf.")
        return 0

    print(f"Found {len(unallowed)} duplicate crate(s) not in the allowlist:\n")
    for name in sorted(unallowed):
        print(f"  {name}: {', '.join(sorted(unallowed[name], key=Version))}")
    print("\nRun with --update-sotf-allowlist to accept the current baseline.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Rewrite Cargo.toml files to use the chosen target version.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root containing the inventoried workspace directories.",
    )
    parser.add_argument(
        "--risky-package",
        action="append",
        default=[],
        metavar="NAME",
        help="Additional package to exclude from automatic alignment. cpal, gpui, and gpui-* are excluded by default.",
    )
    parser.add_argument(
        "--allow-major",
        action="store_true",
        help="Allow automatic alignment across SemVer-incompatible major versions.",
    )
    parser.add_argument(
        "--check-sotf-duplicates",
        action="store_true",
        help="Check that compiling sotf does not introduce duplicate external crate versions.",
    )
    parser.add_argument(
        "--update-sotf-allowlist",
        action="store_true",
        help="Update the sotf duplicate allowlist from the current resolved dependency graph.",
    )
    args = parser.parse_args()

    if args.check_sotf_duplicates:
        return report_sotf_duplicates(args.root, update_allowlist=False)
    if args.update_sotf_allowlist:
        return report_sotf_duplicates(args.root, update_allowlist=True)

    risky_packages = DEFAULT_RISKY_PACKAGES | set(args.risky_package)
    by_package = collect_versions(args.root)
    conflicts, skipped = find_conflicts(by_package)

    if not args.apply:
        return report_change_plan(
            args.root,
            conflicts,
            skipped,
            risky_packages=risky_packages,
            allow_major=args.allow_major,
        )

    return align_versions(
        args.root,
        conflicts,
        risky_packages=risky_packages,
        allow_major=args.allow_major,
        skipped=skipped,
    )


if __name__ == "__main__":
    sys.exit(main())
