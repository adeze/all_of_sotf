"""Focused tests for the local release-group contract and safe runner."""

from __future__ import annotations

import json
import plistlib
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.release import local_release_groups as groups


def clean_snapshot() -> dict[str, object]:
    sources: dict[str, object] = {}
    for index, name in enumerate((
        "autoeq", "gpui-toolkit", "math-audio", "sofa-reader", "sotf",
        "sotf-capture", "sotf-daw", "sotf-systemwide",
    )):
        row: dict[str, object] = {
            "revision": f"{index + 1:040x}", "dirty": False,
            "lock_sha256": f"lock-{name}",
        }
        if name == "autoeq":
            row["nested_lock_sha256"] = "lock-autoeq-demo"
        sources[name] = row
    return {
        "root_revision": "a" * 40,
        "sources_manifest_sha256": "manifest-hash",
        "sources": sources,
        "root_layout": {"missing": [], "unexpected": [], "tracked_gitlinks": [], "allowed_siblings": []},
        "issues": [],
    }


class FakeProcess:
    next_code = 0
    commands: list[list[str]] = []

    def __init__(self, command, **kwargs):
        self.command = list(command)
        self.kwargs = kwargs
        self.pid = 12345
        self.returncode = None
        self.__class__.commands.append(self.command)

    def wait(self, timeout=None):
        if self.__class__.next_code == 0:
            target_dir = Path(self.command[self.command.index("--target-dir") + 1])
            step = next(
                item
                for target_spec in groups.CONTRACT["targets"].values()
                for group_spec in target_spec.values()
                for item in group_spec["build_only"]
                if [str(part) for part in item["argv"]] == self.command[:-2]
            )
            output = groups._actual_cargo_output(target_dir, step["output"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"fresh mocked cargo artifact")
        self.returncode = self.__class__.next_code
        return self.returncode

    def poll(self):
        return self.returncode


class LocalReleaseGroupsTests(unittest.TestCase):
    def setUp(self):
        FakeProcess.next_code = 0
        FakeProcess.commands = []

    def test_contract_covers_required_groups_and_keeps_unavailable_gates_explicit(self):
        self.assertEqual(tuple(groups.CONTRACT["required_groups"]), groups.REQUIRED_GROUPS)
        self.assertEqual(
            groups.CONTRACT["required_groups_by_target"],
            {
                "macos-arm64": list(groups.REQUIRED_GROUPS),
                "linux-arm64": ["desktop", "tui", "roomeq", "plugins"],
            },
        )
        self.assertEqual(groups.CONTRACT["deferred_groups_by_target"]["linux-arm64"], ["systemwide"])
        for target in groups.TARGETS:
            self.assertEqual(set(groups.CONTRACT["targets"][target]), set(groups.REQUIRED_GROUPS))
        self.assertEqual(groups.CONTRACT["minimum_os"]["macos"], "15.0")
        linux_systemwide = groups.CONTRACT["targets"]["linux-arm64"]["systemwide"]
        self.assertFalse(linux_systemwide["required"])
        self.assertTrue(linux_systemwide["deferred"])
        self.assertIn("outside the mandatory release scope", linux_systemwide["deferred_reason"])
        self.assertTrue(linux_systemwide["package_gate"].startswith("deferred"))
        self.assertEqual(linux_systemwide["build_only"], [])
        mac_systemwide = groups.CONTRACT["targets"]["macos-arm64"]["systemwide"]
        self.assertEqual(
            {stage["name"] for stage in mac_systemwide["required_payload_stages"]},
            {"menu-bar app", "HAL driver bundle"},
        )
        self.assertTrue(all(stage["build_only_status"].startswith("unavailable")
                            for stage in mac_systemwide["required_payload_stages"]))
        self.assertTrue(mac_systemwide["required"])
        self.assertEqual([item["label"] for item in mac_systemwide["build_only"]], ["systemwide-daemon-cargo"])
        self.assertIn("macos-arm64.pkg", mac_systemwide["expected_distribution_artifacts"][0])
        self.assertIn("payload binaries", mac_systemwide["existing_package_route"])
        plugin_route = groups.CONTRACT["targets"]["linux-arm64"]["plugins"]["existing_package_route"]
        self.assertIn("not dist", plugin_route)

    def test_dry_run_exposes_all_groups_and_target_specific_requirements(self):
        plan = groups.render_plan(groups.TARGETS)
        self.assertEqual(set(plan["targets"]), set(groups.TARGETS))
        for spec in plan["targets"].values():
            self.assertEqual(set(spec), set(groups.REQUIRED_GROUPS))
        self.assertEqual(FakeProcess.commands, [])

    def test_metadata_declarations_match_the_macOS_contract_baseline(self):
        baseline = groups.CONTRACT["minimum_os"]["macos"]

        def plist_minimum(path: Path) -> str:
            data = plistlib.loads(path.read_bytes())
            return data["LSMinimumSystemVersion"]

        self.assertEqual(plist_minimum(groups.ROOT / "sotf/builds/macos/org.spinorama.sotf.plist"), baseline)
        self.assertEqual(plist_minimum(groups.ROOT / "sotf-systemwide/swift/driver-hal/Info.plist"), baseline)

        build_script = (groups.ROOT / "sotf-systemwide/scripts/build-systemwide.sh").read_text(encoding="utf-8")
        plist_match = re.search(
            r'cat > "\$APP_BUNDLE/Contents/Info\.plist" << EOF\n(.*?)\nEOF',
            build_script,
            re.DOTALL,
        )
        self.assertIsNotNone(plist_match, "generated systemwide Info.plist template not found")
        generated_plist = plistlib.loads(plist_match.group(1).encode("utf-8"))
        self.assertEqual(generated_plist["LSMinimumSystemVersion"], baseline)
        self.assertRegex(build_script, rf"--minimum-deployment-target\s+{re.escape(baseline)}\b")

        package_swift = (groups.ROOT / "sotf-systemwide/swift/configbar/Package.swift").read_text(encoding="utf-8")
        self.assertRegex(package_swift, rf"\.macOS\(\"{re.escape(baseline)}\"\)")

    def test_linux_build_only_does_not_run_deferred_systemwide_group(self):
        snapshot = clean_snapshot()
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "evidence"
            with (
                patch.object(groups, "cargo_environment", return_value={"CARGO_HOME": "/mock/cargo"}),
                patch.object(groups, "toolchain_identity", return_value={"cargo": "mock"}),
                patch.object(groups, "_snapshot_provenance", side_effect=[snapshot, snapshot]),
            ):
                results, complete, report = groups.run_build_only(
                    ["linux-arm64"], evidence,
                    popen_factory=FakeProcess,
                    cleanup=lambda _child: {"ok": True, "remaining": [], "errors": []},
                    host_system="Linux", host_machine="aarch64",
                )
        self.assertFalse(complete)
        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertEqual(report["required_groups_by_target"]["linux-arm64"], ["desktop", "tui", "roomeq", "plugins"])
        self.assertEqual(report["deferred_groups_by_target"]["linux-arm64"], ["systemwide"])
        self.assertNotIn("systemwide", {result.group for result in results})
        self.assertTrue(FakeProcess.commands)
        self.assertTrue(all("sotf-daemon" not in command for command in FakeProcess.commands))

    def test_build_only_uses_fresh_external_target_and_preserves_all_lock_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "evidence"
            cargo_home = str(Path(temporary) / "canonical-cargo-home")
            environment = {"CARGO_HOME": cargo_home}
            with (
                patch.object(groups, "cargo_environment", return_value=environment),
                patch.object(groups, "toolchain_identity", return_value={"rustc": "mock", "cargo": "mock"}),
                patch.object(groups, "_snapshot_provenance", side_effect=[clean_snapshot(), clean_snapshot()]),
            ):
                results, complete, report = groups.run_build_only(
                    ["macos-arm64"], evidence,
                    popen_factory=FakeProcess,
                    cleanup=lambda _child: {"ok": True, "remaining": [], "errors": []},
                    host_system="Darwin", host_machine="arm64",
                )
            self.assertFalse(complete)
            self.assertEqual(report["status"], "INCOMPLETE")
            self.assertEqual(report["cargo_environment"]["cargo_home"], cargo_home)
            self.assertIsNone(report["cargo_environment"]["cargo_net_offline"])
            target_dir = Path(report["cargo_environment"]["cargo_target_dir"])
            self.assertEqual(target_dir, (evidence / "cargo-target").resolve())
            self.assertTrue(all(str(target_dir) in command for command in FakeProcess.commands))
            self.assertTrue(all((evidence / result.log).exists() if not Path(result.log).is_absolute() else Path(result.log).exists()
                                for result in results if result.log))
            desktop_result = next(result for result in results if result.step == "desktop-cargo" and result.status == "built")
            self.assertIsNotNone(desktop_result.argv)
            self.assertIn("--profile", desktop_result.argv)
            self.assertEqual(desktop_result.argv[desktop_result.argv.index("--profile") + 1], "dist")
            self.assertEqual(desktop_result.argv[desktop_result.argv.index("--target") + 1], "aarch64-apple-darwin")
            self.assertEqual(desktop_result.argv[desktop_result.argv.index("--features") + 1], "hal,onnx")
            self.assertEqual(desktop_result.argv[desktop_result.argv.index("--target-dir") + 1], str(target_dir))
            self.assertIn(desktop_result.argv, FakeProcess.commands)
            recorded_sources = report["sources_before"]["sources"]
            lock_hashes = [row["lock_sha256"] for row in recorded_sources.values()]
            lock_hashes.append(recorded_sources["autoeq"]["nested_lock_sha256"])
            self.assertEqual(len(lock_hashes), 9)
            self.assertTrue(report["sources_before"]["root_layout"])
            self.assertTrue((evidence / "local-release-groups-report.json").is_file())

    def test_build_failure_aggregates_and_never_touches_source_targets(self):
        FakeProcess.next_code = 9
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            evidence = temporary_root / "evidence"
            source_root = temporary_root / "source"
            source_root.mkdir()
            stale = source_root / "sotf/target/aarch64-apple-darwin/dist/sotf-desktop"
            stale.parent.mkdir(parents=True, exist_ok=True)
            existed = stale.exists()
            original = stale.read_bytes() if existed else None
            try:
                with (
                    patch.object(groups, "ROOT", source_root),
                    patch.object(groups, "cargo_environment", return_value={"CARGO_HOME": "/mock/cargo"}),
                    patch.object(groups, "toolchain_identity", return_value={}),
                    patch.object(groups, "_snapshot_provenance", side_effect=[clean_snapshot(), clean_snapshot()]),
                ):
                    results, complete, report = groups.run_build_only(
                        ["macos-arm64"], evidence,
                        popen_factory=FakeProcess,
                        cleanup=lambda _child: {"ok": True, "remaining": [], "errors": []},
                        host_system="Darwin", host_machine="arm64",
                    )
                self.assertFalse(complete)
                self.assertEqual(report["status"], "INCOMPLETE")
                self.assertTrue(any(result.status == "failed" for result in results))
                self.assertEqual(
                    {result.group for result in results if result.status == "failed"},
                    set(groups.REQUIRED_GROUPS),
                )
                self.assertEqual(
                    {result.group for result in results if result.status in {"pending", "unavailable"}},
                    set(groups.REQUIRED_GROUPS),
                )
                self.assertTrue(all(result.log and Path(result.log).is_file() for result in results if result.status == "failed"))
                self.assertEqual(stale.exists(), existed)
                if existed:
                    self.assertEqual(stale.read_bytes(), original)
            finally:
                if not existed:
                    stale.unlink(missing_ok=True)

    def test_dirty_or_pin_issues_reject_build_before_toolchain_probe(self):
        dirty = clean_snapshot()
        dirty["issues"] = ["sotf: source tree was dirty before validation"]
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(groups, "cargo_environment", return_value={}),
                patch.object(groups, "_snapshot_provenance", return_value=dirty),
                patch.object(groups, "toolchain_identity") as toolchain,
            ):
                with self.assertRaisesRegex(RuntimeError, "clean, pin-matched"):
                    groups.run_build_only(["macos-arm64"], Path(temporary) / "evidence")
                toolchain.assert_not_called()

    def test_snapshot_provenance_ignores_capture_time_but_preserves_source_and_lock_changes(self):
        revision = "a" * 40
        layout = {"missing": [], "unexpected": [], "tracked_gitlinks": [], "allowed_siblings": []}

        def workspace_state(captured_at: str, *, source_revision: str = revision, lock_hash: str = "lock-a"):
            return {
                "sotf": {
                    "captured_at": captured_at,
                    "workspace": "sotf",
                    "revision": source_revision,
                    "describe": source_revision[:8],
                    "branch": "release",
                    "dirty": False,
                    "platform_label": "macos",
                    "os": "Darwin",
                    "os_release": "27.0",
                    "architecture": "arm64",
                    "python": "3.13.0",
                    "rustc": "rustc mock",
                    "cargo": "cargo mock",
                    "cargo_nextest": "nextest mock",
                    "just": "just mock",
                    "lock_sha256": lock_hash,
                }
            }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "scripts/release/sources.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}\n", encoding="utf-8")
            root_layout = patch.object(groups, "root_layout_status", return_value=layout)
            with (
                patch.object(groups, "read_manifest", return_value=(None, None, {"sotf": revision})),
                patch.object(groups, "workspace_map", return_value={"sotf": {}}),
                patch.object(groups, "host_platform", return_value="macos"),
                root_layout,
                patch.object(groups, "subprocess") as subprocess_mock,
                patch.object(
                    groups,
                    "source_state",
                    side_effect=[
                        workspace_state("first"),
                        workspace_state("second"),
                        workspace_state("third", source_revision="b" * 40),
                        workspace_state("fourth", lock_hash="lock-b"),
                    ],
                ),
            ):
                subprocess_mock.run.return_value = SimpleNamespace(stdout="root-revision\n")
                before = groups._snapshot_provenance(root)
                after = groups._snapshot_provenance(root)
                self.assertEqual(before, after)
                revision_changed = groups._snapshot_provenance(root)
                lock_changed = groups._snapshot_provenance(root)

        self.assertNotEqual(before, revision_changed)
        self.assertIn("sotf: source revision does not match release pin", revision_changed["issues"])
        self.assertNotEqual(before["sources"], lock_changed["sources"])
        self.assertEqual(
            groups.source_issues(before["sources"], lock_changed["sources"], require_clean=True),
            ["sotf: Cargo.lock changed"],
        )

    def test_stop_before_toolchain_probe_persists_after_snapshot(self):
        snapshot = clean_snapshot()
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "evidence"
            with (
                patch.object(groups, "cargo_environment", return_value={}),
                patch.object(groups, "_snapshot_provenance", side_effect=[snapshot, snapshot]),
                patch.object(groups, "toolchain_identity") as toolchain,
            ):
                results, complete, report = groups.run_build_only(
                    ["macos-arm64"], evidence, stop_check=lambda: True,
                )
            self.assertEqual(results, [])
            self.assertFalse(complete)
            self.assertEqual(report["status"], "INTERRUPTED")
            self.assertEqual(report["sources_after"], snapshot)
            self.assertTrue(report["source_snapshot_unchanged"])
            self.assertTrue((evidence / "local-release-groups-report.json").is_file())
            toolchain.assert_not_called()
            self.assertEqual(FakeProcess.commands, [])

    def test_stop_after_toolchain_probe_prevents_cargo_launch_and_persists_report(self):
        snapshot = clean_snapshot()
        checks = iter((False, True))
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "evidence"
            with (
                patch.object(groups, "cargo_environment", return_value={}),
                patch.object(groups, "_snapshot_provenance", side_effect=[snapshot, snapshot]),
                patch.object(groups, "toolchain_identity", return_value={"cargo": "mock"}) as toolchain,
            ):
                _results, complete, report = groups.run_build_only(
                    ["macos-arm64"], evidence, stop_check=lambda: next(checks),
                )
            self.assertFalse(complete)
            self.assertEqual(report["status"], "INTERRUPTED")
            self.assertTrue(report["source_snapshot_unchanged"])
            self.assertTrue((evidence / "local-release-groups-report.json").is_file())
            toolchain.assert_called_once()
            self.assertEqual(FakeProcess.commands, [])

    def test_evidence_directory_must_be_fresh(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "already-there"
            evidence.mkdir()
            with self.assertRaises(FileExistsError):
                groups.run_build_only(["macos-arm64"], evidence)


if __name__ == "__main__":
    unittest.main()
