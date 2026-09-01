from __future__ import annotations

import json
import os
import sqlite3
import sys
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
from world_engine_companion import main as companion_main

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
            resources={
                "spell_slots_1": 2,
                "focus": {"current": 1, "max": 3, "private": "PLAYER_RESOURCE_SECRET"},
                "opaque": {"gm_note": "RESOURCE_CONTAINER_SECRET"},
            },
            inventory=[{"id": "rope", "name": "Rope", "qty": 1, "gm_secret": "NO"}],
            notes={"secret_backstory": "NO"},
        )
        with self.engine._write_db() as db:
            db.execute(
                "UPDATE characters SET conditions_json=? WHERE campaign_id='c' AND id='hero'",
                (self.engine._dumps(["poisoned", {"private": "CONDITION_SECRET"}]),),
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
        self.engine.upsert_npc(
            "c",
            "hidden_spy",
            "HIDDEN RELATIONSHIP NPC",
            location="hidden",
        )
        self.engine.adjust_relationship("c", "spy", "hero", trust_delta=30)
        self.engine.adjust_relationship("c", "hidden_spy", "hero", trust_delta=80)
        self.engine.commit_event("c", "gm_note", "RAW_EVENT_SECRET")
        snapshot = DesktopProjectionKernel(self.engine, "c", "hero").snapshot()
        encoded = json.dumps(snapshot, sort_keys=True)
        self.assertEqual(DESKTOP_PROJECTION_VERSION, snapshot["schema"])
        self.assertEqual("Hero", snapshot["player"]["name"])
        self.assertEqual(
            ["known"], [row["id"] for row in snapshot["world_map"]["locations"]]
        )
        self.assertEqual("Rope", snapshot["inventory"][0]["name"])
        self.assertEqual(["poisoned"], snapshot["player"]["conditions"])
        self.assertEqual(
            {
                "spell_slots_1": 2,
                "focus": {"current": 1, "max": 3},
            },
            snapshot["player"]["resources"],
        )
        self.assertEqual(
            [("spy", "hero")],
            [
                (row["source_id"], row["target_id"])
                for row in snapshot["known_relationships"]
            ],
        )
        for forbidden in (
            "SECRET CITADEL",
            "HIDDEN DESCRIPTION",
            "NPC_BELIEF_SECRET",
            "NPC_GOAL_SECRET",
            "NPC_MEMORY_SECRET",
            "RAW_EVENT_SECRET",
            "secret_backstory",
            "gm_secret",
            "hidden_spy",
            "HIDDEN RELATIONSHIP NPC",
            "PLAYER_RESOURCE_SECRET",
            "RESOURCE_CONTAINER_SECRET",
            "CONDITION_SECRET",
            "events",
            "beliefs",
            "memory",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_snapshot_uses_one_connection_and_no_detached_public_reads(self):
        self.engine.set_simulation_seed("c", 123456789)
        original_db = self.engine._db
        connection_count = 0

        def counted_db():
            nonlocal connection_count
            connection_count += 1
            return original_db()

        with (
            mock.patch.object(self.engine, "_db", side_effect=counted_db),
            mock.patch.object(
                self.engine,
                "get_campaign",
                side_effect=AssertionError("detached campaign read"),
            ),
            mock.patch.object(
                self.engine,
                "latest_accepted_presentation",
                side_effect=AssertionError("detached presentation read"),
            ),
            mock.patch.object(
                self.engine,
                "simulation_config",
                side_effect=AssertionError("detached simulation read"),
            ),
        ):
            snapshot = DesktopProjectionKernel(self.engine, "c").snapshot()

        self.assertEqual(1, connection_count)
        self.assertEqual(123456789 & 0x7FFFFFFF, snapshot["terrain_seed"])
        self.assertEqual("Desktop Test", snapshot["campaign"]["name"])
        self.assertEqual([], snapshot["journal"]["presentations"])

    def test_presentations_are_validated_bounded_and_strictly_allowlisted(self):
        # The acceptance verifier itself has exhaustive publication tests. This
        # fixture supplies acceptance index rows so this test can probe the
        # desktop's query bound and second allowlist independently.
        connection = sqlite3.connect(self.db)
        try:
            connection.executemany(
                """INSERT INTO we43_narrative_packet_acceptances(
                       campaign_id,packet_id,attempt_id,candidate_digest,
                       accepted_output_id,receipt_id,presentation_id,outbox_id,
                       acceptance_mode,semantic_attestation_id,accepted_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        "c",
                        f"packet-{index:02d}",
                        f"attempt-{index:02d}",
                        f"digest-{index:02d}",
                        f"output-{index:02d}",
                        f"receipt-{index:02d}",
                        f"presentation-{index:02d}",
                        f"outbox-{index:02d}",
                        "deterministic",
                        None,
                        f"2026-08-30T00:00:{index:02d}+00:00",
                    )
                    for index in range(25)
                ],
            )
            connection.commit()
        finally:
            connection.close()

        def accepted_result(_db, _campaign, packet_id, _digest, *, replayed):
            self.assertTrue(replayed)
            return {
                "presentation": {
                    "campaign_id": "c",
                    "presentation_id": "pres-" + packet_id,
                    "revision": int(packet_id[-2:]),
                    "narration": "PUBLIC " + ("x" * 3_000),
                    "turn_id": "turn-" + packet_id,
                    "choices": [f"Choice {index}" for index in range(12)],
                    "private_validation_context": "CHRONICLE_SECRET",
                }
            }

        with mock.patch.object(
            self.engine,
            "_accepted_publication_result_db",
            side_effect=accepted_result,
        ) as verify:
            snapshot = DesktopProjectionKernel(self.engine, "c").snapshot()

        history = snapshot["journal"]["presentations"]
        self.assertEqual(20, len(history))
        self.assertEqual(20, verify.call_count)
        self.assertEqual("pres-packet-24", history[0]["id"])
        self.assertEqual("Turn turn-packet-24", history[0]["title"])
        self.assertIsNone(history[0]["world_time"])
        self.assertEqual(2_000, len(history[0]["narration"]))
        self.assertEqual(9, len(history[0]["choices"]))
        self.assertEqual(
            {
                "presentation_id",
                "turn_id",
                "id",
                "title",
                "revision",
                "accepted_at",
                "narration",
                "choices",
                "world_time",
            },
            set(history[0]),
        )
        self.assertNotIn("CHRONICLE_SECRET", json.dumps(snapshot, sort_keys=True))

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
                    "token_fingerprint": "a" * 12,
                    "retryable": False,
                },
            ),
            mock.patch(
                "world_engine_startup.ensure_launcher_config",
                return_value=("api-secret", False),
            ),
            mock.patch(
                "world_engine_startup.switch_to_ngrok_endpoint_outcome",
                return_value={
                    "status": "READY",
                    "provider": "ngrok_user",
                    "public_url": "https://example.ngrok.app",
                    "retryable": False,
                },
            ),
        ):
            result = self.api.configure_ngrok(token)
        self.assertTrue(result["ok"])
        self.assertEqual("a" * 12, result["token_fingerprint"])
        self.assertNotIn(token, json.dumps(result))
        self.assertNotIn("api-secret", json.dumps(result))

    def test_endpoint_results_apply_a_secondary_recursive_secret_boundary(self):
        token = "B" * 30
        with (
            mock.patch.dict(os.environ, {"WORLD_ENGINE_DATA_DIR": self.temp.name}),
            mock.patch(
                "world_engine_startup.configure_ngrok_token_once",
                return_value={
                    "status": "READY",
                    "provider": "ngrok",
                    "token_fingerprint": token,
                    "api_key": "CONFIGURED_API_SECRET",
                },
            ),
            mock.patch(
                "world_engine_startup.ensure_launcher_config",
                return_value=("LAUNCHER_API_SECRET", False),
            ),
            mock.patch(
                "world_engine_startup.switch_to_ngrok_endpoint_outcome",
                return_value={
                    "status": "READY",
                    "provider": "ngrok_user",
                    "public_url": "https://example.ngrok.app",
                    "retryable": False,
                    "api_key": "OUTCOME_API_SECRET",
                    "nested": {"secret": "NESTED_SECRET"},
                },
            ),
            mock.patch(
                "world_engine_startup.ensure_endpoint_outcome",
                return_value={
                    "status": "READY",
                    "provider": "ngrok_user",
                    "public_url": "https://example.ngrok.app",
                    "retryable": False,
                    "api_key": "OUTCOME_API_SECRET",
                    "nested": {"secret": "NESTED_SECRET"},
                },
            ),
        ):
            configured = self.api.configure_ngrok(token)
            retried = self.api.retry_endpoint()

        for result in (configured, retried):
            encoded = json.dumps(result, sort_keys=True)
            for canary in (
                token,
                "CONFIGURED_API_SECRET",
                "LAUNCHER_API_SECRET",
                "OUTCOME_API_SECRET",
                "NESTED_SECRET",
            ):
                self.assertNotIn(canary, encoded)
            self.assertNotIn("api_key", result)
            self.assertNotIn("nested", result)
        self.assertIsNone(configured["token_fingerprint"])

    def test_endpoint_operation_lock_fails_busy_without_calling_runtime(self):
        self.api._endpoint_lock.acquire()
        try:
            with mock.patch(
                "world_engine_startup.configure_ngrok_token_once"
            ) as configure:
                configured = self.api.configure_ngrok("A" * 30)
                retried = self.api.retry_endpoint()
        finally:
            self.api._endpoint_lock.release()
        self.assertEqual("ENDPOINT_BUSY", configured["code"])
        self.assertEqual("ENDPOINT_BUSY", retried["code"])
        configure.assert_not_called()

    def test_reimport_ack_uses_current_receipt_over_stale_status_and_stays_safe(self):
        data = Path(self.temp.name)
        stale_secret = "STALE_RECEIPT_SECRET"
        current_secret = "CURRENT_CONFIG_SECRET"
        (data / "last_startup_result.json").write_text(
            json.dumps(
                {
                    "endpoint": {
                        "status": "READY",
                        "provider": "cloudflare_quick",
                        "public_url": "https://old.trycloudflare.com",
                        "action_reimport_required": True,
                        "api_key": stale_secret,
                    }
                }
            ),
            encoding="utf-8",
        )
        (data / "permanent_endpoint.json").write_text(
            json.dumps(
                {
                    "provider": "cloudflare_quick",
                    "public_url": "https://new.trycloudflare.com",
                    "permanent": False,
                    "stable_hostname": False,
                    "requires_account": False,
                    "action_reimport_required": True,
                    "authtoken": current_secret,
                }
            ),
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, {"WORLD_ENGINE_DATA_DIR": self.temp.name}):
            before = self.api.snapshot()["connection"]
            acknowledged = self.api.acknowledge_action_reimport()
            after = self.api.snapshot()["connection"]

        self.assertTrue(before["action_reimport_required"])
        self.assertTrue(acknowledged["ok"])
        self.assertFalse(acknowledged["action_reimport_required"])
        self.assertFalse(after["action_reimport_required"])
        self.assertEqual("https://new.trycloudflare.com", after["public_url"])
        encoded = json.dumps({"before": before, "ack": acknowledged, "after": after})
        self.assertNotIn(stale_secret, encoded)
        self.assertNotIn(current_secret, encoded)
        self.assertNotIn("api_key", encoded)
        self.assertNotIn("authtoken", encoded)

    def test_unexpected_bridge_failures_never_expose_exception_or_paths(self):
        canary = "BRIDGE_TRACEBACK_CANARY C:\\private\\repo\\secret.py"
        with mock.patch.object(
            self.api,
            "_snapshot_projection",
            side_effect=RuntimeError(canary),
        ):
            snapshot = self.api.snapshot()
        with mock.patch.object(
            self.api,
            "_select_projected_character",
            side_effect=RuntimeError(canary),
        ):
            selected = self.api.select_character("hero")
        for payload in (snapshot, selected):
            encoded = json.dumps(payload, sort_keys=True)
            self.assertNotIn("BRIDGE_TRACEBACK_CANARY", encoded)
            self.assertNotIn("private", encoded.casefold())
            self.assertNotIn("traceback", encoded.casefold())
        self.assertEqual("OFFLINE", snapshot["states"]["engine"])
        self.assertEqual("INVALID_CHARACTER", selected["code"])

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
        self.assertIn("World Engine 5.1.1 Companion", html)
        self.assertIn("Incident journal", js)
        self.assertIn("Available world actions", js)
        self.assertIn("Public politics", js)
        self.assertIn("Speaker identities and portraits are not inferred", js)
        self.assertEqual(1, html.count('id="stage-content"'))
        self.assertIn("Procedural world forge", html)
        self.assertIn("Automatic account-free link", html)
        self.assertIn('id="acknowledge-reimport"', html)
        self.assertIn("Advanced: use an existing ngrok account", html)
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

    def test_duplicate_companion_exits_before_creating_visible_server(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(sys, "argv", ["world_engine_companion.py"]), \
             mock.patch("world_engine_companion.persistent_data_dir", return_value=Path(td)), \
             mock.patch("world_engine_startup.claim_companion_instance", return_value=None), \
             mock.patch("world_engine_companion.ThreadingHTTPServer") as server:
            result = companion_main()
        self.assertEqual(0, result)
        server.assert_not_called()

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


    def test_audio_asset_is_served_csp_safe_and_index_has_no_inline_script(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), AssetHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = "http://127.0.0.1:" + str(server.server_address[1])
            with urllib.request.urlopen(base + "/ambient_audio.js", timeout=3) as response:
                body = response.read().decode("utf-8")
                self.assertEqual(200, response.status)
                self.assertIn("text/javascript", response.headers["Content-Type"])
                self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])
                self.assertEqual("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'", response.headers["Content-Security-Policy"])
                self.assertIn("WorldEngineAmbience", body)
            with urllib.request.urlopen(base + "/index.html", timeout=3) as response:
                index = response.read().decode("utf-8")
                self.assertNotRegex(index, r"<script(?![^>]*\bsrc=)[^>]*>")
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                urllib.request.urlopen(base + "/not-an-asset.js", timeout=3)
            self.assertEqual(404, rejected.exception.code)
        finally:
            server.shutdown()
            server.server_close()
if __name__ == "__main__":
    unittest.main()
