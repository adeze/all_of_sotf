#!/usr/bin/env python3
"""Tests for the mono workspace generator.

Runs under pytest (`pytest scripts/mono`) and standalone
(`python3 scripts/mono/generate_test.py`) for environments without pytest.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate
from generate import MonoError


class TestSatisfies(unittest.TestCase):
    def test_caret_family(self):
        self.assertTrue(generate.satisfies("0.5", "0.5.77"))
        self.assertTrue(generate.satisfies("0.5.4", "0.5.77"))
        self.assertFalse(generate.satisfies("0.5", "0.6.0"))
        self.assertFalse(generate.satisfies("0.5.77", "0.5.76"))
        self.assertTrue(generate.satisfies("1", "1.16.3"))
        self.assertFalse(generate.satisfies("1", "2.0.0"))

    def test_operators(self):
        self.assertTrue(generate.satisfies(">=0.5.54", "0.5.77"))
        self.assertFalse(generate.satisfies(">=0.5.54", "0.5.53"))
        self.assertTrue(generate.satisfies("=0.2.189", "0.2.189"))
        self.assertFalse(generate.satisfies("=0.2.189", "0.2.190"))
        self.assertTrue(generate.satisfies(">=1.0, <2.0", "1.99.0"))
        self.assertFalse(generate.satisfies(">=1.0, <2.0", "2.0.0"))
        self.assertTrue(generate.satisfies("*", "9.9.9"))

    def test_unknown_shape_crashes(self):
        with self.assertRaises(MonoError):
            generate.satisfies(">=abc", "1.0.0")


class TestPickReq(unittest.TestCase):
    def test_most_specific_wins(self):
        self.assertEqual(generate.pick_req("x", ["1", "1.16.3"]), "1.16.3")
        self.assertEqual(
            generate.pick_req("x", ["4", "3", "4.0.1"]), "4.0.1"
        )
        self.assertEqual(
            generate.pick_req("x", ["=0.2.189", "0.2"]), "=0.2.189"
        )

    def test_identical_is_stable(self):
        self.assertEqual(generate.pick_req("x", ["1.0", "1.0"]), "1.0")


class TestMergeSpecs(unittest.TestCase):
    def test_internal_path_wins_and_drops_version(self):
        merged = generate.merge_specs(
            "math-dsp",
            [
                ("math-audio",
                 {"path": "math-audio/crates/math-dsp", "version": "0.5"}),
                ("sotf",
                 {"path": "math-audio/crates/math-dsp", "version": "0.5"}),
            ],
        )
        self.assertEqual(
            merged, {"path": "math-audio/crates/math-dsp"}
        )

    def test_internal_path_drops_git_companion(self):
        merged = generate.merge_specs(
            "symphonia-codec-dsd",
            [
                ("sotf", {"version": "0.2.0",
                          "git": "https://example.invalid/symphonia"}),
                ("symphonia-add-ons",
                 {"path": "symphonia-add-ons/symphonia-codec-dsd",
                  "version": "0.2.0"}),
            ],
        )
        self.assertEqual(
            merged, {"path": "symphonia-add-ons/symphonia-codec-dsd"}
        )

    def test_internal_paths_must_agree(self):
        with self.assertRaises(MonoError):
            generate.merge_specs(
                "dup",
                [("a", {"path": "a/x"}), ("b", {"path": "b/x"})],
            )

    def test_internal_features_union(self):
        merged = generate.merge_specs(
            "serde",
            [
                ("a", {"version": "1.0", "features": ["derive"]}),
                ("b", {"version": "1.0", "features": ["derive", "rc"]}),
            ],
        )
        self.assertEqual(merged["features"], ["derive", "rc"])

    def test_git_registry_mix_is_an_error(self):
        with self.assertRaises(MonoError):
            generate.merge_specs(
                "mix",
                [
                    ("a", {"version": "1.0"}),
                    ("b", {"version": "1.0",
                           "git": "https://example.invalid/r"}),
                ],
            )

    def test_default_features_disagreement_is_an_error(self):
        with self.assertRaises(MonoError):
            generate.merge_specs(
                "proptest",
                [
                    ("a", {"version": "1.9", "default-features": False,
                           "features": ["std"]}),
                    ("b", {"version": "1.9"}),
                ],
            )


class TestMergeLints(unittest.TestCase):
    def test_loosest_level_wins(self):
        merged, dropped = generate.merge_lints({
            "autoeq": {"clippy": {"perf": "warn"}},
            "sotf": {"clippy": {"perf": {"level": "allow"}}},
        })
        self.assertEqual(merged["clippy"]["perf"], {"level": "allow"})
        self.assertEqual(dropped, [])

    def test_non_universal_forbid_is_dropped(self):
        per_repo = {repo: {} for repo in generate.MONO_REPOS}
        per_repo["autoeq"] = {"rust": {"unsafe_code": "forbid"}}
        merged, dropped = generate.merge_lints(per_repo)
        self.assertNotIn("rust", merged)
        self.assertEqual(len(dropped), 1)
        self.assertIn("unsafe_code", dropped[0])

    def test_universal_deny_is_kept(self):
        per_repo = {
            repo: {"rust": {"warnings": "deny"}}
            for repo in generate.MONO_REPOS
        }
        merged, dropped = generate.merge_lints(per_repo)
        self.assertEqual(merged["rust"]["warnings"], {"level": "deny"})
        self.assertEqual(dropped, [])


def make_fixture_root(tmp: str) -> tuple[Path, dict]:
    """Eight empty repo workspaces plus overlays; returns (root, overlays)."""
    root = Path(tmp)
    overlays = {}
    for repo in generate.MONO_REPOS:
        repo_dir = root / repo
        repo_dir.mkdir()
        (repo_dir / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        overlays[repo] = {"mono": {}}
    return root, overlays


class TestCollectMembers(unittest.TestCase):
    def test_duplicate_package_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, overlays = make_fixture_root(tmp)
            for repo in ("autoeq", "sotf"):
                (root / repo / "Cargo.toml").write_text(
                    "[package]\nname = \"clash\"\nversion = \"0.1.0\"\n",
                    encoding="utf-8",
                )
            with self.assertRaisesRegex(MonoError, "duplicate package name"):
                generate.collect_members(root, overlays)

    def test_workspace_root_members_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, overlays = make_fixture_root(tmp)
            (root / "sotf-capture" / "Cargo.toml").write_text(
                "[workspace]\n[package]\nname = \"sotf-capture\"\n"
                'version = "0.5.1"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MonoError, "multiple workspace roots"):
                generate.collect_members(root, overlays)


class TestExcludedWorkspaceRoots(unittest.TestCase):
    def test_path_dependency_root_stays_standalone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, overlays = make_fixture_root(tmp)
            (root / "autoeq" / "Cargo.toml").write_text(
                '[package]\nname = "autoeq"\nversion = "0.1.0"\n'
                '[workspace]\nmembers = [".", "child"]\n', encoding="utf-8",
            )
            overlays["autoeq"] = {"mono": {"exclude_members": ["."]}}
            child = root / "autoeq" / "child"
            for directory in (root / "autoeq", child):
                (directory / "src").mkdir(parents=True)
                (directory / "src" / "lib.rs").write_text("", encoding="utf-8")
            (child / "Cargo.toml").write_text(
                '[package]\nname = "child"\nversion = "0.1.0"\n'
                '[dependencies]\nautoeq = { path = ".." }\n', encoding="utf-8",
            )
            mono = {"mono": {"exclude": []}, "package": {"version": "0.1.0"}}
            with patch.object(generate, "load_overlays", return_value=(mono, overlays)):
                text, _ = generate.generate(root)
            self.assertIn('    "autoeq",', text)
            self.assertIn('    "autoeq/child",', text)
            (root / "Cargo.toml").write_text(text, encoding="utf-8")
            result = subprocess.run(
                ["cargo", "metadata", "--offline", "--no-deps", "--format-version", "1"],
                cwd=root, capture_output=True, text=True, check=True,
            )
            metadata = json.loads(result.stdout)
            self.assertEqual(len(metadata["workspace_members"]), 1)
            self.assertEqual(metadata["packages"][0]["name"], "child")
            standalone = subprocess.run(
                ["cargo", "metadata", "--offline", "--no-deps", "--format-version", "1"],
                cwd=root / "autoeq", capture_output=True, text=True, check=True,
            )
            self.assertEqual(json.loads(standalone.stdout)["workspace_root"], str((root / "autoeq").resolve()))


class TestValidateExternalPaths(unittest.TestCase):
    def test_excluded_subtree_skips_union_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            member = root / "m"
            member.mkdir()
            (member / "Cargo.toml").write_text(
                "[package]\nname = \"m\"\nversion = \"0.1.0\"\n"
                "[dependencies]\n"
                'ext = { path = "../excluded/ext" }\n',
                encoding="utf-8",
            )
            target = root / "excluded" / "ext"
            target.mkdir(parents=True)
            (target / "Cargo.toml").write_text(
                "[package]\nname = \"ext\"\nversion.workspace = true\n"
                "[dependencies]\nnope.workspace = true\n",
                encoding="utf-8",
            )
            members = [("repo", "m", member / "Cargo.toml", "m")]
            # Excluded: own workspace context, only existence is checked.
            generate.validate_external_paths(
                root, members, "0.5.77", set(), set(), ["excluded"]
            )
            # Non-excluded: inherits must resolve from the mono union.
            with self.assertRaises(MonoError):
                generate.validate_external_paths(
                    root, members, "0.5.77", set(), set(), []
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
