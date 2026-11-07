# Software Quality Matrix Methodology

This directory contains a static assessment of the populated Rust repositories in `all_of_sotf`. Scores describe the inspected revision; they are not a release QA pass.

## Files

- `repos.json` — shared repository inventory consumed by quality collection, dependency alignment, Bacon, and Buildbot through `../workspaces.py`.
- `collect.py` — static collector and scoring engine.
- `execute_quality.py` — execution-based checker that runs tests, coverage and benchmarks.
- `scores.json` — machine-readable scores per repository.
- `matrix.md` — human-readable heat-map matrix.
- `README.md` — this methodology document.

## Prerequisites

`execute_quality.py` dispatches the native toolchain for each repository. Make sure the following tools are installed and on `PATH`:

| Tool | Needed for | How to install | Homebrew (macOS) |
|---|---|---|---|
| `cargo` + `rustc` | Rust repositories | [rustup](https://rustup.rs/) | `brew install rustup` then `rustup-init` |
| `cargo-llvm-cov` | Rust coverage reports | `cargo install cargo-llvm-cov` (also needs LLVM tools: `rustup component add llvm-tools-preview`) | `cargo install cargo-llvm-cov` |
| `npm` / `pnpm` | TypeScript repositories | Bundled with [Node.js](https://nodejs.org/) / `npm install -g pnpm` | `brew install node pnpm` |
| `bun` | TypeScript repositories using Bun | `curl -fsSL https://bun.sh/install \| bash` (see [bun.sh](https://bun.sh/docs/installation)) | `brew install bun` |
| `forge` + `cast` + `anvil` | Solidity repositories (Foundry) | `curl -L https://foundry.paradigm.xyz \| bash` then run `foundryup` (see [Foundry docs](https://book.getfoundry.sh/getting-started/installation)) | Not available in Homebrew; use the official installer above |
| `gradle` or `./gradlew` | Android/Kotlin repositories | [Gradle](https://gradle.org/install/) or the wrapper; also requires a working Android SDK for native tests | `brew install gradle` and `brew install --cask android-studio` |
| `xcodebuild` | iOS/Swift repositories | Install Xcode from the Mac App Store or Apple Developer portal | `brew install --cask xcode` |

If a tool is missing, `execute_quality.py` will log the failure and move on; the repository will keep its static score and coverage will be reported as N/A.

## Approach

The chosen approach is **Option A: Lightweight Static + Heuristic**. No full test suites were executed because of heterogeneous toolchains, long build times, and missing runtime dependencies. Instead, the collector inspects:

- file-system structure (`tests/`, `benches/`, `docs/`, `.github/workflows/`);
- source and test-file counts;
- configuration files (`Cargo.toml`, `package.json`, `tsconfig.json`, `deny.toml`, lint configs);
- lightweight regex/AST heuristics for design patterns and doc comments;
- CI workflow categories.

**Coverage values are N/A** unless a badge or coverage artifact is present. To obtain real percentages, run the appropriate toolchain for each repository (see Coverage section below).

## Scoring Dimensions

All dimensions are scored **0–5**, except unit-test coverage which is reported as a percentage when available.

| Dimension | What it measures | How it is computed |
|---|---|---|
| **Unit Tests** | Density of unit-test code relative to source code. | Rust: count of `#[test]` attributes per `.rs` file. TS/JS: count of `describe`/`it`/`test` blocks in `*.test/spec.ts`. Solidity: count of `*.t.sol` files. Kotlin: `test/**/*.kt` files. Swift: `*Tests.swift` files. |
| **Integration / E2E Tests** | Presence and CI enforcement of integration or end-to-end tests. | Detects `integration-tests/`, `integration_tests/`, `e2e-tests/`, `e2e/` directories and corresponding CI workflows. |
| **Documentation** | Completeness of human-readable docs. | Checks README, CONTRIBUTING, CHANGELOG, SECURITY/CODE_OF_CONDUCT, `docs/` directory, API-doc tooling, and doc-comment ratio. |
| **Architecture** | Modularity and separation of concerns. | Workspace/module count, ADRs/architecture docs, average source-file size, monorepo structure. |
| **Design Patterns** | Deliberate use of idiomatic abstractions. | Rust: traits/enums/Result/Option usage. TS: interfaces/abstract classes/service layers. Solidity: interfaces/proxy/access-control. Mobile: MVVM/repository patterns. |
| **Benchmarks** | Performance regression testing. | Presence of `benches/`/`benchmarks/` directories, `criterion`, `frame-benchmarking`, or benchmark CI workflows. |
| **CI Maturity** | Breadth of automated pipelines. | One point each for test, lint, security, release/deploy, and performance/coverage/mutation workflows. |
| **Static Analysis** | Linting and formatting enforcement. | Presence of lint/format configs and matching CI enforcement. |
| **Dependency Security** | Supply-chain risk controls. | `deny.toml`, Dependabot config, security workflows. |
| **Maintainability** | Long-term project health signals. | CODEOWNERS, CHANGELOG, CONTRIBUTING, issue/PR templates, release automation. |
| **Type Safety** | Strength of static type guarantees. | Rust/Kotlin/Swift = 5 by default. TypeScript depends on `strict: true` in `tsconfig.json`. Solidity depends on static-analysis tooling. |
| **Test Pyramid Balance** | Healthy mix of unit vs integration tests. | Ratio of unit test files to integration/e2e directories. |
| **Mutation / Fuzz** | Deeper verification beyond line coverage. | Fuzz targets, mutation workflows, property-based testing (fast-check). |

## Weighting

The overall score is a weighted average of the dimensions above:

| Dimension | Weight |
|---|---|
| Unit Tests | 0.15 |
| Integration / E2E Tests | 0.10 |
| Documentation | 0.10 |
| Architecture | 0.10 |
| Design Patterns | 0.10 |
| Benchmarks | 0.08 |
| CI Maturity | 0.08 |
| Static Analysis | 0.07 |
| Dependency Security | 0.07 |
| Maintainability | 0.05 |
| Type Safety | 0.05 |
| Test Pyramid Balance | 0.03 |
| Mutation / Fuzz | 0.02 |

Weights can be adjusted by editing the `overall_score` function in `collect.py` and re-running.

## Additional Quality Parameters

Beyond the dimensions explicitly requested by the user, the matrix includes seven extra parameters that are easy to compute and add valuable signal:

1. **CI/CD Maturity** — breadth of automated checks (test, lint, security, release, perf).
2. **Static Analysis / Linting** — config presence and CI enforcement.
3. **Dependency Security / Freshness** — `deny.toml`, Dependabot, security audits.
4. **Maintainability / Bus Factor** — CODEOWNERS, changelog, templates, release automation.
5. **Type Safety / Static Guarantees** — strict TypeScript, Rust ownership, static analysis.
6. **Test Pyramid Balance** — distribution of unit vs integration vs e2e tests.
7. **Mutation / Fuzz Testing** — fuzz targets, mutation workflows, property-based tests.

## How to Generate Real Coverage

To replace the N/A coverage values with actual percentages, run the appropriate tool per ecosystem:

- **Rust**: `cargo llvm-cov --workspace --lcov` or `cargo tarpaulin --workspace --out Xml`
- **TypeScript (vitest)**: `vitest run --coverage`
- **TypeScript (jest)**: `jest --coverage`
- **Solidity (Foundry)**: `forge coverage`
- **Kotlin/Android**: `./gradlew jacocoTestReport`
- **Swift/iOS**: `xcodebuild test -enableCodeCoverage YES`

Then update `coverage_value` in `collect.py` to parse the generated artifacts and re-run.

## Re-running the Analysis

Generate static scores only:

```bash
python3 scripts/quality-matrix/collect.py
```

Run tests, coverage and benchmarks and merge the results into the matrix (resumes from `execution_results.json` if interrupted):

```bash
python3 scripts/quality-matrix/execute_quality.py
```

## Limitations

- Scores are based on static signals, not runtime behavior.
- Architecture and design-pattern scores are heuristic; they indicate structural conventions, not true design quality.
- Coverage is N/A for repositories where the test toolchain could not run; see the per-repo coverage notes in `matrix.md`.
- Mobile repositories may be under-scored if test conventions differ from the heuristics used.

## Evidence freshness

Static collection records its timestamp and each inspected source revision. An
absent checkout is listed as unassessed and receives no score. The external GPUI
Toolkit may be absent from a submodule checkout. Complexity from a separately
maintained TokenSave index is excluded by default, and its weight is removed
from the overall score. Use `--with-index-complexity` only after verifying index
freshness. Static collection does not execute tests, coverage, or benchmarks.
