"""Companion checkout inventory must not change Rust release membership."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import workspaces


class InventoryTests(unittest.TestCase):
    def test_companion_paths_preserve_rust_inventory_and_reject_invalid_entries(self):
        self.assertEqual(len(workspaces.workspace_names()), 9)
        self.assertTrue(all(not path.startswith("../") for path in workspaces.source_names()))
        self.assertEqual(
            workspaces.application_paths(),
            ["../open-room-calibration", "../py-open-room-calibration"],
        )
        for paths in (
            ["../same", "../same"], ["/absolute"], ["../../outside"],
            ["inside"], ["../a/../b"],
        ):
            with self.subTest(paths=paths):
                data = {"companion_applications": [{"path": path} for path in paths]}
                with patch.object(workspaces.Path, "read_text", return_value=json.dumps(data)):
                    with self.assertRaises(ValueError):
                        workspaces.application_paths()


class WorkspaceInventoryTests(unittest.TestCase):
    def test_local_selection_is_explicit_and_does_not_depend_on_presence(self):
        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "repos.json"
            inventory.write_text(json.dumps({"repositories": [
                {"path": "audio", "primary_language": "Rust"},
                {"path": "toolkit", "primary_language": "Rust", "checkout": "git-dependency"},
            ]}))
            with patch.object(workspaces, "INVENTORY", inventory):
                self.assertEqual(workspaces.workspace_names(), ["audio", "toolkit"])
                self.assertEqual(workspaces.workspace_names(local_only=True), ["audio"])

    def test_unknown_checkout_kind_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "repos.json"
            inventory.write_text(json.dumps({"repositories": [
                {"path": "audio", "primary_language": "Rust", "checkout": "typo"},
            ]}))
            with patch.object(workspaces, "INVENTORY", inventory), self.assertRaisesRegex(ValueError, "checkout kind"):
                workspaces.workspace_names(local_only=True)


if __name__ == "__main__":
    unittest.main()
