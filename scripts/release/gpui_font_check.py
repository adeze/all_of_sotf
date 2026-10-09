#!/usr/bin/env python3
"""Qualify pinned GPUI font shaping, SVG fallback, and WGPU text paint."""

import hashlib
import json
import os
import ctypes
from pathlib import Path
import re
import tomllib
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

FONT_TEST = "bundled_font_weights_and_mixed_combining_runs_shape"
PAINT_TEST = "wgpu_paints_latin_combining_rtl_cjk_and_emoji"
SVG_TESTS = {
    "text_with_split_glyph_clusters_in_mixed_fonts_does_not_panic",
    "test_is_emoji_presentation",
    "fix_generic_font_families_sets_all_families",
    "test_select_emoji_font_skips_family_without_glyph",
    "fix_generic_font_families_monospace_resolves_to_lilex",
}
COSMIC_REV = "59089955e1c8698c6b83b2e6ab6ebceff825ff96"
INTERRUPTED = False


def interrupt(signum: int, _frame: object) -> None:
    global INTERRUPTED
    INTERRUPTED = True


def persist_report(evidence: Path, report: dict) -> None:
    report["interrupted"] = INTERRUPTED
    temporary = evidence / "report.json.tmp"
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    os.replace(temporary, evidence / "report.json")


def exact_test(text: str, name: str) -> None:
    if not re.search(rf"(?m)^test\s+(?:\S*::)?{re.escape(name)}\s+\.\.\.\s+ok$", text):
        raise RuntimeError(f"{name}: named test did not pass")
    if not re.search(r"test result: ok\.\s+1 passed; 0 failed; 0 ignored;", text):
        raise RuntimeError(f"{name}: unexpected test inventory")


def font_graph(lock_bytes: dict[str, bytes]) -> dict:
    forks = {
        entry["directory"]: entry
        for entry in json.loads((ROOT / "sotf-3rdparties" / "FORKS.json").read_bytes())["forks"]
    }
    pin = forks["cosmic-text"]["upstream_revision"]
    if pin != COSMIC_REV:
        raise RuntimeError("cosmic-text collection pin differs from reviewed upstream commit")
    results = {}
    for key, data in lock_bytes.items():
        packages = tomllib.loads(data.decode())["package"]
        cosmic = [p for p in packages if p["name"] == "sotf-cosmic-text"]
        fontdb = [p for p in packages if p["name"] == "fontdb"]
        if fontdb:
            if len(fontdb) != 1 or fontdb[0]["version"] != "0.24.0":
                raise RuntimeError(f"{key}: fontdb must resolve once at 0.24.0")
        if cosmic:
            if len(cosmic) != 1 or cosmic[0]["version"] != "0.19.0" or "source" in cosmic[0]:
                raise RuntimeError(f"{key}: sotf-cosmic-text is not the local 0.19.0 collection copy")
            if len(fontdb) != 1:
                raise RuntimeError(f"{key}: sotf-cosmic-text has no sole fontdb")
            if "fontdb" not in cosmic[0].get("dependencies", []):
                raise RuntimeError(f"{key}: sotf-cosmic-text does not use the sole fontdb")
            results[key] = {"cosmic_text": pin, "fontdb": fontdb[0]["version"]}
    if "gpui-toolkit/Cargo.lock" not in results:
        raise RuntimeError("GPUI font graph is absent")
    return results


def main() -> int:
    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    if len(sys.argv) != 3:
        print("usage: gpui_font_check.py EVIDENCE_DIR linux|macos", file=sys.stderr)
        return 2
    evidence = Path(sys.argv[1]).resolve()
    platform_name = sys.argv[2]
    report: dict = {"schema": 1, "platform": platform_name, "commands": [], "errors": []}
    if (platform_name not in {"linux", "macos"} or os.environ.get("CI") != "true"
            or os.environ.get("SOTF_GITEA_DISPOSABLE_RUNNER") != "1"
            or (platform_name == "macos") != (sys.platform == "darwin")):
        print("font qualification requires a disposable matching Gitea runner", file=sys.stderr)
        return 2
    enable_subreaper()
    evidence.mkdir(parents=True, exist_ok=True)
    persist_report(evidence, report)
    expected: dict[str, str] = {}
    try:
        manifest_bytes = (ROOT / "scripts/release/sources.json").read_bytes()
        expected = {name: entry["revision"] for name, entry in
                    json.loads(manifest_bytes)["sources"].items()}
        report["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
        before = source_state(ROOT, expected)
        report["before"] = before
        report["font_graph"] = font_graph(locks(ROOT))
        persist_report(evidence, report)
        workspace = ROOT / "gpui-toolkit"
        run_required("gpui-font-all-targets",
                     ["cargo", "check", "--locked", "-p", "sotf-gpui-toolkit-gpui",
                      "-p", "sotf-gpui-toolkit-gpui-wgpu", "--all-targets", "--all-features"],
                     workspace, evidence, report)
        text = run_required("bundled-font-layout",
                            ["cargo", "test", "--locked", "-p", "sotf-gpui-toolkit-gpui-wgpu",
                             "--features", "headless-qa", "--lib", FONT_TEST, "--",
                             "--test-threads=1", "--show-output"], workspace, evidence, report)
        exact_test(text, FONT_TEST)
        text = run_required("svg-font-fallback",
                            ["cargo", "test", "--locked", "-p", "sotf-gpui-toolkit-gpui",
                             "--lib", "svg_renderer::tests::", "--", "--test-threads=1",
                             "--show-output"], workspace, evidence, report)
        passed = set(re.findall(r"(?m)^test\s+(\S+)\s+\.\.\.\s+ok$", text))
        found = {name for name in SVG_TESTS if any(item.endswith("::" + name) for item in passed)}
        if found != SVG_TESTS or len(passed) != 5 or not re.search(
                r"test result: ok\.\s+5 passed; 0 failed; 0 ignored;", text):
            raise RuntimeError(f"SVG font test inventory differs: {sorted(found)}")
        paint_env = os.environ.copy()
        paint_env["GPUI_FONT_CAPTURE_DIR"] = str(evidence / "paint")
        text = run_required("wgpu-multiscript-paint",
                            ["cargo", "test", "--locked", "-p", "sotf-gpui-toolkit-gpui-wgpu",
                             "--features", "headless-qa", "--lib", PAINT_TEST, "--",
                             "--test-threads=1", "--show-output"],
                            workspace, evidence, report, env=paint_env)
        exact_test(text, PAINT_TEST)
        image = evidence / "paint" / "wgpu-font-scripts.png"
        if not image.is_file() or image.stat().st_size == 0:
            raise RuntimeError("WGPU paint screenshot missing")
        report["paint_png_sha256"] = sha256(image)
        after = source_state(ROOT, expected)
        report["after"] = after
        if after != before or hashlib.sha256((ROOT / "scripts/release/sources.json").read_bytes()).hexdigest() != report["manifest_sha256"]:
            raise RuntimeError("source, manifest, or one of ten locks changed")
    except Exception as error:
        report["errors"].append(str(error))
        if expected:
            try:
                report["after"] = source_state(ROOT, expected)
            except Exception as inspection_error:
                report["errors"].append(f"final source inspection: {inspection_error}")
    persist_report(evidence, report)
    return 1 if report["errors"] else 0

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def members(pgid: int) -> list[dict[str, str]]:
    listing = subprocess.run(["ps", "-axo", "pid=,ppid=,pgid=,stat="], text=True,
                             capture_output=True, check=True, timeout=5)
    found = []
    for line in listing.stdout.splitlines():
        fields = line.split()
        if len(fields) != 4 or not all(field.isdigit() for field in fields[:3]):
            raise ValueError(f"unparseable process inventory row: {line!r}")
        if int(fields[2]) == pgid:
            found.append(dict(zip(("pid", "ppid", "pgid", "state"), fields, strict=True)))
    return found


def enable_subreaper() -> None:
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER failed")


def clean_group(child: subprocess.Popen[bytes]) -> dict:
    errors: list[str] = []
    remaining: list[dict[str, str]] = []

    def reap_owned_descendants() -> None:
        if not sys.platform.startswith("linux") or child.poll() is None:
            return
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                return

    for signum in (signal.SIGTERM, signal.SIGKILL):
        try:
            child.poll()
            reap_owned_descendants()
            remaining = members(child.pid)
            if not remaining:
                break
        except Exception as error:
            errors.append(f"owned group inspection/reap: {error}")
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass
        except OSError as error:
            errors.append(f"owned group signal: {error}")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                child.poll()
                reap_owned_descendants()
                remaining = members(child.pid)
                if not remaining:
                    break
            except Exception as error:
                errors.append(f"owned group inspection/reap: {error}")
                break
            time.sleep(0.1)
    try:
        child.wait(timeout=5)
        reap_owned_descendants()
        remaining = members(child.pid)
    except Exception as error:
        errors.append(f"owned group final reap: {error}")
    return {"ok": not errors and not remaining, "errors": errors, "remaining": remaining}


def command(name: str, argv: list[str], cwd: Path, evidence: Path,
            env: dict[str, str] | None = None) -> dict:
    log_path = evidence / f"{name}.log"
    entry: dict = {"name": name, "argv": argv, "log": str(log_path), "status": "RUNNING"}
    if INTERRUPTED:
        entry.update({"exit_code": 130, "status": "FAIL", "not_started": True,
                      "cleanup": {"ok": True, "errors": [], "remaining": []}, "text": ""})
        return entry
    with log_path.open("wb") as log:
        if INTERRUPTED:
            entry.update({"exit_code": 130, "status": "FAIL", "not_started": True,
                          "cleanup": {"ok": True, "errors": [], "remaining": []}, "text": ""})
            return entry
        child: subprocess.Popen[bytes] | None = None
        code = 130
        try:
            child = subprocess.Popen(argv, cwd=cwd, env=env, stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
            entry["owned_pgid"] = child.pid
            next_heartbeat = time.monotonic() + 30
            while not INTERRUPTED:
                try:
                    code = child.wait(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= next_heartbeat:
                        print(f"[{name}] still running", flush=True)
                        next_heartbeat = time.monotonic() + 30
        finally:
            if child is not None:
                entry["cleanup"] = clean_group(child)
    text = log_path.read_text(errors="replace")
    entry.update({"exit_code": code, "text": text,
                  "status": "PASS" if code == 0 and entry["cleanup"]["ok"] else "FAIL"})
    return entry


def git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def locks(root: Path, revisions: dict[str, str] | None = None) -> dict[str, bytes]:
    result = {}
    for name in sorted(json.loads((root / "scripts/release/sources.json").read_text())["sources"]):
        paths = ["Cargo.lock"]
        if name == "autoeq":
            paths.append("crates/autoeq-gpui-examples/Cargo.lock")
        for relative in paths:
            key = f"{name}/{relative}"
            if revisions is None:
                result[key] = (root / key).read_bytes()
            else:
                result[key] = subprocess.check_output(
                    ["git", "show", f"{revisions[name]}:{relative}"], cwd=root / name
                )
    if len(result) != 10:
        raise RuntimeError(f"expected ten locks, found {len(result)}")
    return result


def duplicate_groups(lock_bytes: dict[str, bytes]) -> set[tuple[str, str]]:
    names: dict[str, set[str]] = {}
    sources: dict[tuple[str, str], set[str]] = {}
    for data in lock_bytes.values():
        for package in tomllib.loads(data.decode())["package"]:
            name, version = package["name"], package["version"]
            names.setdefault(name, set()).add(version)
            sources.setdefault((name, version), set()).add(package.get("source", "path/local"))
    groups = {("multiple_versions", name) for name, versions in names.items()
              if len(versions) > 1}
    groups.update(("mixed_sources", name) for (name, _), values in sources.items()
                  if len(values) > 1)
    return groups


def source_state(root: Path, expected: dict[str, str]) -> dict:
    state: dict = {"root_revision": git(root, "rev-parse", "HEAD"), "sources": {}}
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("aggregate root changed or contains untracked files")
    for name, revision in sorted(expected.items()):
        path = root / name
        entry = git(root, "ls-files", "--stage", "--", name).split()
        if len(entry) != 4 or entry[0] != "160000" or entry[1] != revision or entry[2] != "0":
            raise RuntimeError(f"{name}: root gitlink differs from manifest")
        actual = git(path, "rev-parse", "HEAD")
        status = git(path, "status", "--porcelain", "--untracked-files=all")
        if actual != revision or status:
            raise RuntimeError(f"{name}: checkout differs from clean pinned revision")
        state["sources"][name] = actual
    state["locks"] = {name: hashlib.sha256(data).hexdigest()
                      for name, data in locks(root).items()}
    return state


def run_required(name: str, argv: list[str], cwd: Path, evidence: Path, report: dict,
                 env: dict[str, str] | None = None) -> str:
    report["active_command"] = {"name": name, "argv": argv, "cwd": str(cwd)}
    persist_report(evidence, report)
    result = command(name, argv, cwd, evidence, env=env)
    report["commands"].append({key: value for key, value in result.items() if key != "text"})
    report["active_command"] = None
    persist_report(evidence, report)
    if result["status"] != "PASS":
        raise RuntimeError(f"{name}: command failed or left owned processes")
    return result["text"]




if __name__ == "__main__":
    raise SystemExit(main())
