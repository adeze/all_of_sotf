"""Focused tests for the native NIH validator's receipt and supervision gates.

These fixtures exercise receipt handling and owned-process cleanup only. They
do not invoke either native plugin validator or claim native validation.
"""

from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.release import package_nih_artifacts as packer
from scripts.release import validate_nih_artifacts as validator
from scripts.release.checkout_sources import read_manifest


class ValidateNihArtifactsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.evidence = self.base / "build-evidence"
        self.evidence.mkdir()
        self.target_dir = self.evidence / "cargo-target"
        self.target_dir.mkdir()
        self.current = self.current_snapshot()

    def current_snapshot(self) -> dict:
        _, _, pins = read_manifest(packer.ROOT / "scripts/release/sources.json")
        sources = {}
        for index, name in enumerate(sorted(pins)):
            source = {
                "revision": pins[name], "dirty": False,
                "lock_sha256": None if name in packer.vendor_names() else f"{index + 1:064x}",
            }
            if name == "autoeq":
                source["nested_lock_sha256"] = "a" * 64
            sources[name] = source
        return {
            "root_revision": "b" * 40,
            "sources_manifest_sha256": packer.file_sha256(
                packer.ROOT / "scripts/release/sources.json"),
            "sources": sources,
            "root_layout": {
                "missing": [], "unexpected": [], "tracked_gitlinks": sorted(pins),
                "allowed_siblings": [],
            },
            "issues": [],
        }

    @staticmethod
    def macho_arm64() -> bytes:
        return struct.pack("<IiiIIII", 0xFEEDFACF, packer.MACOS_ARM64_CPU_TYPE,
                           0, 6, 1, 0, 0) + b"fixture-plugin-payload"

    def make_build_receipt(self) -> Path:
        results = []
        specs = {
            str(step["label"]): step
            for step in packer.CONTRACT["targets"]["macos-arm64"]["plugins"]["build_only"]
        }
        for feature in packer.NIH_FEATURES:
            label = f"plugin-{feature}"
            path = (self.evidence / "artifacts/macos-arm64/plugins" / label
                    / "libplugins_nih.dylib")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.macho_arm64() + feature.encode("ascii"))
            argv = [str(part) for part in specs[label]["argv"]]
            argv.extend(["--target-dir", str(self.target_dir.resolve())])
            results.append({
                "target": "macos-arm64", "group": "plugins", "step": label,
                "status": "built", "artifact": str(path),
                "sha256": packer.file_sha256(path), "exit_code": 0, "argv": argv,
                "owned_group_cleanup": {"ok": True, "remaining": [], "errors": []},
            })
        before = {
            "root_revision": self.current["root_revision"],
            "sources_manifest_sha256": self.current["sources_manifest_sha256"],
            "sources": self.current["sources"],
            "root_layout": self.current["root_layout"],
            "issues": [],
        }
        report = {
            "schema": 1, "mode": "build-only", "status": "INCOMPLETE",
            "release_complete": False, "source_root": str(packer.ROOT),
            "source_snapshot_unchanged": True, "sources_before": before,
            "sources_after": before,
            "cargo_environment": {
                "cargo_net_offline": "true",
                "cargo_target_dir": str(self.target_dir.resolve()),
            },
            "results": results,
        }
        path = self.evidence / "local-release-groups-report.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def package(self) -> Path:
        build_receipt = self.make_build_receipt()
        output = self.base / "packed"
        with mock.patch.object(packer, "_current_provenance", return_value=self.current):
            return packer.package_from_receipt(build_receipt, "macos-arm64", output)

    def test_pack_receipt_and_underlying_build_proof_are_revalidated(self) -> None:
        receipt = self.package()
        with mock.patch.object(packer, "_current_provenance", return_value=self.current):
            validated = validator._validate_package(receipt, current=self.current)
        self.assertEqual(len(validated["features"]), 43)
        self.assertEqual(len(validated["files"]), 352)
        self.assertEqual(validated["build_receipt_sha256"],
                         validator.sha256(validated["build_receipt_path"]))

    def test_missing_notice_is_rejected_before_native_command_generation(self) -> None:
        receipt = self.package()
        data = json.loads(receipt.read_text())
        item = data["third_party_notices"]["files"][0]
        (receipt.parent / item["path"]).unlink()
        with mock.patch.object(packer, "_current_provenance", return_value=self.current):
            with self.assertRaisesRegex(ValueError, "notice is missing or empty"):
                validator._validate_package(receipt, current=self.current)

    def test_tampered_notice_is_rejected_even_when_receipt_inventory_is_rehashed(self) -> None:
        receipt = self.package()
        data = json.loads(receipt.read_text())
        item = data["third_party_notices"]["files"][0]
        path = receipt.parent / item["path"]
        path.write_bytes(path.read_bytes() + b"tampered")
        row = next(row for row in data["artifact_inventory"] if row["path"] == item["path"])
        row.update(size=path.stat().st_size, sha256=packer.file_sha256(path))
        receipt.write_text(json.dumps(data))
        with mock.patch.object(packer, "_current_provenance", return_value=self.current):
            with self.assertRaisesRegex(ValueError, "notice bytes differ from canonical"):
                validator._validate_package(receipt, current=self.current)

    def test_notice_provenance_tampering_is_rejected(self) -> None:
        receipt = self.package()
        data = json.loads(receipt.read_text())
        data["third_party_notices"]["full_clearance"] = True
        receipt.write_text(json.dumps(data))
        with mock.patch.object(packer, "_current_provenance", return_value=self.current):
            with self.assertRaisesRegex(ValueError, "notice provenance/layout differs"):
                validator._validate_package(receipt, current=self.current)

    def test_source_notice_change_after_preflight_is_rejected_by_final_check(self) -> None:
        source = self.base / "fixture-notice.txt"
        source.write_bytes(b"original notice")
        specs = ((str(source), packer.NOTICE_SPECS[0][1], None), *packer.NOTICE_SPECS[1:])
        with mock.patch.object(packer, "NOTICE_SPECS", specs), mock.patch.object(packer, "_current_provenance", return_value=self.current):
            receipt = self.package()
            validated = validator._validate_package(receipt, current=self.current)
            source.write_bytes(b"changed notice")
            errors, _, _ = validator._final_package_check(validated)
        self.assertTrue(any("notice provenance or payload changed" in error for error in errors))

    def test_missing_or_wrong_clap_bundle_metadata_is_rejected(self) -> None:
        receipt = self.package()
        metadata = receipt.parent / "artifacts/sotf-daw/dist/clap/sotf_eq.clap/Contents/Info.plist"
        original = metadata.read_bytes()
        metadata.unlink()
        with mock.patch.object(packer, "_current_provenance", return_value=self.current):
            with self.assertRaisesRegex(ValueError, "physical packed files|paths do not match"):
                validator._validate_package(receipt, current=self.current)
        import plistlib
        changed = plistlib.loads(original)
        changed["CFBundleExecutable"] = "wrong-executable"
        metadata.write_bytes(plistlib.dumps(changed))
        data = json.loads(receipt.read_text())
        row = next(row for row in data["artifact_inventory"] if row["path"] == str(metadata.relative_to(receipt.parent)))
        row.update(size=metadata.stat().st_size, sha256=packer.file_sha256(metadata))
        receipt.write_text(json.dumps(data))
        with mock.patch.object(packer, "_current_provenance", return_value=self.current):
            with self.assertRaisesRegex(ValueError, "CLAP bundle metadata differs"):
                validator._validate_package(receipt, current=self.current)

    def test_clap_binary_at_wrong_bundle_path_is_rejected(self) -> None:
        receipt = self.package()
        binary = receipt.parent / "artifacts/sotf-daw/dist/clap/sotf_eq.clap/Contents/MacOS/sotf_eq"
        binary.rename(binary.with_name("wrong-executable"))
        with mock.patch.object(packer, "_current_provenance", return_value=self.current):
            with self.assertRaisesRegex(ValueError, "physical packed files|paths do not match"):
                validator._validate_package(receipt, current=self.current)

    def test_packed_artifact_hash_tamper_is_rejected(self) -> None:
        receipt = self.package()
        clap = receipt.parent / "artifacts/sotf-daw/dist/clap/sotf_eq.clap/Contents/MacOS/sotf_eq"
        clap.write_bytes(b"tampered")
        with mock.patch.object(packer, "_current_provenance", return_value=self.current):
            with self.assertRaisesRegex(
                ValueError, "CLAP and VST3 payload differ|hash/size differs|not a thin 64-bit Mach-O"
            ):
                validator._validate_package(receipt, current=self.current)

    def test_post_validation_pack_mutation_is_reported_against_receipt(self) -> None:
        receipt = self.package()
        with mock.patch.object(packer, "_current_provenance", return_value=self.current):
            validated = validator._validate_package(receipt, current=self.current)
        clap = receipt.parent / "artifacts/sotf-daw/dist/clap/sotf_eq.clap/Contents/MacOS/sotf_eq"
        clap.write_bytes(b"changed during validation")
        with mock.patch.object(packer, "_current_provenance", return_value=self.current):
            errors, source_after, final_hashes = validator._final_package_check(validated)
        self.assertTrue(any("packed input changed during native validation" in item for item in errors))
        self.assertIsNotNone(source_after)
        changed = next(row for row in final_hashes["artifacts"] if row["path"] == str(clap.relative_to(receipt.parent)))
        self.assertNotEqual(
            changed["sha256"], validated["inventory"][changed["path"]]["sha256"]
        )

    def test_build_receipt_mutation_breaks_package_receipt_chain(self) -> None:
        receipt = self.package()
        package_data = json.loads(receipt.read_text())
        build_receipt = Path(package_data["input_receipt"]["path"])
        build_data = json.loads(build_receipt.read_text())
        build_data["results"][0]["argv"][-1] = str(self.evidence / "changed-target")
        build_receipt.write_text(json.dumps(build_data), encoding="utf-8")
        with mock.patch.object(packer, "_current_provenance", return_value=self.current):
            with self.assertRaisesRegex(ValueError, "underlying build receipt is missing or has changed"):
                validator._validate_package(receipt, current=self.current)

    def test_command_inventory_keeps_native_gui_tests_and_outer_deadline(self) -> None:
        receipt = self.package()
        with mock.patch.object(packer, "_current_provenance", return_value=self.current):
            validated = validator._validate_package(receipt, current=self.current)
        commands = validator._validator_commands(
            validated["features"], validated["per_feature_paths"], self.base / "validation", 60
        )
        self.assertEqual(len(commands), 86)
        self.assertEqual(sum(item["format"] == "clap" for item in commands), 43)
        self.assertEqual(sum(item["format"] == "vst3" for item in commands), 43)
        clap_commands = [item for item in commands if item["format"] == "clap"]
        for item in clap_commands:
            self.assertTrue(item["path"].is_dir())
            self.assertEqual(item["path"].suffix, ".clap")
            self.assertEqual(item["hash_path"], item["path"] / "Contents/MacOS" / packer.feature_base(item["feature"]))
            self.assertEqual(item["argv_suffix"], ["validate", str(item["path"])])
        vst3_commands = [item for item in commands if item["format"] == "vst3"]
        self.assertEqual({Path(item["path"]).suffix for item in vst3_commands}, {".vst3"})
        for item in vst3_commands:
            feature = item["feature"]
            base = packer.feature_base(feature)
            display = packer.vst3_name(feature)
            expected_bundle = validated["package_root"] / (
                f"artifacts/sotf-daw/dist/vst3/{display}.vst3"
            )
            expected_binary = expected_bundle / "Contents/MacOS" / base
            self.assertEqual(item["path"], expected_bundle)
            self.assertEqual(item["hash_path"], expected_binary)
            self.assertEqual(item["argv_suffix"][1], str(item["path"]))
            self.assertTrue(Path(item["hash_path"]).name.startswith("sotf_"))
            self.assertNotEqual(item["hash_path"], item["path"])
            self.assertEqual(
                item["expected_sha256"],
                validated["per_feature_paths"][feature]["payload_sha256"],
            )
        for item in commands:
            self.assertEqual(item["timeout_seconds"], 60)
            if item["format"] == "vst3":
                self.assertIn("--strictness-level", item["argv_suffix"])
                self.assertIn("--timeout-ms", item["argv_suffix"])
                self.assertIn("--output-dir", item["argv_suffix"])
                self.assertNotIn("--skip-gui-tests", item["argv_suffix"])

    def test_owned_runner_records_pass_and_timeout_without_validator_claim(self) -> None:
        artifact = self.base / "fixture-plugin.clap"
        artifact.write_bytes(b"test-only fixture")
        output = self.base / "runner-evidence"
        output.mkdir()
        validator.enable_subreaper()
        passed_report = {"commands": []}
        with mock.patch.object(validator, "STOP", False):
            passed = validator._run_owned({
                "feature": "fixture", "format": "clap", "path": artifact,
                "hash_path": artifact,
                "expected_sha256": validator.sha256(artifact),
                "argv_kind": "test-child",
                "argv_suffix": ["-c", "print('owned fixture complete')"],
                "timeout_seconds": 5, "log": output / "pass.log",
            }, Path(sys.executable), {}, validator.sha256(Path(sys.executable)),
                passed_report, output)
        self.assertEqual(passed["status"], "PASS")
        self.assertTrue(passed["owned_group_cleanup"]["ok"])
        self.assertEqual(len(passed_report["commands"]), 1)

        timeout_report = {"commands": []}
        with mock.patch.object(validator, "STOP", False):
            timed_out = validator._run_owned({
                "feature": "fixture", "format": "clap", "path": artifact,
                "hash_path": artifact,
                "expected_sha256": validator.sha256(artifact),
                "argv_kind": "test-child",
                "argv_suffix": ["-c", "import time; time.sleep(10)"],
                "timeout_seconds": 0.1, "log": output / "timeout.log",
            }, Path(sys.executable), {}, validator.sha256(Path(sys.executable)),
                timeout_report, output)
        self.assertEqual(timed_out["status"], "FAIL")
        self.assertTrue(timed_out["timed_out"])
        self.assertTrue(timed_out["owned_group_cleanup"]["ok"])
        self.assertEqual(len(timeout_report["commands"]), 1)

    def test_payload_and_validator_mutation_are_rejected_before_launch(self) -> None:
        artifact = self.base / "fixture-plugin.clap"
        artifact.write_bytes(b"test-only fixture")
        output = self.base / "runner-evidence"
        output.mkdir()
        command = {
            "feature": "fixture", "format": "clap", "path": artifact,
            "hash_path": artifact, "expected_sha256": "0" * 64,
            "argv_kind": "test-child", "argv_suffix": ["-c", "print('must not run')"],
            "timeout_seconds": 5, "log": output / "never.log",
        }
        with mock.patch.object(validator.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(ValueError, "payload changed before validation"):
                validator._run_owned(command, Path(sys.executable), {},
                                      "0" * 64, {"commands": []}, output)
            command["expected_sha256"] = validator.sha256(artifact)
            with self.assertRaisesRegex(ValueError, "executable changed before launch"):
                validator._run_owned(command, Path(sys.executable), {},
                                      "0" * 64, {"commands": []}, output)
        popen.assert_not_called()

    def test_failed_launch_after_payload_deletion_is_recorded_and_clears_active(self) -> None:
        artifact = self.base / "fixture-plugin.clap"
        artifact.write_bytes(b"test-only fixture")
        output = self.base / "runner-evidence"
        output.mkdir()
        command = {
            "feature": "fixture", "format": "clap", "path": artifact,
            "hash_path": artifact, "expected_sha256": validator.sha256(artifact),
            "argv_kind": "test-child", "argv_suffix": ["-c", "print('must not run')"],
            "timeout_seconds": 5, "log": output / "failed-launch.log",
        }
        report = {"commands": []}

        def delete_then_fail(*_args, **_kwargs):
            artifact.unlink()
            raise OSError("fixture launch failure")

        with mock.patch.object(validator.subprocess, "Popen", side_effect=delete_then_fail):
            result = validator._run_owned(
                command, Path(sys.executable), {}, validator.sha256(Path(sys.executable)),
                report, output,
            )
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("fixture launch failure", result["launch_error"])
        self.assertIsNone(result["artifact_sha256_after"])
        self.assertNotIn("active_command", report)
        self.assertEqual(len(report["commands"]), 1)

    def test_unexpected_wait_error_is_recorded_after_group_cleanup(self) -> None:
        artifact = self.base / "fixture-plugin.clap"
        artifact.write_bytes(b"test-only fixture")
        output = self.base / "runner-evidence"
        output.mkdir()
        child = mock.Mock()
        child.pid = 4242
        child.returncode = 0
        child.wait.side_effect = OSError("fixture wait failure")
        report = {"commands": []}
        with (mock.patch.object(validator.subprocess, "Popen", return_value=child),
              mock.patch.object(validator, "clean_group", return_value={
                  "ok": True, "remaining": [], "errors": [],
              })):
            with self.assertRaisesRegex(OSError, "fixture wait failure"):
                validator._run_owned({
                    "feature": "fixture", "format": "clap", "path": artifact,
                    "hash_path": artifact, "expected_sha256": validator.sha256(artifact),
                    "argv_kind": "test-child", "argv_suffix": ["-c", "pass"],
                    "timeout_seconds": 5, "log": output / "wait-error.log",
                }, Path(sys.executable), {}, validator.sha256(Path(sys.executable)),
                    report, output)
        row = report["commands"][0]
        self.assertEqual(row["status"], "FAIL")
        self.assertTrue(any("wait raised OSError" in error for error in row["supervision_errors"]))
        self.assertNotIn("active_command", report)

    def test_preflight_wait_error_cleans_and_records_active_entry(self) -> None:
        output = self.base / "probe-evidence"
        output.mkdir()
        child = mock.Mock()
        child.pid = 4243
        child.returncode = 0
        child.wait.side_effect = OSError("probe wait failure")
        report: dict = {}
        with (mock.patch.object(validator.subprocess, "Popen", return_value=child),
              mock.patch.object(validator, "clean_group", return_value={
                  "ok": True, "remaining": [], "errors": [],
              })):
            with self.assertRaisesRegex(OSError, "probe wait failure"):
                validator._run_preflight_owned(
                    "fixture-probe", [sys.executable, "--version"], output, {}, report
                )
        row = report["preflight_commands"][0]
        self.assertEqual(row["status"], "FAIL")
        self.assertTrue(any("wait raised OSError" in error for error in row["supervision_errors"]))
        self.assertNotIn("active_command", report)

    def test_preflight_cleanup_error_is_recorded_and_clears_active_entry(self) -> None:
        output = self.base / "probe-evidence"
        output.mkdir()
        child = mock.Mock()
        child.pid = 4244
        child.returncode = 0
        child.wait.return_value = 0
        report: dict = {}
        with (mock.patch.object(validator.subprocess, "Popen", return_value=child),
              mock.patch.object(validator, "clean_group",
                                side_effect=RuntimeError("fixture cleanup failure"))):
            with self.assertRaisesRegex(RuntimeError, "fixture cleanup failure"):
                validator._run_preflight_owned(
                    "fixture-probe", [sys.executable, "--version"], output, {}, report
                )
        row = report["preflight_commands"][0]
        self.assertEqual(row["status"], "FAIL")
        self.assertFalse(row["owned_group_cleanup"]["ok"])
        self.assertTrue(any("clean_group raised RuntimeError" in error
                            for error in row["supervision_errors"]))
        self.assertNotIn("active_command", report)

    def test_deleted_payload_refuses_before_launch_and_leaves_no_active_command(self) -> None:
        artifact = self.base / "deleted-plugin.clap"
        report = {"commands": []}
        command = {
            "feature": "fixture", "format": "clap", "path": artifact,
            "hash_path": artifact, "expected_sha256": "0" * 64,
            "argv_kind": "test-child", "argv_suffix": ["-c", "raise SystemExit(0)"],
            "timeout_seconds": 5, "log": self.base / "deleted.log",
        }
        with mock.patch.object(validator.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(ValueError, "validator input is missing"):
                validator._run_owned(command, Path(sys.executable), {},
                                      validator.sha256(Path(sys.executable)), report, self.base)
        popen.assert_not_called()
        self.assertNotIn("active_command", report)

    def test_incomplete_inventory_distinguishes_failure_from_not_run(self) -> None:
        commands = [
            {"format": "clap", "status": "PASS"},
            {"format": "clap", "status": "FAIL"},
            {"format": "vst3", "status": "PASS"},
        ]
        self.assertEqual(validator._format_result(commands, "clap"), {
            "passed": 1, "failed": 1, "not_run": 41, "expected": 43,
        })
        self.assertEqual(validator._format_result(commands, "vst3"), {
            "passed": 1, "failed": 0, "not_run": 42, "expected": 43,
        })

    def test_symlinked_evidence_parent_is_rejected(self) -> None:
        receipt = self.package()
        linked_parent = self.base / "linked-parent"
        real_parent = self.base / "real-parent"
        real_parent.mkdir()
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        with mock.patch.object(packer, "_current_provenance", return_value=self.current):
            validated = validator._validate_package(receipt, current=self.current)
        with self.assertRaisesRegex(ValueError, "symlink path component"):
            validator._check_external_output(
                linked_parent / "validation-run", validated["package_root"]
            )

    def test_native_identity_probe_timeout_cleans_its_owned_group(self) -> None:
        output = self.base / "probe-evidence"
        output.mkdir()
        report: dict = {}
        validator.enable_subreaper()
        with mock.patch.object(validator, "STOP", False):
            result, _ = validator._run_preflight_owned(
                "test-version-probe", [sys.executable, "-c", "import time;time.sleep(10)"],
                output, {}, report, timeout_seconds=0.1,
            )
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["timed_out"])
        self.assertTrue(result["owned_group_cleanup"]["ok"])
        self.assertEqual(len(report["preflight_commands"]), 1)


if __name__ == "__main__":
    unittest.main()
