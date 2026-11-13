#!/usr/bin/env python3
"""Record the pinned native CLAP activation stress matrix on Gitea."""

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
    "band-split": "band_split_activation_matrix",
    "crossfeed": "crossfeed_activation_matrix",
    "crossover": "crossover_activation_matrix",
    "de-esser": "de_esser_activation_matrix",
    "downmix": "downmix_activation_matrix",
    "dynamic-eq": "dynamic_eq_activation_matrix",
    "eq": "eq_activation_matrix",
    "hiss-reducer": "hiss_reducer_activation_matrix",
    "linear-phase-eq": "linear_phase_eq_activation_matrix",
    "mono-to-stereo": "mono_to_stereo_activation_matrix",
    "multiband-compressor": "multiband_compressor_activation_matrix",
    "saturation": "saturation_activation_matrix",
    "spectrum-analyzer": "spectrum_analyzer_activation_matrix",
    "speech-denoiser": "speech_denoiser_activation_matrix",
    "stereo-imager": "stereo_imager_activation_matrix",
    "upmixer": "upmixer_activation_matrix",
}
RATES = (
    8000.0, 22050.0, 44100.0, 48000.0, 88200.0, 96000.0,
    192000.0, 384000.0, 768000.0, 1234.5678, 12345.678,
    45678.901, 123456.78,
)
TEST_PREFIX = "clap_activation_matrix_tests::"
TIMEOUT_SECONDS = 2400


def snapshot() -> dict[str, object]:
    return source_state(ROOT, list(workspace_map()), "linux")


def run_case(feature: str, test_name: str, log_path: Path) -> dict[str, object]:
    command = [
        "cargo", "test", "-p", "plugins-nih", "--lib",
        "--no-default-features", "--features", feature,
        TEST_PREFIX + test_name, "--", "--exact", "--show-output", "--test-threads=1",
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
    test_result = re.search(
        rf"^test {re.escape(TEST_PREFIX + test_name)} \.\.\. (ok|FAILED)$",
        output,
        flags=re.MULTILINE,
    )
    matrix = re.search(
        r"^Sotf\w+ pinned-validator CLAP activation matrix: (.+)$",
        output,
        flags=re.MULTILINE,
    )
    outcomes = []
    if matrix:
        outcomes = [
            {"rate": float(rate), "initialized": initialized == "true", "activated": activated == "true"}
            for rate, initialized, activated in re.findall(
                r"\(([0-9.]+), (true|false), (true|false)\)", matrix.group(1)
            )
        ]
    exact_rates = tuple(item["rate"] for item in outcomes) == RATES
    positive_control = exact_rates and outcomes[RATES.index(48000.0)]["activated"]
    result = {
        "feature": feature,
        "test": TEST_PREFIX + test_name,
        "command": command,
        "exit_code": code,
        "test_result": test_result.group(1) if test_result else None,
        "outcomes": outcomes,
        "exact_rates": exact_rates,
        "positive_control": positive_control,
        "log": str(log_path.relative_to(ROOT)),
    }
    print(f"[{feature}] exit={code} test={result['test_result']} rates={len(outcomes)} control={positive_control}", flush=True)
    if code or not exact_rates:
        print("\n".join(output.splitlines()[-80:]), flush=True)
    return result


def main() -> int:
    def interrupt(signum: int, _frame: object) -> None:
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    if len(sys.argv) != 3 or sys.argv[1] not in {"baseline", "fixed"}:
        print("usage: clap_activation_matrix_check.py baseline|fixed EVIDENCE_DIR", file=sys.stderr)
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
        for feature, test_name in CASES.items():
            results.append(run_case(feature, test_name, evidence / "logs" / f"{feature}.log"))
    except Exception as error:
        interrupted = True
        print(f"runner error: {error}", file=sys.stderr, flush=True)
    except KeyboardInterrupt as error:
        interrupted = True
        print(f"interrupted: {error}", file=sys.stderr, flush=True)
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
    observed = complete and all(
        result["test_result"] in {"ok", "FAILED"}
        and result["exact_rates"]
        and result["positive_control"]
        and all(item["initialized"] for item in result["outcomes"])
        for result in results
    )
    all_green = observed and all(
        result["exit_code"] == 0
        and result["test_result"] == "ok"
        and all(item["activated"] for item in result["outcomes"])
        for result in results
    )
    expected_red = observed and any(
        result["exit_code"] == 101
        and result["test_result"] == "FAILED"
        and any(not item["activated"] for item in result["outcomes"])
        for result in results
    ) and all(
        result["exit_code"] == 0
        or (result["exit_code"] == 101 and result["test_result"] == "FAILED")
        for result in results
    )
    verdict = (
        "PASS" if mode == "fixed" and all_green
        else "EXPECTED_RED" if mode == "baseline" and expected_red
        else "FAIL"
    )
    report = {
        "mode": mode,
        "verdict": verdict,
        "expected_case_count": len(CASES),
        "rates": RATES,
        "results": results,
        "source_issues": issues,
        "interrupted": interrupted,
    }
    (evidence / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"CLAP activation matrix verdict: {verdict}", flush=True)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
