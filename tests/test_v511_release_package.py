"""Release-package manifest and provenance checks for World Engine 5.1.1."""

from __future__ import annotations

import hashlib
import tempfile
import zipfile
from pathlib import Path

from scripts import package_v511


def test_critical_manifest_covers_hidden_start_and_new_runtime_assets() -> None:
    required = {
        "START_WORLD_ENGINE.vbs",
        "ONE_CLICK_README.txt",
        "PERMANENT_ENDPOINT_GUIDE.md",
        "MUSIC_GUIDE.md",
        "companion_ui/ambient_audio.js",
        "tests/test_v511_automatic_tunnel.py",
        "tests/test_v511_offline_music.py",
        "tests/test_v511_release_package.py",
    }
    assert required <= set(package_v511.CRITICAL_FILES)


def test_full_suite_timeout_exceeds_measured_runtime_with_extraction_headroom() -> None:
    assert package_v511.FULL_SUITE_TIMEOUT_SECONDS >= 1_800
    assert package_v511.FULL_SUITE_TIMEOUT_SECONDS > 1_315


def test_archive_member_manifest_hashes_every_member_and_detects_duplicates() -> None:
    with tempfile.TemporaryDirectory() as raw:
        archive_path = Path(raw) / "release.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("root/a.txt", b"alpha")
            archive.writestr("root/b.txt", b"beta")
        manifest = package_v511.archive_member_manifest(archive_path)
    by_path = {row["path"]: row for row in manifest["members"]}
    assert manifest["member_count"] == 2
    assert manifest["duplicate_paths"] == []
    assert by_path["root/a.txt"]["sha256"] == hashlib.sha256(b"alpha").hexdigest()
    assert by_path["root/b.txt"]["sha256"] == hashlib.sha256(b"beta").hexdigest()


def test_base_provenance_is_optional_and_validates_when_available() -> None:
    unavailable = package_v511.accepted_base_provenance(Path("missing-base.zip"))
    assert unavailable["available"] is False
    assert unavailable["sha256_matches_expected"] is None
    discovered = package_v511.accepted_base_provenance()
    assert discovered["supplied_archive_name"] == package_v511.ACCEPTED_BASE_ARCHIVE_NAME
    assert discovered["expected_sha256"] == package_v511.ACCEPTED_BASE_ARCHIVE_SHA256
    if discovered["available"]:
        assert discovered["sha256_matches_expected"] is True
        assert discovered["size_bytes"] == 1_287_876