"""Regression tests for scripts/align_crate_versions.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from align_crate_versions import (
    DEFAULT_RISKY_PACKAGES,
    align_versions,
    build_change_map,
    compatibility_key,
    collect_sotf_duplicates,
    collect_versions,
    find_cargo_tomls,
    find_conflicts,
    load_allowlist,
    normalize_version,
    package_info,
    pick_target,
    report_change_plan,
    save_allowlist,
)


def test_normalize_version():
    assert str(normalize_version("=21.0.0")) == "21.0.0"
    assert str(normalize_version(">=0.4")) == "0.4"
    assert str(normalize_version("^1.2.3")) == "1.2.3"


def test_compatibility_key_matches_cargo_semver_groups():
    assert compatibility_key("1.2.3") == (1, None)
    assert compatibility_key("1.9.0") == (1, None)
    assert compatibility_key("0.4.1") == (0, 4)
    assert compatibility_key("0.5.0") == (0, 5)


def test_package_info_string():
    assert package_info("serde", "1.0") == ("serde", "1.0", "crates.io")


def test_package_info_table():
    assert package_info("serde", {"version": "1.0"}) == ("serde", "1.0", "crates.io")


def test_package_info_renamed():
    assert package_info("myserde", {"package": "serde", "version": "1.0"}) == (
        "serde",
        "1.0",
        "crates.io",
    )


def test_package_info_path_dependency_ignored():
    assert package_info("foo", {"path": "crates/foo", "version": "0.1.0"}) is None


def test_package_info_workspace_dependency_ignored():
    assert package_info("foo", {"workspace": True}) is None


def test_package_info_git_with_version_tracked():
    assert package_info(
        "foo", {"git": "https://example.com/foo", "version": "0.2.0"}
    ) == ("foo", "0.2.0", "git:https://example.com/foo")


def test_pick_target_prefers_highest_semver(tmp_path):
    (tmp_path / "sotf").mkdir()
    (tmp_path / "autoeq").mkdir()
    (tmp_path / "sotf" / "Cargo.toml").write_text(
        '[workspace]\n[workspace.dependencies]\nthiserror = "2.0.17"\n'
    )
    (tmp_path / "autoeq" / "Cargo.toml").write_text(
        '[workspace]\n[workspace.dependencies]\nthiserror = "2.0"\n'
    )
    by_package = collect_versions(tmp_path)
    conflicts, skipped = find_conflicts(by_package)
    assert "thiserror" in conflicts
    assert pick_target(conflicts["thiserror"]) == "2.0.17"
    assert not skipped


def test_path_dependencies_are_ignored(tmp_path):
    (tmp_path / "sotf").mkdir()
    (tmp_path / "sotf" / "Cargo.toml").write_text(
        '[workspace]\n[workspace.dependencies]\nmy-internal = { path = "crates/my-internal", version = "0.1.0" }\n'
    )
    by_package = collect_versions(tmp_path)
    assert "my-internal" not in by_package


def test_git_dependencies_with_version_are_tracked(tmp_path):
    (tmp_path / "sotf").mkdir()
    (tmp_path / "autoeq").mkdir()
    (tmp_path / "sotf" / "Cargo.toml").write_text(
        '[workspace]\n[workspace.dependencies]\nfoo = { git = "https://example.com/foo", version = "0.2.0" }\n'
    )
    (tmp_path / "autoeq" / "Cargo.toml").write_text(
        '[workspace]\n[workspace.dependencies]\nfoo = { git = "https://example.com/foo", version = "0.1.0" }\n'
    )
    by_package = collect_versions(tmp_path)
    conflicts, skipped = find_conflicts(by_package)
    assert "foo" in conflicts
    assert pick_target(conflicts["foo"]) == "0.2.0"
    assert not skipped


def test_worktrees_and_3rdparties_are_ignored(tmp_path):
    (tmp_path / "sotf").mkdir()
    (tmp_path / "sotf" / ".worktrees").mkdir()
    (tmp_path / "sotf" / "crates" / "3rdparties" / "foo").mkdir(parents=True)
    (tmp_path / "sotf" / "Cargo.toml").write_text(
        '[workspace]\n[workspace.dependencies]\nthiserror = "2.0.17"\n'
    )
    (tmp_path / "sotf" / ".worktrees" / "Cargo.toml").write_text(
        '[workspace]\n[workspace.dependencies]\nthiserror = "1.0.0"\n'
    )
    (tmp_path / "sotf" / "crates" / "3rdparties" / "foo" / "Cargo.toml").write_text(
        '[package]\nname = "foo"\nversion = "0.1.0"\n[dependencies]\nthiserror = "1.0.0"\n'
    )
    by_package = collect_versions(tmp_path)
    assert by_package["thiserror"] == [
        (tmp_path / "sotf" / "Cargo.toml", "workspace.dependencies", "2.0.17", "crates.io")
    ]


def test_inventory_covers_split_workspaces(tmp_path):
    for name in ("sotf-daw", "sotf-capture", "sotf-systemwide"):
        root = tmp_path / name
        root.mkdir()
        (root / "Cargo.toml").write_text('[dependencies]\nserde = "1"\n')
    entries = collect_versions(tmp_path)["serde"]
    assert {path.parent.name for path, *_ in entries} == {
        "sotf-daw", "sotf-capture", "sotf-systemwide"
    }


def test_discovery_prunes_snapshots_worktrees_and_symlinks(tmp_path):
    root = tmp_path / "sotf"
    root.mkdir()
    active = root / "Cargo.toml"
    active.write_text("[workspace]\n")
    for directory in (
        root / ".muse" / "worktrees" / "old",
        root / "audit" / "artifacts" / "selected-source",
        root / "target-static" / "generated",
        root / "nested-checkout",
    ):
        directory.mkdir(parents=True)
        (directory / "Cargo.toml").write_text("not even valid TOML")
    (root / "nested-checkout" / ".git").write_text("gitdir: elsewhere")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Cargo.toml").write_text("invalid TOML")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    assert find_cargo_tomls(tmp_path) == [active]


def test_mixed_sources_are_skipped(tmp_path):
    (tmp_path / "sotf").mkdir()
    (tmp_path / "autoeq").mkdir()
    (tmp_path / "sotf" / "Cargo.toml").write_text(
        '[workspace]\n[workspace.dependencies]\nfoo = { git = "https://example.com/foo", version = "0.2.0" }\n'
    )
    (tmp_path / "autoeq" / "Cargo.toml").write_text(
        '[package]\nname = "autoeq"\nversion = "0.1.0"\n[dependencies]\nfoo = "0.1.0"\n'
    )
    by_package = collect_versions(tmp_path)
    conflicts, skipped = find_conflicts(by_package)
    assert "foo" not in conflicts
    assert "foo" in skipped


def test_change_map_skips_risky_packages(tmp_path):
    (tmp_path / "sotf").mkdir()
    (tmp_path / "autoeq").mkdir()
    (tmp_path / "sotf" / "Cargo.toml").write_text(
        '[workspace]\n[workspace.dependencies]\ncpal = "0.15.2"\ngpui = "0.1.0"\n'
    )
    (tmp_path / "autoeq" / "Cargo.toml").write_text(
        '[workspace]\n[workspace.dependencies]\ncpal = "0.16.0"\ngpui = "0.2.0"\n'
    )
    conflicts, _skipped = find_conflicts(collect_versions(tmp_path))
    assert build_change_map(conflicts, risky_packages=DEFAULT_RISKY_PACKAGES) == {}


def test_change_map_aligns_only_semver_compatible_groups_by_default(tmp_path):
    (tmp_path / "sotf").mkdir()
    (tmp_path / "autoeq").mkdir()
    (tmp_path / "math-audio").mkdir()
    (tmp_path / "sotf" / "Cargo.toml").write_text(
        '[workspace]\n[workspace.dependencies]\nthiserror = "1.0.69"\n'
    )
    (tmp_path / "autoeq" / "Cargo.toml").write_text(
        '[workspace]\n[workspace.dependencies]\nthiserror = "2.0.17"\n'
    )
    (tmp_path / "math-audio" / "Cargo.toml").write_text(
        '[workspace]\n[workspace.dependencies]\nthiserror = "2.0.18"\n'
    )

    conflicts, _skipped = find_conflicts(collect_versions(tmp_path))
    changes = build_change_map(conflicts, risky_packages=set())

    assert changes == {
        tmp_path / "autoeq" / "Cargo.toml": {
            "workspace.dependencies": {"thiserror": "2.0.18"}
        }
    }


def test_target_dependencies_include_aliases_and_ordinary_tables(tmp_path):
    for name in ("sotf", "autoeq"):
        (tmp_path / name).mkdir()
    source = tmp_path / "sotf" / "Cargo.toml"
    source.write_text(
        '[target.\'cfg(target_os = "macos")\'.dependencies]\n'
        'renamed = { package = "serde", version = "1.0.1" }\n'
        '[target.\'cfg(target_os = "linux")\'.build-dependencies.serde]\n'
        'version = "1.0.1"\n'
        '[target.\'cfg(target_os = "windows")\'.dev-dependencies]\n'
        'serde = "1.0.1"\n'
    )
    (tmp_path / "autoeq" / "Cargo.toml").write_text(
        '[dependencies]\nserde = "1.0.2"\n'
    )
    entries = collect_versions(tmp_path)["serde"]
    assert {section for path, section, *_ in entries if path == source} == {
        'target.cfg(target_os = "macos").dependencies',
        'target.cfg(target_os = "linux").build-dependencies',
        'target.cfg(target_os = "windows").dev-dependencies',
    }
    conflicts, _ = find_conflicts(collect_versions(tmp_path))
    assert build_change_map(conflicts, risky_packages=set())[source] == {
        section: {"serde": "1.0.2"}
        for section in (
            'target.cfg(target_os = "macos").dependencies',
            'target.cfg(target_os = "linux").build-dependencies',
            'target.cfg(target_os = "windows").dev-dependencies',
        )
    }
    assert align_versions(tmp_path, conflicts, risky_packages=set()) == 0
    assert {
        version for path, _, version, _ in collect_versions(tmp_path)["serde"]
        if path == source
    } == {"1.0.2"}
    assert 'renamed = { package = "serde", version = "1.0.2" }' in source.read_text()


def test_skipped_mismatches_reported_without_safe_changes(tmp_path, capsys):
    path = tmp_path / "sotf" / "Cargo.toml"
    path.parent.mkdir()
    path.write_text(
        '[dependencies]\n'
        'cpal = "0.15"\nthiserror = "1"\n'
        'mixed = { git = "https://example.com/mixed", version = "1" }\n'
    )
    other = tmp_path / "autoeq" / "Cargo.toml"
    other.parent.mkdir()
    other.write_text(
        '[dependencies]\ncpal = "0.16"\nthiserror = "2"\nmixed = "2"\n'
    )
    conflicts, skipped = find_conflicts(collect_versions(tmp_path))
    assert report_change_plan(tmp_path, conflicts, skipped, DEFAULT_RISKY_PACKAGES, False) == 0
    output = capsys.readouterr().out
    assert "mixed sources" in output
    assert "risky package" in output and "cpal: 0.15, 0.16" in output
    assert "Left major-version splits unchanged" in output
    assert "thiserror: 1, 2" in output
    assert "No safe version mismatches to fix" in output

    assert align_versions(tmp_path, conflicts, skipped=skipped) == 0
    apply_output = capsys.readouterr().out
    assert "mixed sources" in apply_output
    assert "risky package" in apply_output
    assert "Left major-version splits unchanged" in apply_output


def _metadata_result(metadata: dict):
    class Result:
        returncode = 0
        stderr = ""
        stdout = json.dumps(metadata)

    return Result()


def test_collect_sotf_duplicates_from_metadata(tmp_path, monkeypatch):
    (tmp_path / "sotf").mkdir()
    (tmp_path / "sotf" / "Cargo.toml").write_text("[workspace]\nmembers = []\n")

    metadata = {
        "workspace_members": [],
        "packages": [
            {"name": "bitflags", "version": "1.3.2", "id": "bitflags 1.3.2"},
            {"name": "bitflags", "version": "2.13.0", "id": "bitflags 2.13.0"},
            {"name": "serde", "version": "1.0.210", "id": "serde 1.0.210"},
        ],
    }

    monkeypatch.setattr(
        "align_crate_versions.subprocess.run",
        lambda *args, **kwargs: _metadata_result(metadata),
    )
    duplicates, members = collect_sotf_duplicates(tmp_path)
    assert members == set()
    assert duplicates == {"bitflags": {"1.3.2", "2.13.0"}}


def test_workspace_members_are_excluded_from_duplicates(tmp_path, monkeypatch):
    (tmp_path / "sotf").mkdir()
    (tmp_path / "sotf" / "Cargo.toml").write_text("[workspace]\nmembers = []\n")

    metadata = {
        "workspace_members": ["path+file:///x#sotf-engine@1.0.29"],
        "packages": [
            {"name": "sotf-engine", "version": "1.0.29", "id": "path+file:///x#sotf-engine@1.0.29"},
            {"name": "sotf-engine", "version": "1.0.30", "id": "registry+https://example.com#sotf-engine@1.0.30"},
        ],
    }

    monkeypatch.setattr(
        "align_crate_versions.subprocess.run",
        lambda *args, **kwargs: _metadata_result(metadata),
    )
    duplicates, members = collect_sotf_duplicates(tmp_path)
    assert "sotf-engine" in members
    assert "sotf-engine" not in duplicates


def test_duplicate_check_uses_normal_cargo_and_workspace_config(tmp_path, monkeypatch):
    manifest = tmp_path / "sotf" / "Cargo.toml"
    manifest.parent.mkdir()
    manifest.write_text("[workspace]\n")

    def metadata(cmd, **kwargs):
        assert "--locked" not in cmd
        assert kwargs["cwd"] == manifest.parent
        return _metadata_result({"packages": [], "workspace_members": []})

    monkeypatch.setattr("align_crate_versions.subprocess.run", metadata)
    assert collect_sotf_duplicates(tmp_path) == ({}, set())


def test_allowlist_roundtrip(tmp_path):
    path = tmp_path / "allowlist.toml"
    save_allowlist(path, {"bitflags": {"1.3.2", "2.13.0"}})
    loaded = load_allowlist(path)
    assert loaded == {"bitflags": {"1.3.2", "2.13.0"}}
