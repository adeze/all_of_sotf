#!/usr/bin/env python3
"""Audit the nine-workspace dependency direction and package identities."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import re
import sys
import tomllib
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from workspaces import vendor_names, workspace_names

# Smaller layer numbers may depend on larger ones. Same-layer edges are reported.
LAYERS = {
    "sotf": 0, "sotf-systemwide": 0,
    "sotf-daw": 1, "sotf-capture": 1,
    "gpui-toolkit": 2, "autoeq": 2,
    "math-audio": 3, "sofa-reader": 3,
}
SOURCE_LAYERS = {**LAYERS, **{name: 4 for name in vendor_names()}}
SKIP_DIRS = {".git", "target", "target-static", "venv", ".venv", ".release-venv", "node_modules", "dist", "build", ".muse", ".worktrees", "worktrees", ".evo", "audit", ".docker-target"}
DEPENDENCY_KEYS = {"dependencies", "dev-dependencies", "build-dependencies"}
VENDOR_PARTS = {"3rdparties", "vendor"}


def manifests(root: Path, names: list[str]):
    for owner in names:
        base = root / owner
        if owner not in vendor_names() and not (base / "Cargo.toml").is_file():
            yield owner, base / "Cargo.toml", None
            continue
        for directory, subdirs, files in os.walk(base):
            if Path(directory) != base and (Path(directory) / ".git").exists():
                subdirs[:] = []
                continue
            subdirs[:] = sorted(item for item in subdirs if item not in SKIP_DIRS
                                and not (Path(directory) / item).is_symlink())
            if "Cargo.toml" in files:
                path = Path(directory) / "Cargo.toml"
                yield owner, path, tomllib.loads(path.read_text(encoding="utf-8"))


def dependency_tables(doc: dict, prefix: str = ""):
    """Find ordinary, target-specific, inherited, and patch dependencies."""
    for key, value in doc.items():
        section = f"{prefix}.{key}" if prefix else key
        if key in DEPENDENCY_KEYS and isinstance(value, dict):
            yield section, value
        elif key == "patch" and isinstance(value, dict):
            for registry, dependencies in value.items():
                if isinstance(dependencies, dict):
                    yield f"{section}.{registry}", dependencies
        elif (key in {"target", "workspace"} or prefix.startswith("target")) and isinstance(value, dict):
            yield from dependency_tables(value, section)


def source_owner(value: dict, manifest: Path, root: Path, names: set[str]) -> tuple[str | None, str]:
    if "path" in value:
        resolved = (manifest.parent / str(value["path"])).resolve()
        for name in names:
            base = (root / name).resolve()
            if resolved == base or base in resolved.parents:
                return name, f"path:{resolved}"
        return None, f"path:{resolved}"
    if "git" in value:
        parsed = urlparse(str(value["git"]))
        repo = re.sub(r"\.git$", "", parsed.path.rstrip("/").split("/")[-1])
        return (repo if repo in names else None), f"git:{value['git']}@{value.get('rev') or value.get('tag') or value.get('branch') or 'unbound'}"
    return None, "registry"


def finding(code: str, severity: str, **details) -> dict:
    return {"code": code, "severity": severity, **details}


def inherited_dependencies(manifest: Path, base: Path) -> tuple[dict, Path | None]:
    """Resolve workspace inheritance from the nearest enclosing workspace."""
    for parent in (manifest.parent, *manifest.parent.parents):
        if parent != base and base not in parent.parents:
            break
        candidate = parent / "Cargo.toml"
        if candidate.is_file():
            doc = tomllib.loads(candidate.read_text(encoding="utf-8"))
            if "workspace" in doc:
                return doc["workspace"].get("dependencies", {}), candidate
    return {}, None


def graph_cycles(edges: set[tuple[str, str]]) -> list[list[str]]:
    adjacency = defaultdict(set)
    for source, target in edges:
        adjacency[source].add(target)
    seen, active, cycles = set(), [], []

    def visit(node: str) -> None:
        if node in active:
            cycle = active[active.index(node):] + [node]
            if cycle not in cycles:
                cycles.append(cycle)
            return
        if node in seen:
            return
        active.append(node)
        for target in sorted(adjacency[node]):
            visit(target)
        active.pop()
        seen.add(node)

    for node in sorted(adjacency):
        visit(node)
    return cycles


def audit_manifests(root: Path, names: list[str]) -> tuple[list[dict], list[dict]]:
    findings = []
    vendors = defaultdict(list)
    known = set(names) | set(vendor_names())
    graph_edges = set()
    for owner, path, doc in manifests(root, names):
        if doc is None:
            findings.append(finding("missing_manifest", "error", workspace=owner, manifest=str(path)))
            continue
        package = doc.get("package")
        if package and (owner in vendor_names() or any(part in VENDOR_PARTS for part in path.relative_to(root / owner).parts)):
            vendors[package.get("name")].append({"version": package.get("version"), "manifest": str(path), "owner": owner})
        for section, dependencies in dependency_tables(doc):
            for alias, raw in dependencies.items():
                value = {"version": raw} if isinstance(raw, str) else raw
                if not isinstance(value, dict):
                    continue
                source_manifest = path
                if value.get("workspace") is True:
                    workspace_dependencies, workspace_manifest = inherited_dependencies(path, root / owner)
                    inherited = workspace_dependencies.get(alias)
                    if inherited is None:
                        findings.append(finding("unresolved_workspace_dependency", "error", workspace=owner,
                                                manifest=str(path), section=section, dependency=alias))
                        continue
                    value = {"version": inherited} if isinstance(inherited, str) else inherited
                    source_manifest = workspace_manifest
                target, source = source_owner(value, source_manifest, root, known)
                is_patch = section.startswith("patch.")
                if source.startswith("path:") and target is None:
                    findings.append(finding("external_path", "error", workspace=owner,
                                            manifest=str(path), section=section, dependency=alias, source=source))
                if target is None or target == owner:
                    continue
                details = dict(workspace=owner, target=target, manifest=str(path),
                               section=section, dependency=alias, package=value.get("package", alias),
                               source=source, role="source_override" if is_patch else "dependency")
                if SOURCE_LAYERS[target] < SOURCE_LAYERS[owner]:
                    findings.append(finding("upward_dependency", "error", **details))
                elif SOURCE_LAYERS[target] == SOURCE_LAYERS[owner]:
                    findings.append(finding("same_layer_dependency", "report", **details))
                if not is_patch:
                    graph_edges.add((owner, target))
    for cycle in graph_cycles(graph_edges):
        findings.append(finding("repository_cycle", "error", cycle=cycle))
    for name, copies in sorted(vendors.items()):
        paths = {copy["manifest"] for copy in copies}
        if len(paths) > 1:
            findings.append(finding("duplicate_vendor", "error", package=name,
                                    copies=copies, owners=sorted({copy["owner"] for copy in copies})))
    return findings, [{"package": name, "copies": copies}
                      for name, copies in sorted(vendors.items())]


def audit_lockfiles(root: Path, names: list[str]) -> list[dict]:
    findings = []
    aggregate = defaultdict(list)
    lockfiles = [(name, root / name / "Cargo.lock") for name in names]
    if "autoeq" in names:
        lockfiles.append(("autoeq-gpui-examples", root / "autoeq" / "crates" /
                          "autoeq-gpui-examples" / "Cargo.lock"))
    for workspace, path in lockfiles:
        if not path.is_file():
            findings.append(finding("missing_lockfile", "error", workspace=workspace, lockfile=str(path)))
            continue
        packages = tomllib.loads(path.read_text(encoding="utf-8")).get("package", [])
        by_name = defaultdict(list)
        for package in packages:
            by_name[package["name"]].append(package)
            aggregate[package["name"]].append({
                "workspace": workspace, "lockfile": str(path),
                "version": package["version"],
                "source": package.get("source", "path/local"),
            })
        for name, entries in sorted(by_name.items()):
            versions = sorted({entry["version"] for entry in entries})
            if len(versions) > 1:
                findings.append(finding("multiple_versions", "error", workspace=workspace,
                                        lockfile=str(path), package=name, versions=versions,
                                        scope="lockfile union of targets, features, and dev dependencies"))
            by_version = defaultdict(set)
            for entry in entries:
                by_version[entry["version"]].add(entry.get("source", "path/local"))
            for version, sources in sorted(by_version.items()):
                if len(sources) > 1:
                    findings.append(finding("mixed_sources", "error", workspace=workspace,
                                            lockfile=str(path), package=name, version=version,
                                            sources=sorted(sources),
                                            scope="lockfile union of targets, features, and dev dependencies"))
    for name, locations in sorted(aggregate.items()):
        versions = sorted({item["version"] for item in locations})
        if len(versions) > 1:
            findings.append(finding("aggregate_multiple_versions", "error", package=name,
                                    versions=versions, locations=locations,
                                    scope="all nine root and nested lockfile unions"))
        for version in versions:
            matching = [item for item in locations if item["version"] == version]
            sources = sorted({item["source"] for item in matching})
            if len(sources) > 1:
                findings.append(finding("aggregate_mixed_sources", "error", package=name,
                                        version=version, sources=sources, locations=matching,
                                        scope="all nine root and nested lockfile unions"))
    return findings


def audit(root: Path, names: list[str] | None = None) -> dict:
    names = names or workspace_names()
    if set(names) != set(LAYERS):
        raise ValueError(f"inventory/layer mismatch: {sorted(set(names) ^ set(LAYERS))}")
    vendor_roots = [name for name in vendor_names() if (root / name).is_dir()]
    manifest_findings, vendors = audit_manifests(root, names + vendor_roots)
    findings = manifest_findings + audit_lockfiles(root, names)
    return {"schema_version": 1, "scope": "static manifests, per-workspace and aggregate lockfile unions",
            "workspaces": names, "vendored_repositories": vendor_roots,
            "layers": SOURCE_LAYERS, "findings": findings, "vendors": vendors,
            "summary": {"errors": sum(item["severity"] == "error" for item in findings),
                        "reports": sum(item["severity"] == "report" for item in findings)}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = audit(args.root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for item in report["findings"]:
        print(f"{item['severity'].upper()}: {item['code']}: {item.get('workspace', '')} {item.get('package', item.get('dependency', ''))} {item.get('manifest', item.get('lockfile', ''))}")
    print(f"{report['summary']['errors']} errors, {report['summary']['reports']} reports; {args.output}")
    return 1 if report["summary"]["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
