---
name: sotf-workflow
description: "Use for SOTF repository planning and implementation workflow: TokenSave-first code research, issue creation before implementation plans, GitLab branch and merge request hygiene, GitHub publication and public bug tracking, verification summaries, commit drafting, and avoiding user worktree churn."
---

# SOTF Workflow

## Core Flow

Use this skill whenever work is in `gpui-toolkit`, `math-audio`, `autoeq`, `sotf` or derived worktree and the task involves planning, implementing, reviewing, committing, or opening a PR.

1. Start code research with TokenSave tools before reading source files.
2. For each new implementation plan, create or identify the corresponding issue before coding.
3. Keep changes scoped to the requested behavior and existing ownership boundaries.
4. Verify with the narrowest meaningful commands first, then broader checks when blast radius requires them.
5. Summarize changed behavior, verification, and residual risk before commit or merge request.
6. Push the branch to GitLab and open a GitLab merge request when the implementation plan is complete and the user has not asked to stop earlier. Keep development branches and reviews in GitLab; publish completed code changes and releases to GitHub only as part of publication work.
7. Keep customer-reported bugs tracked on their public GitHub issues, with customer-facing progress and published fix links; implement and review the fixes in GitLab. Follow the hosting and publication rules in `references/repo-protocol.md`.

## Research Order

- Use `tokensave_context` first for unfamiliar code areas.
- Use `tokensave_search` for known symbols, then callers/callees/impact tools if available.
- Use `rg` or file reads only after TokenSave gives the likely files or when searching raw text, config, docs, logs, or CLI strings.
- If TokenSave cannot answer a structural question, query `.tokensave/tokensave.db` directly before doing broad source scans.

## Worktree Care

- Assume unrelated uncommitted changes belong to the user.
- Do not revert, reset, or overwrite changes you did not make.
- Before touching files with existing modifications, inspect the diff and work with those edits.
- Prefer non-interactive git commands.
- Do not use `/tmp` or `/private/tmp` for task files, worktrees, downloads, or build artifacts; system temporary storage is space-constrained.
- When `/Volumes/home_worktrees` exists, create worktrees in task-specific subdirectories beneath it. Otherwise, use `<repo>/.worktrees/<task>`.
- When `/Volumes/Downloads` exists, place temporary files and downloads in task-specific subdirectories beneath it. Otherwise, use `<repo>/tmp/<task>` (`./tmp` at the repository root).
- Prefer the `mbx` cache for Cargo build artifacts when available. Otherwise, place task-specific Cargo target directories beneath the selected temporary directory and set `CARGO_TARGET_DIR` for the command.
- For tools that honor `TMPDIR`, set it per command to the selected temporary directory so their intermediate files use the same storage. Do not change the user's global environment.
- Keep local `.worktrees/` and `tmp/` contents out of commits; use existing ignore rules or a local Git exclude when needed. Do not delete unrelated contents of shared directories.

## References

- Read `references/repo-protocol.md` when creating an issue, branch, commit, or merge request, handling a public GitHub bug, or publishing code to GitHub.
