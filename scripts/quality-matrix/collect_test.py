"""Missing evidence must remain unassessed in static quality reports."""

import importlib.util
import json
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("quality_collect", Path(__file__).with_name("collect.py"))
collect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect)


class QualityEvidenceTests(unittest.TestCase):
    def test_untracked_source_marks_revision_as_dirty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "audio"
            source.mkdir()
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            (source / "untracked.rs").write_text("fn added() {}\n")
            with patch.object(collect, "ROOT", root):
                self.assertTrue(collect.source_dirty("audio"))

    def test_recursive_patterns_match_zero_and_multiple_directory_levels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = ["benches/bench.rs", "crates/a/benches/bench.rs",
                     "crates/a/benches/group/bench.rs", "integration-tests/foo/bar.rs",
                     "target/benches/ignored.rs"]
            for path in paths:
                item = root / "audio" / path
                item.parent.mkdir(parents=True, exist_ok=True)
                item.touch()
            with patch.object(collect, "ROOT", root):
                matches = collect.list_files("audio", ["**/benches/**/*.rs", "**/integration-tests/**"])
            self.assertEqual({str(path.relative_to(root / "audio")) for path in matches}, set(paths[:-1]))

    def test_unmeasured_complexity_does_not_receive_perfect_score(self):
        with patch.object(collect, "INDEX_COMPLEXITY", False):
            score, stats = collect.score_complexity({"path": "missing"})
        self.assertIsNone(score)
        self.assertIsNone(stats["high_function_count"])

    def test_absent_checkout_is_not_scored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(collect, "ROOT", root), \
                    patch.object(collect, "SCORES_JSON", root / "scores.json"), \
                    patch.object(collect, "MATRIX_MD", root / "matrix.md"), \
                    patch.object(collect, "load_repos", return_value=[{"path": "toolkit", "name": "toolkit"}]), \
                    patch.object(collect, "analyze_repo") as analyze:
                collect.build_matrix()
            analyze.assert_not_called()
            result = json.loads((root / "scores.json").read_text())
            self.assertEqual(result["repositories"], [])
            self.assertEqual(result["unassessed"][0]["name"], "toolkit")
            self.assertIn("generated_at", result)
            self.assertIn("no score assigned", (root / "matrix.md").read_text())

    def test_unavailable_index_does_not_receive_perfect_score(self):
        with patch.object(collect, "INDEX_COMPLEXITY", True), \
                patch.object(collect, "_tokensave_complexity_ranking", return_value=None):
            score, stats = collect.score_complexity({"path": "missing"})
        self.assertIsNone(score)
        self.assertIsNone(stats["high_function_count"])


if __name__ == "__main__":
    unittest.main()
