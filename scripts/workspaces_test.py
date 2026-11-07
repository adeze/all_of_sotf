"""Local checks exclude declared external dependencies without hiding missing submodules."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import workspaces


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
