"""Companion checkout inventory must not change Rust release membership."""

import json
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


if __name__ == "__main__":
    unittest.main()
