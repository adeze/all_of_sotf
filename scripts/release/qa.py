#!/usr/bin/env python3
"""Run aggregate CI gates with pinned-source and command-level evidence."""

from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time
import re

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "buildbot"))
sys.path.insert(0, str(ROOT / "scripts" / "release"))
from ci_matrix import gate_commands, workspace_map
from version_snapshot import snapshot
from checkout_sources import read_manifest, root_layout_status
from process_supervision import clean_group, enable_subreaper
from workspaces import source_names, vendor_names

STOP = False


REJECTED_UPMIXER_EXPERIMENTS = {
    "above_512_hr_delay_baseline_and_source_aligned_candidate_tone":
        "AUD132 rejected candidate: removing the HR input delay preserves the N=2048 tone residual above the fixed 1% ceiling",
    "rejected_static_delay_candidate_does_not_align_fixed_gain_impulses":
        "Rejected AUD130 static output-delay candidate; failed impulse evidence is retained in audit/upmixer-hr-timing.md",
    "rejected_static_delay_candidate_does_not_align_fixed_gain_tone_phase":
        "Rejected AUD130 static output-delay candidate; failed tone residual evidence is retained in audit/upmixer-hr-timing.md",
}


def host_platform() -> str:
    return {"darwin": "macos", "linux": "linux", "windows": "windows"}.get(
        platform.system().lower(), platform.system().lower()
    )


def commands_for(name: str, phase: str, platform_name: str | None = None) -> list[tuple[str, ...]]:
    """Return commands required of this workspace on the selected platform."""
    platform_name = platform_name or host_platform()
    metadata = ("cargo", "metadata", "--format-version", "1")
    demo_manifest = "crates/autoeq-gpui-examples/Cargo.toml"
    demo_metadata = (*metadata, "--all-features", "--manifest-path", demo_manifest)
    demo_check = ("cargo", "check", "--all-targets", "--all-features",
                  "--manifest-path", demo_manifest)
    if phase == "metadata":
        return [metadata, demo_metadata] if name == "autoeq" else [metadata]
    if phase == "check":
        commands = [metadata, ("cargo", "check", "--workspace", "--all-targets")]
        return [*commands, demo_metadata, demo_check] if name == "autoeq" else commands
    workspace = workspace_map()[name]
    commands = [metadata, *gate_commands(workspace, qa=phase == "qa")]
    if name == "autoeq":
        commands.extend((demo_metadata, demo_check))
    if phase == "qa":
        extras = {
            "sotf": [("just", recipe) for recipe in (
                "test-pr", "ntest", "itest", "perf-smoke", "dev-driver-smoke",
                "dev-driver-full", "dev-driver-roomeq", "dev-driver-tui",
            )],
            "sotf-daw": [("just", recipe) for recipe in (
                "ntest", "qa-ffi", "qa-bridge", "qa-plugins-cross-format",
            )],
        }
        if name == "sotf-daw" and platform_name == "linux":
            extras[name].append((
                "cargo", "test", "-p", "sotf-host",
                "--features", "worker-test-backend,external-plugin-clap",
                "--test", "external_plugin_isolation", "--", "--nocapture",
            ))
        if name == "gpui-toolkit":
            # The strict release recipe requires Metal. Linux runs the portable QA recipe.
            commands = [metadata, ("just", "qa-release-evidence" if platform_name == "macos" else "qa")]
        commands.extend(extras.get(name, []))
    return commands


def source_state(root: Path, names: list[str], platform_name: str) -> dict:
    """Capture every sibling before and after the complete aggregate run."""
    states = {}
    for name in names:
        workspace = root / name
        if not workspace.is_dir() or (name not in vendor_names() and not (workspace / "Cargo.toml").is_file()):
            states[name] = {"error": "missing workspace Cargo.toml"}
            continue
        source = snapshot(workspace, platform_name)
        lock = workspace / "Cargo.lock"
        source["lock_sha256"] = hashlib.sha256(lock.read_bytes()).hexdigest() if lock.is_file() else None
        if name == "autoeq":
            nested = workspace / "crates" / "autoeq-gpui-examples" / "Cargo.lock"
            source["nested_lock_sha256"] = (
                hashlib.sha256(nested.read_bytes()).hexdigest() if nested.is_file() else None
            )
        states[name] = source
    return states


def source_issues(before: dict, after: dict, require_clean: bool) -> list[str]:
    issues = []
    for name, start in before.items():
        end = after.get(name, {})
        if "error" in start or "error" in end:
            issues.append(f"{name}: {start.get('error') or end.get('error')}")
        elif not start.get("revision") or start.get("dirty") is None:
            issues.append(f"{name}: source revision or cleanliness unavailable")
        elif require_clean and start["dirty"]:
            issues.append(f"{name}: source tree was dirty before validation")
        elif start["revision"] != end.get("revision") or start["dirty"] != end.get("dirty"):
            issues.append(f"{name}: source revision or cleanliness changed")
        elif require_clean and end.get("dirty") is not False:
            issues.append(f"{name}: source tree is dirty after validation")
        elif start["lock_sha256"] != end.get("lock_sha256"):
            issues.append(f"{name}: Cargo.lock changed")
        elif name == "autoeq" and not start.get("nested_lock_sha256"):
            issues.append("autoeq: nested GPUI examples Cargo.lock missing")
        elif name == "autoeq" and start["nested_lock_sha256"] != end.get("nested_lock_sha256"):
            issues.append("autoeq: nested GPUI examples Cargo.lock changed")
    return issues


def write_report(path: Path, report: dict) -> None:
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    pending.replace(path)


def demo_metadata_issues(log: Path) -> list[str]:
    """Require both shipped Spinorama binaries in Cargo's actual target inventory."""
    metadata = None
    for line in reversed(log.read_text(encoding="utf-8").splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and isinstance(candidate.get("packages"), list):
            metadata = candidate
            break
    if metadata is None:
        return ["nested AutoEQ demo cargo metadata is missing or invalid"]
    packages = [package for package in metadata["packages"]
                if package.get("name") == "autoeq-gpui-examples"]
    if len(packages) != 1:
        return ["nested AutoEQ demo package is absent or ambiguous"]
    binaries = {target.get("name") for target in packages[0].get("targets", [])
                if "bin" in target.get("kind", [])}
    missing = {"d3rs-spinorama", "px-spinorama"} - binaries
    return [f"nested AutoEQ demo binary missing: {name}" for name in sorted(missing)]


def print_failure_tail(log: Path, lines: int = 80) -> None:
    """Show the end of a failed gate in CI while preserving the full artifact."""
    print(f"--- Last {lines} lines of {log} ---", flush=True)
    with log.open("r", encoding="utf-8", errors="replace") as stream:
        for line in deque(stream, maxlen=lines):
            print(line, end="", flush=True)


def sandbox_coverage_skipped(name: str, phase: str, platform_name: str,
                             command: tuple[str, ...], log: Path) -> bool:
    """Reject the sandbox integration test's successful kernel-unavailable return."""
    if (name, phase, platform_name) != ("sotf-daw", "qa", "linux"):
        return False
    if not (command[:4] == ("cargo", "test", "-p", "sotf-host")
            and "external_plugin_isolation" in command):
        return False
    with log.open("r", encoding="utf-8", errors="replace") as stream:
        return any("SKIP: kernel sandbox unavailable" in line for line in stream)


class GateInterrupted(KeyboardInterrupt):
    def __init__(self, result: dict):
        super().__init__("gate interrupted")
        self.result = result


def test_inventory(log: Path) -> dict:
    """Count actually executed tests without treating zero-selection as coverage."""
    body = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", log.read_text(encoding="utf-8", errors="replace"))
    cargo = [tuple(map(int, match)) for match in re.findall(
        r"test result: (?:ok|FAILED)\.\s+(\d+) passed; (\d+) failed; (\d+) ignored;", body,
    )]
    nextest = re.findall(r"Summary\s+\[[^\]]+\]\s+\S+ tests? run:\s*([^\n]+)", body)
    nextest_counts = [
        {kind: int(match.group(1)) if (match := re.search(rf"\b(\d+) {kind}\b", line)) else 0
         for kind in ("passed", "failed", "skipped")}
        for line in nextest
    ]
    named = set(re.findall(r"^test\s+(\S+)\s+\.\.\.\s+ok$", body, re.M))
    named.update(re.findall(
        r"^\s*PASS\s+\[[^\]]+\]\s+(?:\([^)]*\)\s+)?([^\n]+?)\s*$", body, re.M,
    ))
    ignored_named = {name.rsplit("::", 1)[-1]: reason for name, reason in re.findall(
        r"^test\s+(\S+)\s+\.\.\.\s+ignored,\s+([^\n]+)$", body, re.M,
    )}
    return {"passed": sum(row[0] for row in cargo) + sum(row["passed"] for row in nextest_counts),
            "failed": sum(row[1] for row in cargo) + sum(row["failed"] for row in nextest_counts),
            "ignored": sum(row[2] for row in cargo) + sum(row["skipped"] for row in nextest_counts),
            "summaries": len(cargo) + len(nextest_counts), "named_passes": sorted(named),
            "ignored_named": ignored_named}


def requires_test_inventory(command: tuple[str, ...]) -> bool:
    if command[:2] == ("cargo", "test") or command[:2] == ("cargo", "nextest"):
        return True
    return command[:1] == ("just",) and len(command) > 1 and (
        command[1] in {"qa", "ntest", "test", "all", "qa-release-evidence"}
        or command[1].startswith(("test-", "qa-"))
    )


def required_test_names(name: str, command: tuple[str, ...]) -> set[str]:
    if name == "sotf-daw" and command == ("just", "qa-plugins-cross-format"):
        return {"nondefault_normalized_parameters_match_direct_typed_audio",
                "test_bridge_set_get_roundtrip_on_plugin"}
    return set()


def approved_ignored_inventory(name: str, command: tuple[str, ...], inventory: dict) -> bool:
    observed = {item.rsplit("::", 1)[-1] for item in inventory["named_passes"]}
    aud132_controls = {
        "aud132_live_source_tags_align_above_512_exact_and_noninteger_tones",
        "aud132_preserves_small_fft_and_512_pre_edit_full_output_controls",
    }
    return (name == "sotf-daw"
            and command[:6] == ("cargo", "test", "-p", "sotf-plugin-upmixer",
                                "--features", "onnx")
            and "--lib" in command and len(command) == 7
            and inventory["passed"] >= 147
            and len(observed) == inventory["passed"]
            and aud132_controls <= observed
            and inventory["ignored"] == len(REJECTED_UPMIXER_EXPERIMENTS)
            and inventory["ignored_named"] == REJECTED_UPMIXER_EXPERIMENTS)


def validator_issues(name: str, command: tuple[str, ...], workspace: Path,
                     revision: str | None) -> list[str]:
    if name != "gpui-toolkit" or command != ("just", "qa-release-evidence"):
        return []
    manifest = workspace / "target/qa/release-evidence.json"
    if not manifest.is_file():
        return ["strict GPUI release evidence manifest missing"]
    try:
        evidence = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError) as error:
        return [f"strict GPUI evidence unreadable: {error}"]
    if (evidence.get("schema_version") != 1
            or evidence.get("report_type") != "gpui-toolkit-release-evidence-manifest"
            or evidence.get("source", {}).get("revision") != revision
            or evidence.get("source", {}).get("dirty") is not False):
        return ["strict GPUI manifest schema, clean source, or revision differs"]
    artifacts = evidence.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return ["strict GPUI artifact inventory missing"]
    root = workspace.resolve()
    seen = set()
    for row in artifacts:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            return ["strict GPUI artifact row malformed"]
        path = (workspace / row["path"]).resolve()
        if path in seen or not path.is_relative_to(root) or not path.is_file():
            return [f"strict GPUI artifact missing, duplicate, or outside workspace: {row['path']}"]
        seen.add(path)
        with path.open("rb") as stream:
            actual_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if (path.stat().st_size != row.get("size_bytes")
                or actual_digest != row.get("sha256")):
            return [f"strict GPUI artifact size/hash differs: {row['path']}"]
        binding = row.get("embedded_source")
        if binding is not None and binding.get("matches_manifest_source") is not True:
            return [f"strict GPUI artifact source binding differs: {row['path']}"]
    return []


def run_workspace(name: str, phase: str, root: Path, output: Path, require_clean: bool,
                  platform_name: str | None = None, report: dict | None = None,
                  report_path: Path | None = None) -> dict:
    platform_name = platform_name or host_platform()
    workspace = root / name
    result = {"workspace": name, "status": "FAIL", "commands": [], "gates": []}
    if not (workspace / "Cargo.toml").is_file():
        result["error"] = "missing workspace Cargo.toml"
        return result
    if phase == "qa" and name == "gpui-toolkit" and platform_name != "macos":
        result["gates"].append({"name": "strict Metal release evidence", "status": "SKIPPED", "required_on": "macos"})
    before = source_state(root, [name], platform_name)
    result["source"] = before[name]
    issues = source_issues(before, before, require_clean)
    if issues:
        result["error"] = "; ".join(issues)
        return result
    for index, command in enumerate(commands_for(name, phase, platform_name)):
        if STOP:
            result["error"] = "interrupted before next command"
            raise GateInterrupted(result)
        log = output / f"{name}-{index:02d}.log"
        started = time.monotonic()
        print(f"[{name}] {' '.join(command)}", flush=True)
        entry = {"argv": list(command), "exit_code": None, "log": str(log)}
        if report is not None and report_path is not None:
            report["active_command"] = entry
            write_report(report_path, report)
        with log.open("w", encoding="utf-8") as stream:
            process = None
            code = 130
            try:
                if STOP:
                    entry["status"] = "INTERRUPTED_BEFORE_LAUNCH"
                    result["commands"].append(entry)
                    result["error"] = "gate interrupted before command launch"
                    raise GateInterrupted(result)
                command_env = cargo_environment(workspace)
                entry["toolchain"] = toolchain_identity(workspace, command_env)
                if STOP:
                    entry["status"] = "INTERRUPTED_BEFORE_LAUNCH"
                    result["commands"].append(entry)
                    result["error"] = "gate interrupted during toolchain inspection"
                    raise GateInterrupted(result)
                process = subprocess.Popen(command, cwd=workspace, stdout=stream,
                                           stderr=subprocess.STDOUT, start_new_session=True,
                                           env=command_env)
                entry["owned_pgid"] = process.pid
                if report is not None and report_path is not None:
                    write_report(report_path, report)
                heartbeat = time.monotonic()
                while not STOP:
                    try:
                        code = process.wait(timeout=1)
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() - heartbeat >= 30:
                            print(f"[{name}] command still running", flush=True)
                            heartbeat = time.monotonic()
            except OSError as error:
                stream.write(str(error) + "\n")
                code = 127
            finally:
                if process is not None:
                    entry["owned_group_cleanup"] = clean_group(process)
        entry.update({"exit_code": code,
                      "duration_seconds": round(time.monotonic() - started, 3)})
        result["commands"].append(entry)
        if report is not None:
            report.pop("active_command", None)
            if report_path is not None:
                report["active_workspace_result"] = result
                write_report(report_path, report)
        print(f"[{name}] exit {code}; log: {log}", flush=True)
        if STOP:
            entry["status"] = "INTERRUPTED"
            result["error"] = "gate interrupted"
            raise GateInterrupted(result)
        if not entry.get("owned_group_cleanup", {}).get("ok", code == 127):
            result["error"] = "owned command group cleanup failed"
            return result
        if code:
            print_failure_tail(log)
            result["error"] = "command failed; later commands were not run"
            return result
        if requires_test_inventory(command):
            entry["test_inventory"] = test_inventory(log)
            observed = {item.rsplit("::", 1)[-1] for item in entry["test_inventory"]["named_passes"]}
            missing = required_test_names(name, command) - observed
            if (entry["test_inventory"]["passed"] <= 0
                    or entry["test_inventory"]["failed"]
                    or (entry["test_inventory"]["ignored"]
                        and not approved_ignored_inventory(name, command, entry["test_inventory"]))
                    or not entry["test_inventory"]["named_passes"] or missing):
                result["error"] = f"test recipe lacks required named passes or has failed/ignored tests: {sorted(missing)}"
                return result
        validator_errors = validator_issues(name, command, workspace, before[name].get("revision"))
        if validator_errors:
            entry["validator_issues"] = validator_errors
            result["error"] = "; ".join(validator_errors)
            return result
        if name == "autoeq" and command[:2] == ("cargo", "metadata") and "--manifest-path" in command:
            inventory_issues = demo_metadata_issues(log)
            if inventory_issues:
                result["error"] = "; ".join(inventory_issues)
                return result
        if sandbox_coverage_skipped(name, phase, platform_name, command, log):
            print_failure_tail(log)
            result["error"] = "required Linux sandbox coverage skipped; later commands were not run"
            result["gates"].append({"name": "Linux external-plugin sandbox", "status": "FAIL",
                                    "reason": "kernel sandbox unavailable"})
            return result
    after = source_state(root, [name], platform_name)
    issues = source_issues(before, after, require_clean)
    if issues:
        result["error"] = "; ".join(issues)
    else:
        result["status"] = "PASS"
    return result


def interrupted(_signum, _frame):
    global STOP
    STOP = True


def cargo_environment(cwd: Path | None = None) -> dict[str, str]:
    """Return the child environment with Cargo's cache path made canonical."""
    environment = os.environ.copy()
    cargo_home = environment.get("CARGO_HOME")
    if cargo_home is None:
        home = environment.get("HOME") or str(Path.home())
        cargo_home = str(Path(home) / ".cargo")
    if cargo_home:
        path = Path(os.path.expanduser(cargo_home))
        if not path.is_absolute():
            path = (cwd or Path.cwd()) / path
        environment["CARGO_HOME"] = str(path.resolve())
    else:
        # An explicitly empty CARGO_HOME is meaningful to the caller; preserve it.
        environment["CARGO_HOME"] = cargo_home
    return environment


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
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    enable_subreaper()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", nargs="?", choices=("metadata", "check", "tests", "qa"))
    parser.add_argument("--phase", dest="phase_option", choices=("metadata", "check", "tests", "qa"))
    parser.add_argument("--platform", choices=("macos", "linux", "windows"))
    parser.add_argument("--workspace", action="append", choices=tuple(workspace_map()))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--evidence-root", type=Path,
                         help="parent directory for a timestamped report (defaults to target/release-qa)")
    parser.add_argument("--require-clean", action="store_true")
    args = parser.parse_args(argv)
    phase = args.phase_option or args.phase
    if not phase or (args.phase and args.phase_option and args.phase != args.phase_option):
        parser.error("specify exactly one phase")
    platform_name = args.platform or host_platform()
    if platform_name != host_platform():
        parser.error(f"requested platform {platform_name} differs from host {host_platform()}")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    evidence_root = (args.evidence_root or Path(os.environ.get(
        "SOTF_QA_EVIDENCE_ROOT", ROOT / "target" / "release-qa")))
    output = (args.output or evidence_root / f"{timestamp}-{phase}").resolve()
    output.mkdir(parents=True, exist_ok=False)
    names = args.workspace or list(workspace_map())
    report = {"started_at": timestamp, "phase": phase, "platform": platform_name,
              "toolchain": toolchain_identity(ROOT, cargo_environment(ROOT)),
              "cargo_net_offline": os.environ.get("CARGO_NET_OFFLINE"),
              "effective_cargo_home": cargo_environment(ROOT).get("CARGO_HOME"),
              "effective_cargo_target_dir": os.environ.get("CARGO_TARGET_DIR"),
              "status": "RUNNING", "require_clean": args.require_clean,
              "required_workspaces": names, "workspaces": [],
              "release_coverage": {"complete": False, "reason": "single-platform phase evidence; release requires separately verified platform and packaging lanes",
                                   "missing_platforms": [item for item in ("macos", "linux", "windows") if item != platform_name],
                                   "packaging": "not_run"}}
    if phase == "qa" and platform_name == "linux":
        report["release_coverage"]["physical_audio"] = (
            "INCOMPLETE: commands run in a disposable container without host audio devices"
        )
    path = output / "report.json"
    root_before = None
    manifest_before = None
    try:
        report["sources_before"] = source_state(ROOT, source_names(), platform_name)
        if args.require_clean:
            manifest = ROOT / "scripts" / "release" / "sources.json"
            manifest_before = manifest.read_bytes()
            _, _, pins = read_manifest(manifest)
            root_before = root_layout_status(ROOT, pins)
            report["root_status_before"] = root_before
            report["root_revision_before"] = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        write_report(path, report)
        issues = source_issues(report["sources_before"], report["sources_before"], args.require_clean)
        if args.require_clean:
            if root_before["missing"] or root_before["unexpected"]:
                issues.append("root checkout has missing or unexpected paths")
            for name, revision in pins.items():
                if report["sources_before"].get(name, {}).get("revision") != revision:
                    issues.append(f"{name}: source pin mismatch")
        if issues:
            report["errors"] = issues
        else:
            for name in names:
                report["active_workspace"] = name
                write_report(path, report)
                result = run_workspace(name, phase, ROOT, output, args.require_clean,
                                       platform_name, report, path)
                report["workspaces"].append(result)
                report.pop("active_workspace", None)
                report.pop("active_workspace_result", None)
                write_report(path, report)
    except GateInterrupted as error:
        report["workspaces"].append(error.result)
        report["error"] = str(error)
        report["interrupted"] = True
    except BaseException as error:
        report["error"] = f"{type(error).__name__}: {error}"
        if isinstance(error, KeyboardInterrupt):
            report["interrupted"] = True
    finally:
        try:
            report["sources_after"] = source_state(ROOT, source_names(), platform_name)
            report["source_issues"] = source_issues(report.get("sources_before", {}), report["sources_after"], args.require_clean)
            if args.require_clean and root_before is not None:
                manifest = ROOT / "scripts" / "release" / "sources.json"
                if manifest.read_bytes() != manifest_before:
                    report["source_issues"].append("source manifest changed")
                _, _, pins = read_manifest(manifest)
                report["root_status_after"] = root_layout_status(ROOT, pins)
                if report["root_status_after"] != root_before:
                    report["source_issues"].append("root checkout status changed")
                report["root_revision_after"] = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
                if report["root_revision_after"] != report["root_revision_before"]:
                    report["source_issues"].append("root revision changed")
        except BaseException as error:
            report["source_issues"] = [f"final source snapshot failed: {type(error).__name__}: {error}"]
        passed = len(report["workspaces"]) == len(names) and all(
            item["status"] == "PASS" for item in report["workspaces"])
        report["status"] = "PASS" if passed and not report["source_issues"] and not report.get("error") else "FAIL"
        write_report(path, report)
    print(f"{report['status']}: {path}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
