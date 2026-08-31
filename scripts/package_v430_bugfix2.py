#!/usr/bin/env python3
"""Build and independently verify the World Engine 4.3.0 BUGFIX2 archive."""

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
PACKAGE_ROOT = "world_engine_chatgpt_v4_3_0_OUTPUT_COMPANION_HARDENED_BUGFIX2"
PACKAGE_NAME = "world_engine_v4_3_0_OUTPUT_COMPANION_HARDENED_WINDOWS_FULL_BUGFIX2.zip"
HANDOFF_NAME = "BUGFIX2_HANDOFF_V430_2026-08-30.json"
CRITICAL_FILES = (
    "START_WORLD_ENGINE.bat",
    "START_COMPANION_WORKER.bat",
    "run_windows.bat",
    "INSTALL_CLOUDFLARE_NAMED.bat",
    "launcher.py",
    "world_engine_startup.py",
    "world_engine_permanent_endpoint.py",
    "scripts/static_openapi_surface_audit.py",
    "scripts/release_verify_v420.py",
    "scripts/package_v430_bugfix2.py",
    "openapi_actions.json",
    "CUSTOM_GPT_INSTRUCTIONS_V430.txt",
    "BUGFIX2_REPORT_V430_2026-08-30.md",
    "BUGFIX2_AUDIT_V430_2026-08-30.json",
    "WORLD_ENGINE_V430_SOURCE_AUDIT.json",
    "WORLD_ENGINE_V430_RELEASE_AUDIT.json",
)


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


def verify_extracted(zip_path: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="world-engine-v430-bugfix2-") as temp:
        destination = Path(temp)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(destination)
        extracted = destination / PACKAGE_ROOT
        compile_passed = compileall.compile_dir(str(extracted), quiet=1, force=True)
        pytest_result = run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=extracted,
            check=False,
            timeout=900,
        )
        static_result = run(
            [sys.executable, "scripts/static_openapi_surface_audit.py"],
            cwd=extracted,
            check=False,
            timeout=120,
        )
        audit_dir = extracted / "_package_verification_audits"
        audit_dir.mkdir()
        release_result = run(
            [
                sys.executable,
                "scripts/release_verify_v420.py",
                "--output-dir",
                str(audit_dir),
            ],
            cwd=extracted,
            check=False,
            timeout=300,
        )
        match = re.search(
            r"(\d+) passed(?:, (\d+) subtests passed)?",
            pytest_result.stdout + pytest_result.stderr,
        )
        return {
            "passed": (
                compile_passed
                and pytest_result.returncode == 0
                and static_result.returncode == 0
                and release_result.returncode == 0
            ),
            "python_compilation": bool(compile_passed),
            "pytest": {
                **gate_result(pytest_result),
                "passed_tests": int(match.group(1)) if match else None,
                "passed_subtests": int(match.group(2) or 0) if match else None,
            },
            "static_action_audit": gate_result(static_result),
            "release_verifier": gate_result(release_result),
        }


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
        "handoff_version": "WE430-BUGFIX2-HANDOFF-1.0",
        "release": "4.3.0",
        "package": zip_path.name,
        "package_root": PACKAGE_ROOT,
        "source_commit": source_commit,
        "source_worktree_clean": True,
        "source_stable_through_handoff": True,
        "zip_size_bytes": zip_path.stat().st_size,
        "zip_sha256": sha256_file(zip_path),
        "tracked_source_files": len(files),
        "critical_file_sha256": critical_hashes,
        "archive_verification": archive_verification,
        "extracted_verification": extracted_verification,
        "unverified_boundaries": [
            "actual cloudflared Windows service start/restart and service ACLs",
            "live ngrok, Cloudflare, and Tailscale connectivity",
            "live Foundry relay delivery",
            "graphical pywebview behavior",
        ],
    }
    handoff["passed"] = bool(
        archive_verification["passed"] and extracted_verification["passed"]
    )
    handoff_path.write_text(
        json.dumps(handoff, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(handoff, indent=2, ensure_ascii=False))
    return 0 if handoff["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
