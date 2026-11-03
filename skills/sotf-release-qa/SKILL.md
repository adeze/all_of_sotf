---
name: sotf-release-qa
description: "Use for SOTF release and QA workflows: selecting just recipes, nextest, minimal versus full QA, cross-platform builds, DMG/release scripts, signing, entitlements, iOS/tvOS builds, and release verification summaries."
---

# SOTF Release QA

## When To Use

Use this skill when preparing or verifying a release, choosing QA commands, building distribution artifacts, debugging release scripts, or deciding how much validation a SOTF change needs.

## Working Sequence

1. Identify the touched areas and select the smallest meaningful QA slice.
2. Use release-level checks only when the change affects broad behavior, packaging, platform builds, or release assets.
3. Record exact commands and pass/fail status.
4. If a slow or platform-specific check is skipped, say why and name the remaining risk.

## Release Hosting

- GitLab owns development branches, merge requests, and release preparation. GitHub receives completed code changes and public releases.
- Before publishing, read `../sotf-workflow/references/repo-protocol.md` for remote selection, publication scope, and public bug handling.
- Publish only the intended code branch, release tags, and release assets to GitHub. Keep development branches and review history in GitLab.
- For public GitHub bugs fixed by a release, update their issues with public fix/release links and availability as part of the requested bug-handling work.

## Default Ladder

- Local targeted tests for the edited crate.
- `just check` for workspace compile sanity.
- `just ntest` for broad test coverage.
- Minimal QA subset for affected domains.
- Full `just qa` only for release candidates or high-risk shared changes.

## References

- Read `references/release-matrix.md` for command selection and platform build notes.
