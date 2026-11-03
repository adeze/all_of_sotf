#!/usr/bin/env python3
"""Guarded all-feature, all-target compilation of nine pinned Rust workspaces."""

from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.release.checkout_sources import read_manifest, root_layout_status
from scripts.release.qa import cargo_environment, source_issues, source_state, workspace_map

STOP = False


def interrupt(_signum: int, _frame: object) -> None:
    global STOP
    STOP = True


def members(pgid: int) -> list[dict]:
    text = subprocess.run(["ps", "-eo", "pid=,pgid=,stat="], check=True,
                          capture_output=True, text=True, timeout=3).stdout
    result = []
    for line in text.splitlines():
        pid, group, state = line.split(maxsplit=2)
        if int(group) == pgid:
            result.append({"pid": int(pid), "state": state})
    return result


def reap(child: subprocess.Popen, result: dict) -> None:
    child.poll()
    if child.returncode is None or sys.platform != "linux":
        return
    try:
        group = members(child.pid)
    except Exception as error:
        result.setdefault("cleanup_errors", []).append(f"reap inspection: {error}")
        return
    for item in group:
        pid = item["pid"]
        if pid == child.pid:
            continue
        try:
            reaped, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            continue
        if reaped:
            result.setdefault("reaped_descendants", []).append({"pid": reaped, "status": status})


def cleanup(child: subprocess.Popen, result: dict) -> bool:
    for signum in (signal.SIGTERM, signal.SIGKILL):
        reap(child, result)
        try:
            remaining = members(child.pid)
        except Exception as error:
            result.setdefault("cleanup_errors", []).append(f"inspection: {error}")
            remaining = [{"pid": child.pid, "state": "uninspectable"}]
        if not remaining:
            break
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass
        for _ in range(50):
            child.poll()
            reap(child, result)
            try:
                if not members(child.pid):
                    break
            except Exception as error:
                result.setdefault("cleanup_errors", []).append(f"poll inspection: {error}")
                break
            time.sleep(0.1)
    child.poll()
    reap(child, result)
    try:
        result["survivors"] = members(child.pid)
    except Exception as error:
        result.setdefault("cleanup_errors", []).append(f"final inspection: {error}")
        result["survivors"] = [{"pid": child.pid, "state": "uninspectable"}]
    return not result["survivors"] and not result.get("cleanup_errors")


def root_status(pins: dict[str, str]) -> dict:
    return root_layout_status(ROOT, pins)


def run(name: str, argv: list[str], output: Path, cwd_name: str | None = None) -> dict:
    if STOP:
        raise KeyboardInterrupt("stopped before command launch")
    command_cwd = ROOT / (cwd_name or name)
    result: dict = {"workspace": name, "argv": argv, "exit_code": None,
                    "cleanup_ok": False, "owned_pgid": None,
                    "working_directory": str(command_cwd.resolve())}
    child: subprocess.Popen | None = None
    with (output / f"{name}.log").open("wb") as log:
        try:
            if STOP:
                raise KeyboardInterrupt("stopped before process launch")
            command_env = cargo_environment(command_cwd)
            result["toolchain"] = toolchain_identity(command_cwd, command_env)
            if STOP:
                raise KeyboardInterrupt("stopped during toolchain inspection")
            child = subprocess.Popen(argv, cwd=command_cwd, stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True,
                                     env=command_env)
            result["owned_pgid"] = child.pid
            (output / "active-command.json").write_text(json.dumps(result, indent=2) + "\n")
            while child.poll() is None and not STOP:
                print(f"{name}: all-feature check still running, owned PGID {child.pid}", flush=True)
                try:
                    child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    pass
            result["exit_code"] = 130 if STOP else child.poll()
            result["interrupted"] = STOP
        except Exception as error:
            result["runner_error"] = str(error)
        finally:
            if child is not None:
                result["cleanup_ok"] = cleanup(child, result)
            (output / "active-command.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def toolchain_identity(cwd: Path | None = None,
                       environment: dict[str, str] | None = None) -> dict[str, str | None]:
    identity: dict[str, str | None] = {
        "platform": platform.platform(), "machine": platform.machine(),
        "working_directory": str((cwd or Path.cwd()).resolve()),
    }
    for name in ("rustc", "cargo"):
        try:
            result = subprocess.run([name, "--version"], check=True, capture_output=True,
                                    text=True, timeout=5, cwd=cwd, env=environment)
            identity[name] = result.stdout.strip()
        except (OSError, subprocess.SubprocessError) as error:
            identity[name] = f"unavailable: {error}"
    return identity


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", help="explicit output directory (legacy positional form)")
    parser.add_argument("--evidence-root", type=Path,
                        help="parent directory for a timestamped report (defaults to target/release-qa)")
    args = parser.parse_args(argv)
    if args.output and args.evidence_root:
        parser.error("use either the positional output directory or --evidence-root")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    evidence_root = args.evidence_root or Path(os.environ.get(
        "SOTF_QA_EVIDENCE_ROOT", ROOT / "target" / "release-qa"))
    output = (Path(args.output) if args.output else evidence_root / f"{timestamp}-all-features").resolve()
    output.mkdir(parents=True, exist_ok=False)
    report: dict = {"status": "FAIL", "started_at": timestamp,
                    "platform": platform.platform(), "machine": platform.machine(),
                    "toolchain": toolchain_identity(ROOT, cargo_environment(ROOT)),
                    "cargo_net_offline": os.environ.get("CARGO_NET_OFFLINE"),
                    "scope": "nine locked all-feature/all-target checks plus independent AutoEQ GUI demo",
                    "commands": [], "issues": [],
                    "effective_cargo_home": cargo_environment(ROOT).get("CARGO_HOME"),
                    "effective_cargo_target_dir": os.environ.get("CARGO_TARGET_DIR")}
    before = None
    root_before = None
    status_before = None
    try:
        if sys.platform == "linux":
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(36, 1, 0, 0, 0) != 0:
                raise RuntimeError("could not register Linux child subreaper")
        manifest = ROOT / "scripts/release/sources.json"
        (output / "sources.json").write_bytes(manifest.read_bytes())
        _, _, pins = read_manifest(manifest)
        root_before = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                              text=True).strip()
        report["root_revision"] = root_before
        status_before = root_status(pins)
        before = source_state(ROOT, list(workspace_map()), "macos" if sys.platform == "darwin" else "linux")
        (output / "sources-before.json").write_text(json.dumps(before, indent=2) + "\n")
        report["issues"].extend(source_issues(before, before, True))
        for name, revision in pins.items():
            if before.get(name, {}).get("revision") != revision:
                report["issues"].append(f"{name}: source pin mismatch")
            if not before.get(name, {}).get("lock_sha256"):
                report["issues"].append(f"{name}: canonical Cargo.lock missing")
        if not before.get("autoeq", {}).get("nested_lock_sha256"):
            report["issues"].append("autoeq: nested GPUI examples Cargo.lock missing")
        if status_before["missing"] or status_before["unexpected"]:
            report["issues"].append("root checkout has missing or unexpected paths")
        if report["issues"]:
            raise RuntimeError("source preflight failed")
        names = list(workspace_map())
        for name in names:
            command = ["cargo", "check", "--workspace", "--all-targets",
                       "--all-features", "--keep-going"]
            result = run(name, command, output)
            report["commands"].append(result)
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            if result["exit_code"] != 0:
                report["issues"].append(f"{name}: locked all-feature check failed")
            if not result["cleanup_ok"] or STOP:
                raise RuntimeError(f"{name}: cleanup incomplete or interrupted")
        command = ["cargo", "check", "--all-targets", "--all-features", "--keep-going",
                   "--manifest-path", "crates/autoeq-gpui-examples/Cargo.toml"]
        result = run("autoeq-gpui-examples", command, output, cwd_name="autoeq")
        report["commands"].append(result)
        if result["exit_code"] != 0:
            report["issues"].append("nested AutoEQ demo all-feature check failed")
        if not result["cleanup_ok"] or STOP:
            raise RuntimeError("nested AutoEQ demo cleanup incomplete or interrupted")
    except (Exception, KeyboardInterrupt) as error:
        report["issues"].append(str(error))
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            after = source_state(ROOT, list(workspace_map()), "macos" if sys.platform == "darwin" else "linux")
            (output / "sources-after.json").write_text(json.dumps(after, indent=2) + "\n")
            if before is not None:
                report["issues"].extend(source_issues(before, after, True))
            _, _, pins = read_manifest(ROOT / "scripts/release/sources.json")
            if status_before is not None and root_status(pins) != status_before:
                report["issues"].append("root checkout status changed")
            if root_before is not None and subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip() != root_before:
                report["issues"].append("root revision changed")
            if (ROOT / "scripts/release/sources.json").read_bytes() != (output / "sources.json").read_bytes():
                report["issues"].append("source manifest changed")
        except Exception as error:
            report["issues"].append(f"final source guard: {error}")
        report["status"] = "PASS" if not report["issues"] and len(report["commands"]) == 10 else "FAIL"
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
