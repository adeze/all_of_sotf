#!/usr/bin/env python3
"""
Static software-quality collector for the SOTF repository family.
Produces quality-matrix/scores.json and quality-matrix/matrix.md.
"""

import argparse
import fnmatch
import json
import os
import re
import glob
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

_SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = _SCRIPT_DIR.parent.parent
MATRIX_DIR = _SCRIPT_DIR
REPOS_JSON = MATRIX_DIR / "repos.json"
SCORES_JSON = MATRIX_DIR / "scores.json"
MATRIX_MD = MATRIX_DIR / "matrix.md"
INDEX_COMPLEXITY = False


def load_repos():
    with open(REPOS_JSON) as f:
        return json.load(f)["repositories"]


# Directories to skip when scanning repository contents.
SKIP_DIRS = {
    ".git", "target", ".tokensave", "node_modules", ".venv", "venv",
    "__pycache__", ".pytest_cache", "data_generated", "fuzzer_output",
    "build", "dist", ".idea", ".vscode", ".ruff_cache", ".docker-target",
    ".worktrees", "worktrees", ".muse", ".evo", "audit",
}


def _matches_path(parts, pattern):
    if not pattern:
        return not parts
    if pattern[0] == "**":
        return _matches_path(parts, pattern[1:]) or bool(parts) and _matches_path(parts[1:], pattern)
    return bool(parts) and fnmatch.fnmatchcase(parts[0], pattern[0]) and _matches_path(parts[1:], pattern[1:])


def list_files(repo_path, patterns):
    """Return matched files without descending into build and cache directories."""
    base = ROOT / repo_path
    seen = set()
    files = []
    patterns = [tuple(pattern.split("/")) if pattern.startswith("**/")
                else ("**", *pattern.split("/")) for pattern in patterns]
    for directory, children, names in os.walk(base):
        children[:] = [name for name in children if name not in SKIP_DIRS]
        for name in names:
            path = Path(directory) / name
            relative = path.relative_to(base)
            if not any(_matches_path(relative.parts, pattern) for pattern in patterns):
                continue
            key = path.resolve()
            if key not in seen:
                seen.add(key)
                files.append(path)
    return files


def count_lines(path):
    """Count non-blank lines in a file."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def count_source_lines(repo_path, exts):
    files = list_files(repo_path, [f"**/*.{e}" for e in exts])
    return sum(count_lines(f) for f in files), len(files)


def count_doc_comments(repo_path, lang):
    total = 0
    if lang == "Rust":
        files = list_files(repo_path, ["**/*.rs"])
        for f in files:
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                    total += sum(1 for line in fh if re.match(r"^\s*///", line) or re.match(r"^\s*/\*\*", line))
            except Exception:
                pass
    elif lang == "TypeScript":
        files = list_files(repo_path, ["**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx"])
        for f in files:
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                    total += sum(1 for line in fh if re.search(r"(^\s*///)|(^\s*/\*\*)|(^\s*\* )", line))
            except Exception:
                pass
    elif lang == "Kotlin":
        files = list_files(repo_path, ["**/*.kt"])
        for f in files:
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                    total += sum(1 for line in fh if re.match(r"^\s*\*", line) or "@" in line)
            except Exception:
                pass
    elif lang == "Swift":
        files = list_files(repo_path, ["**/*.swift"])
        for f in files:
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                    total += sum(1 for line in fh if re.match(r"^\s*///", line) or re.match(r"^\s*/\*\*", line))
            except Exception:
                pass
    return total


def has_file(repo_path, patterns):
    return any(list_files(repo_path, patterns))


def count_files(repo_path, patterns):
    return len(list_files(repo_path, patterns))


def read_text(repo_path, rel_path):
    p = ROOT / repo_path / rel_path
    if p.exists():
        try:
            return p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
    return ""


def _safe_json_loads(text):
    """Load JSON while tolerating ANSI escapes and stray control chars."""
    if not text:
        return None
    # Strip ANSI escape sequences
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
    # Remove control characters except tab/newline/carriage-return
    text = "".join(
        ch for ch in text
        if ch in ("\t", "\n", "\r") or ord(ch) >= 32
    )
    try:
        return json.loads(text)
    except Exception:
        return None


def _tokensave_complexity_ranking_once(repo_path, source_dir, limit):
    """Single tokensave complexity query; return None if output was truncated."""
    base = ROOT / repo_path
    cmd = ["tokensave", "tool", "complexity", "--limit", str(limit)]
    if source_dir:
        cmd.extend(["--path", source_dir])
    try:
        result = subprocess.run(
            cmd,
            cwd=base,
            capture_output=True,
            text=True,
            timeout=180,
        )
        data = _safe_json_loads(result.stdout)
        if not isinstance(data, dict) or not isinstance(data.get("ranking"), list):
            return None
        return data["ranking"]
    except Exception:
        return None


def _tokensave_complexity_ranking(repo_path, source_dir, max_limit=100):
    """
    Query TokenSave complexity with an adaptive limit.
    TokenSave truncates large outputs, so we bisect down to a limit that parses.
    """
    for limit in [max_limit, max(10, max_limit // 2), max(10, max_limit // 4), 10]:
        ranking = _tokensave_complexity_ranking_once(repo_path, source_dir, limit)
        if ranking is not None:
            return ranking
    return None


def _source_dirs(repo_path):
    """Return source directories to scan for complexity."""
    base = ROOT / repo_path
    dirs = []
    for d in ["crates", "src", "tests", "benches", "examples"]:
        if (base / d).is_dir():
            dirs.append(d)

    # Workspaces whose members live at the repo root
    cargo_toml = base / "Cargo.toml"
    if cargo_toml.exists() and not dirs:
        text = cargo_toml.read_text(encoding="utf-8", errors="ignore")
        if "[workspace]" in text:
            members_match = re.search(r"members\s*=\s*\[(.*?)\]", text, re.DOTALL)
            if members_match:
                for member in re.findall(r'"([^"]+)"', members_match.group(1)):
                    top = member.split("/")[0]
                    if top and (base / top).is_dir() and top not in dirs:
                        dirs.append(top)

    # Fallback: scan the whole repo (may include vendored/venv noise)
    if not dirs:
        dirs.append("")
    return dirs


def analyze_repo_complexity(repo_path, threshold=25):
    """Return cyclomatic-complexity stats using the existing TokenSave index."""
    high_funcs = []
    seen_ids = set()
    complete = True
    for source_dir in _source_dirs(repo_path):
        ranking = _tokensave_complexity_ranking(repo_path, source_dir)
        if ranking is None:
            complete = False
            continue
        for item in ranking:
            fid = item.get("id")
            if fid in seen_ids:
                continue
            seen_ids.add(fid)
            file_path = item.get("file", "")
            if not file_path.endswith(".rs"):
                continue
            if any(part in SKIP_DIRS for part in Path(file_path).parts[:-1]):
                continue
            cc = item.get("cyclomatic_complexity", 0)
            if isinstance(cc, (int, float)) and cc > threshold:
                high_funcs.append({
                    "name": item.get("name", "<unknown>"),
                    "file": file_path,
                    "complexity": cc,
                })
    return {
        "high_function_count": len(high_funcs) if complete else None,
        "threshold": threshold,
        "functions": high_funcs,
    }


def detect_workflow_categories(repo_path):
    base = ROOT / repo_path / ".github" / "workflows"
    if not base.exists():
        return {}
    cats = defaultdict(int)
    for wf in base.glob("*.yml"):
        text = wf.read_text(encoding="utf-8", errors="ignore").lower()
        if any(k in text for k in ("test", "cargo test", "vitest", "jest", "playwright", "e2e")):
            cats["test"] += 1
        if any(k in text for k in ("lint", "clippy", "eslint", "format", "rustfmt", "prettier")):
            cats["lint"] += 1
        if any(k in text for k in ("audit", "security", "snyk", "dependabot", "cargo audit", "npm audit")):
            cats["security"] += 1
        if any(k in text for k in ("release", "publish", "deploy")):
            cats["release_deploy"] += 1
        if any(k in text for k in ("benchmark", "perf", "coverage")):
            cats["perf_coverage"] += 1
        if any(k in text for k in ("mutation", "fuzz")):
            cats["mutation_fuzz"] += 1
    return dict(cats)


def score_ci_maturity(cats):
    score = 0
    if cats.get("test", 0) > 0:
        score += 1
    if cats.get("lint", 0) > 0:
        score += 1
    if cats.get("security", 0) > 0:
        score += 1
    if cats.get("release_deploy", 0) > 0:
        score += 1
    if cats.get("perf_coverage", 0) > 0 or cats.get("mutation_fuzz", 0) > 0:
        score += 1
    return score


def score_documentation(repo):
    rp = repo["path"]
    score = 0
    # README present
    if has_file(rp, ["README*"]):
        score += 1
    # CONTRIBUTING
    if has_file(rp, ["CONTRIBUTING*"]):
        score += 1
    # CHANGELOG or RELEASING
    if has_file(rp, ["CHANGELOG*", "RELEASING*", "RELEASE*"]):
        score += 1
    # Security / conduct
    if has_file(rp, ["SECURITY*", "CODE_OF_CONDUCT*"]):
        score += 0.5
    # docs/ directory
    docs_dir = ROOT / rp / "docs"
    if docs_dir.exists() and any(docs_dir.iterdir()):
        score += 1
    # API doc generation config
    if has_file(rp, ["typedoc*", "jsdoc*", "*.swiftpm", "Package.swift"]):
        score += 0.5
    # doc-comment ratio
    src_lines, _ = count_source_lines(rp, source_extensions(repo))
    doc_lines = count_doc_comments(rp, repo["primary_language"])
    if src_lines > 0 and doc_lines / src_lines > 0.05:
        score += 0.5
    if src_lines > 0 and doc_lines / src_lines > 0.10:
        score += 0.5
    return min(5, round(score))


def source_extensions(repo):
    lang = repo["primary_language"]
    if lang == "Rust":
        return ["rs"]
    if lang == "TypeScript":
        return ["ts", "tsx"]
    if lang == "Kotlin":
        return ["kt"]
    if lang == "Swift":
        return ["swift"]
    if lang == "Solidity":
        return ["sol"]
    return ["rs", "ts", "tsx", "kt", "swift", "sol", "js", "jsx"]


def score_architecture(repo):
    rp = repo["path"]
    score = 0
    # workspace / monorepo structure
    if repo.get("is_monorepo"):
        score += 1.5
    elif has_file(rp, ["Cargo.toml", "package.json"]):
        score += 0.5
    # crates or packages count
    crates = count_files(rp, ["*/Cargo.toml"])
    packages = count_files(rp, ["*/package.json"])
    modules = max(crates, packages)
    if modules >= 5:
        score += 1.5
    elif modules >= 2:
        score += 1
    # ADRs / architecture docs
    if has_file(rp, ["adrs/**/*.md", "**/adr*.md", "ARCHITECTURE*", "design/**/*.md"]):
        score += 1
    # average source file size sanity
    src_lines, src_count = count_source_lines(rp, source_extensions(repo))
    if src_count > 0:
        avg = src_lines / src_count
        if 50 <= avg <= 400:
            score += 0.5
    return min(5, round(score))


def score_design_patterns(repo):
    rp = repo["path"]
    lang = repo["primary_language"]
    score = 0
    if lang == "Rust":
        traits = count_pattern(rp, "*.rs", r"\btrait\s+\w+")
        enums = count_pattern(rp, "*.rs", r"\benum\s+\w+")
        impls = count_pattern(rp, "*.rs", r"\bimpl\s+")
        if traits > 10:
            score += 2
        elif traits > 0:
            score += 1
        if enums > 10:
            score += 1
        if impls > traits * 2:
            score += 1
        # builder / result patterns
        if count_pattern(rp, "*.rs", r"Result<|Option<") > 50:
            score += 1
    elif lang == "TypeScript":
        interfaces = count_pattern(rp, "*.ts", r"\binterface\s+\w+")
        abstract = count_pattern(rp, "*.ts", r"\babstract\s+class")
        if interfaces > 10:
            score += 2
        elif interfaces > 0:
            score += 1
        if abstract > 0:
            score += 1
        if count_pattern(rp, "*.ts", r"export\s+class|export\s+interface") > 20:
            score += 1
        if has_file(rp, ["**/di/*.ts", "**/services/*.ts", "**/repositories/*.ts"]):
            score += 1
    elif lang == "Solidity":
        if count_pattern(rp, "*.sol", r"\binterface\s+\w+") > 0:
            score += 1
        if count_pattern(rp, "*.sol", r"\bcontract\s+\w+\s+is\s+") > 0:
            score += 1
        if has_file(rp, ["**/proxy*.sol", "**/access*.sol"]):
            score += 1
    elif lang == "Kotlin":
        if count_pattern(rp, "*.kt", r"\binterface\s+\w+") > 5:
            score += 2
        if has_file(rp, ["**/ViewModel*", "**/Repository*", "**/Store*"]):
            score += 2
    elif lang == "Swift":
        if count_pattern(rp, "*.swift", r"\bprotocol\s+\w+|\binterface\s+\w+") > 5:
            score += 2
        if has_file(rp, ["**/ViewModel*", "**/Repository*", "**/Store*", "**/View*", "**/Interactor*"]):
            score += 2
    return min(5, round(score))


def count_pattern(repo_path, glob_pat, regex):
    total = 0
    for f in list_files(repo_path, [f"**/{glob_pat}"]):
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                total += len(re.findall(regex, fh.read()))
        except Exception:
            pass
    return total


def score_benchmarks(repo):
    rp = repo["path"]
    score = 0
    has_benches = has_file(rp, ["**/benches/**/*.rs", "**/benchmarks/**/*.ts", "**/benchmarks/**/*.js", "benchmark_config.json"])
    has_criterion = count_pattern(rp, "Cargo.toml", r"criterion") > 0
    has_frame_bench = count_pattern(rp, "Cargo.toml", r"frame-benchmarking") > 0
    if has_benches or has_criterion or has_frame_bench:
        score += 2
    cats = detect_workflow_categories(rp)
    if cats.get("perf_coverage", 0) > 0:
        score += 2
    elif has_benches:
        score += 1
    return min(5, score)


def count_ts_test_blocks(repo_path):
    """Count describe/it/test blocks across TS/JS test files (mocha/chai/jest/vitest)."""
    blocks = 0
    for pat in ["**/*.test.ts", "**/*.spec.ts", "**/*.test.tsx", "**/*.spec.tsx", "**/*.test.js", "**/*.spec.js"]:
        for f in list_files(repo_path, [pat]):
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
                    blocks += len(re.findall(r"\bdescribe\s*\(", text))
                    blocks += len(re.findall(r"\bit\s*\(", text))
                    blocks += len(re.findall(r"\btest\s*\(", text))
            except Exception:
                pass
    return blocks


def score_tests(repo):
    rp = repo["path"]
    lang = repo["primary_language"]
    unit_score = 0
    integration_score = 0
    test_counts = {"unit_files": 0, "unit_blocks": 0}

    # unit test signals
    if lang == "Rust":
        unit_tests = count_pattern(rp, "*.rs", r"#\[test\]")
        test_counts["unit_blocks"] = unit_tests
    elif lang == "TypeScript":
        unit_patterns = ["**/*.test.ts", "**/*.spec.ts", "**/*.test.tsx", "**/*.spec.tsx", "**/*.test.js", "**/*.spec.js"]
        unit_files = count_files(rp, unit_patterns)
        unit_blocks = count_ts_test_blocks(rp)
        unit_tests = unit_blocks if unit_blocks > 0 else unit_files
        test_counts = {"unit_files": unit_files, "unit_blocks": unit_blocks}
    elif lang == "Solidity":
        unit_patterns = ["**/*.t.sol"]
        unit_tests = count_files(rp, unit_patterns)
        test_counts["unit_files"] = unit_tests
    elif lang == "Kotlin":
        unit_patterns = ["**/test/**/*.kt"]
        unit_tests = count_files(rp, unit_patterns)
        test_counts["unit_files"] = unit_tests
    elif lang == "Swift":
        unit_patterns = ["**/*Tests.swift"]
        unit_tests = count_files(rp, unit_patterns)
        test_counts["unit_files"] = unit_tests
    else:
        unit_tests = 0

    src_lines, src_count = count_source_lines(rp, source_extensions(repo))
    if src_count > 0:
        ratio = unit_tests / src_count
        if ratio >= 0.3:
            unit_score = 5
        elif ratio >= 0.2:
            unit_score = 4
        elif ratio >= 0.1:
            unit_score = 3
        elif ratio >= 0.05:
            unit_score = 2
        elif unit_tests > 0:
            unit_score = 1
    else:
        unit_score = 1 if unit_tests > 0 else 0

    # integration/e2e signals
    integ_patterns = [
        "**/integration-tests/**",
        "**/integration_tests/**",
        "**/e2e-tests/**",
        "**/e2e/**",
        "**/tests/e2e/**",
        "**/*.integration.test.ts",
        "**/*.integration.spec.ts",
        "**/*.e2e.test.ts",
        "**/*.e2e.spec.ts",
        "**/*.e2e-spec.ts",
    ]
    integ_count = count_files(rp, integ_patterns)
    cats = detect_workflow_categories(rp)
    if integ_count >= 5 and cats.get("test", 0) > 0:
        integration_score = 5
    elif integ_count >= 2 and cats.get("test", 0) > 0:
        integration_score = 4
    elif integ_count > 0 and cats.get("test", 0) > 0:
        integration_score = 3
    elif integ_count > 0:
        integration_score = 1
    else:
        integration_score = 0

    return unit_score, integration_score, test_counts


def score_static_analysis(repo):
    rp = repo["path"]
    score = 0
    has_lint_config = has_file(rp, [
        "clippy.toml", ".clippy.toml", "eslint.config.*", ".eslintrc*",
        "oxlint.config.*", "biome.json", "dprint.json", "rustfmt.toml",
        ".swiftlint*", "detekt*"
    ])
    if has_lint_config:
        score += 2
    cats = detect_workflow_categories(rp)
    if cats.get("lint", 0) > 0:
        score += 3
    return min(5, score)


def score_dependency_security(repo):
    rp = repo["path"]
    score = 0
    if has_file(rp, ["deny.toml"]):
        score += 2
    if has_file(rp, [".github/dependabot.yml", ".github/dependabot.yaml"]):
        score += 1
    cats = detect_workflow_categories(rp)
    if cats.get("security", 0) > 0:
        score += 2
    return min(5, score)


def score_maintainability(repo):
    rp = repo["path"]
    score = 0
    if has_file(rp, ["CODEOWNERS", ".github/CODEOWNERS"]):
        score += 1
    if has_file(rp, ["CHANGELOG*", "RELEASING*", "RELEASE*"]):
        score += 1
    if has_file(rp, ["CONTRIBUTING*"]):
        score += 1
    if has_file(rp, [".github/ISSUE_TEMPLATE*", ".github/PULL_REQUEST_TEMPLATE*"]):
        score += 1
    cats = detect_workflow_categories(rp)
    if cats.get("release_deploy", 0) > 0:
        score += 1
    return min(5, score)


def score_type_safety(repo):
    rp = repo["path"]
    lang = repo["primary_language"]
    if lang == "Rust":
        return 5  # ownership + type system
    if lang == "TypeScript":
        configs = list_files(rp, ["**/tsconfig*.json"])
        if not configs:
            return 3
        strict_count = 0
        any_strict = False
        for cfg in configs:
            text = cfg.read_text(encoding="utf-8", errors="ignore")
            if '"strict": true' in text:
                strict_count += 1
                any_strict = True
            elif "strict" in text:
                any_strict = True
        if strict_count == len(configs) and configs:
            return 5
        if any_strict:
            return 4
        return 3
    if lang == "Kotlin":
        return 5
    if lang == "Swift":
        return 5
    if lang == "Solidity":
        if has_file(rp, [".github/workflows/4naly3er.yml", "slither.config.json"]):
            return 4
        return 3
    return 3


def score_test_pyramid(repo):
    rp = repo["path"]
    unit = count_files(rp, ["**/*.test.ts", "**/*.spec.ts", "**/*.test.tsx", "**/src/**/*.rs"])
    integ = count_files(rp, ["**/integration-tests/**", "**/e2e-tests/**", "**/e2e/**"])
    if unit > 0 and integ > 0:
        ratio = unit / (unit + integ)
        if 0.5 <= ratio <= 0.85:
            return 5
        if 0.3 <= ratio < 0.5 or 0.85 < ratio <= 0.95:
            return 4
        return 3
    if unit > 0:
        return 2
    if integ > 0:
        return 2
    return 0


def score_mutation_fuzz(repo):
    rp = repo["path"]
    score = 0
    fuzz_qa_files = count_files(rp, [
        "**/fuzz/**/*.rs",
        "**/fuzz_targets/**/*.rs",
        "**/bin/*fuzzer*.rs",
        "**/bin/**/*fuzzer*.rs",
        "**/src/bin/*fuzzer*.rs",
        "**/src/bin/**/*fuzzer*.rs",
        "**/bin/qa.rs",
        "**/bin/**/qa.rs",
        "**/bin/*qa_*.rs",
        "**/bin/**/*qa_*.rs",
        "**/src/bin/qa.rs",
        "**/src/bin/**/qa.rs",
        "**/src/bin/*qa_*.rs",
        "**/src/bin/**/*qa_*.rs",
    ])
    if fuzz_qa_files > 0:
        score += 3
    if fuzz_qa_files > 10:
        score += 1
    if fuzz_qa_files > 30:
        score += 1
    cats = detect_workflow_categories(rp)
    if cats.get("mutation_fuzz", 0) > 0:
        score += 2
    if count_pattern(rp, "*.ts", r"fast-check|fc\.") > 5:
        score += 2
    return min(5, score)


def coverage_value(repo):
    """Try to find an existing coverage report; otherwise return N/A and a note."""
    rp = repo["path"]
    # Look for coverage badges or reports
    readme = ""
    for p in (ROOT / rp).glob("README*"):
        readme = p.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"coverage[\s\w]*[:\s]+(\d{1,3})%", readme, re.IGNORECASE)
    if m:
        return int(m.group(1)), "from README badge"
    # Look for lcov/cobertura files
    for pat in ["**/coverage/lcov.info", "**/lcov.info", "**/cobertura.xml", "**/tarpaulin-report.xml"]:
        files = list_files(rp, [pat])
        if files:
            return None, f"coverage artifact exists: {files[0].relative_to(ROOT / rp)}"
    return None, "no coverage report found; run toolchains to generate"


def score_complexity(repo, threshold=25):
    """
    Compute cyclomatic-complexity stats and return a 0-5 score.
    Higher score means fewer functions above the complexity threshold.
    """
    if not INDEX_COMPLEXITY:
        return None, {"high_function_count": None, "threshold": threshold, "functions": []}
    stats = analyze_repo_complexity(repo["path"], threshold=threshold)
    count = stats["high_function_count"]
    if count is None:
        return None, stats
    if count == 0:
        score = 5
    elif count <= 5:
        score = 4
    elif count <= 15:
        score = 3
    elif count <= 30:
        score = 2
    elif count <= 50:
        score = 1
    else:
        score = 0
    return score, stats


def analyze_repo(repo):
    rp = repo["path"]
    cats = detect_workflow_categories(rp)
    unit_score, integ_score, test_counts = score_tests(repo)
    cov_value, cov_note = coverage_value(repo)
    complexity_score, complexity_stats = score_complexity(repo)

    return {
        "name": repo["name"],
        "path": rp,
        "language": repo["primary_language"],
        "unit_test_score": unit_score,
        "integration_test_score": integ_score,
        "unit_coverage_percent": cov_value,
        "unit_coverage_note": cov_note,
        "test_counts": test_counts,
        "documentation": score_documentation(repo),
        "architecture": score_architecture(repo),
        "design_patterns": score_design_patterns(repo),
        "benchmarks": score_benchmarks(repo),
        "ci_maturity": score_ci_maturity(cats),
        "static_analysis": score_static_analysis(repo),
        "dependency_security": score_dependency_security(repo),
        "maintainability": score_maintainability(repo),
        "type_safety": score_type_safety(repo),
        "test_pyramid_balance": score_test_pyramid(repo),
        "mutation_fuzz": score_mutation_fuzz(repo),
        "complexity_score": complexity_score,
        "complexity_stats": complexity_stats,
        "workflow_categories": cats,
    }


def overall_score(scores):
    # weights add to 1.0
    weights = {
        "unit_test_score": 0.10,
        "integration_test_score": 0.10,
        "documentation": 0.10,
        "architecture": 0.10,
        "design_patterns": 0.10,
        "benchmarks": 0.08,
        "ci_maturity": 0.08,
        "static_analysis": 0.07,
        "dependency_security": 0.07,
        "maintainability": 0.05,
        "type_safety": 0.05,
        "test_pyramid_balance": 0.03,
        "mutation_fuzz": 0.02,
        "complexity_score": 0.05,
    }
    total = 0
    available_weight = 0
    for k, w in weights.items():
        value = scores.get(k, 0)
        if value is not None:
            total += value * w
            available_weight += w
    return round(total / available_weight, 2) if available_weight else None


def source_revision(repo_path):
    result = subprocess.run(["git", "-C", str(ROOT / repo_path), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def source_dirty(repo_path):
    status = subprocess.run(["git", "-C", str(ROOT / repo_path), "status", "--porcelain", "--untracked-files=normal"],
                            capture_output=True, text=True, check=False)
    return bool(status.returncode or status.stdout)


def build_matrix():
    repos = load_repos()
    results = []
    unassessed = []
    for repo in repos:
        if not (ROOT / repo["path"] / "Cargo.toml").is_file():
            unassessed.append({"name": repo["name"], "reason": "source checkout is absent"})
            continue
        print(f"Analyzing {repo['name']} ...")
        scores = analyze_repo(repo)
        scores["overall"] = overall_score(scores)
        scores["source_revision"] = source_revision(repo["path"])
        scores["source_dirty"] = source_dirty(repo["path"])
        results.append(scores)

    # sort by overall score descending
    results.sort(key=lambda x: x["overall"], reverse=True)

    with open(SCORES_JSON, "w") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(),
                   "assessment": "static inspection; no tests or coverage executed",
                   "unassessed": unassessed, "repositories": results}, f, indent=2)
    print(f"Wrote {SCORES_JSON}")

    write_markdown(results, unassessed)
    print(f"Wrote {MATRIX_MD}")


def write_markdown(results, unassessed=None):
    lines = []
    lines.append("# Software Quality Matrix\n")
    lines.append(f"Generated {datetime.now(timezone.utc).isoformat()}. "
                 "Static inspection only; this run executes no tests, coverage, or benchmarks.\n")
    lines.append("Scores are 0-5 unless otherwise noted. Higher is better.\n")
    lines.append("| Repo | Lang | Unit | Integ | Docs | Arch | Patterns | Bench | CI | Lint | Sec | Maint | Type | Pyramid | Fuzz | Cx>25 | **Overall** |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        cov = f"{r['unit_coverage_percent']}%" if r['unit_coverage_percent'] is not None else "N/A"
        cx_count = r['complexity_stats']['high_function_count']
        cx_display = f"{cx_count} ({r['complexity_score']})" if cx_count is not None else "N/A"
        lines.append(
            f"| {r['name']} | {r['language']} | {r['unit_test_score']} ({cov}) | "
            f"{r['integration_test_score']} | {r['documentation']} | {r['architecture']} | "
            f"{r['design_patterns']} | {r['benchmarks']} | {r['ci_maturity']} | "
            f"{r['static_analysis']} | {r['dependency_security']} | {r['maintainability']} | "
            f"{r['type_safety']} | {r['test_pyramid_balance']} | {r['mutation_fuzz']} | "
            f"{cx_display} | **{r['overall']}** |"
        )
    lines.append("\n## Coverage Notes\n")
    for r in results:
        lines.append(f"- **{r['name']}**: {r['unit_coverage_note']}")
    lines.append("\n## Source revisions\n")
    for r in results:
        lines.append(f"- **{r['name']}**: `{r.get('source_revision') or 'unknown'}`"
                     + (" with working-tree changes" if r.get("source_dirty") else ""))
    if unassessed:
        lines.append("\n## Not assessed\n")
        for item in unassessed:
            lines.append(f"- **{item['name']}**: {item['reason']}; no score assigned.")
    lines.append("\n## Complexity Notes\n")
    if not INDEX_COMPLEXITY:
        lines.append("Complexity is not assessed by default. Overall scores exclude its weight. "
                     "Use `--with-index-complexity` only after checking index freshness.\n")
    lines.append("Cx>25 counts functions/methods with cyclomatic complexity greater than 25, "
                 "computed from the existing TokenSave index. Because TokenSave truncates "
                 "large tool outputs, the count is a lower-bound estimate of the most "
                 "complex functions.\n")
    for r in results:
        cx = r['complexity_stats']
        lines.append(f"- **{r['name']}**: {cx['high_function_count'] if cx['high_function_count'] is not None else 'unassessed'} functions above "
                     f"complexity threshold {cx['threshold']}")
    lines.append("\n## Methodology\n")
    lines.append("See `README.md` for rubric definitions and weighting.\n")
    MATRIX_MD.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-index-complexity", action="store_true",
                        help="include complexity estimates from separately verified TokenSave indexes")
    INDEX_COMPLEXITY = parser.parse_args().with_index_complexity
    build_matrix()
