import subprocess
import tempfile
import unittest
from pathlib import Path

from clean_build_artifacts import clean


class CleanBuildArtifactsTests(unittest.TestCase):
    def test_removes_profiles_preserving_evidence_and_external_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for profile in ("debug", "style-review/release", "style-review/wasm32-unknown-unknown/release"):
                (root / "target" / profile / ".fingerprint").mkdir(parents=True)
            evidence = root / "target/style-review/logs/result.json"
            evidence.parent.mkdir()
            evidence.write_text("{}")
            external = root / "external/debug/.fingerprint"
            external.mkdir(parents=True)
            (root / "target/linked").symlink_to(root / "external", target_is_directory=True)
            self.assertEqual(clean(root), 3)
            self.assertTrue(evidence.is_file())
            self.assertTrue(external.is_dir())
            self.assertEqual(clean(root), 0)

    def test_refuses_tracked_content_and_symlinked_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            tracked = root / "target/debug/.fingerprint/receipt"
            tracked.parent.mkdir(parents=True)
            tracked.write_text("keep")
            subprocess.run(["git", "add", "target"], cwd=root, check=True)
            with self.assertRaisesRegex(ValueError, "tracked"):
                clean(root)
            self.assertTrue(tracked.is_file())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "external").mkdir()
            (root / "target").symlink_to(root / "external", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlinked"):
                clean(root)


if __name__ == "__main__":
    unittest.main()
