---
name: sotf-submodule-release
description: "Use to bring all SOTF Rust submodules to a committed, releasable state: run just lint/test (qa opt-in) per submodule, fix failures, update CHANGELOGs, bump Cargo.toml versions, commit each submodule, then commit the top-level gitlinks. Deterministic core is scripts/release/sync_submodules.py (just sync-*); this skill covers the judgment around it."
---

# SOTF Submodule Release

## When To Use

Use when the user wants every submodule (or one named submodule) checked,
bumped, and committed, with the top-level `all_of_sotf` gitlinks updated
afterward. Do not use for single-crate development work or for publishing:
push stays on GitLab with a merge request (see `sotf-workflow`).

## Tooling Contract

- `just sync-status` — read-only state per submodule. Always run first.
- `just sync-check [--workspace X]` — lint -> ntest/test; add `just sync-check-qa` only for release candidates or shared high-risk changes (ladder in `sotf-release-qa`).
- `just sync-prepare [patch|minor|major]` — gates, then bumps `Cargo.toml` and promotes the `CHANGELOG.md` Unreleased section. Never commits; leaves diffs for review.
- `just sync-commit` — commits prepared submodules, runs top-level preflight, commits gitlinks. Never pushes.
- Foundations are processed first (`math-audio`, `sofa-reader` before `autoeq` before `sotf-daw`/`sotf-capture` before `sotf`/`sotf-systemwide`). `gpui-toolkit` and `sotf-3rdparties` are out of scope.

## Working Sequence

1. Run `just sync-status`. Report dirty/ahead workspaces and blockers before changing anything.
2. Clear blockers with judgment the script must not fake:
   - Missing or empty CHANGELOG: draft entries from `git -C <ws> log <gitlink>..HEAD` and the working diff, in the file's existing voice. `sotf` and `sotf-daw` start from the skeleton `prepare` creates.
   - `sotf-capture` has no Unreleased section: entries land under the heading `prepare` inserts; fill them before `sync-commit`.
   - Lint/test failures: fix the root cause in the owning workspace; never weaken a test or silence a lint to go green.
3. Choose the bump: `patch` by default, `minor` when the diff adds features or public API, `major` only on explicit user instruction. `sotf-systemwide` daemon version bumps only when `crates/daemon` changed — the script decides from the diff; verify the printed mapping.
4. Run `just sync-prepare <bump>`. Show the user each workspace diff (`Cargo.toml`, `CHANGELOG.md`, staged work) and the gate results. Stop here unless the user approves committing.
5. On approval, run `just sync-commit`. If a workspace has unstaged work the user staged only in part, do not pass `--stage-all` without naming what it adds.
6. Hand off with exact next commands: push each submodule branch and the top level to GitLab and open MRs. Report skipped gates (`--qa` not run, `--skip-gates` used) and remaining risk.

## Hard Rules

- Never commit or push without the user's explicit approval in this session.
- Never bump a workspace whose only change is `Cargo.lock` or `.DS_Store` noise; the script already ignores noise — if it asks for a bump, show why.
- Unknown recipe, manifest, or changelog shape is a hard error in the script. Extend its explicit spec map; do not add a fallback.
- Preserve unrelated user changes inside submodules. Stage only named files.
