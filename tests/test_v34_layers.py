from pathlib import Path
import tempfile
import unittest
from datetime import datetime

from world_engine import WorldEngine


class WorldSceneDirectorLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "v34.sqlite3"
        self.e = WorldEngine(self.db)
        self.e.ensure_campaign("c", "Campaign", "1492-01-01T08:00:00+00:00")
        self.e.set_simulation_seed("c", 12345)
        self.e.upsert_location("c", "city", "Rivergate", region="north", realm_id="crown", x=0, y=0, description="A walled river city.")
        self.e.upsert_location("c", "abbey", "Old Abbey", region="north", realm_id="crown", x=3, y=4)
        self.e.upsert_location("c", "south", "Southwatch", region="south", realm_id="crown", x=10, y=0)
        self.e.save_location_link("c", "city", "abbey", 2.0, bidirectional=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scene_materializes_at_most_12_and_reports_context_tracking(self):
        for i in range(14):
            self.e.upsert_npc("c", f"n{i:02d}", f"NPC {i}", hp=5, max_hp=5, ac=10, location="city")
        scene = self.e.start_scene("c", "s1", "city")
        self.assertEqual(12, len(scene["entities"]))
        ctx = self.e.get_world_context("c", "city", entity_limit=8)
        self.assertEqual("s1", ctx["world_tracking"]["active_scene_id"])
        self.assertEqual(14, ctx["entity_window"]["total_count"])
        self.assertEqual(8, ctx["entity_window"]["returned_count"])
        self.assertTrue(ctx["entity_window"]["truncated"])
        self.assertLessEqual(len(ctx["active_scene"]["entities"]), 12)

    def test_scene_feature_folds_back_and_scene_is_disposable(self):
        self.e.start_scene("c", "s1", "city")
        self.e.set_scene_feature("c", "s1", "burned_gate", kind="damage", x=1, y=2, persistent=True, state={"description": "charred gatehouse"})
        ended = self.e.end_scene("c", "s1", foldback_state={"gate_damaged": True})
        self.assertTrue(ended["ended"])
        self.assertIsNone(self.e.get_scene("c", "s1"))
        loc = self.e.get_location("c", "city")
        self.assertTrue(loc["state"]["gate_damaged"])
        self.assertEqual("burned_gate", loc["state"]["persistent_scene_features"][-1]["id"])

    def test_combat_materializes_from_scene_and_folds_position_back(self):
        self.e.upsert_character("c", "hero", "Hero", hp=20, max_hp=20, ac=15, location="city")
        self.e.upsert_npc("c", "bandit", "Bandit", hp=10, max_hp=10, ac=12, location="city")
        self.e.start_scene("c", "s1", "city", entities=[
            {"kind": "character", "id": "hero", "x": -10, "y": -10, "zone": "west"},
            {"kind": "npc", "id": "bandit", "x": 10, "y": 10, "zone": "east"},
        ], features=[{"id": "cart", "kind": "cart", "x": 0, "y": 0, "blocks_los": True}])
        combat = self.e.start_combat("c", "fight", "city", [{"kind": "character", "id": "hero"}, {"kind": "npc", "id": "bandit"}], scene_id="s1")
        self.assertEqual(2, len(combat["positions"]))
        self.assertGreaterEqual(len(combat["terrain"]), 1)
        self.e.set_combat_position("c", "fight", "character", "hero", 15, 15)
        self.e.end_combat("c", "fight")
        scene = self.e.get_scene("c", "s1")
        hero = next(x for x in scene["entities"] if x["actor_id"] == "hero")
        self.assertNotEqual(-10, hero["x"])
        with self.e._db() as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM combat_positions WHERE campaign_id='c' AND combat_id='fight'").fetchone()[0])
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM combat_terrain WHERE campaign_id='c' AND combat_id='fight'").fetchone()[0])

    def _seed_authority_stack(self):
        self.e.upsert_npc("c", "mayor", "Mayor Vale", hp=10, max_hp=10, ac=10, location="city")
        self.e.upsert_npc("c", "king", "King Alder", hp=10, max_hp=10, ac=10, location="city", faction_id="crown_faction")
        self.e.upsert_npc("c", "prince", "Prince Rowan", hp=10, max_hp=10, ac=10, location="city", faction_id="crown_faction")
        self.e.save_npc_lifecycle("c", "king", birth_year=1440, heir_id="prince", mortality={"enabled": False})
        self.e.save_npc_lifecycle("c", "prince", birth_year=1470, mortality={"enabled": False})
        self.e.upsert_faction("c", "crown_faction", "Crown", region="north", leader_id="king", reserve_score=100)
        self.e.save_director("c", "mayor_dir", "Rivergate Mayor", director_kind="civic", scope_type="location", scope_id="city", source_kind="npc", source_id="mayor", authority=0.8, priority=10, weights={"crime": 0.5}, policies={"curfew": "dusk"})
        self.e.save_director("c", "king_dir", "Royal Authority", director_kind="realm", scope_type="realm", scope_id="crown", source_kind="faction_leader", source_id="crown_faction", authority=1.0, priority=20, weights={"threat": 1.5}, policies={"law": "royal"})
        self.e.save_director("c", "gaia_dir", "Gaia's Influence", director_kind="divine", scope_type="region", scope_id="north", source_kind="deity", source_id="Gaia", authority=0.6, priority=30, weights={"relief": 1.4}, policies={"sacred_groves": True})

    def test_directors_change_by_location_and_hierarchy(self):
        self._seed_authority_stack()
        city = self.e.get_active_directors("c", "city")
        self.assertEqual(["mayor_dir", "king_dir", "gaia_dir"], [x["id"] for x in city["stack"]])
        south = self.e.get_active_directors("c", "south")
        self.assertEqual(["king_dir"], [x["id"] for x in south["stack"]])
        self.assertEqual("dusk", city["policies"]["curfew"])
        self.assertGreater(city["event_multipliers"]["threat"], 1.0)

    def test_faction_leader_director_tracks_succession_and_ownership(self):
        self._seed_authority_stack()
        self.e.save_ownership("c", "title", "crown_of_north", "npc", "king")
        self.e.set_actor_status("c", "npc", "king", "dead", reason="old age")
        faction = self.e.get_faction("c", "crown_faction")
        self.assertEqual("prince", faction["leader_id"])
        owner = self.e.get_ownership("c", "title", "crown_of_north")
        self.assertEqual("prince", owner["owner_id"])
        stack = self.e.get_active_directors("c", "city")
        royal = next(x for x in stack["stack"] if x["id"] == "king_dir")
        self.assertEqual("prince", royal["resolved_source_id"])
        self.assertEqual("Prince Rowan", royal["resolved_source_name"])
        self.assertNotIn("king", [n["id"] for n in self.e.get_world_context("c", "city")["npcs"]])

    def test_director_multiplier_is_wired_into_chance_event_ledger(self):
        self._seed_authority_stack()
        self.e.save_simulation_rule("c", "raid", "chance", cadence="day", params={
            "p": 1.0, "event_type": "raid", "summary": "Raid pressure", "location_id": "city", "director_role": "threat"
        })
        self.e.advance_world("c", 1440, "day passes")
        event = next(e for e in self.e.recent_events("c", 50) if e["event_type"] == "raid")
        self.assertGreater(event["payload"]["director_multiplier"], 1.0)
        self.assertEqual(1.0, event["payload"]["base_p"])

    def test_scene_and_directors_feed_image_generation_without_raw_authority_numbers(self):
        self._seed_authority_stack()
        self.e.upsert_character("c", "hero", "Hero", hp=20, max_hp=20, ac=15, location="city")
        self.e.start_scene("c", "s1", "city", entities=[{"kind": "character", "id": "hero", "zone": "gate", "stance": "watchful"}], features=[{"id": "statue", "kind": "sacred statue", "persistent": True}])
        cue = self.e.build_image_cue("c", trigger_type="scene_start", location_id="city", scene_key="scene_director", force=True)
        self.assertTrue(cue["should_generate"])
        self.assertIn("Rivergate Mayor", cue["prompt"])
        self.assertIn("Gaia's Influence", cue["prompt"])
        self.assertIn("watchful", cue["prompt"])
        self.assertIn("sacred statue", cue["prompt"])
        self.assertNotIn("0.8", cue["prompt"])
        self.assertNotIn("1.5", cue["prompt"])
        self.assertNotIn("0.6", cue["prompt"])
        self.assertIn("directors", cue["visual_context"])

    def test_internal_state_is_capped_and_reports_director_count(self):
        self._seed_authority_stack()
        for i in range(60):
            self.e.upsert_npc("c", f"crowd{i:02d}", f"Crowd {i}", hp=5, max_hp=5, ac=10, location="city")
        block = self.e.get_internal_state_block("c", "city", entity_limit=12)
        self.assertEqual(12, block["entity_window"]["returned_count"])
        self.assertTrue(block["entity_window"]["truncated"])
        self.assertEqual(3, block["director_count"])

    def test_configured_fertility_can_create_persistent_child(self):
        self.e.upsert_npc("c", "parent_a", "Parent A", hp=5, max_hp=5, ac=10, location="city")
        self.e.upsert_npc("c", "parent_b", "Parent B", hp=5, max_hp=5, ac=10, location="city")
        fertility = {"enabled": True, "annual_birth_rate": 3, "cooldown_days": 300, "min_age": 18, "max_age": 45, "partner_min_age": 18, "partner_max_age": 80}
        self.e.save_npc_lifecycle("c", "parent_a", birth_year=1465, spouse_id="parent_b", fertility=fertility)
        self.e.save_npc_lifecycle("c", "parent_b", birth_year=1460, spouse_id="parent_a")
        result = self.e.advance_world("c", 525600, "one year")
        self.assertGreaterEqual(result["simulation"]["lifecycle"], 1)
        with self.e._db() as db:
            row = db.execute("SELECT id FROM npcs WHERE campaign_id='c' AND id LIKE 'child_%' LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        child = self.e.get_npc_lifecycle("c", row["id"])
        self.assertEqual(sorted(["parent_a", "parent_b"]), sorted(child["parents"]))

    def test_seeded_gameplay_dice_replays_across_db_reopen(self):
        self.e.set_simulation_seed("c", 777)
        seq1 = [self.e.resolve_check(2, 10, campaign_id="c")["natural"] for _ in range(5)]
        reopen = WorldEngine(self.db)
        continuation = [reopen.resolve_check(2, 10, campaign_id="c")["natural"] for _ in range(3)]

        other = Path(self.tmp.name) / "other.sqlite3"
        e2 = WorldEngine(other); e2.ensure_campaign("c", "Campaign", "1492-01-01T08:00:00+00:00"); e2.set_simulation_seed("c", 777)
        seq2 = [e2.resolve_check(2, 10, campaign_id="c")["natural"] for _ in range(8)]
        self.assertEqual(seq1 + continuation, seq2)

    def test_no_unseeded_system_randomness_in_engine_sources(self):
        root = Path(__file__).resolve().parents[1] / "world_engine"
        sources = "\n".join(p.read_text(encoding="utf-8") for p in root.glob("*.py"))
        self.assertNotIn("SystemRandom", sources)
        self.assertNotIn("random.random(", sources)
        self.assertNotIn("random.randint(", sources)

    def test_crit_dice_cap_is_reported_not_crashed(self):
        result = self.e._roll_damage("51d6", critical=True)
        self.assertTrue(result["critical_dice_clamped"])
        self.assertEqual(102, result["requested_dice_count"])
        self.assertEqual(100, len(result["rolls"]))

    def test_context_uses_bounded_connection_count(self):
        for i in range(80):
            self.e.upsert_npc("c", f"x{i:02d}", f"X {i}", hp=5, max_hp=5, ac=10, location="city")
        original = self.e._db
        count = {"n": 0}
        from contextlib import contextmanager
        @contextmanager
        def counted_db():
            count["n"] += 1
            with original() as db:
                yield db
        self.e._db = counted_db
        try:
            ctx = self.e.get_world_context("c", "city", entity_limit=20)
        finally:
            self.e._db = original
        self.assertLessEqual(count["n"], 1)
        self.assertEqual(20, ctx["entity_window"]["returned_count"])
        self.assertTrue(ctx["entity_window"]["truncated"])


if __name__ == "__main__":
    unittest.main()
