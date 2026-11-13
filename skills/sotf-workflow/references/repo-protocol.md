# SOTF Repo Protocol

## Hosting and Publication

- GitLab is the primary development host. Keep development issues, implementation plans, feature/fix branches, merge requests (PRs), and code review in GitLab.
- GitHub is the public destination for completed code changes, releases, and customer bug reports. Do not use it as a fallback development host when GitLab is unavailable; report the blocker and continue local work where possible.
- Inspect remote URLs before pushing; do not assume `origin` identifies either host. Use explicit remotes and refspecs. Discover the actual target branch instead of assuming `main` or `master`.
- Publish completed, merged code to the intended GitHub publication branch when the task includes code publication, and publish selected release tags/assets when releasing. Routine development pushes go only to GitLab. Do not mirror all branches or use `git push --mirror` or `--all` to GitHub.
- Keep published history consistent with GitLab. If the public branch has diverged, inspect and reconcile the difference before publishing; do not force-push it as a routine synchronization step.

## Issue, Branch, Merge Request

- New implementation plan: create or identify a GitLab issue first unless the user explicitly asks for a local-only experiment. For a public bug, retain the original GitHub issue and link it from the GitLab development issue.
- Use short branch names that include issue context when available, such as `fix/issue-177-roomeq-linux-export`.
- When done, push the development branch to GitLab and open a GitLab merge request.
- Merge request descriptions should include:
  - `Closes #N` for the corresponding GitLab issue when applicable.
  - The full URL of any related public GitHub issue; issue numbers are host-specific.
  - Summary bullets of behavior changes.
  - Verification commands and whether they passed.
  - Any known residual risk.

## Public GitHub Bugs

- Keep the original GitHub issue as the customer-facing record. Implement and review the fix through a GitLab branch and merge request, including for bugs reported on GitHub.
- When the user asks to handle a public bug, provide relevant progress, reproduction findings, and verification results on that GitHub issue. Keep private diagnostics and internal planning in GitLab; public updates must be understandable without access to private links.
- After publishing the fix to GitHub, link the public commit or release and explain availability. Do not close the public issue merely because a private GitLab merge request merged; close it when the published fix is available, distinguishing a source fix from a released binary.
- These instructions define the workflow for requested bug handling or publication; they do not authorize unrelated public messages or releases.

## Useful Tools

- Use connected GitLab tools or `glab` for development issues and merge requests, explicitly targeting the intended GitLab repository.
- Use connected GitHub tools or `gh` for public bug issues and release publication, explicitly targeting the intended GitHub repository.
- Use `git push <gitlab-remote> <branch>` for development; use explicit publication branch/tag refspecs for GitHub.
- `tokensave_commit_context` for commit messages.
- `tokensave_pr_context` for merge request descriptions.

## Verification Ladder

Pick the smallest useful check for the edited area, then widen if the change affects shared contracts.

- General Rust formatting: `cargo fmt --all`.
- Workspace sanity: `just check` or targeted `cargo check -p <crate>`.
- Targeted tests: `cargo test -p <crate> <filter>`.
- Broad tests: `just ntest`.
- QA matrix: see `$sotf-release-qa` for release and slow QA selection.

## Commit Style

- Use imperative, specific commit subjects.
- Mention the user-visible or correctness effect, not only the edited file.
- Keep unrelated changes out of the commit.
