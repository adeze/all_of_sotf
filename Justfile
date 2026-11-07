# Local aggregate QA records each run outside the source workspaces. Override
# SOTF_QA_EVIDENCE_ROOT to keep evidence on another local volume.
default:
    @just --list

set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

qa_evidence_root := env_var_or_default("SOTF_QA_EVIDENCE_ROOT", "/Volumes/home_tmp/cache/mbx/release-qa")
export CARGO_HOME := `python3 -c 'import os; from pathlib import Path; home=os.environ.get("CARGO_HOME"); home=(str(Path(os.environ.get("HOME") or Path.home()) / ".cargo") if home is None else home); print(str((Path(os.path.expanduser(home)) if Path(os.path.expanduser(home)).is_absolute() else Path.cwd() / os.path.expanduser(home)).resolve()) if home else "")'`
workspaces := `python3 scripts/workspaces.py --local`

dependencies:
	SOTF_QA_EVIDENCE_ROOT="{{qa_evidence_root}}" python3 scripts/release/qa.py --phase metadata

check: dependencies
	SOTF_QA_EVIDENCE_ROOT="{{qa_evidence_root}}" python3 scripts/release/all_features_candidate_check.py --evidence-root "{{qa_evidence_root}}"

test:
	SOTF_QA_EVIDENCE_ROOT="{{qa_evidence_root}}" python3 scripts/release/qa.py --phase tests

qa: check test
	SOTF_QA_EVIDENCE_ROOT="{{qa_evidence_root}}" python3 scripts/release/qa.py --phase qa

qa-release: qa

# Show the recipes exposed by each workspace Justfile.
local-list:
	@for workspace in {{workspaces}}; do \
		echo "=== $workspace ==="; \
		just --justfile "$workspace/Justfile" --working-directory "$workspace" --list; \
	done

# Run the canonical suites in populated submodule workspaces.
# External Git dependencies are qualified by the release source checkout.
local-test: local-test-autoeq local-test-math-audio local-test-sofa-reader local-test-sotf local-test-sotf-capture local-test-sotf-daw local-test-sotf-systemwide
	@echo "All workspace test suites passed."

local-test-autoeq:
	just --justfile autoeq/Justfile --working-directory autoeq test

local-test-math-audio:
	just --justfile math-audio/Justfile --working-directory math-audio test

local-test-sofa-reader:
	just --justfile sofa-reader/Justfile --working-directory sofa-reader test

local-test-sotf:
	just --justfile sotf/Justfile --working-directory sotf test

local-test-sotf-capture:
	cargo test --manifest-path sotf-capture/Cargo.toml --workspace

local-test-sotf-daw:
	just --justfile sotf-daw/Justfile --working-directory sotf-daw ntest

local-test-sotf-systemwide:
	just --justfile sotf-systemwide/Justfile --working-directory sotf-systemwide test

# Preserve the per-workspace developer checks alongside the all-feature release
# check above. math-audio has no separate check recipe.
local-check: local-check-autoeq local-check-math-audio local-check-sofa-reader local-check-sotf local-check-sotf-capture local-check-sotf-daw local-check-sotf-systemwide
	@echo "All workspace checks passed."

local-check-autoeq:
	just --justfile autoeq/Justfile --working-directory autoeq check

local-check-math-audio:
	cargo check --manifest-path math-audio/Cargo.toml --workspace --all-targets

local-check-sofa-reader:
	just --justfile sofa-reader/Justfile --working-directory sofa-reader check

local-check-sotf:
	just --justfile sotf/Justfile --working-directory sotf check

local-check-sotf-capture:
	just --justfile sotf-capture/Justfile --working-directory sotf-capture check

local-check-sotf-daw:
	just --justfile sotf-daw/Justfile --working-directory sotf-daw check

local-check-sotf-systemwide:
	just --justfile sotf-systemwide/Justfile --working-directory sotf-systemwide check

local-fmt:
	just --justfile autoeq/Justfile --working-directory autoeq fmt
	just --justfile math-audio/Justfile --working-directory math-audio fmt
	just --justfile sofa-reader/Justfile --working-directory sofa-reader fmt
	just --justfile sotf/Justfile --working-directory sotf fmt
	cargo fmt --manifest-path sotf-capture/Cargo.toml --all
	just --justfile sotf-daw/Justfile --working-directory sotf-daw fmt
	just --justfile sotf-systemwide/Justfile --working-directory sotf-systemwide fmt

local-fmt-check:
	cargo fmt --manifest-path autoeq/Cargo.toml --all -- --check
	cargo fmt --manifest-path math-audio/Cargo.toml --all -- --check
	cargo fmt --manifest-path sofa-reader/Cargo.toml -- --check
	cargo fmt --manifest-path sotf/Cargo.toml --all -- --check
	cargo fmt --manifest-path sotf-capture/Cargo.toml --all -- --check
	cargo fmt --manifest-path sotf-daw/Cargo.toml --all -- --check
	cargo fmt --manifest-path sotf-systemwide/Cargo.toml --all -- --check

local-lint:
	just --justfile autoeq/Justfile --working-directory autoeq lint
	just --justfile math-audio/Justfile --working-directory math-audio lint
	just --justfile sofa-reader/Justfile --working-directory sofa-reader clippy
	just --justfile sotf/Justfile --working-directory sotf lint
	just --justfile sotf-capture/Justfile --working-directory sotf-capture clippy
	just --justfile sotf-daw/Justfile --working-directory sotf-daw lint
	just --justfile sotf-systemwide/Justfile --working-directory sotf-systemwide lint

local-build:
	cargo build --manifest-path autoeq/Cargo.toml --workspace
	cargo build --manifest-path math-audio/Cargo.toml --workspace
	cargo build --manifest-path sofa-reader/Cargo.toml --release
	cargo build --manifest-path sotf/Cargo.toml --workspace
	cargo build --manifest-path sotf-capture/Cargo.toml --workspace
	cargo build --manifest-path sotf-daw/Cargo.toml --workspace
	cargo build --manifest-path sotf-systemwide/Cargo.toml --workspace

local-clean:
	just --justfile autoeq/Justfile --working-directory autoeq clean
	just --justfile math-audio/Justfile --working-directory math-audio clean
	just --justfile sofa-reader/Justfile --working-directory sofa-reader clean
	just --justfile sotf/Justfile --working-directory sotf clean
	cargo clean --manifest-path sotf-capture/Cargo.toml
	just --justfile sotf-daw/Justfile --working-directory sotf-daw clean
	just --justfile sotf-systemwide/Justfile --working-directory sotf-systemwide clean

# Aggregate mono workspace (root Cargo.toml, generated -- see scripts/mono).
mono-generate:
	python3 scripts/mono/generate.py

mono-check-generate:
	python3 scripts/mono/generate.py --check

mono-check: preflight
	cargo check --workspace --manifest-path Cargo.toml

mono-test-build: preflight
	cargo test --workspace --manifest-path Cargo.toml --no-run

preflight:
	python3 scripts/release/preflight.py

preflight-release:
	python3 scripts/release/preflight.py --release
