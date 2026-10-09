"""Pure preflight checks for the GPUI font qualification runner."""

from pathlib import Path
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts.release import gpui_font_check as gate


class FontGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        gate.enable_subreaper()

    def test_duplicate_groups_include_version_and_source_conflicts(self) -> None:
        locks = {
            "one": b'[[package]]\nname="shared"\nversion="1.0.0"\nsource="registry+a"\n',
            "two": b'[[package]]\nname="shared"\nversion="2.0.0"\nsource="registry+a"\n'
                   b'[[package]]\nname="shared"\nversion="1.0.0"\nsource="git+b"\n',
        }
        self.assertEqual(gate.duplicate_groups(locks), {
            ("multiple_versions", "shared"), ("mixed_sources", "shared")
        })

    def test_font_graph_requires_single_reviewed_source(self) -> None:
        packages = (
            '[[package]]\nname="sotf-cosmic-text"\nversion="0.19.0"\n'
            'dependencies=["fontdb"]\n'
            '[[package]]\nname="fontdb"\nversion="0.24.0"\n'
        ).encode()
        self.assertIn("gpui-toolkit/Cargo.lock", gate.font_graph({"gpui-toolkit/Cargo.lock": packages}))
        fontdb_only = b'[[package]]\nname="fontdb"\nversion="0.24.0"\n'
        self.assertEqual(
            set(gate.font_graph({"gpui-toolkit/Cargo.lock": packages,
                                 "sotf-capture/Cargo.lock": fontdb_only})),
            {"gpui-toolkit/Cargo.lock"},
        )
        with self.assertRaises(RuntimeError):
            gate.font_graph({"gpui-toolkit/Cargo.lock": packages.replace(b"0.24.0", b"0.23.0")})

    def test_malformed_process_inventory_fails_closed(self) -> None:
        result = subprocess.CompletedProcess([], 0, stdout="unexpected row\n", stderr="")
        with patch.object(gate.subprocess, "run", return_value=result):
            with self.assertRaises(ValueError):
                gate.members(12345)

    def test_interrupt_before_launch_starts_no_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(gate, "INTERRUPTED", True):
                with patch.object(gate.subprocess, "Popen") as launch:
                    result = gate.command("stopped", ["cargo", "check"], root, root)
            launch.assert_not_called()
            self.assertTrue(result["not_started"])
            self.assertEqual(result["status"], "FAIL")

    def test_command_report_persists_active_and_completed_states(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            report = {"commands": []}

            def completed_command(*_args, **_kwargs):
                active = json.loads((directory / "report.json").read_text())
                self.assertEqual(active["active_command"]["name"], "focused")
                return {"name": "focused", "status": "PASS", "exit_code": 0,
                        "cleanup": {"ok": True, "errors": [], "remaining": []}, "text": "ok"}

            with patch.object(gate, "command", side_effect=completed_command):
                self.assertEqual(
                    gate.run_required("focused", ["cargo", "check"], directory, directory, report),
                    "ok",
                )
            finished = json.loads((directory / "report.json").read_text())
            self.assertIsNone(finished["active_command"])
            self.assertEqual(finished["commands"][0]["exit_code"], 0)

    def test_exited_leader_still_cleans_owned_descendant(self) -> None:
        leader = subprocess.Popen(
            [sys.executable, "-c", "import subprocess,sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])"],
            start_new_session=True,
        )
        try:
            leader.wait(timeout=5)
            result = gate.clean_group(leader)
            self.assertTrue(result["ok"], result)
            self.assertEqual(gate.members(leader.pid), [])
        finally:
            try:
                os.killpg(leader.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            leader.wait(timeout=5)

    def test_signal_requests_graceful_cleanup_and_stops_next_launch(self) -> None:
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signum=signum), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                ready, graceful = directory / "ready", directory / "graceful"
                child_code = (
                    "import pathlib,signal,sys,time\n"
                    "ready,graceful=map(pathlib.Path,sys.argv[1:3])\n"
                    "def stop(_signum,_frame):\n"
                    "    graceful.write_text('terminated')\n"
                    "    raise SystemExit(0)\n"
                    "signal.signal(signal.SIGTERM,stop)\n"
                    "ready.write_text('ready')\n"
                    "while True: time.sleep(0.1)\n"
                )
                gate.INTERRUPTED = False
                previous = signal.signal(signum, gate.interrupt)

                def signal_ready_command() -> None:
                    deadline = time.monotonic() + 5
                    while not ready.is_file() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    os.kill(os.getpid(), signum)

                timer = threading.Thread(target=signal_ready_command)
                try:
                    timer.start()
                    result = gate.command("interrupted", [sys.executable, "-c", child_code,
                                                          str(ready), str(graceful)],
                                          directory, directory)
                    self.assertTrue(ready.is_file())
                    self.assertEqual(result["status"], "FAIL")
                    self.assertTrue(result["cleanup"]["ok"], result)
                    self.assertEqual(gate.members(result["owned_pgid"]), [])
                    self.assertEqual(graceful.read_text(), "terminated")
                    with patch.object(gate.subprocess, "Popen",
                                      side_effect=AssertionError("launched after stop")):
                        next_result = gate.command("must-not-launch", ["false"], directory, directory)
                    self.assertTrue(next_result["not_started"])
                finally:
                    timer.join(timeout=6)
                    signal.signal(signum, previous)
                    gate.INTERRUPTED = False


if __name__ == "__main__":
    unittest.main()
