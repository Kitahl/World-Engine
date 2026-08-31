#!/usr/bin/env python3
"""Build and independently verify the full World Engine 4.5.0 Windows archive."""

from __future__ import annotations

import argparse
import compileall
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = "world_engine_v4_5_0_PROCEDURAL_DESKTOP_PBEM_ENVIRONMENT_WINDOWS_FULL"
PACKAGE_NAME = PACKAGE_ROOT + ".zip"
HANDOFF_NAME = "WORLD_ENGINE_V450_HANDOFF.json"
CRITICAL_FILES = (
    "START_WORLD_ENGINE.bat",
    "START_COMPANION_UI.bat",
    "START_COMPANION_WORKER.bat",
    "run_windows.bat",
    "INSTALL_CLOUDFLARE_NAMED.bat",
    "launcher.py",
    "world_engine_startup.py",
    "world_engine_permanent_endpoint.py",
    "scripts/static_openapi_surface_audit.py",
    "scripts/release_verify_v450.py",
    "scripts/package_v450.py",
    "openapi_actions.json",
    "CUSTOM_GPT_INSTRUCTIONS_V450.txt",
    "GPT_INSTRUCTIONS.md",
    "BUILD_REPORT_V450.md",
    "V4_5_CHANGELOG.md",
    "MERGE_ANALYSIS_V450.md",
    "world_engine/environment.py",
    "world_engine/pbem.py",
    "world_engine/procedural.py",
    "world_engine/desktop.py",
    "world_engine_companion.py",
    "companion_ui/index.html",
    "companion_ui/app.css",
    "companion_ui/app.js",
    "tests/test_v450_environment.py",
    "tests/test_v450_pbem.py",
    "tests/test_procedural.py",
    "tests/test_v440_desktop.py",
)

FORBIDDEN_PACKAGE_PARTS = {
    ".git", ".venv", "graphify-out", "__pycache__", ".pytest_cache",
    ".release-audit-v450",
}


def run(
    command: list[str],
    *,
    cwd: Path = ROOT,
    check: bool = True,
    timeout: int | None = 600,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if check and completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
    return completed


def git_text(*arguments: str) -> str:
    return run(["git", *arguments], timeout=60).stdout.strip()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tracked_files() -> list[Path]:
    raw = run(["git", "ls-files", "-z"], timeout=60).stdout
    paths = [Path(item) for item in raw.split("\0") if item]
    missing = [str(path) for path in paths if not (ROOT / path).is_file()]
    if missing:
        raise RuntimeError(f"tracked package inputs are missing: {missing}")
    forbidden = [
        str(path) for path in paths
        if FORBIDDEN_PACKAGE_PARTS.intersection(path.parts)
        or path.suffix.lower() in {".pyc", ".sqlite3", ".wal", ".shm"}
        or path.name in {".env", "launcher_config.json"}
    ]
    if forbidden:
        raise RuntimeError(f"forbidden generated/local inputs are tracked: {forbidden}")
    return sorted(paths, key=lambda item: item.as_posix())


def zip_timestamp() -> tuple[int, int, int, int, int, int]:
    epoch = int(git_text("show", "-s", "--format=%ct", "HEAD"))
    value = datetime.fromtimestamp(epoch, tz=timezone.utc)
    if value.year < 1980:
        return (1980, 1, 1, 0, 0, 0)
    return (value.year, value.month, value.day, value.hour, value.minute, value.second)


def build_archive(zip_path: Path, files: list[Path]) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = zip_path.with_suffix(zip_path.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    timestamp = zip_timestamp()
    with zipfile.ZipFile(
        temporary,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for relative in files:
            source = ROOT / relative
            arcname = f"{PACKAGE_ROOT}/{relative.as_posix()}"
            info = zipfile.ZipInfo(arcname, date_time=timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            mode = stat.S_IMODE(source.stat().st_mode)
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, source.read_bytes(), compresslevel=9)
    os.replace(temporary, zip_path)


def verify_archive(zip_path: Path, files: list[Path]) -> dict[str, Any]:
    expected_names = [f"{PACKAGE_ROOT}/{path.as_posix()}" for path in files]
    byte_matches = 0
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        bad_member = archive.testzip()
        unsafe = [
            name
            for name in names
            if PurePosixPath(name).is_absolute()
            or ".." in PurePosixPath(name).parts
            or not PurePosixPath(name).parts
            or PurePosixPath(name).parts[0] != PACKAGE_ROOT
        ]
        for relative, name in zip(files, expected_names, strict=True):
            if archive.read(name) == (ROOT / relative).read_bytes():
                byte_matches += 1
    duplicates = len(names) - len(set(names))
    exact_inventory = names == expected_names
    passed = (
        bad_member is None
        and not unsafe
        and duplicates == 0
        and exact_inventory
        and byte_matches == len(files)
    )
    return {
        "passed": passed,
        "zip_test_error": bad_member,
        "archive_entries": len(names),
        "unsafe_entries": unsafe,
        "duplicate_entries": duplicates,
        "exact_tracked_file_inventory": exact_inventory,
        "byte_for_byte_entries_verified": byte_matches,
    }


def gate_result(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    output = (completed.stdout + "\n" + completed.stderr).strip()
    return {
        "passed": completed.returncode == 0,
        "exit_code": completed.returncode,
        "output_tail": "\n".join(output.splitlines()[-40:]),
    }


def verify_tree(tree: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="world-engine-v450-audits-") as audit_temp:
        compile_passed = compileall.compile_dir(str(tree), quiet=1, force=True)
        pytest_result = run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=tree,
            check=False,
            timeout=900,
        )
        static_result = run(
            [sys.executable, "scripts/static_openapi_surface_audit.py"],
            cwd=tree,
            check=False,
            timeout=120,
        )
        audit_dir = Path(audit_temp)
        release_result = run(
            [
                sys.executable,
                "scripts/release_verify_v450.py",
                "--output-dir",
                str(audit_dir),
            ],
            cwd=tree,
            check=False,
            timeout=300,
        )
        narrative_result = run(
            [
                sys.executable,
                "scripts/narrative_release_audit.py",
                "--output",
                str(audit_dir / "WORLD_ENGINE_V450_NARRATIVE_AUDIT.json"),
            ],
            cwd=tree,
            check=False,
            timeout=300,
        )
        pytest_output = pytest_result.stdout + pytest_result.stderr
        match = re.search(r"(\d+) passed", pytest_output)
        subtests_match = re.search(r"(\d+) subtests passed", pytest_output)
        return {
            "passed": (
                compile_passed
                and pytest_result.returncode == 0
                and static_result.returncode == 0
                and release_result.returncode == 0
                and narrative_result.returncode == 0
            ),
            "python_compilation": bool(compile_passed),
            "pytest": {
                **gate_result(pytest_result),
                "passed_tests": int(match.group(1)) if match else None,
                "passed_subtests": int(subtests_match.group(1)) if subtests_match else 0,
            },
            "static_action_audit": gate_result(static_result),
            "release_verifier": gate_result(release_result),
            "narrative_release_audit": gate_result(narrative_result),
        }


def verify_extracted(zip_path: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="world-engine-v450-extracted-") as temp:
        destination = Path(temp)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(destination)
        return verify_tree(destination / PACKAGE_ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    dirty = git_text("status", "--porcelain")
    if dirty:
        raise SystemExit("Refusing to package a dirty worktree. Commit and verify the release first.")
    source_commit = git_text("rev-parse", "HEAD")
    files = tracked_files()
    tracked_names = {path.as_posix() for path in files}
    missing_critical = sorted(set(CRITICAL_FILES) - tracked_names)
    if missing_critical:
        raise SystemExit(f"Critical package inputs are not tracked: {missing_critical}")
    critical_hashes = {
        relative: sha256_file(ROOT / relative)
        for relative in CRITICAL_FILES
    }
    source_verification = verify_tree(ROOT)
    if not source_verification["passed"]:
        raise SystemExit(
            "Source verification failed before packaging:\n"
            + json.dumps(source_verification, indent=2, ensure_ascii=False)
        )
    zip_path = args.output_dir.resolve() / PACKAGE_NAME
    handoff_path = args.output_dir.resolve() / HANDOFF_NAME
    existing_outputs = [str(path) for path in (zip_path, handoff_path) if path.exists()]
    if existing_outputs:
        raise SystemExit(f"Refusing to overwrite existing release outputs: {existing_outputs}")
    build_archive(zip_path, files)
    archive_verification = verify_archive(zip_path, files)
    if archive_verification["passed"]:
        extracted_verification = verify_extracted(zip_path)
    else:
        extracted_verification = {
            "passed": False,
            "skipped": "Archive integrity/inventory verification failed before extraction",
        }
    final_commit = git_text("rev-parse", "HEAD")
    final_dirty = git_text("status", "--porcelain")
    final_critical_hashes = {
        relative: sha256_file(ROOT / relative)
        for relative in CRITICAL_FILES
    }
    source_stable = (
        final_commit == source_commit
        and not final_dirty
        and final_critical_hashes == critical_hashes
    )
    if not source_stable:
        zip_path.unlink(missing_ok=True)
        raise SystemExit(
            "Source changed during packaging; the incomplete ZIP was removed and no handoff was written."
        )
    handoff = {
        "handoff_version": "WE450-HANDOFF-1.0",
        "release": "4.5.0",
        "package": zip_path.name,
        "package_root": PACKAGE_ROOT,
        "source_commit": source_commit,
        "source_worktree_clean": True,
        "source_stable_through_handoff": True,
        "zip_size_bytes": zip_path.stat().st_size,
        "zip_sha256": sha256_file(zip_path),
        "tracked_source_files": len(files),
        "critical_file_sha256": critical_hashes,
        "source_verification": source_verification,
        "archive_verification": archive_verification,
        "extracted_verification": extracted_verification,
        "unverified_boundaries": [
            "actual Windows double-click startup and Service Control Manager execution",
            "live ngrok, Cloudflare, and Tailscale connectivity",
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
        json.dumps(handoff, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(handoff, indent=2, ensure_ascii=False))
    return 0 if handoff["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
