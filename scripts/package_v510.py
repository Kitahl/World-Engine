#!/usr/bin/env python3
"""Build and independently verify the full World Engine 5.1.0 Windows archive."""

from __future__ import annotations

import argparse
import compileall
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import package_v470 as inherited

RELEASE = "5.1.0"
PACKAGE_ROOT = "world_engine_v5_1_0_PYWEBVIEW_UI_ADAPTED_WINDOWS_FULL"
PACKAGE_NAME = PACKAGE_ROOT + ".zip"
HANDOFF_NAME = "WORLD_ENGINE_V510_HANDOFF.json"
CRITICAL_FILES = (
    "START_WORLD_ENGINE.bat",
    "START_COMPANION_UI.bat",
    "run_windows.bat",
    "app.py",
    "world_engine_startup.py",
    "world_engine_permanent_endpoint.py",
    "openapi_actions.json",
    "CUSTOM_GPT_INSTRUCTIONS_V510.txt",
    "GPT_INSTRUCTIONS.md",
    "README.md",
    "BUILD_REPORT_V500.md",
    "QUALIFICATION_REPORT_V501.md",
    "V5_0_1_CHANGELOG.md",
    "V5_0_CHANGELOG.md",
    "WORLD_ENGINE_5_0_0_CORRECTED_MERGED_PLAN_AND_IMPLEMENTATION_REPORT.md",
    "scripts/static_openapi_surface_audit.py",
    "scripts/narrative_release_audit.py",
    "scripts/release_verify_v510.py",
    "scripts/package_v510.py",
    "world_engine/mechanisms.py",
    "world_engine/incidents.py",
    "world_engine/politics.py",
    "world_engine/agency.py",
    "world_engine/quests.py",
    "world_engine/procedural.py",
    "world_engine/authoring.py",
    "world_engine/desktop.py",
    "world_engine/simulation.py",
    "world_engine_autostart.py",
    "companion_ui/index.html",
    "companion_ui/app.css",
    "companion_ui/app.js",
    "tests/test_v500_runtime_incidents.py",
    "tests/test_v500_politics.py",
    "tests/test_v500_agency.py",
    "tests/test_v500_quests.py",
    "tests/test_v500_procedural_runtime.py",
    "tests/test_v500_persistence_qualification.py",
    "tests/test_v500_playtest_qualification.py",
    "tests/test_v500_stress_qualification.py",
    "tests/test_v440_desktop.py",
    "world_engine_companion.py",
    "world_engine/process_guard.py",
    "requirements.txt",
    "requirements-companion.txt",
    "launcher.py",
    "V5_1_0_CHANGELOG.md",
    "UI_ADAPTATION_REPORT_V510.md",
    "tests/test_launcher.py",
    "tests/test_v398_permanent_full.py",
    "tests/test_v440_local_first_startup.py",
    "tests/test_v510_bridge_surface.py",
    "tests/test_v510_process_guard.py",
    "tests/test_v510_projection.py",
    "tests/test_v510_startup_recovery.py",
    "tests/test_v510_static_ui.py",
)


def _gate(completed: Any) -> dict[str, Any]:
    return inherited.gate_result(completed)


def verify_tree(tree: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="world-engine-v510-audits-") as temporary:
        audit_dir = Path(temporary)
        compilation = compileall.compile_dir(str(tree), quiet=1, force=True)
        pytest_result = inherited.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=tree,
            check=False,
            timeout=1200,
        )
        static_result = inherited.run(
            [sys.executable, "scripts/static_openapi_surface_audit.py"],
            cwd=tree,
            check=False,
            timeout=120,
        )
        release_result = inherited.run(
            [
                sys.executable,
                "scripts/release_verify_v510.py",
                "--output-dir",
                str(audit_dir),
            ],
            cwd=tree,
            check=False,
            timeout=600,
        )
        narrative_result = inherited.run(
            [
                sys.executable,
                "scripts/narrative_release_audit.py",
                "--output",
                str(audit_dir / "WORLD_ENGINE_V510_NARRATIVE_AUDIT.json"),
                "--release",
                RELEASE,
            ],
            cwd=tree,
            check=False,
            timeout=300,
        )
        output = pytest_result.stdout + pytest_result.stderr
        passed_match = re.search(r"(\d+) passed", output)
        subtest_match = re.search(r"(\d+) subtests passed", output)
        passed = all(
            (
                compilation,
                pytest_result.returncode == 0,
                static_result.returncode == 0,
                release_result.returncode == 0,
                narrative_result.returncode == 0,
            )
        )
        return {
            "passed": passed,
            "python_compilation": bool(compilation),
            "pytest": {
                **_gate(pytest_result),
                "passed_tests": int(passed_match.group(1)) if passed_match else None,
                "passed_subtests": int(subtest_match.group(1)) if subtest_match else 0,
            },
            "static_action_audit": _gate(static_result),
            "release_verifier": _gate(release_result),
            "narrative_release_audit": _gate(narrative_result),
        }


def verify_extracted(zip_path: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="world-engine-v510-extracted-"
    ) as temporary:
        destination = Path(temporary)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(destination)
        return verify_tree(destination / PACKAGE_ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    dirty = inherited.git_text("status", "--porcelain")
    if dirty:
        raise SystemExit(
            "Refusing to package a dirty worktree. Commit and verify first."
        )
    source_commit = inherited.git_text("rev-parse", "HEAD")
    files = inherited.tracked_files()
    tracked_names = {path.as_posix() for path in files}
    missing = sorted(set(CRITICAL_FILES) - tracked_names)
    if missing:
        raise SystemExit(f"Critical package inputs are not tracked: {missing}")
    hashes = {name: inherited.sha256_file(ROOT / name) for name in CRITICAL_FILES}
    source_verification = verify_tree(ROOT)
    if not source_verification["passed"]:
        raise SystemExit(
            "Source verification failed before packaging:\n"
            + json.dumps(source_verification, indent=2, ensure_ascii=False)
        )
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / PACKAGE_NAME
    handoff_path = output_dir / HANDOFF_NAME
    existing = [str(path) for path in (zip_path, handoff_path) if path.exists()]
    if existing:
        raise SystemExit(f"Refusing to overwrite existing outputs: {existing}")
    inherited.PACKAGE_ROOT = PACKAGE_ROOT
    inherited.build_archive(zip_path, files)
    archive_verification = inherited.verify_archive(zip_path, files)
    extracted_verification = (
        verify_extracted(zip_path)
        if archive_verification["passed"]
        else {"passed": False, "skipped": "archive verification failed"}
    )
    final_commit = inherited.git_text("rev-parse", "HEAD")
    final_dirty = inherited.git_text("status", "--porcelain")
    final_hashes = {name: inherited.sha256_file(ROOT / name) for name in CRITICAL_FILES}
    stable = (
        source_commit == final_commit and not final_dirty and hashes == final_hashes
    )
    if not stable:
        zip_path.unlink(missing_ok=True)
        raise SystemExit("Source changed during packaging; incomplete ZIP removed.")
    handoff = {
        "handoff_version": "WE510-HANDOFF-1.0",
        "release": RELEASE,
        "package": zip_path.name,
        "package_root": PACKAGE_ROOT,
        "source_commit": source_commit,
        "source_worktree_clean": True,
        "source_stable_through_handoff": True,
        "zip_size_bytes": zip_path.stat().st_size,
        "zip_sha256": inherited.sha256_file(zip_path),
        "tracked_source_files": len(files),
        "critical_file_sha256": hashes,
        "source_verification": source_verification,
        "archive_verification": archive_verification,
        "extracted_verification": extracted_verification,
        "unverified_boundaries": [
            "actual Windows double-click startup and Service Control Manager execution",
            "live ngrok, Cloudflare, and Tailscale connectivity/accounts",
            "live Foundry relay delivery",
            "graphical pywebview rendering and OS clipboard behavior",
        ],
    }
    handoff["passed"] = bool(
        source_verification["passed"]
        and archive_verification["passed"]
        and extracted_verification["passed"]
    )
    handoff_path.write_text(
        json.dumps(handoff, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(handoff, indent=2, ensure_ascii=False))
    return 0 if handoff["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
