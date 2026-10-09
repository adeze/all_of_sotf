"""Resolution artifacts must identify proposed locks without claiming QA passed."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import resolve_sources


def workspace(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "root"
    child = root / "sotf"
    child.mkdir(parents=True)
    (child / "Cargo.toml").write_text("[package]\nname='sotf'\nversion='0.1.0'\n")
    (child / "Cargo.lock").write_text("old lock\n")
    output = tmp_path / "output"
    (output / "metadata").mkdir(parents=True)
    (output / "logs").mkdir()
    return root, output


def resolve_with_changes(tmp_path: Path, changes: list[str]) -> dict:
    root, output = workspace(tmp_path)

    def fake_cargo(_command, *, cwd, stdout, stderr, start_new_session):
        assert cwd == root / "sotf" and start_new_session
        (cwd / "Cargo.lock").write_text("new lock\n")
        stdout.write(json.dumps({"packages": []}))
        return SimpleNamespace(wait=lambda: 0)

    with patch.object(resolve_sources, "git", return_value="a" * 40):
        with patch.object(resolve_sources, "changed_paths", side_effect=[[], changes]):
            with patch.object(resolve_sources.subprocess, "Popen", side_effect=fake_cargo):
                result = resolve_sources.resolve_one("sotf", "a" * 40, root, output)
    assert (output / "locks/sotf/Cargo.lock.before").read_text() == "old lock\n"
    assert (output / "locks/sotf/Cargo.lock").read_text() == "new lock\n"
    return result


def test_resolution_preserves_lock_evidence_without_qa_claim(tmp_path: Path) -> None:
    result = resolve_with_changes(tmp_path, ["Cargo.lock"])
    assert result["status"] == "RESOLVED"
    assert result["lock_changed"] is True
    assert result["lock_before_sha256"] != result["lock_after_sha256"]


def test_resolution_rejects_non_lock_source_mutation(tmp_path: Path) -> None:
    result = resolve_with_changes(tmp_path, ["Cargo.lock", "Cargo.toml"])
    assert result["status"] == "FAIL"
    assert "beyond Cargo.lock" in result["error"]


def test_interruption_stops_cargo_and_preserves_partial_lock(tmp_path: Path) -> None:
    root, output = workspace(tmp_path)
    process = SimpleNamespace(pid=1234, returncode=None)

    def wait(*, timeout=None):
        if timeout is None and process.returncode is None:
            (root / "sotf" / "Cargo.lock").write_text("partial lock\n")
            raise KeyboardInterrupt
        process.returncode = -15
        return -15

    process.wait = wait
    process.poll = lambda: process.returncode
    with patch.object(resolve_sources, "git", return_value="a" * 40):
        with patch.object(resolve_sources, "changed_paths", side_effect=[[], ["Cargo.lock"]]):
            with patch.object(resolve_sources.subprocess, "Popen", return_value=process):
                with patch.object(resolve_sources.os, "killpg") as killpg:
                    killpg.side_effect = lambda *_: setattr(process, "returncode", -15)
                    with patch.object(resolve_sources, "process_group_alive", side_effect=[True, False]):
                        try:
                            resolve_sources.resolve_one("sotf", "a" * 40, root, output)
                        except resolve_sources.ResolutionInterrupted as error:
                            result = error.result
                        else:
                            raise AssertionError("interrupted resolution must raise")
    killpg.assert_called_once_with(1234, resolve_sources.signal.SIGTERM)
    assert result["status"] == "INTERRUPTED"
    assert result["exit_code"] == -15
    assert result["lock_changed"] is True
    assert (output / "locks/sotf/Cargo.lock").read_text() == "partial lock\n"


def test_main_writes_failure_report_for_interrupted_workspace(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    manifest = tmp_path / "sources.json"
    manifest.write_text("{}")
    output = tmp_path / "evidence"
    partial = {"workspace": "sotf", "status": "INTERRUPTED", "lock_changed": True}
    revisions = {name: "a" * 40 for name in resolve_sources.ORDER}
    with patch.object(resolve_sources, "read_manifest", return_value=("server", "owner", revisions)):
        with patch.object(resolve_sources, "resolve_one", side_effect=resolve_sources.ResolutionInterrupted(partial)):
            with patch.object(resolve_sources.signal, "signal"):
                assert resolve_sources.main(["--manifest", str(manifest), "--root", str(root),
                                             "--output", str(output)]) == 1
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "FAIL"
    assert report["interrupted"] is True
    assert report["workspaces"] == [partial]
    assert "finished_at" in report


def test_targeted_update_only_selects_registry_package(tmp_path: Path) -> None:
    lock = tmp_path / "Cargo.lock"
    lock.write_text('''version = 3
[[package]]
name = "wgpu"
version = "29.0.3"
source = "git+https://github.com/zed-industries/wgpu.git"
[[package]]
name = "wgpu"
version = "29.0.4"
source = "registry+https://github.com/rust-lang/crates.io-index"
''')
    assert resolve_sources.has_registry_package(lock, "wgpu", "29.0.4")
    assert not resolve_sources.has_registry_package(lock, "wgpu", "29.0.3")
    assert not resolve_sources.has_registry_package(lock, "naga", "29.0.4")


def test_targeted_updates_are_recorded_before_metadata(tmp_path: Path) -> None:
    root, output = workspace(tmp_path)
    old = root / "sotf"
    child = root / "autoeq"
    old.rename(child)
    (child / "Cargo.lock").write_text('''version = 3
[[package]]
name = "wgpu"
version = "29.0.4"
source = "registry+https://github.com/rust-lang/crates.io-index"
''')
    nested = child / "crates" / "autoeq-gpui-examples"
    nested.mkdir(parents=True)
    (nested / "Cargo.toml").write_text("[package]\nname='autoeq-gpui-examples'\nversion='0.1.0'\n")
    (nested / "Cargo.lock").write_text("old nested lock\n")
    commands = []

    def fake_cargo(command, *, cwd, stdout, stderr, start_new_session):
        commands.append((cwd, command))
        assert cwd in (child, nested) and start_new_session
        if command[1] == "update":
            (child / "Cargo.lock").write_text("version = 3\n")
        else:
            metadata = {"packages": []}
            if cwd == nested:
                (nested / "Cargo.lock").write_text("new nested lock\n")
                metadata = {"packages": [{"name": "autoeq-gpui-examples", "targets": [
                    {"name": "d3rs-spinorama", "kind": ["bin"]},
                    {"name": "px-spinorama", "kind": ["bin"]},
                ]}]}
            stdout.write(json.dumps(metadata))
        return SimpleNamespace(pid=1234, poll=lambda: 0, wait=lambda timeout=None: 0)

    updates = {"autoeq": (("wgpu", "29.0.4", "29.0.3"), ("naga", "29.0.4", "29.0.3"))}
    with patch.object(resolve_sources, "git", return_value="a" * 40):
        with patch.object(resolve_sources, "changed_paths", side_effect=[[], ["Cargo.lock"]]):
            with patch.object(resolve_sources.subprocess, "Popen", side_effect=fake_cargo):
                with patch.object(resolve_sources, "process_group_alive", return_value=False):
                    with patch.dict(resolve_sources.TARGETED_UPDATES, updates, clear=True):
                        result = resolve_sources.resolve_one("autoeq", "a" * 40, root, output)
    assert result["status"] == "RESOLVED"
    assert [(cwd, command[1]) for cwd, command in commands] == [
        (child, "update"), (child, "metadata"), (nested, "metadata")
    ]
    assert result["targeted_updates"][0]["status"] == "PASS"
    assert result["targeted_updates"][1]["status"] == "SKIPPED"
    assert result["targeted_updates"][0]["argv"] == [
        "cargo", "update", "-p", "wgpu@29.0.4", "--precise", "29.0.3",
    ]
    assert result["nested_workspace"]["status"] == "RESOLVED"
    assert result["nested_workspace"]["lock_before_sha256"] != result["nested_workspace"]["lock_after_sha256"]
    assert (output / "locks/autoeq-gpui-examples/Cargo.lock").read_text() == "new nested lock\n"


def test_missing_nested_demo_lock_fails_autoeq_resolution(tmp_path: Path) -> None:
    root, output = workspace(tmp_path)
    (root / "sotf").rename(root / "autoeq")
    (root / "autoeq" / "Cargo.lock").write_text("version = 3\n")

    def fake_cargo(_command, *, cwd, stdout, stderr, start_new_session):
        assert cwd == root / "autoeq" and start_new_session
        stdout.write(json.dumps({"packages": []}))
        return SimpleNamespace(wait=lambda: 0)

    with patch.object(resolve_sources, "git", return_value="a" * 40):
        with patch.object(resolve_sources, "changed_paths", side_effect=[[], []]):
            with patch.object(resolve_sources.subprocess, "Popen", side_effect=fake_cargo):
                result = resolve_sources.resolve_one("autoeq", "a" * 40, root, output)
    assert result["status"] == "FAIL"
    assert result["nested_workspace"]["status"] == "FAIL"
    assert "missing" in result["nested_workspace"]["error"]


def test_nested_demo_resolution_timeout_fails_and_cleans_group(tmp_path: Path) -> None:
    root, output = workspace(tmp_path)
    (root / "sotf").rename(root / "autoeq")
    (root / "autoeq" / "Cargo.lock").write_text("version = 3\n")
    nested = root / "autoeq" / "crates" / "autoeq-gpui-examples"
    nested.mkdir(parents=True)
    (nested / "Cargo.toml").write_text("[package]\nname='autoeq-gpui-examples'\nversion='0.1.0'\n")
    (nested / "Cargo.lock").write_text("old nested lock\n")

    def fake_cargo(command, *, cwd, stdout, stderr, start_new_session):
        assert start_new_session
        if cwd == nested:
            def wait(timeout=None):
                assert timeout == 900
                raise resolve_sources.subprocess.TimeoutExpired(command, timeout)
            return SimpleNamespace(pid=1234, returncode=None, wait=wait)
        stdout.write(json.dumps({"packages": []}))
        return SimpleNamespace(wait=lambda: 0)

    with patch.object(resolve_sources, "git", return_value="a" * 40):
        with patch.object(resolve_sources, "changed_paths", side_effect=[[], []]):
            with patch.object(resolve_sources.subprocess, "Popen", side_effect=fake_cargo):
                with patch.object(resolve_sources, "stop_process_group", return_value=True) as stopped:
                    result = resolve_sources.resolve_one("autoeq", "a" * 40, root, output)
    stopped.assert_called_once()
    assert result["status"] == "FAIL"
    assert result["nested_workspace"]["exit_code"] == 124
    assert "timed out" in result["nested_workspace"]["error"]


def test_failed_targeted_update_preserves_lock_and_skips_metadata(tmp_path: Path) -> None:
    root, output = workspace(tmp_path)
    (root / "sotf").rename(root / "autoeq")
    lock = root / "autoeq" / "Cargo.lock"
    lock.write_text('''version = 3
[[package]]
name = "wgpu"
version = "29.0.4"
source = "registry+https://github.com/rust-lang/crates.io-index"
''')

    def fake_cargo(command, *, cwd, stdout, stderr, start_new_session):
        assert command[1] == "update" and cwd == root / "autoeq"
        return SimpleNamespace(wait=lambda: 101)

    updates = {"autoeq": (("wgpu", "29.0.4", "29.0.3"), ("naga", "29.0.4", "29.0.3"))}
    with patch.object(resolve_sources, "git", return_value="a" * 40):
        with patch.object(resolve_sources, "changed_paths", side_effect=[[], []]):
            with patch.object(resolve_sources.subprocess, "Popen", side_effect=fake_cargo):
                with patch.dict(resolve_sources.TARGETED_UPDATES, updates, clear=True):
                    result = resolve_sources.resolve_one("autoeq", "a" * 40, root, output)
    assert result["status"] == "FAIL"
    assert result["targeted_updates"][0]["exit_code"] == 101
    assert not (output / "metadata/autoeq.json").exists()
    assert (output / "locks/autoeq/Cargo.lock").read_bytes() == lock.read_bytes()
