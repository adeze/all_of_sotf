#!/usr/bin/env python3
"""Draft macOS Gitea lane: run non-hardware release gates and report omissions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.buildbot.ci_matrix import workspace_map
from scripts.release.checkout_sources import read_manifest, root_layout_status
from scripts.release.qa import source_issues, source_state
from scripts.release.process_supervision import members

STOP = False
TEST_GATES = {
    "sotf-test-pr", "sotf-ntest", "sotf-test-negative", "sotf-test-proptest",
    "sotf-itest-nonphysical", "sotf-itest-serial-nonphysical",
    "daw-ntest", "daw-test-unit-core", "daw-test-integration-engine",
    "daw-test-device-fakes", "daw-test-realtime-safety", "daw-qa-ffi",
    "daw-qa-bridge", "daw-qa-plugins-cross-format",
}


def root_checkout_status(pins: dict[str, str]) -> dict[str, list[str]]:
    """Check the manifest pins against tracked gitlinks or legacy checkouts."""
    return root_layout_status(ROOT, pins)


def interrupted(_signal: int, _frame: object) -> None:
    global STOP
    STOP = True


def write_report(path: Path, report: dict) -> None:
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(report, indent=2) + "\n")
    pending.replace(path)


def test_evidence(log: Path) -> dict:
    body = log.read_text(encoding="utf-8", errors="replace")
    cargo = [int(value) for value in re.findall(r"test result: ok\.\s+(\d+) passed;", body)]
    nextest = [int(value) for value in re.findall(
        r"Summary\s+\[[^\]]+\]\s+\d+ tests? run:\s+(\d+) passed", body,
    )]
    ignored = sum(int(value) for value in re.findall(
        r"test result: ok\.[^\n]*?;\s+(\d+) ignored;", body,
    ))
    ignored += sum(int(value) for value in re.findall(
        r"Summary\s+\[[^\]]+\][^\n]*?\b(\d+) skipped\b", body,
    ))
    expected_hardware_skips = len(re.findall(r"Skipping test \(AEQ_E2E!=1\)", body))
    all_skip_lines = [line for line in body.splitlines() if "SKIP:" in line or "Skipping test" in line]
    unexpected_skips = [line for line in all_skip_lines if "Skipping test (AEQ_E2E!=1)" not in line]
    return {"passed_tests": sum(cargo) + sum(nextest),
            "cargo_test_binaries": len(cargo), "nextest_summaries": len(nextest),
            "ignored_or_skipped_tests": ignored,
            "known_physical_hardware_skips": expected_hardware_skips,
            "unexpected_skip_lines": unexpected_skips[:30]}


def nested_qa_evidence(path: Path, *, require_tests: bool) -> dict:
    report = json.loads((path / "report.json").read_text())
    commands = [command for workspace in report.get("workspaces", [])
                for command in workspace.get("commands", [])]
    failures = [command for command in commands
                if not command.get("owned_group_cleanup", {}).get("ok")]
    test_count = 0
    ignored = 0
    unexpected_skips: list[str] = []
    missing_logs: list[str] = []
    zero_test_commands: list[list[str]] = []
    for command in commands:
        log = Path(command.get("log", ""))
        if not log.is_file():
            missing_logs.append(str(log))
            continue
        evidence = test_evidence(log)
        test_count += evidence["passed_tests"]
        ignored += evidence["ignored_or_skipped_tests"]
        unexpected_skips.extend(evidence["unexpected_skip_lines"])
        argv = command.get("argv", [])
        recipe = argv[1] if argv[:1] == ["just"] and len(argv) > 1 else ""
        test_recipe = (recipe in {"qa", "qa-release-evidence", "all", "ntest", "test"}
                       or recipe.startswith("test-") or recipe.startswith("qa-"))
        if require_tests and (test_recipe or argv[:2] == ["cargo", "test"]) and evidence["passed_tests"] == 0:
            zero_test_commands.append(argv)
    # Every workspace QA lane must show actual executed tests. A successful
    # compilation command alone cannot qualify a test-bearing QA recipe.
    release_evidence = None
    if path.name == "gpui-toolkit-qa":
        manifest = ROOT / "gpui-toolkit/target/qa/release-evidence.json"
        if manifest.is_file():
            data = json.loads(manifest.read_text())
            release_evidence = {"artifact_count": len(data.get("artifacts", [])),
                                "source": data.get("source"),
                                "report_type": data.get("report_type")}
        else:
            release_evidence = {"error": "strict toolkit release evidence is absent"}
    expected_source = report.get("workspaces", [{}])[0].get("source", {})
    custom_ok = (release_evidence is None or
                 (release_evidence.get("artifact_count", 0) > 0 and
                  release_evidence.get("source", {}).get("revision") == expected_source.get("revision") and
                  release_evidence.get("source", {}).get("dirty") is False))
    return {"status": report.get("status"), "command_count": len(commands),
            "unowned_or_unclean_commands": failures,
            "passed_tests_observed": test_count, "ignored_or_skipped_tests": ignored,
            "unexpected_skip_lines": unexpected_skips[:30],
            "missing_logs": missing_logs, "zero_test_commands": zero_test_commands,
            "custom_validator": release_evidence,
            "ok": report.get("status") == "PASS" and bool(commands) and not failures
                  and not missing_logs and not unexpected_skips and ignored == 0
                  and (not require_tests or test_count > 0)
                  and not zero_test_commands and custom_ok}


def active_inner_pgid(report_path: Path | None) -> tuple[int | None, bool]:
    if report_path is None or not report_path.is_file():
        return None, False
    report = json.loads(report_path.read_text())
    active = report.get("active_command")
    if not isinstance(active, dict):
        return None, False
    if active.get("owned_group_cleanup", {}).get("ok") is True:
        return None, False
    value = active.get("owned_pgid")
    return (value if isinstance(value, int) and value > 0 else None), True


def cleanup(
    process: subprocess.Popen[bytes], inner_report: Path | None = None,
) -> tuple[bool, list[dict[str, str]], list[str]]:
    """Stop our group; give nested qa.py time to clean its registered group."""
    errors: list[str] = []
    active_inner: int | None = None
    remaining: list[dict[str, str]] = []

    def inspect() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        nonlocal active_inner
        inner, active = active_inner_pgid(inner_report)
        active_inner = inner
        if active and inner is None:
            errors.append("qa.py active command has no registered owned process group")
        outer_members = members(process.pid)
        inner_members = members(active_inner) if active_inner is not None else []
        return outer_members, inner_members

    try:
        outer, inner = inspect()
        if not outer and not inner:
            process.wait(timeout=5)
            return not errors, [], errors
    except Exception as error:
        errors.append(f"owned group initial inspection: {error}")
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError as error:
        errors.append(f"owned outer group TERM: {error}")

    # The inner qa.py cleanup can spend 5s per TERM/KILL stage plus bounded ps
    # inspection and direct-child reaping. 60s is cleanup grace, not a QA run
    # deadline; never terminate a still-running gate based on elapsed QA time.
    grace = 60 if inner_report is not None else 5
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            process.poll()
            outer, inner = inspect()
        except Exception as error:
            errors.append(f"owned group inspection/reap: {error}")
            break
        if not outer and not inner:
            break
        time.sleep(0.1)
    else:
        # Escalate the exact inner PGID registered by qa.py before its wrapper.
        if active_inner is not None:
            try:
                os.killpg(active_inner, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as error:
                errors.append(f"registered inner group signal: {error}")
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as error:
            errors.append(f"outer group signal: {error}")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                process.poll()
                outer, inner = inspect()
            except Exception as error:
                errors.append(f"post-KILL group inspection/reap: {error}")
                break
            if not outer and not inner:
                break
            time.sleep(0.1)
    try:
        process.wait(timeout=5)
        outer, inner = inspect()
        remaining = outer + inner
    except Exception as error:
        errors.append(f"owned direct child/group reap: {error}")
    return not errors and not remaining, remaining, errors


def commands(evidence: Path) -> list[tuple[str, str, list[str]]]:
    checks: list[tuple[str, str, list[str]]] = [
        ("dependency-graph", ".", ["python3", "scripts/release/dependency_graph.py", "--output", str(evidence / "dependency-graph.json")]),
        ("all-nine-check", ".", ["python3", "scripts/release/qa.py", "check", "--platform", "macos", "--require-clean", "--output", str(evidence / "all-nine-check")]),
    ]
    for workspace in ("autoeq", "math-audio", "gpui-toolkit", "sofa-reader", "sotf-capture", "sotf-systemwide"):
        checks.append((f"{workspace}-qa", ".", ["python3", "scripts/release/qa.py", "qa", "--workspace", workspace, "--platform", "macos", "--require-clean", "--output", str(evidence / f"{workspace}-qa")]))
    for recipe in ("check", "lint", "test-pr", "ntest", "test-negative", "test-proptest"):
        checks.append((f"sotf-{recipe}", "sotf", ["just", recipe]))
    features = "--features=qa,onnx,hal,gpu-2d,gpu-3d,iamf,streaming,hls"
    common = ["cargo", "nextest", "run", "--release", "--no-fail-fast", "--workspace", "--tests", features]
    checks.append(("sotf-itest-nonphysical", "sotf", common + ["-E", "not (test(test_play_to_audible_latency) | test(test_loudness_compensation_zero_alloc) | test(test_upmixer_plugin_timing))"]))
    checks.append(("sotf-itest-serial-nonphysical", "sotf", common + ["--test-threads=1", "-E", "test(test_loudness_compensation_zero_alloc) | test(test_upmixer_plugin_timing)"]))
    for recipe in ("perf-smoke", "dev-driver-smoke", "dev-driver-roomeq"):
        checks.append((f"sotf-{recipe}", "sotf", ["just", recipe]))
    for recipe in ("check", "lint", "ntest", "test-unit-core", "test-integration-engine", "test-device-fakes", "test-realtime-safety", "qa-plugins", "qa-ffi", "qa-bridge", "qa-plugins-cross-format", "qa-engine-fuzzer"):
        checks.append((f"daw-{recipe}", "sotf-daw", ["just", recipe]))
    return checks


def run_gate(name: str, cwd: str, argv: list[str], evidence: Path) -> dict:
    env = os.environ.copy()
    for key in ("AEQ_E2E", "AEQ_E2E_DEVICE", "AEQ_E2E_SR", "AEQ_E2E_SEND_CH", "AEQ_E2E_RECORD_CH", "SOTF_OUTPUT_DEVICE"):
        env.pop(key, None)
    if cwd == "sotf-systemwide" or name == "sotf-systemwide-qa":
        runtime = evidence / "systemwide-private-runtime"
        runtime.mkdir(mode=0o700, exist_ok=True)
        env["SOTF_SYSTEMWIDE_RUNTIME_DIR"] = str(runtime)
        env["SOTF_SYSTEMWIDE_STATE_PATH"] = str(runtime / "state.json")
    if name.startswith("sotf-itest"):
        env["PROPTEST_CASES"] = "10000"
        env["CARGO_PROFILE_RELEASE_LTO"] = "off"
    log = evidence / "logs" / f"{name}.log"
    started = time.monotonic()
    status = 124
    with log.open("wb") as output:
        if STOP:
            return {"name": name, "cwd": cwd, "command": argv, "exit_code": 130,
                    "cleanup_ok": True, "owned_survivors": [],
                    "cleanup_errors": [], "status": "INTERRUPTED_BEFORE_LAUNCH",
                    "log": str(log.relative_to(ROOT))}
        child = subprocess.Popen(argv, cwd=ROOT / cwd, env=env, stdout=output,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        heartbeat = time.monotonic()
        try:
            while True:
                if STOP:
                    status = 130
                    break
                try:
                    status = child.wait(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() - heartbeat >= 30:
                        elapsed = time.monotonic() - started
                        print(f"[{name}] still running after {elapsed:.0f}s", flush=True)
                        heartbeat = time.monotonic()
        finally:
            cleanup_ok, survivors, cleanup_errors = cleanup(
                child, evidence / name / "report.json"
                if name == "all-nine-check" or name.endswith("-qa") else None,
            )
    result = {"name": name, "cwd": cwd, "command": argv, "exit_code": status,
              "elapsed_seconds": round(time.monotonic() - started, 2),
              "cleanup_ok": cleanup_ok, "owned_survivors": survivors,
              "cleanup_errors": cleanup_errors, "log": str(log.relative_to(ROOT))}
    if name in TEST_GATES:
        result["test_evidence"] = test_evidence(log)
        result["positive_tests"] = result["test_evidence"]["passed_tests"] > 0
        result["no_unexpected_skips"] = (
            not result["test_evidence"]["unexpected_skip_lines"]
            and result["test_evidence"]["ignored_or_skipped_tests"] == 0
        )
    if name == "all-nine-check" or (cwd == "." and name.endswith("-qa")):
        try:
            result["nested_qa"] = nested_qa_evidence(
                evidence / name, require_tests=name != "all-nine-check",
            )
        except Exception as error:
            result["nested_qa"] = {"ok": False, "error": str(error)}
    if status or not cleanup_ok:
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
        print(f"[{name}] exit={status} cleanup={cleanup_ok}\n" + "\n".join(tail), flush=True)
    return result


def main() -> int:
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    if len(sys.argv) != 2 or sys.platform != "darwin" or not os.getenv("CI"):
        print("usage: Gitea macOS CI: mac_safe_full_qa.py EVIDENCE_DIR", file=sys.stderr)
        return 2
    manifest = ROOT / "scripts/release/sources.json"
    _, _, pins = read_manifest(manifest)
    if sys.argv[1] == "--preflight-root":
        status = root_checkout_status(pins)
        print(json.dumps(status, indent=2), flush=True)
        return 0 if not status["unexpected"] and not status["missing"] else 1
    evidence = (ROOT / sys.argv[1]).resolve()
    (evidence / "logs").mkdir(parents=True, exist_ok=False)
    (evidence / "sources.json").write_bytes(manifest.read_bytes())
    root_before = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    root_status_before = root_checkout_status(pins)
    before = source_state(ROOT, list(workspace_map()), "macos")
    (evidence / "sources-before.json").write_text(json.dumps(before, indent=2) + "\n")
    errors = source_issues(before, before, True)
    if root_status_before["unexpected"] or root_status_before["missing"]:
        errors.append("root release checkout has unexpected or missing paths before validation")
    for name, revision in pins.items():
        if before.get(name, {}).get("revision") != revision:
            errors.append(f"{name}: checkout does not match sources.json pin")
    results: list[dict] = []
    root_status_after: dict[str, list[str]] | None = None
    report_path = evidence / "report.json"
    write_report(report_path, {"lane_status": "RUNNING", "full_release_qa": "INCOMPLETE",
                               "root_revision": root_before, "results": results, "errors": errors})
    if not errors:
        for name, cwd, argv in commands(evidence):
            if STOP:
                errors.append("interrupted before next gate")
                break
            print(f"[{name}] {' '.join(argv)}", flush=True)
            write_report(report_path, {"lane_status": "RUNNING", "full_release_qa": "INCOMPLETE",
                                       "root_revision": root_before, "active_gate": name,
                                       "results": results, "errors": errors})
            try:
                result = run_gate(name, cwd, argv, evidence)
                results.append(result)
                write_report(report_path, {"lane_status": "RUNNING", "full_release_qa": "INCOMPLETE",
                                           "root_revision": root_before, "results": results, "errors": errors})
                if not result["cleanup_ok"] or STOP:
                    errors.append(f"{name}: owned process cleanup or interruption")
                    break
            except Exception as error:
                errors.append(f"{name}: runner failure: {error}")
                break
    try:
        after = source_state(ROOT, list(workspace_map()), "macos")
        (evidence / "sources-after.json").write_text(json.dumps(after, indent=2) + "\n")
        errors.extend(source_issues(before, after, True))
        if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() != root_before:
            errors.append("root revision changed")
        root_status_after = root_checkout_status(pins)
        if root_status_after != root_status_before:
            errors.append("root checkout status changed")
        if manifest.read_bytes() != (evidence / "sources.json").read_bytes():
            errors.append("root source manifest changed")
    except Exception as error:
        errors.append(f"after-source guard: {error}")
    complete = len(results) == len(commands(evidence))
    included_pass = complete and all(
        item["exit_code"] == 0 and item["cleanup_ok"]
        and item.get("positive_tests", True) and item.get("no_unexpected_skips", True)
        and item.get("nested_qa", {"ok": True})["ok"]
        for item in results
    ) and not errors
    report = {"lane_status": "PASS" if included_pass else "FAIL", "full_release_qa": "INCOMPLETE",
              "root_revision": root_before, "results": results, "errors": errors,
              "root_status_before": root_status_before,
              "root_status_after": root_status_after,
              "required_omissions": ["SOTF physical play-to-audible", "DAW AEQ_E2E CPAL loopback at 16/44.1/48/96 kHz", "hardware-bearing dev-driver transport", "native packaging and Windows lanes"]}
    write_report(report_path, report)
    print(f"mac-safe QA: {report['lane_status']}; full release: INCOMPLETE", flush=True)
    return 0 if included_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
