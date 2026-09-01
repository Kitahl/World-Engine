#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from world_engine import WorldEngine
from world_engine.desktop import DesktopProjectionKernel
from world_engine.narrative import NARRATIVE_SCHEMA
from world_engine.openapi_compat import PUBLIC_ACTION_OPERATION_IDS, object_schema_paths_missing_properties
from world_engine.pbem import PBEM_INTEGRATION_VERSION
from world_engine.procedural import GENERATION_CONTRACT_VERSION, SUPPORTED_GENERATION_CONTRACTS
from world_engine.turn_router import DEFAULT_CAPABILITIES

RELEASE = "4.7.0"
EXPECTED_SCHEMA = 20
EXPECTED_ACTIONS = 5
EXPECTED_CAPABILITIES = 33
EXPECTED_NARRATIVE_TABLES = 13
EXPECTED_163_SHA256 = "0748cf20e6fc870055d1d96ac329b83561c71162922bbb2220278ccb1f2feee5"
EXPECTED_ENVIRONMENT_TABLES = {
    "environment_disaster_config", "environment_effects", "environment_materials",
    "environment_disaster_counters", "environment_targets", "environment_weather",
}
EXPECTED_MECHANISM_TABLES = {
    "mechanism_operators", "mechanism_execution_receipts",
}
EXPECTED_ECONOMY_TABLES = {
    "economy_config", "economy_markets", "economy_market_items",
    "economy_extractors", "economy_producers", "economy_routes",
    "economy_shipments", "economy_supply_links", "economy_transactions",
}
EXPECTED_POPULATION_TABLES = {
    "population_config", "settlement_profiles", "population_cohorts",
    "population_households", "settlement_labor", "settlement_service_needs",
    "population_flows",
}
EXPECTED_FEATURES = {
    "output_companion_hardening": "4.3.0",
    "procedural_desktop_companion": "4.7.0",
    "environment_consequence_runtime": "4.5.0",
    "pbem_public_boundary": "4.7.0",
    "canonical_mechanism_contract": "4.7.0",
    "economy_production_logistics_runtime": "4.7.0",
    "population_lifecycle_settlement_runtime": "4.7.0",
}


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _narrative_table_names() -> set[str]:
    names: set[str] = set()
    for statement in NARRATIVE_SCHEMA.split(";"):
        words = statement.strip().split()
        if len(words) >= 6 and words[:5] == ["CREATE", "TABLE", "IF", "NOT", "EXISTS"]:
            names.add(words[5].strip())
    return names


def openapi_audit() -> dict[str, Any]:
    schema = json.loads((ROOT / "openapi_actions.json").read_text(encoding="utf-8"))
    operations: list[dict[str, Any]] = []
    for path, methods in (schema.get("paths") or {}).items():
        for method, operation in methods.items():
            if isinstance(operation, dict) and operation.get("operationId"):
                operations.append({
                    "operation_id": operation["operationId"],
                    "method": method.upper(),
                    "path": path,
                    "is_consequential": operation.get("x-openai-isConsequential"),
                })
    refs: list[str] = []
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "$ref" and isinstance(item, str):
                    refs.append(item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(schema)
    components = ((schema.get("components") or {}).get("schemas") or {})
    unresolved = sorted({
        ref for ref in refs
        if ref.startswith("#/components/schemas/") and ref.rsplit("/", 1)[-1] not in components
    })
    turn_schema = components.get("ResolveTurnRequest") or {}
    result = {
        "release": RELEASE,
        "info_version": (schema.get("info") or {}).get("version"),
        "operations": len(operations),
        "unique_operation_ids": len({item["operation_id"] for item in operations}),
        "non_consequential_false": sum(item["is_consequential"] is False for item in operations),
        "resolveTurn_present": any(item["operation_id"] == "resolveTurn" for item in operations),
        "resolveTurn_supports_narrative_mode_override": "narrative_mode_override" in (turn_schema.get("properties") or {}),
        "resolveTurn_supports_narrative_hint": "narrative_hint" in (turn_schema.get("properties") or {}),
        "missing_object_properties": [list(path) for path in object_schema_paths_missing_properties(schema)],
        "unresolved_refs": unresolved,
        "operations_detail": sorted(operations, key=lambda x: (x["operation_id"], x["path"])),
        "operation_ids": sorted(item["operation_id"] for item in operations),
    }
    result["passed"] = all([
        result["info_version"] == RELEASE,
        result["operations"] == EXPECTED_ACTIONS,
        result["unique_operation_ids"] == EXPECTED_ACTIONS,
        set(result["operation_ids"]) == set(PUBLIC_ACTION_OPERATION_IDS),
        result["non_consequential_false"] == EXPECTED_ACTIONS,
        result["resolveTurn_present"],
        result["resolveTurn_supports_narrative_mode_override"],
        result["resolveTurn_supports_narrative_hint"],
        not result["missing_object_properties"],
        not result["unresolved_refs"],
    ])
    return result


def sqlite_audit() -> dict[str, Any]:
    expected_tables = _narrative_table_names()
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        fresh_path = td_path / "fresh.sqlite3"
        fresh = WorldEngine(fresh_path)
        fresh.ensure_campaign("fresh", "Fresh schema audit")
        with closing(sqlite3.connect(fresh_path)) as db, db:
            fresh_version = db.execute("PRAGMA user_version").fetchone()[0]
            fresh_integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            fresh_fk = db.execute("PRAGMA foreign_key_check").fetchall()
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            compiler_tables = {
                "knowledge_claims", "context_compile_receipts", "context_compile_items", "context_index_state"
            }.issubset(tables)
            environment_tables = EXPECTED_ENVIRONMENT_TABLES.intersection(tables)
            mechanism_tables = EXPECTED_MECHANISM_TABLES.intersection(tables)
            economy_tables = EXPECTED_ECONOMY_TABLES.intersection(tables)
            population_tables = EXPECTED_POPULATION_TABLES.intersection(tables)
            material_count = db.execute(
                "SELECT COUNT(*) FROM environment_materials WHERE campaign_id='fresh'"
            ).fetchone()[0]
            fresh_features = dict(db.execute(
                "SELECT feature_id,feature_version FROM we42_schema_features"
            ).fetchall())
        capability_count = len(fresh.list_capabilities("fresh"))

        old_path = td_path / "schema13.sqlite3"
        with closing(sqlite3.connect(old_path)) as db, db:
            db.execute(
                """CREATE TABLE campaigns(
                    id TEXT PRIMARY KEY,name TEXT NOT NULL,world_time TEXT NOT NULL,weather TEXT NOT NULL DEFAULT 'clear',
                    revision INTEGER NOT NULL DEFAULT 0,settings_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"""
            )
            db.execute(
                "INSERT INTO campaigns VALUES('old','Preserved campaign','1492-01-01T08:00:00+00:00','clear',7,'{}','now','now')"
            )
            db.execute("PRAGMA user_version=13")
            db.commit()
        migrated = WorldEngine(old_path)
        preserved = migrated.get_campaign("old")
        with closing(sqlite3.connect(old_path)) as db, db:
            migrated_version = db.execute("PRAGMA user_version").fetchone()[0]
            migrated_integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            migrated_fk = db.execute("PRAGMA foreign_key_check").fetchall()
            migrated_tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            migrated_features = dict(db.execute(
                "SELECT feature_id,feature_version FROM we42_schema_features"
            ).fetchall())

    result = {
        "release": RELEASE,
        "fresh": {
            "user_version": fresh_version,
            "integrity_check": fresh_integrity,
            "foreign_key_violations": len(fresh_fk),
            "narrative_tables": sorted(expected_tables.intersection(tables)),
            "narrative_table_count": len(expected_tables.intersection(tables)),
            "compiler_tables_present": compiler_tables,
            "environment_tables": sorted(environment_tables),
            "mechanism_tables": sorted(mechanism_tables),
            "economy_tables": sorted(economy_tables),
            "population_tables": sorted(population_tables),
            "environment_material_count": material_count,
            "capability_manifest_count": capability_count,
            "feature_manifest": fresh_features,
        },
        "migration_13_to_current": {
            "user_version": migrated_version,
            "integrity_check": migrated_integrity,
            "foreign_key_violations": len(migrated_fk),
            "campaign_name": preserved["name"],
            "campaign_revision": preserved["revision"],
            "narrative_table_count": len(expected_tables.intersection(migrated_tables)),
            "mechanism_tables": sorted(EXPECTED_MECHANISM_TABLES.intersection(migrated_tables)),
            "economy_tables": sorted(EXPECTED_ECONOMY_TABLES.intersection(migrated_tables)),
            "population_tables": sorted(EXPECTED_POPULATION_TABLES.intersection(migrated_tables)),
            "feature_manifest": migrated_features,
        },
    }
    result["passed"] = all([
        fresh_version == EXPECTED_SCHEMA,
        fresh_integrity == "ok",
        not fresh_fk,
        len(expected_tables) == EXPECTED_NARRATIVE_TABLES,
        len(expected_tables.intersection(tables)) == EXPECTED_NARRATIVE_TABLES,
        compiler_tables,
        environment_tables == EXPECTED_ENVIRONMENT_TABLES,
        mechanism_tables == EXPECTED_MECHANISM_TABLES,
        economy_tables == EXPECTED_ECONOMY_TABLES,
        population_tables == EXPECTED_POPULATION_TABLES,
        material_count == 11,
        capability_count == EXPECTED_CAPABILITIES,
        all(fresh_features.get(key) == value for key, value in EXPECTED_FEATURES.items()),
        migrated_version == EXPECTED_SCHEMA,
        migrated_integrity == "ok",
        not migrated_fk,
        preserved["name"] == "Preserved campaign",
        preserved["revision"] == 7,
        len(expected_tables.intersection(migrated_tables)) == EXPECTED_NARRATIVE_TABLES,
        EXPECTED_MECHANISM_TABLES.issubset(migrated_tables),
        EXPECTED_ECONOMY_TABLES.issubset(migrated_tables),
        EXPECTED_POPULATION_TABLES.issubset(migrated_tables),
        all(migrated_features.get(key) == value for key, value in EXPECTED_FEATURES.items()),
    ])
    return result


def http_audit() -> dict[str, Any]:
    old_db = os.environ.get("WORLD_ENGINE_DB")
    old_key = os.environ.get("WORLD_ENGINE_API_KEY")
    old_admin = os.environ.get("WORLD_ENGINE_ADMIN_KEY")
    with tempfile.TemporaryDirectory() as td:
        os.environ["WORLD_ENGINE_DB"] = str(Path(td) / "http.sqlite3")
        os.environ["WORLD_ENGINE_API_KEY"] = "v470-release-verification-secret-0123456789"
        os.environ["WORLD_ENGINE_ADMIN_KEY"] = "v470-operator-verification-secret-9876543210"
        if "app" in sys.modules:
            del sys.modules["app"]
        api = importlib.import_module("app")
        client = TestClient(api.app)
        headers = {"Authorization": "Bearer v470-release-verification-secret-0123456789"}
        operator_headers = {
            **headers,
            "X-World-Engine-Operator-Key": "v470-operator-verification-secret-9876543210",
        }
        checks: dict[str, bool] = {}
        evidence: dict[str, Any] = {}
        try:
            health = client.get("/health")
            checks["health_200"] = health.status_code == 200
            evidence["health"] = health.json() if health.status_code == 200 else health.text
            unauthorized = client.get("/api/context")
            checks["auth_fail_closed"] = unauthorized.status_code == 401

            campaign = client.post("/api/campaign", headers=operator_headers, json={"campaign_id": "audit", "name": "HTTP audit"})
            checks["campaign_bootstrap"] = campaign.status_code == 200
            checks["new_campaign_narrative_default_off"] = api.engine.get_narrative_config("audit")["mode"] == "off"
            for endpoint, payload in [
                ("/api/setup/location", {"campaign_id": "audit", "location_id": "inn", "name": "Inn", "region": "coast"}),
                ("/api/setup/character", {"campaign_id": "audit", "character_id": "hero", "name": "Hero", "location": "inn", "level": 1, "hp": 10, "max_hp": 10, "ac": 12}),
                ("/api/setup/npc", {"campaign_id": "audit", "npc_id": "mara", "name": "Mara", "location": "inn", "hp": 6, "max_hp": 6, "ac": 11, "importance": "major"}),
            ]:
                response = client.post(endpoint, headers=operator_headers, json=payload)
                if response.status_code != 200:
                    raise RuntimeError(f"{endpoint}: {response.status_code} {response.text}")

            def turn(key: str, override: str) -> dict[str, Any]:
                response = client.post("/api/turn", headers=headers, json={
                    "campaign_id": "audit",
                    "actor_kind": "character",
                    "actor_id": "hero",
                    "idempotency_key": key,
                    "player_text": "Ask Mara about the eastern road.",
                    "intents": [{"type": "interact", "parameters": {"npc_id": "mara", "topic": "eastern road"}}],
                    "narrative_mode_override": override,
                    "narrative_hint": {"speaker_id": "mara", "speech_act": "warn", "subtext": "She is protecting a witness."},
                })
                if response.status_code != 200:
                    raise RuntimeError(f"turn {override}: {response.status_code} {response.text}")
                return response.json()

            shadow = turn("http-shadow", "shadow")
            compare = turn("http-compare", "compare")
            enforce = turn("http-enforce", "enforce")
            shadow_packet = shadow.get("_narrative_shadow", {})
            checks["shadow_packet"] = shadow_packet.get("packet_version") == "NRP-1.2"
            checks["shadow_packet_hash"] = len(str(shadow_packet.get("packet_hash") or "")) == 64
            checks["compare_packet"] = compare.get("_narrative_compare", {}).get("candidate_packet", {}).get("mode") == "compare"
            checks["enforce_packet"] = enforce.get("_narrative_render_packet", {}).get("mode") == "enforce"
            checks["engine_receipt_current"] = shadow.get("_engine_receipt", {}).get("engine_version") == RELEASE
            checks["schema_receipt_current"] = shadow.get("_engine_receipt", {}).get("schema_version") == EXPECTED_SCHEMA
            packet_id = shadow["_narrative_shadow"]["packet_id"]

            cutscene_packet = {
                "cutscene_id": "http-audit",
                "scene_goal": "Warn the player without forcing a response.",
                "location": "inn",
                "participants": ["character:hero", "npc:mara"],
                "beats": ["Mara draws attention to the silent road."],
                "choices": ["Ask for details", "Inspect the map"],
            }
            cutscene_response = client.post("/api/turn", headers=headers, json={
                "campaign_id": "audit",
                "idempotency_key": "http-cutscene",
                "narrative_mode_override": "off",
                "intents": [{"type": "narrative", "parameters": {
                    "operation": "validate_cutscene",
                    "payload": {"cutscene_packet": cutscene_packet},
                }}],
            })
            cutscene_error = cutscene_response.json().get("detail") if cutscene_response.status_code == 403 else None
            checks["private_cutscene_blocked_publicly"] = cutscene_error == "PUBLIC_TURN_CAPABILITY_NOT_ALLOWED"
            cutscene_result = api.engine.narrative_dispatch(
                "validate_cutscene", "audit", {"cutscene_packet": cutscene_packet},
            )
            checks["cutscene_packet_internal"] = (
                cutscene_result.get("cutscene_version") == "CUT-1.0"
                and cutscene_result.get("hidden_structure") is True
            )

            output_text = "Rain ticks against the shutters while Mara keeps one hand on the folded map. The empty harness hook beside the stable door rocks once in the draft. ‘The eastern road is open,’ she says, ‘but two wagons missed the dusk bell, and no driver returned before dawn. Bring back tracks, names, or survivors before you trust the tavern talk. Until then, keep the south gate in sight and do not travel alone.’"
            quality_response = client.post("/api/turn", headers=headers, json={
                "campaign_id": "audit",
                "idempotency_key": "http-quality",
                "narrative_mode_override": "off",
                "intents": [{"type": "narrative", "parameters": {
                    "operation": "quality_check",
                    "payload": {
                        "packet_id": packet_id,
                        "record": False,
                        "output_text": output_text,
                    },
                }}],
            })
            quality_error = quality_response.json().get("detail") if quality_response.status_code == 403 else None
            checks["private_quality_blocked_publicly"] = quality_error == "PUBLIC_TURN_CAPABILITY_NOT_ALLOWED"
            quality_result = api.engine.check_narrative_quality(
                "audit", output_text, packet_id=packet_id, record=False,
            )
            checks["quality_receipt_internal"] = quality_result.get("receipt_version") == "NQR-1.2" and quality_result.get("hard_pass") is True

            evidence.update({
                "shadow": {"mode": shadow.get("_narrative_shadow", {}).get("mode"), "packet_id": packet_id},
                "compare": {"mode": compare.get("_narrative_compare", {}).get("candidate_packet", {}).get("mode")},
                "enforce": {"mode": enforce.get("_narrative_render_packet", {}).get("mode")},
                "cutscene": {
                    "public_status": cutscene_response.status_code,
                    "public_error": cutscene_error,
                    "internal_version": cutscene_result.get("cutscene_version"),
                    "hidden_structure": cutscene_result.get("hidden_structure"),
                },
                "quality": {
                    "public_status": quality_response.status_code,
                    "public_error": quality_error,
                    "internal_receipt_version": quality_result.get("receipt_version"),
                    "hard_pass": quality_result.get("hard_pass"),
                    "hard_failures": quality_result.get("hard_failures"),
                },
            })
        finally:
            client.close()
            if old_db is None:
                os.environ.pop("WORLD_ENGINE_DB", None)
            else:
                os.environ["WORLD_ENGINE_DB"] = old_db
            if old_key is None:
                os.environ.pop("WORLD_ENGINE_API_KEY", None)
            else:
                os.environ["WORLD_ENGINE_API_KEY"] = old_key
            if old_admin is None:
                os.environ.pop("WORLD_ENGINE_ADMIN_KEY", None)
            else:
                os.environ["WORLD_ENGINE_ADMIN_KEY"] = old_admin
    return {"release": RELEASE, "checks": checks, "evidence": evidence, "passed": all(checks.values())}


def source_audit() -> dict[str, Any]:
    import hashlib
    source = ROOT / "legacy" / "World_Engine_1.63.txt"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    instructions_path = ROOT / "CUSTOM_GPT_INSTRUCTIONS_V470.txt"
    instructions = instructions_path.read_text(encoding="utf-8")
    instruction_mirror = (ROOT / "GPT_INSTRUCTIONS.md").read_text(encoding="utf-8")
    instruction_bytes = len(instructions.encode("utf-8"))
    mcp_source = (ROOT / "mcp_server.py").read_text(encoding="utf-8")
    required_instruction_markers = {
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
        "WEGEN-1.2",
        "economy",
        "population",
        "exactly five operations",
        "local `inspect`, `ignite`, `extinguish`, and `douse`",
        "standalone local desktop app",
    }
    missing_instruction_markers = sorted(
        marker for marker in required_instruction_markers if marker not in instructions
    )
    return {
        "release": RELEASE,
        "legacy_source": str(source.relative_to(ROOT)),
        "legacy_source_sha256": digest,
        "expected_sha256": EXPECTED_163_SHA256,
        "active_instruction_file": str(instructions_path.relative_to(ROOT)),
        "active_instruction_sha256": hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
        "active_instruction_bytes": instruction_bytes,
        "active_instruction_limit": 8000,
        "missing_active_instruction_markers": missing_instruction_markers,
        "active_mirror_exact": instructions == instruction_mirror,
        "mcp_trusted_local_only": (
            "ip_address(address).is_loopback" in mcp_source
            and "MCP_HOST must be a loopback address" in mcp_source
            and "MCP_ALLOWED_HOSTS" not in mcp_source
        ),
        "passed": (
            digest == EXPECTED_163_SHA256
            and instruction_bytes <= 8000
            and not missing_instruction_markers
            and instructions == instruction_mirror
            and "ip_address(address).is_loopback" in mcp_source
            and "MCP_HOST must be a loopback address" in mcp_source
            and "MCP_ALLOWED_HOSTS" not in mcp_source
        ),
    }


def feature_audit() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        engine = WorldEngine(Path(td) / "v470.sqlite3")
        engine.ensure_campaign("feature", "V4.7 feature audit")
        revision = engine.get_campaign("feature")["revision"]
        staged = engine.stage_generated_world(
            "feature",
            "release_feature",
            "release-seed",
            {
                "location_count": 4,
                "faction_count": 2,
                "npcs_per_faction": 1,
                "resource_count": 2,
                "quest_count": 1,
            },
            expected_revision=revision,
        )
        authoring_schema = json.loads(
            (ROOT / "AUTHORING_PAYLOAD_SCHEMA.json").read_text(encoding="utf-8")
        )
        generated_sections = set(staged["generation"]["payload"])
        validation = engine.author_validate("feature", "release_feature")
        dry_run = engine.author_dry_run("feature", "release_feature", days=1)
        promoted = engine.author_promote("feature", "release_feature")
        engine.mechanism_dispatch("save", "feature", {"operator": {"id": "release.noop"}})
        mechanism_result = engine.mechanism_dispatch(
            "execute",
            "feature",
            {"operator_id": "release.noop", "idempotency_key": "release-mechanism"},
        )
        projection = DesktopProjectionKernel(engine, "feature").snapshot()
        location_id = projection["world_map"]["locations"][0]["id"]
        engine.environment_dispatch("apply_effect", "feature", {
            "effect_type": "darkness",
            "target": {"type": "location", "id": location_id},
            "intensity": 0.25,
        })
        environment = engine.environment_dispatch("snapshot", "feature", {"location_id": location_id})
        encoded_projection = json.dumps(projection, sort_keys=True)
        assets = {
            name: (ROOT / "companion_ui" / name).is_file()
            for name in ("index.html", "app.css", "app.js")
        }
        feature_rows = {}
        climate_count = 0
        mechanism_count = 0
        mechanism_receipt_count = 0
        market_count = 0
        cohort_count = 0
        with engine._db() as db:
            for row in db.execute(
                "SELECT feature_id,feature_version FROM we42_schema_features"
            ).fetchall():
                feature_rows[str(row["feature_id"])] = str(row["feature_version"])
            climate_count = db.execute(
                "SELECT COUNT(*) FROM regional_climate WHERE campaign_id='feature'"
            ).fetchone()[0]
            mechanism_count = db.execute(
                "SELECT COUNT(*) FROM mechanism_operators WHERE campaign_id='feature'"
            ).fetchone()[0]
            mechanism_receipt_count = db.execute(
                "SELECT COUNT(*) FROM mechanism_execution_receipts WHERE campaign_id='feature'"
            ).fetchone()[0]
            market_count = db.execute(
                "SELECT COUNT(*) FROM economy_markets WHERE campaign_id='feature'"
            ).fetchone()[0]
            cohort_count = db.execute(
                "SELECT COUNT(*) FROM population_cohorts WHERE campaign_id='feature'"
            ).fetchone()[0]
        checks = {
            "generation_contract": staged["generation"]["contract_version"] == GENERATION_CONTRACT_VERSION == "WEGEN-1.2",
            "generation_backward_validation": SUPPORTED_GENERATION_CONTRACTS == {"WEGEN-1.0", "WEGEN-1.1", "WEGEN-1.2"},
            "authoring_schema_covers_generation": generated_sections.issubset(
                authoring_schema.get("properties", {})
            ),
            "generation_stage_only": staged["batch"]["status"] == "staged",
            "generation_valid": bool(validation["valid"]),
            "generation_dry_run_passed": bool(dry_run["passed"]),
            "generation_promoted": promoted["status"] == "promoted",
            "generation_climates_promoted": climate_count == 4,
            "generation_markets_promoted": market_count == 4,
            "generation_population_promoted": cohort_count > 0,
            "mechanism_execution": (
                mechanism_count == 1
                and mechanism_receipt_count == 1
                and bool(mechanism_result["executed"])
            ),
            "environment_runtime": any(
                row["effect_type"] == "darkness" and row["active"]
                for row in environment["effects"]
            ),
            "pbem_integration": PBEM_INTEGRATION_VERSION == "WE4.7-PBEM-2.2",
            "desktop_projection": projection["schema"] == "WE-DESKTOP-1.1",
            "desktop_has_player": projection["player"] is not None,
            "desktop_has_public_map": len(projection["world_map"]["locations"]) == 4,
            "desktop_has_public_economy": "economy" in projection,
            "desktop_has_public_population": "population" in projection,
            "desktop_excludes_raw_events": '"events"' not in encoded_projection,
            "desktop_assets": all(assets.values()),
            "feature_manifest": all(feature_rows.get(key) == value for key, value in EXPECTED_FEATURES.items()),
            "capability_manifest": len(DEFAULT_CAPABILITIES) == EXPECTED_CAPABILITIES,
            "least_privilege_action_surface": EXPECTED_ACTIONS == len(PUBLIC_ACTION_OPERATION_IDS) == 5,
        }
    return {
        "release": RELEASE,
        "checks": checks,
        "assets": assets,
        "feature_manifest": feature_rows,
        "passed": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate World Engine 4.7.0 release audits.")
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = {
        "openapi": openapi_audit(),
        "sqlite": sqlite_audit(),
        "http": http_audit(),
        "source": source_audit(),
        "features": feature_audit(),
    }
    _write(args.output_dir / "WORLD_ENGINE_V470_OPENAPI_AUDIT.json", results["openapi"])
    _write(args.output_dir / "WORLD_ENGINE_V470_SQLITE_AUDIT.json", results["sqlite"])
    _write(args.output_dir / "WORLD_ENGINE_V470_HTTP_CHECK.json", results["http"])
    _write(args.output_dir / "WORLD_ENGINE_V470_SOURCE_AUDIT.json", results["source"])
    _write(args.output_dir / "WORLD_ENGINE_V470_FEATURE_AUDIT.json", results["features"])
    summary = {
        "release": RELEASE,
        "openapi_passed": results["openapi"]["passed"],
        "sqlite_passed": results["sqlite"]["passed"],
        "http_passed": results["http"]["passed"],
        "source_passed": results["source"]["passed"],
        "features_passed": results["features"]["passed"],
        "passed": all(result["passed"] for result in results.values()),
    }
    _write(args.output_dir / "WORLD_ENGINE_V470_RELEASE_AUDIT.json", summary)
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
