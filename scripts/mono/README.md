# Mono workspace (aggregate Cargo workspace)

One Cargo workspace at the `all_of_sotf` root to compile and check every
crate in every member repo with a single command, a single resolve, and a
single lockfile:

```sh
cargo check --workspace --locked   # from the aggregate root
cargo test --workspace --locked --no-run
```

Each repo keeps building standalone with its own manifest: nested workspace
roots shadow the root manifest, so day-to-day per-repo builds, tests, and QA
are unchanged. Run `just local-test` / `bacon test` for those as before.

## Layout

| Path | Ownership | Purpose |
|---|---|---|
| `Cargo.toml` (root) | generated, committed | aggregate workspace (102 members; 122 when whole) |
| `Cargo.lock` (root) | generated, committed | mono resolve (~1150 packages) |
| `rust-toolchain.toml` (root) | hand | stable + components, same as the repos |
| `.cargo/config.toml` (root) | hand | union of the per-repo cargo configs |
| `scripts/mono/generate.py` | hand | generator (stdlib only) |
| `scripts/mono/generate_test.py` | hand | unit tests (pytest or `python3` directly) |
| `scripts/mono/overlays/mono.toml` | hand | global reconciliations |
| `scripts/mono/overlays/<repo>.toml` | hand | one overlay file per directory |

## Regenerate / check drift

```sh
python3 scripts/mono/generate.py          # rewrite root Cargo.toml
python3 scripts/mono/generate.py --check # CI gate: fail if stale
python3 scripts/mono/generate.py --report # show merge decisions
just mono-generate / just mono-check     # justfile wrappers
bacon mono-check                          # watched mono check
```

Regenerate (and re-resolve the lockfile) whenever a member manifest changes
membership, workspace deps, lints, or patches.

## How the merge works

- **Members**: union of every repo's `[workspace]` members (102 today; 20
  temporarily plus 1 permanently excluded, see below).
- **Root packages cannot join.** Cargo forbids a package with its own
  `[workspace]` table in an outer workspace ("multiple workspace roots",
  probed listed and auto-joined). The generator rejects such members with
  a pointer to issue #4.
- **Internal path deps** win over git/registry entries for the same package
  (mono checks working-copy edits, e.g. symphonia crates), and their
  `version`/`git` companions are dropped: members share one mono version.
- **External deps**: sources must agree; most-specific version req wins
  (`4.0.1` over `3`/`4` for serial_test); features union; `default-features`
  disagreements fail loudly and need a `[deps.*]` overlay decision.
- **Excluded targets keep their own context.** A path dep under an
  `[mono] exclude` path resolves with its own workspace tables and version
  (probed) instead of joining mono. Used for the nested gpui-toolkit.
- **Lints**: loosest level wins; `deny`/`forbid` is kept only when universal
  (autoeq's `unsafe_code = forbid` is dropped: sotf-media-controls has
  legitimate unsafe). Per-repo clippy stays the authoritative lint gate.
- **Patches**: unioned with rewritten paths (none today; the old scheduler
  bridge patch is fixed upstream).
- **Profiles**: canonical dev/release/dist (check builds ignore them).
- **Exclude**: `sotf-3rdparties` (git-only consumption; excluding preserves
  standalone fork-development builds inside it) and `gpui-toolkit`
  (dangling-safe: cargo ignores missing excluded paths, so the entry
  survives the extraction).

The generator validates the result before writing: no duplicate package
names, mono version satisfies every direct internal pin, every
`workspace = true` use resolves from the root, and external path targets
inherit only names mono defines.

## Scope and limits (read before relying on mono)

- **Never publish from mono.** Members share `[workspace.package]`
  (version `0.5.77`, aggregate repository/authors). Per-repo manifests stay
  the source of truth for versions, licenses (math-audio is ISC, mono says
  GPL), and package metadata.
- **Host check/test only.** Mobile QA, packaging, and signing stay per-repo.
- **Lint parity is best-effort.** Mono warns where the union is stricter
  than a repo's own table (e.g. pedantic lints newly visible on autoeq
  code); standalone clippy is authoritative.
- **sotf-capture / sofa-reader** resolve under resolver v3 in mono (v2
  standalone). No fallout observed; full test execution will confirm.
- **`serial_test` is v4 in mono** while sotf stands on v3 (dev-only).
  Revisit if sotf tests misbehave under mono.

## Excluded members

- **20 temporary (issue #4).** The `autoeq` and `sotf-capture` root packages
  are workspace roots, which cargo forbids in an outer workspace, so they
  plus their reverse-dependency closure stay out until those repo roots are
  virtualized (facade moves into `crates/`). Removing the overlay entries
  then rejoins the crates untouched; no other mono change is needed.
- **1 permanent.** `sofa-reader` is its own workspace root and every
  consumer takes it via git, so mono resolves the git rev and never lists
  the working copy. Covered standalone instead.

Mono is decoupled from the gpui-toolkit extraction: while nested, the
toolkit is excluded (its path dependents keep its workspace context); after
extraction the exclude entry dangles harmlessly and the migrated git dep
takes over. Either way, regenerate the lockfile when the switch lands
(`cargo generate-lockfile`, committed).

## Why not one `Cargo.mono.toml` per directory

Probed and rejected: cargo requires `--manifest-path` to name `Cargo.toml`,
workspace members ignore alternate filenames, and a subdir workspace cannot
claim `../` members. A mode script swapping `Cargo.mono.toml` over
`Cargo.toml` would dirty all 8 submodules per switch and solve nothing the
generator does not. The per-directory overlays are generator inputs owned by
the aggregate repo instead: same one-file-per-directory maintenance surface,
no submodule churn.
