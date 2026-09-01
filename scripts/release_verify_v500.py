#!/usr/bin/env python3
"""Generate independent World Engine 5.0.0 release audits."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import release_verify_v470 as inherited
from world_engine import WorldEngine
from world_engine.desktop import DesktopProjectionKernel
from world_engine.openapi_compat import PUBLIC_ACTION_OPERATION_IDS
from world_engine.procedural import (
    GENERATION_CONTRACT_VERSION,
    SUPPORTED_GENERATION_CONTRACTS,
)


RELEASE = "5.0.0"
EXPECTED_SCHEMA = 24
EXPECTED_FEATURES = {
    **inherited.EXPECTED_FEATURES,
    "procedural_desktop_companion": RELEASE,
    "canonical_mechanism_contract": RELEASE,
    "event_incident_runtime": RELEASE,
    "politics_commitment_runtime": RELEASE,
    "actor_agency_runtime": RELEASE,
    "quest_graph_runtime": RELEASE,
}
REQUIRED_RUNTIME_TABLES = {
    "incident_definitions",
    "incident_pressures",
    "incident_instances",
    "incident_runtime_state",
    "politics_commitments",
    "politics_projects",
    "politics_claims",
    "politics_grievances",
    "politics_treaties",
    "politics_wars",
    "agency_affordances",
    "agency_goals",
    "agency_plans",
    "agency_memories",
    "quest_runtime_instances",
    "quest_nodes",
    "quest_transition_receipts",
}


def _configure_inherited() -> None:
    inherited.RELEASE = RELEASE
    inherited.EXPECTED_SCHEMA = EXPECTED_SCHEMA
    inherited.EXPECTED_FEATURES = EXPECTED_FEATURES


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def source_audit() -> dict[str, Any]:
    source = ROOT / "legacy" / "World_Engine_1.63.txt"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    instruction_path = ROOT / "CUSTOM_GPT_INSTRUCTIONS_V500.txt"
    instructions = instruction_path.read_text(encoding="utf-8")
    mirror = (ROOT / "GPT_INSTRUCTIONS.md").read_text(encoding="utf-8")
    required = {
        "resolveTurn",
        "publishPresentation",
        "NRP-1.2",
        "_narrative_render_packet",
        "PLAYER AUTHORSHIP",
        "expected_revision",
        "idempotency_key",
        "semantic_review_required",
        "rejected",
        "PBEM 2.2",
        "WEGEN-2.0",
        "exactly five operations",
        "standalone local desktop app",
        "politics",
        "actor agency",
        "incidents",
        "executable quests",
    }
    missing = sorted(marker for marker in required if marker not in instructions)
    checks = {
        "legacy_source_unchanged": digest == inherited.EXPECTED_163_SHA256,
        "instruction_under_8000_bytes": len(instructions.encode("utf-8")) <= 8000,
        "instruction_markers": not missing,
        "instruction_mirror_exact": instructions == mirror,
    }
    return {
        "release": RELEASE,
        "active_instruction_file": instruction_path.name,
        "active_instruction_bytes": len(instructions.encode("utf-8")),
        "active_instruction_sha256": hashlib.sha256(
            instructions.encode("utf-8")
        ).hexdigest(),
        "missing_active_instruction_markers": missing,
        "checks": checks,
        "passed": all(checks.values()),
    }


def runtime_audit() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="world-engine-v500-") as temporary:
        path = Path(temporary) / "runtime.sqlite3"
        engine = WorldEngine(path)
        engine.ensure_campaign("release", "World Engine 5.0 audit")
        revision = engine.get_campaign("release")["revision"]
        staged = engine.stage_generated_world(
            "release",
            "release-runtime",
            "release-seed",
            {
                "location_count": 3,
                "faction_count": 2,
                "npcs_per_faction": 1,
                "resource_count": 1,
                "quest_count": 1,
            },
            expected_revision=revision,
        )
        validation = engine.author_validate("release", "release-runtime")
        dry_run = engine.author_dry_run("release", "release-runtime", days=1)
        promoted = engine.author_promote("release", "release-runtime")
        projection = DesktopProjectionKernel(engine, "release").snapshot()
        engine.commit_event(
            "release",
            "private_probe",
            "entity-only marker",
            scope_type="ENTITY",
            principal_kind="character",
            principal_id="missing-but-private",
        )
        engine.commit_event(
            "release",
            "secret_probe",
            "secret marker",
            sensitivity="SECRET",
            scope_type="GM",
        )
        public_types = {
            row["event_type"] for row in engine.get_world_context("release")["recent_events"]
        }
        with closing(sqlite3.connect(path)) as db:
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            user_version = int(db.execute("PRAGMA user_version").fetchone()[0])
            integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_keys = len(db.execute("PRAGMA foreign_key_check").fetchall())
            features = dict(
                db.execute(
                    "SELECT feature_id,feature_version FROM we42_schema_features"
                ).fetchall()
            )
            runtime_counts = {
                table: int(
                    db.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE campaign_id='release'"
                    ).fetchone()[0]
                )
                for table in (
                    "incident_definitions",
                    "politics_territorial_control",
                    "agency_goals",
                    "quest_runtime_instances",
                )
            }
        checks = {
            "schema_24": user_version == EXPECTED_SCHEMA,
            "integrity": integrity == "ok",
            "foreign_keys": foreign_keys == 0,
            "runtime_tables": REQUIRED_RUNTIME_TABLES <= tables,
            "feature_manifest": all(
                features.get(key) == value for key, value in EXPECTED_FEATURES.items()
            ),
            "generation_contract": (
                staged["generation"]["contract_version"]
                == GENERATION_CONTRACT_VERSION
                == "WEGEN-2.0"
            ),
            "generation_backward_compatible": {
                "WEGEN-1.0", "WEGEN-1.1", "WEGEN-1.2", "WEGEN-2.0"
            } <= SUPPORTED_GENERATION_CONTRACTS,
            "generation_valid": bool(validation["valid"]),
            "generation_dry_run": bool(dry_run["passed"]),
            "generation_promoted": promoted["status"] == "promoted",
            "generated_runtime_rows": all(value > 0 for value in runtime_counts.values()),
            "desktop_projection": projection["schema"] == "WE-DESKTOP-5.0.0",
            "desktop_local_domains": all(
                key in projection
                for key in ("journal", "agency", "politics", "quests")
            ) and "incidents" in projection["journal"],
            "entity_scope_not_world_public": "private_probe" not in public_types,
            "secret_not_world_public": "secret_probe" not in public_types,
            "exactly_five_public_actions": len(PUBLIC_ACTION_OPERATION_IDS) == 5,
        }
        return {
            "release": RELEASE,
            "checks": checks,
            "runtime_counts": runtime_counts,
            "passed": all(checks.values()),
        }


def run_audits() -> dict[str, dict[str, Any]]:
    _configure_inherited()
    return {
        "openapi": inherited.openapi_audit(),
        "sqlite": inherited.sqlite_audit(),
        "http": inherited.http_audit(),
        "source": source_audit(),
        "runtime": runtime_audit(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = run_audits()
    for name, result in results.items():
        _write(args.output_dir / f"WORLD_ENGINE_V500_{name.upper()}_AUDIT.json", result)
    summary = {
        "release": RELEASE,
        "audits": {name: bool(result["passed"]) for name, result in results.items()},
    }
    summary["passed"] = all(summary["audits"].values())
    _write(args.output_dir / "WORLD_ENGINE_V500_RELEASE_AUDIT.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
