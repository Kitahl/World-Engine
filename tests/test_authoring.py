from pathlib import Path
import tempfile
import unittest

from world_engine import WorldEngine


class AuthoringPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "author.sqlite3"
        self.e = WorldEngine(self.path)
        self.e.ensure_campaign("c", "Authoring")
        self.e.upsert_location("c", "greymoor", "Greymoor", region="Moor", state={"pop": 340, "unrest": 0.72, "wealth": 0.31})
        self.e.set_simulation_seed("c", 11)

    def tearDown(self):
        self.tmp.cleanup()

    def payload(self):
        return {
            "world_bible": {"tone": "grim frontier", "tech": "late medieval", "magic": "rare but real"},
            "items": [{"id": "flour", "name": "Flour", "base_price": 1}],
            "archetypes": [{
                "id": "baker", "name": "Baker",
                "needs": {"hunger": {"value": 20, "baseline": 20, "drift_per_day": 0.05, "curve": "threshold"}, "coin": {"value": 50, "baseline": 50, "drift_per_day": 0.02, "curve": "quadratic"}},
                "actions": [
                    {"id": "bake", "base_utility": 0.2, "considerations": [{"type": "need", "key": "coin", "weight": 0.5}], "cost_hours": 8},
                    {"id": "idle", "base_utility": 0.01, "cost_hours": 2},
                ],
                "routine": {"08:00": "bakery", "20:00": "home"},
                "visual": {"clothing": "flour-dusted apron"},
            }],
            "rule_templates": [{"id": "daily_decisions", "archetype": "decide", "cadence": "day", "params": {"top_k": 3, "temperature": 0.35}}],
            "rules": [{"id": "greymoor_decide", "template_id": "daily_decisions"}],
            "reactions": [{"id": "bad_news_fear", "trigger_event_type": "bad_news", "repeat_policy": "once_per_cascade", "effects": [{"type": "need", "npc_id": "$actor", "need": "hunger", "delta": 1}]}],
            "recipes": [{"id": "bread", "kind": "cook", "inputs": {"flour": 1}, "output_item_id": "flour", "output_qty": 1, "skill": "survival", "dc": 10, "hours": 1}],
            "npcs": [{"id": "mara", "name": "Mara", "archetype_id": "baker", "location": "greymoor", "hp": 8, "max_hp": 8}],
        }

    def test_stage_validate_dry_run_promote(self):
        self.e.author_stage("c", "b1", self.payload(), mode="bootstrap")
        val = self.e.author_validate("c", "b1")
        self.assertTrue(val["valid"], val)
        dry = self.e.author_dry_run("c", "b1", days=30)
        self.assertTrue(dry["passed"], dry)
        promoted = self.e.author_promote("c", "b1")
        self.assertEqual("promoted", promoted["status"])
        self.assertEqual("grim frontier", self.e.get_world_bible("c")["bible"]["tone"])
        npc = self.e.get_npc("c", "mara")
        self.assertEqual("baker", npc["archetype_id"])
        self.assertEqual(1, npc["materialized"])
        self.assertEqual("threshold", self.e.save_npc_need("c", "mara", "hunger", 20, curve="threshold")["curve"])

    def test_invalid_high_frequency_chance_rule_rejected(self):
        p = {"rules": [{"id": "doom", "archetype": "chance", "cadence": "day", "params": {"p_day": 0.8}}]}
        self.e.author_stage("c", "bad", p)
        val = self.e.author_validate("c", "bad")
        self.assertFalse(val["valid"])
        self.assertTrue(any("0..0.2" in x["message"] for x in val["errors"]))

    def test_reaction_requires_repeat_policy_and_bounds_self_loop(self):
        p = {"reactions": [{"id": "loop", "trigger_event_type": "grief", "effects": [{"type": "emit", "event_type": "grief"}]}]}
        self.e.author_stage("c", "loop", p)
        val = self.e.author_validate("c", "loop")
        self.assertFalse(val["valid"])
        self.assertTrue(any("repeat_policy" in x["path"] for x in val["errors"]))

    def test_canon_lock_blocks_overwrite(self):
        self.e.author_stage("c", "first", {"archetypes": [{"id": "guard", "name": "Guard"}]})
        self.assertTrue(self.e.author_validate("c", "first")["valid"])
        self.assertTrue(self.e.author_dry_run("c", "first", days=1)["passed"])
        self.e.author_promote("c", "first")
        self.e.author_lock("c", "archetype", "guard", reason="player met a guard using this canon")
        self.e.author_stage("c", "second", {"archetypes": [{"id": "guard", "name": "Secret Wizard"}]})
        val = self.e.author_validate("c", "second")
        self.assertFalse(val["valid"])
        self.assertTrue(any("canon-locked" in x["message"] for x in val["errors"]))

    def test_gameplay_mutation_auto_canon_locks_materialized_npc(self):
        p = {"archetypes": [{"id": "guard", "name": "Guard"}], "npcs": [{"id": "rusk", "name": "Rusk", "archetype_id": "guard", "location": "greymoor", "hp": 10, "max_hp": 10}]}
        self.e.author_stage("c", "rusk_batch", p, mode="lazy")
        self.e.author_validate("c", "rusk_batch"); self.e.author_dry_run("c", "rusk_batch", days=1); self.e.author_promote("c", "rusk_batch")
        self.e.start_scene("c", "rusk_scene", "greymoor", entities=[{"kind": "npc", "id": "rusk"}])
        self.e.apply_hp_delta("c", "npc", "rusk", -1, "player shoved Rusk")
        result = self.e.end_scene("c", "rusk_scene")
        self.assertNotIn("rusk", result["dematerialized_npcs"])
        self.assertEqual(9, self.e.get_npc("c", "rusk")["hp"])
        with self.e._db() as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM canon_locks WHERE campaign_id='c' AND object_kind='npc' AND object_id='rusk'").fetchone())

    def test_world_context_surfaces_bible_materialization_signal_and_content_gaps(self):
        self.e.author_stage("c", "bible_ctx", {"world_bible": {"tone": "somber"}})
        self.e.author_validate("c", "bible_ctx"); self.e.author_dry_run("c", "bible_ctx", days=1); self.e.author_promote("c", "bible_ctx")
        self.e.author_log_gap("c", "missing:cult", "faction", "A cult is referenced but not materialized", scope_id="greymoor")
        ctx = self.e.get_world_context("c", "greymoor")
        self.assertEqual("somber", ctx["world_bible"]["bible"]["tone"])
        self.assertTrue(ctx["content_materialization"]["needs_materialization"])
        self.assertEqual(340, ctx["content_materialization"]["aggregate_population"])
        self.assertEqual("missing:cult", ctx["open_content_gaps"][0]["gap_key"])

    def test_materialization_brief_is_derived_from_world_aggregates(self):
        self.e.author_stage("c", "bible", {"world_bible": {"naming": "Anglo-Celtic", "magic": "low"}})
        self.e.author_validate("c", "bible"); self.e.author_dry_run("c", "bible", days=1); self.e.author_promote("c", "bible")
        brief = self.e.author_materialization_brief("c", "greymoor")
        self.assertEqual(340, brief["aggregates"]["population"])
        self.assertAlmostEqual(0.72, brief["location"]["state"]["unrest"])
        self.assertEqual("low", brief["world_bible"]["magic"])
        self.assertIn("Do not alter aggregates", brief["generation_instruction"])

    def test_dry_run_does_not_mutate_live_database(self):
        before = self.e.author_world_digest("c")
        self.e.author_stage("c", "scratch", self.payload())
        self.e.author_dry_run("c", "scratch", days=30)
        after = self.e.author_world_digest("c")
        self.assertEqual(before["population_alive"], after["population_alive"])
        self.assertEqual(before["event_count"], after["event_count"])
        with self.e._db() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM npcs WHERE campaign_id='c' AND id='mara'").fetchone())

    def test_reactive_content_gap_created_by_simulation_missing_reference(self):
        self.e.save_simulation_rule("c", "bandit_raid", "chance", cadence="day", params={"p_day": 0.2, "event_type": "raid", "summary": "Bandits raid the road", "location_id": "greymoor", "requires_content": {"kind": "faction", "id": "red_knives", "gap_key": "faction:red_knives"}})
        # Search a deterministic number of days; p=0.2 makes an occurrence overwhelmingly likely.
        self.e.advance_world("c", 100 * 1440)
        gaps = self.e.author_list_gaps("c")
        self.assertTrue(any(g["gap_key"] == "faction:red_knives" for g in gaps), gaps)
        self.assertFalse(any(ev["event_type"] == "raid" for ev in self.e.recent_events("c", 100)))

    def test_world_bible_feeds_scene_image_generation(self):
        self.e.author_stage("c", "bible_img", {"world_bible": {"tone": "somber folklore", "magic": "rare and sacred"}})
        self.e.author_validate("c", "bible_img"); self.e.author_dry_run("c", "bible_img", days=1); self.e.author_promote("c", "bible_img")
        cue = self.e.build_image_cue("c", trigger_type="new_location", location_id="greymoor", summary="Arrival at dusk")
        self.assertTrue(cue["should_generate"])
        self.assertIn("somber folklore", cue["prompt"])
        self.assertIn("rare and sacred", cue["prompt"])
        self.assertIn("world_bible", cue["visual_context"])

    def test_world_digest_is_compact_and_reports_saturation(self):
        d = self.e.author_world_digest("c")
        self.assertIn("population_alive", d)
        self.assertIn("saturation", d)
        self.assertLess(len(str(d)), 5000)

    def test_lazy_scene_demotes_untouched_materialized_npc_but_keeps_locked_one(self):
        p = {
            "archetypes": [{"id": "villager", "name": "Villager"}],
            "npcs": [
                {"id": "temp_a", "name": "Temp A", "archetype_id": "villager", "location": "greymoor"},
                {"id": "temp_b", "name": "Temp B", "archetype_id": "villager", "location": "greymoor"},
            ],
        }
        self.e.author_stage("c", "lazy", p, mode="lazy")
        self.e.author_validate("c", "lazy"); self.e.author_dry_run("c", "lazy", days=1); self.e.author_promote("c", "lazy")
        self.e.start_scene("c", "s1", "greymoor", entities=[
            {"kind": "npc", "id": "temp_a", "x": 0, "y": 0},
            {"kind": "npc", "id": "temp_b", "x": 1, "y": 0},
        ])
        self.e.author_lock("c", "npc", "temp_b", reason="player spoke to Temp B")
        result = self.e.end_scene("c", "s1")
        self.assertIn("temp_a", result["dematerialized_npcs"])
        with self.assertRaises(KeyError):
            self.e.get_npc("c", "temp_a")
        self.assertEqual("Temp B", self.e.get_npc("c", "temp_b")["name"])

    def test_schema_migrates_new_authoring_tables_and_npc_columns(self):
        reopened = WorldEngine(self.path)
        with reopened._db() as db:
            tables = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("world_bible", "npc_archetypes", "sim_rule_templates", "recipes", "authoring_batches", "canon_locks", "content_gaps"):
                self.assertIn(table, tables)
            cols = {r["name"] for r in db.execute("PRAGMA table_info(npcs)")}
            self.assertIn("archetype_id", cols)
            self.assertIn("materialized", cols)


if __name__ == "__main__":
    unittest.main()
