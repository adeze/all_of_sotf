#!/usr/bin/env python3
"""Bounded macOS runner compilation probe; this is not full release QA."""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.buildbot.ci_matrix import workspace_map
from scripts.release.checkout_sources import read_manifest
from scripts.release.qa import ROOT, source_issues, source_state

COMMAND = ["cargo", "check", "-p", "sotf-engine", "--all-targets"]
LIMIT_SECONDS = 1800
HEARTBEAT_SECONDS = 10


def snapshot() -> dict:
    return source_state(ROOT, list(workspace_map()), "macos")


def stop_group(child: subprocess.Popen) -> bool:
    for signum in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            child.wait(timeout=1)
            return True
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            continue
        try:
            os.killpg(child.pid, 0)
        except ProcessLookupError:
            return True
    return False


def own_process_state(pid: int) -> dict:
    result = subprocess.run(["ps", "-p", str(pid), "-o", "rss=,stat="],
                            text=True, capture_output=True, timeout=3, check=False)
    fields = result.stdout.split()
    return {"pid": pid, "rss_kib": int(fields[0]), "state": fields[1]} if len(fields) == 2 else {"pid": pid, "state": "exited"}


def main() -> int:
    def interrupted(signum: int, _frame: object) -> None:
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    if len(sys.argv) != 2:
        print("usage: macos_compile_probe.py EVIDENCE_DIR", file=sys.stderr)
        return 2
    evidence = ROOT / sys.argv[1]
    evidence.mkdir(parents=True, exist_ok=False)
    before = snapshot()
    (evidence / "sources-before.json").write_text(json.dumps(before, indent=2) + "\n")
    report: dict = {"scope": "one bounded locked compile; no full build readiness verdict",
                    "status": "RUNNING", "command": COMMAND, "heartbeats": [],
                    "source_issues": [], "errors": []}
    (evidence / "sources.json").write_bytes((ROOT / "scripts/release/sources.json").read_bytes())
    try:
        if platform.system() != "Darwin":
            raise RuntimeError("macOS probe dispatched on non-Darwin host")
        report["root_revision"] = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                                            cwd=ROOT, text=True).strip()
        report["rustc"] = subprocess.check_output(["rustc", "--version"], text=True).strip()
        report["cargo"] = subprocess.check_output(["cargo", "--version"], text=True).strip()
        if report["rustc"].split()[1] != "1.99.0":
            raise RuntimeError("runner Rust differs from pinned 1.99.0")
        report["host"] = {"os": platform.platform(),
                          "disk_free_bytes": shutil.disk_usage(ROOT).free,
                          "physical_memory_bytes": int(subprocess.check_output(
                              ["sysctl", "-n", "hw.memsize"], text=True).strip())}
        _, _, pinned_revisions = read_manifest(ROOT / "scripts/release/sources.json")
        for name, revision in pinned_revisions.items():
            if before.get(name, {}).get("revision") != revision:
                report["source_issues"].append(f"{name}: checkout revision differs from source manifest")
        report["source_issues"].extend(source_issues(before, before, require_clean=True))
        if report["source_issues"]:
            raise RuntimeError("source checkout is dirty or mismatched before compile")
        log_path = evidence / "cargo-check.log"
        with log_path.open("w", encoding="utf-8") as log:
            child = subprocess.Popen(COMMAND, cwd=ROOT / "sotf-daw", stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
            start = time.monotonic()
            try:
                while child.poll() is None:
                    elapsed = round(time.monotonic() - start, 2)
                    if elapsed >= LIMIT_SECONDS:
                        report["timed_out"] = True
                        break
                    state = own_process_state(child.pid)
                    state["elapsed_seconds"] = elapsed
                    report["heartbeats"].append(state)
                    print(f"macOS compile heartbeat {state}", flush=True)
                    try:
                        child.wait(timeout=HEARTBEAT_SECONDS)
                    except subprocess.TimeoutExpired:
                        pass
                report["exit_code"] = child.poll()
            finally:
                report["cleanup_ok"] = stop_group(child)
                report["duration_seconds"] = round(time.monotonic() - start, 2)
        if report.get("timed_out") or report["exit_code"] != 0 or not report["cleanup_ok"]:
            report["errors"].append("bounded sotf-engine all-targets compile failed or child group survived")
            print("\n".join(log_path.read_text(errors="replace").splitlines()[-60:]), flush=True)
    except KeyboardInterrupt as error:
        report["errors"].append(f"interrupted: {error}")
    except Exception as error:
        report["errors"].append(f"probe error: {error}")
    finally:
        try:
            after = snapshot()
            (evidence / "sources-after.json").write_text(json.dumps(after, indent=2) + "\n")
            report["source_issues"].extend(source_issues(before, after, require_clean=True))
        except Exception as error:
            report["source_issues"].append(f"after-state unavailable: {error}")
        report["status"] = "PASS" if not report["errors"] and not report["source_issues"] else "FAIL"
        (evidence / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
