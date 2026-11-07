#!/usr/bin/env python3
"""Package verified dist-profile NIH feature outputs without rebuilding them."""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.workspaces import source_names, vendor_names
from scripts.release import qa
from scripts.release.checkout_sources import read_manifest, root_layout_status
from scripts.release.local_release_groups import CONTRACT, NIH_FEATURES, TARGETS
from scripts.release.nih_native_artifact_check import assert_elf_architecture

SCHEMA = 1
MACOS_ARM64_CPU_TYPE = 0x0100000C
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()



# These notices cover the built-in NN model and the four fonts embedded by the
# convolution egui editor. They do not establish full model/Cargo/font clearance.
NOTICE_SPECS = (
    ("sotf-daw/assets/third-party/nnnoiseless/MODEL_NOTICES.txt", "nnnoiseless/MODEL_NOTICES.txt", None),
    ("sotf-daw/assets/third-party/nnnoiseless/nnnoiseless-COPYING.txt", "nnnoiseless/nnnoiseless-COPYING.txt", None),
    ("sotf-daw/assets/third-party/egui-default-fonts/Hack-Regular.txt", "egui-default-fonts/Hack-Regular.txt", ("convolution",)),
    ("sotf-daw/assets/third-party/egui-default-fonts/emoji-icon-font-mit-license.txt", "egui-default-fonts/emoji-icon-font-mit-license.txt", ("convolution",)),
    ("sotf-daw/assets/third-party/egui-default-fonts/OFL.txt", "egui-default-fonts/OFL.txt", ("convolution",)),
    ("sotf-daw/assets/third-party/egui-default-fonts/UFL.txt", "egui-default-fonts/UFL.txt", ("convolution",)),
)


def notice_sources() -> list[dict[str, Any]]:
    sources = []
    for relative, destination, features in NOTICE_SPECS:
        path = ROOT / relative
        _no_symlink_components(path)
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"notice source is missing or empty: {path}")
        sources.append({"source_path": str(path), "path": destination,
                        "features": list(features) if features is not None else None,
                        "size": path.stat().st_size, "sha256": file_sha256(path)})
    return sources


def verify_notice_sources(sources: list[dict[str, Any]]) -> None:
    if notice_sources() != sources:
        raise ValueError("notice source changed during packaging or validation")


def notice_layout(target: str, sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if target not in ("macos-arm64", "linux-arm64"):
        raise ValueError(f"unsupported notice target: {target}")
    suffix = "-linux" if target == "linux-arm64" else ""
    layout = {}
    for source in sources:
        shared = f"artifacts/sotf-daw/dist/clap{suffix}/notices/{source['path']}"
        if target == "linux-arm64":
            layout[shared] = source
        for feature in NIH_FEATURES:
            if source["features"] is not None and feature not in source["features"]:
                continue
            destination = (f"artifacts/sotf-daw/dist/vst3{suffix}/{vst3_name(feature)}.vst3/"
                           f"Contents/Resources/third-party-notices/{source['path']}")
            layout[destination] = source
            if target == "macos-arm64":
                clap_notice = (f"artifacts/sotf-daw/dist/clap/{feature_base(feature)}.clap/"
                               f"Contents/Resources/third-party-notices/{source['path']}")
                layout[clap_notice] = source
    return layout


def stage_notices(output_root: Path, target: str, sources: list[dict[str, Any]]) -> None:
    verify_notice_sources(sources)
    for relative, source in notice_layout(target, sources).items():
        destination = output_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source["source_path"], destination)
        current_source = Path(source["source_path"])
        if (current_source.stat().st_size != source["size"]
                or file_sha256(current_source) != source["sha256"]):
            raise ValueError("notice source changed during packaging")
        if (destination.stat().st_size != source["size"]
                or file_sha256(destination) != source["sha256"]):
            raise ValueError(f"copied notice bytes differ from source: {destination}")
    verify_notice_sources(sources)


def notice_receipt(target: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
    layout = notice_layout(target, sources)
    return {
        "scope": "default NN model plus convolution egui embedded fonts only",
        "full_clearance": False,
        "sources": sources,
        "file_count": len(layout),
        "files": [{"path": relative, "source_path": source["source_path"],
                   "sha256": source["sha256"], "size": source["size"]}
                  for relative, source in sorted(layout.items())],
    }



def expected_artifact_paths(target: str, sources: list[dict[str, Any]]) -> set[str]:
    linux = target == "linux-arm64"
    suffix = "-linux" if linux else ""
    expected = set(notice_layout(target, sources))
    for feature in NIH_FEATURES:
        base, display = feature_base(feature), vst3_name(feature)
        clap = f"artifacts/sotf-daw/dist/clap{suffix}/{base}.clap"
        if linux:
            expected.add(clap)
        else:
            expected.update({f"{clap}/Contents/MacOS/{base}", f"{clap}/Contents/Info.plist"})
        bundle = f"artifacts/sotf-daw/dist/vst3{suffix}/{display}.vst3/Contents"
        expected.add(f"{bundle}/aarch64-linux/{display}.so" if linux else f"{bundle}/MacOS/{base}")
        if not linux:
            expected.add(f"{bundle}/Info.plist")
    return expected


def validate_notice_inventory(receipt: dict[str, Any], target: str,
                              physical_files: dict[str, Path]) -> set[str]:
    sources = notice_sources()
    expected_receipt = notice_receipt(target, sources)
    if receipt.get("third_party_notices") != expected_receipt:
        raise ValueError("pack receipt notice provenance/layout differs from canonical sources")
    layout = notice_layout(target, sources)
    for relative, source in layout.items():
        path = physical_files.get(relative)
        if path is None or not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"packaged notice is missing or empty: {relative}")
        if path.stat().st_size != source["size"] or file_sha256(path) != source["sha256"]:
            raise ValueError(f"packaged notice bytes differ from canonical source: {relative}")
    verify_notice_sources(sources)
    return set(layout)


def feature_base(feature: str) -> str:
    return "sotf_" + feature.replace("-", "_")


def vst3_name(feature: str) -> str:
    words = feature.replace("-", " ").split()
    return "SOTF " + " ".join(word[0].upper() + word[1:] for word in words)


def _no_symlink_components(path: Path, *, beneath: Path | None = None) -> None:
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raise ValueError(f"path must be absolute: {path}")
    if ".." in raw.parts:
        raise ValueError(f"parent traversal is not allowed in path: {path}")
    absolute = raw
    if beneath is None:
        current = Path(absolute.anchor)
        for component in absolute.parts[1:]:
            current /= component
            if current.is_symlink():
                raise ValueError(f"symlink path component is not allowed: {current}")
        return
    anchor = Path(beneath)
    if anchor.is_symlink():
        raise ValueError(f"symlink evidence root is not allowed: {anchor}")
    try:
        relative = absolute.relative_to(anchor)
    except ValueError as error:
        raise ValueError(f"path is outside its evidence tree: {absolute}") from error
    current = anchor
    for component in relative.parts:
        current /= component
        if current.is_symlink():
            raise ValueError(f"symlink path component is not allowed: {current}")


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def assert_macho_arm64(path: Path) -> None:
    """Require a thin ARM64 Mach-O dynamic library for this target."""
    try:
        with path.open("rb") as stream:
            header = stream.read(16)
    except OSError as error:
        raise ValueError(f"cannot read imported plugin binary {path}: {error}") from error
    formats = {
        b"\xcf\xfa\xed\xfe": "<",  # MH_MAGIC_64, little endian
        b"\xfe\xed\xfa\xcf": ">",  # MH_CIGAM_64, big endian
    }
    endian = formats.get(header[:4])
    if endian is None or len(header) < 16:
        raise ValueError(f"imported plugin is not a thin 64-bit Mach-O binary: {path}")
    cpu_type = struct.unpack(f"{endian}I", header[4:8])[0]
    if cpu_type != MACOS_ARM64_CPU_TYPE:
        raise ValueError(f"imported plugin is not an ARM64 Mach-O binary: {path}")
    file_type = struct.unpack(f"{endian}I", header[12:16])[0]
    if file_type != 6:  # MH_DYLIB
        raise ValueError(f"imported plugin is not a Mach-O dynamic library: {path}")


def _current_provenance(root: Path, target: str) -> dict[str, Any]:
    platform_name = "macos" if target == "macos-arm64" else "linux"
    manifest_path = root / "scripts/release/sources.json"
    _server, _owner, pins = read_manifest(manifest_path)
    names = sorted(source_names())
    sources = qa.source_state(root, names, platform_name)
    issues = qa.source_issues(sources, sources, require_clean=True)
    for name, revision in pins.items():
        source = sources.get(name, {})
        if source.get("revision") != revision:
            issues.append(f"{name}: source revision does not match release pin")
        lock_hash = source.get("lock_sha256")
        if name not in vendor_names() and (not isinstance(lock_hash, str) or not SHA256_RE.fullmatch(lock_hash)):
            issues.append(f"{name}: canonical Cargo.lock hash is missing")
        if name == "autoeq" and (
            not isinstance(source.get("nested_lock_sha256"), str)
            or not SHA256_RE.fullmatch(source["nested_lock_sha256"])
        ):
            issues.append("autoeq: nested GPUI examples Cargo.lock hash is missing")
    layout = root_layout_status(root, pins)
    if layout["missing"]:
        issues.extend(f"root sibling layout: {problem}" for problem in layout["missing"])
    if layout["unexpected"]:
        issues.extend(f"root checkout is dirty: {problem}" for problem in layout["unexpected"])
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    return {
        "root_revision": revision,
        "sources_manifest_sha256": file_sha256(manifest_path),
        "sources": sources,
        "root_layout": layout,
        "issues": issues,
    }


def _validate_source_report(
    report: dict[str, Any], report_path: Path, target: str,
    *, root: Path = ROOT, current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence_root = report_path.parent.resolve(strict=True)
    if _is_within(evidence_root, root.resolve()):
        raise ValueError("input build receipt and artifacts must be outside the source checkout")
    source_root = report.get("source_root")
    if not isinstance(source_root, str) or Path(source_root).resolve() != root.resolve():
        raise ValueError("build receipt source_root does not match this checkout")
    before = report.get("sources_before")
    after = report.get("sources_after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("build receipt is missing before/after source provenance")
    if report.get("source_snapshot_unchanged") is not True or before != after:
        raise ValueError("build receipt source/lock snapshot changed during the run")
    if before.get("issues") != []:
        raise ValueError("build receipt source snapshot contains cleanliness or pin issues")
    current = current if current is not None else _current_provenance(root, target)
    if current.get("issues") != []:
        details = "; ".join(current["issues"])
        raise ValueError("current checkout is not clean and pin-matched: " + details)
    if before.get("root_revision") != current.get("root_revision"):
        raise ValueError("build receipt root revision is stale")
    if before.get("sources_manifest_sha256") != current.get("sources_manifest_sha256"):
        raise ValueError("build receipt source pin manifest is stale")
    if before.get("root_layout") != current.get("root_layout"):
        raise ValueError("build receipt root sibling layout is stale")
    source_issues = qa.source_issues(
        before.get("sources", {}), current.get("sources", {}), require_clean=True
    )
    if source_issues:
        raise ValueError("build receipt source/lock snapshot is stale: " + "; ".join(source_issues))
    _server, _owner, pins = read_manifest(root / "scripts/release/sources.json")
    for name, revision in pins.items():
        source = before.get("sources", {}).get(name, {})
        if source.get("revision") != revision:
            raise ValueError(f"build receipt source pin differs for {name}")
        lock_hash = source.get("lock_sha256")
        if name not in vendor_names() and (not isinstance(lock_hash, str) or not SHA256_RE.fullmatch(lock_hash)):
            raise ValueError(f"build receipt canonical Cargo.lock hash is missing for {name}")
        if name == "autoeq" and (
            not isinstance(source.get("nested_lock_sha256"), str)
            or not SHA256_RE.fullmatch(source["nested_lock_sha256"])
        ):
            raise ValueError("build receipt AutoEQ nested GPUI examples Cargo.lock hash is missing")

    cargo_environment = report.get("cargo_environment")
    if (not isinstance(cargo_environment, dict)
            or cargo_environment.get("cargo_net_offline") != "true"):
        raise ValueError("build receipt does not prove offline Cargo execution")
    target_dir_value = cargo_environment.get("cargo_target_dir")
    if not isinstance(target_dir_value, str) or not Path(target_dir_value).is_absolute():
        raise ValueError("build receipt Cargo target directory is missing or not absolute")
    target_dir = Path(target_dir_value).resolve(strict=True)
    if target_dir != (evidence_root / "cargo-target").resolve(strict=True):
        raise ValueError("build receipt Cargo target directory is outside its evidence tree")

    return {
        "evidence_root": evidence_root,
        "evidence_root_path": report_path.parent.absolute(),
        "target_dir": target_dir,
        "pins": pins,
    }


def _validate_build_receipt(
    report_path: Path, target: str, *, root: Path = ROOT,
    current: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    if target not in ("macos-arm64", "linux-arm64"):
        raise ValueError(f"unsupported pack target: {target}")
    if not report_path.is_absolute():
        raise ValueError("--input-receipt must be an absolute path")
    _no_symlink_components(report_path)
    if not report_path.is_file():
        raise ValueError("input build receipt must be a regular file")
    report_bytes = report_path.read_bytes()
    report_hash = hashlib.sha256(report_bytes).hexdigest()
    try:
        report = json.loads(report_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"input build receipt is not valid JSON: {error}") from error
    if (not isinstance(report, dict) or report.get("schema") != 1
            or report.get("mode") != "build-only"):
        raise ValueError("input receipt is not a supported local build-only report")
    if report.get("status") != "INCOMPLETE" or report.get("release_complete") is not False:
        raise ValueError("input receipt must be a final INCOMPLETE build-only report")
    if "active_command" in report:
        raise ValueError("input receipt still has an active Cargo command")
    if report.get("source_root") is None:
        raise ValueError("input receipt source_root is missing")

    provenance = _validate_source_report(report, report_path, target, root=root, current=current)
    features = list(NIH_FEATURES)
    if len(features) != 43 or len(set(features)) != 43:
        raise ValueError("canonical NIH feature inventory must contain 43 unique features")
    specs = {
        str(step["label"]): step
        for step in CONTRACT["targets"][target]["plugins"]["build_only"]
    }
    rows = report.get("results")
    if not isinstance(rows, list):
        raise ValueError("input receipt build results are missing")
    plugin_results: dict[str, dict[str, Any]] = {}
    for row in rows:
        if (not isinstance(row, dict) or row.get("target") != target
                or row.get("group") != "plugins"):
            continue
        step = row.get("step")
        if not isinstance(step, str) or not step.startswith("plugin-"):
            continue
        feature = step.removeprefix("plugin-")
        if feature in plugin_results:
            raise ValueError(f"duplicate NIH feature result: {feature}")
        plugin_results[feature] = row
    if set(plugin_results) != set(features):
        raise ValueError(
            "input receipt does not contain exactly the canonical 43 NIH feature results"
        )

    extension = "dylib" if target == "macos-arm64" else "so"
    triple = str(TARGETS[target]["triple"])
    validated: list[dict[str, Any]] = []
    for feature in features:
        row = plugin_results[feature]
        if row.get("status") != "built" or row.get("exit_code") != 0:
            raise ValueError(f"{feature}: input receipt does not prove a successful fresh build")
        cleanup = row.get("owned_group_cleanup")
        if not isinstance(cleanup, dict) or cleanup.get("ok") is not True:
            raise ValueError(f"{feature}: process-group cleanup is not verified")
        spec = specs.get(f"plugin-{feature}")
        argv = row.get("argv")
        if (spec is None or not isinstance(argv, list)
                or any(not isinstance(part, str) for part in argv)):
            raise ValueError(f"{feature}: input receipt lacks persisted build command proof")
        expected_argv = [str(part) for part in spec["argv"]]
        expected_argv.extend(["--target-dir", str(provenance["target_dir"])])
        if argv != expected_argv:
            reason = "build command does not prove the expected locked offline dist profile/target"
            raise ValueError(f"{feature}: {reason}")
        if "--profile" not in argv or argv[argv.index("--profile") + 1] != "dist":
            raise ValueError(f"{feature}: input receipt does not prove the dist profile")

        artifact_value = row.get("artifact")
        expected_artifact = (
            provenance["evidence_root"] / "artifacts" / target / "plugins" /
            f"plugin-{feature}" / f"libplugins_nih.{extension}"
        )
        if not isinstance(artifact_value, str) or not Path(artifact_value).is_absolute():
            raise ValueError(f"{feature}: staged plugin artifact path is missing")
        artifact = Path(artifact_value)
        if not _is_within(artifact.absolute(), provenance["evidence_root_path"]):
            raise ValueError(
                f"{feature}: staged plugin artifact is outside its receipt evidence tree"
            )
        _no_symlink_components(artifact, beneath=provenance["evidence_root_path"])
        if not artifact.is_file() or artifact.stat().st_size == 0:
            raise ValueError(f"{feature}: staged plugin artifact is missing or empty")
        if artifact.resolve(strict=True) != expected_artifact.resolve(strict=True):
            raise ValueError(
                f"{feature}: staged plugin artifact is outside the expected receipt evidence path"
            )
        recorded_hash = row.get("sha256")
        if not isinstance(recorded_hash, str) or not SHA256_RE.fullmatch(recorded_hash):
            raise ValueError(f"{feature}: staged plugin hash is missing")
        actual_hash = file_sha256(artifact)
        if actual_hash != recorded_hash:
            raise ValueError(f"{feature}: staged plugin hash differs from the input receipt")
        if target == "macos-arm64":
            assert_macho_arm64(artifact)
        else:
            assert_elf_architecture(artifact, "aarch64-linux")
        validated.append({
            "feature": feature,
            "input": artifact,
            "sha256": actual_hash,
            "size": artifact.stat().st_size,
        })
    return report, validated, report_hash


def _write_artifacts(
    output_root: Path, target: str, inputs: list[dict[str, Any]], sources: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    linux = target == "linux-arm64"
    suffix = "-linux" if linux else ""
    dist_root = output_root / "artifacts/sotf-daw/dist"
    clap_root = dist_root / f"clap{suffix}"
    vst3_root = dist_root / f"vst3{suffix}"
    clap_root.mkdir(parents=True)
    vst3_root.mkdir(parents=True)
    for item in inputs:
        feature = item["feature"]
        base = feature_base(feature)
        clap_bundle = clap_root / f"{base}.clap"
        display = vst3_name(feature)
        if linux:
            clap = clap_bundle
        else:
            clap = clap_bundle / "Contents/MacOS" / base
            clap.parent.mkdir(parents=True)
            (clap_bundle / "Contents/Info.plist").write_bytes(plistlib.dumps({
                "CFBundleExecutable": base,
                "CFBundleIdentifier": f"org.spinorama.sotf.{base}.clap",
                "CFBundleName": display,
                "CFBundlePackageType": "BNDL",
                "CFBundleVersion": "1",
            }, fmt=plistlib.FMT_XML, sort_keys=False))
        shutil.copy2(item["input"], clap)
        bundle = vst3_root / f"{display}.vst3"
        if linux:
            binary = bundle / "Contents/aarch64-linux" / f"{display}.so"
        else:
            binary = bundle / "Contents/MacOS" / base
        binary.parent.mkdir(parents=True)
        shutil.copy2(item["input"], binary)
        for copied in (clap, binary):
            if file_sha256(copied) != item["sha256"]:
                raise ValueError(f"{feature}: copied plugin bytes differ from the verified input")
        if not linux:
            info = bundle / "Contents/Info.plist"
            info.write_bytes(plistlib.dumps({
                "CFBundleExecutable": base,
                "CFBundleIdentifier": f"org.spinorama.sotf.{base}.vst3",
                "CFBundleName": display,
                "CFBundlePackageType": "BNDL",
                "CFBundleVersion": "1",
            }, fmt=plistlib.FMT_XML, sort_keys=False))

    stage_notices(output_root, target, sources)
    notice_paths = set(notice_layout(target, sources))
    inventory = []
    for path in sorted((output_root / "artifacts").rglob("*")):
        if path.is_file():
            if path.stat().st_size == 0:
                raise ValueError(f"packaged artifact is empty: {path}")
            inventory.append({
                "path": str(path.relative_to(output_root)),
                "kind": ("notice" if str(path.relative_to(output_root)) in notice_paths
                         else "bundle-metadata" if path.name == "Info.plist" else "plugin-binary"),
                "size": path.stat().st_size,
                "sha256": file_sha256(path),
            })
    if {row["path"] for row in inventory} != expected_artifact_paths(target, sources):
        raise ValueError("packed artifact paths differ from the exact plugin/notice layout")
    expected_files = (86 if linux else 172) + len(notice_paths)
    if len(inventory) != expected_files:
        raise ValueError(f"expected {expected_files} packaged files, found {len(inventory)}")
    return inventory


def package_from_receipt(input_receipt: Path, target: str, output_dir: Path) -> Path:
    if not output_dir.is_absolute():
        raise ValueError("--output-dir must be an absolute path")
    output_dir = output_dir.expanduser()
    _no_symlink_components(output_dir)
    output_root = output_dir.resolve()
    source_root = ROOT.resolve()
    if _is_within(output_root, source_root):
        raise ValueError("output directory must be outside the source checkout")
    report, inputs, report_hash = _validate_build_receipt(input_receipt, target)
    sources = notice_sources()
    evidence_root = input_receipt.resolve(strict=True).parent
    if _is_within(output_root, evidence_root) or _is_within(evidence_root, output_root):
        raise ValueError("output directory and input evidence tree must be separate")
    _no_symlink_components(output_root)
    output_root.parent.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(exist_ok=False)
    try:
        inventory = _write_artifacts(output_root, target, inputs, sources)
        if file_sha256(input_receipt) != report_hash:
            raise ValueError("input build receipt changed during packaging")
        current = _current_provenance(ROOT, target)
        _validate_source_report(report, input_receipt, target, current=current)
        _server, _owner, pins = read_manifest(ROOT / "scripts/release/sources.json")
        target_triple = str(TARGETS[target]["triple"])
        verify_notice_sources(sources)
        receipt = {
            "schema": SCHEMA,
            "status": "PACKAGED",
            "qualification": {
                "native_validators": "pending",
                "host_loading": "pending",
                "release_qualification": "pending",
            },
            "target": target,
            "target_triple": target_triple,
            "profile": "dist",
            "input_receipt": {"path": str(input_receipt.resolve()), "sha256": report_hash},
            "source_snapshot": {
                "root_revision": report["sources_before"]["root_revision"],
                "sources_manifest_sha256": report["sources_before"]["sources_manifest_sha256"],
                "pins": pins,
            },
            "features": [
                {"name": item["feature"], "input": str(item["input"]),
                 "input_sha256": item["sha256"], "input_size": item["size"]}
                for item in inputs
            ],
            "artifact_inventory": inventory,
            "third_party_notices": notice_receipt(target, sources),
        }
        receipt_path = output_root / "package-nih-report.json"
        pending = receipt_path.with_suffix(".pending")
        pending.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        pending.replace(receipt_path)
        return receipt_path
    except BaseException:
        shutil.rmtree(output_root)
        raise


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-receipt", type=Path, required=True,
                        help="absolute final local-release-groups-report.json")
    parser.add_argument("--target", choices=("macos-arm64", "linux-arm64"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="fresh absolute external evidence directory (for example under MBX)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    try:
        report = package_from_receipt(args.input_receipt, args.target, args.output_dir)
    except (
        OSError, RuntimeError, ValueError, KeyError, IndexError, subprocess.SubprocessError
    ) as error:
        print(f"NIH pack-only failed: {error}", file=sys.stderr)
        return 2
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
