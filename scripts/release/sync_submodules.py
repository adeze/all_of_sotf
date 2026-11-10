#!/usr/bin/env python3
"""Synchronize Rust submodule workspaces: gate, changelog, bump, commit.

Phases (subcommands):
  status   Read-only report: dirty/ahead state, versions, changelog state.
  check    Run the gate ladder (lint -> test, plus qa with --qa). No writes.
  prepare  Gate, then bump Cargo.toml and promote the CHANGELOG Unreleased
           section. Leaves everything unstaged for review. Never commits.
  commit   Commit prepared submodules, then the top-level gitlinks.
           Requires --yes. Never pushes.

Only submodule Rust workspaces from scripts/quality-matrix/repos.json are
considered (gpui-toolkit is a git dependency and stays out of scope).
Workspaces are processed foundations-first (dependency_graph.LAYERS order)
so dependents resolve against already-bumped path dependencies.

Unknown workspaces, missing just recipes, and unrecognized changelog or
manifest shapes are hard errors: this tool never guesses a fallback.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from workspaces import workspace_names

NOISE_BASENAMES = {".DS_Store"}
RELEASE_FILES = ("Cargo.toml", "Cargo.lock", "CHANGELOG.md")
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class SyncError(RuntimeError):
    """Hard failure: unknown shape or state this tool must not guess at."""


@dataclass(frozen=True)
class Recipes:
    lint: str
    test: str
    qa: str | None


@dataclass(frozen=True)
class WorkspaceSpec:
    name: str
    layer: int
    recipes: Recipes
    version_section: str
    changelog_style: str
    daemon_manifest: str | None = None


# Explicit per-workspace contract, verified live against `just --list`.
# Layer order mirrors dependency_graph.LAYERS (higher = more foundational).
SPECS: dict[str, WorkspaceSpec] = {
    spec.name: spec
    for spec in (
        WorkspaceSpec("math-audio", 3, Recipes("lint", "ntest", "qa"),
                      "workspace.package", "bracketed"),
        WorkspaceSpec("sofa-reader", 3, Recipes("clippy", "ntest", "all"),
                      "package", "bracketed"),
        WorkspaceSpec("autoeq", 2, Recipes("lint", "ntest", "qa"),
                      "workspace.package", "hash"),
        WorkspaceSpec("sotf-daw", 1, Recipes("lint", "ntest", "qa"),
                      "workspace.package", "bracketed"),
        WorkspaceSpec("sotf-capture", 1, Recipes("clippy", "ntest", "qa"),
                      "package", "insert"),
        WorkspaceSpec("sotf", 0, Recipes("lint", "ntest", "qa"),
                      "workspace.package", "bracketed"),
        WorkspaceSpec("sotf-systemwide", 0, Recipes("lint", "test", "qa"),
                      "workspace.package", "daemon",
                      daemon_manifest="crates/daemon/Cargo.toml"),
    )
}

CHANGELOG_SKELETON = "# Changelog\n\n## [Unreleased]\n\n"


def spec_for(name: str) -> WorkspaceSpec:
    if name not in SPECS:
        raise SyncError(f"no sync spec for workspace {name!r}; add one explicitly")
    return SPECS[name]


def selected_workspaces(requested: list[str]) -> list[WorkspaceSpec]:
    known = workspace_names(local_only=True)
    for name in known:
        spec_for(name)
    names = requested or known
    for name in names:
        if name not in known:
            raise SyncError(f"{name!r} is not a local Rust submodule workspace")
    return sorted((spec_for(name) for name in names),
                  key=lambda spec: (-spec.layer, spec.name))


def run(args: list[str], cwd: Path, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True,
                          capture_output=capture, check=False)


def git(args: list[str], cwd: Path) -> str:
    result = run(["git", *args], cwd)
    if result.returncode != 0:
        raise SyncError(f"git {' '.join(args)} failed in {cwd}: {result.stderr.strip()}")
    return result.stdout


def is_noise(path: str) -> bool:
    return Path(path).name in NOISE_BASENAMES


def parse_porcelain(text: str) -> list[tuple[str, str]]:
    """Return (xy, path) pairs from `git status --porcelain` output."""
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        entries.append((line[:2], path.strip('"')))
    return entries


def meaningful_changes(workspace: Path) -> list[tuple[str, str]]:
    entries = parse_porcelain(git(["status", "--porcelain"], workspace))
    return [(xy, path) for xy, path in entries if not is_noise(path)]


def gitlink_revision(root: Path, name: str) -> str:
    out = git(["ls-tree", "HEAD", name], root).split()
    if len(out) < 3:
        raise SyncError(f"{name}: no gitlink recorded at top-level HEAD")
    return out[2]


def changed_paths_since(workspace: Path, revision: str) -> list[str]:
    committed = git(["diff", "--name-only", f"{revision}..HEAD"], workspace).splitlines()
    pending = [path for _, path in meaningful_changes(workspace)]
    return sorted(set(committed + pending))


@dataclass
class WorkspaceState:
    spec: WorkspaceSpec
    head: str
    gitlink: str
    ahead: int
    changes: list[tuple[str, str]]
    version: str
    head_version: str
    changelog: str
    blockers: list[str] = field(default_factory=list)

    @property
    def dirty(self) -> bool:
        return bool(self.changes)

    @property
    def code_changed(self) -> bool:
        paths = {path for _, path in self.changes}
        return bool(paths - {"Cargo.lock", "CHANGELOG.md"})

    @property
    def needs_action(self) -> bool:
        return self.dirty or self.ahead > 0

    @property
    def needs_bump(self) -> bool:
        return self.code_changed and self.version == self.head_version


def version_in_manifest(text: str, section: str, origin: str) -> tuple[str, int, int]:
    """Return (version, start, end) of the version string under [section]."""
    header = re.search(rf"^\[{re.escape(section)}\]\s*$", text, re.MULTILINE)
    if header is None:
        raise SyncError(f"{origin}: no [{section}] section")
    rest = text[header.end():]
    following = re.search(r"^\[", rest, re.MULTILINE)
    body = rest[:following.start()] if following else rest
    match = re.search(r'^version\s*=\s*"([^"]+)"', body, re.MULTILINE)
    if match is None:
        raise SyncError(f"{origin}: no version under [{section}]")
    start = header.end() + match.start(1)
    return match.group(1), start, header.end() + match.end(1)


def bump_semver(version: str, part: str) -> str:
    match = SEMVER_RE.match(version)
    if match is None:
        raise SyncError(f"not a plain semver version: {version!r}")
    major, minor, patch = (int(group) for group in match.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise SyncError(f"unknown bump part: {part!r}")


def write_version(manifest: Path, section: str, new_version: str) -> str:
    text = manifest.read_text(encoding="utf-8")
    old, start, end = version_in_manifest(text, section, str(manifest))
    manifest.write_text(text[:start] + new_version + text[end:], encoding="utf-8")
    return old


def unreleased_body(text: str, style: str) -> str | None:
    """Body of the Unreleased section, or None when the style has none."""
    if style == "insert":
        return None
    patterns = {
        "hash": r"^# Unreleased[ \t]*$",
        "bracketed": r"^## \[Unreleased\][ \t]*$",
        "daemon": r"^## Unreleased[ \t]*$",
    }
    if style not in patterns:
        raise SyncError(f"unknown changelog style: {style!r}")
    match = re.search(patterns[style], text, re.MULTILINE)
    if match is None:
        raise SyncError(f"changelog has no Unreleased heading for style {style!r}")
    rest = text[match.end():]
    level = "#" if style == "hash" else "##"
    following = re.search(rf"^{level} ", rest, re.MULTILINE)
    return (rest[:following.start()] if following else rest).strip()


def changelog_state(workspace: Path, spec: WorkspaceSpec, changed: bool) -> str:
    path = workspace / "CHANGELOG.md"
    if not path.is_file():
        return "missing"
    if spec.changelog_style == "insert":
        return "updated" if changed else "no-unreleased-section"
    body = unreleased_body(path.read_text(encoding="utf-8"), spec.changelog_style)
    return "ready" if body else "empty-unreleased"


def promote_changelog(workspace: Path, spec: WorkspaceSpec, version: str,
                        daemon_version: str | None, today: date) -> None:
    path = workspace / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    stamp = today.isoformat()
    if spec.changelog_style == "hash":
        new, count = re.subn(r"^# Unreleased[ \t]*$", f"# Unreleased\n\n# {version}",
                             text, count=1, flags=re.MULTILINE)
    elif spec.changelog_style == "bracketed":
        new, count = re.subn(r"^## \[Unreleased\][ \t]*$",
                             f"## [Unreleased]\n\n## [{version}] - {stamp}",
                             text, count=1, flags=re.MULTILINE)
    elif spec.changelog_style == "daemon":
        if daemon_version is None:
            raise SyncError(f"{spec.name}: daemon version required to promote changelog")
        new, count = re.subn(r"^## Unreleased[ \t]*$",
                             f"## Unreleased\n\n## {version} (daemon {daemon_version})",
                             text, count=1, flags=re.MULTILINE)
    elif spec.changelog_style == "insert":
        match = re.search(r"^## ", text, re.MULTILINE)
        if match is None:
            raise SyncError(f"{spec.name}: changelog has no release heading to insert above")
        new = text[:match.start()] + f"## {version} \u2014 {stamp}\n\n" + text[match.start():]
        count = 1
    else:
        raise SyncError(f"unknown changelog style: {spec.changelog_style!r}")
    if count != 1:
        raise SyncError(f"{spec.name}: could not promote CHANGELOG.md Unreleased section")
    path.write_text(new, encoding="utf-8")


def release_section_body(text: str, spec: WorkspaceSpec, version: str) -> str:
    """Body under the heading for `version`; empty string when absent/empty."""
    if spec.changelog_style == "hash":
        pattern, level = rf"^# {re.escape(version)}[ \t]*$", "#"
    elif spec.changelog_style == "bracketed":
        pattern, level = rf"^## \[{re.escape(version)}\].*$", "##"
    elif spec.changelog_style == "daemon":
        pattern, level = rf"^## {re.escape(version)} .*$", "##"
    elif spec.changelog_style == "insert":
        pattern, level = rf"^## {re.escape(version)} .*$", "##"
    else:
        raise SyncError(f"unknown changelog style: {spec.changelog_style!r}")
    match = re.search(pattern, text, re.MULTILINE)
    if match is None:
        return ""
    rest = text[match.end():]
    following = re.search(rf"^{level} ", rest, re.MULTILINE)
    return (rest[:following.start()] if following else rest).strip()


def collect_state(root: Path, spec: WorkspaceSpec) -> WorkspaceState:
    workspace = root / spec.name
    if not (workspace / "Cargo.toml").is_file():
        raise SyncError(f"{spec.name}: missing Cargo.toml (submodule not populated?)")
    head = git(["rev-parse", "HEAD"], workspace).strip()
    gitlink = gitlink_revision(root, spec.name)
    ahead = int(git(["rev-list", "--count", f"{gitlink}..HEAD"], workspace).strip())
    changes = meaningful_changes(workspace)
    manifest_text = (workspace / "Cargo.toml").read_text(encoding="utf-8")
    version, _, _ = version_in_manifest(manifest_text, spec.version_section, spec.name)
    head_text = git(["show", "HEAD:Cargo.toml"], workspace)
    head_version, _, _ = version_in_manifest(head_text, spec.version_section, spec.name)
    changelog_changed = any(path == "CHANGELOG.md" for _, path in changes)
    return WorkspaceState(spec, head, gitlink, ahead, changes,
                          version, head_version,
                          changelog_state(workspace, spec, changelog_changed))


def changelog_blocker(state: WorkspaceState) -> str | None:
    if not state.code_changed and state.ahead == 0:
        return None
    if state.changelog == "missing":
        return "CHANGELOG.md is missing; prepare creates a skeleton to fill in"
    if state.changelog == "empty-unreleased":
        return "CHANGELOG.md Unreleased section is empty; add entries before prepare"
    if state.changelog == "no-unreleased-section":
        return "CHANGELOG.md has no Unreleased section and is unchanged; " \
               "entries go under the heading prepare inserts, before commit"
    if state.changelog in {"ready", "updated"}:
        return None
    raise SyncError(f"unknown changelog state: {state.changelog!r}")


def available_recipes(workspace: Path) -> set[str]:
    result = run(["just", "--list", "--unsorted"], workspace)
    if result.returncode != 0:
        raise SyncError(f"{workspace.name}: `just --list` failed: {result.stderr.strip()}")
    recipes = set()
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            recipes.add(stripped.split()[0])
    return recipes


def gate_recipes(spec: WorkspaceSpec, include_qa: bool) -> list[str]:
    recipes = [spec.recipes.lint, spec.recipes.test]
    if include_qa:
        if spec.recipes.qa is None:
            raise SyncError(f"{spec.name}: no qa recipe mapped")
        recipes.append(spec.recipes.qa)
    return recipes


def run_gates(workspace: Path, spec: WorkspaceSpec, include_qa: bool) -> list[dict[str, object]]:
    present = available_recipes(workspace)
    results: list[dict[str, object]] = []
    for recipe in gate_recipes(spec, include_qa):
        if recipe not in present:
            raise SyncError(f"{spec.name}: just recipe {recipe!r} not found")
        print(f"--- {spec.name}: just {recipe}", flush=True)
        started = time.monotonic()
        result = run(["just", recipe], workspace, capture=False)
        elapsed = round(time.monotonic() - started, 1)
        results.append({"recipe": recipe, "exit": result.returncode, "seconds": elapsed})
        if result.returncode != 0:
            break
    return results


def state_report(state: WorkspaceState) -> dict[str, object]:
    return {
        "workspace": state.spec.name,
        "version": state.version,
        "head_version": state.head_version,
        "ahead": state.ahead,
        "dirty": state.dirty,
        "changes": [path for _, path in state.changes],
        "changelog": state.changelog,
        "needs_bump": state.needs_bump,
    }


def print_state(state: WorkspaceState) -> None:
    flags = []
    if state.dirty:
        flags.append(f"dirty({len(state.changes)})")
    if state.ahead:
        flags.append(f"ahead({state.ahead})")
    if state.needs_bump:
        flags.append("needs-bump")
    print(f"{state.spec.name}: v{state.version} changelog={state.changelog} "
          f"{' '.join(flags) or 'clean'}")
    blocker = changelog_blocker(state)
    if blocker:
        print(f"  BLOCKER: {blocker}")


def daemon_version_after_prepare(root: Path, state: WorkspaceState,
                                   bump: str) -> str | None:
    spec = state.spec
    if spec.daemon_manifest is None:
        return None
    workspace = root / spec.name
    manifest = workspace / spec.daemon_manifest
    current, _, _ = version_in_manifest(
        manifest.read_text(encoding="utf-8"), "package", str(manifest))
    daemon_dir = str(Path(spec.daemon_manifest).parent)
    touched = any(path.startswith(daemon_dir + "/")
                  for path in changed_paths_since(workspace, state.gitlink))
    if not touched:
        return current
    new = bump_semver(current, bump)
    write_version(manifest, "package", new)
    print(f"  daemon: {current} -> {new}")
    return new


def phase_status(root: Path, states: list[WorkspaceState]) -> int:
    for state in states:
        print_state(state)
    return 0


def phase_check(root: Path, states: list[WorkspaceState], include_qa: bool,
                check_all: bool, report: dict[str, Any]) -> int:
    failures = 0
    for state in states:
        print_state(state)
        if changelog_blocker(state):
            failures += 1
        if not check_all and not state.needs_action:
            continue
        results = run_gates(root / state.spec.name, state.spec, include_qa)
        report.setdefault("gates", {})[state.spec.name] = results
        if results and results[-1]["exit"] != 0:
            failures += 1
            print(f"  FAIL: just {results[-1]['recipe']} exited {results[-1]['exit']}")
    return 1 if failures else 0


def phase_prepare(root: Path, states: list[WorkspaceState], bump: str,
                  include_qa: bool, skip_gates: bool,
                  report: dict[str, Any]) -> int:
    failures = 0
    for state in states:
        print_state(state)
        if not state.needs_action:
            continue
        workspace = root / state.spec.name
        blocked = False
        if state.changelog == "missing":
            (workspace / "CHANGELOG.md").write_text(CHANGELOG_SKELETON, encoding="utf-8")
            print("  created CHANGELOG.md skeleton; fill in Unreleased, then re-run prepare")
            blocked = True
        elif state.changelog == "empty-unreleased":
            blocked = True
        if blocked:
            failures += 1
        elif state.needs_bump:
            new_version = bump_semver(state.version, bump)
            write_version(workspace / "Cargo.toml", state.spec.version_section, new_version)
            daemon = daemon_version_after_prepare(root, state, bump)
            promote_changelog(workspace, state.spec, new_version, daemon, date.today())
            refresh = run(["cargo", "metadata", "--format-version", "1"], workspace)
            if refresh.returncode != 0:
                raise SyncError(f"{state.spec.name}: cargo metadata failed after bump: "
                                f"{refresh.stderr.strip()}")
            print(f"  bumped: {state.version} -> {new_version}; changelog promoted")
            report.setdefault("prepared", {})[state.spec.name] = new_version
        if not skip_gates:
            results = run_gates(workspace, state.spec, include_qa)
            report.setdefault("gates", {})[state.spec.name] = results
            if results and results[-1]["exit"] != 0:
                failures += 1
                print(f"  FAIL: just {results[-1]['recipe']} exited {results[-1]['exit']}")
    if not failures:
        print("prepare complete; review `git -C <workspace> diff`, then run commit --yes")
    return 1 if failures else 0


def phase_commit(root: Path, states: list[WorkspaceState], message: str | None,
                 stage_all: bool, skip_top: bool,
                 report: dict[str, Any]) -> int:
    committed: list[str] = []
    for state in states:
        if not state.dirty and state.ahead == 0:
            continue
        print_state(state)
        workspace = root / state.spec.name
        if state.dirty:
            if state.needs_bump:
                print("  REFUSE: version not bumped; run prepare first")
                return 1
            if state.version != state.head_version:
                text = (workspace / "CHANGELOG.md").read_text(encoding="utf-8")
                if not release_section_body(text, state.spec, state.version):
                    print(f"  REFUSE: CHANGELOG.md has no entries under v{state.version}")
                    return 1
            if stage_all:
                for _, path in state.changes:
                    git(["add", "--", path], workspace)
            else:
                for name in RELEASE_FILES:
                    if any(path == name for _, path in state.changes):
                        git(["add", "--", name], workspace)
                unstaged = [path for xy, path in meaningful_changes(workspace)
                            if xy[1] != " "]
                if unstaged:
                    print(f"  REFUSE: unstaged changes remain {unstaged}; "
                          "stage them or pass --stage-all")
                    return 1
            staged = git(["diff", "--cached", "--name-only"], workspace).splitlines()
            if not staged:
                print("  REFUSE: nothing staged")
                return 1
            print(f"  committing {len(staged)} files: {', '.join(staged)}")
            git(["commit", "-m", message or f"release: v{state.version}"], workspace)
        committed.append(state.spec.name)
        report.setdefault("committed", []).append(state.spec.name)
    if skip_top or not committed:
        return 0
    moved = [name for name in committed
             if git(["rev-parse", "HEAD"], root / name).strip()
             != gitlink_revision(root, name)]
    if not moved:
        print("top level: no gitlinks moved")
        return 0
    preflight = run([sys.executable, "scripts/release/preflight.py"], root)
    if preflight.returncode != 0:
        print(preflight.stdout + preflight.stderr)
        print("REFUSE: top-level preflight failed; gitlinks left uncommitted")
        return 1
    for name in moved:
        git(["add", "--", name], root)
    summary = ", ".join(
        f"{name} {collect_state(root, spec_for(name)).version}" for name in moved)
    git(["commit", "-m", f"chore: update submodules ({summary})"], root)
    print(f"top level: committed gitlinks for {summary}")
    print("next: push submodule branches and the top level to GitLab and open MRs "
          "(this tool never pushes)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", nargs="?", default="status",
                        choices=["status", "check", "prepare", "commit"])
    parser.add_argument("--workspace", action="append", default=[],
                        help="limit to this workspace (repeatable)")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--all", action="store_true",
                        help="check: gate every selected workspace, not only dirty/ahead")
    parser.add_argument("--qa", action="store_true", help="also run the qa recipe")
    parser.add_argument("--bump", default="patch", choices=["patch", "minor", "major"])
    parser.add_argument("--skip-gates", action="store_true",
                        help="prepare: bump/promote without running gates")
    parser.add_argument("--yes", action="store_true", help="commit: required approval")
    parser.add_argument("--stage-all", action="store_true",
                        help="commit: stage every meaningful change, not only the index")
    parser.add_argument("--message", help="commit: message for submodule commits")
    parser.add_argument("--skip-top", action="store_true",
                        help="commit: do not commit top-level gitlinks")
    parser.add_argument("--report", type=Path, help="write JSON report to this path")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        specs = selected_workspaces(args.workspace)
        states = [collect_state(root, spec) for spec in specs]
        report: dict[str, Any] = {
            "phase": args.phase,
            "workspaces": [state_report(state) for state in states],
        }
        if args.phase == "status":
            code = phase_status(root, states)
        elif args.phase == "check":
            code = phase_check(root, states, args.qa, args.all, report)
        elif args.phase == "prepare":
            code = phase_prepare(root, states, args.bump, args.qa,
                                 args.skip_gates, report)
        elif args.phase == "commit":
            if not args.yes:
                parser.error("commit requires --yes (review prepare output first)")
            code = phase_commit(root, states, args.message, args.stage_all,
                                args.skip_top, report)
        else:
            raise SyncError(f"unknown phase: {args.phase!r}")
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
