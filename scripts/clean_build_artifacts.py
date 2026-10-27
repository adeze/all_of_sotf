"""Remove Cargo compiler caches under root target/, preserving reports and logs."""

import os
from pathlib import Path
import shutil
import subprocess


def clean(root: Path) -> int:
    target = root / "target"
    if target.is_symlink():
        raise ValueError("Refusing to clean a symlinked target directory")
    if not target.exists():
        return 0
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "target"], cwd=root
    )
    if tracked:
        raise ValueError("Refusing to clean target/: it contains tracked files")

    profiles = []
    for directory, children, _ in os.walk(target, followlinks=False):
        # Cargo's marker distinguishes compiler output from saved QA evidence.
        if ".fingerprint" in children:
            profiles.append(Path(directory))
            children.clear()

    for profile in profiles:
        shutil.rmtree(profile)
        print(f"Removed {profile.relative_to(root)}")
    return len(profiles)


if __name__ == "__main__":
    count = clean(Path(__file__).resolve().parents[1])
    print(f"Removed {count} compiler cache directories; retained logs and reports.")
