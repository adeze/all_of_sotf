#!/usr/bin/env python3
"""Generate the aggregate (mono) Cargo workspace manifest for all_of_sotf.

The mono workspace lets one command compile and check every crate in every
member repo (`cargo check --workspace` from the aggregate root) with a single
dependency resolution and lockfile. Each repo keeps building standalone with
its own manifest: nested workspace roots shadow the root manifest (verified
by probe), so this file changes nothing for per-repo workflows.

Inputs (all owned by the aggregate repo; no submodule is ever modified):
  <repo>/Cargo.toml               member lists, workspace deps/lints/patches
  scripts/mono/overlays/mono.toml global reconciliations (version, excludes,
                                  forced dep specs, lint decisions)
  scripts/mono/overlays/<repo>.toml
                                  per-repo mono participation (extra member
                                  excludes, notes)

Output: <aggregate root>/Cargo.toml (committed; `--check` gates drift).

Only the standard library is used so the script runs anywhere.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

# Repos aggregated into the mono workspace. gpui-toolkit is excluded: it is
# being extracted to a standalone repo and is consumed via git dependencies.
# sotf-3rdparties is excluded: it is consumed via git dependencies only, and
# excluding it preserves standalone fork-development builds inside it.
MONO_REPOS = [
    "autoeq",
    "math-audio",
    "sofa-reader",
    "sotf",
    "sotf-capture",
    "sotf-daw",
    "sotf-systemwide",
    "symphonia-add-ons",
]

CANONICAL_DEP_KEYS = (
    "package",
    "version",
    "git",
    "branch",
    "tag",
    "rev",
    "path",
    "features",
    "default-features",
    "optional",
)

# Mono canonical profiles. Per-repo profiles stay authoritative for standalone
# builds; `cargo check` (the main mono use) ignores optimisation settings.
CANONICAL_PROFILES = {
    "dev": {
        "opt-level": 1,
        "split-debuginfo": "packed",
        "debug": 1,
        "codegen-units": 256,
    },
    "release": {
        "lto": "thin",
        "codegen-units": 14,
        "panic": "unwind",
    },
    "dist": {
        "inherits": "release",
        "lto": "fat",
        "codegen-units": 1,
    },
}

LINT_STRICTNESS = {"allow": 0, "warn": 1, "deny": 2, "forbid": 3}


class MonoError(Exception):
    """Fatal mono generation error with an actionable message."""


def aggregate_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def load_toml(path: Path) -> dict:
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def load_overlays(root: Path) -> tuple[dict, dict[str, dict]]:
    """Load the global overlay plus the one overlay file per directory."""
    mono_path = root / "scripts" / "mono" / "overlays" / "mono.toml"
    if not mono_path.is_file():
        raise MonoError(f"missing global overlay: {mono_path}")
    per_repo: dict[str, dict] = {}
    for repo in MONO_REPOS:
        path = root / "scripts" / "mono" / "overlays" / f"{repo}.toml"
        if not path.is_file():
            raise MonoError(
                f"missing per-repo overlay: {path} "
                "(every mono repo needs one, even if only comments)"
            )
        per_repo[repo] = load_toml(path)
    return load_toml(mono_path), per_repo


def workspace_members(repo_dir: Path, doc: dict) -> list[str]:
    """Member dirs of one repo root manifest, repo-relative, normalised."""
    ws = doc.get("workspace", {}) or {}
    members = ws.get("members")
    if members is None:
        members = ["."] if "package" in doc else []
    return [member_dir(m) for m in members]


def member_dir(member: str) -> str:
    """Normalise a workspace member entry ('.' -> '' for path joining)."""
    normal = member.replace("\\", "/")
    if normal in (".", "./"):
        return ""
    return normal.rstrip("/")


def collect_members(root: Path, overlay: dict[str, dict]):
    """Return [(repo, member_dir, manifest_path, package_name)].

    Extra overlay excludes are honoured; every member must be a package
    manifest and package names must be unique across the mono workspace.
    """
    collected = []
    seen: dict[str, str] = {}
    for repo in MONO_REPOS:
        repo_dir = root / repo
        doc = load_toml(repo_dir / "Cargo.toml")
        repo_members = workspace_members(repo_dir, doc)
        extra_excludes = {
            member_dir(m)
            for m in ((overlay[repo].get("mono", {}) or {}).get(
                "exclude_members", []))
        }
        unknown = extra_excludes - set(repo_members)
        if unknown:
            raise MonoError(
                f"{repo} overlay excludes unknown members: "
                f"{sorted(unknown)}"
            )
        for member in repo_members:
            if member in extra_excludes:
                continue
            manifest = repo_dir / member / "Cargo.toml" if member else repo_dir / "Cargo.toml"
            manifest = manifest.resolve()
            if not manifest.is_file():
                raise MonoError(f"member manifest missing: {manifest}")
            rel = f"{repo}/{member}" if member else repo
            member_doc = load_toml(manifest)
            if "workspace" in member_doc:
                raise MonoError(
                    f"{rel}: member manifest has its own [workspace] table; "
                    "cargo forbids workspace roots in an outer workspace "
                    "('multiple workspace roots'). Virtualize the repo root "
                    "(move the package into crates/, see issue #4) or "
                    f"exclude the reverse-dependency closure in "
                    f"overlays/{repo}.toml"
                )
            pkg = member_doc.get("package", {}) or {}
            name = pkg.get("name")
            if not name:
                raise MonoError(f"member has no [package] name: {manifest}")
            if name in seen:
                raise MonoError(
                    f"duplicate package name {name!r}: {seen[name]} and {rel}"
                )
            seen[name] = rel
            collected.append((repo, member, manifest, name))
    return collected


def root_relative_path(spec_path: str, repo: str) -> str:
    """Rewrite a repo-root-relative path dep to aggregate-root-relative."""
    joined = (Path(repo) / spec_path).as_posix()
    return str(Path(joined).as_posix())


def abspath_under_root(root: Path, rel: str) -> Path:
    path = root / rel
    return Path(str(path))


def is_internal_repo_path(root: Path, abs_path: Path) -> str | None:
    """Return the mono repo name containing abs_path, else None."""
    try:
        rel = abs_path.relative_to(root).as_posix()
    except ValueError:
        return None
    first = rel.split("/", 1)[0]
    return first if first in MONO_REPOS else None


def normalize_spec(spec) -> dict:
    """Turn a dependency spec (string or table) into a plain dict."""
    if isinstance(spec, str):
        return {"version": spec}
    if isinstance(spec, dict):
        return dict(spec)
    raise MonoError(f"unsupported dependency spec shape: {spec!r}")


def merge_specs(name: str, entries: list[tuple[str, dict]]) -> dict:
    """Merge one [workspace.dependencies] name across repo roots.

    Internal path entries win over git/registry entries for the same package
    (mono checks local code, not the pushed rev); companion `version`/`git`
    sources are dropped: member crates share the single mono version, which
    cannot satisfy every repo's own version line at once, and mono never
    publishes.
    """
    internal = [(repo, s) for repo, s in entries if "path" in s]
    if internal:
        targets = {s["path"] for _, s in internal}
        if len(targets) > 1:
            detail = ", ".join(f"{r}:{s['path']}" for r, s in internal)
            raise MonoError(
                f"irreconcilable internal paths for {name!r}: {detail} "
                f"(add a [deps.{name}] override to overlays/mono.toml)"
            )
        # The working-copy path wins outright: companion version/git sources
        # describe standalone resolution and publishing, both meaningless for
        # mono (single shared version, never published from here).
        merged: dict = {"path": next(iter(targets))}
        agree_keys = [k for k in CANONICAL_DEP_KEYS
                      if k not in ("version", "git", "branch", "tag", "rev",
                                   "path", "features",
                                   "default-features", "optional")]
        for key in agree_keys:
            values = {repr(s[key]) for _, s in entries if key in s}
            if len(values) > 1:
                detail = ", ".join(f"{r}:{s.get(key)!r}" for r, s in entries)
                raise MonoError(
                    f"conflicting {key!r} for internal dep {name!r}: {detail} "
                    f"(add a [deps.{name}] override to overlays/mono.toml)"
                )
            if values:
                merged[key] = [s[key] for _, s in entries if key in s][0]
        # Absent booleans carry cargo's implicit default, which counts as a
        # position: silent unification would change some repo's feature set.
        for key, implicit in (("default-features", True), ("optional", False)):
            values = {s.get(key, implicit) for _, s in entries}
            if len(values) > 1:
                detail = ", ".join(
                    f"{r}:{s.get(key, implicit)!r}" for r, s in entries
                )
                raise MonoError(
                    f"conflicting {key!r} for internal dep {name!r}: {detail} "
                    f"(add a [deps.{name}] override to overlays/mono.toml)"
                )
            if next(iter(values)) is not implicit:
                merged[key] = next(iter(values))
        features: list[str] = []
        for _, spec in entries:
            for feat in spec.get("features", []):
                if feat not in features:
                    features.append(feat)
        if features:
            merged["features"] = sorted(features)
        return merged
    return merge_external_specs(name, entries)


def merge_external_specs(name: str, entries: list[tuple[str, dict]]) -> dict:
    """Merge a non-path dependency: sources must agree, reqs maximise."""
    git_urls = {s["git"] for _, s in entries if "git" in s}
    if len(git_urls) > 1:
        detail = ", ".join(f"{r}:{s.get('git')!r}" for r, s in entries)
        raise MonoError(
            f"conflicting git sources for {name!r}: {detail} "
            f"(add a [deps.{name}] override to overlays/mono.toml)"
        )
    has_git = any("git" in s for _, s in entries)
    has_plain = any("git" not in s for _, s in entries)
    if has_git and has_plain:
        detail = ", ".join(f"{r}:{s!r}" for r, s in entries)
        raise MonoError(
            f"git/registry source mix for {name!r}: {detail} "
            f"(add a [deps.{name}] override to overlays/mono.toml)"
        )
    merged: dict = {}
    if git_urls:
        merged["git"] = next(iter(git_urls))
    for key in ("package", "branch", "tag", "rev"):
        values = {repr(s[key]) for _, s in entries if key in s}
        if len(values) > 1:
            detail = ", ".join(f"{r}:{s.get(key)!r}" for r, s in entries)
            raise MonoError(
                f"conflicting {key!r} for dep {name!r}: {detail} "
                f"(add a [deps.{name}] override to overlays/mono.toml)"
            )
        if values:
            merged[key] = [s[key] for _, s in entries if key in s][0]
    for key, implicit in (("default-features", True), ("optional", False)):
        values = {s.get(key, implicit) for _, s in entries}
        if len(values) > 1:
            detail = ", ".join(
                f"{r}:{s.get(key, implicit)!r}" for r, s in entries
            )
            raise MonoError(
                f"conflicting {key!r} for dep {name!r}: {detail} "
                f"(add a [deps.{name}] override to overlays/mono.toml)"
            )
        if next(iter(values)) is not implicit:
            merged[key] = next(iter(values))
    versions = [s["version"] for _, s in entries if "version" in s]
    if versions:
        merged["version"] = pick_req(name, versions)
    features: list[str] = []
    for _, spec in entries:
        for feat in spec.get("features", []):
            if feat not in features:
                features.append(feat)
    if features:
        merged["features"] = sorted(features)
    return merged


REQ_OPS = (">=", "<=", ">", "<", "=", "^", "~")


def parse_req_part(part: str) -> tuple[str, str]:
    part = part.strip()
    if part == "*":
        return ("*", "")
    for op in REQ_OPS:
        if part.startswith(op):
            return (op, part[len(op):].strip())
    return ("^", part)


def parse_version(text: str) -> tuple[int, ...]:
    nums = text.strip().split(".")
    try:
        return tuple(int(n) for n in nums)
    except ValueError:
        raise MonoError(f"cannot parse version number: {text!r}")


def caret_upper(nums: tuple[int, ...]) -> tuple[int, ...]:
    nums = nums + (0,) * (3 - len(nums))
    if nums[0] > 0:
        return (nums[0] + 1, 0, 0)
    if nums[1] > 0:
        return (0, nums[1] + 1, 0)
    return (0, 0, nums[2] + 1)


def tilde_upper(nums: tuple[int, ...]) -> tuple[int, ...]:
    nums = nums + (0,) * (3 - len(nums))
    if len(nums) >= 2 and nums[:2] != (0, 0):
        return (nums[0], nums[1] + 1, 0)
    if nums[0] > 0:
        return (nums[0] + 1, 0, 0)
    return (0, 1, 0)


def pad(version: tuple[int, ...], width: int = 3) -> tuple[int, ...]:
    return version + (0,) * max(0, width - len(version))


def satisfies(req: str, version: str) -> bool:
    """Check a cargo-style version requirement against a version number."""
    mine = pad(parse_version(version))
    for raw_part in req.split(","):
        op, num = parse_req_part(raw_part)
        if op == "*":
            continue
        want = pad(parse_version(num))
        if op == "=":
            ok = mine == want
        elif op == ">=":
            ok = mine >= want
        elif op == "<=":
            ok = mine <= want
        elif op == ">":
            ok = mine > want
        elif op == "<":
            ok = mine < want
        elif op == "^":
            ok = mine >= want and mine < caret_upper(parse_version(num))
        elif op == "~":
            ok = mine >= want and mine < tilde_upper(parse_version(num))
        else:  # pragma: no cover - parse_req_part only returns known ops
            raise MonoError(f"unknown requirement operator in {req!r}")
        if not ok:
            return False
    return True


def req_minimum(req: str) -> tuple[int, ...]:
    """Lower bound of the first requirement part (for most-specific picks)."""
    first = req.split(",")[0].strip()
    if first == "*":
        return (0, 0, 0)
    op, num = parse_req_part(first)
    if op in ("^", "~", "=", ">="):
        return pad(parse_version(num))
    if op == ">":
        lo = pad(parse_version(num))
        return (lo[0], lo[1], lo[2] + 1)
    if op in ("<", "<="):
        return (0, 0, 0)
    raise MonoError(f"unknown requirement operator in {req!r}")


def pick_req(name: str, reqs: list[str]) -> str:
    """Pick the most specific requirement (highest lower bound wins)."""
    if len(set(reqs)) == 1:
        return reqs[0]
    ranked = sorted(
        ((req_minimum(r), "=" in r.split(",")[0], r) for r in reqs),
        key=lambda item: (item[0], item[1]),
    )
    return ranked[-1][2]


def lint_level(spec) -> tuple[str, dict]:
    if isinstance(spec, str):
        if spec not in LINT_STRICTNESS:
            raise MonoError(f"unknown lint level: {spec!r}")
        return spec, {}
    if isinstance(spec, dict):
        level = spec.get("level")
        if level not in LINT_STRICTNESS:
            raise MonoError(f"unknown lint level in {spec!r}")
        extras = {k: v for k, v in spec.items() if k != "level"}
        return level, extras
    raise MonoError(f"unsupported lint spec shape: {spec!r}")


def merge_lints(per_repo: dict[str, dict]):
    """Union workspace lints: loosest level wins; non-universal deny is out.

    A deny/forbid level is only kept when every mono repo defines the same
    lint at that level, otherwise crates that merely inherit `[lints]
    workspace = true` would newly fail to compile (e.g. autoeq forbids
    unsafe_code, but sotf-media-controls legitimately contains unsafe).
    """
    pairs: dict[tuple[str, str], list[tuple[str, str, dict]]] = {}
    for repo in MONO_REPOS:
        for group, lints in (per_repo.get(repo) or {}).items():
            for lint, spec in (lints or {}).items():
                level, extras = lint_level(spec)
                pairs.setdefault((group, lint), []).append((repo, level, extras))
    merged: dict[str, dict] = {}
    dropped: list[str] = []
    for (group, lint), defs in pairs.items():
        loosest = min(defs, key=lambda d: LINT_STRICTNESS[d[1]])
        _, level, _ = loosest
        if level in ("deny", "forbid"):
            defining = {repo for repo, _, _ in defs}
            if defining != set(MONO_REPOS) or any(
                lv != level for _, lv, _ in defs
            ):
                dropped.append(
                    f"{group}::{lint} "
                    f"({', '.join(f'{r}={lv}' for r, lv, _ in defs)})"
                )
                continue
        extras = next(e for r, lv, e in defs if lv == level)
        merged.setdefault(group, {})[lint] = {"level": level, **extras}
    return merged, dropped


def iter_dep_specs(doc: dict):
    """Yield (table_path, dep_name, spec) for every dependency entry."""
    for table in ("dependencies", "dev-dependencies", "build-dependencies"):
        deps = doc.get(table, {}) or {}
        for dep_name, spec in deps.items():
            yield table, dep_name, spec
    for target, target_doc in (doc.get("target", {}) or {}).items():
        if not isinstance(target_doc, dict):
            continue
        for table in ("dependencies", "dev-dependencies", "build-dependencies"):
            deps = target_doc.get(table, {}) or {}
            for dep_name, spec in deps.items():
                yield f"target.{target}.{table}", dep_name, spec


def collect_workspace_deps(root: Path) -> dict[str, list[tuple[str, dict]]]:
    """Gather [workspace.dependencies] entries, paths made root-relative."""
    by_name: dict[str, list[tuple[str, dict]]] = {}
    for repo in MONO_REPOS:
        repo_dir = root / repo
        doc = load_toml(repo_dir / "Cargo.toml")
        ws_deps = (doc.get("workspace", {}) or {}).get("dependencies", {}) or {}
        for dep_name, raw in ws_deps.items():
            spec = normalize_spec(raw)
            if spec.get("workspace") is True:
                raise MonoError(
                    f"{repo}/Cargo.toml: [workspace.dependencies] entry "
                    f"{dep_name!r} cannot itself inherit from a workspace"
                )
            if "path" in spec:
                abs_target = (repo_dir / spec["path"]).resolve()
                spec["path"] = abs_target.relative_to(root).as_posix()
            by_name.setdefault(dep_name, []).append((repo, spec))
    return by_name


def manifest_inherit_uses(manifest: Path) -> tuple[set[str], set[str], bool]:
    """Workspace-inheritance uses: (dep names, package keys, lints bool)."""
    doc = load_toml(manifest)
    deps: set[str] = set()
    for _, dep_name, spec in iter_dep_specs(doc):
        if isinstance(spec, dict) and spec.get("workspace") is True:
            deps.add(dep_name)
    keys: set[str] = set()
    pkg = doc.get("package", {}) or {}
    for key, value in pkg.items():
        if isinstance(value, dict) and value.get("workspace") is True:
            keys.add(key)
    lints = (doc.get("lints", {}) or {}).get("workspace") is True
    return deps, keys, lints


def validate_external_paths(
    root: Path,
    members,
    mono_version: str,
    union_deps: set[str],
    package_keys: set[str],
    excluded_paths: list[str],
) -> None:
    """Check path deps pointing outside the mono members.

    Non-excluded targets join the mono workspace when building from the
    root, so they inherit from the mono root and take the mono version
    when theirs is inherited: every inherited name must exist in the mono
    union and every pin must hold. Targets under [mono] exclude paths keep
    their own workspace context (verified by probe); only their existence
    is checked here, cargo arbitrates the rest.
    """
    member_manifests = {m for _, _, m, _ in members}
    excluded_dirs = [(root / rel).resolve() for rel in excluded_paths]
    checked: set[Path] = set()
    for _, _, manifest, _ in members:
        doc = load_toml(manifest)
        for table, dep_name, raw in iter_dep_specs(doc):
            if not isinstance(raw, dict) or "path" not in raw:
                continue
            target = (manifest.parent / raw["path"]).resolve()
            target_manifest = target / "Cargo.toml"
            if target_manifest in member_manifests or target_manifest in checked:
                continue
            checked.add(target_manifest)
            if not target_manifest.is_file():
                raise MonoError(
                    f"{manifest}: {table} {dep_name!r} points at missing "
                    f"manifest {target_manifest}"
                )
            if any(
                target == ex or ex in target.parents for ex in excluded_dirs
            ):
                continue
            uses_deps, uses_keys, _ = manifest_inherit_uses(target_manifest)
            missing = (uses_deps - union_deps) | (uses_keys - package_keys)
            if missing:
                raise MonoError(
                    f"{target_manifest} joins the mono workspace via "
                    f"{manifest} but inherits {sorted(missing)} which mono "
                    "does not define (convert the path dep to git/registry "
                    "or exclude the dependent chain in the repo overlay)"
                )
            pin = raw.get("version")
            if pin is not None:
                target_doc = load_toml(target_manifest)
                target_version = (target_doc.get("package", {}) or {}).get(
                    "version"
                )
                effective = (
                    mono_version
                    if isinstance(target_version, dict)
                    else target_version
                )
                if not satisfies(pin, effective):
                    raise MonoError(
                        f"{manifest}: {table} {dep_name!r} pins {pin!r}, "
                        f"rejected by {effective} from {target_manifest}"
                    )


def validate_member_pins(
    members, mono_version: str, member_manifests: set[Path]
) -> None:
    """Every direct internal path pin must accept the mono version."""
    for _, _, manifest, _ in members:
        doc = load_toml(manifest)
        for table, dep_name, raw in iter_dep_specs(doc):
            if not isinstance(raw, dict) or "path" not in raw:
                continue
            target = (manifest.parent / raw["path"]).resolve()
            if target / "Cargo.toml" not in member_manifests:
                continue
            pin = raw.get("version")
            if pin is not None and not satisfies(pin, mono_version):
                raise MonoError(
                    f"{manifest}: {table} {dep_name!r} pins {pin!r}, which "
                    f"rejects mono version {mono_version} "
                    "(bump [package] version in overlays/mono.toml)"
                )


def validate_inherit_coverage(
    members, union_deps: set[str], package_keys: set[str]
) -> None:
    """Every `workspace = true` use must resolve from the mono root."""
    for _, _, manifest, _ in members:
        doc = load_toml(manifest)
        for _, dep_name, spec in iter_dep_specs(doc):
            if (
                isinstance(spec, dict)
                and spec.get("workspace") is True
                and dep_name not in union_deps
            ):
                raise MonoError(
                    f"{manifest}: {dep_name!r} inherits from the workspace "
                    "but no repo defines it in [workspace.dependencies]"
                )
        pkg = doc.get("package", {}) or {}
        for key, value in pkg.items():
            if (
                isinstance(value, dict)
                and value.get("workspace") is True
                and key not in package_keys
            ):
                raise MonoError(
                    f"{manifest}: [package] {key!r} inherits from the "
                    "workspace but overlays/mono.toml [package] omits it"
                )


def collect_patches(root: Path) -> dict[str, dict[str, dict]]:
    """Union [patch.<source>] tables; identical entries collapse, else error."""
    patches: dict[str, dict[str, list[tuple[str, dict]]]] = {}
    for repo in MONO_REPOS:
        repo_dir = root / repo
        doc = load_toml(repo_dir / "Cargo.toml")
        for key, table in doc.items():
            if not key.startswith("patch."):
                continue
            source = key[len("patch."):]
            for pkg_name, raw in (table or {}).items():
                spec = normalize_spec(raw)
                if "path" in spec:
                    abs_target = (repo_dir / spec["path"]).resolve()
                    try:
                        spec["path"] = abs_target.relative_to(root).as_posix()
                    except ValueError:
                        raise MonoError(
                            f"{repo}/Cargo.toml: [patch.{source}] "
                            f"{pkg_name!r} escapes the aggregate root"
                        )
                patches.setdefault(source, {}).setdefault(pkg_name, []).append(
                    (repo, spec)
                )
    merged: dict[str, dict[str, dict]] = {}
    for source, pkgs in patches.items():
        for pkg_name, entries in pkgs.items():
            distinct = {repr(sorted(spec.items())) for _, spec in entries}
            if len(distinct) > 1:
                detail = ", ".join(f"{r}:{s!r}" for r, s in entries)
                raise MonoError(
                    f"conflicting [patch.{source}] {pkg_name!r}: {detail} "
                    "(reconcile the repo manifests; patches have no overlay)"
                )
            merged.setdefault(source, {})[pkg_name] = entries[0][1]
    return merged


def toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def toml_value(value) -> str:
    if isinstance(value, str):
        return toml_string(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[ " + ", ".join(toml_value(v) for v in value) + " ]"
    raise MonoError(f"cannot emit TOML value: {value!r}")


def inline_table(spec: dict) -> str:
    parts = []
    keys = [k for k in CANONICAL_DEP_KEYS if k in spec]
    keys += sorted(k for k in spec if k not in CANONICAL_DEP_KEYS)
    for key in keys:
        parts.append(f"{key} = {toml_value(spec[key])}")
    return "{ " + ", ".join(parts) + " }"


def emit_manifest(
    members: list[str],
    exclude: list[str],
    package: dict,
    deps: dict[str, dict],
    lints: dict[str, dict],
    patches: dict[str, dict[str, dict]],
) -> str:
    """Render the root manifest deterministically (sorted, commented)."""
    lines = [
        "# GENERATED by scripts/mono/generate.py -- do not edit by hand.",
        "# Regenerate with: python3 scripts/mono/generate.py",
        "# Check drift with: python3 scripts/mono/generate.py --check",
        "#",
        "# Aggregate workspace: one resolve/lockfile to compile and check",
        "# every crate. Standalone repo builds are unaffected (nested",
        "# workspace roots shadow this manifest). Never publish from here:",
        "# member crates share the mono [workspace.package] metadata.",
        "",
        "[workspace]",
        'resolver = "3"',
        "members = [",
    ]
    for member in sorted(members):
        lines.append(f"    {toml_string(member)},")
    lines.append("]")
    if exclude:
        lines.append("exclude = [")
        for path in sorted(exclude):
            lines.append(f"    {toml_string(path)},")
        lines.append("]")
    lines += ["", "[workspace.package]"]
    for key in (
        "version",
        "edition",
        "license",
        "authors",
        "repository",
        "description",
        "documentation",
        "homepage",
        "keywords",
        "categories",
        "readme",
        "rust-version",
    ):
        if key in package:
            lines.append(f"{key} = {toml_value(package[key])}")
    lines += ["", "[workspace.dependencies]"]
    for dep_name in sorted(deps):
        lines.append(f"{dep_name} = {inline_table(deps[dep_name])}")
    for group in sorted(lints):
        lines.append(f"")
        lines.append(f"[workspace.lints.{group}]")
        for lint in sorted(lints[group]):
            spec = lints[group][lint]
            if set(spec) == {"level"}:
                lines.append(f"{lint} = {toml_string(spec['level'])}")
            else:
                lines.append(f"{lint} = {inline_table(spec)}")
    for source in sorted(patches):
        lines.append(f"")
        lines.append(f"[patch.{toml_string(source)}]")
        for pkg_name in sorted(patches[source]):
            lines.append(f"{pkg_name} = {inline_table(patches[source][pkg_name])}")
    for profile in ("dev", "release", "dist"):
        lines.append(f"")
        lines.append(f"[profile.{profile}]")
        for key, value in CANONICAL_PROFILES[profile].items():
            lines.append(f"{key} = {toml_value(value)}")
    return "\n".join(lines) + "\n"


def generate(root: Path) -> tuple[str, dict]:
    """Build the manifest text plus a human-readable decisions report."""
    mono_overlay, per_repo = load_overlays(root)
    mono_cfg = mono_overlay.get("mono", {}) or {}
    exclude = mono_cfg.get("exclude", None)
    if not isinstance(exclude, list):
        raise MonoError("overlays/mono.toml must define [mono] exclude = [...]")
    package = mono_overlay.get("package", None)
    if not isinstance(package, dict) or "version" not in package:
        raise MonoError(
            "overlays/mono.toml must define [package] with at least version"
        )
    mono_version = package["version"]

    members = collect_members(root, per_repo)
    member_rel = [f"{repo}/{m}" if m else repo for repo, m, _, _ in members]
    member_manifests = {m for _, _, m, _ in members}
    excluded = sorted(
        f"{repo}/{m}" if member_dir(m) else repo
        for repo in MONO_REPOS
        for m in ((per_repo[repo].get("mono", {}) or {}).get(
            "exclude_members", []))
    )

    by_name = collect_workspace_deps(root)
    forced = mono_overlay.get("deps", {}) or {}
    for dep_name, spec in forced.items():
        if not isinstance(spec, dict):
            raise MonoError(
                f"overlays/mono.toml [deps.{dep_name}] must be a table"
            )
    merged_deps: dict[str, dict] = {}
    for dep_name in sorted(by_name):
        if dep_name in forced:
            continue  # overlay replaces the merged spec entirely
        merged_deps[dep_name] = merge_specs(dep_name, by_name[dep_name])
    for dep_name, spec in forced.items():
        merged_deps[dep_name] = dict(spec)

    per_repo_lints = {}
    for repo in MONO_REPOS:
        doc = load_toml(root / repo / "Cargo.toml")
        per_repo_lints[repo] = (doc.get("workspace", {}) or {}).get("lints")
    merged_lints, dropped_lints = merge_lints(per_repo_lints)
    for key, spec in ((mono_overlay.get("lints", {}) or {}).items()):
        if spec is False:
            for group in merged_lints:
                merged_lints[group].pop(key, None)
            continue
        if not isinstance(spec, dict) or "group" not in spec:
            raise MonoError(
                "overlays/mono.toml [lints] entries must be false or "
                "{ group = ..., ... }"
            )
        group = spec.pop("group")
        merged_lints.setdefault(group, {})[key] = spec
    merged_lints = {g: v for g, v in merged_lints.items() if v}

    patches = collect_patches(root)

    validate_member_pins(members, mono_version, member_manifests)
    validate_external_paths(
        root, members, mono_version, set(merged_deps), set(package), exclude
    )
    validate_inherit_coverage(
        members, set(merged_deps), set(package)
    )

    text = emit_manifest(
        member_rel, exclude, package, merged_deps, merged_lints, patches
    )
    report = {
        "members": len(members),
        "excluded": excluded,
        "missing_excludes": sorted(
            rel for rel in exclude if not (root / rel).exists()
        ),
        "workspace_deps": len(merged_deps),
        "forced_deps": sorted(forced),
        "dropped_lints": dropped_lints,
        "patches": {s: sorted(p) for s, p in patches.items()},
    }
    return text, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the aggregate mono Cargo workspace manifest."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed manifest differs from generated output",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="print the merge decisions report",
    )
    args = parser.parse_args(argv)
    root = aggregate_root()
    try:
        text, report = generate(root)
    except MonoError as exc:
        print(f"mono: error: {exc}", file=sys.stderr)
        return 1
    if args.report or args.check:
        print(f"members: {report['members']}")
        print(f"excluded: {report['excluded']}")
        print(f"missing_excludes: {report['missing_excludes']}")
        print(f"workspace_deps: {report['workspace_deps']}")
        print(f"forced_deps: {report['forced_deps']}")
        print(f"dropped_lints: {report['dropped_lints']}")
        print(f"patches: {report['patches']}")
    manifest = root / "Cargo.toml"
    if args.check:
        if not manifest.is_file():
            print("mono: error: root Cargo.toml does not exist", file=sys.stderr)
            return 1
        current = manifest.read_text(encoding="utf-8")
        if current != text:
            print(
                "mono: root Cargo.toml is stale; regenerate with "
                "python3 scripts/mono/generate.py",
                file=sys.stderr,
            )
            return 1
        print("mono: root Cargo.toml is up to date")
        return 0
    manifest.write_text(text, encoding="utf-8")
    print(f"mono: wrote {manifest} ({report['members']} members)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

