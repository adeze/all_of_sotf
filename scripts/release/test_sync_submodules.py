"""Behavioral checks for submodule synchronization helpers."""

from datetime import date
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import sync_submodules as sync

TODAY = date(2026, 11, 9)


class BumpTests(unittest.TestCase):
    def test_bump_parts(self):
        self.assertEqual(sync.bump_semver("0.5.78", "patch"), "0.5.79")
        self.assertEqual(sync.bump_semver("0.5.78", "minor"), "0.6.0")
        self.assertEqual(sync.bump_semver("0.5.78", "major"), "1.0.0")

    def test_non_semver_is_a_hard_error(self):
        with self.assertRaises(sync.SyncError):
            sync.bump_semver("0.5.78-rc1", "patch")

    def test_unknown_part_is_a_hard_error(self):
        with self.assertRaises(sync.SyncError):
            sync.bump_semver("0.5.78", "build")


class ManifestTests(unittest.TestCase):
    WORKSPACE = '[workspace]\nmembers = ["a"]\n\n[workspace.package]\n' \
                'version = "0.7.18"\nauthors = ["x"]\n\n[package]\nversion = "9.9.9"\n'

    def test_version_scoped_to_section(self):
        version, _, _ = sync.version_in_manifest(
            self.WORKSPACE, "workspace.package", "test")
        self.assertEqual(version, "0.7.18")

    def test_write_version_only_touches_named_section(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "Cargo.toml"
            manifest.write_text(self.WORKSPACE)
            sync.write_version(manifest, "workspace.package", "0.7.19")
            text = manifest.read_text()
            self.assertIn('version = "0.7.19"', text)
            self.assertIn('version = "9.9.9"', text)

    def test_missing_section_is_a_hard_error(self):
        with self.assertRaises(sync.SyncError):
            sync.version_in_manifest('[package]\nversion = "1.0.0"\n',
                                     "workspace.package", "test")


class PorcelainTests(unittest.TestCase):
    def test_parse_and_noise_filter(self):
        entries = sync.parse_porcelain("M  src/lib.rs\n M Cargo.lock\n?? .DS_Store\n")
        self.assertEqual(len(entries), 3)
        self.assertTrue(sync.is_noise("src/.DS_Store"))
        self.assertFalse(sync.is_noise("src/lib.rs"))

    def test_rename_uses_destination(self):
        entries = sync.parse_porcelain("R  old.rs -> new.rs\n")
        self.assertEqual(entries, [("R ", "new.rs")])


class ChangelogTests(unittest.TestCase):
    def write(self, directory: str, text: str) -> Path:
        (Path(directory) / "CHANGELOG.md").write_text(text)
        return Path(directory)

    def test_bracketed_promotion_keeps_empty_unreleased(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.write(directory, "# Changelog\n\n## [Unreleased]\n\n"
                                         "- Thing.\n\n## [0.5.5] - 2026-10-31\n\n- Old.\n")
            sync.promote_changelog(root, sync.spec_for("math-audio"), "0.5.6", None, TODAY)
            text = (root / "CHANGELOG.md").read_text()
            self.assertIn("## [Unreleased]\n\n## [0.5.6] - 2026-11-09\n\n- Thing.", text)

    def test_hash_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.write(directory, "# Unreleased\n\n- Thing.\n\n# 0.5.78\n\n- Old.\n")
            sync.promote_changelog(root, sync.spec_for("autoeq"), "0.5.79", None, TODAY)
            self.assertIn("# Unreleased\n\n# 0.5.79\n\n- Thing.",
                          (root / "CHANGELOG.md").read_text())

    def test_daemon_promotion_records_both_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.write(directory, "# Notes\n\n## Unreleased\n\n- Thing.\n\n"
                                         "## 0.7.19 (daemon 0.1.39)\n\n- Old.\n")
            sync.promote_changelog(root, sync.spec_for("sotf-systemwide"),
                                   "0.7.20", "0.1.40", TODAY)
            self.assertIn("## 0.7.20 (daemon 0.1.40)\n\n- Thing.",
                          (root / "CHANGELOG.md").read_text())

    def test_insert_promotion_goes_above_first_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.write(directory, "# Changelog\n\nNotes.\n\n"
                                         "## 0.6.0 \u2014 2026-10-18\n\n- Old.\n")
            sync.promote_changelog(root, sync.spec_for("sotf-capture"), "0.6.2", None, TODAY)
            self.assertIn("## 0.6.2 \u2014 2026-11-09\n\n## 0.6.0",
                          (root / "CHANGELOG.md").read_text())

    def test_unreleased_body_detects_empty(self):
        self.assertEqual(sync.unreleased_body(
            "## Unreleased\n\n## 0.7.19 (daemon 0.1.39)\n", "daemon"), "")
        self.assertEqual(sync.unreleased_body(
            "## [Unreleased]\n\n- Thing.\n\n## [0.5.5]\n", "bracketed"), "- Thing.")

    def test_unknown_style_is_a_hard_error(self):
        with self.assertRaises(sync.SyncError):
            sync.unreleased_body("## Unreleased\n", "invented")

    def test_release_section_body(self):
        text = "## [Unreleased]\n\n## [0.5.6] - 2026-11-09\n\n- Thing.\n\n## [0.5.5]\n"
        self.assertEqual(sync.release_section_body(
            text, sync.spec_for("math-audio"), "0.5.6"), "- Thing.")
        self.assertEqual(sync.release_section_body(
            text, sync.spec_for("math-audio"), "9.9.9"), "")


class SpecTests(unittest.TestCase):
    def test_unknown_workspace_is_a_hard_error(self):
        with self.assertRaises(sync.SyncError):
            sync.spec_for("gpui-toolkit")

    def test_foundations_come_first(self):
        specs = sync.selected_workspaces(["sotf", "math-audio", "autoeq"])
        self.assertEqual([spec.name for spec in specs],
                         ["math-audio", "autoeq", "sotf"])

    def test_gate_ladder_and_qa_opt_in(self):
        spec = sync.spec_for("sotf-daw")
        self.assertEqual(sync.gate_recipes(spec, include_qa=False), ["lint", "ntest"])
        self.assertEqual(sync.gate_recipes(spec, include_qa=True),
                         ["lint", "ntest", "qa"])
        systemwide = sync.spec_for("sotf-systemwide")
        self.assertEqual(sync.gate_recipes(systemwide, include_qa=False),
                         ["lint", "test"])


class ScratchRepoTests(unittest.TestCase):
    """End-to-end prepare+commit against a scratch root and submodule."""

    def setUp(self):
        import os
        import subprocess
        from unittest.mock import patch
        self.subprocess = subprocess
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "root"
        self.root.mkdir()
        self.bin = base / "bin"
        self.bin.mkdir()
        just = self.bin / "just"
        just.write_text('#!/bin/sh\nif [ "$1" = "--list" ]; then\n'
                        'echo lint; echo ntest; echo qa; echo check\nfi\nexit 0\n')
        just.chmod(0o755)
        cargo = self.bin / "cargo"
        cargo.write_text("#!/bin/sh\nexit 0\n")
        cargo.chmod(0o755)
        env = {"PATH": f"{self.bin}:{os.environ['PATH']}",
               "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.com",
               "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.com"}
        self.env_patch = patch.dict(os.environ, env)
        self.env_patch.start()
        ws = base / "ws-src"
        ws.mkdir()
        (ws / "Cargo.toml").write_text(
            '[workspace.package]\nversion = "0.5.5"\n\n[workspace]\nmembers = []\n')
        (ws / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [Unreleased]\n\n- New thing.\n\n## [0.5.5] - 2026-10-31\n")
        (ws / "lib.rs").write_text("// v1\n")
        self.git(ws, "init", "-b", "main")
        self.git(ws, "add", "-A")
        self.git(ws, "commit", "-m", "initial")
        self.git(self.root, "init", "-b", "main")
        self.git(self.root, "-c", "protocol.file.allow=always",
                 "submodule", "add", str(ws), "math-audio")
        self.git(self.root, "commit", "-m", "add submodule")
        (self.root / "math-audio/lib.rs").write_text("// v2\n")

    def tearDown(self):
        self.env_patch.stop()
        self.tmp.cleanup()

    def git(self, cwd, *args):
        self.subprocess.run(["git", *args], cwd=cwd, check=True,
                            capture_output=True, text=True)

    def git_out(self, cwd, *args):
        return self.subprocess.run(["git", *args], cwd=cwd, check=True,
                                   capture_output=True, text=True).stdout.strip()

    def test_prepare_then_commit(self):
        ws = self.root / "math-audio"
        code = sync.main(["prepare", "--root", str(self.root),
                          "--workspace", "math-audio"])
        self.assertEqual(code, 0)
        self.assertIn('version = "0.5.6"', (ws / "Cargo.toml").read_text())
        self.assertIn("## [0.5.6] - ", (ws / "CHANGELOG.md").read_text())
        self.assertIn("- New thing.",
                      sync.release_section_body((ws / "CHANGELOG.md").read_text(),
                                                sync.spec_for("math-audio"), "0.5.6"))
        code = sync.main(["commit", "--yes", "--stage-all", "--skip-top",
                          "--root", str(self.root), "--workspace", "math-audio"])
        self.assertEqual(code, 0)
        self.assertEqual(self.git_out(ws, "log", "-1", "--format=%s"), "release: v0.5.6")
        self.assertEqual(self.git_out(ws, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
