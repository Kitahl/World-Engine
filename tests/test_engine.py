from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import random
import sqlite3
import tempfile
import unittest

from world_engine import WorldEngine


class WorldEngineV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.sqlite3"
        self.engine = WorldEngine(self.path, rng=random.Random(7))
        self.engine.ensure_campaign("c1", "Test")

    def tearDown(self):
        self.tmp.cleanup()

    def seed_actors(self):
        self.engine.upsert_character("c1", "hero", "Hero", level=3, hp=20, max_hp=20, ac=15, location="Town", abilities={"dex": 2}, resources={"spell_slots_1": 2})
        self.engine.upsert_npc("c1", "goblin", "Goblin", hp=12, max_hp=12, ac=12, location="Town", stats={"dex_mod": 2})

    def test_state_persists_across_engine_reopen(self):
        self.seed_actors()
        self.engine.apply_hp_delta("c1", "character", "hero", -4, "trap")
        reopened = WorldEngine(self.path, rng=random.Random(8))
        self.assertEqual(16, reopened.get_character("c1", "hero")["hp"])
        self.assertGreater(reopened.get_campaign("c1")["revision"], 0)

    def test_roll_bounds(self):
        for _ in range(30):
            r = self.engine.roll_dice("2d6+3")
            self.assertGreaterEqual(r.total, 5)
            self.assertLessEqual(r.total, 15)

    def test_world_context_location_filter(self):
        self.seed_actors()
        self.engine.upsert_npc("c1", "far", "Far NPC", hp=5, max_hp=5, ac=10, location="Forest")
        ctx = self.engine.get_world_context("c1", "Town")
        self.assertEqual(["hero"], [x["id"] for x in ctx["characters"]])
        self.assertEqual(["goblin"], [x["id"] for x in ctx["npcs"]])

    def test_attack_mutates_hp_and_logs(self):
        self.seed_actors()
        result = self.engine.resolve_attack("c1", "character", "hero", "npc", "goblin", attack_bonus=20, damage_expression="1d6+2", attack_name="sword")
        self.assertTrue(result["hit"])
        self.assertLess(self.engine.get_npc("c1", "goblin")["hp"], 12)
        self.assertEqual("attack", self.engine.recent_events("c1", 1)[0]["event_type"])

    def test_condition_persists(self):
        self.seed_actors()
        self.engine.set_condition("c1", "character", "hero", "poisoned", True)
        self.assertIn("poisoned", self.engine.get_character("c1", "hero")["conditions"])
        reopened = WorldEngine(self.path)
        self.assertIn("poisoned", reopened.get_character("c1", "hero")["conditions"])

    def test_resources_and_inventory_persist(self):
        self.seed_actors()
        self.engine.update_character_resources("c1", "hero", resource_delta={"spell_slots_1": -1}, add_inventory=[{"name": "Potion"}])
        hero = self.engine.get_character("c1", "hero")
        self.assertEqual(1, hero["resources"]["spell_slots_1"])
        self.assertEqual("Potion", hero["inventory"][0]["name"])

    def test_relationship_clamps_and_persists(self):
        result = self.engine.adjust_relationship("c1", "npc:a", "hero", trust_delta=150, fear_delta=-150)
        self.assertEqual(100, result["trust"])
        self.assertEqual(-100, result["fear"])
        reopened = WorldEngine(self.path)
        self.assertEqual(100, reopened.get_relationship("c1", "npc:a", "hero")["trust"])

    def test_quest_persists(self):
        self.engine.upsert_quest("c1", "q1", "Find the relic", region="Town", objectives=["Locate vault"])
        reopened = WorldEngine(self.path)
        self.assertEqual("active", reopened.get_quest("c1", "q1")["status"])

    def test_combat_turn_state_persists(self):
        self.seed_actors()
        combat = self.engine.start_combat("c1", "cmb1", "Town", [{"kind": "character", "id": "hero"}, {"kind": "npc", "id": "goblin"}])
        self.assertEqual(2, len(combat["initiative"]))
        advanced = self.engine.next_turn("c1", "cmb1")
        reopened = WorldEngine(self.path)
        persisted = reopened.get_combat("c1", "cmb1")
        self.assertEqual(advanced["round"], persisted["round"])
        self.assertEqual(advanced["turn_index"], persisted["turn_index"])

    def test_location_and_generic_world_state_persist(self):
        self.engine.upsert_location("c1", "town_square", "Town Square", region="Town", tags=["urban"], state={"market_open": True})
        self.engine.set_world_state("c1", "location", "town_square", "crime_heat", 3)
        reopened = WorldEngine(self.path)
        self.assertTrue(reopened.get_location("c1", "town_square")["state"]["market_open"])
        values = reopened.get_world_state("c1", "location", "town_square")
        self.assertEqual(3, values[0]["value"])

    def test_move_actor_persists(self):
        self.seed_actors()
        self.engine.move_actor("c1", "character", "hero", "Forest", "travel")
        reopened = WorldEngine(self.path)
        self.assertEqual("Forest", reopened.get_character("c1", "hero")["location"])

    def test_npc_state_delta_preserves_memory(self):
        self.seed_actors()
        self.engine.update_npc_state("c1", "goblin", attitude_delta=-2, add_beliefs=["Hero is dangerous"], add_memory=[{"event": "defeated"}])
        self.engine.update_npc_state("c1", "goblin", add_memory=[{"event": "escaped"}])
        npc = self.engine.get_npc("c1", "goblin")
        self.assertEqual(-2, npc["attitude"])
        self.assertEqual(2, len(npc["memory"]))
        self.assertIn("Hero is dangerous", npc["beliefs"])

    def test_faction_delta_preserves_state(self):
        self.engine.upsert_faction("c1", "guild", "Guild", region="Town", state={"leader": "A"}, reserve_score=10)
        self.engine.adjust_faction("c1", "guild", reserve_delta=-3, state_patch={"alert": True})
        f = self.engine.get_faction("c1", "guild")
        self.assertEqual(7, f["reserve_score"])
        self.assertEqual("A", f["state"]["leader"])
        self.assertTrue(f["state"]["alert"])

    def test_world_time_weather_persist(self):
        before = self.engine.get_campaign("c1")["world_time"]
        self.engine.advance_world("c1", 180, "travel", "rain")
        reopened = WorldEngine(self.path)
        after = reopened.get_campaign("c1")
        self.assertNotEqual(before, after["world_time"])
        self.assertEqual("rain", after["weather"])


    def test_world_graph_route_and_lod(self):
        self.engine.upsert_location("c1", "a", "A", region="north", x=0, y=0)
        self.engine.upsert_location("c1", "b", "B", region="north", x=1, y=0)
        self.engine.upsert_location("c1", "c", "C", region="south", x=2, y=0)
        self.engine.upsert_location("c1", "d", "D", region="far", x=20, y=20)
        self.engine.save_location_link("c1", "a", "b", 2.0)
        self.engine.save_location_link("c1", "b", "c", 3.0)
        route = self.engine.route_locations("c1", "a", "c")
        self.assertEqual(["a", "b", "c"], route["path"])
        self.assertEqual(5.0, route["travel_hours"])
        tiers = {row["location_id"]: row["tier"] for row in self.engine.get_lod_tiers("c1", "a")}
        self.assertEqual("near", tiers["a"])
        self.assertEqual("near", tiers["b"])
        self.assertEqual("mid", tiers["c"])
        self.assertEqual("far", tiers["d"])
        ctx = self.engine.get_world_context("c1", "a", destination="c")
        self.assertEqual(["a", "b", "c"], ctx["world_graph"]["route_to_destination"]["path"])

    def test_combat_grid_cover_and_disposal(self):
        self.seed_actors()
        combat = self.engine.start_combat(
            "c1", "gridfight", "Town",
            [{"kind": "character", "id": "hero"}, {"kind": "npc", "id": "goblin"}],
            grid_width=12, grid_height=12,
            positions=[
                {"kind": "character", "id": "hero", "x": 1, "y": 1, "cover": "none"},
                {"kind": "npc", "id": "goblin", "x": 4, "y": 1, "cover": "half"},
            ],
            terrain=[],
        )
        self.assertEqual(12, combat["grid_width"])
        self.assertEqual(2, len(combat["positions"]))
        result = self.engine.resolve_attack(
            "c1", "character", "hero", "npc", "goblin",
            attack_bonus=30, damage_expression="1", attack_name="arrow", combat_id="gridfight", range_cells=6,
        )
        self.assertTrue(result["hit"])
        self.assertEqual(2, result["spatial"]["cover_ac_bonus"])
        self.engine.end_combat("c1", "gridfight")
        ended = self.engine.get_combat("c1", "gridfight")
        self.assertEqual([], ended["positions"])
        self.assertEqual([], ended["terrain"])

    def test_combat_grid_total_cover_blocks_attack(self):
        self.seed_actors()
        self.engine.start_combat(
            "c1", "wallfight", "Town",
            [{"kind": "character", "id": "hero"}, {"kind": "npc", "id": "goblin"}],
            positions=[
                {"kind": "character", "id": "hero", "x": 1, "y": 1},
                {"kind": "npc", "id": "goblin", "x": 4, "y": 1},
            ],
            terrain=[{"x": 2, "y": 1, "kind": "wall", "blocks_los": True}],
        )
        with self.assertRaises(ValueError):
            self.engine.resolve_attack(
                "c1", "character", "hero", "npc", "goblin",
                attack_bonus=30, damage_expression="1", combat_id="wallfight", range_cells=6,
            )


    def test_battle_image_cue_uses_grid_staging_without_portrait_trigger(self):
        self.seed_actors()
        self.engine.upsert_location("c1", "Town", "Town", region="Town", description="A narrow crossroads.")
        self.engine.start_combat(
            "c1", "imagefight", "Town",
            [{"kind": "character", "id": "hero"}, {"kind": "npc", "id": "goblin"}],
            grid_width=12, grid_height=12,
            positions=[
                {"kind": "character", "id": "hero", "x": 1, "y": 1},
                {"kind": "npc", "id": "goblin", "x": 10, "y": 10, "cover": "half"},
            ],
            terrain=[{"x": 6, "y": 6, "kind": "broken cart", "difficult": True}],
        )
        cue = self.engine.build_image_cue("c1", trigger_type="battle_start", combat_id="imagefight", force=True)
        self.assertTrue(cue["should_generate"])
        self.assertIn("tactical staging", cue["prompt"].lower())
        self.assertIn("north-west", cue["prompt"].lower())
        self.assertIn("south-east", cue["prompt"].lower())
        self.assertIn("broken cart", cue["prompt"].lower())
        self.assertIn("combat_grid", cue["visual_context"])


    def test_concurrent_hp_deltas_do_not_lose_updates(self):
        self.engine.upsert_character("c1", "tank", "Tank", level=1, hp=300, max_hp=300, ac=10, location="Arena")
        def hit(_):
            return self.engine.apply_hp_delta("c1", "character", "tank", -1, "concurrency probe")
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(hit, range(200)))
        self.assertEqual(100, self.engine.get_character("c1", "tank")["hp"])
        events = [e for e in self.engine.recent_events("c1", 100) if e["event_type"] == "hp_delta"]
        self.assertEqual(100, len(events))  # recent_events is intentionally capped at 100

    def test_concurrent_attacks_do_not_lose_damage(self):
        self.engine.upsert_character("c1", "attacker", "Attacker", level=1, hp=10, max_hp=10, ac=10, location="Arena")
        self.engine.upsert_npc("c1", "target", "Target", hp=100, max_hp=100, ac=10, location="Arena")
        original_check = self.engine._resolve_check_db
        original_damage = self.engine._roll_damage_db
        self.engine._resolve_check_db = lambda db, campaign_id, modifier, dc, mode, namespace: {"natural": 10, "modifier": modifier, "total": 10 + modifier, "dc": dc, "mode": mode, "success": True, "d20_rolls": [10]}
        self.engine._roll_damage_db = lambda db, campaign_id, expression, critical, namespace: {"expression": expression, "rolls": [1], "modifier": 0, "total": 1, "critical_dice_doubled": False, "critical_dice_clamped": False}
        try:
            def attack(_):
                return self.engine.resolve_attack("c1", "character", "attacker", "npc", "target", attack_bonus=20, damage_expression="1", attack_name="probe")
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(attack, range(80)))
        finally:
            self.engine._resolve_check_db = original_check
            self.engine._roll_damage_db = original_damage
        self.assertEqual(20, self.engine.get_npc("c1", "target")["hp"])

    def test_concurrent_world_advance_accumulates(self):
        before = self.engine.get_campaign("c1")["world_time"]
        def advance(_):
            return self.engine.advance_world("c1", 1, "concurrency probe")
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(advance, range(60)))
        from datetime import datetime
        delta = datetime.fromisoformat(self.engine.get_campaign("c1")["world_time"]) - datetime.fromisoformat(before)
        self.assertEqual(60 * 60, int(delta.total_seconds()))


    def test_visual_preferences_persist(self):
        prefs = self.engine.set_visual_preferences("c1", art_style="grimdark watercolor", battle_start=False)
        self.assertEqual("grimdark watercolor", prefs["art_style"])
        self.assertFalse(prefs["battle_start"])
        reopened = WorldEngine(self.path)
        self.assertEqual("grimdark watercolor", reopened.get_visual_preferences("c1")["art_style"])

    def test_location_image_cue_and_record_dedupe(self):
        self.seed_actors()
        self.engine.upsert_location("c1", "Town", "Town", region="Town", description="A busy riverside market square.", tags=["urban", "market"])
        cue = self.engine.build_image_cue("c1", trigger_type="new_location", location_id="Town", summary="The party arrives in town.")
        self.assertTrue(cue["should_generate"])
        self.assertIn("busy riverside market square", cue["prompt"])
        self.engine.record_image_generation("c1", "new_location", cue["scene_key"], title=cue["title"], prompt=cue["prompt"], aspect_ratio=cue["aspect_ratio"], location_id="Town")
        second = self.engine.build_image_cue("c1", trigger_type="new_location", location_id="Town")
        self.assertFalse(second["should_generate"])
        self.assertEqual("already_generated_for_scene_key", second["reason"])

    def test_battle_image_cue_contains_participants(self):
        self.seed_actors()
        self.engine.upsert_location("c1", "Town", "Town", region="Town", description="A torchlit crossroads.")
        self.engine.start_combat("c1", "cmb1", "Town", [{"kind": "character", "id": "hero"}, {"kind": "npc", "id": "goblin"}])
        cue = self.engine.build_image_cue("c1", trigger_type="battle_start", combat_id="cmb1", summary="An ambush begins.")
        self.assertTrue(cue["should_generate"])
        self.assertIn("Hero", cue["prompt"])
        self.assertIn("Goblin", cue["prompt"])
        self.assertEqual("16:9", cue["aspect_ratio"])


    def test_internal_state_block_is_hidden_numeric_projection(self):
        self.seed_actors()
        self.engine.adjust_relationship("c1", "goblin", "hero", trust_delta=-12, fear_delta=7)
        self.engine.set_world_state("c1", "location", "Town", "corruption", 80)
        block = self.engine.get_internal_state_block("c1", "Town")
        self.assertEqual(1, block["internal_only"])
        self.assertEqual(20, block["characters"][0]["hp"])
        self.assertEqual(80, block["numeric_world_state"][0]["value"])
        self.assertIn("weather_code", block)
        self.assertNotIn("weather", block)

    def test_visual_profile_persists_and_feeds_scene_not_portrait(self):
        self.seed_actors()
        self.engine.upsert_location("c1", "Town", "Town", region="Town", description="A riverside square.")
        self.engine.set_visual_profile("c1", "npc", "goblin", {"skin": "mottled olive", "cloak": "torn red wool", "scar": "split left ear", "height_cm": 112})
        reopened = WorldEngine(self.path, rng=random.Random(9))
        profile = reopened.get_visual_profile("c1", "npc", "goblin")
        self.assertEqual("split left ear", profile["profile"]["scar"])
        cue = reopened.build_image_cue("c1", trigger_type="scene_start", location_id="Town", scene_key="scene:intro")
        self.assertIn("mottled olive", cue["prompt"])
        self.assertIn("torn red wool", cue["prompt"])
        self.assertNotIn("112", cue["prompt"])
        self.assertNotIn("portrait", cue["instructions_for_gpt"].lower())

    def test_numeric_simulation_changes_visuals_without_leaking_numbers(self):
        self.seed_actors()
        self.engine.upsert_location("c1", "Town", "Town", region="Town", description="An old shrine courtyard.")
        self.engine.apply_hp_delta("c1", "character", "hero", -16, "wounded")
        self.engine.set_world_state("c1", "location", "Town", "corruption", 80)
        cue = self.engine.build_image_cue("c1", trigger_type="scene_start", location_id="Town", scene_key="scene:wounded")
        self.assertIn("severely wounded", cue["prompt"])
        self.assertIn("severe warped corruption", cue["prompt"])
        self.assertNotIn("corruption: 80", cue["prompt"].lower())
        self.assertNotIn("4/20", cue["prompt"])

    def test_visual_state_tracks_location_scene_and_strips_raw_numbers_from_prompt(self):
        self.seed_actors()
        self.engine.upsert_location("c1", "Town", "Town", region="Town", description="An old shrine courtyard.")
        self.engine.set_visual_state("c1", "location", "Town", {"landmark": "collapsed bell tower", "ground": "rain-slick black stone", "torch_count": 12})
        self.engine.set_visual_state("c1", "scene", "scene:rain", {"lighting": "cold blue moonlight", "crowd": "deserted"})
        cue = self.engine.build_image_cue("c1", trigger_type="scene_start", location_id="Town", scene_key="scene:rain")
        self.assertIn("collapsed bell tower", cue["prompt"])
        self.assertIn("cold blue moonlight", cue["prompt"])
        self.assertNotIn('"torch_count":12', cue["prompt"])
        reopened = WorldEngine(self.path)
        self.assertEqual("collapsed bell tower", reopened.get_visual_state("c1", "location", "Town")["state"]["landmark"])

    def test_image_record_does_not_advance_gameplay_revision_and_dedupes_scene(self):
        self.seed_actors()
        self.engine.upsert_location("c1", "Town", "Town", region="Town", description="A plaza.")
        cue = self.engine.build_image_cue("c1", trigger_type="scene_start", location_id="Town")
        before = self.engine.get_campaign("c1")["revision"]
        self.engine.record_image_generation("c1", "scene_start", cue["scene_key"], title=cue["title"], prompt=cue["prompt"], aspect_ratio=cue["aspect_ratio"], location_id="Town", visual_context=cue["visual_context"])
        after = self.engine.get_campaign("c1")["revision"]
        self.assertEqual(before, after)
        second = self.engine.build_image_cue("c1", trigger_type="scene_start", location_id="Town")
        self.assertFalse(second["should_generate"])
        self.assertEqual("already_generated_for_scene_key", second["reason"])
        recent = self.engine.get_recent_image_context("c1", "Town", 1)["recent"][0]
        self.assertEqual(cue["scene_key"], recent["scene_key"])
        self.assertIn("source_revision", recent)


    def test_v3_image_table_forward_migrates(self):
        old_path = Path(self.tmp.name) / "v3.sqlite3"
        db = sqlite3.connect(old_path)
        try:
            db.execute("""CREATE TABLE image_generations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                scene_key TEXT NOT NULL,
                location_id TEXT,
                combat_id TEXT,
                title TEXT NOT NULL,
                prompt TEXT NOT NULL,
                aspect_ratio TEXT NOT NULL DEFAULT '4:3',
                status TEXT NOT NULL DEFAULT 'generated',
                image_ref TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(campaign_id, trigger_type, scene_key)
            )""")
            db.commit()
        finally:
            db.close()
        WorldEngine(old_path)
        db = sqlite3.connect(old_path)
        try:
            cols = {row[1] for row in db.execute("PRAGMA table_info(image_generations)")}
        finally:
            db.close()
        self.assertIn("visual_context_json", cols)
        self.assertIn("source_revision", cols)


if __name__ == "__main__":
    unittest.main()
