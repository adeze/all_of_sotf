#!/usr/bin/env python3
"""Run the pinned CLAP parameter-text ABI regressions on Gitea."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.buildbot.ci_matrix import workspace_map
from scripts.release.qa import ROOT, source_issues, source_state


CASES = {
    "aec": "aec_step_size_text_is_stable",
    "crossfeed": "crossfeed_frequency_text_is_stable",
    "upmixer": "upmixer_lfo_rate_text_is_stable",
}
CONTROLS = (
    "clap_unit_and_custom_formatter_roundtrip_through_extension",
    "clap_noncanonical_text_and_stepped_params_keep_parser_values",
)
TEST_PREFIX = "clap_param_text_roundtrip_tests::"
TIMEOUT_SECONDS = 3600


def snapshot() -> dict[str, object]:
    return source_state(ROOT, list(workspace_map()), "linux")


def run_case(feature: str, log_path: Path) -> dict[str, object]:
    command = [
        "cargo", "test", "-p", "plugins-nih", "--lib",
        "--no-default-features", "--features", feature,
        "clap_param_text_roundtrip_tests", "--", "--test-threads=1",
    ]
    print(f"[{feature}] {' '.join(command)}", flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        child = subprocess.Popen(
            command,
            cwd=ROOT / "sotf-daw",
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        interrupted = False
        try:
            code = child.wait(timeout=TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            code = 124
            print(f"[{feature}] timed out", flush=True)
        except KeyboardInterrupt:
            code = 130
            interrupted = True
            print(f"[{feature}] interrupted", flush=True)
        finally:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print(f"[{feature}] owned process did not reap after SIGKILL", flush=True)
                code = 1
        if interrupted:
            raise KeyboardInterrupt(f"{feature} interrupted after owned process cleanup")

    output = log_path.read_text(encoding="utf-8", errors="replace")
    observed = {
        name: bool(
            re.search(
                rf"^test .*{re.escape(TEST_PREFIX + name)} \.\.\. (ok|FAILED)$",
                output,
                flags=re.MULTILINE,
            )
        )
        for name in (*CONTROLS, CASES[feature])
    }
    passed = {
        name: bool(
            re.search(
                rf"^test .*{re.escape(TEST_PREFIX + name)} \.\.\. ok$",
                output,
                flags=re.MULTILINE,
            )
        )
        for name in observed
    }
    result = {
        "feature": feature,
        "command": command,
        "exit_code": code,
        "observed": observed,
        "passed": passed,
        "log": str(log_path.relative_to(ROOT)),
    }
    print(f"[{feature}] exit={code}; observed={observed}; passed={passed}", flush=True)
    if code or not all(observed.values()):
        print("\n".join(output.splitlines()[-80:]), flush=True)
    return result


def main() -> int:
    def interrupt(signum: int, _frame: object) -> None:
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    if len(sys.argv) != 3 or sys.argv[1] not in {"baseline", "fixed"}:
        print("usage: clap_param_text_check.py baseline|fixed EVIDENCE_DIR", file=sys.stderr)
        return 2
    mode = sys.argv[1]
    evidence = ROOT / sys.argv[2]
    (evidence / "logs").mkdir(parents=True, exist_ok=True)
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
    results: list[dict[str, object]] = []
    interrupted = False
    try:
        if source_issues(before, before, require_clean=True):
            raise RuntimeError("pinned source checkout is already dirty")
        for feature in CASES:
            results.append(run_case(feature, evidence / "logs" / f"{feature}.log"))
    except (KeyboardInterrupt, subprocess.TimeoutExpired) as error:
        interrupted = True
        print(f"interrupted: {error}", file=sys.stderr, flush=True)
    except Exception as error:
        print(f"runner error: {error}", file=sys.stderr, flush=True)
        interrupted = True
    finally:
        try:
            after = snapshot()
            (evidence / "sources-after.json").write_text(json.dumps(after, indent=2) + "\n")
            issues = source_issues(before, after, require_clean=True)
        except Exception as error:
            interrupted = True
            issues = [f"source guard failed: {error}"]
            print(issues[0], file=sys.stderr, flush=True)
        (evidence / "source-issues.json").write_text(json.dumps(issues, indent=2) + "\n")

    complete = len(results) == len(CASES) and not interrupted and not issues
    controls_pass = complete and all(
        all(result["passed"][name] for name in CONTROLS) for result in results
    )
    abi_pass = complete and all(
        result["exit_code"] == 0 and result["passed"][CASES[result["feature"]]]
        for result in results
    )
    abi_expected_fail = complete and controls_pass and all(
        result["exit_code"] != 0
        and result["observed"][CASES[result["feature"]]]
        and not result["passed"][CASES[result["feature"]]]
        for result in results
    )
    verdict = (
        "PASS" if mode == "fixed" and controls_pass and abi_pass
        else "EXPECTED_RED" if mode == "baseline" and abi_expected_fail
        else "FAIL"
    )
    report = {
        "mode": mode,
        "verdict": verdict,
        "results": results,
        "source_issues": issues,
        "interrupted": interrupted,
    }
    (evidence / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"CLAP parameter-text verdict: {verdict}", flush=True)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
