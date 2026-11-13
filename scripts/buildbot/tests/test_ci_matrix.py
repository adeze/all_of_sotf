import unittest

from ci_matrix import (
    DESKTOP_PLATFORMS,
    TARGET_BUILDS,
    WORKSPACES,
    desktop_builder_names,
    gate_commands,
    nightly_builder_names,
    validate_matrix,
    workspace_map,
)


class MatrixTests(unittest.TestCase):
    def test_matrix_is_valid(self):
        validate_matrix()

    def test_requested_workspaces_are_covered(self):
        self.assertEqual(
            set(workspace_map()),
            {"gpui-toolkit", "math-audio", "autoeq", "sotf", "sotf-daw",
             "sotf-systemwide", "sotf-capture", "sofa-reader"},
        )

    def test_every_workspace_has_tests_and_qa_on_every_desktop(self):
        for workspace in WORKSPACES:
            names = set(desktop_builder_names(workspace.name, include_qa=True))
            for platform in DESKTOP_PLATFORMS:
                self.assertIn(f"{workspace.name}-{platform.name}-tests", names)
                self.assertIn(f"{workspace.name}-{platform.name}-qa", names)

    def test_gpui_uses_existing_ntest_recipe(self):
        self.assertIn("ntest", workspace_map()["gpui-toolkit"].test_recipes)
        self.assertNotIn("test", workspace_map()["gpui-toolkit"].test_recipes)

    def test_sotf_full_qa_is_nightly(self):
        names = nightly_builder_names("sotf")
        for platform in DESKTOP_PLATFORMS:
            self.assertIn(f"sotf-{platform.name}-qa", names)

    def test_roots_without_qa_recipes_run_cargo_and_doctests(self):
        for name in ("sotf-capture",):
            commands = gate_commands(workspace_map()[name], qa=True)
            self.assertTrue(all(command[0] == "cargo" for command in commands))
            self.assertIn(("cargo", "test", "--workspace", "--doc"), commands)
            self.assertTrue(any("--all-targets" in command for command in commands))

    def test_sofa_runs_its_minimal_feature_gate(self):
        self.assertEqual(gate_commands(workspace_map()["sofa-reader"], qa=True), (("just", "all"),))

    def test_device_targets_are_force_only(self):
        device_targets = [target for target in TARGET_BUILDS if "device" in target.name]
        self.assertTrue(device_targets)
        self.assertTrue(all(not target.nightly for target in device_targets))


if __name__ == "__main__":
    unittest.main()
