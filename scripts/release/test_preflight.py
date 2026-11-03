"""Behavioral checks for aggregate input preflight."""

from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import preflight


class PreflightTests(unittest.TestCase):
    def test_removed_dependency_recipe_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Justfile").write_text(
                "clean:\n\tjust --justfile symphonia-add-ons/Justfile clean\n"
            )
            self.assertEqual(preflight.check_recipes(root),
                             ["Justfile: missing recipe input symphonia-add-ons/Justfile"])

    def test_recipe_inputs_in_present_workspace_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "audio").mkdir()
            (root / "audio/Cargo.toml").touch()
            (root / "Justfile").write_text("check:\n\tcargo check --manifest-path audio/Cargo.toml\n")
            self.assertEqual(preflight.check_recipes(root), [])

    def test_missing_submodule_is_not_treated_as_optional_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(preflight, "workspace_names", return_value=["audio"]), \
                    patch.object(preflight, "vendor_names", return_value=[]), \
                    patch.object(preflight, "gitlink_revision", return_value="a" * 40):
                self.assertIn("missing submodule Cargo.toml", preflight.check_layout(root)[0])

    def test_release_requires_absent_external_source(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(preflight, "source_names", return_value=["toolkit"]), \
                    patch.object(preflight, "read_manifest", return_value=("server", "owner", {"toolkit": "a" * 40})):
                self.assertIn("release source is absent", preflight.check_release_pins(Path(directory))[0])

    def test_release_rejects_checkout_and_gitlink_pin_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "audio").mkdir()
            with patch.object(preflight, "source_names", return_value=["audio"]), \
                    patch.object(preflight, "read_manifest", return_value=("server", "owner", {"audio": "a" * 40})), \
                    patch.object(preflight, "gitlink_revision", return_value="b" * 40), \
                    patch.object(preflight.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 0, "c" * 40),
                                                                         subprocess.CompletedProcess([], 0, "")]):
                errors = preflight.check_release_pins(root)
                self.assertEqual(len(errors), 2)
                self.assertIn("checkout differs", errors[0])
                self.assertIn("gitlink differs", errors[1])

    def test_release_rejects_dirty_pinned_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "audio").mkdir()
            with patch.object(preflight, "source_names", return_value=["audio"]), \
                    patch.object(preflight, "read_manifest", return_value=("server", "owner", {"audio": "a" * 40})), \
                    patch.object(preflight, "gitlink_revision", return_value="a" * 40), \
                    patch.object(preflight.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 0, "a" * 40),
                                                                         subprocess.CompletedProcess([], 0, " M src/lib.rs\n")]):
                self.assertIn("release source is dirty", preflight.check_release_pins(root)[0])


    def test_release_uses_selected_root_and_manifest(self):
        root = Path("/selected/root")
        manifest = Path("/selected/candidate.json")
        with patch.object(preflight, "check_layout", return_value=[]) as layout, \
                patch.object(preflight, "check_recipes", return_value=[]), \
                patch.object(preflight, "check_generated_manifest", return_value=[]), \
                patch.object(preflight, "read_manifest", return_value=("server", "owner", {"audio": "a" * 40})) as read, \
                patch.object(preflight, "root_layout_status", return_value={"missing": [], "unexpected": []}), \
                patch.object(preflight, "check_release_pins", return_value=[]) as pins:
            self.assertEqual(preflight.main(["--release", "--root", str(root), "--manifest", str(manifest)]), 0)
            layout.assert_called_once_with(root)
            read.assert_called_once_with(manifest)
            pins.assert_called_once_with(root, manifest)

    def test_release_rejects_dirty_root_and_pin_mismatch(self):
        with patch.object(preflight, "check_layout", return_value=[]), \
                patch.object(preflight, "check_recipes", return_value=[]), \
                patch.object(preflight, "check_generated_manifest", return_value=[]), \
                patch.object(preflight, "read_manifest", return_value=("server", "owner", {})), \
                patch.object(preflight, "root_layout_status", return_value={"missing": ["audio: root gitlink pin mismatch"], "unexpected": [" M README.md"]}), \
                patch.object(preflight, "check_release_pins", return_value=[]):
            self.assertEqual(preflight.main(["--release"]), 1)


    def test_target_inventory_controls_layout_and_manifest_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "scripts/quality-matrix/repos.json"
            inventory.parent.mkdir(parents=True)
            inventory.write_text(json.dumps({
                "repositories": [{"path": "target-audio", "primary_language": "Rust"}],
                "vendored_repositories": ["target-vendor"],
            }))
            for name in ("target-audio", "target-vendor"):
                (root / name).mkdir()
            (root / "target-audio/Cargo.toml").touch()
            manifest = root / "candidate.json"
            data = {"schema": 1, "server": "https://example.test", "owner": "owner",
                    "sources": {name: {"revision": "a" * 40}
                                for name in ("target-audio", "target-vendor")}}
            manifest.write_text(json.dumps(data))
            original = preflight.workspaces.INVENTORY
            def git_result(args, **kwargs):
                output = "a" * 40 if "rev-parse" in args else ""
                return subprocess.CompletedProcess(args, 0, output)
            with patch.object(preflight, "check_recipes", return_value=[]), \
                    patch.object(preflight, "check_generated_manifest", return_value=[]), \
                    patch.object(preflight, "root_layout_status", return_value={"missing": [], "unexpected": []}), \
                    patch.object(preflight, "gitlink_revision", return_value="a" * 40), \
                    patch.object(preflight.subprocess, "run", side_effect=git_result):
                args = ["--release", "--root", str(root), "--manifest", str(manifest)]
                self.assertEqual(preflight.main(args), 0)
                self.assertEqual(preflight.workspaces.INVENTORY, original)
                del data["sources"]["target-vendor"]
                manifest.write_text(json.dumps(data))
                self.assertEqual(preflight.main(args), 1)
                self.assertEqual(preflight.workspaces.INVENTORY, original)


if __name__ == "__main__":
    unittest.main()
