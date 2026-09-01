#!/usr/bin/env python3
"""Generate independent World Engine 5.1.1 release audits.

5.1.1 is a patch release.  It re-pins the executable/API receipt to 5.1.1,
while deliberately retaining schema 24, the V510 GPT instruction contract,
WEGEN-2.0, and the WE-DESKTOP-5.1.0 projection contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import release_verify_v510 as inherited

RELEASE = "5.1.1"
EXPECTED_SCHEMA = 24


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def release_metadata_audit() -> dict[str, Any]:
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    policy_source = (ROOT / "world_engine" / "turn_policy.py").read_text(
        encoding="utf-8"
    )
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    one_click = (ROOT / "ONE_CLICK_README.txt").read_text(encoding="utf-8")
    endpoint_guide = (ROOT / "PERMANENT_ENDPOINT_GUIDE.md").read_text(encoding="utf-8")
    music_guide = (ROOT / "MUSIC_GUIDE.md").read_text(encoding="utf-8")
    required_release_files = (
        "START_WORLD_ENGINE.vbs",
        "ONE_CLICK_README.txt",
        "PERMANENT_ENDPOINT_GUIDE.md",
        "MUSIC_GUIDE.md",
        "scripts/headless_player_v511.py",
        "tests/test_v511_headless_player.py",
        "HEADLESS_PLAYER_V511.md",
        "tests/test_v511_automatic_tunnel.py",
        "tests/test_v511_offline_music.py",
        "tests/test_v511_release_package.py",
    )
    checks = {
        "app_api_version": 'version="5.1.1"' in app_source,
        "app_engine_receipt_version": 'ENGINE_VERSION = "5.1.1"' in app_source,
        "turn_policy_fallback_version": 'packet.get("engine_version", "5.1.1")'
        in policy_source,
        "schema_unchanged": "EXPECTED_SCHEMA = 24"
        in (ROOT / "scripts" / "release_verify_v510.py").read_text(encoding="utf-8")
        and EXPECTED_SCHEMA == 24,
        "desktop_projection_unchanged": "WE-DESKTOP-5.1.0" in readme,
        "active_gpt_instructions_unchanged": "CUSTOM_GPT_INSTRUCTIONS_V510.txt"
        in readme,
        "quick_tunnel_documented_temporary": all(
            marker in readme
            for marker in ("Cloudflare Quick Tunnel", "temporary", "re-import")
        ),
        "offline_music_documented": all(
            marker in readme
            for marker in ("generated locally", "no YouTube", "press **Play**")
        ),
        "release_files_present": all(
            (ROOT / item).is_file() for item in required_release_files
        ),
        "one_click_vbs_documented": "START_WORLD_ENGINE.vbs" in one_click,
        "guides_no_stale_streaming_or_ngrok_default": (
            "World Engine 5.1.0" not in endpoint_guide
            and "World Engine v3.6" not in music_guide
            and "Paste a YouTube URL" not in music_guide
            and "Cloudflare Quick Tunnel" in endpoint_guide
            and "offline" in music_guide.casefold()
        ),
    }
    return {
        "release": RELEASE,
        "checks": checks,
        "required_release_files": list(required_release_files),
        "passed": all(checks.values()),
    }


def run_audits() -> dict[str, dict[str, Any]]:
    # The inherited audit owns the mature OpenAPI, SQLite, HTTP, source, and
    # runtime checks.  Rebinding its module-level identity makes all receipt
    # checks exercise the 5.1.1 API without changing the retained contracts.
    inherited.RELEASE = RELEASE
    inherited.EXPECTED_SCHEMA = EXPECTED_SCHEMA
    results = inherited.run_audits()
    results["release_metadata"] = release_metadata_audit()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = run_audits()
    for name, result in results.items():
        _write(args.output_dir / f"WORLD_ENGINE_V511_{name.upper()}_AUDIT.json", result)
    summary = {
        "release": RELEASE,
        "audits": {name: bool(result["passed"]) for name, result in results.items()},
    }
    summary["passed"] = all(summary["audits"].values())
    _write(args.output_dir / "WORLD_ENGINE_V511_RELEASE_AUDIT.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())