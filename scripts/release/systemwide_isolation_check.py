#!/usr/bin/env python3
"""Run bounded, source-pinned systemwide lab-isolation checks on Gitea."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "sotf-systemwide"


def source_state() -> dict[str, dict[str, object]]:
    sources = json.loads((ROOT / "scripts/release/sources.json").read_text())["sources"]
    state = {}
    for name, entry in sources.items():
        path = ROOT / name
        revision = subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
        ).strip()
        tracked = subprocess.check_output(
            ["git", "-C", str(path), "diff", "--name-only", "HEAD"], text=True
        ).splitlines()
        state[name] = {
            "revision": revision,
            "tracked_status": tracked,
            "lock_sha256": hashlib.sha256((path / "Cargo.lock").read_bytes()).hexdigest(),
        }
        if revision != entry["revision"] or tracked:
            raise RuntimeError(f"{name}: source differs from clean pinned revision")
    return state


def run_check(
    output: Path, name: str, argv: list[str], timeout_seconds: int,
    required_patterns: tuple[str, ...] = (),
) -> bool:
    log = output / f"{name}.log"
    print(f"[{name}] timeout {timeout_seconds}s: {' '.join(argv)}", flush=True)
    start = time.monotonic()
    interrupted = False
    with log.open("wb") as stream:
        process = subprocess.Popen(argv, cwd=WORKSPACE, stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            status = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            status = 124
            print(f"[{name}] timed out", flush=True)
        except KeyboardInterrupt:
            status = 130
            interrupted = True
            print(f"[{name}] interrupted", flush=True)
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print(f"[{name}] owned process did not reap after SIGKILL", flush=True)
                status = 1
    text = log.read_text(errors="replace")
    matched = all(re.search(pattern, text, re.MULTILINE) is not None
                  for pattern in required_patterns)
    print(f"[{name}] exit {status}, required-test={matched}, elapsed={time.monotonic()-start:.1f}s", flush=True)
    if status or not matched:
        print("\n".join(text.splitlines()[-80:]), flush=True)
    if interrupted:
        raise KeyboardInterrupt(f"{name} interrupted after owned process cleanup")
    return status == 0 and matched


def main() -> int:
    def interrupt(signum: int, _frame: object) -> None:
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    if len(sys.argv) != 3 or sys.argv[1] not in ("macos", "linux"):
        print("usage: systemwide_isolation_check.py {macos,linux} OUTPUT", file=sys.stderr)
        return 2
    platform = sys.argv[1]
    if (platform == "macos") != (sys.platform == "darwin"):
        print(f"requested {platform} on {sys.platform}", file=sys.stderr)
        return 2
    output = Path(sys.argv[2]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "sources.json").write_bytes((ROOT / "scripts/release/sources.json").read_bytes())
    (output / "NOT_FULL_QA").write_text(
        "Focused systemwide isolation proof only; generic full QA and installed HAL remain unverified.\n"
    )
    (output / "toolchain.txt").write_text(
        "".join(subprocess.check_output(command, text=True) for command in
                (["rustc", "--version"], ["cargo", "--version"], ["python3", "--version"]))
    )
    before = source_state()
    (output / "source-before.json").write_text(json.dumps(before, indent=2) + "\n")
    positive_result = r"^test result: ok\. [1-9][0-9]* passed; 0 failed;"
    checks: list[tuple[str, list[str], int, tuple[str, ...]]] = [
        ("systemwide-owned-format", ["rustfmt", "--check", "--edition", "2024",
                                      "crates/daemon/bin/driver_manager.rs",
                                      "crates/daemon/bin/sotf_daemon/audio_daemon.rs",
                                      "crates/daemon/bin/sotf_daemon/pipeline_reconfigure_outcome.rs",
                                      "crates/daemon/bin/sotf_daemon/tests.rs",
                                      "crates/daemon/tests/ipc_line_tests.rs",
                                      "crates/daemon/tests/hal_driver_contract_tests.rs"], 180, ()),
        ("driver-output-inventory", ["cargo", "test", "-p", "sotf-daemon",
                                     "--bin", "sotf-daemon", "--", "--list"], 1200,
         (r"injected_driver_lab_output_is_explicit_and_keeps_driver_identity: test",)),
        ("driver-output-identity", ["cargo", "test", "-p", "sotf-daemon",
                                    "--bin", "sotf-daemon", "injected_driver_lab_output_is_explicit_and_keeps_driver_identity",
                                    "--", "--nocapture"], 1200,
         (positive_result, "injected_driver_lab_output_is_explicit_and_keeps_driver_identity")),
        ("daemon-ipc-safety-inventory", ["cargo", "test", "-p", "sotf-daemon",
                                         "--bin", "sotf-daemon", "--", "--list"], 1200,
         (r"testkit_live_rack_state_promotion_and_graph_reorder_preserve_node_state: test",
          r"testkit_concurrent_add_plugin_preserves_both_mutations: test")),
        ("daemon-ipc-safety", ["cargo", "test", "-p", "sotf-daemon",
                               "--bin", "sotf-daemon", "ipc_safety_tests::", "--",
                               "--nocapture", "--test-threads=1"], 1200,
         (positive_result,
          "testkit_live_rack_state_promotion_and_graph_reorder_preserve_node_state",
          "testkit_concurrent_add_plugin_preserves_both_mutations")),
    ]
    ipc_command = ["cargo", "test", "-p", "sotf-daemon", "--test", "ipc_line_tests"]
    ipc_names = [
        "second_daemon_cannot_take_ownership_of_a_live_runtime",
        "daemon_shutdown_drains_clients_and_allows_immediate_restart",
        "systemwide_lab_scenario_matrix_over_ipc",
        "systemwide_lab_restarts_with_a_fresh_coherent_snapshot",
    ]
    if platform == "macos":
        ipc_command.extend(["--features", "hal"])
        ipc_names.append("second_daemon_with_distinct_socket_cannot_rotate_shared_transport_key")
    checks.extend([
        ("daemon-ipc-line-inventory", ipc_command + ["--", "--list"], 1200,
         tuple(f"{name}: test" for name in ipc_names)),
        ("daemon-ipc-line", ipc_command + ["--", "--nocapture", "--test-threads=1"], 1200,
         (positive_result, *ipc_names)),
        ("daemon-hal-contract", ["cargo", "test", "-p", "sotf-daemon",
                                 "--test", "hal_driver_contract_tests"], 600,
         (positive_result,)),
    ])
    results = {}
    try:
        for name, argv, timeout, patterns in checks:
            results[name] = run_check(output, name, argv, timeout, patterns)
            (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    except (Exception, KeyboardInterrupt) as error:
        print(f"focused runner failed: {error}", file=sys.stderr, flush=True)
        results["runner_exception"] = False
    finally:
        (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        try:
            after = source_state()
            (output / "source-after.json").write_text(json.dumps(after, indent=2) + "\n")
            if after != before:
                print("pinned source or Cargo.lock changed", file=sys.stderr)
                results["source_unchanged"] = False
        except Exception as error:
            print(f"could not verify final source state: {error}", file=sys.stderr)
            results["source_unchanged"] = False
        (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
