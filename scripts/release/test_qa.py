"""Regression coverage for aggregate gates that must never report false passes."""

from pathlib import Path
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import qa


def test_qa_and_supervisor_import_without_cycle() -> None:
    root = Path(__file__).resolve().parents[2]
    probes = (
        "import scripts.release.qa as qa; import scripts.release.process_supervision; "
        "assert callable(qa.clean_group) and callable(qa.enable_subreaper)",
        "import scripts.release.process_supervision; import scripts.release.qa",
        "import sys; sys.path.insert(0, 'scripts/release'); "
        "import qa; import process_supervision; "
        "assert callable(qa.clean_group) and callable(qa.enable_subreaper)",
    )
    for probe in probes:
        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=root, capture_output=True, text=True,
            check=False,
        )
        assert result.returncode == 0, f"{probe}: {result.stderr}"


def fake_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "sotf"
    workspace.mkdir()
    (workspace / "Cargo.toml").write_text("[workspace]\n")
    (workspace / "Cargo.lock").write_text("original lock")
    output = tmp_path / "evidence"
    output.mkdir()
    monkeypatch.setattr(qa, "workspace_map", lambda: {"sotf": object()})
    monkeypatch.setattr(qa, "snapshot", lambda *args: {"revision": "abc123", "dirty": False})
    return workspace, output


def test_test_inventory_rejects_zero_selected_and_ignored(tmp_path):
    log = tmp_path / "tests.log"
    log.write_text("test result: ok. 0 passed; 0 failed; 0 ignored; 12 filtered out;\n")
    assert qa.test_inventory(log)["passed"] == 0
    log.write_text("test result: ok. 1 passed; 0 failed; 1 ignored; 0 filtered out;\n")
    assert qa.test_inventory(log) == {"passed": 1, "failed": 0, "ignored": 1,
                                      "summaries": 1, "named_passes": [], "ignored_named": {}}


def test_test_inventory_requires_named_passes_and_rejects_mixed_failures(tmp_path):
    log = tmp_path / "tests.log"
    log.write_text("test module::good ... ok\n"
                   "test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 filtered out;\n")
    inventory = qa.test_inventory(log)
    assert inventory["named_passes"] == ["module::good"]
    assert inventory["failed"] == 1
    log.write_text("    \x1b[32mPASS\x1b[0m [   0.009s] (   1/7970) driver-common tests::good\n"
                   "     Summary [   3.374s] 590/7970 tests run: 588 passed, 2 failed, 71 skipped\n")
    inventory = qa.test_inventory(log)
    assert inventory["named_passes"] == ["driver-common tests::good"]
    assert (inventory["passed"], inventory["failed"], inventory["ignored"]) == (588, 2, 71)


def test_test_inventory_keeps_compile_only_commands_exempt():
    assert not qa.requires_test_inventory(("cargo", "check"))
    assert qa.requires_test_inventory(("cargo", "test"))
    assert qa.requires_test_inventory(("just", "ntest"))


def test_rejected_upmixer_experiments_require_exact_inventory(tmp_path):
    log = tmp_path / "upmixer.log"
    lines = [f"test module::{name} ... ignored, {reason}"
             for name, reason in qa.REJECTED_UPMIXER_EXPERIMENTS.items()]
    log.write_text("\n".join(lines) + "\n"
                   "test result: ok. 147 passed; 0 failed; 3 ignored; 0 filtered out;\n")
    command = ("cargo", "test", "-p", "sotf-plugin-upmixer",
               "--features", "onnx", "--lib")
    inventory = qa.test_inventory(log)
    inventory["named_passes"] = [f"module::other_{index}" for index in range(145)] + [
        "module::aud132_live_source_tags_align_above_512_exact_and_noninteger_tones",
        "module::aud132_preserves_small_fft_and_512_pre_edit_full_output_controls",
    ]
    assert qa.approved_ignored_inventory("sotf-daw", command, inventory)
    inventory["ignored_named"]["rejected_static_delay_candidate_does_not_align_fixed_gain_tone_phase"] = "different reason"
    assert not qa.approved_ignored_inventory("sotf-daw", command, inventory)
    assert not qa.approved_ignored_inventory("sotf", command, qa.test_inventory(log))


def test_gpui_release_validator_requires_artifacts_from_current_revision(tmp_path):
    command = ("just", "qa-release-evidence")
    assert qa.validator_issues("gpui-toolkit", command, tmp_path, "reviewed")
    manifest = tmp_path / "target/qa/release-evidence.json"
    manifest.parent.mkdir(parents=True)
    artifact = tmp_path / "target/qa/metal-render.png"
    artifact.write_bytes(b"known captured pixels")
    document = {"schema_version": 1,
                "report_type": "gpui-toolkit-release-evidence-manifest",
                "artifacts": [{"path": "target/qa/metal-render.png",
                               "size_bytes": artifact.stat().st_size,
                               "sha256": qa.hashlib.sha256(artifact.read_bytes()).hexdigest()}],
                "source": {"revision": "stale", "dirty": False}}
    manifest.write_text(json.dumps(document))
    assert qa.validator_issues("gpui-toolkit", command, tmp_path, "reviewed")
    document["source"]["revision"] = "reviewed"
    manifest.write_text(json.dumps(document))
    assert qa.validator_issues("gpui-toolkit", command, tmp_path, "reviewed") == []
    artifact.write_bytes(b"tampered captured pixels")
    assert qa.validator_issues("gpui-toolkit", command, tmp_path, "reviewed")


def test_interruption_before_spawn_does_not_launch_command(tmp_path, monkeypatch):
    _, output = fake_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(qa, "commands_for", lambda *_: [(sys.executable, "-c", "pass")])
    monkeypatch.setattr(qa, "STOP", True)
    with monkeypatch.context() as scoped:
        scoped.setattr(qa.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("child launched after STOP")))
        try:
            qa.run_workspace("sotf", "check", tmp_path, output, False)
        except qa.GateInterrupted as error:
            assert error.result["commands"] == []
        else:
            raise AssertionError("STOP did not interrupt the workspace")


def test_owned_descendant_is_cleaned_after_leader_exits(tmp_path, monkeypatch):
    _, output = fake_workspace(tmp_path, monkeypatch)
    qa.enable_subreaper()
    ready = tmp_path / "descendant-ready"
    program = ("import pathlib, subprocess, sys, time; "
               "ready=pathlib.Path(sys.argv[1]); "
               "subprocess.Popen([sys.executable, '-c', "
               "'import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(\"ready\"); time.sleep(30)', "
               "str(ready)]); "
               "deadline=time.monotonic()+5; "
               "exec('while not ready.exists() and time.monotonic()<deadline: time.sleep(0.01)')")
    monkeypatch.setattr(qa, "commands_for", lambda *_: [(sys.executable, "-c", program, str(ready))])
    result = qa.run_workspace("sotf", "check", tmp_path, output, False)
    assert ready.read_text() == "ready"
    assert result["commands"][0]["owned_group_cleanup"]["ok"] is True


def test_sigterm_during_live_owned_command_cleans_group_and_stops_next(tmp_path, monkeypatch):
    _, output = fake_workspace(tmp_path, monkeypatch)
    qa.enable_subreaper()
    ready = tmp_path / "live-ready"
    descendant = tmp_path / "descendant-ready"
    program = ("import pathlib,subprocess,sys,time; "
               "pathlib.Path(sys.argv[1]).write_text('ready'); "
               "subprocess.Popen([sys.executable,'-c',"
               "'import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(\"ready\"); time.sleep(30)',"
               "sys.argv[2]]); time.sleep(30)")
    monkeypatch.setattr(qa, "commands_for", lambda *_: [
        (sys.executable, "-c", program, str(ready), str(descendant)),
        (sys.executable, "-c", "raise AssertionError('later command launched')"),
    ])
    previous_handler = signal.signal(signal.SIGTERM, qa.interrupted)
    qa.STOP = False
    cancel = threading.Event()

    def interrupt_when_ready() -> None:
        deadline = time.monotonic() + 5
        while (not ready.exists() or not descendant.exists()) and time.monotonic() < deadline and not cancel.is_set():
            time.sleep(0.01)
        if not cancel.is_set():
            os.kill(os.getpid(), signal.SIGTERM)

    sender = threading.Thread(target=interrupt_when_ready)
    sender.start()
    try:
        try:
            qa.run_workspace("sotf", "check", tmp_path, output, False)
        except qa.GateInterrupted as error:
            assert ready.is_file() and descendant.is_file()
            assert len(error.result["commands"]) == 1
            assert error.result["commands"][0]["owned_group_cleanup"]["ok"] is True
        else:
            raise AssertionError("SIGTERM did not interrupt the live command")
    finally:
        cancel.set()
        sender.join(timeout=6)
        signal.signal(signal.SIGTERM, previous_handler)
        qa.STOP = False


def test_missing_workspace_is_failure(tmp_path):
    result = qa.run_workspace("sotf", "metadata", tmp_path, tmp_path, False)
    assert result["status"] == "FAIL"
    assert "missing workspace" in result["error"]


def test_command_failure_stops_workspace_and_keeps_log(tmp_path, monkeypatch, capsys):
    _, output = fake_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(qa, "toolchain_identity",
                        lambda cwd, env=None: {"working_directory": str(cwd), "rustc": "workspace-pinned"})
    monkeypatch.setattr(qa, "commands_for", lambda *_: [
        (sys.executable, "-c", "print('compile failure'); raise SystemExit(7)"),
        (sys.executable, "-c", "raise AssertionError('must not run')"),
    ])
    result = qa.run_workspace("sotf", "check", tmp_path, output, False)
    assert result["status"] == "FAIL"
    assert len(result["commands"]) == 1
    assert result["commands"][0]["exit_code"] == 7
    assert result["commands"][0]["toolchain"] == {
        "working_directory": str(tmp_path / "sotf"), "rustc": "workspace-pinned"}
    assert "compile failure" in Path(result["commands"][0]["log"]).read_text()
    assert "compile failure" in capsys.readouterr().out


def test_failure_tail_is_limited_to_last_eighty_lines(tmp_path, capsys):
    log = tmp_path / "failed.log"
    log.write_text("".join(f"line {number}\n" for number in range(100)))
    qa.print_failure_tail(log)
    output = capsys.readouterr().out
    assert "line 19\n" not in output
    assert "line 20\n" in output
    assert "line 99\n" in output


def test_successful_command_cannot_change_lockfile(tmp_path, monkeypatch):
    _, output = fake_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(qa, "commands_for", lambda *_: [
        (sys.executable, "-c", "from pathlib import Path; Path('Cargo.lock').write_text('changed')"),
    ])
    result = qa.run_workspace("sotf", "check", tmp_path, output, False)
    assert result["status"] == "FAIL"
    assert result["error"] == "sotf: Cargo.lock changed"


def test_clean_release_requirement_rejects_dirty_sources(tmp_path, monkeypatch):
    _, output = fake_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(qa, "snapshot", lambda *_: {"revision": "abc123", "dirty": True})
    result = qa.run_workspace("sotf", "qa", tmp_path, output, True)
    assert result["status"] == "FAIL"
    assert result["commands"] == []


def test_sibling_change_fails_aggregate_source_guard():
    before = {"sotf": {"revision": "a", "dirty": False, "lock_sha256": "1"},
              "sotf-daw": {"revision": "b", "dirty": False, "lock_sha256": "2"}}
    after = {"sotf": before["sotf"],
             "sotf-daw": {"revision": "b", "dirty": False, "lock_sha256": "3"}}
    assert qa.source_issues(before, after, True) == ["sotf-daw: Cargo.lock changed"]


def test_shared_vendor_source_is_guarded_without_root_manifest(tmp_path, monkeypatch):
    (tmp_path / "sotf-3rdparties").mkdir()
    monkeypatch.setattr(qa, "snapshot", lambda *args: {"revision": "abc", "dirty": False})
    before = qa.source_state(tmp_path, ["sotf-3rdparties"], "macos")
    assert qa.source_issues(before, before, True) == []
    monkeypatch.setattr(qa, "snapshot", lambda *args: {"revision": "abc", "dirty": True})
    after = qa.source_state(tmp_path, ["sotf-3rdparties"], "macos")
    assert qa.source_issues(before, after, True) == ["sotf-3rdparties: source revision or cleanliness changed"]


def test_nested_autoeq_lock_is_required_and_guarded(tmp_path, monkeypatch):
    workspace = tmp_path / "autoeq"
    nested = workspace / "crates" / "autoeq-gpui-examples"
    nested.mkdir(parents=True)
    (workspace / "Cargo.toml").write_text("[workspace]\n")
    (workspace / "Cargo.lock").write_text("root lock")
    (nested / "Cargo.lock").write_text("nested lock")
    monkeypatch.setattr(qa, "snapshot", lambda *args: {"revision": "abc", "dirty": False})
    before = qa.source_state(tmp_path, ["autoeq"], "linux")
    assert qa.source_issues(before, before, True) == []
    (nested / "Cargo.lock").write_text("changed nested lock")
    assert qa.source_issues(before, qa.source_state(tmp_path, ["autoeq"], "linux"), True) == [
        "autoeq: nested GPUI examples Cargo.lock changed"
    ]
    (nested / "Cargo.lock").unlink()
    assert qa.source_issues(qa.source_state(tmp_path, ["autoeq"], "linux"),
                            qa.source_state(tmp_path, ["autoeq"], "linux"), True) == [
        "autoeq: nested GPUI examples Cargo.lock missing"
    ]


def test_nested_demo_commands_are_required():
    demo = "crates/autoeq-gpui-examples/Cargo.toml"
    metadata = ("cargo", "metadata", "--format-version", "1",
                "--all-features", "--manifest-path", demo)
    check = ("cargo", "check", "--all-targets", "--all-features",
             "--manifest-path", demo)
    assert metadata in qa.commands_for("autoeq", "metadata", "linux")
    assert check in qa.commands_for("autoeq", "check", "macos")
    assert check in qa.commands_for("autoeq", "qa", "linux")


def test_nested_demo_metadata_requires_both_real_binary_targets(tmp_path):
    log = tmp_path / "demo.log"
    metadata = {"packages": [{"name": "autoeq-gpui-examples", "targets": [
        {"name": "d3rs-spinorama", "kind": ["bin"]},
        {"name": "px-spinorama", "kind": ["bin"]},
    ]}]}
    log.write_text("warning: cached index\n" + json.dumps(metadata) + "\n")
    assert qa.demo_metadata_issues(log) == []
    metadata["packages"][0]["targets"].pop()
    log.write_text(json.dumps(metadata) + "\n")
    assert qa.demo_metadata_issues(log) == ["nested AutoEQ demo binary missing: px-spinorama"]


def test_sotf_qa_covers_application_and_integration_suites():
    commands = qa.commands_for("sotf", "qa", "macos")
    for recipe in ("qa", "test-pr", "ntest", "itest", "dev-driver-full", "dev-driver-roomeq"):
        assert ("just", recipe) in commands


def test_daw_qa_covers_cross_format_plugin_comparison():
    for host in ("macos", "linux"):
        assert ("just", "qa-plugins-cross-format") in qa.commands_for("sotf-daw", "qa", host)


def test_linux_daw_qa_requires_feature_gated_sandbox_worker():
    sandbox = (
        "cargo", "test", "-p", "sotf-host",
        "--features", "worker-test-backend,external-plugin-clap",
        "--test", "external_plugin_isolation", "--", "--nocapture",
    )
    assert sandbox in qa.commands_for("sotf-daw", "qa", "linux")
    assert sandbox not in qa.commands_for("sotf-daw", "qa", "macos")
    assert sandbox not in qa.commands_for("sotf-daw", "tests", "linux")


def test_successful_sandbox_test_with_kernel_skip_fails_qa(tmp_path, monkeypatch):
    workspace = tmp_path / "sotf-daw"
    workspace.mkdir()
    (workspace / "Cargo.toml").write_text("[workspace]\n")
    (workspace / "Cargo.lock").write_text("original lock")
    output = tmp_path / "evidence"
    output.mkdir()
    monkeypatch.setattr(qa, "snapshot", lambda *args: {"revision": "abc123", "dirty": False})
    sandbox = qa.commands_for("sotf-daw", "qa", "linux")[-1]
    monkeypatch.setattr(qa, "commands_for", lambda *_: [sandbox])

    real_popen = qa.subprocess.Popen
    def fake_popen(command, *args, **kwargs):
        if command != sandbox:
            # The cleanup helper also uses subprocess. Leave its process
            # inspection and reaping calls intact.
            return real_popen(command, *args, **kwargs)
        assert kwargs["cwd"] == workspace
        program = ("print('test sandbox::owned_worker_starts ... ok'); "
                   "print('test result: ok. 1 passed; 0 failed; 0 ignored; 0 filtered out;'); "
                   "print('SKIP: kernel sandbox unavailable (backend LinuxLandlock)')")
        return real_popen((sys.executable, "-c", program), *args, **kwargs)

    monkeypatch.setattr(qa.subprocess, "Popen", fake_popen)
    result = qa.run_workspace("sotf-daw", "qa", tmp_path, output, False, "linux")
    assert result["status"] == "FAIL"
    assert result["commands"][0]["exit_code"] == 0
    assert result["error"] == "required Linux sandbox coverage skipped; later commands were not run"
    assert result["gates"][-1]["status"] == "FAIL"


def test_sandbox_skip_marker_is_scoped_to_linux_daw_qa(tmp_path):
    log = tmp_path / "sandbox.log"
    log.write_text("SKIP: kernel sandbox unavailable\n")
    sandbox = qa.commands_for("sotf-daw", "qa", "linux")[-1]
    assert qa.sandbox_coverage_skipped("sotf-daw", "qa", "linux", sandbox, log)
    assert not qa.sandbox_coverage_skipped("sotf-daw", "qa", "macos", sandbox, log)
    assert not qa.sandbox_coverage_skipped("sotf-daw", "tests", "linux", sandbox, log)


def test_toolkit_strict_gate_runs_only_on_macos():
    assert ("just", "qa-release-evidence") in qa.commands_for("gpui-toolkit", "qa", "macos")
    linux = qa.commands_for("gpui-toolkit", "qa", "linux")
    assert ("just", "qa") in linux
    assert ("just", "qa-release-evidence") not in linux


def test_darwin_host_uses_strict_macos_gate(monkeypatch):
    monkeypatch.setattr(qa.platform, "system", lambda: "Darwin")
    assert qa.host_platform() == "macos"
    assert ("just", "qa-release-evidence") in qa.commands_for("gpui-toolkit", "qa")


class ToolchainEvidenceTests(unittest.TestCase):
    def test_cargo_environment_resolves_default_symlinked_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home with spaces"
            physical_cache = root / "cache" / ".cargo"
            home.mkdir()
            physical_cache.mkdir(parents=True)
            (home / ".cargo").symlink_to(physical_cache, target_is_directory=True)
            with mock.patch.dict(qa.os.environ, {"HOME": str(home)}, clear=True):
                environment = qa.cargo_environment(root)
                self.assertEqual(environment["CARGO_HOME"], str(physical_cache.resolve()))
                self.assertTrue(physical_cache.is_dir())

    def test_cargo_environment_resolves_explicit_symlink_without_mutating_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            physical_cache = root / "physical cache"
            physical_cache.mkdir()
            alias = root / "cargo alias"
            alias.symlink_to(physical_cache, target_is_directory=True)
            with mock.patch.dict(qa.os.environ, {"CARGO_HOME": str(alias)}):
                environment = qa.cargo_environment(root)
                self.assertEqual(environment["CARGO_HOME"], str(physical_cache.resolve()))
                self.assertTrue(alias.is_symlink())

    def test_identity_queries_versions_from_workspace_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            calls = []

            def identify(command, **kwargs):
                calls.append((command[0], kwargs["cwd"]))
                return SimpleNamespace(stdout=f"{command[0]} workspace version\n")

            with (mock.patch.object(qa.subprocess, "run", side_effect=identify),
                  mock.patch.object(qa.platform, "platform", return_value="test-platform"),
                  mock.patch.object(qa.platform, "machine", return_value="test-machine")):
                identity = qa.toolchain_identity(workspace)

        self.assertEqual(calls, [("rustc", workspace), ("cargo", workspace)])
        self.assertEqual(identity["working_directory"], str(workspace.resolve()))
        self.assertEqual(identity["rustc"], "rustc workspace version")
        self.assertEqual(identity["cargo"], "cargo workspace version")

    def test_workspace_command_records_effective_toolchain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "sotf"
            workspace.mkdir()
            (workspace / "Cargo.toml").write_text("[workspace]\n")
            (workspace / "Cargo.lock").write_text("lock")
            output = root / "evidence"
            output.mkdir()
            toolchain = {"working_directory": str(workspace.resolve()),
                         "rustc": "rustc workspace version"}
            child = mock.Mock(pid=12345)
            child.wait.return_value = 0
            child.poll.return_value = 0
            source = {"sotf": {"revision": "abc123", "dirty": False,
                               "lock_sha256": "same-lock"}}
            home = root / "home"
            physical_cache = root / "physical-cache"
            home.mkdir()
            physical_cache.mkdir()
            (home / ".cargo").symlink_to(physical_cache, target_is_directory=True)
            with (mock.patch.dict(qa.os.environ, {"HOME": str(home)}, clear=True),
                  mock.patch.object(qa, "workspace_map", return_value={"sotf": object()}),
                  mock.patch.object(qa, "source_state", return_value=source),
                  mock.patch.object(qa, "commands_for", return_value=[("cargo", "check")]),
                  mock.patch.object(qa, "toolchain_identity", return_value=toolchain),
                  mock.patch.object(qa.subprocess, "Popen", return_value=child) as spawn,
                  mock.patch.object(qa, "clean_group", return_value={"ok": True, "remaining": []})):
                result = qa.run_workspace("sotf", "check", root, output, False)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["commands"][0]["toolchain"], toolchain)
        spawn.assert_called_once()
        self.assertEqual(spawn.call_args.kwargs["env"]["CARGO_HOME"],
                         str(physical_cache.resolve()))
