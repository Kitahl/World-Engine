from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from world_engine import WorldEngine
from world_engine.turn_policy import narrative_policy


class V393Tests(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.TemporaryDirectory()
        self.e=WorldEngine(Path(self.td.name)/"w.sqlite3")
        self.e.ensure_campaign("c","C")
        self.e.upsert_location("c","town","Town",region="r",description="Stone river town")
        self.e.upsert_character("c","hero","Hero",level=1,hp=12,max_hp=12,ac=14,location="town",inventory=[{"item_id":"sword","qty":1,"equipped":True}])
        self.e.upsert_faction("c","watch","Watch",reputation=0)
    def tearDown(self): self.td.cleanup()

    def test_xp_crossing_is_pending_until_advancement_applied(self):
        p=self.e.world_systems_dispatch("set_progression","c",{"character_id":"hero","mode":"xp","xp":0,"class_id":"fighter"})
        self.assertFalse(p["level_up_available"])
        p=self.e.world_systems_dispatch("award_xp","c",{"character_id":"hero","amount":300,"reason":"quest"})
        self.assertEqual(300,p["xp"]); self.assertEqual(2,p["pending_level"]); self.assertTrue(p["level_up_available"])
        self.assertEqual(1,self.e.get_character("c","hero")["level"])
        self.e.rules_dispatch("define_advancement","c",{"advancement_id":"fighter2","class_id":"fighter","level":2,"rules_version":"2024"})
        self.e.rules_dispatch("apply_advancement","c",{"actor_kind":"character","actor_id":"hero","class_id":"fighter","level":2})
        p=self.e.world_systems_dispatch("get_progression","c",{"character_id":"hero"})
        self.assertEqual(2,p["current_level"]); self.assertIsNone(p["pending_level"]); self.assertFalse(p["level_up_available"])

    def test_reward_atomically_applies_xp_currency_items_reputation(self):
        self.e.world_systems_dispatch("set_progression","c",{"character_id":"hero","mode":"xp","xp":250})
        self.e.world_systems_dispatch("save_reward","c",{"reward_id":"quest1","xp":50,"currency":{"gp":25},"items":[{"item_id":"gem","qty":2}],"reputation":{"watch":3}})
        r=self.e.world_systems_dispatch("grant_reward","c",{"reward_id":"quest1","actor_kind":"character","actor_id":"hero"})
        self.assertEqual(300,r["progression"]["xp_after"]); self.assertEqual(2,r["progression"]["pending_level"])
        with self.e._db() as db:
            self.assertEqual(25,db.execute("SELECT amount FROM owner_balances WHERE campaign_id='c' AND owner_kind='character' AND owner_id='hero' AND currency_key='gp'").fetchone()[0])
            self.assertEqual(3,db.execute("SELECT reputation FROM factions WHERE campaign_id='c' AND id='watch'").fetchone()[0])
        self.assertEqual(2,next(x for x in self.e.get_inventory_items("c","character","hero") if x["item_id"]=="gem")["qty"])

    def test_context_reports_progression(self):
        self.e.world_systems_dispatch("set_progression","c",{"character_id":"hero","mode":"xp","xp":299})
        ctx=self.e.get_world_context("c","town")
        self.assertEqual(299,ctx["characters"][0]["progression"]["xp"])
        self.assertEqual(1,ctx["characters"][0]["progression"]["xp_to_next_level"])

    def test_beliefs_and_goals_are_first_class_decide_considerations(self):
        self.e.upsert_npc("c","guard","Guard",location="town",beliefs=["The gate must be defended"],goals=["Protect the town"])
        self.e.save_npc_action("c","guard","patrol",base_utility=0,considerations=[{"type":"belief","key":"The gate must be defended","weight":1.2},{"type":"goal","key":"Protect the town","weight":1.2}],effects=[],cost_hours=0)
        self.e.save_npc_action("c","guard","rest",base_utility=1.0,considerations=[],effects=[],cost_hours=0)
        self.e.save_simulation_rule("c","guard_decide","DECIDE",cadence="hour",target="guard",params={"npc_id":"guard","temperature":0})
        self.e.advance_world("c",minutes=60,simulate=True)
        snap=self.e.npc_life_dispatch("cognition_snapshot","c",{"npc_id":"guard"})
        self.assertEqual("patrol",snap["last_decision"]["action_id"])
        self.e.update_npc_state("c","guard",remove_beliefs=["The gate must be defended"],remove_goals=["Protect the town"],reason="The emergency ended")
        self.e.advance_world("c",minutes=60,simulate=True)
        snap=self.e.npc_life_dispatch("cognition_snapshot","c",{"npc_id":"guard"})
        self.assertEqual("rest",snap["last_decision"]["action_id"])

    def test_cognition_snapshot_combines_beliefs_goals_needs_thoughts_and_decision(self):
        self.e.upsert_npc("c","mara","Mara",location="town",importance="major",beliefs=["The watch protects the town"],goals=["Keep the gate safe"],memory=["Bandits attacked last winter"])
        self.e.npc_life_dispatch("seed_needs","c",{"npc_id":"mara","overrides":{"safety":{"value":90}}})
        self.e.npc_life_dispatch("add_thought","c",{"npc_id":"mara","thought_id":"raid","cause":"Bandits were sighted","mood_delta":-20,"tags":["fear"]})
        self.e.save_npc_action("c","mara","patrol",base_utility=2,considerations=[{"type":"need","key":"safety","weight":1.0}],effects=[])
        self.e.save_simulation_rule("c","decide_mara","DECIDE",cadence="hour",target="mara",params={"npc_id":"mara","temperature":0})
        self.e.advance_world("c",minutes=60,simulate=True)
        snap=self.e.npc_life_dispatch("cognition_snapshot","c",{"npc_id":"mara"})
        self.assertIn("Keep the gate safe",snap["goals"]); self.assertIn("The watch protects the town",snap["beliefs"])
        self.assertEqual("patrol",snap["last_decision"]["action_id"])
        self.assertTrue(any(t["id"].startswith("decision:") for t in snap["thoughts"]))
        self.assertTrue(snap["dominant_motives"])
        ctx=self.e.get_world_context("c","town")
        self.assertEqual("mara",ctx["npc_cognition"][0]["npc_id"])

    def test_reference_image_is_saved_and_reused_with_gear(self):
        self.e.set_visual_profile("c","character","hero",{"hair":"long black curls","eyes":"bright yellow","build":"tall athletic","clothing":"green travel coat"})
        cue=self.e.build_image_cue("c",trigger_type="character_reference",entity_kind="character",entity_id="hero")
        self.assertTrue(cue["should_generate"]); self.assertEqual("3:4",cue["aspect_ratio"])
        self.e.record_image_generation("c","character_reference",cue["scene_key"],title=cue["title"],prompt=cue["prompt"],aspect_ratio=cue["aspect_ratio"],image_ref="chatgpt-image-ref-hero",status="generated",visual_context=cue["visual_context"],entity_kind="character",entity_id="hero",set_as_primary_reference=True)
        ref=self.e.get_visual_reference("c","character","hero")
        self.assertEqual("chatgpt-image-ref-hero",ref["image_ref"])
        scene=self.e.build_image_cue("c",trigger_type="scene_start",location_id="town",scene_key="scene:town",force=True)
        refs=scene["visual_context"]["reference_images"]
        self.assertEqual("hero",refs[0]["id"]); self.assertIn("Gear continuity",scene["prompt"]); self.assertIn("sword",scene["prompt"])

    def test_major_npc_reference_auto_requested_by_profile_api_minor_is_not(self):
        import app as api
        old_engine=api.engine; old_key=os.environ.get("WORLD_ENGINE_API_KEY")
        try:
            api.engine=self.e; os.environ["WORLD_ENGINE_API_KEY"]="test-secret-0123456789-abcdef"
            self.e.upsert_npc("c","major","Major",location="town",importance="major")
            self.e.upsert_npc("c","minor","Minor",location="town",importance="minor")
            client=TestClient(api.app); headers={"Authorization":"Bearer test-secret-0123456789-abcdef"}
            major=client.post("/api/visual/profile",headers=headers,json={"campaign_id":"c","entity_kind":"npc","entity_id":"major","profile":{"hair":"silver braid","armor":"black plate"}}).json()
            minor=client.post("/api/visual/profile",headers=headers,json={"campaign_id":"c","entity_kind":"npc","entity_id":"minor","profile":{"hair":"brown"}}).json()
            self.assertTrue(major["_turn_directives"]["image"]["required"])
            self.assertEqual("npc_reference",major["_turn_directives"]["image"]["cue"]["trigger_type"])
            self.assertFalse(minor["_turn_directives"]["image"]["required"])
        finally:
            api.engine=old_engine
            if old_key is None: os.environ.pop("WORLD_ENGINE_API_KEY",None)
            else: os.environ["WORLD_ENGINE_API_KEY"]=old_key

    def test_narrative_policy_has_booklike_bounded_lengths(self):
        opening=narrative_policy(task="routine",trigger_type="scene_start")
        combat=narrative_policy(task="combat")
        dialogue=narrative_policy(task="dialogue")
        self.assertEqual({"min":350,"max":550},opening["target_words"])
        self.assertEqual({"min":90,"max":180},combat["target_words"])
        self.assertEqual({"min":120,"max":280},dialogue["target_words"])
        self.assertIn("novel-like",opening["style"])
        self.assertIn("Do not invent",opening["player_agency"])

    def test_schema_11_shape_migrates_to_12_without_losing_campaign(self):
        import sqlite3
        path=Path(self.td.name)/"migration.sqlite3"
        old=WorldEngine(path)
        old.ensure_campaign("old","Old Campaign")
        old.upsert_npc("old","n","Legacy NPC",location="unknown")
        db = sqlite3.connect(path)
        try:
            db.execute("DROP TABLE IF EXISTS entity_visual_references")
            db.execute("DROP TABLE IF EXISTS character_progression")
            db.execute("DROP TABLE IF EXISTS owner_balances")
            db.execute("ALTER TABLE npcs DROP COLUMN importance")
            db.execute("ALTER TABLE visual_preferences DROP COLUMN character_reference")
            db.execute("ALTER TABLE visual_preferences DROP COLUMN major_npc_reference")
            db.execute("PRAGMA user_version=11")
            db.commit()
        finally:
            db.close()
        migrated=WorldEngine(path)
        self.assertEqual("Old Campaign",migrated.get_campaign("old")["name"])
        self.assertEqual("minor",migrated.get_npc("old","n")["importance"])
        self.assertTrue(migrated.get_visual_preferences("old")["character_reference"])
        with migrated._db() as db:
            self.assertEqual(20,db.execute("PRAGMA user_version").fetchone()[0])
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertTrue({"entity_visual_references","character_progression","owner_balances"}.issubset(tables))

    def test_failed_reference_generation_remains_retryable(self):
        self.e.set_visual_profile("c","character","hero",{"hair":"black","armor":"green coat"})
        cue=self.e.build_image_cue("c",trigger_type="character_reference",entity_kind="character",entity_id="hero")
        self.e.record_image_generation("c","character_reference",cue["scene_key"],title=cue["title"],prompt=cue["prompt"],aspect_ratio=cue["aspect_ratio"],status="failed",visual_context=cue["visual_context"],entity_kind="character",entity_id="hero",set_as_primary_reference=True)
        again=self.e.build_image_cue("c",trigger_type="character_reference",entity_kind="character",entity_id="hero")
        self.assertTrue(again["should_generate"])

    def test_milestone_progression_stays_pending_until_advancement(self):
        self.e.world_systems_dispatch("set_progression","c",{"character_id":"hero","mode":"milestone","class_id":"fighter"})
        p=self.e.world_systems_dispatch("award_milestone","c",{"character_id":"hero","target_level":2,"reason":"story milestone"})
        self.assertEqual(2,p["pending_level"]); self.assertTrue(p["level_up_available"]); self.assertEqual(1,self.e.get_character("c","hero")["level"])
        self.e.rules_dispatch("define_advancement","c",{"advancement_id":"fighter2m","class_id":"fighter","level":2,"rules_version":"2024"})
        self.e.rules_dispatch("apply_advancement","c",{"actor_kind":"character","actor_id":"hero","class_id":"fighter","level":2})
        p=self.e.world_systems_dispatch("get_progression","c",{"character_id":"hero"})
        self.assertEqual(2,p["current_level"]); self.assertFalse(p["level_up_available"])

    def test_reward_rolls_back_if_reputation_target_is_invalid(self):
        self.e.world_systems_dispatch("save_reward","c",{"reward_id":"bad","currency":{"gp":9},"items":[{"item_id":"ruby","qty":1}],"reputation":{"missing-faction":2}})
        with self.assertRaises(KeyError):
            self.e.world_systems_dispatch("grant_reward","c",{"reward_id":"bad","actor_kind":"character","actor_id":"hero"})
        with self.e._db() as db:
            self.assertIsNone(db.execute("SELECT amount FROM owner_balances WHERE campaign_id='c' AND owner_kind='character' AND owner_id='hero' AND currency_key='gp'").fetchone())
            self.assertIsNone(db.execute("SELECT qty FROM inventories WHERE campaign_id='c' AND owner_kind='character' AND owner_id='hero' AND item_id='ruby'").fetchone())

    def test_large_xp_award_can_make_multiple_levels_pending_without_auto_leveling(self):
        self.e.world_systems_dispatch("set_progression","c",{"character_id":"hero","mode":"xp","xp":0})
        p=self.e.world_systems_dispatch("award_xp","c",{"character_id":"hero","amount":2700,"reason":"large award"})
        self.assertEqual(4,p["eligible_level"]); self.assertEqual(4,p["pending_level"]); self.assertEqual(1,self.e.get_character("c","hero")["level"])


    def test_advancement_rejects_unearned_or_skipped_level(self):
        self.e.rules_dispatch("define_advancement","c",{"advancement_id":"fighter2_gate","class_id":"fighter","level":2,"rules_version":"2024"})
        with self.assertRaisesRegex(ValueError,"not eligible"):
            self.e.rules_dispatch("apply_advancement","c",{"actor_kind":"character","actor_id":"hero","class_id":"fighter","level":2})
        self.e.world_systems_dispatch("award_xp","c",{"character_id":"hero","amount":2700,"reason":"large award"})
        with self.assertRaisesRegex(ValueError,"exactly the next level"):
            self.e.rules_dispatch("apply_advancement","c",{"actor_kind":"character","actor_id":"hero","class_id":"fighter","level":4})
        self.e.rules_dispatch("apply_advancement","c",{"actor_kind":"character","actor_id":"hero","class_id":"fighter","level":2})
        self.assertEqual(2,self.e.get_character("c","hero")["level"])

    def test_character_readback_includes_reward_ledger_balances_and_progression(self):
        self.e.world_systems_dispatch("save_reward","c",{"reward_id":"visible","xp":25,"currency":{"gp":7},"items":[{"item_id":"potion","qty":1}]})
        self.e.world_systems_dispatch("grant_reward","c",{"reward_id":"visible","actor_kind":"character","actor_id":"hero"})
        sheet=self.e.get_character_sheet("c","hero")
        self.assertEqual(25,sheet["progression"]["xp"])
        self.assertEqual(7,sheet["balances"]["gp"])
        self.assertEqual("potion",sheet["inventory_ledger"][0]["item_id"])
        ctx=self.e.get_world_context("c","town")
        self.assertEqual(7,ctx["characters"][0]["balances"]["gp"])
        self.assertTrue(any(x["item_id"]=="potion" for x in ctx["characters"][0]["inventory_ledger"]))

    def test_npc_readback_includes_cognition_and_visual_reference_status(self):
        self.e.upsert_npc("c","sage","Sage",location="town",importance="major",beliefs=["Knowledge should be protected"],goals=["Find the archive"])
        self.e.npc_life_dispatch("seed_needs","c",{"npc_id":"sage"})
        sheet=self.e.get_npc_sheet("c","sage")
        self.assertEqual("sage",sheet["cognition"]["npc_id"])
        self.assertIn("Find the archive",sheet["cognition"]["goals"])
        self.assertEqual("missing",sheet["visual_reference"]["status"])

    def test_openapi_30_actions_all_always_allow_eligible(self):
        import json, subprocess, sys
        root=Path(__file__).resolve().parents[1]
        proc=subprocess.run([sys.executable,str(root/"scripts"/"export_openapi.py")],cwd=root,text=True,capture_output=True)
        self.assertEqual(0,proc.returncode,proc.stderr)
        schema=json.loads((root/"openapi_actions.json").read_text())
        ops=[operation for item in schema["paths"].values() for operation in item.values() if isinstance(operation,dict) and operation.get("operationId")]
        self.assertLessEqual(len(ops),30)
        self.assertEqual(len(ops),len({o["operationId"] for o in ops}))
        self.assertTrue(all(o.get("x-openai-isConsequential") is False for o in ops))
        self.assertTrue(any(o["operationId"]=="saveVisualProfile" for o in ops))

if __name__ == "__main__": unittest.main()
