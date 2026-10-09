#!/usr/bin/env python3
"""Resolve nine pinned workspaces on Gitea and preserve lockfile evidence.

This is a preparation phase, not a release QA pass. Its Cargo.lock outputs
must be reviewed and committed before the --locked QA workflow is dispatched.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import tomllib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from workspaces import workspace_names

from checkout_sources import read_manifest
from qa import demo_metadata_issues

ORDER = (
    "math-audio", "gpui-toolkit", "sofa-reader", "symphonia-add-ons",
    "autoeq", "sotf-daw", "sotf-capture", "sotf", "sotf-systemwide",
)
AUTOEQ_DEMO = Path("crates/autoeq-gpui-examples")

# No targeted updates remain. The Vello/GPUI GPU stack previously needed an
# explicit downgrade to the patched Zed fork (wgpu/naga 29.0.4 -> 29.0.3)
# because plain metadata refreshes retained older crates.io lock entries.
# The collection crates now publish under renamed sotf-* package names and
# consumers resolve them through direct path dependencies instead of
# [patch] tables, so there is no crates.io entry left to downgrade.
TARGETED_UPDATES: dict[str, tuple] = {}


def git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(workspace), *args], check=True,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def changed_paths(workspace: Path) -> list[str]:
    modified = git(workspace, "diff", "--name-only").splitlines()
    staged = git(workspace, "diff", "--cached", "--name-only").splitlines()
    untracked = git(workspace, "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted(set(modified + staged + untracked))


def digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def has_registry_package(lock: Path, name: str, version: str) -> bool:
    if not lock.is_file():
        return False
    packages = tomllib.loads(lock.read_text(encoding="utf-8")).get("package", [])
    return any(
        package.get("name") == name
        and package.get("version") == version
        and package.get("source", "").startswith("registry+")
        for package in packages
    )


def write_report(path: Path, report: dict) -> None:
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    pending.replace(path)


class ResolutionInterrupted(KeyboardInterrupt):
    def __init__(self, result: dict):
        super().__init__("resolution interrupted")
        self.result = result


def process_group_alive(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


def stop_process_group(process: subprocess.Popen) -> bool:
    """Reap the leader and stop any remaining Cargo descendants within ten seconds."""
    for kind in (signal.SIGTERM, signal.SIGKILL):
        process.poll()
        if not process_group_alive(process.pid):
            return True
        try:
            os.killpg(process.pid, kind)
        except ProcessLookupError:
            process.poll()
            return True
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            process.poll()
            if not process_group_alive(process.pid):
                return True
            time.sleep(0.1)
    process.poll()
    return not process_group_alive(process.pid)


def interrupted(_signum, _frame):
    raise KeyboardInterrupt("SIGTERM")


def resolve_one(name: str, revision: str, root: Path, output: Path) -> dict:
    workspace = root / name
    result: dict = {"workspace": name, "revision": revision, "status": "FAIL"}
    if not (workspace / "Cargo.toml").is_file():
        result["error"] = "workspace Cargo.toml missing"
        return result
    actual = git(workspace, "rev-parse", "HEAD")
    result["actual_revision"] = actual
    if actual != revision:
        result["error"] = "checkout does not match pinned revision"
        return result
    if changed_paths(workspace):
        result["error"] = "checkout was dirty before resolution"
        return result

    lock = workspace / "Cargo.lock"
    result["lock_before_sha256"] = digest(lock)
    lock_dir = output / "locks" / name
    lock_dir.mkdir(parents=True, exist_ok=True)
    if lock.is_file():
        shutil.copy2(lock, lock_dir / "Cargo.lock.before")

    metadata = output / "metadata" / f"{name}.json"
    log = output / "logs" / f"{name}.log"
    start = time.monotonic()
    print(f"[{name}] resolving {revision}", flush=True)
    was_interrupted = False
    result["targeted_updates"] = []
    update_failure = False
    try:
        for package, before, after in TARGETED_UPDATES.get(name, ()):
            command = ["cargo", "update", "-p", f"{package}@{before}", "--precise", after]
            update_log = output / "logs" / f"{name}-update-{package}.log"
            update = {"argv": command, "log_file": str(update_log.relative_to(output))}
            result["targeted_updates"].append(update)
            if not has_registry_package(lock, package, before):
                update["status"] = "SKIPPED"
                update["reason"] = f"registry {package}@{before} absent from lockfile"
                continue
            print(f"[{name}] {' '.join(command)}", flush=True)
            update["lock_before_sha256"] = digest(lock)
            with update_log.open("w", encoding="utf-8") as stdout:
                try:
                    process = subprocess.Popen(
                        command, cwd=workspace, stdout=stdout, stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    try:
                        update["exit_code"] = process.wait()
                    except KeyboardInterrupt:
                        stop_process_group(process)
                        update["exit_code"] = process.returncode
                        was_interrupted = True
                except OSError as error:
                    stdout.write(str(error) + "\n")
                    update["exit_code"] = 127
            update["lock_after_sha256"] = digest(lock)
            update["registry_old_remaining"] = has_registry_package(lock, package, before)
            update["status"] = "INTERRUPTED" if was_interrupted else (
                "PASS" if update["exit_code"] == 0 and not update["registry_old_remaining"] else "FAIL"
            )
            if update["status"] != "PASS":
                result["exit_code"] = update["exit_code"]
                update_failure = True
                break
    except (OSError, tomllib.TOMLDecodeError) as error:
        result["error"] = f"cannot inspect lockfile for targeted update: {error}"
        update_failure = True

    if not update_failure:
        with metadata.open("w", encoding="utf-8") as stdout, log.open("w", encoding="utf-8") as stderr:
            try:
                process = subprocess.Popen(
                    ["cargo", "metadata", "--format-version", "1"],
                    cwd=workspace, stdout=stdout, stderr=stderr,
                    start_new_session=True,
                )
                try:
                    result["exit_code"] = process.wait()
                except KeyboardInterrupt:
                    stop_process_group(process)
                    result["exit_code"] = process.returncode
                    was_interrupted = True
            except OSError as error:
                stderr.write(str(error) + "\n")
                result["exit_code"] = 127
    if name == "autoeq" and not update_failure and result.get("exit_code") == 0 and not was_interrupted:
        nested = workspace / AUTOEQ_DEMO
        nested_lock = nested / "Cargo.lock"
        nested_result = {
            "workspace": "autoeq-gpui-examples",
            "lock_before_sha256": digest(nested_lock),
            "metadata_file": "metadata/autoeq-gpui-examples.json",
            "log_file": "logs/autoeq-gpui-examples.log",
        }
        result["nested_workspace"] = nested_result
        nested_lock_dir = output / "locks" / "autoeq-gpui-examples"
        nested_lock_dir.mkdir(parents=True, exist_ok=True)
        if nested_lock.is_file():
            shutil.copy2(nested_lock, nested_lock_dir / "Cargo.lock.before")
        if not (nested / "Cargo.toml").is_file() or not nested_lock.is_file():
            nested_result["error"] = "nested demo manifest or Cargo.lock missing"
            nested_result["exit_code"] = 1
        else:
            print("[autoeq-gpui-examples] resolving nested workspace", flush=True)
            nested_metadata = output / nested_result["metadata_file"]
            nested_log = output / nested_result["log_file"]
            with nested_metadata.open("w", encoding="utf-8") as stdout, nested_log.open("w", encoding="utf-8") as stderr:
                try:
                    process = subprocess.Popen(
                        ["cargo", "metadata", "--format-version", "1", "--all-features"],
                        cwd=nested, stdout=stdout, stderr=stderr, start_new_session=True,
                    )
                    try:
                        nested_result["exit_code"] = process.wait(timeout=900)
                    except subprocess.TimeoutExpired:
                        nested_result["exit_code"] = 124
                        nested_result["error"] = "nested cargo metadata timed out after 900 seconds"
                    except KeyboardInterrupt:
                        nested_result["exit_code"] = process.returncode or 130
                        was_interrupted = True
                    finally:
                        if not stop_process_group(process):
                            nested_result["exit_code"] = 1
                            nested_result["error"] = "nested cargo process group survived SIGKILL"
                except OSError as error:
                    stderr.write(str(error) + "\n")
                    nested_result["exit_code"] = 127
            if nested_result["exit_code"] == 0:
                try:
                    json.loads(nested_metadata.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    nested_result["error"] = f"invalid nested cargo metadata: {error}"
                    nested_result["exit_code"] = 1
                else:
                    inventory_issues = demo_metadata_issues(nested_metadata)
                    if inventory_issues:
                        nested_result["error"] = "; ".join(inventory_issues)
                        nested_result["exit_code"] = 1
        nested_result["lock_after_sha256"] = digest(nested_lock)
        if nested_lock.is_file():
            shutil.copy2(nested_lock, nested_lock_dir / "Cargo.lock")
        nested_result["status"] = "RESOLVED" if nested_result["exit_code"] == 0 else "FAIL"
        if nested_result["status"] != "RESOLVED":
            result["exit_code"] = nested_result["exit_code"]
    result["duration_seconds"] = round(time.monotonic() - start, 3)
    if metadata.is_file():
        result["metadata_file"] = str(metadata.relative_to(output))
        result["log_file"] = str(log.relative_to(output))
    result["lock_after_sha256"] = digest(lock)
    result["lock_changed"] = result["lock_before_sha256"] != result["lock_after_sha256"]
    if lock.is_file():
        shutil.copy2(lock, lock_dir / "Cargo.lock")
    result["changed_paths"] = changed_paths(workspace)

    if was_interrupted:
        result["status"] = "INTERRUPTED"
        result["error"] = "resolution interrupted; inspect log and partial lockfile"
        raise ResolutionInterrupted(result)

    if update_failure:
        result.setdefault("error", "targeted cargo update failed; inspect its log and partial lockfile")
    elif result["exit_code"]:
        result["error"] = "cargo metadata failed; inspect log and partial lockfile"
    elif git(workspace, "rev-parse", "HEAD") != revision:
        result["error"] = "source revision changed during resolution"
    elif set(result["changed_paths"]) - ({"Cargo.lock", str(AUTOEQ_DEMO / "Cargo.lock")}
                                         if name == "autoeq" else {"Cargo.lock"}):
        result["error"] = "resolution modified source files beyond Cargo.lock"
    else:
        try:
            json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            result["error"] = f"invalid cargo metadata output: {error}"
        else:
            result["status"] = "RESOLVED"
    return result


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "scripts" / "release" / "sources.json")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / "release-resolution")
    args = parser.parse_args(argv)
    if set(ORDER) != set(workspace_names()):
        parser.error("resolution order and workspace inventory differ")
    _, _, revisions = read_manifest(args.manifest)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "metadata").mkdir()
    (output / "logs").mkdir()
    shutil.copy2(args.manifest, output / "sources.json")
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "RUNNING", "release_qa": False,
        "required_workspaces": list(ORDER), "workspaces": [],
    }
    report_path = output / "report.json"
    write_report(report_path, report)
    try:
        for name in ORDER:
            report["active_workspace"] = name
            write_report(report_path, report)
            try:
                result = resolve_one(name, revisions[name], args.root.resolve(), output)
            except (OSError, subprocess.CalledProcessError) as error:
                result = {"workspace": name, "revision": revisions[name], "status": "FAIL", "error": str(error)}
            report["workspaces"].append(result)
            report.pop("active_workspace", None)
            write_report(report_path, report)
    except ResolutionInterrupted as error:
        report["workspaces"].append(error.result)
        report.pop("active_workspace", None)
        report["interrupted"] = True
        report["error"] = str(error)
    except KeyboardInterrupt as error:
        report["interrupted"] = True
        report["error"] = str(error) or "resolution interrupted"
    finally:
        report["status"] = "FAIL" if report.get("interrupted") or len(report["workspaces"]) != len(ORDER) or any(
            item["status"] != "RESOLVED" for item in report["workspaces"]
        ) else "RESOLVED"
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_report(report_path, report)
    print(f"{report['status']}: {report_path}", flush=True)
    return 0 if report["status"] == "RESOLVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
