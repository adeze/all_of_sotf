# all_of_sotf

This repository is the top-level tree for nine related Rust workspaces and
the shared `sotf-3rdparties/` vendored dependency repository. The
workspaces are kept as Git submodules so each project retains its own history,
Cargo workspace, tests, and release process.

| Workspace | Directory | Repository |
| --- | --- | --- |
| SOTF | `sotf/` | <https://github.com/pierreaubert/sotf> |
| AutoEQ | `autoeq/` | <https://github.com/pierreaubert/autoeq> |
| Math Audio | `math-audio/` | <https://github.com/pierreaubert/math-audio> |
| GPUI Toolkit | `gpui-toolkit/` | <https://github.com/pierreaubert/gpui-toolkit> |
| SOFA Reader | `sofa-reader/` | <https://github.com/pierreaubert/sofa-reader> |
| Symphonia Add-ons | git dependency | <https://github.com/pierreaubert/symphonia-add-ons> |
| Shared third-party crates | `sotf-3rdparties/` | <https://github.com/pierreaubert/sotf-3rdparties> |

There is no top-level `Cargo.toml`; run Cargo commands from a workspace or
pass its manifest path explicitly.

## Populate the Git tree

For a new clone, initialize the submodules as part of the clone:

```bash
git clone --recurse-submodules <superproject-url>
cd <superproject-directory>
```

If the repository is already cloned, populate or repair the workspace tree
from its root with:

```bash
git submodule sync --recursive
git submodule update --init --recursive
```

Check the checked-out commits with:

```bash
git submodule status
```

The superproject records an exact commit for every workspace. To intentionally
move the submodules to the latest commit from their configured remote branch,
review and commit the resulting gitlink changes in the superproject:

```bash
git submodule update --remote --merge
git submodule status
git diff --submodule
```

After the tree is populated, examples of workspace commands are:

```bash
cargo test --manifest-path sotf/Cargo.toml
cargo test --manifest-path autoeq/Cargo.toml
```

See each workspace's own `AGENTS.md` and README for workspace-specific
commands and prerequisites.

## Tools in `scripts/`

The scripts operate on the populated sibling workspaces from the repository
root. Install the shared Python dependencies when needed with:

```bash
python3 -m pip install -r scripts/requirements.txt
```

### `scripts/align-crates/` — Cargo dependency alignment

`align_crate_versions.py` scans Cargo manifests across all six workspaces and
reports external crates whose version requirements differ. It ignores path
dependencies and avoids changing risky platform/audio/UI packages by default.

Run a report without modifying manifests:

```bash
python3 scripts/align-crates/align_crate_versions.py
```

Apply compatible alignment changes only after reviewing the report:

```bash
python3 scripts/align-crates/align_crate_versions.py --apply
```

Useful options include `--root PATH`, `--allow-major`, and repeated
`--risky-package NAME`. The SOTF duplicate-version guard uses the checked-in
allowlist:

```bash
python3 scripts/align-crates/align_crate_versions.py --check-sotf-duplicates
python3 scripts/align-crates/align_crate_versions.py --update-sotf-allowlist
```

Run its regression tests with:

```bash
python3 -m pytest scripts/align-crates/align_crate_versions_test.py
```

### `scripts/buildbot/` — local cross-platform CI

The Buildbot setup runs the CI matrix for the workspaces and records build
history. Its detailed setup, worker requirements, and security settings are in
[`scripts/buildbot/README.md`](scripts/buildbot/README.md).

The common local workflow is:

```bash
cd scripts/buildbot
just install
just validate
just start
```

Open <http://localhost:8010> to view the dashboard. Use `just stop` to stop
the local master and worker, `just restart` to restart them, and
`just logs-master` or `just logs-worker` to follow logs. Linux latent-worker
image recipes are available as `just rebuild-linux` and
`just rebuild-linux-clean`.

### `scripts/quality-matrix/` — repository quality analysis

The quality-matrix tools analyze the six workspaces using the inventory in
[`scripts/quality-matrix/repos.json`](scripts/quality-matrix/repos.json).
`collect.py` produces static scores; `execute_quality.py` optionally runs
native tests, coverage, and benchmarks; and `coverage_complexity.py` reports
coverage for high-complexity Rust functions when coverage and TokenSave data
are available.

From the repository root, regenerate the static report with:

```bash
python3 scripts/quality-matrix/collect.py
```

Run the heavier execution-based checks when the required native toolchains are
installed:

```bash
python3 scripts/quality-matrix/execute_quality.py
```

Inspect high-complexity coverage for one workspace, for example:

```bash
python3 scripts/quality-matrix/coverage_complexity.py sotf
```

The generated `scores.json`, `matrix.md`, and execution log are written under
`scripts/quality-matrix/`. See
[`scripts/quality-matrix/README.md`](scripts/quality-matrix/README.md) for the
scoring methodology and toolchain prerequisites.
