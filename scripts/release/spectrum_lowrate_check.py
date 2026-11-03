#!/usr/bin/env python3
"""Check Spectrum Analyzer low-rate host and native CLAP behavior on Gitea."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import ctypes

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.buildbot.ci_matrix import workspace_map
from scripts.release.clap_activation_matrix_check import RATES, run_case
from scripts.release.qa import ROOT, source_issues, source_state

HOST_TESTS = {
    "initialize_rejects_zero_rate_and_minimum_above_nyquist",
    "requested_maximum_survives_low_rate_and_frequency_labels_follow_reinitialization",
    "low_rate_tone_uses_effective_bins_without_old_rate_history",
}


def snapshot() -> dict:
    return source_state(ROOT, list(workspace_map()), "linux")


def enable_subreaper() -> None:
    # Cargo may exit before a compiler process. Adopt and reap only descendants
    # of this runner so a zombie cannot make its owned process group look live.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER failed")


def group_members(pgid: int) -> list[dict]:
    process = subprocess.run(["ps", "-eo", "pid=,ppid=,pgid=,stat="],
                             text=True, capture_output=True, check=True, timeout=3)
    members = []
    for line in process.stdout.splitlines():
        fields = line.split()
        if len(fields) == 4 and int(fields[2]) == pgid:
            members.append({"pid": int(fields[0]), "ppid": int(fields[1]),
                            "pgid": pgid, "state": fields[3]})
    return members


def reap_children() -> None:
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def stop_group(child: subprocess.Popen) -> tuple[bool, list[dict], list[str]]:
    inspection_errors: list[str] = []

    def inspect() -> list[dict] | None:
        try:
            return group_members(child.pid)
        except Exception as error:
            inspection_errors.append(f"owned group inspection failed: {error}")
            return None

    for signum in (signal.SIGTERM, signal.SIGKILL):
        members = inspect()
        if members == [] and not inspection_errors:
            return True, [], []
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass
        except OSError as error:
            inspection_errors.append(f"owned group signal failed: {error}")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                reap_children()
            except OSError as error:
                inspection_errors.append(f"owned child reap failed: {error}")
            members = inspect()
            if members == [] and not inspection_errors:
                return True, [], []
            time.sleep(0.1)
    try:
        reap_children()
    except OSError as error:
        inspection_errors.append(f"owned child reap failed: {error}")
    members = inspect()
    return members == [] and not inspection_errors, members or [], inspection_errors


def run_host(command: list[str], log: Path) -> tuple[int, bool, list[dict], list[str]]:
    with log.open("w", encoding="utf-8") as stream:
        child = subprocess.Popen(command, cwd=ROOT / "sotf-daw", stdout=stream,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = child.wait(timeout=1200)
        except subprocess.TimeoutExpired:
            code = 124
        finally:
            cleanup_ok, survivors, inspection_errors = stop_group(child)
        return code, cleanup_ok, survivors, inspection_errors


def main() -> int:
    def interrupted(signum: int, _frame: object) -> None:
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    if len(sys.argv) != 2:
        print("usage: spectrum_lowrate_check.py EVIDENCE_DIR", file=sys.stderr)
        return 2
    evidence = ROOT / sys.argv[1]
    (evidence / "logs").mkdir(parents=True, exist_ok=False)
    (evidence / "sources.json").write_bytes((ROOT / "scripts/release/sources.json").read_bytes())
    (evidence / "root-revision.txt").write_text(
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True)
    )
    (evidence / "toolchain.txt").write_text(
        subprocess.check_output(["rustc", "--version"], text=True)
        + subprocess.check_output(["cargo", "--version"], text=True)
    )
    before = snapshot()
    (evidence / "sources-before.json").write_text(json.dumps(before, indent=2) + "\n")
    results: dict = {}
    errors: list[str] = []
    try:
        enable_subreaper()
        errors.extend(source_issues(before, before, require_clean=True))
        if errors:
            raise RuntimeError("pinned source checkout is dirty")
        host_command = ["cargo", "test", "-p", "sotf-host", "--lib",
                        "analyzer_spectrum::tests::", "--", "--show-output", "--test-threads=1"]
        host_log = evidence / "logs" / "sotf-host-analyzer.log"
        host_exit, host_cleanup, host_survivors, host_inspection_errors = run_host(
            host_command, host_log
        )
        if host_exit == 124:
            errors.append("sotf-host analyzer tests timed out")
        output = host_log.read_text(encoding="utf-8", errors="replace")
        passed = set(re.findall(r"^test ([^\n ]+) \.\.\. ok$", output, flags=re.MULTILINE))
        named = {test for test in HOST_TESTS if any(item.endswith("::" + test) for item in passed)}
        results["host"] = {"command": host_command, "exit_code": host_exit,
                           "cleanup_ok": host_cleanup,
                           "owned_group_survivors": host_survivors,
                           "cleanup_inspection_errors": host_inspection_errors,
                           "named_passed": sorted(named), "positive_count": len(passed),
                           "log": str(host_log.relative_to(ROOT))}
        if host_exit or not host_cleanup or named != HOST_TESTS or not passed:
            errors.append("sotf-host analyzer tests or three named low-rate cases failed")
        native = run_case("spectrum-analyzer", "spectrum_analyzer_activation_matrix",
                          evidence / "logs" / "spectrum-native-clap.log")
        results["native_clap"] = native
        if (native["exit_code"] != 0 or native["test_result"] != "ok"
                or not native["exact_rates"] or not native["positive_control"]
                or tuple(row["rate"] for row in native["outcomes"]) != RATES
                or not all(row["initialized"] and row["activated"] for row in native["outcomes"])):
            errors.append("Spectrum native CLAP activation did not pass all 13 rates")
    except KeyboardInterrupt as error:
        errors.append(f"interrupted: {error}")
    except Exception as error:
        errors.append(f"runner error: {error}")
    finally:
        try:
            after = snapshot()
            (evidence / "sources-after.json").write_text(json.dumps(after, indent=2) + "\n")
            errors.extend(source_issues(before, after, require_clean=True))
        except Exception as error:
            errors.append(f"source after-state unavailable: {error}")
        report = {"status": "PASS" if not errors else "FAIL", "results": results,
                  "errors": errors, "required_host_tests": sorted(HOST_TESTS), "rates": RATES}
        (evidence / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
