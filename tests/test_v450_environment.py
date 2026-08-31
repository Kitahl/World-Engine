from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from world_engine import WorldEngine
from world_engine.environment import EnvironmentKernel


class EnvironmentV440Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "world.sqlite3"
        self.e = WorldEngine(self.db)
        self.e.ensure_campaign("c", "Environment", "1492-07-01T05:00:00+00:00")
        self.e.upsert_location("c", "woods", "Woods", tags=["outdoors", "wilderness"])
        self.e.upsert_location("c", "shelter", "Shelter", tags=["indoors", "building"])
        self.e.upsert_character("c", "hero", "Hero", hp=100, max_hp=100, ac=15, location="woods")
        self.e.upsert_npc("c", "mara", "Mara", hp=50, max_hp=50, ac=12, location="woods")
        self.e.set_simulation_seed("c", 440)

    def tearDown(self):
        self.tmp.cleanup()

    def _map(self, map_id="m", location="woods", max_x=2):
        self.e.world_systems_dispatch("save_map", "c", {
            "map_id": map_id, "name": map_id, "scope_type": "location", "scope_id": location,
            "bounds": {"min_x": 0, "max_x": max_x, "min_y": 0, "max_y": 0, "min_z": 0, "max_z": 0},
        })

    def _tile(self, x, *, terrain="wood plank", hp=40, map_id="m"):
        return self.e.world_systems_dispatch("save_tile", "c", {
            "map_id": map_id, "x": x, "y": 0, "z": 0, "terrain": terrain,
            "walkable": True, "terrain_hp": hp,
        })

    def test_schema_and_environment_capability(self):
        with self.e._db() as db:
            self.assertEqual(17, db.execute("PRAGMA user_version").fetchone()[0])
            for table in ("environment_materials", "environment_targets", "environment_effects", "environment_weather", "environment_disaster_config"):
                self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())
            self.assertGreaterEqual(db.execute("SELECT COUNT(*) n FROM environment_materials WHERE campaign_id='c'").fetchone()["n"], 10)
        caps = {x["capability_id"] for x in self.e.list_capabilities("c")}
        self.assertIn("environment.interact", caps)

    def test_public_ignite_creates_authoritative_fire(self):
        self._map(max_x=0); self._tile(0, terrain="dry hay", hp=25)
        with self.e._write_db() as db:
            db.execute("INSERT INTO item_defs(campaign_id,id,name,base_price,effect_dice,tags_json,metadata_json,updated_at) VALUES('c','torch','Torch',1,NULL,'[\"fire\",\"torch\"]','{}',?)", (self.e._now(),))
        self.e.set_inventory_item("c", "character", "hero", "torch", 1)
        result = self.e.resolve_turn(
            "c", actor_kind="character", actor_id="hero", location_id="woods",
            intents=[{"type": "ignite", "parameters": {"target": {"type": "tile", "map_id": "m", "x": 0, "y": 0, "z": 0}, "source": {"type": "item", "item_id": "torch"}}}],
        )
        self.assertEqual("completed", result["status"])
        self.assertEqual("environment.interact", result["steps"][0]["capability_id"])
        self.assertTrue(result["steps"][0]["result"]["success"])
        snap = self.e.environment_dispatch("snapshot", "c", {"location_id": "woods"})
        self.assertTrue(any(x["effect_type"] == "fire" for x in snap["effects"]))

    def test_fire_smoke_spread_and_structural_destruction(self):
        self._map(max_x=2)
        self._tile(0, terrain="dry hay", hp=12); self._tile(1, terrain="wood beam", hp=16); self._tile(2, terrain="wood wall", hp=18)
        self.e.environment_dispatch("apply_effect", "c", {"effect_type": "fire", "target": {"type": "tile", "map_id": "m", "x": 0, "y": 0, "z": 0}, "intensity": 0.95})
        result = self.e.advance_world("c", 12 * 60, "fire simulation")
        self.assertGreater(result["simulation"]["environment_spread"], 0)
        self.assertGreater(result["simulation"]["environment_damage"], 0)
        snap = self.e.environment_dispatch("snapshot", "c", {"location_id": "woods"})
        kinds = {x["effect_type"] for x in snap["effects"]}
        self.assertIn("smoke", kinds)
        with self.e._db() as db:
            rows = db.execute("SELECT terrain_hp,state_json FROM spatial_tiles WHERE campaign_id='c' AND map_id='m' ORDER BY x").fetchall()
            self.assertTrue(any(float(r["terrain_hp"]) <= 0 for r in rows))

    def test_sustained_fire_emits_structure_destroyed_once(self):
        self._map(max_x=0)
        self._tile(0, terrain="dry wood", hp=1)
        self.e.environment_dispatch(
            "apply_effect", "c",
            {"effect_type": "fire", "target": {"type": "tile", "map_id": "m", "x": 0, "y": 0, "z": 0}, "intensity": 0.95},
        )
        self.e.advance_world("c", 3 * 60, "sustained fire")
        destroyed = [
            event for event in self.e.recent_events("c", 200)
            if event["event_type"] == "environment_structure_destroyed"
        ]
        self.assertEqual(1, len(destroyed))

    def test_weather_generates_from_existing_climate_and_season_advances(self):
        self.e.world_systems_dispatch("set_climate", "c", {
            "scope_type": "location", "scope_id": "woods", "climate": "temperate", "season": "summer",
            "weather_weights": {"rain": 1.0}, "state": {"auto_weather": True, "auto_season": True},
        })
        self.e.advance_world("c", 60, "weather tick")  # 06:00 boundary
        snap = self.e.environment_dispatch("snapshot", "c", {"location_id": "woods"})
        wx = next(x for x in snap["weather"] if x["scope_type"] == "location")
        self.assertEqual("rain", wx["condition"])
        self.assertEqual("rain", wx["precipitation"])
        # Move campaign to the Aug->Sep boundary and let the existing climate row update itself.
        with self.e._write_db() as db:
            db.execute("UPDATE campaigns SET world_time='1492-08-31T23:00:00+00:00' WHERE id='c'")
        self.e.advance_world("c", 60, "season boundary")
        with self.e._db() as db:
            season = db.execute("SELECT season FROM regional_climate WHERE campaign_id='c' AND scope_type='location' AND scope_id='woods'").fetchone()["season"]
        self.assertEqual("autumn", season)

    def test_direct_climate_write_rejects_invalid_weather_weights_atomically(self):
        invalid = [
            {"meteors": 1.0},
            {"rain": float("nan")},
            {"rain": "oops"},
            {"rain": True},
            {"rain": 0.0},
            {"clear": 600_000.0, "rain": 600_000.0},
            {"clear": 1e308, "rain": 1e308},
        ]
        for index, weights in enumerate(invalid):
            scope_id = f"invalid-{index}"
            with self.subTest(weights=weights), self.assertRaises(ValueError):
                self.e.world_systems_dispatch("set_climate", "c", {
                    "scope_type": "location", "scope_id": scope_id,
                    "climate": "temperate", "weather_weights": weights,
                })
            with self.e._db() as db:
                stored = db.execute(
                    "SELECT 1 FROM regional_climate WHERE campaign_id='c' AND scope_type='location' AND scope_id=?",
                    (scope_id,),
                ).fetchone()
            self.assertIsNone(stored)

    def test_rain_wets_terrain_and_changes_movement(self):
        self._map(max_x=0); self._tile(0, terrain="earth", hp=100)
        self.e.environment_dispatch("bind_target", "c", {"type": "tile", "map_id": "m", "x": 0, "y": 0, "z": 0})
        self.e.world_systems_dispatch("set_climate", "c", {
            "scope_type": "location", "scope_id": "woods", "climate": "temperate", "weather_weights": {"rain": 1.0},
        })
        self.e.advance_world("c", 8 * 60, "rain exposure")
        snap = self.e.environment_dispatch("snapshot", "c", {"location_id": "woods"})
        target = next(x for x in snap["targets"] if x["target_key"].startswith("tile:m:"))
        self.assertGreater(target["properties"].get("wetness", 0), 0)
        with self.e._db() as db:
            move = float(db.execute("SELECT move_cost FROM spatial_tiles WHERE campaign_id='c' AND map_id='m' AND x=0 AND y=0 AND z=0").fetchone()["move_cost"])
        self.assertGreater(move, 1.0)

    def test_sparse_flooding_propagates_and_increases_move_cost(self):
        self._map(max_x=2)
        for x in range(3): self._tile(x, terrain="earth", hp=100)
        self.e.environment_dispatch("apply_effect", "c", {"effect_type": "water", "target": {"type": "tile", "map_id": "m", "x": 0, "y": 0, "z": 0}, "intensity": 1.0, "amount": 8.0})
        result = self.e.advance_world("c", 12 * 60, "flood")
        self.assertGreater(result["simulation"]["environment_spread"], 0)
        snap = self.e.environment_dispatch("snapshot", "c", {"location_id": "woods"})
        wet_tiles = [t for t in snap["targets"] if t["target_type"] == "tile" and t["properties"].get("water_level", 0) > 0]
        self.assertGreaterEqual(len(wet_tiles), 2)
        with self.e._db() as db:
            self.assertGreater(max(float(r["move_cost"]) for r in db.execute("SELECT move_cost FROM spatial_tiles WHERE campaign_id='c' AND map_id='m'")), 1.0)

    def test_extreme_weather_exposes_outdoor_actors_but_not_sheltered_location(self):
        self.e.world_systems_dispatch("set_climate", "c", {
            "scope_type": "location", "scope_id": "woods", "climate": "arctic", "weather_weights": {"cold_snap": 1.0}, "state": {"actor_exposure": True},
        })
        self.e.upsert_character("c", "safe", "Safe", hp=100, max_hp=100, location="shelter")
        self.e.world_systems_dispatch("set_climate", "c", {
            "scope_type": "location", "scope_id": "shelter", "climate": "arctic", "weather_weights": {"cold_snap": 1.0}, "state": {"actor_exposure": True},
        })
        self.e.advance_world("c", 60, "cold snap")
        self.assertLess(self.e.get_character("c", "hero")["hp"], 100)
        self.assertEqual(self.e.get_character("c", "safe")["hp"], 100)
        exposed_hp = self.e.get_character("c", "hero")["hp"]
        self.e.move_actor("c", "character", "hero", "shelter", "take shelter")
        self.e.advance_world("c", 60, "sheltered from cold")
        self.assertEqual(exposed_hp, self.e.get_character("c", "hero")["hp"])
        with self.e._db() as db:
            target = db.execute(
                "SELECT location_id FROM environment_targets WHERE campaign_id='c' AND target_key='actor:character:hero'"
            ).fetchone()
        self.assertEqual("shelter", target["location_id"])

    def test_disease_uses_affliction_system(self):
        self.e.environment_dispatch("apply_effect", "c", {"effect_type": "disease", "target": {"type": "actor", "actor_kind": "character", "actor_id": "hero"}, "intensity": 0.35})
        self.e.advance_world("c", 6 * 60, "illness")
        with self.e._db() as db:
            row = db.execute("SELECT kind,stage,max_stage FROM afflictions WHERE campaign_id='c' AND actor_kind='character' AND actor_id='hero' AND id='environment_disease'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual("disease", row["kind"])
        self.assertGreaterEqual(row["stage"], 1)

    def test_environment_consideration_drives_existing_decide_ai(self):
        self.e.upsert_location("c", "safe", "Safe Clearing", tags=["outdoors"])
        self.e.environment_dispatch("apply_effect", "c", {"effect_type": "gas", "target": {"type": "location", "id": "woods"}, "intensity": 0.9, "amount": 3})
        self.e.save_npc_action("c", "mara", "flee", location="safe", base_utility=0.0, considerations=[{"type": "environment", "effect_type": "gas", "weight": 5.0}])
        self.e.save_npc_action("c", "mara", "stay", location="woods", base_utility=0.4)
        self.e.save_simulation_rule("c", "mara_environment_decide", "decide", cadence="hour", target="mara", params={"npc_id": "mara", "temperature": 0})
        self.e.advance_world("c", 60, "hazard reaction")
        self.assertEqual("safe", self.e.get_npc("c", "mara")["location"])

    def test_guaranteed_disaster_scheduler_injects_environment_event(self):
        self.e.environment_dispatch("set_disaster_config", "c", {
            "scope_id": "woods", "enabled": True,
            "profile": {"max_days": {"1": 2, "2": 99999, "3": 99999, "4": 99999, "5": 99999}, "types": {"1": "flood"}},
        })
        self.e.advance_world("c", 3 * 24 * 60, "disaster clock")
        events = self.e.recent_events("c", 200)
        self.assertTrue(any(x["event_type"] == "environment_disaster" for x in events))
        snap = self.e.environment_dispatch("snapshot", "c", {"location_id": "woods"})
        self.assertTrue(any(x["effect_type"] == "water" for x in snap["effects"]))

    def test_context_contains_environment_and_world_state_aggregate(self):
        self.e.environment_dispatch("apply_effect", "c", {"effect_type": "blight", "target": {"type": "location", "id": "woods"}, "intensity": 0.6})
        self.e.advance_world("c", 60, "blight")
        ctx = self.e.get_world_context("c", "woods")
        self.assertIn("environment", ctx)
        self.assertTrue(any(x["effect_type"] == "blight" for x in ctx["environment"]["location_effects"]))
        with self.e._db() as db:
            row = db.execute("SELECT value_json FROM world_state WHERE campaign_id='c' AND scope_type='location' AND scope_id='woods' AND state_key='environment.blight'").fetchone()
        self.assertIsNotNone(row)
        self.assertGreater(float(self.e._loads(row["value_json"])), 0)

    def test_environment_is_chunk_invariant(self):
        def run(path: Path, chunks: list[int]):
            e = WorldEngine(path); e.ensure_campaign("x", "X", "1492-07-01T08:00:00+00:00"); e.set_simulation_seed("x", 991)
            e.upsert_location("x", "barn", "Barn", tags=["outdoors"])
            e.world_systems_dispatch("save_map", "x", {"map_id":"b","name":"b","scope_type":"location","scope_id":"barn","bounds":{"min_x":0,"max_x":1,"min_y":0,"max_y":0,"min_z":0,"max_z":0}})
            for x in (0,1): e.world_systems_dispatch("save_tile","x",{"map_id":"b","x":x,"y":0,"z":0,"terrain":"wood beam","terrain_hp":30})
            e.world_systems_dispatch("set_climate", "x", {"scope_type":"location","scope_id":"barn","climate":"temperate","weather_weights":{"clear":1.0}})
            e.environment_dispatch("apply_effect", "x", {"effect_type":"fire","target":{"type":"tile","map_id":"b","x":0,"y":0,"z":0},"intensity":0.7})
            for mins in chunks: e.advance_world("x", mins, "chunk")
            with e._db() as db:
                effects=[(r["effect_type"],r["target_key"],round(float(r["intensity"]),6),round(float(r["amount"]),6),int(r["active"])) for r in db.execute("SELECT effect_type,target_key,intensity,amount,active FROM environment_effects WHERE campaign_id='x' ORDER BY effect_type,target_key")]
                tiles=[(int(r["x"]),round(float(r["terrain_hp"]),6)) for r in db.execute("SELECT x,terrain_hp FROM spatial_tiles WHERE campaign_id='x' AND map_id='b' ORDER BY x")]
                wx=[(r["condition"],round(float(r["temperature_c"]),6),round(float(r["wind_speed"]),6),r["wind_direction"]) for r in db.execute("SELECT * FROM environment_weather WHERE campaign_id='x' ORDER BY scope_type,scope_id")]
            return effects,tiles,wx,e.get_campaign("x")["world_time"]
        whole=run(Path(self.tmp.name)/"whole.sqlite3",[12*60])
        chunked=run(Path(self.tmp.name)/"chunked.sqlite3",[60]*12)
        self.assertEqual(whole,chunked)

    def test_fire_contribution_is_not_overwritten_by_stale_smoke_row(self):
        def smoke_after(path: Path, *, with_fire: bool, with_smoke: bool = True) -> tuple[float, float]:
            engine = WorldEngine(path)
            engine.ensure_campaign("x", "X", "1492-07-01T05:00:00+00:00")
            engine.upsert_location("x", "yard", "Yard", tags=["outdoors"])
            if with_smoke:
                engine.environment_dispatch(
                    "apply_effect", "x",
                    {"effect_type": "smoke", "target": {"type": "location", "id": "yard"}, "intensity": 0.5, "amount": 1.0},
                )
            if with_fire:
                engine.environment_dispatch(
                    "apply_effect", "x",
                    {"effect_type": "fire", "target": {"type": "location", "id": "yard"}, "intensity": 0.7, "amount": 1.0},
                )
            engine.advance_world("x", 60, "effect ordering")
            with engine._db() as db:
                row = db.execute(
                    "SELECT intensity,amount FROM environment_effects WHERE campaign_id='x' AND target_key='location:yard' AND effect_type='smoke'"
                ).fetchone()
            return float(row["intensity"]), float(row["amount"])

        smoke_only = smoke_after(Path(self.tmp.name) / "smoke_only.sqlite3", with_fire=False)
        fire_only = smoke_after(Path(self.tmp.name) / "fire_only.sqlite3", with_fire=True, with_smoke=False)
        fire_and_smoke = smoke_after(Path(self.tmp.name) / "fire_smoke.sqlite3", with_fire=True)
        self.assertGreater(fire_and_smoke[0], smoke_only[0])
        self.assertGreater(fire_and_smoke[1], smoke_only[1])
        self.assertAlmostEqual(smoke_only[1] + fire_only[1], fire_and_smoke[1], places=6)

    def test_environment_preserves_new_canonical_base_move_cost(self):
        self._map(max_x=0)
        self._tile(0, terrain="earth", hp=100)
        self.e.environment_dispatch(
            "apply_effect", "c",
            {"effect_type": "smoke", "target": {"type": "tile", "map_id": "m", "x": 0, "y": 0, "z": 0}, "intensity": 0.5},
        )
        self.e.world_systems_dispatch(
            "save_tile", "c",
            {"map_id": "m", "x": 0, "y": 0, "z": 0, "terrain": "earth", "walkable": True, "move_cost": 5.0, "terrain_hp": 100},
        )
        self.e.advance_world("c", 60, "environment after tile update")
        with self.e._db() as db:
            move_cost = float(db.execute(
                "SELECT move_cost FROM spatial_tiles WHERE campaign_id='c' AND map_id='m' AND x=0 AND y=0 AND z=0"
            ).fetchone()["move_cost"])
        self.assertEqual(5.0, move_cost)

    def test_ambient_weather_is_independent_of_active_effect_count(self):
        def weathered(path: Path, effects: list[str]) -> tuple[float, float, float]:
            engine = WorldEngine(path)
            engine.ensure_campaign("x", "X", "1492-07-01T05:00:00+00:00")
            engine.upsert_location("x", "yard", "Yard", tags=["outdoors"])
            engine.world_systems_dispatch("set_climate", "x", {
                "scope_type": "location", "scope_id": "yard", "climate": "temperate",
                "weather_weights": {"rain": 1.0},
            })
            for effect in effects:
                engine.environment_dispatch(
                    "apply_effect", "x",
                    {"effect_type": effect, "target": {"type": "location", "id": "yard"}, "intensity": 0.4},
                )
            engine.advance_world("x", 60, "one weather phase")
            target = next(
                row for row in engine.environment_dispatch("snapshot", "x", {"location_id": "yard"})["targets"]
                if row["target_key"] == "location:yard"
            )
            props = target["properties"]
            return float(props["wetness"]), float(props["temperature_c"]), float(props["humidity"])

        one = weathered(Path(self.tmp.name) / "one_effect.sqlite3", ["darkness"])
        two = weathered(Path(self.tmp.name) / "two_effects.sqlite3", ["darkness", "smoke"])
        self.assertEqual(one, two)

    def test_actor_with_active_effect_still_receives_forced_storm_lightning(self):
        self.e.upsert_character("c", "control", "Control", hp=100, max_hp=100, location="woods")
        self.e.world_systems_dispatch("set_climate", "c", {
            "scope_type": "location", "scope_id": "woods", "climate": "temperate",
            "weather_weights": {"storm": 1.0}, "state": {"actor_exposure": True},
        })
        self.e.environment_dispatch(
            "apply_effect", "c",
            {"effect_type": "darkness", "target": {"type": "actor", "actor_kind": "character", "actor_id": "hero"}, "intensity": 0.4},
        )
        with patch.object(EnvironmentKernel, "_rand_keyed", return_value=0.0):
            self.e.advance_world("c", 60, "forced lightning")
        with self.e._db() as db:
            struck = {
                row["target_key"] for row in db.execute(
                    "SELECT target_key FROM environment_effects WHERE campaign_id='c' AND effect_type='electricity' AND active=1"
                )
            }
        self.assertIn("actor:character:hero", struck)
        self.assertIn("actor:character:control", struck)

    def test_explosion_is_one_shot_damage_and_can_seed_fire(self):
        self._map(max_x=1)
        self._tile(0, terrain="wood crate", hp=20); self._tile(1, terrain="wood beam", hp=20)
        self.e.environment_dispatch("apply_effect", "c", {"effect_type":"explosion","target":{"type":"tile","map_id":"m","x":0,"y":0,"z":0},"intensity":1.0,"state":{"radius":1}})
        self.e.advance_world("c", 60, "blast")
        with self.e._db() as db:
            hp=[float(r["terrain_hp"]) for r in db.execute("SELECT terrain_hp FROM spatial_tiles WHERE campaign_id='c' AND map_id='m' ORDER BY x")]
            blast=db.execute("SELECT active,intensity FROM environment_effects WHERE campaign_id='c' AND effect_type='explosion'").fetchone()
        self.assertTrue(any(x < 20 for x in hp))
        self.assertEqual(0, int(blast["active"]))
        self.assertEqual(0.0, float(blast["intensity"]))

    def test_drought_depletes_resources_and_daily_hazard_moves_population_pressure(self):
        self.e.save_resource_node("c", "spring", "woods", "water", qty=100, qty_max=100, regen_per_day=10)
        self.e.world_systems_dispatch("set_population", "c", {"location_id":"woods","population":100,"food_capacity":120,"safety":0.9,"employment":0.5,"migration_pressure":0.0})
        self.e.environment_dispatch("apply_effect", "c", {"effect_type":"drought","target":{"type":"location","id":"woods"},"intensity":0.8})
        # Add a severe social-pressure hazard as well.
        self.e.environment_dispatch("apply_effect", "c", {"effect_type":"blight","target":{"type":"location","id":"woods"},"intensity":0.7})
        self.e.advance_world("c", 24*60, "drought day")
        with self.e._db() as db:
            qty=float(db.execute("SELECT qty FROM resource_nodes WHERE campaign_id='c' AND id='spring'").fetchone()["qty"])
            pop=db.execute("SELECT safety,migration_pressure FROM population_state WHERE campaign_id='c' AND location_id='woods'").fetchone()
        self.assertLess(qty,100)
        self.assertLess(float(pop["safety"]),0.9)
        self.assertGreater(float(pop["migration_pressure"]),0.0)
        self.assertTrue(any(x["event_type"]=="environment_social_pressure" for x in self.e.recent_events("c",200)))

    def test_visibility_recovers_after_smoke_expires(self):
        self.e.world_systems_dispatch("set_climate", "c", {"scope_type":"location","scope_id":"woods","climate":"temperate","weather_weights":{"clear":1.0}})
        self.e.environment_dispatch("apply_effect", "c", {"effect_type":"smoke","target":{"type":"location","id":"woods"},"intensity":0.9,"amount":2.0})
        self.e.advance_world("c", 60, "smoke")
        first=self.e.environment_dispatch("snapshot","c",{"location_id":"woods"})
        loc=next(t for t in first["targets"] if t["target_key"]=="location:woods")
        self.assertLess(loc["properties"].get("visibility",1.0),1.0)
        self.e.advance_world("c", 12*60, "smoke clears")
        second=self.e.environment_dispatch("snapshot","c",{"location_id":"woods"})
        loc=next(t for t in second["targets"] if t["target_key"]=="location:woods")
        self.assertGreater(loc["properties"].get("visibility",0.0),0.9)


if __name__ == "__main__":
    unittest.main()
