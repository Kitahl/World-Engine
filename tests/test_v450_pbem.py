from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from world_engine import WorldEngine
from world_engine.pbem import PBEM_VERSION
from world_engine.turn_router import TurnRouter


class PBEM21RouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "pbem.sqlite3"
        self.e = WorldEngine(self.path)
        self.e.ensure_campaign("c", "PBEM")
        self.e.set_simulation_seed("c", 1701)
        self.e.upsert_location("c", "a", "A", region="r")
        self.e.upsert_location("c", "b", "B", region="r")
        self.e.upsert_character(
            "c", "hero", "Hero", location="a", hp=20, max_hp=20, ac=14,
            abilities={"str": 3, "dex": 2, "wis": 1, "int": 0, "cha": 0},
            proficiency_bonus=2, resources={"gold": 1},
        )
        self.e.upsert_character("c", "victim", "Victim", location="a", hp=10, max_hp=10, ac=10)
        self.e.upsert_npc("c", "gob", "Goblin", location="a", hp=10, max_hp=10, ac=10)

    def tearDown(self):
        self.tmp.cleanup()

    def resolve_public(self, intents, **kwargs):
        return self.e.resolve_turn(
            "c", actor_kind="character", actor_id="hero",
            intents=intents, enforce_pbem=True, **kwargs,
        )

    def test_pbem_is_opt_in_for_trusted_internal_calls(self):
        before = self.e.get_character("c", "hero")["resources"]["gold"]
        result = self.e.resolve_turn(
            "c", actor_kind="character", actor_id="hero",
            intents=[{"type": "resources", "parameters": {"resource_delta": {"gold": 2}}}],
        )
        self.assertEqual("completed", result["status"])
        self.assertFalse(result["pbem"]["enforced"])
        self.assertEqual(before + 2, self.e.get_character("c", "hero")["resources"]["gold"])

    def test_actor_parameter_impersonation_is_rejected_without_mutation(self):
        result = self.resolve_public([
            {"type": "move", "parameters": {"actor_id": "victim", "kind": "character", "destination": "b"}}
        ])
        self.assertEqual("failed", result["status"])
        self.assertEqual("rejected_by_pbem", result["steps"][0]["status"])
        self.assertEqual("PBEM_ACTOR_SCOPE_MISMATCH", result["steps"][0]["error"]["code"])
        self.assertEqual("a", self.e.get_character("c", "victim")["location"])
        self.assertEqual("a", self.e.get_character("c", "hero")["location"])

    def test_direct_consequence_writer_is_rejected_without_state_change(self):
        before = dict(self.e.get_character("c", "hero")["resources"])
        result = self.resolve_public([
            {"type": "resources", "parameters": {"resource_delta": {"gold": 999}}}
        ])
        self.assertEqual("PBEM_DIRECT_CONSEQUENCE_WRITE_FORBIDDEN", result["steps"][0]["error"]["code"])
        self.assertEqual(before, self.e.get_character("c", "hero")["resources"])

    def test_rules_generic_admin_operation_cannot_escape_public_boundary(self):
        result = self.resolve_public([
            {"type": "rules", "parameters": {"operation": "define_object", "payload": {
                "object_id": "forged", "name": "Forged", "object_kind": "class_feature"
            }}}
        ])
        self.assertEqual("PBEM_RULE_OPERATION_NOT_PLAYER_SAFE", result["steps"][0]["error"]["code"])
        with self.e._db() as db:
            count = db.execute("SELECT COUNT(*) FROM rule_objects WHERE campaign_id=? AND id=?", ("c", "forged")).fetchone()[0]
        self.assertEqual(0, count)

    def test_legacy_attack_with_caller_damage_is_rejected(self):
        hp = self.e.get_npc("c", "gob")["hp"]
        result = self.resolve_public([
            {"type": "attack", "parameters": {
                "target_kind": "npc", "target_id": "gob", "attack_bonus": 100,
                "damage_expression": "99d100", "attack_name": "forged attack"
            }}
        ])
        self.assertEqual("PBEM_LEGACY_MECHANICS_INPUT_FORBIDDEN", result["steps"][0]["error"]["code"])
        self.assertEqual(hp, self.e.get_npc("c", "gob")["hp"])

    def test_resolve_activity_requires_authored_owned_rule_object(self):
        self.e.rules_dispatch("define_object", "c", {
            "object_id": "blade", "name": "Blade", "object_kind": "magic_item", "rules_version": "both"
        })
        self.e.rules_dispatch("define_activity", "c", {
            "activity_id": "blade_hit", "object_id": "blade", "name": "Blade Hit",
            "activity_type": "damage", "rules_version": "both",
            "damage": [{"formula": "1", "type": "slashing"}],
            "targeting": {"mode": "single"},
        })
        payload = {"operation": "resolve_activity", "payload": {
            "activity_id": "blade_hit", "targets": [{"kind": "npc", "id": "gob"}]
        }}
        denied = self.resolve_public([{"type": "rules", "parameters": payload}], idempotency_key="before-grant")
        self.assertEqual("PBEM_RULE_OBJECT_NOT_OWNED", denied["steps"][0]["error"]["code"])
        self.assertEqual(10, self.e.get_npc("c", "gob")["hp"])

        self.e.rules_dispatch("grant_object", "c", {
            "actor_kind": "character", "actor_id": "hero", "object_id": "blade", "source": "test"
        })
        allowed = self.resolve_public([{"type": "rules", "parameters": payload}], idempotency_key="after-grant")
        self.assertEqual("completed", allowed["status"])
        self.assertEqual("PBEM_RULE_OPERATION_ALLOWED", allowed["steps"][0]["pbem"]["code"])
        self.assertEqual(9, self.e.get_npc("c", "gob")["hp"])

    def test_unbound_global_activity_is_not_implicitly_a_player_ability(self):
        self.e.rules_dispatch("define_activity", "c", {
            "activity_id": "global_damage", "name": "Global Damage", "activity_type": "damage",
            "damage": [{"formula": "1", "type": "force"}], "targeting": {"mode": "single"},
        })
        result = self.resolve_public([{"type": "rules", "parameters": {
            "operation": "resolve_activity", "payload": {
                "activity_id": "global_damage", "targets": [{"kind": "npc", "id": "gob"}]
            }
        }}])
        self.assertEqual("PBEM_ACTIVITY_UNBOUND_TO_RULE_OBJECT", result["steps"][0]["error"]["code"])
        self.assertEqual(10, self.e.get_npc("c", "gob")["hp"])

    def test_fpc_uses_server_dc_and_actor_derived_modifier(self):
        self.e.rules_dispatch("set_actor_profile", "c", {
            "actor_kind": "character", "actor_id": "hero", "skill_proficiencies": ["athletics"]
        })
        result = self.resolve_public([{"intent_id": "fpc", "type": "check", "parameters": {
            "modifier": 99, "dc": 1, "mode": "advantage",
            "pbem_fpc": {"severity": "severe", "skill": "athletics"},
        }}])
        step = result["steps"][0]
        self.assertEqual("completed", step["status"])
        self.assertEqual(5, step["result"]["modifier"])
        self.assertEqual(22, step["result"]["dc"])
        self.assertEqual("normal", step["result"]["mode"])
        self.assertEqual(5, step["pbem"]["audit"]["computed_modifier"])
        self.assertEqual("constrained_only", step["pbem"]["audit"]["outcome_scope"])

    def test_requires_success_of_blocks_action_after_failed_fpc(self):
        self.e.upsert_character(
            "c", "hero", "Hero", location="a", hp=20, max_hp=20, ac=14,
            abilities={"str": 0}, proficiency_bonus=0,
        )
        result = self.resolve_public([
            {"intent_id": "fpc", "type": "check", "parameters": {
                "pbem_fpc": {"severity": "world_break", "skill": "athletics"}
            }},
            {"intent_id": "move", "type": "move", "parameters": {"destination": "b"},
             "requires_success_of": ["fpc"]},
        ])
        self.assertFalse(result["steps"][0]["result"]["success"])
        self.assertEqual("skipped_success_condition_failed", result["steps"][1]["status"])
        self.assertEqual("a", self.e.get_character("c", "hero")["location"])

    def test_requires_success_of_executes_after_explicit_success(self):
        self.e.upsert_character(
            "c", "hero", "Hero", location="a", hp=20, max_hp=20, ac=14,
            abilities={"str": 20}, proficiency_bonus=6,
        )
        self.e.rules_dispatch("set_actor_profile", "c", {
            "actor_kind": "character", "actor_id": "hero",
            "skill_proficiencies": ["athletics"], "metadata": {"expertise": ["athletics"]},
        })
        result = self.resolve_public([
            {"intent_id": "fpc", "type": "check", "parameters": {
                "pbem_fpc": {"severity": "world_break", "skill": "athletics"}
            }},
            {"intent_id": "move", "type": "move", "parameters": {"destination": "b"},
             "requires_success_of": ["fpc"]},
        ])
        self.assertTrue(result["steps"][0]["result"]["success"])
        self.assertEqual("completed", result["steps"][1]["status"])
        self.assertEqual("b", self.e.get_character("c", "hero")["location"])

    def test_success_gate_is_part_of_plan_and_dependency_order(self):
        plan = TurnRouter(self.e).capability_plan("c", [
            {"intent_id": "move", "type": "move", "parameters": {"destination": "b"},
             "requires_success_of": ["check"]},
            {"intent_id": "check", "type": "check", "parameters": {"modifier": 0, "dc": 10}},
        ])
        self.assertEqual(["check", "move"], [x["intent_id"] for x in plan])
        self.assertEqual(["check"], plan[1]["requires_success_of"])
        self.assertIn("check", plan[1]["depends_on"])

    def test_world_advance_strips_player_world_overrides(self):
        before_weather = self.e.get_campaign("c")["weather"]
        result = self.resolve_public([{"type": "advance_time", "parameters": {
            "minutes": 60, "weather": "PLAYER_FORCED_LAVA", "season": "forced", "simulate": False
        }}])
        self.assertEqual("completed", result["status"])
        step = result["steps"][0]
        self.assertTrue(step["pbem"]["audit"]["stripped_world_overrides"])
        self.assertNotEqual("PLAYER_FORCED_LAVA", self.e.get_campaign("c")["weather"])
        # The exact simulated weather is intentionally owned by SimulationKernel.
        self.assertIsInstance(before_weather, str)

    def test_pbem_idempotency_namespace_cannot_replay_trusted_turn(self):
        internal = self.e.resolve_turn(
            "c", actor_kind="character", actor_id="hero", idempotency_key="same-key",
            intents=[{"type": "move", "parameters": {"destination": "b"}}],
        )
        public = self.e.resolve_turn(
            "c", actor_kind="character", actor_id="hero", idempotency_key="same-key",
            intents=[{"type": "move", "parameters": {"destination": "b"}}], enforce_pbem=True,
        )
        self.assertNotEqual(internal["turn_id"], public["turn_id"])
        self.assertTrue(public["turn_id"].startswith("turn_pbem22_"))
        self.assertFalse(public["idempotent_replay"])

    def test_pbem_idempotency_namespace_respects_turn_id_length_limit(self):
        result = self.e.resolve_turn(
            "c", actor_kind="character", actor_id="hero", idempotency_key="k" * 100,
            intents=[{"type": "move", "parameters": {"destination": "b"}}], enforce_pbem=True,
        )
        self.assertLessEqual(len(result["turn_id"]), 100)
        self.assertEqual(result["turn_id"], TurnRouter(self.e).get_turn("c", result["turn_id"])["turn_id"])


class PBEM21PublicApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.key = "pbem-api-secret-0123456789"
        self.old_env = os.environ.get("WORLD_ENGINE_API_KEY")
        os.environ["WORLD_ENGINE_API_KEY"] = self.key
        import app as api
        self.api = api
        self.old_engine = api.engine
        api.engine = WorldEngine(Path(self.tmp.name) / "api.sqlite3")
        api.engine.ensure_campaign("c", "PBEM API")
        api.engine.upsert_location("c", "a", "A")
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

    def test_public_resolve_turn_always_enforces_pbem(self):
        response = self.client.post("/api/turn", headers=self.headers, json={
            "campaign_id": "c", "actor_kind": "character", "actor_id": "hero",
            "intents": [{"type": "resources", "parameters": {"resource_delta": {"gold": 999}}}],
        })
        self.assertEqual(200, response.status_code, response.text)
        body = response.json()
        self.assertEqual(PBEM_VERSION, body["pbem"]["version"])
        self.assertTrue(body["pbem"]["enforced"])
        self.assertEqual("rejected_by_pbem", body["steps"][0]["status"])

    def test_openapi_exports_success_dependency_field_without_adding_actions(self):
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
        self.assertLessEqual(len(operations), 30)
        self.assertEqual(len(operations), len({op["operationId"] for op in operations}))
        turn_req = schema["components"]["schemas"]["TurnIntentRequest"]
        self.assertIn("requires_success_of", turn_req["properties"])


if __name__ == "__main__":
    unittest.main()
