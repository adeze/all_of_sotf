#!/usr/bin/env python3
"""Generate real AutoEQ RoomEQ artifacts and test the daemon's graph importer."""

from __future__ import annotations

import hashlib
import json
import os
import ctypes
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.buildbot.ci_matrix import workspace_map
from scripts.release.checkout_sources import read_manifest
from scripts.release.qa import ROOT, source_issues, source_state


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


def stop_group(child: subprocess.Popen) -> tuple[bool, list[dict], list[str]]:
    errors: list[str] = []

    def inspect() -> list[dict] | None:
        try:
            return group_members(child.pid)
        except Exception as exc:
            errors.append(f"owned group inspection failed: {exc}")
            return None

    for signum in (signal.SIGTERM, signal.SIGKILL):
        members = inspect()
        if members == [] and not errors:
            return True, [], []
        try:
            os.killpg(child.pid, signum)
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


def run(name: str, argv: list[str], cwd: Path, log: Path, env: dict[str, str]) -> dict:
    with log.open("w", encoding="utf-8") as stream:
        child = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stream,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        interrupted = False
        try:
            deadline = time.monotonic() + 3600
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    code = 124
                    break
                try:
                    code = child.wait(timeout=min(30, remaining))
                    break
                except subprocess.TimeoutExpired:
                    print(f"[{name}] still running; owned leader PID {child.pid}", flush=True)
        except KeyboardInterrupt:
            code = 130
            interrupted = True
        finally:
            cleanup_ok, survivors, cleanup_errors = stop_group(child)
    return {"name": name, "argv": argv, "exit_code": code, "cleanup_ok": cleanup_ok,
            "interrupted": interrupted,
            "owned_group_survivors": survivors, "cleanup_inspection_errors": cleanup_errors,
            "log": str(log.relative_to(ROOT))}


def main() -> int:
    def interrupted(signum: int, _frame: object) -> None:
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    evidence = ROOT / "generated-roomeq-evidence"
    evidence.mkdir(exist_ok=True)
    before = source_state(ROOT, list(workspace_map()), "linux")
    (evidence / "sources-before.json").write_text(json.dumps(before, indent=2))
    commands: list[dict] = []
    files: list[dict] = []
    error = None
    try:
        enable_subreaper()
        if not Path("/.dockerenv").exists() or os.geteuid() != 0 or Path("/dev/snd").exists():
            raise RuntimeError("audio-device-free disposable Linux container required")
        if source_issues(before, before, require_clean=True):
            raise RuntimeError("source checkout is not clean")
        _, _, pinned = read_manifest(ROOT / "scripts/release/sources.json")
        for name, revision in pinned.items():
            if before[name]["revision"] != revision:
                raise RuntimeError(f"{name} checkout does not match pinned revision")
        with tempfile.TemporaryDirectory(prefix="roomeq-generated-") as private:
            private_root = Path(private)
            output = private_root / "artifacts"
            home = private_root / "home"
            runtime = private_root / "runtime"
            output.mkdir(mode=0o700)
            home.mkdir(mode=0o700)
            runtime.mkdir(mode=0o700)
            autoeq = ROOT / "autoeq"
            config = autoeq / "data_tests/roomeq/generate/fem/small_stereo_2_0/config.json"
            overrides = autoeq / "data_tests/roomeq/generate/optimiser-config/small_stereo_2_0"
            env = os.environ.copy()
            env.pop("SOTF_OUTPUT_DEVICE", None)
            env.pop("PULSE_COOKIE", None)
            env["HOME"] = str(home)
            env["XDG_RUNTIME_DIR"] = str(runtime)
            env["PULSE_SERVER"] = f"unix:{runtime}/no-pulse-server"
            env["PIPEWIRE_REMOTE"] = "no-pipewire-server"
            env["PIPEWIRE_RUNTIME_DIR"] = str(runtime)
            env["JACK_NO_START_SERVER"] = "1"
            for method in ("iir", "fir", "mixed"):
                artifact = output / f"dsp_{method}.json"
                argv = ["cargo", "run", "--features", "cli", "--bin", "roomeq",
                        "--release", "--", "--config", str(config), "--override-config",
                        str(overrides / f"optimiser-{method}.json"), "--output", str(artifact)]
                result = run(f"generate-{method}", argv, autoeq, evidence / f"generate-{method}.log", env)
                commands.append(result)
                if result["exit_code"] or not result["cleanup_ok"]:
                    raise RuntimeError(f"AutoEQ {method} generator failed")
            # Retain the complete native bundles, including metadata and WAV
            # sidecars, before a failed handoff check can remove the tempdir.
            shutil.copytree(output, evidence / "generated-artifacts")
            published = sorted(path for path in output.rglob("*") if path.is_file())
            observed = [{"path": str(path.relative_to(output)), "bytes": path.stat().st_size}
                        for path in published]
            (evidence / "published-inventory.json").write_text(json.dumps(observed, indent=2))
            graph_handoff = private_root / "graph-handoff"
            graph_handoff.mkdir(mode=0o700)
            for method in ("iir", "fir", "mixed"):
                path = output / f"dsp_{method}.json"
                if path.is_symlink() or not path.is_file():
                    raise RuntimeError(f"missing canonical DSP output: {path.name}")
                data = path.read_bytes()
                if not data:
                    raise RuntimeError(f"empty generated artifact: {path.name}")
                copied = graph_handoff / path.name
                copied.write_bytes(data)
                digest = hashlib.sha256(data).hexdigest()
                if hashlib.sha256(copied.read_bytes()).hexdigest() != digest:
                    raise RuntimeError(f"graph handoff changed DSP bytes: {path.name}")
                files.append({"name": path.name, "bytes": len(data), "sha256": digest})
            if sorted(path.name for path in graph_handoff.rglob("*.json")) != [
                    "dsp_fir.json", "dsp_iir.json", "dsp_mixed.json"]:
                raise RuntimeError("expected exactly three generated DSP JSON artifacts")
            (evidence / "artifact-inventory.json").write_text(json.dumps(files, indent=2))
            env["SOTF_GENERATED_ROOM_EQ_DIR"] = str(graph_handoff)
            argv = ["cargo", "test", "-p", "sotf-daemon", "--bin", "sotf-daemon",
                    "plugin_artifact::tests::all_generated_room_eq_files_build_graphs", "--",
                    "--ignored", "--exact"]
            result = run("systemwide-graph", argv, ROOT / "sotf-systemwide",
                         evidence / "systemwide-graph.log", env)
            commands.append(result)
            if result["exit_code"] or not result["cleanup_ok"]:
                raise RuntimeError("generated RoomEQ graph test failed")
            text = (evidence / "systemwide-graph.log").read_text(errors="replace")
            if not re.search(r"test plugin_artifact::tests::all_generated_room_eq_files_build_graphs \.\.\. ok", text):
                raise RuntimeError("named ignored graph test did not run and pass")
            if not re.search(r"test result: ok\. 1 passed; 0 failed", text):
                raise RuntimeError("graph test summary did not report one passing test")
    except (KeyboardInterrupt, Exception) as exc:
        error = str(exc)
    after = source_state(ROOT, list(workspace_map()), "linux")
    issues = source_issues(before, after, require_clean=True)
    (evidence / "sources-after.json").write_text(json.dumps(after, indent=2))
    report = {"status": "PASS" if not error and not issues else "FAIL",
              "error": error, "source_issues": issues, "commands": commands, "artifacts": files}
    (evidence / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
