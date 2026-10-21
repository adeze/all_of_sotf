# all_of_sotf

This repository is the top-level tree for nine related Rust workspaces and
the shared `sotf-3rdparties/` vendored dependency repository. The
workspaces are kept as Git submodules so each project retains its own history,
Cargo workspace, tests, and release process.

The umbrella also inventories the sibling Swift and Python applications without
moving or copying their checkouts. `scripts/quality-matrix/repos.json` records
them separately from Cargo/release workspaces:

```sh
uv run --no-project python scripts/workspaces.py --applications
uv run --no-project python scripts/workspaces.py --all-sources
```

Paths are relative to this umbrella root. The default command remains the Rust
workspace list used by existing build tooling. Companion checkouts are not
automatically cloned, built or included in Rust releases. Their own lockfiles,
instructions and build tools remain authoritative. Private project data stays
outside source inventory; see [storage and capability plan](ADEZE_WORKBENCH_SPEC.md).

| Workspace | Directory | Repository |
| --- | --- | --- |
| SOTF | `sotf/` | <https://github.com/adeze/sotf> |
| SOTF DAW and plugins | `sotf-daw/` | <https://github.com/adeze/sotf-daw> |
| Systemwide | `sotf-systemwide/` | <https://github.com/adeze/sotf-systemwide> |
| Capture | `sotf-capture/` | <https://github.com/adeze/sotf-capture> |
| AutoEQ | `autoeq/` | <https://github.com/adeze/autoeq> |
| Math Audio | `math-audio/` | <https://github.com/adeze/math-audio> |
| GPUI Toolkit | `gpui-toolkit/` | <https://github.com/adeze/gpui-toolkit> |
| SOFA Reader | `sofa-reader/` | <https://github.com/adeze/sofa-reader> |
| Symphonia Add-ons | `symphonia-add-ons/` | <https://github.com/adeze/symphonia-add-ons> |
| Shared third-party crates | `sotf-3rdparties/` | <https://github.com/adeze/sotf-3rdparties> |

The generated top-level `Cargo.toml` supports aggregate checks; see
[`scripts/mono/README.md`](scripts/mono/README.md) for exclusions and metadata
limits. Standalone workspace manifests remain authoritative for publishing.

## Adeze integration checkout

Submodules use Adeze forks and the `codex/all-of-sotf` integration branches.
Gitlinks pin the compatible source revisions; the branch setting only affects
explicit `git submodule update --remote` operations. Existing older Adeze-only
changes are not implicitly merged into these revisions.

On the original development machine these directories are linked Git worktrees
sharing object stores with sibling clones. Their existing branches and working
files remain separate. Remove a linked checkout with `git worktree remove`
from its owning clone, rather than deleting its Git administration files.

Run Cargo from this tree so its `.cargo/config.toml` applies. Other projects
depending on these crates must supply their own source overrides; Cargo ignores
dependency-local patch configuration.

The local source overrides cover the shared GPUI, SOFA, Symphonia, vendor and
registry Math Audio dependencies. Capture's legacy `block` compatibility patch
retains its pinned Git source: the current vendor package is renamed
`sotf-block`, which is not a transparent replacement. Local-source resolution
and compilation are separate checks; refresh affected lockfiles deliberately
when changing integration revisions.

Integration validation (2026-10-22): all 53 override paths match package names;
root and SOTF full locked metadata resolve without Git-sourced dependencies;
all nine standalone workspace manifests pass metadata discovery. Compilation,
runtime behavior and capture's separate legacy compatibility graph are not
established by these checks.

```bash
rustup run stable cargo metadata --locked --format-version 1
rustup run stable cargo metadata --locked --format-version 1 --manifest-path sotf/Cargo.toml
```

Sonium is a pinned source dependency, excluded from the aggregate numerical
workspace so its package metadata remains authoritative. The
[`sotf-speaker-workbench`](sotf/crates/sotf-speaker-workbench/README.md) embeds its
passive editor and offers a separate active preview through SOTF's existing
crossover plugin. Numerical models remain in Sonium; no solver code is copied.

See [Adeze capability and contribution specification](ADEZE_WORKBENCH_SPEC.md)
for Designer, receiver/Audyssey, room visualization and cross-language boundaries.

The optional [workbench MCP service](sotf/crates/sotf-dev-driver/WORKBENCH_MCP.md)
lets agents inspect Room EQ state, edit supported limits and run offline studies
through the same commands as the UI. Chat remains disabled.

See [design language](DESIGN.md) and [workflow/storage integration](WORKBENCH_WORKFLOW.md) for current boundaries.

Sync installed Symposium guidance from this umbrella root with
`cargo-agents sync --update none --json`. Generated `.agents/skills/` stay local;
dependency ownership and each repository's instructions remain authoritative.

The optional [Swift–Rust ABI build probe](scripts/swift-rust-bridge/README.md)
checks native macOS linkage in both directions without choosing production DSP
interfaces or rebuilding the player:

```sh
bash scripts/swift-rust-bridge/check.sh
```

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
