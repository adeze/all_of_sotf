#!/usr/bin/env python3
"""Run one complete AutoEQ QA shard and verify the four coverage shards."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET

from checkout_sources import read_manifest
from qa import ROOT, source_issues, source_state

MODES = ("iir", "fir", "mixed", "mixed_phase")
EXTRAS = {
    "quality": ("cargo", "run", "--features", "qa", "--bin", "roomeq-qa-quality", "--release", "--", "--jobs", "1"),
    "generated": ("cargo", "test", "--release", "-p", "autoeq", "--features", "qa", "--test", "roomeq_generated_data_test", "--", "--ignored", "--test-threads=1"),
    "synthetic": ("cargo", "run", "--features", "qa", "--bin", "roomeq-qa-synthetic", "--no-default-features", "--release", "--", "--full-matrix"),
    "features": ("cargo", "run", "--features", "qa", "--bin", "roomeq-qa-features", "--no-default-features", "--release"),
    "acoustic": ("cargo", "run", "--features", "qa", "--bin", "roomeq-qa-acoustic", "--release", "--", "--tier", "nightly"),
    "fuzzer": ("cargo", "run", "--bin", "roomeq-fuzzer", "--release", "--features=qa,plotly", "--", "-n", "1000", "--seed", "42", "--skip-kautz-modal"),
}
LANES = ("autoeq", *(f"coverage-{mode}" for mode in MODES), *EXTRAS, "export")


def expected_cases(registry: dict) -> dict[str, set[str]]:
    expected = {mode: set() for mode in MODES}
    for family in registry["families"]:
        for mode in family["modes"]:
            expected[mode].add(f'{family["scenario"]} {family["solver"]} {mode}')
    for case in registry["home_cinema"]:
        expected[case["mode"]].add(case["id"].replace("_", " "))
    if [len(expected[mode]) for mode in MODES] != [26, 22, 22, 21]:
        raise ValueError("weekly registry partition changed; review the shard inventory")
    return expected


def junit_cases(path: Path) -> set[str]:
    suite = ET.parse(path).getroot()
    cases = suite.findall("testcase")
    if int(suite.attrib["tests"]) != len(cases):
        raise ValueError(f"JUnit case count mismatch: {path}")
    if any(int(suite.attrib.get(key, "0")) for key in ("failures", "errors", "skipped")):
        raise ValueError(f"RoomEQ coverage failures or skips: {path}")
    if any(case.find(kind) is not None for case in cases for kind in ("failure", "error", "skipped")):
        raise ValueError(f"RoomEQ coverage failure or skipped testcase: {path}")
    names = [case.attrib["name"] for case in cases]
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate RoomEQ coverage case: {path}")
    return set(names)


def source_signature(states: dict) -> dict:
    return {name: (item.get("revision"), item.get("lock_sha256")) for name, item in states.items()}


def run_lane(lane: str, output: Path) -> int:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    before = source_state(ROOT, list(source_state_names()), "linux")
    report = {"lane": lane, "status": "RUNNING", "sources_before": before}
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    previous_handlers = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
    child: subprocess.Popen | None = None
    failed_output = False
    stopping = False

    def interrupt(_signum, _frame):
        if stopping:
            return
        if child is not None and child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        raise KeyboardInterrupt

    for signum in previous_handlers:
        signal.signal(signum, interrupt)
    try:
        issues = source_issues(before, before, True)
        if issues:
            raise ValueError("; ".join(issues))
        if lane.startswith("coverage-"):
            mode = lane.removeprefix("coverage-")
            command = ("cargo", "run", "--features", "qa", "--bin", "roomeq-qa-coverage", "--release", "--", "--tier", "weekly", "--mode", mode, "--jobs", "1", "--junit", str(output / "coverage.xml"))
        elif lane in EXTRAS:
            command = EXTRAS[lane]
        else:
            recipe = {"autoeq": "qa-autoeq-all", "export": "qa-export-all"}[lane]
            command = ("just", recipe)
        report["command"] = command
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        with (output / "command.log").open("w") as log:
            child = subprocess.Popen(command, cwd=ROOT / "autoeq", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
            assert child.stdout is not None
            for line in child.stdout:
                print(line, end="", flush=True)
                log.write(line)
                log.flush()
                if lane == "autoeq" and line.strip() == "FAIL":
                    failed_output = True
            code = child.wait()
        report["exit_code"] = code
        if code or failed_output:
            raise ValueError(f"QA command failed (exit={code}, printed_FAIL={failed_output})")
        if lane.startswith("coverage-"):
            registry = json.loads((ROOT / "autoeq/crates/roomeq-qa/src/registry.json").read_text())
            wanted = expected_cases(registry)[mode]
            actual = junit_cases(output / "coverage.xml")
            if actual != wanted:
                raise ValueError(f"coverage case mismatch: missing={sorted(wanted - actual)}, extra={sorted(actual - wanted)}")
            report["coverage_cases"] = sorted(actual)
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
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        try:
            after = source_state(ROOT, list(source_state_names()), "linux")
            report["sources_after"] = after
            report["source_issues"] = source_issues(before, after, True)
        except BaseException as error:
            report["source_issues"] = [f"final source snapshot failed: {type(error).__name__}: {error}"]
        report["status"] = "FAIL" if report.get("error") or report["source_issues"] else "PASS"
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["status"] == "PASS" else 1


def source_state_names():
    from qa import workspace_map

    return workspace_map()


def verify(download: Path) -> int:
    current = source_state(ROOT, list(source_state_names()), "linux")
    issues = source_issues(current, current, True)
    if issues:
        raise ValueError(f"verifier source checkout invalid: {issues}")
    _, _, revisions = read_manifest(ROOT / "scripts/release/sources.json")
    if {name: item["revision"] for name, item in current.items()} != revisions:
        raise ValueError("verifier checkout does not match pinned source manifest")
    reports = {}
    for lane in LANES:
        matches = list(download.glob(f"**/autoeq-shard-{lane}/report.json"))
        if len(matches) != 1:
            raise ValueError(f"expected one report for {lane}, got {len(matches)}")
        reports[lane] = json.loads(matches[0].read_text())
        if reports[lane]["lane"] != lane:
            raise ValueError(f"wrong QA lane in {matches[0]}")
    pinned = source_signature(current)
    for lane, item in reports.items():
        if source_signature(item["sources_before"]) != pinned or source_signature(item["sources_after"]) != pinned:
            raise ValueError(f"{lane}: source revisions or locks differ from pinned verifier checkout")
        if source_issues(item["sources_before"], item["sources_after"], True):
            raise ValueError(f"{lane}: source changed during QA")
    if any(item["status"] != "PASS" for item in reports.values()):
        raise ValueError("one or more required QA shards failed")
    registry = json.loads((ROOT / "autoeq/crates/roomeq-qa/src/registry.json").read_text())
    expected = expected_cases(registry)
    actual = [name for mode in MODES for name in reports[f"coverage-{mode}"]["coverage_cases"]]
    wanted = set().union(*expected.values())
    if len(actual) != 91 or len(set(actual)) != 91 or set(actual) != wanted:
        raise ValueError("RoomEQ weekly coverage did not run all 91 distinct cases")
    print("PASS: all 12 AutoEQ QA shards and all 91 weekly RoomEQ cases", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--lane", choices=LANES)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        if args.lane is None:
            parser.error("--lane is required for run")
        return run_lane(args.lane, args.output)
    return verify(args.output)


if __name__ == "__main__":
    sys.exit(main())
