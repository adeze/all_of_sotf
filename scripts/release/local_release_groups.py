#!/usr/bin/env python3
"""Plan or run offline Cargo build stages for the local SotF release groups.

This deliberately stops before packaging, signing, installation, SSH, or
publishing. Existing package routes are recorded in the contract so missing
profile/architecture routes remain visible instead of being reported as a
complete release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts/release"))
from scripts.release.qa import (
    cargo_environment,
    host_platform,
    source_issues,
    source_state,
    toolchain_identity,
)
from scripts.release.checkout_sources import read_manifest, root_layout_status
from scripts.buildbot.ci_matrix import workspace_map
from scripts.release.process_supervision import clean_group, enable_subreaper

STOP = False
TARGETS = {
    "macos-arm64": {
        "os": "macos",
        "architecture": "aarch64",
        "triple": "aarch64-apple-darwin",
        "host_system": "Darwin",
        "host_machine": ("arm64", "aarch64"),
    },
    "linux-arm64": {
        "os": "linux",
        "architecture": "aarch64",
        "triple": "aarch64-unknown-linux-gnu",
        "host_system": "Linux",
        "host_machine": ("aarch64", "arm64"),
    },
}

REQUIRED_GROUPS = ("desktop", "tui", "roomeq", "plugins", "systemwide")
MANDATORY_GROUPS_BY_TARGET = {
    "macos-arm64": REQUIRED_GROUPS,
    "linux-arm64": ("desktop", "tui", "roomeq", "plugins"),
}
MACOS_MINIMUM_VERSION = "15.0"


def _read_nih_features() -> tuple[str, ...]:
    justfile = ROOT / "sotf-daw/crates/sotf-plugins/crates/plugins-nih/Justfile"
    match = re.search(r'^NIH_FEATURES\s*:=\s*"([^"]+)"\s*$', justfile.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise RuntimeError(f"Could not find canonical NIH_FEATURES in {justfile}")
    return tuple(match.group(1).split())


NIH_FEATURES = _read_nih_features()


def _step(cwd: str, args: Iterable[str], output: str, label: str) -> dict[str, object]:
    return {"label": label, "cwd": cwd, "argv": list(args), "output": output}


def build_contract() -> dict[str, object]:
    """Return the unified target/group contract and current packaging routes."""
    groups: dict[str, dict[str, dict[str, object]]] = {}
    for target_name, target in TARGETS.items():
        triple = str(target["triple"])
        is_macos = target["os"] == "macos"
        sotf_features = ["--features", "hal,onnx"] if is_macos else []
        sotf_manifest = ["--manifest-path", "sotf/Cargo.toml"]
        binary_root = f"sotf/target/{triple}/dist"
        group_map: dict[str, dict[str, object]] = {
            "desktop": {
                "required": True,
                "build_only": [
                    _step(".", ["cargo", "build", *sotf_manifest, "--profile", "dist", "--target", triple, "-p", "sotf-gpui", "--bin", "sotf-desktop", *sotf_features], f"{binary_root}/sotf-desktop", "desktop-cargo"),
                ],
                "expected_distribution_artifacts": (
                    ["sotf/target/dist/sotf-desktop-<version>-macos-arm64.dmg"]
                    if is_macos else [
                        "sotf/dist/sotf-desktop-<version>-linux-arm64.tar.gz",
                        "sotf/dist/sotf-desktop-<version>-linux-arm64.AppImage",
                    ]
                ),
                "existing_package_route": (
                    "sotf/scripts/build-dmg-sotf.sh --arch arm64 --binary <dist binary>"
                    if is_macos else "sotf/scripts/build-linux.sh --appimage (native ARM64)"
                ),
                "package_gate": "pending; package command is outside build-only mode",
            },
            "tui": {
                "required": True,
                "build_only": [
                    _step(".", ["cargo", "build", *sotf_manifest, "--profile", "dist", "--target", triple, "-p", "sotf-tui", *sotf_features], f"{binary_root}/sotf-tui", "tui-cargo"),
                ],
                "expected_distribution_artifacts": (
                    ["sotf/dist/sotf-tui-<version>-macos-arm64"]
                    if is_macos else ["sotf/dist/sotf-desktop-<version>-linux-arm64.tar.gz (contains sotf-tui)"]
                ),
                "existing_package_route": (
                    "local orchestrator copies the dist TUI binary"
                    if is_macos else "sotf/scripts/build-linux.sh --appimage stages desktop and TUI together"
                ),
                "package_gate": "pending; macOS TUI has no installer bundle route",
            },
            "roomeq": {
                "required": True,
                "build_only": [
                    _step(".", ["cargo", "build", "--manifest-path", "autoeq/Cargo.toml", "--profile", "dist", "--target", triple, "--features", "cli", "--bin", "roomeq"], f"autoeq/target/{triple}/dist/roomeq", "roomeq-cargo"),
                ],
                "expected_distribution_artifacts": [f"autoeq/target/{triple}/dist/roomeq"],
                "existing_package_route": "autoeq: just dist-roomeq (dist profile; native target only)",
                "package_gate": "binary is the release artifact; stage/copy and runtime validation pending",
            },
            "plugins": {
                "required": True,
                "build_only": [
                    _step(
                        ".",
                        ["cargo", "build", "--manifest-path", "sotf-daw/Cargo.toml", "--profile", "dist", "--target", triple, "-p", "plugins-nih", "--no-default-features", "--features", feature],
                        f"sotf-daw/target/{triple}/dist/libplugins_nih.{ 'dylib' if is_macos else 'so' }",
                        f"plugin-{feature}",
                    )
                    for feature in NIH_FEATURES
                ],
                "expected_distribution_artifacts": (
                    ["sotf-daw/dist/vst3/*.vst3", "sotf-daw/dist/clap/*.clap"]
                    if is_macos else ["sotf-daw/dist/vst3-linux/*.vst3", "sotf-daw/dist/clap-linux/*.clap"]
                ),
                "existing_package_route": (
                    "sotf-daw: just prod-vst3 && just prod-clap (currently --release, not dist)"
                    if is_macos else "sotf-daw: just prod-plugin-formats-linux (native x86_64-linux/aarch64-linux bundle path; currently --release, not dist); native qualification: scripts/release/nih_native_artifact_check.py --local --evidence-root <outside checkout>"
                ),
                "validators": ["pluginval", "clap-validator"],
                "package_gate": "pending; build-only emits raw feature libraries, not validated plugin bundles",
            },
            "systemwide": {
                "required": is_macos,
                "deferred": not is_macos,
                "deferred_reason": (
                    None if is_macos else
                    "Linux systemwide is outside the mandatory release scope by explicit deferral. The daemon has cpal input capture and a NullDriver status-only fallback, but Linux system-audio routing and installed-product acceptance are unqualified. Daemon builds/tests remain separately available, and no distribution package is required here."
                ),
                "build_only": ([
                    _step(
                        ".",
                        ["cargo", "build", "--manifest-path", "sotf-systemwide/Cargo.toml", "--profile", "dist", "--target", triple, "-p", "sotf-daemon", *( ["--features", "hal"] if is_macos else [] )],
                        f"sotf-systemwide/target/{triple}/dist/sotf-daemon",
                        "systemwide-daemon-cargo",
                    ),
                ] if is_macos else []),
                "expected_distribution_artifacts": (
                    ["sotf-systemwide/target/daemon-dmg/sotf-systemwide-<version>-macos-arm64.pkg"]
                    if is_macos else []
                ),
                "required_payload_stages": (
                    [
                        {
                            "name": "menu-bar app",
                            "existing_recipe": "sotf-systemwide: just dist-systemwide",
                            "raw_output": "sotf-systemwide/target/dist/sotf-systemwide",
                            "build_only_status": "unavailable; SwiftPM output would write into the source checkout",
                        },
                        {
                            "name": "HAL driver bundle",
                            "existing_recipe": "sotf-systemwide: just dist-hal-driver",
                            "raw_output": "sotf-systemwide/target/dist/SotFHAL.driver",
                            "build_only_status": "unavailable; recipe ad-hoc signs the driver payload",
                        },
                    ]
                    if is_macos else [
                        {
                            "name": "Linux systemwide distribution package",
                            "existing_recipe": None,
                            "build_only_status": "deferred; Linux systemwide is not a mandatory release group and has no ARM64 distribution recipe",
                        }
                    ]
                ),
                "existing_package_route": (
                    "sotf-systemwide: just build-systemwide; package filename and host architectures follow all required payload binaries (arm64 for this release); path builds/adhoc-signs HAL payload"
                    if is_macos else None
                ),
                "package_gate": (
                    "pending; expected first-target artifact is macos-arm64.pkg; package architecture is derived from required payload binaries; build-only includes daemon Cargo only, with Swift app/HAL stages unavailable"
                    if is_macos else "deferred; Linux systemwide is outside the mandatory release scope and has no ARM64 distribution-package recipe"
                ),
            },
        }
        groups[target_name] = group_map

    return {
        "schema": 1,
        "status": "qualification in progress",
        "distribution": "local only; GitHub sync deferred",
        "execution_policy": {
            "build_mode": "local Cargo only; no SSH, signing, installation, or publishing",
            "dry_run": "plans every required group without executing commands",
            "complete_release_requires": "all expected distribution artifacts and required validators; build-only is never release-complete",
        },
        "minimum_os": {"macos": MACOS_MINIMUM_VERSION, "linux": "Ubuntu 24.04 qualification baseline; runtime acceptance pending"},
        "required_groups": list(REQUIRED_GROUPS),
        "required_groups_by_target": {
            target_name: list(MANDATORY_GROUPS_BY_TARGET[target_name])
            for target_name in TARGETS
        },
        "deferred_groups_by_target": {
            target_name: [group for group in REQUIRED_GROUPS if group not in MANDATORY_GROUPS_BY_TARGET[target_name]]
            for target_name in TARGETS
        },
        "deferred_platforms": ["windows", "android", "ios"],
        "targets": groups,
        "notes": [
            "macOS desktop/TUI features follow the current local build orchestrator hal,onnx selection; Linux follows build-linux.sh native defaults (ONNX disabled in its release builder).",
            "RoomEQ uses the existing dist profile and CLI feature. Other AutoEQ binaries are outside the RoomEQ required-artifact mapping.",
            "Plugin Cargo build-only compiles each NIH feature independently in dist profile. Existing plugin packaging recipes use release profile and cannot be substituted as dist qualification.",
            "Systemwide macOS requires daemon, Swift menu-bar app, and Swift HAL driver payloads. Build-only covers daemon Cargo alone; Swift/package steps are explicitly unavailable pending isolated no-sign outputs. Package filenames and installer host architectures are derived from the actual required payload binaries; the first-target artifact contract uses macos-arm64. Linux systemwide remains deferred by the release scope; cpal input capture exists, but Linux system-audio routing and installed-product acceptance are unqualified. Linux daemon builds/tests remain separately available, but no Linux systemwide distribution package is required or produced here.",
            "Native macOS artifact qualification remains pending: owned executable minimum versions must not exceed 15.0; older minimum versions on compatible dependency libraries are allowed.",
        ],
    }


CONTRACT = build_contract()


@dataclass
class BuildResult:
    target: str
    group: str
    step: str
    status: str
    detail: str
    artifact: str | None = None
    sha256: str | None = None
    log: str | None = None
    exit_code: int | None = None
    owned_group_cleanup: dict[str, object] | None = None
    argv: list[str] | None = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _host_matches(target: str, system: str, machine: str) -> bool:
    config = TARGETS[target]
    return system == config["host_system"] and machine in config["host_machine"]


def _snapshot_provenance(root: Path) -> dict[str, object]:
    manifest_path = root / "scripts/release/sources.json"
    _server, _owner, pins = read_manifest(manifest_path)
    names = sorted(pins)
    sources = source_state(root, names, host_platform())
    # version_snapshot adds a wall-clock capture time for standalone QA receipts.
    # It is not source provenance, so exclude only that volatile field from the
    # before/after equality check while retaining all source/toolchain/lock data.
    for source in sources.values():
        source.pop("captured_at", None)
    issues = source_issues(sources, sources, require_clean=True)
    for name, pinned_revision in pins.items():
        observed = sources.get(name, {}).get("revision")
        if observed != pinned_revision:
            issues.append(f"{name}: source revision does not match release pin")
    layout = root_layout_status(root, pins)
    if layout["missing"]:
        issues.extend(f"root sibling layout: {problem}" for problem in layout["missing"])
    if layout["unexpected"]:
        issues.extend(f"root checkout is dirty: {problem}" for problem in layout["unexpected"])
    root_revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    return {
        "root_revision": root_revision,
        "sources_manifest_sha256": _sha256(manifest_path),
        "sources": sources,
        "root_layout": layout,
        "issues": issues,
    }


def _actual_cargo_output(target_dir: Path, relative_output: str) -> Path:
    parts = Path(relative_output).parts
    try:
        target_index = parts.index("target")
    except ValueError as error:
        raise ValueError(f"expected output must include a target directory: {relative_output}") from error
    return target_dir.joinpath(*parts[target_index + 1 :])


def interrupted(_signum: int, _frame: object) -> None:
    global STOP
    STOP = True


def run_build_only(
    targets: Iterable[str],
    evidence_root: Path,
    *,
    popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    cleanup: Callable[[subprocess.Popen[bytes]], dict[str, object]] = clean_group,
    host_system: str | None = None,
    host_machine: str | None = None,
    stop_check: Callable[[], bool] | None = None,
) -> tuple[list[BuildResult], bool, dict[str, object]]:
    """Run fresh, offline Cargo stages only; return results and completeness.

    The boolean is true only when all groups produced their distribution
    artifacts and validators. Cargo-only completion therefore returns false.
    """
    system = host_system or platform.system()
    machine = host_machine or platform.machine()
    results: list[BuildResult] = []
    all_complete = True
    stop_check = stop_check or (lambda: STOP)
    evidence_root = evidence_root.expanduser().resolve()
    root_resolved = ROOT.resolve()
    if evidence_root == root_resolved or root_resolved in evidence_root.parents:
        raise ValueError("evidence directory must be outside the source checkout")
    evidence_root.mkdir(parents=True, exist_ok=False)
    logs_root = evidence_root / "logs"
    artifacts_root = evidence_root / "artifacts"
    target_dir = evidence_root / "cargo-target"
    logs_root.mkdir()
    artifacts_root.mkdir()
    target_dir.mkdir()

    environment = cargo_environment(ROOT)
    environment["CARGO_TARGET_DIR"] = str(target_dir)
    try:
        sources_before = _snapshot_provenance(ROOT)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise RuntimeError(f"could not record source/lock provenance: {error}") from error
    source_problems = list(sources_before["issues"])
    if source_problems:
        raise RuntimeError("build-only requires a clean, pin-matched source snapshot: " + "; ".join(source_problems))
    report: dict[str, object] = {
        "schema": 1,
        "mode": "build-only",
        "status": "RUNNING",
        "release_complete": False,
        "source_root": str(ROOT),
        "required_groups_by_target": CONTRACT["required_groups_by_target"],
        "deferred_groups_by_target": CONTRACT["deferred_groups_by_target"],
        "toolchain": None,
        "cargo_environment": {
            "cargo_home": environment.get("CARGO_HOME"),
            "cargo_target_dir": environment["CARGO_TARGET_DIR"],
            "cargo_net_offline": environment.get("CARGO_NET_OFFLINE"),
        },
        "sources_before": sources_before,
        "results": [],
    }
    report_path = evidence_root / "local-release-groups-report.json"

    def save_report() -> None:
        temporary = report_path.with_suffix(".pending")
        temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        temporary.replace(report_path)

    save_report()

    if stop_check():
        report["status"] = "INTERRUPTED"
        report["sources_after"] = _snapshot_provenance(ROOT)
        report["source_snapshot_unchanged"] = sources_before == report["sources_after"]
        save_report()
        return results, False, report

    # Tool identity probes execute rustc and cargo. Honor STOP before starting
    # them and once more before any Cargo build process is launched.
    try:
        toolchain = toolchain_identity(ROOT, environment)
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        report["status"] = "FAILED"
        report["setup_error"] = f"could not identify local toolchain: {error}"
        report["sources_after"] = _snapshot_provenance(ROOT)
        report["source_snapshot_unchanged"] = sources_before == report["sources_after"]
        save_report()
        raise RuntimeError(report["setup_error"]) from error
    report["toolchain"] = toolchain
    save_report()
    if stop_check():
        report["status"] = "INTERRUPTED"
        report["sources_after"] = _snapshot_provenance(ROOT)
        report["source_snapshot_unchanged"] = sources_before == report["sources_after"]
        save_report()
        return results, False, report

    for target in targets:
        if target not in TARGETS:
            results.append(BuildResult(target, "*", "host-check", "failed", "unsupported target"))
            all_complete = False
            continue
        if not _host_matches(target, system, machine):
            for group in MANDATORY_GROUPS_BY_TARGET[target]:
                results.append(BuildResult(target, group, "host-check", "unavailable", f"requires {TARGETS[target]['host_system']} {TARGETS[target]['host_machine']}; host is {system} {machine}"))
            all_complete = False
            continue

        for group in MANDATORY_GROUPS_BY_TARGET[target]:
            spec = CONTRACT["targets"][target][group]  # type: ignore[index]
            for item in spec["build_only"]:
                if stop_check():
                    results.append(BuildResult(target, group, str(item["label"]), "interrupted", "stop requested before process launch"))
                    all_complete = False
                    report["results"] = [result.__dict__ for result in results]
                    report["status"] = "INTERRUPTED"
                    sources_after = _snapshot_provenance(ROOT)
                    report["sources_after"] = sources_after
                    report["source_snapshot_unchanged"] = sources_before == sources_after
                    save_report()
                    return results, False, report

                log_path = logs_root / f"{target}-{group}-{item['label']}.log"
                actual_output = _actual_cargo_output(target_dir, str(item["output"]))
                # The external target tree belongs to this run, so removing a
                # same-path plugin output between feature builds cannot touch
                # any caller-owned or source-tree artifact.
                actual_output.unlink(missing_ok=True)
                command = [str(part) for part in item["argv"]]
                command.extend(["--target-dir", str(target_dir)])
                started = time.monotonic()
                child: subprocess.Popen[bytes] | None = None
                exit_code = 127
                cleanup_report: dict[str, object] = {"ok": True, "remaining": [], "errors": []}
                report["active_command"] = {
                    "target": target,
                    "group": group,
                    "step": item["label"],
                    "argv": command,
                    "cwd": str(ROOT / str(item["cwd"])),
                    "log": str(log_path),
                    "status": "RUNNING",
                }
                save_report()
                try:
                    with log_path.open("wb") as log_stream:
                        if stop_check():
                            raise InterruptedError("stop requested before process launch")
                        child = popen_factory(
                            command,
                            cwd=ROOT / str(item["cwd"]),
                            env=environment,
                            stdout=log_stream,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                        )
                        report["active_command"]["owned_pgid"] = child.pid  # type: ignore[index]
                        save_report()
                        while not stop_check():
                            try:
                                exit_code = child.wait(timeout=1)
                                break
                            except subprocess.TimeoutExpired:
                                continue
                        if stop_check():
                            exit_code = child.poll() if child.poll() is not None else 130
                except InterruptedError as error:
                    log_path.write_text(str(error) + "\n", encoding="utf-8")
                    results.append(BuildResult(target, group, str(item["label"]), "interrupted", str(error), log=str(log_path)))
                    all_complete = False
                    report.pop("active_command", None)
                    report["results"] = [result.__dict__ for result in results]
                    report["status"] = "INTERRUPTED"
                    sources_after = _snapshot_provenance(ROOT)
                    report["sources_after"] = sources_after
                    report["source_snapshot_unchanged"] = sources_before == sources_after
                    save_report()
                    return results, False, report
                except OSError as error:
                    with log_path.open("a", encoding="utf-8") as stream:
                        stream.write(f"\nCould not launch Cargo command: {error}\n")
                finally:
                    if child is not None:
                        cleanup_report = cleanup(child)

                duration = round(time.monotonic() - started, 3)
                report.pop("active_command", None)
                if child is not None and (stop_check() or exit_code == 130):
                    results.append(BuildResult(target, group, str(item["label"]), "interrupted", f"Cargo interrupted after {duration}s", log=str(log_path), exit_code=exit_code, owned_group_cleanup=cleanup_report, argv=command))
                    all_complete = False
                    report["status"] = "INTERRUPTED"
                    sources_after = _snapshot_provenance(ROOT)
                    report["sources_after"] = sources_after
                    report["source_snapshot_unchanged"] = sources_before == sources_after
                    report["results"] = [result.__dict__ for result in results]
                    save_report()
                    return results, False, report

                if child is None or exit_code != 0 or not cleanup_report.get("ok", False):
                    detail = f"Cargo exit {exit_code}; duration {duration}s; owned process cleanup={cleanup_report.get('ok')}"
                    results.append(BuildResult(target, group, str(item["label"]), "failed", detail, log=str(log_path), exit_code=exit_code, owned_group_cleanup=cleanup_report, argv=command if child is not None else None))
                    all_complete = False
                    continue

                if not actual_output.is_file() or actual_output.stat().st_size == 0:
                    results.append(BuildResult(target, group, str(item["label"]), "failed", f"Cargo succeeded but fresh external-target output is missing/empty: {actual_output}", log=str(log_path), exit_code=exit_code, owned_group_cleanup=cleanup_report, argv=command))
                    all_complete = False
                    continue

                # Keep per-feature plugin outputs distinct because Cargo writes
                # the same cdylib filename for each one-feature build.
                staged = artifacts_root / target / group
                if group == "plugins":
                    staged = staged / str(item["label"])
                staged.mkdir(parents=True, exist_ok=True)
                staged_file = staged / actual_output.name
                shutil.copy2(actual_output, staged_file)
                results.append(BuildResult(target, group, str(item["label"]), "built", f"fresh offline Cargo output; packaging/validation pending; duration {duration}s", str(staged_file), _sha256(staged_file), str(log_path), exit_code, cleanup_report, command))
                report["results"] = [result.__dict__ for result in results]
                save_report()

            # Cargo output is not equivalent to the required package/validator
            # gates. Preserve these as pending/unavailable in every report.
            package_gate = str(spec["package_gate"])
            if package_gate.startswith("unavailable"):
                results.append(BuildResult(target, group, "package", "unavailable", package_gate))
            else:
                results.append(BuildResult(target, group, "package", "pending", package_gate))
            all_complete = False
            report["results"] = [result.__dict__ for result in results]
            save_report()

    sources_after = _snapshot_provenance(ROOT)
    source_snapshot_unchanged = sources_before == sources_after
    if not source_snapshot_unchanged:
        results.append(BuildResult("*", "*", "source-integrity", "failed", "source/lock provenance changed during the run"))
        all_complete = False
    report.pop("active_command", None)
    report["sources_after"] = sources_after
    report["source_snapshot_unchanged"] = source_snapshot_unchanged
    report["results"] = [result.__dict__ for result in results]
    report["release_complete"] = all_complete
    report["status"] = "PASS" if all_complete else "INCOMPLETE"
    save_report()
    return results, all_complete, report


def render_plan(targets: Iterable[str]) -> dict[str, object]:
    selected: dict[str, object] = {}
    for target in targets:
        if target not in TARGETS:
            raise ValueError(f"unknown target: {target}")
        selected[target] = CONTRACT["targets"][target]  # type: ignore[index]
    return {**CONTRACT, "targets": selected, "plan_only": True}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("all", *TARGETS), default="all")
    parser.add_argument("--dry-run", action="store_true", help="print the mandatory-group plan; this is the default")
    parser.add_argument("--build-only", action="store_true", help="run fresh Cargo builds offline; never package, sign, install, SSH, or publish")
    parser.add_argument("--evidence-dir", type=Path, help="where to write Cargo logs, staged raw binaries, and a JSON report")
    parser.add_argument("--contract-out", type=Path, help="write the unified release contract JSON here")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.dry_run and args.build_only:
        print("--dry-run and --build-only are mutually exclusive", file=sys.stderr)
        return 2
    targets = list(TARGETS) if args.platform == "all" else [args.platform]

    contract_data = json.dumps(CONTRACT, indent=2) + "\n"
    if args.contract_out:
        args.contract_out.parent.mkdir(parents=True, exist_ok=True)
        args.contract_out.write_text(contract_data, encoding="utf-8")

    if not args.build_only:
        print(json.dumps(render_plan(targets), indent=2))
        return 0
    if args.evidence_dir is None:
        print("--build-only requires --evidence-dir", file=sys.stderr)
        return 2
    if not args.evidence_dir.is_absolute():
        print("--evidence-dir must be an absolute path outside the source checkout", file=sys.stderr)
        return 2

    try:
        signal.signal(signal.SIGINT, interrupted)
        signal.signal(signal.SIGTERM, interrupted)
        enable_subreaper()
        _results, complete, report = run_build_only(targets, args.evidence_dir)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"build-only setup failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0 if complete else 3


if __name__ == "__main__":
    raise SystemExit(main())
