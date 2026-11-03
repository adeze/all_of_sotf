"""Declarative SOTF Buildbot coverage matrix.

Keep this module free of Buildbot imports so the matrix can be validated with
the standard library before Buildbot loads ``master.cfg``.
"""

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Dict, Optional, Tuple

_SCRIPTS_DIR = next(
    candidate
    for parent in Path(__file__).resolve().parents
    for candidate in (parent, parent / "scripts")
    if (candidate / "workspaces.py").is_file()
)
sys.path.insert(0, str(_SCRIPTS_DIR))
from workspaces import workspace_names


@dataclass(frozen=True)
class Workspace:
    name: str
    branch: str
    test_recipes: Tuple[str, ...]
    qa_recipe: Optional[str] = "qa"


@dataclass(frozen=True)
class Platform:
    name: str
    worker: str
    root: str
    python: str


@dataclass(frozen=True)
class TargetBuild:
    name: str
    workspace: str
    worker: str
    root: str
    python: str
    recipe: str
    nightly: bool = True


_RECIPE_WORKSPACES = (
    Workspace("gpui-toolkit", "main", ("check", "lint", "ntest")),
    # math-audio deliberately has no `check` recipe; clippy and nextest both
    # compile the complete supported surface.
    Workspace("math-audio", "main", ("lint", "ntest")),
    Workspace("autoeq", "main", ("check", "lint", "ntest")),
    Workspace("sotf", "master", ("check", "lint", "ntest")),
    Workspace("sotf-daw", "main", ("check", "lint", "ntest")),
    Workspace("sotf-systemwide", "main", ("check", "lint", "test")),
    Workspace("sofa-reader", "main", ("all",), "all"),
)
_RECIPE_BY_NAME = {workspace.name: workspace for workspace in _RECIPE_WORKSPACES}
WORKSPACES: Tuple[Workspace, ...] = tuple(
    _RECIPE_BY_NAME.get(name, Workspace(name, "main", (), None))
    for name in workspace_names()
)


def gate_commands(workspace: Workspace, qa: bool = False) -> Tuple[Tuple[str, ...], ...]:
    """Use owning recipes, or complete Cargo gates for roots without recipes."""
    recipes = (workspace.qa_recipe,) if qa and workspace.qa_recipe else workspace.test_recipes
    if recipes:
        return tuple(("just", recipe) for recipe in recipes)
    return (
        ("cargo", "fmt", "--all", "--", "--check"),
        ("cargo", "check", "--workspace", "--all-targets"),
        ("cargo", "clippy", "--workspace", "--all-targets", "--", "-D", "warnings"),
        ("cargo", "test", "--workspace", "--all-targets"),
        ("cargo", "test", "--workspace", "--doc"),
    )

DESKTOP_PLATFORMS: Tuple[Platform, ...] = (
    Platform("macos", "macos-local", "/Volumes/home_ext1/src_pierre/all_of_sotf", "python3"),
    Platform("linux", "linux-docker", "/workspace", "python3"),
    Platform("windows", "windows-qemu", r"C:\workspace", "python"),
)

# Simulator/check targets are nightly. Device targets remain force-only because
# they may require connected hardware or signing/provisioning credentials.
TARGET_BUILDS: Tuple[TargetBuild, ...] = (
    TargetBuild("gpui-toolkit-android", "gpui-toolkit", "android-qemu", "/workspace", "python3", "showcase-android-check"),
    TargetBuild("gpui-toolkit-ios-sim", "gpui-toolkit", "macos-local", DESKTOP_PLATFORMS[0].root, "python3", "ios-sim"),
    TargetBuild("gpui-toolkit-ios-device", "gpui-toolkit", "macos-local", DESKTOP_PLATFORMS[0].root, "python3", "ios-device", nightly=False),
    TargetBuild("gpui-toolkit-tvos-sim", "gpui-toolkit", "macos-local", DESKTOP_PLATFORMS[0].root, "python3", "tvos-sim"),
    TargetBuild("gpui-toolkit-tvos-device", "gpui-toolkit", "macos-local", DESKTOP_PLATFORMS[0].root, "python3", "tvos-device", nightly=False),
    TargetBuild("sotf-android", "sotf", "android-qemu", "/workspace", "python3", "android-rust"),
    TargetBuild("sotf-ios-sim", "sotf", "macos-local", DESKTOP_PLATFORMS[0].root, "python3", "ios-sim"),
    TargetBuild("sotf-ios-device", "sotf", "macos-local", DESKTOP_PLATFORMS[0].root, "python3", "ios-device", nightly=False),
    TargetBuild("sotf-tvos-sim", "sotf", "macos-local", DESKTOP_PLATFORMS[0].root, "python3", "tvos-sim"),
    TargetBuild("sotf-tvos-device", "sotf", "macos-local", DESKTOP_PLATFORMS[0].root, "python3", "tvos-device", nightly=False),
)


def workspace_map() -> Dict[str, Workspace]:
    return {workspace.name: workspace for workspace in WORKSPACES}


def desktop_builder_names(workspace: str, include_qa: bool) -> Tuple[str, ...]:
    suffixes = ("tests", "qa") if include_qa else ("tests",)
    return tuple(
        f"{workspace}-{platform.name}-{suffix}"
        for platform in DESKTOP_PLATFORMS
        for suffix in suffixes
    )


def nightly_builder_names(workspace: str) -> Tuple[str, ...]:
    desktop = desktop_builder_names(workspace, include_qa=True)
    targets = tuple(
        target.name
        for target in TARGET_BUILDS
        if target.workspace == workspace and target.nightly
    )
    return desktop + targets


def validate_matrix() -> None:
    names = [workspace.name for workspace in WORKSPACES]
    if len(names) != len(set(names)):
        raise ValueError("workspace names must be unique")
    platform_names = [platform.name for platform in DESKTOP_PLATFORMS]
    if len(platform_names) != len(set(platform_names)):
        raise ValueError("platform names must be unique")
    known = set(names)
    for target in TARGET_BUILDS:
        if target.workspace not in known:
            raise ValueError(f"unknown target workspace: {target.workspace}")
    builder_names = []
    for workspace in names:
        builder_names.extend(nightly_builder_names(workspace))
    builder_names.extend(target.name for target in TARGET_BUILDS if not target.nightly)
    if len(builder_names) != len(set(builder_names)):
        raise ValueError("builder names must be unique")


validate_matrix()
