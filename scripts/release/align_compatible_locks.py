#!/usr/bin/env python3
"""Propose compatible registry lock updates across the ten release lockfiles.

This is a Gitea preparation lane. Its output requires separate package/edge
review before any Cargo.lock is committed. It never edits manifests.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import tomllib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "release"))
from checkout_sources import read_manifest

WORKSPACES = (
    "math-audio", "gpui-toolkit", "sofa-reader",
    "autoeq", "sotf-daw", "sotf-capture", "sotf", "sotf-systemwide",
)
NESTED = "autoeq-gpui-examples"
VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$"
)
WASM_FAMILY = {"wasm-bindgen", "wasm-bindgen-futures", "wasm-bindgen-macro",
               "wasm-bindgen-macro-support", "wasm-bindgen-shared", "wasm-bindgen-test",
               "wasm-bindgen-test-macro", "wasm-bindgen-test-shared", "js-sys", "web-sys"}
# Reviewed crates.io manifests: wasm-bindgen-test 0.3.79 has exact
# test-macro 0.3.79, test-shared 0.2.129, js-sys 0.3.106,
# wasm-bindgen 0.2.129, and futures 0.4.79 dependencies.
WASM_TEST_COMPANIONS = (("wasm-bindgen-test", "0.3.77", "0.3.79"),
                        ("wasm-bindgen-test-macro", "0.3.77", "0.3.79"),
                        ("wasm-bindgen-test-shared", "0.2.127", "0.2.129"))
CRATES_IO = "registry+https://github.com/rust-lang/crates.io-index"
ACTIVE: subprocess.Popen | None = None


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def version_key(raw: str) -> tuple[int, int, int] | None:
    match = VERSION.fullmatch(raw)
    if not match or match.group(4):
        return None
    return tuple(int(match.group(i)) for i in (1, 2, 3))


def compatibility(raw: str) -> tuple[int, ...] | None:
    parsed = version_key(raw)
    if parsed is None:
        return None
    major, minor, patch = parsed
    if major:
        return (major,)
    if minor:
        return (0, minor)
    return (0, 0, patch)


def lock_specs(root: Path) -> list[tuple[str, Path, Path]]:
    specs = [(name, root / name, root / name / "Cargo.lock") for name in WORKSPACES]
    specs.append((NESTED, root / "autoeq" / "crates" / NESTED,
                  root / "autoeq" / "crates" / NESTED / "Cargo.lock"))
    return specs


def registry_versions(lock: Path) -> dict[str, set[str]]:
    packages = tomllib.loads(lock.read_text(encoding="utf-8")).get("package", [])
    found: dict[str, set[str]] = defaultdict(set)
    for package in packages:
        if package.get("source") == CRATES_IO:
            found[package["name"]].add(package["version"])
    return found


def package_ids(lock: Path) -> set[tuple[str, str, str]]:
    return {
        (item["name"], item["version"], item.get("source", ""))
        for item in tomllib.loads(lock.read_text(encoding="utf-8")).get("package", [])
    }


def family_versions(lock: Path) -> dict[str, list[str]]:
    versions = registry_versions(lock)
    return {name: sorted(versions[name]) for name in sorted(WASM_FAMILY) if name in versions}


def plans(specs: list[tuple[str, Path, Path]]) -> tuple[list[dict], list[dict]]:
    all_versions: dict[tuple[str, tuple[int, ...]], set[str]] = defaultdict(set)
    rejected: list[dict] = []
    for name, _, lock in specs:
        for package, versions in registry_versions(lock).items():
            for version in versions:
                group = compatibility(version)
                if group is None:
                    rejected.append({"workspace": name, "package": package,
                                     "version": version, "reason": "prerelease or invalid semver"})
                else:
                    all_versions[(package, group)].add(version)
    updates: list[dict] = []
    for (package, group), versions in sorted(all_versions.items()):
        if len(versions) < 2:
            continue
        target = max(versions, key=lambda value: (version_key(value), value))
        for name, _, lock in specs:
            present = registry_versions(lock).get(package, set())
            for old in sorted(present & (versions - {target}), key=lambda value: (version_key(value), value)):
                updates.append({"workspace": name, "package": package, "from": old,
                                "to": target, "compatibility": group,
                                "coupled_wasm_family": package in WASM_FAMILY})
    # GPUI alone captures the old test companion trio, so its reviewed
    # matching targets cannot be inferred from another captured lock. Select
    # them only when the same workspace plans the 0.2.129 core family.
    for name, _, lock in specs:
        versions = registry_versions(lock)
        core_target = any(item["workspace"] == name and item["package"] == "wasm-bindgen"
                          and item["to"] == "0.2.129" for item in updates)
        if not core_target:
            continue
        for package, old, target in WASM_TEST_COMPANIONS:
            if old not in versions.get(package, set()):
                continue
            updates.append({"workspace": name, "package": package,
                            "from": old, "to": target,
                            "compatibility": compatibility(old),
                            "target_origin": "reviewed crates.io exact-dependency companion",
                            "coupled_wasm_family": True})
    updates.sort(key=lambda item: (not item["coupled_wasm_family"], item["workspace"],
                                   item["package"], version_key(item["from"])))
    return updates, rejected


def enable_subreaper() -> None:
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER failed")


def group_members(pgid: int) -> list[dict]:
    proc = subprocess.run(["ps", "-eo", "pid=,ppid=,pgid=,stat="], text=True,
                          capture_output=True, check=True, timeout=3)
    members = []
    for line in proc.stdout.splitlines():
        fields = line.split()
        if len(fields) == 4 and int(fields[2]) == pgid:
            members.append({"pid": int(fields[0]), "ppid": int(fields[1]), "state": fields[3]})
    return members


def reap_children() -> None:
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def stop_group(process: subprocess.Popen) -> tuple[bool, list[dict], list[str]]:
    errors: list[str] = []

    def inspect() -> list[dict] | None:
        try:
            return group_members(process.pid)
        except Exception as exc:
            errors.append(f"owned group inspection failed: {exc}")
            return None

    for signum in (signal.SIGTERM, signal.SIGKILL):
        members = inspect()
        if members == [] and not errors:
            return True, [], []
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass
        except OSError as exc:
            errors.append(f"owned group signal failed: {exc}")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                reap_children()
            except OSError as exc:
                errors.append(f"owned child reap failed: {exc}")
            members = inspect()
            if members == [] and not errors:
                return True, [], []
            time.sleep(0.1)
    try:
        reap_children()
    except OSError as exc:
        errors.append(f"owned child reap failed: {exc}")
    members = inspect()
    return members == [] and not errors, members or [], errors


def interrupted(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt("release alignment interrupted")


def command(argv: list[str], cwd: Path, log: Path, timeout: int) -> dict:
    global ACTIVE
    start = time.monotonic()
    with log.open("wb") as stream:
        ACTIVE = subprocess.Popen(argv, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                                  start_new_session=True)
        try:
            deadline = start + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    code, timed_out, was_interrupted = 124, True, False
                    break
                try:
                    code = ACTIVE.wait(timeout=min(30, remaining))
                    timed_out, was_interrupted = False, False
                    break
                except subprocess.TimeoutExpired:
                    print(f"[{cwd.name}] alignment command still running; owned PID {ACTIVE.pid}", flush=True)
        except KeyboardInterrupt:
            code, timed_out, was_interrupted = 130, False, True
        finally:
            clean, survivors, inspection_errors = stop_group(ACTIVE)
            ACTIVE = None
        return {"argv": argv, "exit_code": code, "timeout": timed_out,
                "interrupted": was_interrupted, "cleanup_ok": clean,
                "owned_group_survivors": survivors,
                "cleanup_inspection_errors": inspection_errors,
                "duration_seconds": round(time.monotonic()-start, 2), "log": str(log)}


def write_report(path: Path, report: dict) -> None:
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / "release-compatible-locks")
    parser.add_argument("--timeout-per-update", type=int, default=300)
    args = parser.parse_args()
    if not 1 <= args.timeout_per_update <= 900:
        parser.error("timeout-per-update must be 1..900 seconds")
    root = args.root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "logs").mkdir()
    (output / "locks").mkdir()
    manifest = root / "scripts" / "release" / "sources.json"
    signal.signal(signal.SIGTERM, interrupted)
    report: dict = {"started_at": utc(), "status": "RUNNING", "release_qa": False,
                    "commands": [], "issues": [], "sources_before": {}, "sources_after": {},
                    "root_before": git(root, "rev-parse", "HEAD"),
                    "manifest_before_sha256": sha(manifest), "locks": {},
                    "package_changes": {}, "wasm_family": {}}
    report_path = output / "report.json"
    write_report(report_path, report)
    specs = lock_specs(root)
    revisions: dict[str, str] = {}
    try:
        enable_subreaper()
        _, _, revisions = read_manifest(manifest)
        for name in WORKSPACES:
            repo = root / name
            actual = git(repo, "rev-parse", "HEAD")
            dirty = git(repo, "status", "--porcelain")
            report["sources_before"][name] = {"revision": actual, "dirty": bool(dirty)}
            if actual != revisions[name] or dirty:
                report["issues"].append(f"{name}: pinned revision or clean checkout mismatch")
        for name, _, lock in specs:
            if not lock.is_file():
                report["issues"].append(f"{name}: Cargo.lock missing")
                continue
            report["locks"][name] = {"before_sha256": sha(lock)}
            report["wasm_family"][name] = {"before": family_versions(lock)}
            destination = output / "locks" / name
            destination.mkdir()
            shutil.copy2(lock, destination / "Cargo.lock.before")
        if report["issues"]:
            raise RuntimeError("source or lock preflight failed")
        proposed, rejected = plans(specs)
        report["plan"] = proposed
        report["manual_review"] = rejected
        report["wasm_batches"] = []
        if rejected:
            report["issues"].append("prerelease or invalid registry versions require manual compatibility review")
            raise RuntimeError("automatic alignment refuses unclassified prerelease compatibility")
        write_report(report_path, report)
        # js-sys/web-sys/futures/test carry exact family constraints.
        # Unlock every present family member together, without --precise: Cargo
        # does not apply separate precise targets to multiple -p selectors.
        for name, cwd, lock in specs:
            family_plan = [item for item in proposed if item["workspace"] == name
                           and item["coupled_wasm_family"]]
            if not family_plan:
                continue
            before_family = family_versions(lock)
            selected = sorted(f"{package}@{version}"
                              for package, versions in before_family.items()
                              for version in versions)
            targets = {package: sorted({item["to"] for item in family_plan
                                        if item["package"] == package})
                       for package in sorted({item["package"] for item in family_plan})}
            batch = {"workspace": name, "selected": selected, "targets": targets,
                     "before": before_family, "status": "RUNNING"}
            report["wasm_batches"].append(batch)
            argv = ["cargo", "update"]
            for spec in selected:
                argv.extend(["-p", spec])
            result = command(argv, cwd, output / "logs" / f"wasm-batch-{name}.log",
                             args.timeout_per_update)
            report["commands"].append(result)
            batch["command"] = result
            batch["after"] = family_versions(lock)
            batch["status"] = "UPDATED" if result["exit_code"] == 0 and result["cleanup_ok"] else "FAIL"
            if batch["status"] == "FAIL":
                report["issues"].append(f"{name}: coupled Wasm unlock failed; partial lock retained")
                write_report(report_path, report)
                break
            for item in family_plan:
                present = registry_versions(lock).get(item["package"], set())
                if item["from"] not in present and (item["to"] in present or not present):
                    item["status"] = "BATCH_TARGET_PRESENT" if present else "BATCH_PACKAGE_PRUNED"
                else:
                    item["status"] = "FAIL"
                    report["issues"].append(
                        f"{name}: coupled Wasm batch did not attain captured target for "
                        f"{item['package']} {item['from']} -> {item['to']}"
                    )
            write_report(report_path, report)
            if report["issues"]:
                break
        for number, item in enumerate(proposed):
            if report["issues"]:
                break
            if item.get("status", "").startswith("BATCH_"):
                continue
            name = item["workspace"]
            _, cwd, lock = next(spec for spec in specs if spec[0] == name)
            if item["from"] not in registry_versions(lock).get(item["package"], set()):
                present = registry_versions(lock).get(item["package"], set())
                if item["to"] in present:
                    item["status"] = "TARGET_ALREADY_PRESENT"
                elif not present:
                    item["status"] = "PACKAGE_PRUNED"
                else:
                    item["status"] = "FAIL"
                    report["issues"].append(
                        f"{name}: {item['package']} old version vanished but target was not selected"
                    )
                    break
                continue
            argv = ["cargo", "update", "-p", f"{item['package']}@{item['from']}",
                    "--precise", item["to"]]
            result = command(argv, cwd, output / "logs" / f"{number:03d}-{name}-{item['package']}.log",
                             args.timeout_per_update)
            report["commands"].append(result)
            item["status"] = "UPDATED" if result["exit_code"] == 0 and result["cleanup_ok"] else "FAIL"
            write_report(report_path, report)
            if item["status"] == "FAIL":
                report["issues"].append(f"{name}: {item['package']} alignment failed; partial locks retained")
                break
        if not report["issues"]:
            for name, cwd, _ in specs:
                result = command(["cargo", "metadata", "--all-features",
                                  "--format-version", "1", "--no-deps"],
                                 cwd, output / "logs" / f"metadata-{name}.log", 300)
                report["commands"].append(result)
                if result["exit_code"] != 0 or not result["cleanup_ok"]:
                    report["issues"].append(f"{name}: final locked metadata failed")
                    break
    except KeyboardInterrupt as error:
        report["issues"].append(str(error))
        report["interrupted"] = True
        if ACTIVE is not None:
            clean, survivors, inspection_errors = stop_group(ACTIVE)
            report["cleanup_ok"] = clean
            report["owned_group_survivors"] = survivors
            report["cleanup_inspection_errors"] = inspection_errors
    except Exception as error:
        report["issues"].append(str(error))
    finally:
        for name, _, lock in specs:
            try:
                if not lock.is_file():
                    raise FileNotFoundError("Cargo.lock missing")
                report["locks"].setdefault(name, {})["after_sha256"] = sha(lock)
                destination = output / "locks" / name
                destination.mkdir(exist_ok=True)
                shutil.copy2(lock, destination / "Cargo.lock")
                before_ids = package_ids(destination / "Cargo.lock.before") if (destination / "Cargo.lock.before").is_file() else set()
                after_ids = package_ids(lock)
                planned_targets = {
                    (item["package"], item["to"], CRATES_IO)
                    for item in report.get("plan", []) if item["workspace"] == name
                }
                report["package_changes"][name] = {
                    "added": sorted(after_ids - before_ids),
                    "removed": sorted(before_ids - after_ids),
                    "unexpected_additions": sorted((after_ids - before_ids) - planned_targets),
                }
                if report["package_changes"][name]["unexpected_additions"]:
                    report["issues"].append(
                        f"{name}: unexpected package/source/version additions require review"
                    )
                report["wasm_family"].setdefault(name, {})["after"] = family_versions(lock)
            except OSError as error:
                report["issues"].append(f"{name}: could not preserve partial lock: {error}")
        for item in report.get("plan", []):
            name = item["workspace"]
            lock = next(path for label, _, path in specs if label == name)
            if not lock.is_file():
                continue
            present = registry_versions(lock).get(item["package"], set())
            if item["from"] in present:
                report["issues"].append(f"{name}: {item['package']} old compatible version remains")
            elif item["to"] in present:
                item["final_observation"] = "TARGET_PRESENT"
            elif not present:
                item["final_observation"] = "PACKAGE_PRUNED"
            else:
                report["issues"].append(
                    f"{name}: {item['package']} target absent after alignment despite package remaining"
                )
        metadata_results = [result for result in report["commands"]
                            if result["argv"][:2] == ["cargo", "metadata"]]
        report["wasm_family_consistent"] = (
            len(metadata_results) == len(specs)
            and all(result["exit_code"] == 0 and result["cleanup_ok"]
                    for result in metadata_results)
            and not any(item["coupled_wasm_family"] and "final_observation" not in item
                        for item in report.get("plan", []))
        )
        for name in WORKSPACES:
            try:
                repo = root / name
                actual = git(repo, "rev-parse", "HEAD")
                changed = git(repo, "status", "--porcelain")
                report["sources_after"][name] = {"revision": actual, "status": changed}
                allowed = {"Cargo.lock"}
                if name == "autoeq":
                    allowed.add("crates/autoeq-gpui-examples/Cargo.lock")
                paths = set(git(repo, "diff", "--name-only").splitlines())
                paths |= set(git(repo, "diff", "--cached", "--name-only").splitlines())
                paths |= set(git(repo, "ls-files", "--others", "--exclude-standard").splitlines())
                if name in revisions and (actual != revisions[name] or paths - allowed):
                    report["issues"].append(f"{name}: source changed outside allowed locks")
            except (OSError, subprocess.CalledProcessError) as error:
                report["issues"].append(f"{name}: source after-state unavailable: {error}")
        try:
            report["root_after"] = git(root, "rev-parse", "HEAD")
            report["manifest_after_sha256"] = sha(manifest)
            if (report["root_after"] != report["root_before"]
                    or report["manifest_after_sha256"] != report["manifest_before_sha256"]):
                report["issues"].append("root revision or source manifest changed during alignment")
        except (OSError, subprocess.CalledProcessError) as error:
            report["issues"].append(f"root after-state unavailable: {error}")
        report["status"] = "FAIL" if report["issues"] else "PROPOSED"
        report["finished_at"] = utc()
        write_report(report_path, report)
    return 1 if report["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
