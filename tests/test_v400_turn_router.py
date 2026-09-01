from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from world_engine import WorldEngine
from world_engine.turn_router import DEFAULT_CAPABILITIES, TurnRouter


class TurnRouterV400Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "world.sqlite3"
        self.e = WorldEngine(self.db_path)
        self.e.ensure_campaign("c", "Campaign")
        self.e.upsert_location("c", "village", "Village", region="coast", realm_id="sword_coast", x=0, y=0)
        self.e.upsert_location("c", "road", "Eastern Road", region="coast", realm_id="sword_coast", x=10, y=0)
        self.e.save_location_link("c", "village", "road", 2.0, road_quality="road", bidirectional=True)
        self.e.upsert_character("c", "hero", "Hero", location="village", hp=20, max_hp=20, ac=15)
        self.e.upsert_npc(
            "c", "mara", "Mara", location="village", faction_id="watch",
            hp=8, max_hp=8, ac=12, importance="major",
            beliefs=["The eastern road is dangerous"], goals=["Protect the village"],
            memory=["A caravan vanished last week"],
        )
        self.e.upsert_faction("c", "watch", "Village Watch", region="coast", leader_id="mara")
        self.e.upsert_quest("c", "caravan", "Find the Caravan", owner_id="hero", region="coast")
        self.router = TurnRouter(self.e)

    def tearDown(self):
        self.tmp.cleanup()

    def test_schema_14_and_router_tables_exist(self):
        expected = {
            "we4_capability_manifests", "we4_entities", "we4_relations",
            "we4_facts", "we4_beliefs", "we4_information_transfers",
            "we4_context_compilations", "we4_turn_records",
        }
        with self.e._db() as db:
            self.assertEqual(WorldEngine.SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(expected.issubset(tables))

    def test_default_capability_registry_is_seeded_and_typed(self):
        caps = self.e.list_capabilities("c")
        self.assertEqual(len(DEFAULT_CAPABILITIES), len(caps))
        self.assertTrue({"READ", "RESOLVED", "SIMULATED", "NARRATED", "AUTHOR"}.issubset({x["mode"] for x in caps}))
        self.assertEqual("turn_router", next(x for x in caps if x["capability_id"] == "context.compile")["provider"])

    def test_existing_tables_sync_to_universal_entities_and_relations(self):
        result = self.router.sync_existing_entities("c")
        self.assertGreaterEqual(result["total"], 7)
        entities = {x["entity_key"] for x in self.router.list_entities("c")}
        self.assertTrue({
            "character:hero", "npc:mara", "faction:watch", "location:village",
            "location:road", "quest:caravan", "realm:sword_coast",
        }.issubset(entities))
        hero_relations = self.router.relations_for("c", "character:hero")
        self.assertTrue(any(x["relation_type"] == "located_in" and x["target_key"] == "location:village" for x in hero_relations))
        self.assertTrue(any(x["relation_type"] == "assigned_to" and x["target_key"] == "quest:caravan" for x in hero_relations))
        mara_relations = self.router.relations_for("c", "npc:mara")
        self.assertTrue(any(x["relation_type"] == "member_of" and x["target_key"] == "faction:watch" for x in mara_relations))
        self.assertTrue(any(x["relation_type"] == "leads" and x["target_key"] == "faction:watch" for x in mara_relations))

    def test_relation_mutation_is_revisioned_and_graph_path_is_deterministic(self):
        before = self.e.get_campaign("c")["revision"]
        relation = self.router.upsert_relation("c", "character:hero", "knows", "npc:mara", strength=0.8)
        self.assertEqual(before + 1, relation["revision"])
        path = self.router.graph_path("c", "character:hero", "faction:watch", max_depth=4)
        self.assertTrue(path["found"])
        self.assertEqual("character:hero", path["nodes"][0])
        self.assertEqual("faction:watch", path["nodes"][-1])
        self.assertEqual(path, self.router.graph_path("c", "character:hero", "faction:watch", max_depth=4))

    def test_ambiguous_plain_entity_id_is_rejected(self):
        self.e.upsert_npc("c", "hero", "Impostor Hero", location="village", hp=2, max_hp=2, ac=10)
        self.router.sync_existing_entities("c")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.router.get_entity("c", "hero")
        self.assertEqual("Hero", self.router.get_entity("c", "character:hero")["entity"]["canonical_name"])

    def test_canonical_fact_belief_and_transfer_preserve_epistemic_boundary(self):
        fact = self.router.assert_fact(
            "c", "location:road", "threat.cause", "bandits",
            provenance={"source": "witnessed event"},
        )
        self.assertEqual("bandits", fact["object_value"])
        mara_belief = self.router.set_belief(
            "c", "npc:mara", fact["fact_id"], belief_value="cultists",
            confidence=0.8, status="believes", provenance={"reason": "mistaken silhouette"},
        )
        self.assertEqual("cultists", mara_belief["belief_value"])
        transfer = self.router.transfer_information(
            "c", fact["fact_id"], "character:hero", sender="npc:mara",
            credibility=0.75, distortion=0.25, channel="speech",
        )
        self.assertAlmostEqual(0.45, transfer["receiver_belief"]["confidence"])
        self.assertEqual("cultists", transfer["receiver_belief"]["belief_value"])
        snapshot = self.router.knowledge_snapshot("c", believer="character:hero")
        self.assertEqual("bandits", snapshot["facts"][0]["object_value"])
        self.assertEqual("cultists", snapshot["beliefs"][0]["belief_value"])
        self.assertEqual(1, len(snapshot["transfers"]))

    def test_information_transfer_genealogy_is_traceable(self):
        fact = self.router.assert_fact("c", "location:road", "bridge.state", "collapsed")
        first = self.router.transfer_information("c", fact["fact_id"], "npc:mara", sender=None, channel="observation")
        second = self.router.transfer_information(
            "c", fact["fact_id"], "character:hero", sender="npc:mara",
            parent_transfer_id=first["transfer"]["transfer_id"], channel="speech",
        )
        self.assertEqual(first["transfer"]["transfer_id"], second["transfer"]["parent_transfer_id"])
        history = self.router.knowledge_snapshot("c", fact_id=fact["fact_id"])
        self.assertEqual(2, len(history["transfers"]))

    def test_context_compiler_is_budgeted_deterministic_and_inspectable(self):
        fact = self.router.assert_fact("c", "location:road", "risk.level", "high")
        self.router.set_belief("c", "character:hero", fact["fact_id"], confidence=1.0)
        intents = [{"type": "interact", "parameters": {"npc_id": "mara", "topic": "caravan"}}]
        a = self.router.compile_context(
            "c", actor_kind="character", actor_id="hero", location_id="village",
            intents=intents, max_chars=4000,
        )
        b = self.router.compile_context(
            "c", actor_kind="character", actor_id="hero", location_id="village",
            intents=intents, max_chars=4000,
        )
        self.assertEqual(a["digest"], b["digest"])
        self.assertLessEqual(a["budget"]["used_chars"], 4000)
        self.assertEqual(a["budget"]["used_chars"], sum(x["char_count"] for tier in a["context"].values() for x in tier))
        self.assertGreater(a["activation_inspector"]["candidate_count"], a["activation_inspector"]["included_count"])
        self.assertTrue(a["context"]["HOT"])
        self.assertEqual([], a["context"]["ARCHIVE"])
        included_ids = {x["item_id"] for x in a["activation_inspector"]["included"]}
        self.assertIn("actor:character:hero", included_ids)

    def test_context_archive_is_opt_in(self):
        for i in range(30):
            self.e.commit_event("c", "history", f"event {i}")
        cold = self.router.compile_context("c", actor_kind="character", actor_id="hero", max_chars=30000, include_archive=False)
        player_archived = self.router.compile_context("c", actor_kind="character", actor_id="hero", max_chars=30000, include_archive=True)
        gm_cold = self.router.compile_context(
            "c", actor_kind="character", actor_id="hero",
            viewer_kind="gm", viewer_id="test-gm",
            max_chars=30000, include_archive=False,
        )
        gm_archived = self.router.compile_context(
            "c", actor_kind="character", actor_id="hero",
            viewer_kind="gm", viewer_id="test-gm",
            max_chars=30000, include_archive=True,
        )
        self.assertEqual([], cold["context"]["ARCHIVE"])
        self.assertEqual([], player_archived["context"]["ARCHIVE"])
        self.assertEqual([], gm_cold["context"]["ARCHIVE"])
        self.assertTrue(any(x["item_id"] == "events:archive" for x in gm_archived["context"]["ARCHIVE"]))

    def test_intent_aliases_and_dependency_order(self):
        plan = self.router.capability_plan("c", [
            {"intent_id": "check", "type": "check", "parameters": {"modifier": 2, "dc": 10}, "depends_on": ["move"]},
            {"intent_id": "move", "type": "move", "parameters": {"destination": "road"}},
        ])
        self.assertEqual(["move", "check"], [x["intent_id"] for x in plan])
        self.assertEqual(["actor.move", "rules.check"], [x["capability_id"] for x in plan])

    def test_dependency_cycle_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.router.capability_plan("c", [
                {"intent_id": "a", "type": "check", "parameters": {"modifier": 0, "dc": 10}, "depends_on": ["b"]},
                {"intent_id": "b", "type": "check", "parameters": {"modifier": 0, "dc": 10}, "depends_on": ["a"]},
            ])

    def test_disabled_capability_fails_closed(self):
        self.router.set_capability_enabled("c", "actor.move", False)
        with self.assertRaisesRegex(ValueError, "unavailable capability"):
            self.router.capability_plan("c", [{"type": "move", "parameters": {"destination": "road"}}])
        self.router.set_capability_enabled("c", "actor.move", True)

    def test_rules_generic_rejects_authoring_even_without_http_guard(self):
        before = self.e.get_campaign("c")["revision"]
        with self.assertRaisesRegex(PermissionError, "PUBLIC_RULES_OPERATION_NOT_ALLOWED"):
            self.router._execute_capability(
                "c",
                "character",
                "hero",
                "rules.generic",
                {
                    "operation": "define_activity",
                    "payload": {
                        "activity_id": "forbidden",
                        "name": "Forbidden",
                        "activity_type": "utility",
                    },
                },
            )
        self.assertEqual(before, self.e.get_campaign("c")["revision"])
        with self.e._db() as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM rule_activities WHERE campaign_id='c' AND id='forbidden'").fetchone()[0])

    def test_resolve_turn_executes_multiple_ordered_capabilities(self):
        before = self.e.get_campaign("c")["revision"]
        result = self.e.resolve_turn(
            "c", actor_kind="character", actor_id="hero", expected_revision=before,
            raw_player_text="I go to the road and search for tracks.",
            intents=[
                {"intent_id": "move", "type": "move", "parameters": {"destination": "road"}},
                {"intent_id": "search", "type": "check", "parameters": {"modifier": 3, "dc": 12}, "depends_on": ["move"]},
            ],
        )
        self.assertEqual("completed", result["status"])
        self.assertEqual(["move", "search"], result["completed_intents"])
        self.assertEqual("road", self.e.get_character("c", "hero")["location"])
        self.assertGreater(result["revision_after"], result["revision_before"])
        self.assertEqual("atomic_per_command; ordered turn stops on first required failure", result["commit_model"])

    def test_resolve_turn_idempotency_prevents_duplicate_mutation(self):
        before = self.e.get_campaign("c")["revision"]
        payload = dict(
            actor_kind="character", actor_id="hero", expected_revision=before,
            idempotency_key="turn_move_once", raw_player_text="I leave.",
            intents=[{"type": "move", "parameters": {"destination": "road"}}],
        )
        first = self.e.resolve_turn("c", **payload)
        revision = self.e.get_campaign("c")["revision"]
        second = self.e.resolve_turn("c", **payload)
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(revision, self.e.get_campaign("c")["revision"])
        self.assertEqual(first["turn_id"], second["turn_id"])

    def test_revision_conflict_requires_context_refresh(self):
        stale = self.e.get_campaign("c")["revision"]
        self.e.commit_event("c", "external", "another request changed state")
        with self.assertRaisesRegex(ValueError, "revision conflict"):
            self.e.resolve_turn(
                "c", actor_kind="character", actor_id="hero", expected_revision=stale,
                intents=[{"type": "move", "parameters": {"destination": "road"}}],
            )

    def test_partial_failure_is_explicit_and_does_not_replay(self):
        before = self.e.get_campaign("c")["revision"]
        result = self.e.resolve_turn(
            "c", actor_kind="character", actor_id="hero", expected_revision=before,
            idempotency_key="partial_failure",
            intents=[
                {"intent_id": "relation", "type": "relation", "parameters": {"source": "character:hero", "relation_type": "knows", "target": "npc:mara"}},
                {"intent_id": "bad_move", "type": "move", "parameters": {"destination": "missing_location"}, "depends_on": ["relation"]},
                {"intent_id": "blocked", "type": "check", "parameters": {"modifier": 0, "dc": 10}, "depends_on": ["bad_move"]},
            ],
        )
        self.assertEqual("partial_failed", result["status"])
        self.assertEqual("completed", result["steps"][0]["status"])
        self.assertEqual("failed", result["steps"][1]["status"])
        self.assertEqual(2, len(result["steps"]))  # stops before blocked required work
        replay = self.e.resolve_turn(
            "c", actor_kind="character", actor_id="hero", idempotency_key="partial_failure",
            intents=[
                {"intent_id": "relation", "type": "relation", "parameters": {"source": "character:hero", "relation_type": "knows", "target": "npc:mara"}},
                {"intent_id": "bad_move", "type": "move", "parameters": {"destination": "missing_location"}, "depends_on": ["relation"]},
                {"intent_id": "blocked", "type": "check", "parameters": {"modifier": 0, "dc": 10}, "depends_on": ["bad_move"]},
            ],
        )
        self.assertTrue(replay["idempotent_replay"])
        self.assertTrue(replay["retry_blocked"])

    def test_optional_failure_does_not_fail_independent_required_step(self):
        result = self.e.resolve_turn(
            "c", actor_kind="character", actor_id="hero",
            intents=[
                {"intent_id": "optional_bad", "type": "move", "optional": True, "parameters": {"destination": "missing"}},
                {"intent_id": "check", "type": "check", "parameters": {"modifier": 0, "dc": 10}},
            ],
        )
        self.assertEqual("completed", result["status"])
        self.assertEqual(["failed", "completed"], [x["status"] for x in result["steps"]])

    def test_plan_and_context_modes_do_not_mutate_campaign_revision(self):
        before = self.e.get_campaign("c")["revision"]
        plan = self.e.resolve_turn(
            "c", actor_kind="character", actor_id="hero", mode="plan",
            intents=[{"type": "move", "parameters": {"destination": "road"}}],
        )
        self.assertEqual("planned", plan["status"])
        self.assertEqual("village", self.e.get_character("c", "hero")["location"])
        self.assertEqual(before, self.e.get_campaign("c")["revision"])
        context = self.e.resolve_turn("c", actor_kind="character", actor_id="hero", mode="context_only")
        self.assertEqual("planned", context["status"])
        self.assertEqual([], context["capability_plan"])
        self.assertEqual(before, self.e.get_campaign("c")["revision"])

    def test_capabilities_mode_exposes_protocol_without_mutation(self):
        before = self.e.get_campaign("c")["revision"]
        result = self.e.resolve_turn("c", mode="capabilities")
        self.assertEqual("WETP-1.0", result["protocol_version"])
        self.assertEqual(len(DEFAULT_CAPABILITIES), len(result["capabilities"]))
        self.assertEqual(before, self.e.get_campaign("c")["revision"])

    def test_turn_record_is_readable_and_contains_context_digest(self):
        result = self.e.resolve_turn(
            "c", actor_kind="character", actor_id="hero", idempotency_key="turn_record_test",
            intents=[{"type": "check", "parameters": {"modifier": 1, "dc": 10}}],
        )
        record = self.router.get_turn("c", result["turn_id"])
        self.assertEqual("completed", record["status"])
        self.assertEqual(result["context_packet"]["digest"], record["context_digest"])
        self.assertEqual("rules.check", record["capability_plan"][0]["capability_id"])

    def test_migration_from_schema_12_shape_creates_router_tables(self):
        import sqlite3

        path = Path(self.tmp.name) / "migration.sqlite3"
        e = WorldEngine(path)
        e.ensure_campaign("old", "Old")
        db = sqlite3.connect(path)
        try:
            for table in (
                "we4_turn_records", "we4_context_compilations", "we4_information_transfers", "we4_beliefs",
                "we4_facts", "we4_relations", "we4_entities", "we4_capability_manifests",
            ):
                db.execute(f"DROP TABLE IF EXISTS {table}")
            db.execute("PRAGMA user_version=12")
            db.commit()
        finally:
            db.close()
        migrated = WorldEngine(path)
        self.assertEqual("Old", migrated.get_campaign("old")["name"])
        with migrated._db() as db:
            self.assertEqual(WorldEngine.SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("we4_turn_records", tables)
        self.assertEqual(len(DEFAULT_CAPABILITIES), len(migrated.list_capabilities("old")))


class TurnRouterApiV400Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "api.sqlite3"
        self.key = "test-secret-0123456789-abcdef"
        self.old_env = os.environ.get("WORLD_ENGINE_API_KEY")
        os.environ["WORLD_ENGINE_API_KEY"] = self.key
        import app as api
        self.api = api
        self.old_engine = api.engine
        api.engine = WorldEngine(self.db)
        api.engine.ensure_campaign("c")
        api.engine.upsert_location("c", "a", "A", region="r")
        api.engine.upsert_location("c", "b", "B", region="r")
        api.engine.save_location_link("c", "a", "b", 1.0, bidirectional=True)
        api.engine.upsert_character("c", "hero", "Hero", location="a", hp=10, max_hp=10, ac=12)
        self.client = TestClient(api.app)
        self.headers = {"Authorization": f"Bearer {self.key}"}

    def tearDown(self):
        self.api.engine = self.old_engine
        if self.old_env is None:
            os.environ.pop("WORLD_ENGINE_API_KEY", None)
        else:
            os.environ["WORLD_ENGINE_API_KEY"] = self.old_env
        self.tmp.cleanup()

    def test_resolve_turn_api_executes_and_returns_router_context_and_directives(self):
        revision = self.api.engine.get_campaign("c")["revision"]
        response = self.client.post(
            "/api/turn", headers=self.headers,
            json={
                "campaign_id": "c", "actor_kind": "character", "actor_id": "hero",
                "expected_revision": revision, "player_text": "I walk to B.",
                "intents": [{"intent_id": "move", "type": "move", "parameters": {"destination": "b"}}],
            },
        )
        self.assertEqual(200, response.status_code, response.text)
        body = response.json()
        self.assertEqual("completed", body["status"])
        self.assertEqual("WETP-1.0", body["protocol_version"])
        self.assertEqual("b", self.api.engine.get_character("c", "hero")["location"])
        self.assertIn("context_packet", body)
        self.assertEqual("resolveTurn", body["_engine_receipt"]["operation"])
        self.assertTrue(body["_turn_directives"]["image"]["required"])

    def test_resolve_turn_api_revision_conflict_is_422(self):
        revision = self.api.engine.get_campaign("c")["revision"]
        self.api.engine.commit_event("c", "change", "changed")
        response = self.client.post(
            "/api/turn", headers=self.headers,
            json={
                "campaign_id": "c", "actor_kind": "character", "actor_id": "hero",
                "expected_revision": revision,
                "intents": [{"type": "move", "parameters": {"destination": "b"}}],
            },
        )
        self.assertEqual(422, response.status_code)
        self.assertEqual("REQUEST_REJECTED", response.json()["detail"])

    def test_exported_openapi_stays_at_30_and_prefers_resolve_turn(self):
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ)
        env["WORLD_ENGINE_DB"] = str(Path(self.tmp.name) / "export.sqlite3")
        env["WORLD_ENGINE_API_KEY"] = self.key
        process = subprocess.run(
            [sys.executable, str(root / "scripts" / "export_openapi.py")],
            cwd=root, env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, process.returncode, process.stderr)
        schema = json.loads((root / "openapi_actions.json").read_text(encoding="utf-8"))
        operations = [
            op for methods in schema["paths"].values() for op in methods.values()
            if isinstance(op, dict) and op.get("operationId")
        ]
        ids = {x["operationId"] for x in operations}
        self.assertLessEqual(len(operations), 30)
        self.assertEqual(len(operations), len(ids))
        self.assertIn("resolveTurn", ids)
        self.assertIn("publishPresentation", ids)
        self.assertNotIn("commitWorldEvent", ids)
        self.assertTrue(all(x.get("x-openai-isConsequential") is False for x in operations))
        turn_schema = schema["paths"]["/api/turn"]["post"]
        self.assertEqual("resolveTurn", turn_schema["operationId"])


if __name__ == "__main__":
    unittest.main()
