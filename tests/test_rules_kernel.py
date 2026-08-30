from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from world_engine import WorldEngine


class RulesKernelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "rules.sqlite3"
        self.e = WorldEngine(self.path)
        self.e.ensure_campaign("c", "Rules", "1492-01-01T08:00:00+00:00")
        self.e.set_simulation_seed("c", 1701)
        self.e.upsert_location("c", "arena", "Arena", region="city")
        self.e.upsert_location("c", "tower", "Tower", region="city")
        self.e.upsert_character("c", "hero", "Hero", level=5, hp=30, max_hp=30, ac=14, location="arena", abilities={"str": 3, "dex": 2, "con": 2, "int": 4}, proficiency_bonus=3)
        self.e.upsert_npc("c", "gob", "Goblin", hp=20, max_hp=20, ac=12, location="arena", stats={"str_mod": 1, "dex_mod": 2, "con_mod": 1, "proficiency_bonus": 2})
        self.e.rules_dispatch("configure", "c", {"rules_version": "2024"})
        self.e.rules_dispatch("set_actor_profile", "c", {"actor_kind": "character", "actor_id": "hero", "spellcasting_ability": "int", "save_proficiencies": ["con"]})
        self.e.rules_dispatch("set_actor_profile", "c", {"actor_kind": "npc", "actor_id": "gob"})

    def tearDown(self):
        self.tmp.cleanup()

    def define(self, activity_id: str, activity_type: str, **kwargs):
        return self.e.rules_dispatch("define_activity", "c", {"activity_id": activity_id, "name": kwargs.pop("name", activity_id), "activity_type": activity_type, **kwargs})

    def resolve(self, activity_id: str, *, actor_kind: str = "character", actor_id: str = "hero", targets=(), **kwargs):
        return self.e.rules_dispatch("resolve_activity", "c", {"activity_id": activity_id, "actor_kind": actor_kind, "actor_id": actor_id, "targets": list(targets), **kwargs})

    def test_schema_version_and_tables(self):
        with self.e._db() as db:
            self.assertEqual(self.e.SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("rules_config", "rule_actor_profiles", "rule_objects", "rule_activities", "rule_resources", "rule_effects", "rule_reactions", "rule_turn_state", "rule_advancements", "rule_summons", "rule_transform_snapshots"):
            self.assertIn(table, tables)

    def test_attack_modifier_composition_and_damage(self):
        self.define("blade", "attack", attack={"ability": "str", "proficient": True, "bonus": 20}, damage=[{"formula": "4", "type": "slashing"}], targeting={"mode": "single"})
        out = self.resolve("blade", targets=[{"kind": "npc", "id": "gob"}])
        result = out["results"][0]
        self.assertTrue(result["hit"])
        self.assertEqual(26, result["attack"]["modifier"])
        self.assertEqual(16, self.e.get_npc("c", "gob")["hp"])

    def test_attack_natural_one_misses_and_natural_twenty_criticals(self):
        self.define("blade", "attack", attack={"ability": "str", "proficient": False}, damage=[{"formula": "2", "type": "slashing"}], targeting={"mode": "single"})
        original = self.e._resolve_check_db
        try:
            self.e._resolve_check_db = lambda db, campaign_id, modifier, dc, mode, namespace: {"mode": mode, "d20_rolls": [1], "natural": 1, "modifier": modifier, "total": 99, "dc": dc, "success": True}
            self.assertFalse(self.resolve("blade", targets=[{"kind": "npc", "id": "gob"}])["results"][0]["hit"])
            self.e._resolve_check_db = lambda db, campaign_id, modifier, dc, mode, namespace: {"mode": mode, "d20_rolls": [20], "natural": 20, "modifier": modifier, "total": 20 + modifier, "dc": dc, "success": False}
            result = self.resolve("blade", targets=[{"kind": "npc", "id": "gob"}])["results"][0]
            self.assertTrue(result["hit"]); self.assertTrue(result["critical"])
        finally:
            self.e._resolve_check_db = original

    def test_resistance_vulnerability_immunity_and_temp_hp(self):
        self.e.rules_dispatch("set_actor_profile", "c", {"actor_kind": "npc", "actor_id": "gob", "temp_hp": 3, "resistances": ["fire"], "vulnerabilities": ["fire"], "immunities": ["cold"]})
        self.define("mixed", "damage", damage=[{"formula": "10", "type": "fire"}, {"formula": "7", "type": "cold"}], targeting={"mode": "single"})
        result = self.resolve("mixed", targets=[{"kind": "npc", "id": "gob"}])["results"][0]["damage"]
        self.assertEqual(10, result["applied_total"])
        self.assertEqual(3, result["temp_hp_absorbed"])
        self.assertEqual(13, self.e.get_npc("c", "gob")["hp"])
        self.assertEqual("resistant+vulnerable", result["parts"][0]["mitigation"])
        self.assertEqual("immune", result["parts"][1]["mitigation"])

    def test_save_success_half_damage_and_failure_full_damage(self):
        self.define("burst", "save", save={"ability": "dex", "dc": 10, "on_success": "half"}, damage=[{"formula": "10", "type": "force"}], targeting={"mode": "single"})
        original = self.e._resolve_check_db
        try:
            self.e._resolve_check_db = lambda db, campaign_id, modifier, dc, mode, namespace: {"mode": mode, "d20_rolls": [20], "natural": 20, "modifier": modifier, "total": 99, "dc": dc, "success": True}
            result = self.resolve("burst", targets=[{"kind": "npc", "id": "gob"}])["results"][0]
            self.assertEqual(5, result["damage"]["applied_total"])
            self.e._resolve_check_db = lambda db, campaign_id, modifier, dc, mode, namespace: {"mode": mode, "d20_rolls": [1], "natural": 1, "modifier": modifier, "total": 1, "dc": dc, "success": False}
            result = self.resolve("burst", targets=[{"kind": "npc", "id": "gob"}])["results"][0]
            self.assertEqual(10, result["damage"]["applied_total"])
        finally:
            self.e._resolve_check_db = original

    def test_spell_slot_consumption_and_upcasting(self):
        self.e.rules_dispatch("set_resource", "c", {"actor_kind": "character", "actor_id": "hero", "resource_key": "spell_slot:2", "current": 1, "maximum": 1, "recovery": "long_rest"})
        self.define("bolt", "damage", damage=[{"formula": "2", "type": "force"}], targeting={"mode": "single"}, consumption=[{"resource": "spell_slot", "minimum_level": 1, "amount": 1}], scaling={"type": "slot", "base_level": 1, "damage_per_level": [{"formula": "3", "type": "force"}]})
        result = self.resolve("bolt", targets=[{"kind": "npc", "id": "gob"}], slot_level=2)
        self.assertEqual(5, result["results"][0]["damage"]["applied_total"])
        resource = self.e.rules_dispatch("get_actor_rules", "c", {"actor_kind": "character", "actor_id": "hero"})["resources"][0]
        self.assertEqual(0, resource["current_value"])

    def test_failed_validation_rolls_back_action_and_all_resources(self):
        self.e.rules_dispatch("set_resource", "c", {"actor_kind": "character", "actor_id": "hero", "resource_key": "spell_slot:1", "current": 1, "maximum": 1})
        self.e.rules_dispatch("set_resource", "c", {"actor_kind": "character", "actor_id": "hero", "resource_key": "focus", "current": 0, "maximum": 1})
        self.define("invalid_use", "damage", damage=[{"formula": "1", "type": "force"}], targeting={"mode": "single"}, consumption=[{"resource": "spell_slot", "minimum_level": 1}, {"resource": "focus"}])
        with self.assertRaises(ValueError):
            self.resolve("invalid_use", targets=[{"kind": "npc", "id": "gob"}], slot_level=1)
        resources = {r["resource_key"]: r["current_value"] for r in self.e.rules_dispatch("get_actor_rules", "c", {"actor_kind": "character", "actor_id": "hero"})["resources"]}
        self.assertEqual(1, resources["spell_slot:1"])

    def test_concentration_group_replacement_preserves_new_siblings(self):
        self.define("focus_one", "utility", targeting={"mode": "self"}, effects=[{"name": "Focus AC", "modifiers": {"ac_bonus": 1}, "concentration": True, "duration": {"unit": "hour", "value": 1}}, {"name": "Focus Save", "modifiers": {"save_bonus": {"dex": 1}}, "concentration": True, "duration": {"unit": "hour", "value": 1}}])
        self.resolve("focus_one")
        self.assertEqual(2, len(self.e.rules_dispatch("list_effects", "c", {"actor_kind": "character", "actor_id": "hero"})))
        self.define("focus_two", "utility", targeting={"mode": "self"}, effects=[{"name": "New Focus", "modifiers": {"ac_bonus": 2}, "concentration": True, "duration": {"unit": "hour", "value": 1}}])
        self.resolve("focus_two")
        effects = self.e.rules_dispatch("list_effects", "c", {"actor_kind": "character", "actor_id": "hero"})
        self.assertEqual(["New Focus"], [e["name"] for e in effects])

    def test_high_damage_concentration_dc_is_not_capped_at_40(self):
        self.define("focus", "utility", targeting={"mode": "self"}, effects=[{"name": "Focus", "concentration": True, "duration": {"unit": "hour", "value": 1}}])
        self.resolve("focus")
        self.define("massive", "damage", damage=[{"formula": "100", "type": "force"}], targeting={"mode": "single"})
        result = self.resolve("massive", actor_kind="npc", actor_id="gob", targets=[{"kind": "character", "id": "hero"}])["results"][0]["damage"]
        self.assertEqual(50, result["concentration"]["dc"])
        self.assertFalse(result["concentration"]["check"]["success"])
        self.assertEqual([], self.e.rules_dispatch("list_effects", "c", {"actor_kind": "character", "actor_id": "hero"}))

    def test_condition_owned_by_multiple_effects_is_removed_only_after_last_ends(self):
        self.define("poison_a", "utility", targeting={"mode": "single"}, effects=[{"name": "Poison A", "condition": "poisoned", "stacking": "stack"}])
        self.define("poison_b", "utility", targeting={"mode": "single"}, effects=[{"name": "Poison B", "condition": "poisoned", "stacking": "stack"}])
        a = self.resolve("poison_a", targets=[{"kind": "npc", "id": "gob"}])["results"][0]["effects"][0]
        b = self.resolve("poison_b", targets=[{"kind": "npc", "id": "gob"}])["results"][0]["effects"][0]
        self.e.rules_dispatch("end_effect", "c", {"effect_id": a["effect_id"]})
        self.assertIn("poisoned", self.e.get_npc("c", "gob")["conditions"])
        self.e.rules_dispatch("end_effect", "c", {"effect_id": b["effect_id"]})
        self.assertNotIn("poisoned", self.e.get_npc("c", "gob")["conditions"])

    def test_concurrent_rules_resource_consumption_allows_exactly_one_success(self):
        self.e.rules_dispatch("set_resource", "c", {"actor_kind":"character","actor_id":"hero","resource_key":"single_charge","current":1,"maximum":1,"recovery":"never"})
        self.define("single_use", "utility", activation="none", targeting={"mode":"self"}, consumption=[{"resource":"single_charge","amount":1}])
        def use_once(_):
            try:
                self.resolve("single_use")
                return "success"
            except ValueError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=12) as pool:
            results=list(pool.map(use_once, range(24)))
        self.assertEqual(1, results.count("success"), results)
        self.assertTrue(all(x=="success" or "insufficient resource" in x for x in results))
        resource=self.e.rules_dispatch("get_actor_rules","c",{"actor_kind":"character","actor_id":"hero"})["resources"][0]
        self.assertEqual(0,resource["current_value"])
        with self.e._db() as db:
            count=db.execute("SELECT COUNT(*) n FROM events WHERE campaign_id='c' AND event_type='rule_activity' AND json_extract(payload_json,'$.activity_id')='single_use'").fetchone()["n"]
        self.assertEqual(1,count)

    def test_dawn_recovery_crosses_each_elapsed_dawn_without_profile_requirement(self):
        self.e.upsert_npc("c","dawn_npc","Dawn NPC",hp=5,max_hp=5,ac=10,location="arena")
        self.e.rules_dispatch("set_resource","c",{"actor_kind":"npc","actor_id":"dawn_npc","resource_key":"daily_charge","current":0,"maximum":5,"recovery":"dawn","recovery_amount":1})
        self.e.advance_world("c",1,simulate=False)
        self.assertEqual(0,self.e.rules_dispatch("get_actor_rules","c",{"actor_kind":"npc","actor_id":"dawn_npc"})["resources"][0]["current_value"])
        self.e.advance_world("c",2*24*60,simulate=False)
        self.assertEqual(2,self.e.rules_dispatch("get_actor_rules","c",{"actor_kind":"npc","actor_id":"dawn_npc"})["resources"][0]["current_value"])

    def test_rest_failure_after_world_tick_rolls_back_time_and_recovery(self):
        self.e.apply_hp_delta("c","character","hero",-5,"test")
        self.e.rules_dispatch("define_reaction","c",{
            "reaction_id":"bad_rest_reaction","owner_kind":"character","owner_id":"hero","trigger":"on_rest","name":"Bad Rest Reaction",
            "conditions":{"rest_type":"long"},"consumption":[{"resource":"missing_rest_resource","amount":1}],"effect":{},"selection_mode":"automatic"
        })
        before=self.e.get_campaign("c"); before_hp=self.e.get_character("c","hero")["hp"]
        with self.assertRaisesRegex(ValueError,"insufficient resource"):
            self.e.rules_dispatch("rest","c",{"actor_kind":"character","actor_id":"hero","rest_type":"long","simulate_world":False})
        after=self.e.get_campaign("c")
        self.assertEqual(before["world_time"],after["world_time"])
        self.assertEqual(before["revision"],after["revision"])
        self.assertEqual(before_hp,self.e.get_character("c","hero")["hp"])

    def test_summon_joins_initiative_after_owner_and_is_removed_cleanly(self):
        combat=self.e.start_combat("c","summon_turn","arena",[{"kind":"character","id":"hero"},{"kind":"npc","id":"gob"}],positions=[{"kind":"character","id":"hero","x":0,"y":0},{"kind":"npc","id":"gob","x":3,"y":0}])
        owner=combat["current_turn"]
        self.define("summon_turn_activity","summon",activation="action",targeting={"mode":"self"},special={"summons":[{"name":"Spirit","hp":5,"ac":12,"x":1,"y":1}],"duration":{"unit":"manual"},"initiative_mode":"after_owner"})
        out=self.resolve("summon_turn_activity",actor_kind=owner["kind"],actor_id=owner["id"],combat_id="summon_turn")
        created=out["summon"]["created"][0]; npc_id=created["npc_id"]; effect_id=created["effect_id"]
        updated=self.e.get_combat("c","summon_turn"); ids=[x["id"] for x in updated["initiative"]]; owner_index=ids.index(owner["id"])
        self.assertEqual(npc_id,ids[owner_index+1])
        next_state=self.e.next_turn("c","summon_turn"); self.assertEqual(npc_id,next_state["current_turn"]["id"])
        self.e.rules_dispatch("end_effect","c",{"effect_id":effect_id,"reason":"dismissed"})
        cleaned=self.e.get_combat("c","summon_turn")
        self.assertNotIn(npc_id,[x["id"] for x in cleaned["initiative"]]); self.assertNotIn(npc_id,[x["id"] for x in cleaned["participants"]])
        with self.assertRaises(KeyError): self.e.get_npc("c",npc_id)

    def test_combat_movement_consumes_path_cost_and_rejects_blocked_or_excess_path(self):
        combat=self.e.start_combat("c","movefight","arena",[{"kind":"character","id":"hero"},{"kind":"npc","id":"gob"}],positions=[{"kind":"character","id":"hero","x":0,"y":0},{"kind":"npc","id":"gob","x":5,"y":5}],terrain=[{"x":1,"y":0,"kind":"mud","difficult":True},{"x":3,"y":0,"kind":"wall","blocks_los":True}])
        current=combat["current_turn"]
        # Ensure the current actor begins at 0,0 for deterministic path assertions.
        self.e.set_combat_position("c","movefight",current["kind"],current["id"],0,0)
        out=self.e.rules_dispatch("move","c",{"actor_kind":current["kind"],"actor_id":current["id"],"combat_id":"movefight","path":[{"x":1,"y":0},{"x":2,"y":0}]})
        self.assertEqual(3,out["movement_cost"]); self.assertEqual(3,out["movement_remaining"]); self.assertEqual({"x":2,"y":0},out["position"])
        with self.assertRaisesRegex(ValueError,"blocked"):
            self.e.rules_dispatch("move","c",{"actor_kind":current["kind"],"actor_id":current["id"],"combat_id":"movefight","path":[{"x":3,"y":0}]})
        with self.assertRaisesRegex(ValueError,"insufficient movement"):
            self.e.rules_dispatch("move","c",{"actor_kind":current["kind"],"actor_id":current["id"],"combat_id":"movefight","path":[{"x":2,"y":1},{"x":2,"y":2},{"x":2,"y":3},{"x":2,"y":4}]})
        with self.e._db() as db:
            pos=db.execute("SELECT x,y FROM combat_positions WHERE campaign_id='c' AND combat_id='movefight' AND actor_kind=? AND actor_id=?",(current["kind"],current["id"])).fetchone()
        self.assertEqual((2,0),(pos["x"],pos["y"]))

    def test_world_teleport_removes_actor_from_old_active_scene(self):
        self.e.start_scene("c","tele_scene","arena",entities=[{"kind":"character","id":"hero"},{"kind":"npc","id":"gob"}])
        self.define("world_gate","teleport",targeting={"mode":"self"},special={"location_id":"tower"})
        result=self.resolve("world_gate")["results"][0]["teleport"]
        self.assertEqual("removed",result["scene_update"]); self.assertEqual("tower",self.e.get_character("c","hero")["location"])
        scene=self.e.get_scene("c","tele_scene")
        self.assertNotIn("hero",[x["actor_id"] for x in scene["entities"]])

    def test_world_time_effect_expiry(self):
        self.define("ward", "utility", targeting={"mode": "self"}, effects=[{"name": "Ward", "modifiers": {"ac_bonus": 2}, "duration": {"unit": "minute", "value": 10}}])
        self.resolve("ward")
        self.e.advance_world("c", 11, simulate=False)
        self.assertEqual([], self.e.rules_dispatch("list_effects", "c", {"actor_kind": "character", "actor_id": "hero"}))

    def test_reaction_changes_final_attack_ac_and_spends_reaction(self):
        self.e.rules_dispatch("define_reaction", "c", {"reaction_id": "shield", "owner_kind": "character", "owner_id": "hero", "trigger": "after_attack_roll", "name": "Shield", "conditions": {"attack_would_hit": True}, "effect": {"effect": {"name": "Shield", "modifiers": {"ac_bonus": 50}, "duration": {"unit": "turn_start"}}}, "priority": 1})
        self.define("claw", "attack", attack={"ability": "str", "proficient": False, "bonus": 20}, damage=[{"formula": "5", "type": "slashing"}], targeting={"mode": "single"})
        original = self.e._resolve_check_db
        try:
            self.e._resolve_check_db = lambda db, campaign_id, modifier, dc, mode, namespace: {"mode": mode, "d20_rolls": [10], "natural": 10, "modifier": modifier, "total": 30, "dc": dc, "success": True}
            out = self.resolve("claw", actor_kind="npc", actor_id="gob", targets=[{"kind": "character", "id": "hero"}])
        finally:
            self.e._resolve_check_db = original
        result=out["results"][0]
        self.assertFalse(result["hit"]); self.assertEqual(64, result["target_ac"])
        self.assertEqual("shield", result["reactions"]["after_attack_roll"]["applied"][0]["reaction_id"])

    def test_prompt_reaction_returns_explicit_unsupported_error(self):
        self.e.rules_dispatch("define_reaction", "c", {"reaction_id": "choose", "owner_kind": "character", "owner_id": "hero", "trigger": "before_activity", "name": "Choose", "selection_mode": "prompt"})
        self.define("wait", "utility", targeting={"mode": "self"})
        with self.assertRaisesRegex(ValueError, "player-choice continuation"):
            self.resolve("wait")

    def test_action_economy_blocks_second_action_and_resets_next_turn(self):
        combat=self.e.start_combat("c", "fight", "arena", [{"kind": "character", "id": "hero"}, {"kind": "npc", "id": "gob"}])
        current=combat["current_turn"]
        self.define("act", "utility", activation="action", targeting={"mode": "self"})
        self.resolve("act", actor_kind=current["kind"], actor_id=current["id"], combat_id="fight")
        with self.assertRaisesRegex(ValueError, "already spent"):
            self.resolve("act", actor_kind=current["kind"], actor_id=current["id"], combat_id="fight")
        self.e.next_turn("c", "fight"); self.e.next_turn("c", "fight")
        self.resolve("act", actor_kind=current["kind"], actor_id=current["id"], combat_id="fight")

    def test_cover_long_range_and_line_of_sight(self):
        self.e.start_combat("c", "grid", "arena", [{"kind": "character", "id": "hero"}, {"kind": "npc", "id": "gob"}], positions=[{"kind": "character", "id": "hero", "x": 0, "y": 0}, {"kind": "npc", "id": "gob", "x": 5, "y": 0, "cover": "half"}])
        self.define("bow", "attack", activation="none", attack={"ability": "dex", "proficient": True, "bonus": 20}, damage=[{"formula": "1", "type": "piercing"}], targeting={"mode": "single", "range_cells": 3, "long_range_cells": 8})
        result=self.resolve("bow", targets=[{"kind": "npc", "id": "gob"}], combat_id="grid")["results"][0]
        self.assertTrue(result["spatial"]["long_range"]); self.assertEqual(2,result["spatial"]["cover_bonus"]); self.assertEqual("disadvantage",result["attack"]["mode"])
        self.e.set_combat_terrain("c", "grid", 2, 0, kind="wall", blocks_los=True)
        with self.assertRaisesRegex(ValueError, "total cover"):
            self.resolve("bow", targets=[{"kind": "npc", "id": "gob"}], combat_id="grid")

    def test_area_radius_resolves_target_set_before_damage(self):
        self.e.start_combat("c", "area", "arena", [{"kind": "character", "id": "hero"}, {"kind": "npc", "id": "gob"}], positions=[{"kind": "character", "id": "hero", "x": 0, "y": 0}, {"kind": "npc", "id": "gob", "x": 2, "y": 2}])
        self.define("burst", "save", activation="none", save={"ability": "dex", "dc": 99, "on_success": "half"}, damage=[{"formula": "3", "type": "fire"}], targeting={"mode": "area", "shape": "radius", "radius_cells": 2, "max_targets": 10})
        out=self.resolve("burst", combat_id="area", center={"x": 1, "y": 1})
        self.assertEqual(2,len(out["targets"])); self.assertFalse(out["target_report"]["truncated"])
        self.assertEqual(27,self.e.get_character("c","hero")["hp"]); self.assertEqual(17,self.e.get_npc("c","gob")["hp"])

    def test_explicit_target_over_cap_fails_instead_of_truncating(self):
        for i in range(3): self.e.upsert_npc("c", f"n{i}", f"N{i}", hp=2, max_hp=2, ac=10, location="arena")
        self.define("two", "damage", damage=[{"formula":"1","type":"force"}], targeting={"mode":"multi","max_targets":2})
        with self.assertRaisesRegex(ValueError,"target cap exceeded"):
            self.resolve("two",targets=[{"kind":"npc","id":f"n{i}"} for i in range(3)])

    def test_summon_materializes_and_combat_end_removes(self):
        combat=self.e.start_combat("c","summonfight","arena",[{"kind":"character","id":"hero"},{"kind":"npc","id":"gob"}])
        current=combat["current_turn"]
        self.define("summon","summon",activation="action",targeting={"mode":"self"},special={"summons":[{"name":"Spirit","hp":5,"ac":12,"x":1,"y":1}],"duration":{"unit":"combat_end"}})
        out=self.resolve("summon",actor_kind=current["kind"],actor_id=current["id"],combat_id="summonfight")
        npc_id=out["summon"]["created"][0]["npc_id"]; self.assertEqual("Spirit",self.e.get_npc("c",npc_id)["name"])
        self.e.end_combat("c","summonfight")
        with self.assertRaises(KeyError): self.e.get_npc("c",npc_id)

    def test_transform_restores_snapshot_on_effect_end(self):
        self.define("form","transform",targeting={"mode":"self"},special={"transform":{"max_hp":50,"hp":50,"ac":18},"duration":{"unit":"manual"},"name":"Beast Form"})
        out=self.resolve("form"); effect_id=out["results"][0]["transformation"]["effect_id"]
        self.assertEqual(50,self.e.get_character("c","hero")["max_hp"])
        self.e.rules_dispatch("end_effect","c",{"effect_id":effect_id,"reason":"form ended"})
        restored=self.e.get_character("c","hero"); self.assertEqual(30,restored["max_hp"]); self.assertEqual(14,restored["ac"])

    def test_teleport_updates_world_location_and_combat_cell(self):
        self.define("gate","teleport",targeting={"mode":"self"},special={"location_id":"tower"})
        self.resolve("gate"); self.assertEqual("tower",self.e.get_character("c","hero")["location"])
        self.e.move_actor("c","character","hero","arena","return")
        self.e.start_combat("c","tele","arena",[{"kind":"character","id":"hero"},{"kind":"npc","id":"gob"}],positions=[{"kind":"character","id":"hero","x":0,"y":0},{"kind":"npc","id":"gob","x":3,"y":3}])
        self.define("step","teleport",targeting={"mode":"self"})
        self.resolve("step",combat_id="tele",center={"x":2,"y":1})
        with self.e._db() as db: pos=db.execute("SELECT x,y FROM combat_positions WHERE campaign_id='c' AND combat_id='tele' AND actor_id='hero'").fetchone()
        self.assertEqual((2,1),(pos["x"],pos["y"]))

    def test_long_rest_advances_world_recovers_and_expires_effects(self):
        self.e.apply_hp_delta("c","character","hero",-10,"test")
        self.e.rules_dispatch("set_resource","c",{"actor_kind":"character","actor_id":"hero","resource_key":"surge","current":0,"maximum":1,"recovery":"short_rest"})
        self.define("restbuff","utility",targeting={"mode":"self"},effects=[{"name":"Rest Buff","modifiers":{"ac_bonus":1},"duration":{"unit":"long_rest"}}]); self.resolve("restbuff")
        before=self.e.get_campaign("c")["world_time"]; out=self.e.rules_dispatch("rest","c",{"actor_kind":"character","actor_id":"hero","rest_type":"long","simulate_world":False})
        self.assertNotEqual(before,out["world"]["world_time"]); self.assertEqual(30,self.e.get_character("c","hero")["hp"]); self.assertEqual([],self.e.rules_dispatch("list_effects","c",{"actor_kind":"character","actor_id":"hero"}))
        self.assertEqual(1,self.e.rules_dispatch("get_actor_rules","c",{"actor_kind":"character","actor_id":"hero"})["resources"][0]["current_value"])

    def test_short_rest_hit_dice(self):
        self.e.apply_hp_delta("c","character","hero",-10,"test")
        self.e.rules_dispatch("set_resource","c",{"actor_kind":"character","actor_id":"hero","resource_key":"hit_dice","current":2,"maximum":2,"recovery":"long_rest"})
        out=self.e.rules_dispatch("rest","c",{"actor_kind":"character","actor_id":"hero","rest_type":"short","hit_dice_count":1,"hit_die_formula":"4","simulate_world":False})
        self.assertEqual(4,out["healed"]); self.assertEqual(24,self.e.get_character("c","hero")["hp"])

    def test_death_save_outcomes_are_persistent(self):
        self.e.apply_hp_delta("c","character","hero",-30,"down")
        original=self.e._roll_dice_db
        try:
            self.e._roll_dice_db=lambda db,campaign_id,expression,namespace: type("R",(),{"total":10})()
            one=self.e.rules_dispatch("death_save","c",{"actor_kind":"character","actor_id":"hero"})
            self.assertEqual(1,one["successes"])
            self.e._roll_dice_db=lambda db,campaign_id,expression,namespace: type("R",(),{"total":20})()
            two=self.e.rules_dispatch("death_save","c",{"actor_kind":"character","actor_id":"hero"})
            self.assertEqual("critical_success_revived",two["outcome"]); self.assertEqual(1,self.e.get_character("c","hero")["hp"])
        finally: self.e._roll_dice_db=original

    def test_advancement_grants_features_and_resources(self):
        self.e.rules_dispatch("define_object","c",{"object_id":"feature_x","name":"Feature X","object_kind":"class_feature","rules_version":"both"})
        self.define("feature_action","utility",object_id="feature_x",rules_version="both",targeting={"mode":"self"})
        with self.assertRaisesRegex(ValueError,"does not possess"):
            self.resolve("feature_action")
        self.e.rules_dispatch("define_advancement","c",{"advancement_id":"fighter6","class_id":"fighter","level":6,"rules_version":"2024","grant_objects":["feature_x"],"resources":{"surge":{"max":1,"recovery":"short_rest"}}})
        self.e.world_systems_dispatch("award_xp","c",{"character_id":"hero","amount":7500,"reason":"advancement test"})
        self.e.rules_dispatch("apply_advancement","c",{"actor_kind":"character","actor_id":"hero","class_id":"fighter","level":6})
        self.resolve("feature_action"); rules=self.e.rules_dispatch("get_actor_rules","c",{"actor_kind":"character","actor_id":"hero"}); self.assertEqual("feature_x",rules["objects"][0]["id"]); self.assertEqual(6,self.e.get_character("c","hero")["level"])

    def test_rules_version_mismatch_fails_explicitly(self):
        self.define("legacy","utility",rules_version="2014",targeting={"mode":"self"})
        with self.assertRaisesRegex(ValueError,"campaign uses 2024"):
            self.resolve("legacy")
        self.e.rules_dispatch("configure","c",{"rules_version":"2014"}); self.resolve("legacy")

    def test_legacy_resolve_attack_uses_shared_temp_hp_and_resistance(self):
        self.e.rules_dispatch("set_actor_profile","c",{"actor_kind":"npc","actor_id":"gob","temp_hp":2,"resistances":["fire"]})
        original=self.e._resolve_check_db
        try:
            self.e._resolve_check_db=lambda db,campaign_id,modifier,dc,mode,namespace:{"mode":mode,"d20_rolls":[10],"natural":10,"modifier":modifier,"total":40,"dc":dc,"success":True}
            out=self.e.resolve_attack("c","character","hero","npc","gob",attack_bonus=30,damage_expression="10",damage_type="fire")
        finally: self.e._resolve_check_db=original
        self.assertEqual(5,out["damage_application"]["applied_total"]); self.assertEqual(2,out["damage_application"]["temp_hp_absorbed"]); self.assertEqual(17,self.e.get_npc("c","gob")["hp"])

    def test_normalized_scene_result_runs_world_cascade_same_transaction(self):
        self.e.save_simulation_reaction("c","ritual_world","ritual_failure",[{"type":"world_state","scope_type":"location","scope_id":"arena","key":"corruption","value":1}],repeat_policy="once_per_cascade")
        self.define("badritual","utility",targeting={"mode":"self"},world_event_type="ritual_failure")
        out=self.resolve("badritual")
        self.assertGreaterEqual(out["world_cascade_events"],1)
        self.assertTrue(any(x["key"]=="corruption" and x["value"]==1 for x in self.e.get_world_state("c","location","arena")))

    def test_schema_8_forward_migration_preserves_campaign(self):
        old=Path(self.tmp.name)/"old.sqlite3"; e=WorldEngine(old); e.ensure_campaign("legacy","Legacy")
        with e._write_db() as db:
            for table in ("rule_transform_snapshots","rule_summons","rule_advancements","rule_turn_state","rule_reactions","rule_effects","rule_resources","rule_actor_objects","rule_activities","rule_objects","rule_actor_profiles","rules_config"):
                db.execute(f"DROP TABLE IF EXISTS {table}")
            db.execute("PRAGMA user_version=8")
        migrated=WorldEngine(old)
        self.assertEqual("Legacy",migrated.get_campaign("legacy")["name"])
        with migrated._db() as db:
            self.assertEqual(e.SCHEMA_VERSION,db.execute("PRAGMA user_version").fetchone()[0]); self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rule_activities'").fetchone())

    def test_seeded_rules_replay_continues_after_reopen(self):
        self.define("checkdamage","damage",damage=[{"formula":"1d6","type":"force"}],targeting={"mode":"single"})
        first=self.resolve("checkdamage",targets=[{"kind":"npc","id":"gob"}])["results"][0]["damage"]["parts"][0]["roll"]["rolls"]
        reopened=WorldEngine(self.path)
        second=reopened.rules_dispatch("resolve_activity","c",{"activity_id":"checkdamage","actor_kind":"character","actor_id":"hero","targets":[{"kind":"npc","id":"gob"}]})["results"][0]["damage"]["parts"][0]["roll"]["rolls"]
        other=Path(self.tmp.name)/"other.sqlite3"; e2=WorldEngine(other); e2.ensure_campaign("c","Rules","1492-01-01T08:00:00+00:00"); e2.set_simulation_seed("c",1701); e2.upsert_location("c","arena","Arena"); e2.upsert_character("c","hero","Hero",level=5,hp=30,max_hp=30,ac=14,location="arena",abilities={"str":3,"dex":2,"con":2,"int":4},proficiency_bonus=3); e2.upsert_npc("c","gob","Goblin",hp=20,max_hp=20,ac=12,location="arena",stats={"con_mod":1}); e2.rules_dispatch("configure","c",{"rules_version":"2024"}); e2.rules_dispatch("define_activity","c",{"activity_id":"checkdamage","name":"checkdamage","activity_type":"damage","damage":[{"formula":"1d6","type":"force"}],"targeting":{"mode":"single"}}); a=e2.rules_dispatch("resolve_activity","c",{"activity_id":"checkdamage","actor_kind":"character","actor_id":"hero","targets":[{"kind":"npc","id":"gob"}]})["results"][0]["damage"]["parts"][0]["roll"]["rolls"]; b=e2.rules_dispatch("resolve_activity","c",{"activity_id":"checkdamage","actor_kind":"character","actor_id":"hero","targets":[{"kind":"npc","id":"gob"}]})["results"][0]["damage"]["parts"][0]["roll"]["rolls"]
        self.assertEqual([first,second],[a,b])

    def test_reaction_cannot_be_reused_in_same_round(self):
        self.e.start_combat("c","react","arena",[{"kind":"character","id":"hero"},{"kind":"npc","id":"gob"}],positions=[{"kind":"character","id":"hero","x":0,"y":0},{"kind":"npc","id":"gob","x":1,"y":0}])
        self.e.rules_dispatch("define_reaction","c",{"reaction_id":"shield_once","owner_kind":"character","owner_id":"hero","trigger":"after_attack_roll","name":"Shield Once","conditions":{"attack_would_hit":True},"effect":{"effect":{"name":"Shield Once","modifiers":{"ac_bonus":40},"duration":{"unit":"turn_start"}}}})
        self.define("poke_attack","attack",activation="none",attack={"ability":"str","proficient":False,"bonus":20},damage=[{"formula":"1","type":"piercing"}],targeting={"mode":"single"})
        original=self.e._resolve_check_db
        try:
            self.e._resolve_check_db=lambda db,campaign_id,modifier,dc,mode,namespace:{"mode":mode,"d20_rolls":[10],"natural":10,"modifier":modifier,"total":30,"dc":dc,"success":True}
            first=self.resolve("poke_attack",actor_kind="npc",actor_id="gob",targets=[{"kind":"character","id":"hero"}],combat_id="react")["results"][0]
            second=self.resolve("poke_attack",actor_kind="npc",actor_id="gob",targets=[{"kind":"character","id":"hero"}],combat_id="react")["results"][0]
        finally: self.e._resolve_check_db=original
        self.assertEqual(1,len(first["reactions"]["after_attack_roll"]["applied"]))
        self.assertEqual(0,len(second["reactions"]["after_attack_roll"]["applied"]))
        with self.e._db() as db: state=db.execute("SELECT reaction_available FROM rule_turn_state WHERE campaign_id='c' AND combat_id='react' AND actor_id='hero'").fetchone()
        self.assertEqual(0,state["reaction_available"])

    def test_effect_advantage_and_disadvantage_cancel(self):
        self.define("adv","utility",targeting={"mode":"self"},effects=[{"name":"Advantage","modifiers":{"advantage":["attack"]},"stacking":"stack"}]); self.resolve("adv")
        self.define("strike_mode","attack",activation="none",attack={"ability":"str","proficient":False,"bonus":20},damage=[{"formula":"1","type":"slashing"}],targeting={"mode":"single"})
        self.assertEqual("advantage",self.resolve("strike_mode",targets=[{"kind":"npc","id":"gob"}])["results"][0]["attack"]["mode"])
        self.define("dis","utility",targeting={"mode":"self"},effects=[{"name":"Disadvantage","modifiers":{"disadvantage":["attack"]},"stacking":"stack"}]); self.resolve("dis")
        self.assertEqual("normal",self.resolve("strike_mode",targets=[{"kind":"npc","id":"gob"}])["results"][0]["attack"]["mode"])

    def test_turn_start_and_combat_end_effect_expiry(self):
        combat=self.e.start_combat("c","expiry","arena",[{"kind":"character","id":"hero"},{"kind":"npc","id":"gob"}]); current=combat["current_turn"]
        self.define("turn_effect","utility",activation="none",targeting={"mode":"self"},effects=[{"name":"Until Turn","duration":{"unit":"turn_start"}}]); self.resolve("turn_effect",actor_kind=current["kind"],actor_id=current["id"],combat_id="expiry")
        self.define("combat_effect","utility",activation="none",targeting={"mode":"self"},effects=[{"name":"Until Combat End","duration":{"unit":"combat_end"}}]); self.resolve("combat_effect",actor_kind=current["kind"],actor_id=current["id"],combat_id="expiry")
        self.e.next_turn("c","expiry"); self.e.next_turn("c","expiry")
        names=[e["name"] for e in self.e.rules_dispatch("list_effects","c",{"actor_kind":current["kind"],"actor_id":current["id"]})]
        self.assertNotIn("Until Turn",names); self.assertIn("Until Combat End",names)
        self.e.end_combat("c","expiry")
        self.assertEqual([],self.e.rules_dispatch("list_effects","c",{"actor_kind":current["kind"],"actor_id":current["id"]}))

    def test_invalid_short_rest_does_not_advance_world(self):
        before=self.e.get_campaign("c")["world_time"]
        with self.assertRaisesRegex(ValueError,"insufficient hit dice"):
            self.e.rules_dispatch("rest","c",{"actor_kind":"character","actor_id":"hero","rest_type":"short","hit_dice_count":1,"hit_die_formula":"1d10","simulate_world":False})
        self.assertEqual(before,self.e.get_campaign("c")["world_time"])

    def test_three_death_save_successes_stabilize_and_three_failures_kill(self):
        self.e.apply_hp_delta("c","character","hero",-30,"down")
        original=self.e._roll_dice_db
        try:
            self.e._roll_dice_db=lambda db,campaign_id,expression,namespace:type("R",(),{"total":10})()
            for _ in range(3): result=self.e.rules_dispatch("death_save","c",{"actor_kind":"character","actor_id":"hero"})
            self.assertEqual("stable",result["outcome"]); self.assertTrue(result["stable"])
            self.e.upsert_character("c","other","Other",hp=1,max_hp=10,ac=10,location="arena"); self.e.apply_hp_delta("c","character","other",-1,"down")
            self.e._roll_dice_db=lambda db,campaign_id,expression,namespace:type("R",(),{"total":1})()
            first=self.e.rules_dispatch("death_save","c",{"actor_kind":"character","actor_id":"other"}); self.assertEqual(2,first["failures"])
            second=self.e.rules_dispatch("death_save","c",{"actor_kind":"character","actor_id":"other"}); self.assertEqual("dead",second["outcome"]); self.assertEqual("dead",self.e.get_character("c","other")["status"])
        finally: self.e._roll_dice_db=original

    def test_healing_caps_at_max_and_rejects_dead_target(self):
        self.e.apply_hp_delta("c","character","hero",-2,"hurt"); self.define("heal","heal",healing=[{"formula":"10","type":"healing"}],targeting={"mode":"single"})
        result=self.resolve("heal",targets=[{"kind":"character","id":"hero"}])["results"][0]["healing"]
        self.assertEqual(2,result["actual_healing"]); self.assertEqual(30,result["new_hp"])
        self.e.set_actor_status("c","npc","gob","dead",reason="test")
        self.define("revive_wrong","heal",healing=[{"formula":"10","type":"healing"}],targeting={"mode":"single","allow_dead":True})
        with self.assertRaisesRegex(ValueError,"ordinary healing"):
            self.resolve("revive_wrong",targets=[{"kind":"npc","id":"gob"}])

    def test_before_damage_reaction_resistance_is_recomputed(self):
        self.e.rules_dispatch("define_reaction","c",{"reaction_id":"fire_guard","owner_kind":"npc","owner_id":"gob","trigger":"before_damage","name":"Fire Guard","conditions":{"damage_type":"fire"},"effect":{"effect":{"name":"Fire Resistance","modifiers":{"resistances":["fire"]},"duration":{"unit":"turn_start"}}}})
        self.define("fire_hit","damage",damage=[{"formula":"10","type":"fire"}],targeting={"mode":"single"})
        result=self.resolve("fire_hit",targets=[{"kind":"npc","id":"gob"}])["results"][0]["damage"]
        self.assertEqual(5,result["applied_total"]); self.assertEqual("resistant",result["parts"][0]["mitigation"])

    def test_context_and_snapshot_surface_rules_state(self):
        self.e.rules_dispatch("set_resource","c",{"actor_kind":"character","actor_id":"hero","resource_key":"focus","current":1,"maximum":2})
        context=self.e.get_world_context("c","arena"); self.assertEqual("2024",context["rules_state"]["config"]["rules_version"]); hero=next(x for x in context["rules_state"]["actors"] if x["id"]=="hero"); self.assertEqual("focus",hero["resources"][0]["resource_key"])
        snapshot=self.e.snapshot("c"); self.assertIn("rules_kernel",snapshot); self.assertEqual("focus",snapshot["rules_kernel"]["resources"][0]["resource_key"])

    def test_area_target_cap_is_reported_when_grid_contains_more(self):
        participants=[{"kind":"character","id":"hero"},{"kind":"npc","id":"gob"}]; positions=[{"kind":"character","id":"hero","x":0,"y":0},{"kind":"npc","id":"gob","x":1,"y":0}]
        for i in range(3):
            self.e.upsert_npc("c",f"area{i}",f"Area {i}",hp=5,max_hp=5,ac=10,location="arena"); participants.append({"kind":"npc","id":f"area{i}"}); positions.append({"kind":"npc","id":f"area{i}","x":i+1,"y":1})
        self.e.start_combat("c","caparea","arena",participants,positions=positions)
        self.define("cap_burst","damage",activation="none",damage=[{"formula":"1","type":"force"}],targeting={"mode":"area","shape":"radius","radius_cells":10,"max_targets":2})
        out=self.resolve("cap_burst",combat_id="caparea",center={"x":0,"y":0})
        self.assertEqual(2,len(out["targets"])); self.assertTrue(out["target_report"]["truncated"]); self.assertEqual(2,out["target_report"]["cap"])

    def test_manual_summon_expiry_cleans_scene_and_rules_rows(self):
        self.e.start_scene("c","summonscene","arena",entities=[{"kind":"character","id":"hero"}])
        self.define("scene_summon","summon",targeting={"mode":"self"},special={"summons":[{"name":"Wisp","hp":1,"ac":10}],"duration":{"unit":"manual"}})
        out=self.resolve("scene_summon"); summon=out["summon"]["created"][0]; self.e.rules_dispatch("end_effect","c",{"effect_id":summon["effect_id"]})
        with self.assertRaises(KeyError): self.e.get_npc("c",summon["npc_id"])
        scene=self.e.get_scene("c","summonscene"); self.assertFalse(any(x["actor_id"]==summon["npc_id"] for x in scene["entities"]))

    def test_unsupported_transform_field_rolls_back(self):
        self.define("badform","transform",targeting={"mode":"self"},special={"transform":{"unknown_stat":99},"duration":{"unit":"manual"}})
        before=self.e.get_character("c","hero")
        with self.assertRaisesRegex(ValueError,"unsupported transform"):
            self.resolve("badform")
        after=self.e.get_character("c","hero"); self.assertEqual(before["hp"],after["hp"]); self.assertEqual(before["ac"],after["ac"])



if __name__ == "__main__":
    unittest.main()
