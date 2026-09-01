#!/usr/bin/env python3
"""Build and independently verify the full World Engine 5.1.1 Windows archive."""

from __future__ import annotations

import argparse
import compileall
import hashlib
import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import package_v510 as inherited

RELEASE = "5.1.1"
PACKAGE_ROOT = "world_engine_v5_1_1_AUTOMATIC_TUNNEL_OFFLINE_MUSIC_WINDOWS_FULL"
PACKAGE_NAME = PACKAGE_ROOT + ".zip"
HANDOFF_NAME = "WORLD_ENGINE_V511_HANDOFF.json"
FULL_SUITE_TIMEOUT_SECONDS = 2400
ACCEPTED_BASE_ARCHIVE_NAME = "world_engine_v5_1_0_PYWEBVIEW_UI_ADAPTED_WINDOWS_FULL.zip"
ACCEPTED_BASE_ARCHIVE_SHA256 = (
    "57826818CAE1835B8075182FE935D289B55858B5F14A29E81B31D4A83286A205"
)
EXTRA_CRITICAL_FILES = (
    "scripts/release_verify_v511.py",
    "scripts/package_v511.py",
    "scripts/live_audio_probe_v511.py",
    "scripts/live_tunnel_probe_v511.py",
    "scripts/headless_player_v511.py",
    "START_WORLD_ENGINE.vbs",
    "ONE_CLICK_README.txt",
    "PERMANENT_ENDPOINT_GUIDE.md",
    "MUSIC_GUIDE.md",
    "HEADLESS_PLAYER_V511.md",
    "V5_1_1_CHANGELOG.md",
    "BUGFIX_REPORT_V511.md",
    "music_player.py",
    "world_engine/music.py",
    "requirements-music.txt",
    "companion_ui/ambient_audio.js",
    "tests/test_v511_automatic_tunnel.py",
    "tests/test_v511_offline_music.py",
    "tests/test_live_tunnel_probe_v511.py",
    "tests/test_v511_headless_player.py",
    "tests/test_v511_release_package.py",
)
CRITICAL_FILES = tuple(dict.fromkeys((*inherited.CRITICAL_FILES, *EXTRA_CRITICAL_FILES)))


def _gate(completed: Any) -> dict[str, Any]:
    return inherited._gate(completed)


def archive_member_manifest(zip_path: Path) -> dict[str, Any]:
    """Return an exhaustive, duplicate-sensitive archive member hash manifest."""
    members: list[dict[str, Any]] = []
    with zipfile.ZipFile(zip_path) as archive:
        infos = sorted(archive.infolist(), key=lambda info: info.filename)
        names = [info.filename for info in infos]
        for info in infos:
            payload = archive.read(info)
            members.append({"path": info.filename, "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)})
    return {"member_count": len(members), "duplicate_paths": sorted({name for name in names if names.count(name) > 1}), "members": members}


def accepted_base_provenance(archive_path: Path | None = None) -> dict[str, Any]:
    """Record, but never require, accepted 5.1.0 source-archive evidence."""
    default_path = ROOT.parents[1] / "deliverables" / ACCEPTED_BASE_ARCHIVE_NAME
    candidate = archive_path or Path(os.environ.get("WORLD_ENGINE_ACCEPTED_BASE_ARCHIVE", default_path))
    result: dict[str, Any] = {"supplied_archive_path": str(candidate), "supplied_archive_name": ACCEPTED_BASE_ARCHIVE_NAME, "expected_sha256": ACCEPTED_BASE_ARCHIVE_SHA256, "available": candidate.is_file(), "size_bytes": None, "actual_sha256": None, "sha256_matches_expected": None}
    if candidate.is_file():
        result["size_bytes"] = candidate.stat().st_size
        result["actual_sha256"] = inherited.inherited.sha256_file(candidate)
        result["sha256_matches_expected"] = (
            str(result["actual_sha256"]).casefold()
            == ACCEPTED_BASE_ARCHIVE_SHA256.casefold()
        )
    return result

def verify_tree(tree: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="world-engine-v511-audits-") as temporary:
        audit_dir = Path(temporary)
        compilation = compileall.compile_dir(str(tree), quiet=1, force=True)
        # A measured source-suite run took 1,314.73 seconds. Source and clean
        # extracted verification need headroom for cold imports and slower disks.
        pytest_result = inherited.inherited.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=tree,
            check=False,
            timeout=FULL_SUITE_TIMEOUT_SECONDS,
        )
        static_result = inherited.inherited.run(
            [sys.executable, "scripts/static_openapi_surface_audit.py"],
            cwd=tree,
            check=False,
            timeout=120,
        )
        release_result = inherited.inherited.run(
            [
                sys.executable,
                "scripts/release_verify_v511.py",
                "--output-dir",
                str(audit_dir),
            ],
            cwd=tree,
            check=False,
            timeout=600,
        )
        narrative_result = inherited.inherited.run(
            [
                sys.executable,
                "scripts/narrative_release_audit.py",
                "--output",
                str(audit_dir / "WORLD_ENGINE_V511_NARRATIVE_AUDIT.json"),
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
    with tempfile.TemporaryDirectory(prefix="world-engine-v511-extracted-") as temporary:
        destination = Path(temporary)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(destination)
        return verify_tree(destination / PACKAGE_ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    dirty = inherited.inherited.git_text("status", "--porcelain")
    if dirty:
        raise SystemExit("Refusing to package a dirty worktree. Commit and verify first.")
    source_commit = inherited.inherited.git_text("rev-parse", "HEAD")
    files = inherited.inherited.tracked_files()
    tracked_names = {path.as_posix() for path in files}
    missing = sorted(set(CRITICAL_FILES) - tracked_names)
    if missing:
        raise SystemExit(f"Critical package inputs are not tracked: {missing}")
    hashes = {name: inherited.inherited.sha256_file(ROOT / name) for name in CRITICAL_FILES}
    base_provenance = accepted_base_provenance()
    if base_provenance["available"] and not base_provenance["sha256_matches_expected"]:
        raise SystemExit("Accepted-base archive SHA-256 does not match supplied provenance.")
    source_verification = verify_tree(ROOT)
    if not source_verification["passed"]:
        raise SystemExit("Source verification failed before packaging:\n" + json.dumps(source_verification, indent=2, ensure_ascii=False))
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / PACKAGE_NAME
    handoff_path = output_dir / HANDOFF_NAME
    existing = [str(path) for path in (zip_path, handoff_path) if path.exists()]
    if existing:
        raise SystemExit(f"Refusing to overwrite existing outputs: {existing}")
    inherited.inherited.PACKAGE_ROOT = PACKAGE_ROOT
    inherited.inherited.build_archive(zip_path, files)
    archive_verification = inherited.inherited.verify_archive(zip_path, files)
    member_manifest = archive_member_manifest(zip_path)
    extracted_verification = verify_extracted(zip_path) if archive_verification["passed"] else {"passed": False, "skipped": "archive verification failed"}
    final_commit = inherited.inherited.git_text("rev-parse", "HEAD")
    final_dirty = inherited.inherited.git_text("status", "--porcelain")
    final_hashes = {name: inherited.inherited.sha256_file(ROOT / name) for name in CRITICAL_FILES}
    stable = source_commit == final_commit and not final_dirty and hashes == final_hashes
    if not stable:
        zip_path.unlink(missing_ok=True)
        raise SystemExit("Source changed during packaging; incomplete ZIP removed.")
    handoff = {
        "handoff_version": "WE511-HANDOFF-1.0",
        "release": RELEASE,
        "package": zip_path.name,
        "package_root": PACKAGE_ROOT,
        "source_commit": source_commit,
        "source_worktree_clean": True,
        "source_stable_through_handoff": True,
        "zip_size_bytes": zip_path.stat().st_size,
        "zip_sha256": inherited.inherited.sha256_file(zip_path),
        "tracked_source_files": len(files),
        "critical_file_sha256": hashes,
        "source_verification": source_verification,
        "archive_verification": archive_verification,
        "archive_member_manifest": member_manifest,
        "accepted_base_provenance": base_provenance,
        "extracted_verification": extracted_verification,
        "unverified_boundaries": [
            "physical speaker, driver, mixer, and mute-state audibility",
            "actual Windows double-click startup and Service Control Manager execution",
            "live named ngrok, Cloudflare, and Tailscale account connectivity",
            "live Foundry relay delivery and external GPT Builder configuration",
        ],
    }
    handoff["passed"] = bool(source_verification["passed"] and archive_verification["passed"] and extracted_verification["passed"])
    handoff_path.write_text(json.dumps(handoff, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(handoff, indent=2, ensure_ascii=False))
    return 0 if handoff["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())