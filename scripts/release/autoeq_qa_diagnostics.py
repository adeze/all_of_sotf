#!/usr/bin/env python3
"""Run the two failing AutoEQ quality gates with complete release evidence."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

from checkout_sources import read_manifest
from qa import ROOT, source_issues, source_state

COMMANDS = (
    ("qa-unit-tests", ("cargo", "test", "-p", "autoeq-cli", "--lib", "qa_tests")),
    ("build-autoeq", ("cargo", "build", "--release", "--features", "cli", "--bin", "autoeq")),
    ("qa-ascilab-6b", ("just", "qa-ascilab-6b")),
    ("qa-beyerdynamic-dt1990pro-score2", ("just", "qa-beyerdynamic-dt1990pro-score2")),
)
EXPECTED_QA_TESTS = {
    "test_perform_qa_analysis_all_pass",
    "test_perform_qa_analysis_no_improvement",
    "test_perform_qa_analysis_no_convergence_returns_error",
    "test_perform_qa_analysis_with_nan",
    "test_display_qa_analysis",
}


def run(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=False)
    _, _, revisions = read_manifest(ROOT / "scripts/release/sources.json")
    names = list(revisions)
    before = source_state(ROOT, names, "linux")
    report = {"status": "RUNNING", "sources_before": before, "commands": []}
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    child: subprocess.Popen[str] | None = None
    stopping = False
    old_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def interrupt(_sig, _frame):
        if stopping:
            return
        raise KeyboardInterrupt

    for sig in old_handlers:
        signal.signal(sig, interrupt)
    try:
        initial_issues = source_issues(before, before, True)
        initial_issues.extend(
            f"{name}: checkout differs from pinned revision {revision}"
            for name, revision in revisions.items()
            if before[name].get("revision") != revision
        )
        if initial_issues:
            raise ValueError("; ".join(initial_issues))
        for name, command in COMMANDS:
            entry = {"name": name, "command": command}
            report["commands"].append(entry)
            report_path.write_text(json.dumps(report, indent=2) + "\n")
            printed_fail = False
            with (output / f"{name}.log").open("w") as log:
                child = subprocess.Popen(command, cwd=ROOT / "autoeq", stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, bufsize=1,
                                         start_new_session=True)
                assert child.stdout is not None
                for line in child.stdout:
                    print(line, end="", flush=True)
                    log.write(line)
                    log.flush()
                    if line.strip() == "FAIL":
                        printed_fail = True
                entry["exit_code"] = child.wait()
            entry["printed_fail"] = printed_fail
            if name == "qa-unit-tests":
                output_text = (output / f"{name}.log").read_text()
                passed = set(re.findall(r"^test \S+::(test_\w+) \.\.\. ok$", output_text, re.MULTILINE))
                entry["qa_tests_passed"] = sorted(passed)
                if not EXPECTED_QA_TESTS <= passed:
                    entry["error"] = f"missing QA regressions: {sorted(EXPECTED_QA_TESTS - passed)}"
            child = None
            report_path.write_text(json.dumps(report, indent=2) + "\n")
            if name in ("qa-unit-tests", "build-autoeq") and (entry["exit_code"] or entry.get("error")):
                break
    except BaseException as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        stopping = True
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if child.poll() is None:
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        for sig, old_handler in old_handlers.items():
            signal.signal(sig, old_handler)
        try:
            after = source_state(ROOT, names, "linux")
            report["sources_after"] = after
            report["source_issues"] = source_issues(before, after, True)
        except BaseException as error:
            report["source_issues"] = [f"final source inspection failed: {type(error).__name__}: {error}"]
        completed = {item["name"] for item in report["commands"] if "exit_code" in item}
        failures = [item["name"] for item in report["commands"]
                    if item.get("exit_code") or item.get("printed_fail") or item.get("error")]
        if completed != {name for name, _ in COMMANDS}:
            failures.append("incomplete-command-set")
        if report["source_issues"]:
            failures.append("source-or-lock-drift")
        if "error" in report:
            failures.append("runner-error")
        report["failures"] = failures
        report["status"] = "FAIL" if failures else "PASS"
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(run(Path(sys.argv[1]).resolve()))
