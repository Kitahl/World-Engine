from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from world_engine import WorldEngine
from world_engine.desktop import (
    DESKTOP_PROJECTION_VERSION,
    DesktopProjectionKernel,
    desktop_projection,
)
from world_engine.politics import PoliticsKernel
from world_engine_companion import ASSET_ROOT, AssetHandler, CompanionApi

ROOT = Path(__file__).resolve().parents[1]


class DesktopProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "desktop.sqlite3"
        self.engine = WorldEngine(self.db)
        self.engine.ensure_campaign("c", "Desktop Test")

    def tearDown(self):
        self.temp.cleanup()

    def test_compatibility_projection_excludes_unlisted_fields(self):
        value = desktop_projection(
            {
                "campaign_id": "c",
                "presentation": {
                    "narration": "exact",
                    "choices": ["Go"],
                    "secret": "NO",
                },
                "api_key": "NO",
            }
        )
        self.assertEqual("exact", value["presentation"]["narration"])
        self.assertNotIn("secret", json.dumps(value))
        self.assertNotIn("api_key", json.dumps(value))

    def test_complete_projection_is_closed_and_hides_private_rows(self):
        self.engine.upsert_location(
            "c",
            "known",
            "Known Vale",
            description="Player-facing place",
            x=1,
            y=2,
            tags=["public_map"],
        )
        self.engine.upsert_location(
            "c",
            "hidden",
            "SECRET CITADEL",
            description="HIDDEN DESCRIPTION",
            x=9,
            y=9,
            tags=["gm_only"],
        )
        self.engine.upsert_character(
            "c",
            "hero",
            "Hero",
            location="known",
            hp=9,
            max_hp=12,
            inventory=[{"id": "rope", "name": "Rope", "qty": 1, "gm_secret": "NO"}],
            notes={"secret_backstory": "NO"},
        )
        self.engine.upsert_npc(
            "c",
            "spy",
            "Visible Person",
            location="known",
            beliefs=["NPC_BELIEF_SECRET"],
            goals=["NPC_GOAL_SECRET"],
            memory=[{"secret": "NPC_MEMORY_SECRET"}],
        )
        self.engine.commit_event("c", "gm_note", "RAW_EVENT_SECRET")
        snapshot = DesktopProjectionKernel(self.engine, "c", "hero").snapshot()
        encoded = json.dumps(snapshot, sort_keys=True)
        self.assertEqual(DESKTOP_PROJECTION_VERSION, snapshot["schema"])
        self.assertEqual("Hero", snapshot["player"]["name"])
        self.assertEqual(
            ["known"], [row["id"] for row in snapshot["world_map"]["locations"]]
        )
        self.assertEqual("Rope", snapshot["inventory"][0]["name"])
        for forbidden in (
            "SECRET CITADEL",
            "HIDDEN DESCRIPTION",
            "NPC_BELIEF_SECRET",
            "NPC_GOAL_SECRET",
            "NPC_MEMORY_SECRET",
            "RAW_EVENT_SECRET",
            "secret_backstory",
            "gm_secret",
            "events",
            "beliefs",
            "memory",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_projection_includes_only_public_market_and_local_population(self):
        self.engine.upsert_location(
            "c", "known", "Known Vale", x=1, y=2, tags=["public_map"]
        )
        self.engine.upsert_character(
            "c", "hero", "Hero", location="known", hp=9, max_hp=12
        )
        self.engine.save_item_def("c", "bread", "Bread", base_price=2)
        self.engine.set_inventory_item("c", "character", "hero", "bread", 2)
        with self.engine._write_db() as db:
            db.execute(
                "INSERT INTO owner_balances(campaign_id,owner_kind,owner_id,currency_key,amount,updated_at) VALUES('c','character','hero','gp',7,?)",
                (self.engine._now(),),
            )
        self.engine.economy_dispatch(
            "save_market",
            "c",
            {
                "market_id": "public_shop",
                "location_id": "known",
                "name": "Public Shop",
                "visibility": "public",
            },
        )
        self.engine.economy_dispatch(
            "set_market_item",
            "c",
            {
                "market_id": "public_shop",
                "item_id": "bread",
                "target_stock": 10,
            },
        )
        self.engine.economy_dispatch(
            "save_market",
            "c",
            {
                "market_id": "secret_shop",
                "location_id": "known",
                "name": "SECRET MARKET",
                "visibility": "private",
            },
        )
        self.engine.world_systems_dispatch(
            "set_population",
            "c",
            {"location_id": "known", "population": 25, "food_capacity": 30},
        )
        snapshot = DesktopProjectionKernel(self.engine, "c", "hero").snapshot()
        encoded = json.dumps(snapshot, sort_keys=True)
        self.assertEqual("WE-DESKTOP-5.1.0", snapshot["schema"])
        self.assertEqual("bread", snapshot["inventory"][0]["item_id"])
        self.assertEqual([{"currency_key": "gp", "amount": 7.0}], snapshot["balances"])
        self.assertEqual(25.0, snapshot["population"]["settlement"]["population"])
        self.assertIn("Public Shop", encoded)
        self.assertNotIn("SECRET MARKET", encoded)

    def test_v500_public_surfaces_have_secondary_allowlists_and_scope(self):
        self.engine.upsert_location(
            "c", "known", "Known Vale", x=1, y=2, tags=["public_map"]
        )
        self.engine.upsert_character(
            "c", "hero", "Hero", location="known", hp=9, max_hp=12
        )
        self.engine.upsert_quest(
            "c",
            "public_quest",
            "Public Quest",
            owner_id="hero",
            objectives=[{"text": "Reach the gate", "secret": "QUEST_OBJECTIVE_SECRET"}],
        )
        self.engine.upsert_quest(
            "c",
            "private_quest",
            "PRIVATE QUEST TITLE",
            owner_id="hero",
            objectives=[{"text": "PRIVATE QUEST OBJECTIVE"}],
        )
        with self.engine._write_db() as db:
            PoliticsKernel(self.engine).install_schema_db(db)

        def quest_projection(*args):
            quest_id = args[-1]
            if quest_id == "private_quest":
                return {
                    "id": quest_id,
                    "status": "active",
                    "visibility": "private",
                    "redacted": True,
                    "private": "PRIVATE QUEST ROW",
                }
            return {
                "id": quest_id,
                "title": "Public Quest",
                "status": "active",
                "owner_id": "hero",
                "region": "vale",
                "objectives": [
                    {"text": "Reach the gate", "secret": "QUEST_OBJECTIVE_SECRET"}
                ],
                "visibility": "public",
                "nodes": [
                    {
                        "id": "gate",
                        "node_type": "objective",
                        "status": "active",
                        "deadline_world_time": None,
                        "trigger": "QUEST_TRIGGER_SECRET",
                    }
                ],
                "edges": [],
                "redacted": False,
                "raw_state": "QUEST_STATE_SECRET",
            }

        with (
            mock.patch(
                "world_engine.desktop.QuestRuntimeKernel.public_projection_db",
                side_effect=quest_projection,
            ),
            mock.patch(
                "world_engine.desktop.IncidentKernel.public_snapshot_db",
                return_value={
                    "incidents": [
                        {
                            "id": "inc-public",
                            "definition_id": "public.storm",
                            "category": "weather",
                            "scope_type": "location",
                            "scope_id": "known",
                            "status": "active",
                            "selected_world_time": "1492-01-01T00:00:00+00:00",
                            "payload": "INCIDENT_PAYLOAD_SECRET",
                        }
                    ],
                    "private_incidents": "PRIVATE INCIDENT ROW",
                },
            ),
            mock.patch(
                "world_engine.desktop.AgencyKernel.public_snapshot_db",
                return_value={
                    "contract_version": "WE-AGENCY-1.0",
                    "actor": {"kind": "character", "id": "hero", "location": "known"},
                    "available_affordances": [
                        {
                            "id": "inspect-gate",
                            "operator_id": "inspect.gate",
                            "location_id": "known",
                            "bindings": "AGENCY_BINDING_SECRET",
                        }
                    ],
                    "goals": "AGENCY_GOAL_SECRET",
                    "memories": "AGENCY_MEMORY_SECRET",
                },
            ),
            mock.patch(
                "world_engine.desktop.PoliticsKernel.public_snapshot_db",
                return_value={
                    "wars": [
                        {
                            "id": "war-public",
                            "attacker_faction_id": "north",
                            "defender_faction_id": "south",
                            "status": "active",
                            "goals": "POLITICS_WAR_GOAL_SECRET",
                        }
                    ],
                    "treaties": [],
                    "proposals": [],
                    "claims": [],
                    "grievances": [],
                    "projects": [],
                    "territorial_control": [],
                    "beliefs": "POLITICS_BELIEF_SECRET",
                    "commitments": "POLITICS_COMMITMENT_SECRET",
                },
            ),
        ):
            snapshot = DesktopProjectionKernel(self.engine, "c", "hero").snapshot()

        encoded = json.dumps(snapshot, sort_keys=True)
        self.assertEqual(
            ["inc-public"], [row["id"] for row in snapshot["journal"]["incidents"]]
        )
        self.assertEqual(
            ["inspect-gate"],
            [row["id"] for row in snapshot["agency"]["available_affordances"]],
        )
        self.assertEqual(
            ["war-public"], [row["id"] for row in snapshot["politics"]["wars"]]
        )
        self.assertEqual(
            ["public_quest"], [row["id"] for row in snapshot["executable_quests"]]
        )
        self.assertEqual(
            ["gate"], [row["id"] for row in snapshot["executable_quests"][0]["nodes"]]
        )
        for forbidden in (
            "PRIVATE QUEST TITLE",
            "PRIVATE QUEST OBJECTIVE",
            "PRIVATE QUEST ROW",
            "QUEST_OBJECTIVE_SECRET",
            "QUEST_TRIGGER_SECRET",
            "QUEST_STATE_SECRET",
            "INCIDENT_PAYLOAD_SECRET",
            "PRIVATE INCIDENT ROW",
            "AGENCY_BINDING_SECRET",
            "AGENCY_GOAL_SECRET",
            "AGENCY_MEMORY_SECRET",
            "POLITICS_WAR_GOAL_SECRET",
            "POLITICS_BELIEF_SECRET",
            "POLITICS_COMMITMENT_SECRET",
        ):
            self.assertNotIn(forbidden, encoded)


class DesktopBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "bridge.sqlite3"
        self.api = CompanionApi(self.db, "c")
        # 5.1.0: the bridge's engine/projection are private so pywebview does
        # not export them; tests reach the private attribute deliberately.
        self.api._engine.ensure_campaign("c", "Bridge")

    def tearDown(self):
        self.temp.cleanup()

    def test_bridge_is_closed_and_generation_runs_all_explicit_gates(self):
        rejected = self.api.authoring("dispatch", "batch", {})
        self.assertFalse(rejected["ok"])
        self.assertEqual("ACTION_NOT_ALLOWED", rejected["code"])
        rejected = self.api.authoring("stage", "bad batch", {})
        self.assertFalse(rejected["ok"])
        rejected = self.api.authoring(
            "stage",
            "batch",
            {
                "seed": "s",
                "namespace": "bootstrap",
                "mode": "bootstrap",
                "surprise": True,
            },
        )
        self.assertFalse(rejected["ok"])
        spec = {
            "seed": "desktop-seed",
            "namespace": "bootstrap",
            "mode": "bootstrap",
            "days": 1,
            "config": {
                "location_count": 4,
                "faction_count": 2,
                "npcs_per_faction": 1,
                "resource_count": 2,
                "quest_count": 1,
            },
        }
        staged = self.api.authoring("stage", "desktop_batch", spec)
        self.assertTrue(staged["ok"], staged)
        self.assertEqual("staged", staged["status"])
        validated = self.api.authoring("validate", "desktop_batch", spec)
        self.assertTrue(validated["ok"], validated)
        dry_run = self.api.authoring("dry_run", "desktop_batch", spec)
        self.assertTrue(dry_run["ok"], dry_run)
        promoted = self.api.authoring("promote", "desktop_batch", spec)
        self.assertTrue(promoted["ok"], promoted)
        snapshot = self.api.snapshot()
        self.assertEqual("READY", snapshot["states"]["engine"])
        self.assertIsNotNone(snapshot["player"])
        self.assertEqual(4, len(snapshot["world_map"]["locations"]))
        self.assertGreaterEqual(len(snapshot["world_map"]["links"]), 3)

    def test_token_configuration_never_returns_secret(self):
        token = "A" * 30
        with (
            mock.patch.dict(os.environ, {"WORLD_ENGINE_DATA_DIR": self.temp.name}),
            mock.patch(
                "world_engine_startup.configure_ngrok_token_once",
                return_value={
                    "status": "READY",
                    "provider": "ngrok",
                    "token_fingerprint": "fingerprint",
                    "retryable": False,
                },
            ),
            mock.patch(
                "world_engine_startup.ensure_launcher_config",
                return_value=("api-secret", False),
            ),
            mock.patch(
                "world_engine_startup.ensure_endpoint_outcome",
                return_value={
                    "status": "READY",
                    "provider": "ngrok",
                    "public_url": "https://example.ngrok.app",
                    "retryable": False,
                },
            ),
        ):
            result = self.api.configure_ngrok(token)
        self.assertTrue(result["ok"])
        self.assertEqual("fingerprint", result["token_fingerprint"])
        self.assertNotIn(token, json.dumps(result))
        self.assertNotIn("api-secret", json.dumps(result))

    def test_bridge_rejects_oversized_and_unavailable_character_inputs(self):
        with self.assertRaisesRegex(ValueError, "too large"):
            self.api._closed_spec({"seed": "x" * 17_000})
        result = self.api.select_character("../secret")
        self.assertFalse(result["ok"])
        self.assertEqual("INVALID_CHARACTER", result["code"])


class DesktopAssetTests(unittest.TestCase):
    def test_assets_are_bundled_accessible_and_have_no_remote_dependencies(self):
        html = (ASSET_ROOT / "index.html").read_text(encoding="utf-8")
        css = (ASSET_ROOT / "app.css").read_text(encoding="utf-8")
        js = (ASSET_ROOT / "app.js").read_text(encoding="utf-8")
        combined = html + css + js
        for mode in (
            "Story",
            "Dialogue",
            "Explore",
            "Combat",
            "Character",
            "World Map",
            "Investigation",
        ):
            self.assertIn(mode, combined)
        self.assertIn("World Engine 5.1.0 Companion", html)
        self.assertIn("Incident journal", js)
        self.assertIn("Available world actions", js)
        self.assertIn("Public politics", js)
        self.assertIn("Speaker identities and portraits are not inferred", js)
        self.assertEqual(1, html.count('id="stage-content"'))
        self.assertIn("Procedural world forge", html)
        self.assertIn("What is ngrok?", html)
        self.assertIn("@media", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertNotIn('<script src="http', combined)
        self.assertNotIn('<link href="http', combined)
        self.assertNotIn("@import url(http", combined)
        self.assertNotIn("fetch(", js)
        self.assertNotIn("XMLHttpRequest", js)
        self.assertNotIn("WebSocket", js)
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("eval(", js)

    def test_host_is_loopback_csp_locked_and_has_no_secret_cli(self):
        text = (ROOT / "world_engine_companion.py").read_text(encoding="utf-8")
        batch = (ROOT / "START_COMPANION_UI.bat").read_text(encoding="utf-8")
        self.assertIn('ThreadingHTTPServer(("127.0.0.1", 0)', text)
        self.assertIn("default-src 'self'", text)
        self.assertIn("connect-src 'none'", text)
        self.assertNotIn("--api-key", text + batch)
        self.assertNotIn("WORLD_ENGINE_API_KEY", text + batch)
        self.assertNotIn("/api/ui/", text)
        self.assertNotIn("world_systems_dispatch", text)
        self.assertNotIn("rules_dispatch", text)

    def test_asset_server_rejects_unknown_paths(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), AssetHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = "http://127.0.0.1:" + str(server.server_address[1])
            with urllib.request.urlopen(base + "/index.html", timeout=3) as response:
                body = response.read().decode("utf-8")
                self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])
                self.assertIn(
                    "default-src 'self'", response.headers["Content-Security-Policy"]
                )
                self.assertIn("World Engine", body)
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                urllib.request.urlopen(base + "/../world_engine.sqlite3", timeout=3)
            self.assertEqual(404, rejected.exception.code)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
