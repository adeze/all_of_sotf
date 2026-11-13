#!/usr/bin/env python3
"""Candidate-only compile and upstream-test qualification of the immutable Cosmic compatibility fork."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.release.checkout_sources import read_manifest
from scripts.release import nih_native_artifact_check as linux_owned
from scripts.release import nih_macos_artifact_check as mac_owned

FORK = "85e4064fcb17aba26b774f1481ce3ba1fa29c57f"
MANIFEST_CHILD = "5d2ccb99a7a470919b62eae46e37bb0d6aa61a48"
OFFICIAL_SHAPE_BLOB = "04f221951ea103223aecdd3999c7c738be47f72e"
FORK_SHAPE_BLOB = "481fb4dd662c417616afa515490237a5bd904e00"
OFFICIAL = "59089955e1c8698c6b83b2e6ab6ebceff825ff96"
TREE = "bce7f98f2adc7a8c07f646dc05070423b5c8e9ac"
OFFICIAL_MANIFEST_BLOB = "08866e238d14bfb4dfae4f1a190d74902354b9b9"
FORK_MANIFEST_BLOB = "08f7122fa47aa2601bba8c9b45b6b3ecdb94a148"
owned = mac_owned if sys.platform == "darwin" else linux_owned
OUTPUT = ROOT / "target/release-gitea/cosmic-standalone"

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def root_snapshot(revisions: dict[str, str]) -> dict:
    return owned.snapshot(revisions)


def root_errors(before: dict, after: dict, revisions: dict[str, str]) -> list[str]:
    return owned.source_errors(before, after, revisions)

def fork_state(repo: Path) -> dict:
    return {
        "head": git(repo, "rev-parse", "HEAD"),
        "parent": git(repo, "rev-parse", "HEAD^"),
        "grandparent": git(repo, "rev-parse", "HEAD^^"),
        "tree": git(repo, "rev-parse", "HEAD^{tree}"),
        "worktree_entries": git(repo, "status", "--porcelain", "--untracked-files=all", "--ignored").splitlines(),
        "official_shape_blob": git(repo, "rev-parse", f"{OFFICIAL}:src/shape.rs"),
        "fork_shape_blob": git(repo, "rev-parse", f"{FORK}:src/shape.rs"),
        "changed_paths": git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", OFFICIAL, FORK).splitlines(),
        "official_manifest_blob": git(repo, "rev-parse", f"{OFFICIAL}:Cargo.toml"),
        "fork_manifest_blob": git(repo, "rev-parse", f"{FORK}:Cargo.toml"),
        "license_apache": git(repo, "rev-parse", "HEAD:LICENSE-APACHE"),
        "license_mit": git(repo, "rev-parse", "HEAD:LICENSE-MIT"),
    }


def verify_initial_fork(state: dict) -> None:
    expected = {
        "head": FORK, "parent": MANIFEST_CHILD, "grandparent": OFFICIAL,
        "tree": TREE, "changed_paths": ["Cargo.toml", "src/shape.rs"],
        "official_manifest_blob": OFFICIAL_MANIFEST_BLOB,
        "fork_manifest_blob": FORK_MANIFEST_BLOB,
        "official_shape_blob": OFFICIAL_SHAPE_BLOB,
        "fork_shape_blob": FORK_SHAPE_BLOB,
        "license_apache": "6f756351aae24b479e6a9418c1f08a8b7a991076",
        "license_mit": "db6aab15cf8c6a1f348650f0c6fa4df60d026a89",
        "worktree_entries": [],
    }
    mismatched = [field for field, value in expected.items() if state.get(field) != value]
    if mismatched:
        raise ValueError(f"Cosmic source, license, or cleanliness differs: {mismatched}")


def run(repo: Path, name: str, command: list[str], report: dict, env: dict[str, str]) -> dict:
    # The reviewed helper owns the process group and saves RUNNING/final evidence.
    # An absolute path is fixed under the ignored target directory, never user input.
    argv = ["bash", "-c", 'cd "$1" && shift && exec "$@"', "cosmic", str(repo), *command]
    return owned.run_owned(name, argv, report, env)

LFS_POINTER = re.compile(rb"^version https://git-lfs.github.com/spec/v1\noid sha256:([0-9a-f]{64})\nsize ([0-9]+)\n?$", re.MULTILINE)
TEST_LINE = re.compile(r"^test ([A-Za-z0-9_:]+) \.\.\. (ok|FAILED|ignored)$", re.MULTILINE)
SUMMARY = re.compile(r"^test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored;", re.MULTILINE)
REQUIRED_LAYOUT = {"empty_lines_use_span_metrics", "wrap_word_fallback", "stable_wrap",
                   "wrap_extra_line", "auto_detects_per_paragraph",
                   "test_ellipsize_ltr_end_single_line", "test_hebrew_word_rendering"}


def verified_lfs_assets(repo: Path) -> dict[str, str]:
    names = git(repo, "ls-files", "*.ttf", "*.png").splitlines()
    if len(names) != 34 or sum(name.endswith(".ttf") for name in names) != 7 or sum(name.endswith(".png") for name in names) != 27:
        raise ValueError("Cosmic font and rendering fixture inventory differs")
    hashes = {}
    for name in names:
        pointer = subprocess.check_output(["git", "-C", str(repo), "show", f"HEAD:{name}"])
        match = LFS_POINTER.fullmatch(pointer)
        if match is None:
            raise ValueError(f"{name}: missing canonical LFS pointer")
        data = (repo / name).read_bytes()
        if len(data) != int(match.group(2)) or sha(data) != match.group(1).decode():
            raise ValueError(f"{name}: real LFS payload is missing or differs")
        hashes[name] = sha(data)
    return hashes


def upstream_tests(name: str) -> dict:
    log = (OUTPUT / "logs" / f"{name}.log").read_text(errors="replace")
    rows = TEST_LINE.findall(log)
    passed = [test for test, result in rows if result == "ok"]
    summaries = [(int(a), int(b), int(c)) for a, b, c in SUMMARY.findall(log)]
    expected = 42 if name == "default-tests" else 49
    needed = REQUIRED_LAYOUT | ({"editor_line_endings_preserved"} if name == "all-feature-tests" else set())
    if len(passed) != expected or len(rows) != expected or len(set(passed)) != expected or not needed.issubset(set(passed)):
        raise ValueError(f"{name}: upstream layout/fallback test inventory incomplete")
    if len(summaries) != 10 or sum(a for a, _, _ in summaries) != expected or any(b or c for _, b, c in summaries):
        raise ValueError(f"{name}: upstream test summaries failed, ignored, or incomplete: {summaries}")
    if re.search(r"^test result: FAILED", log, re.MULTILINE):
        raise ValueError(f"{name}: failed upstream test result")
    return {"passed": expected, "names": sorted(passed), "summaries": summaries}


def main() -> int:
    if sys.flags.optimize != 0:
        raise SystemExit("Python assertions must remain active")
    if os.environ.get("CI") != "true" or os.environ.get("DISPOSABLE") != "1":
        raise SystemExit("Cosmic standalone check runs only in disposable Gitea CI")
    owned.OUTPUT = OUTPUT
    signal.signal(signal.SIGINT, owned.interrupted)
    signal.signal(signal.SIGTERM, owned.interrupted)
    output = OUTPUT
    output.mkdir(parents=True, exist_ok=True)
    (output / "logs").mkdir(exist_ok=True)
    report: dict = {"qualification": "default/all-feature upstream layout and fallback tests with real LFS fixtures; no downstream render claim", "commands": [], "errors": []}
    revisions: dict[str, str] = {}
    before: dict | None = None
    try:
        _, _, revisions = read_manifest(ROOT / "scripts/release/sources.json")
        before = root_snapshot(revisions)
        report["before"] = before
        report["errors"].extend(root_errors(before, before, revisions))
        if report["errors"]:
            raise ValueError("initial nine-source and ten-lock guard failed")
        repo = output / "cosmic-source"
        state = fork_state(repo)
        report["fork_before"] = state
        verify_initial_fork(state)
        if (repo / "Cargo.lock").exists():
            raise ValueError("fork checkout unexpectedly contains Cargo.lock")
        owned.enable_subreaper()
        private_home = output / "private-home"
        private_home.mkdir(exist_ok=True)
        env = os.environ.copy()
        for variable in ("CARGO_HOME", "RUSTUP_HOME"):
            value = env.get(variable)
            if not value:
                value = str(Path.home() / (".cargo" if variable == "CARGO_HOME" else ".rustup"))
            env[variable] = str(Path(value).expanduser().resolve())
        env["HOME"] = str(private_home)
        env["XDG_CONFIG_HOME"] = str(private_home / "config")
        env["XDG_CACHE_HOME"] = str(private_home / "cache")
        env["CARGO_TARGET_DIR"] = str(output / "build")
        (private_home / ".cargo").mkdir(exist_ok=True)
        commands = [
            ("lfs-pull", ["git", "lfs", "pull"]),
            ("resolve", ["cargo", "generate-lockfile"]),
            ("default", ["cargo", "check", "--lib"]),
            ("all-features", ["cargo", "check", "--all-features", "--all-targets"]),
            ("no-default", ["cargo", "check", "--no-default-features", "--lib"]),
            ("no-std", ["cargo", "check", "--no-default-features", "--features", "no_std", "--lib"]),
            ("default-tests", ["cargo", "test", "--lib", "--tests"]),
            ("all-feature-tests", ["cargo", "test", "--all-features", "--lib", "--tests"]),
        ]
        lock_hash = None
        for name, argv in commands:
            if owned.STOP:
                break
            result = run(repo, name, argv, report, env)
            if result["status"] != "PASS":
                break
            if name == "lfs-pull":
                report["lfs_assets"] = verified_lfs_assets(repo)
                continue
            lock = repo / "Cargo.lock"
            if not lock.is_file():
                raise ValueError("generated standalone Cargo.lock missing")
            current = sha(lock.read_bytes())
            if lock_hash is None:
                lock_hash = current
            elif current != lock_hash:
                raise ValueError("standalone Cargo.lock changed after locked command")
        report["fork_lock_sha256"] = lock_hash
        report["upstream_test_inventories"] = {name: upstream_tests(name) for name in ("default-tests", "all-feature-tests")}
        report["fork_after"] = fork_state(repo)
        after_fork = report["fork_after"].copy()
        ignored = after_fork.pop("worktree_entries")
        before_fork = state.copy()
        before_fork.pop("worktree_entries")
        if after_fork != before_fork or ignored != ["!! Cargo.lock"]:
            raise ValueError(f"fork source or worktree changed outside generated Cargo.lock: {ignored}")
        if len(report["commands"]) != len(commands) or any(x["status"] != "PASS" for x in report["commands"]):
            report["errors"].append("compile inventory incomplete or failed")
    except KeyboardInterrupt:
        report["errors"].append("interrupted before completion")
    except Exception as error:
        report["errors"].append(str(error))
    finally:
        try:
            report["after"] = root_snapshot(revisions)
            if before is None:
                report["errors"].append("initial root snapshot unavailable")
            else:
                report["errors"].extend(root_errors(before, report["after"], revisions))
        except Exception as error:
            report["errors"].append(f"final root guard: {error}")
        report["status"] = "PASS" if not owned.STOP and not report["errors"] else "FAIL"
        owned.save(report)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
