#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from world_engine import WorldEngine
from world_engine.narrative import NARRATIVE_SCHEMA
from world_engine.openapi_compat import object_schema_paths_missing_properties

RELEASE = "4.0.2"
EXPECTED_SCHEMA = 14
EXPECTED_ACTIONS = 30
EXPECTED_NARRATIVE_TABLES = 9
EXPECTED_163_SHA256 = "0748cf20e6fc870055d1d96ac329b83561c71162922bbb2220278ccb1f2feee5"


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
    }
    result["passed"] = all([
        result["info_version"] == RELEASE,
        result["operations"] == EXPECTED_ACTIONS,
        result["unique_operation_ids"] == EXPECTED_ACTIONS,
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
        with sqlite3.connect(fresh_path) as db:
            fresh_version = db.execute("PRAGMA user_version").fetchone()[0]
            fresh_integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            fresh_fk = db.execute("PRAGMA foreign_key_check").fetchall()
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        old_path = td_path / "schema13.sqlite3"
        with sqlite3.connect(old_path) as db:
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
        with sqlite3.connect(old_path) as db:
            migrated_version = db.execute("PRAGMA user_version").fetchone()[0]
            migrated_integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            migrated_fk = db.execute("PRAGMA foreign_key_check").fetchall()
            migrated_tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    result = {
        "release": RELEASE,
        "fresh": {
            "user_version": fresh_version,
            "integrity_check": fresh_integrity,
            "foreign_key_violations": len(fresh_fk),
            "narrative_tables": sorted(expected_tables.intersection(tables)),
            "narrative_table_count": len(expected_tables.intersection(tables)),
        },
        "migration_13_to_14": {
            "user_version": migrated_version,
            "integrity_check": migrated_integrity,
            "foreign_key_violations": len(migrated_fk),
            "campaign_name": preserved["name"],
            "campaign_revision": preserved["revision"],
            "narrative_table_count": len(expected_tables.intersection(migrated_tables)),
        },
    }
    result["passed"] = all([
        fresh_version == EXPECTED_SCHEMA,
        fresh_integrity == "ok",
        not fresh_fk,
        len(expected_tables) == EXPECTED_NARRATIVE_TABLES,
        len(expected_tables.intersection(tables)) == EXPECTED_NARRATIVE_TABLES,
        migrated_version == EXPECTED_SCHEMA,
        migrated_integrity == "ok",
        not migrated_fk,
        preserved["name"] == "Preserved campaign",
        preserved["revision"] == 7,
        len(expected_tables.intersection(migrated_tables)) == EXPECTED_NARRATIVE_TABLES,
    ])
    return result


def http_audit() -> dict[str, Any]:
    old_db = os.environ.get("WORLD_ENGINE_DB")
    old_key = os.environ.get("WORLD_ENGINE_API_KEY")
    with tempfile.TemporaryDirectory() as td:
        os.environ["WORLD_ENGINE_DB"] = str(Path(td) / "http.sqlite3")
        os.environ["WORLD_ENGINE_API_KEY"] = "v402-release-verification-secret-0123456789"
        if "app" in sys.modules:
            del sys.modules["app"]
        api = importlib.import_module("app")
        client = TestClient(api.app)
        headers = {"Authorization": "Bearer v402-release-verification-secret-0123456789"}
        checks: dict[str, bool] = {}
        evidence: dict[str, Any] = {}
        try:
            health = client.get("/health")
            checks["health_200"] = health.status_code == 200
            evidence["health"] = health.json() if health.status_code == 200 else health.text
            unauthorized = client.get("/api/context")
            checks["auth_fail_closed"] = unauthorized.status_code == 401

            campaign = client.post("/api/campaign", headers=headers, json={"campaign_id": "audit", "name": "HTTP audit"})
            checks["campaign_bootstrap"] = campaign.status_code == 200
            for endpoint, payload in [
                ("/api/setup/location", {"campaign_id": "audit", "location_id": "inn", "name": "Inn", "region": "coast"}),
                ("/api/setup/character", {"campaign_id": "audit", "character_id": "hero", "name": "Hero", "location": "inn", "level": 1, "hp": 10, "max_hp": 10, "ac": 12}),
                ("/api/setup/npc", {"campaign_id": "audit", "npc_id": "mara", "name": "Mara", "location": "inn", "hp": 6, "max_hp": 6, "ac": 11, "importance": "major"}),
            ]:
                response = client.post(endpoint, headers=headers, json=payload)
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
            checks["shadow_packet"] = shadow.get("_narrative_shadow", {}).get("packet_version") == "NRP-1.0"
            checks["compare_packet"] = compare.get("_narrative_compare", {}).get("candidate_packet", {}).get("mode") == "compare"
            checks["enforce_packet"] = enforce.get("_narrative_render_packet", {}).get("mode") == "enforce"
            checks["engine_receipt_v402"] = shadow.get("_engine_receipt", {}).get("engine_version") == RELEASE
            checks["schema_receipt_14"] = shadow.get("_engine_receipt", {}).get("schema_version") == EXPECTED_SCHEMA
            packet_id = shadow["_narrative_shadow"]["packet_id"]

            cutscene_response = client.post("/api/turn", headers=headers, json={
                "campaign_id": "audit",
                "idempotency_key": "http-cutscene",
                "narrative_mode_override": "off",
                "intents": [{"type": "narrative", "parameters": {
                    "operation": "validate_cutscene",
                    "payload": {"cutscene_packet": {
                        "cutscene_id": "http-audit",
                        "scene_goal": "Warn the player without forcing a response.",
                        "location": "inn",
                        "participants": ["character:hero", "npc:mara"],
                        "beats": ["Mara draws attention to the silent road."],
                        "choices": ["Ask for details", "Inspect the map"],
                    }},
                }}],
            })
            if cutscene_response.status_code != 200:
                raise RuntimeError(f"cutscene: {cutscene_response.status_code} {cutscene_response.text}")
            cutscene_body = cutscene_response.json()
            cutscene_result = cutscene_body["steps"][0]["result"]
            checks["cutscene_packet"] = cutscene_result.get("cutscene_version") == "CUT-1.0" and cutscene_result.get("hidden_structure") is True

            quality_response = client.post("/api/turn", headers=headers, json={
                "campaign_id": "audit",
                "idempotency_key": "http-quality",
                "narrative_mode_override": "off",
                "intents": [{"type": "narrative", "parameters": {
                    "operation": "quality_check",
                    "payload": {
                        "packet_id": packet_id,
                        "record": False,
                        "output_text": "Rain ticks against the shutters while Mara keeps one hand on the folded map. The empty harness hook beside the stable door rocks once in the draft. ‘The eastern road is open,’ she says, ‘but two wagons missed the dusk bell, and no driver returned before dawn. Bring back tracks, names, or survivors before you trust the tavern talk. Until then, keep the south gate in sight and do not travel alone.’",
                    },
                }}],
            })
            if quality_response.status_code != 200:
                raise RuntimeError(f"quality: {quality_response.status_code} {quality_response.text}")
            quality_result = quality_response.json()["steps"][0]["result"]
            checks["quality_receipt"] = quality_result.get("receipt_version") == "NQR-1.0" and quality_result.get("hard_pass") is True

            evidence.update({
                "shadow": {"mode": shadow.get("_narrative_shadow", {}).get("mode"), "packet_id": packet_id},
                "compare": {"mode": compare.get("_narrative_compare", {}).get("candidate_packet", {}).get("mode")},
                "enforce": {"mode": enforce.get("_narrative_render_packet", {}).get("mode")},
                "cutscene": {"version": cutscene_result.get("cutscene_version"), "hidden_structure": cutscene_result.get("hidden_structure")},
                "quality": {"receipt_version": quality_result.get("receipt_version"), "hard_pass": quality_result.get("hard_pass"), "hard_failures": quality_result.get("hard_failures")},
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
    return {"release": RELEASE, "checks": checks, "evidence": evidence, "passed": all(checks.values())}


def source_audit() -> dict[str, Any]:
    import hashlib
    source = ROOT / "legacy" / "World_Engine_1.63.txt"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    instruction_bytes = (ROOT / "CUSTOM_GPT_INSTRUCTIONS_V402.txt").stat().st_size
    return {
        "release": RELEASE,
        "legacy_source": str(source.relative_to(ROOT)),
        "legacy_source_sha256": digest,
        "expected_sha256": EXPECTED_163_SHA256,
        "active_instruction_bytes": instruction_bytes,
        "active_instruction_limit": 8000,
        "passed": digest == EXPECTED_163_SHA256 and instruction_bytes <= 8000,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate World Engine 4.0.2 release audits.")
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = {
        "openapi": openapi_audit(),
        "sqlite": sqlite_audit(),
        "http": http_audit(),
        "source": source_audit(),
    }
    _write(args.output_dir / "WORLD_ENGINE_V402_OPENAPI_AUDIT.json", results["openapi"])
    _write(args.output_dir / "WORLD_ENGINE_V402_SQLITE_AUDIT.json", results["sqlite"])
    _write(args.output_dir / "WORLD_ENGINE_V402_HTTP_CHECK.json", results["http"])
    _write(args.output_dir / "WORLD_ENGINE_V402_SOURCE_AUDIT.json", results["source"])
    summary = {
        "release": RELEASE,
        "openapi_passed": results["openapi"]["passed"],
        "sqlite_passed": results["sqlite"]["passed"],
        "http_passed": results["http"]["passed"],
        "source_passed": results["source"]["passed"],
        "passed": all(result["passed"] for result in results.values()),
    }
    _write(args.output_dir / "WORLD_ENGINE_V402_RELEASE_AUDIT.json", summary)
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
