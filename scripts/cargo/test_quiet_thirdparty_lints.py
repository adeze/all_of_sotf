"""Check diagnostic scope, compiler errors, runtime output, and exit status."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

WRAPPER = Path(__file__).with_name("quiet-thirdparty-lints.py").resolve()
spec = importlib.util.spec_from_file_location("quiet_lints", WRAPPER)
quiet_lints = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quiet_lints)
ROOT = WRAPPER.parents[2]


def diagnostic(level, *sources):
    return {"level": level, "spans": [
        {"file_name": str(ROOT / source), "is_primary": True} for source in sources
    ]}


class LintOutputTests(unittest.TestCase):
    def test_only_third_party_warning_primary_spans_are_hidden(self):
        vendor = "sotf-3rdparties/rfd/src/lib.rs"
        own = "sotf/crates/app-gpui/main.rs"
        self.assertTrue(quiet_lints.is_third_party_warning(diagnostic("warning", vendor)))
        for message in [diagnostic("error", vendor), diagnostic("warning", own),
                        diagnostic("warning", vendor, own), diagnostic("warning"),
                        diagnostic("warning", "sotf-3rdparties-lookalike/lib.rs")]:
            self.assertFalse(quiet_lints.is_third_party_warning(message))

    def test_cached_warnings_are_filtered_and_errors_and_program_output_survive(self):
        messages = []
        for level, source, rendered in [
            ("warning", "sotf-3rdparties/rfd/src/lib.rs", "vendor warning"),
            ("warning", "sotf/crates/app-gpui/main.rs", "own warning"),
            ("error", "sotf-3rdparties/rfd/src/lib.rs", "vendor error")
        ]:
            message = diagnostic(level, source)
            message["rendered"] = rendered + "\n"
            messages.append(json.dumps({"reason": "compiler-message", "message": message}))
        messages += ["application output", json.dumps({"application": "json output"})]
        with tempfile.TemporaryDirectory() as temporary:
            cargo = Path(temporary) / "cargo-fixture"
            cargo.write_text("#!" + sys.executable + "\nimport sys\nprint(" +
                             repr("\n".join(messages)) + ")\nsys.exit(7)\n")
            cargo.chmod(0o755)
            result = subprocess.run([sys.executable, str(WRAPPER), str(cargo), "check"],
                                    capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 7)
        self.assertNotIn("vendor warning", result.stderr)
        self.assertIn("own warning", result.stderr)
        self.assertIn("vendor error", result.stderr)
        self.assertIn("application output", result.stdout)
        self.assertIn('"application": "json output"', result.stdout)


if __name__ == "__main__":
    unittest.main()
