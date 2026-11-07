"""Behavioral checks for aggregate input preflight."""

from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
