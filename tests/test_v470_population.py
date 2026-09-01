from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from world_engine import WorldEngine
from world_engine.population import PopulationKernel


class PopulationV470Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "world.sqlite3"
        self.e = WorldEngine(self.db)
        self.e.ensure_campaign("c", "Population", "1492-01-01T23:00:00+00:00")
        self.e.upsert_location("c", "origin", "Origin")
        self.e.upsert_location("c", "destination", "Destination")
        self.e.upsert_location("c", "isolated", "Isolated")
        self.e.save_location_link("c", "origin", "destination", 4, bidirectional=True)
        self.e.upsert_character("c", "hero", "Hero", hp=20, max_hp=20, location="origin")
        self.e.set_simulation_seed("c", 470)

    def tearDown(self):
        self.tmp.cleanup()

    def _settlement(self, location: str, **overrides):
        payload = {
            "location_id": location,
            "housing_capacity": 1000,
            "water_capacity": 1000,
            "prosperity": 0.5,
            "stability": 0.5,
            "attractiveness": 0.5,
        }
        payload.update(overrides)
        return self.e.population_dispatch("save_settlement", "c", payload)

    def _cohort(self, cohort_id: str, location: str, count: float, **overrides):
        payload = {
            "cohort_id": cohort_id,
            "location_id": location,
            "count": count,
            "age_band": "adult",
            "birth_rate_annual": 0,
            "death_rate_annual": 0,
            "labor_participation": 0.5,
            "replace_legacy": True,
        }
        payload.update(overrides)
        return self.e.population_dispatch("save_cohort", "c", payload)

    def test_schema_feature_and_capability(self):
        with self.e._db() as db:
            self.assertEqual(
                WorldEngine.SCHEMA_VERSION,
                db.execute("PRAGMA user_version").fetchone()[0],
            )
            for table in (
                "population_config",
                "settlement_profiles",
                "population_cohorts",
                "population_households",
                "settlement_labor",
                "settlement_service_needs",
                "population_flows",
            ):
                self.assertIsNotNone(
                    db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                        (table,),
                    ).fetchone()
                )
            feature = db.execute(
                "SELECT feature_version FROM we42_schema_features WHERE feature_id='population_lifecycle_settlement_runtime'"
            ).fetchone()
            self.assertEqual("4.7.0", feature["feature_version"])
        caps = {row["capability_id"] for row in self.e.list_capabilities("c")}
        self.assertIn("population.inspect", caps)

    def test_read_only_population_inspection_does_not_materialize_rows(self):
        with self.e._db() as db:
            before = {
                table: db.execute(f"SELECT COUNT(*) n FROM {table} WHERE campaign_id='c'").fetchone()["n"]
                for table in ("population_state", "settlement_profiles", "population_cohorts")
            }
            revision = db.execute("SELECT revision FROM campaigns WHERE id='c'").fetchone()[0]
        view = self.e.population_dispatch(
            "public_snapshot", "c", {"location_id": "isolated"}
        )
        self.assertEqual(0.0, view["settlement"]["population"])
        with self.e._db() as db:
            after = {
                table: db.execute(f"SELECT COUNT(*) n FROM {table} WHERE campaign_id='c'").fetchone()["n"]
                for table in ("population_state", "settlement_profiles", "population_cohorts")
            }
            after_revision = db.execute("SELECT revision FROM campaigns WHERE id='c'").fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(revision, after_revision)

    def test_legacy_population_summary_bootstraps_into_one_cohort(self):
        with self.e._write_db() as db:
            db.execute(
                "INSERT INTO population_state(campaign_id,location_id,population,food_capacity,safety,employment,migration_pressure,state_json,updated_at) VALUES('c','isolated',120,120,.5,.5,0,'{}',?)",
                (self.e._now(),),
            )
        result = self.e.advance_world("c", 120, "daily population bootstrap")
        self.assertEqual(1.0, result["simulation"]["population_bootstrapped"])
        with self.e._db() as db:
            row = db.execute(
                "SELECT count,birth_rate_annual,death_rate_annual,state_json FROM population_cohorts WHERE campaign_id='c' AND location_id='isolated'"
            ).fetchone()
        self.assertEqual(120.0, float(row["count"]))
        self.assertEqual(0.0, float(row["birth_rate_annual"]))
        self.assertEqual(0.0, float(row["death_rate_annual"]))
        self.assertTrue(self.e._loads(row["state_json"])["legacy_aggregate"])

    def test_aggregate_set_population_scales_existing_cohorts(self):
        self._settlement("origin")
        self.e.population_dispatch(
            "replace_cohorts",
            "c",
            {
                "location_id": "origin",
                "cohorts": [
                    {"id": "farmers", "count": 60, "age_band": "adult", "livelihood": "farmer", "birth_rate_annual": 0, "death_rate_annual": 0},
                    {"id": "children", "count": 40, "age_band": "child", "livelihood": "dependent", "birth_rate_annual": 0, "death_rate_annual": 0},
                ],
            },
        )
        self.e.world_systems_dispatch(
            "set_population", "c", {"location_id": "origin", "population": 200}
        )
        with self.e._db() as db:
            counts = {
                row["id"]: float(row["count"])
                for row in db.execute(
                    "SELECT id,count FROM population_cohorts WHERE campaign_id='c' AND location_id='origin'"
                )
            }
            total = float(
                db.execute(
                    "SELECT population FROM population_state WHERE campaign_id='c' AND location_id='origin'"
                ).fetchone()[0]
            )
        self.assertAlmostEqual(120.0, counts["farmers"])
        self.assertAlmostEqual(80.0, counts["children"])
        self.assertAlmostEqual(200.0, total)

    def test_births_are_deterministic_and_create_child_cohort(self):
        self._settlement("origin", housing_capacity=10000, water_capacity=10000)
        self._cohort(
            "adults",
            "origin",
            100,
            birth_rate_annual=365,
            death_rate_annual=0,
            health=1,
        )
        result = self.e.advance_world("c", 120, "birth day")
        births = result["simulation"]["population_births"]
        self.assertGreater(births, 0)
        with self.e._db() as db:
            child = db.execute(
                "SELECT count FROM population_cohorts WHERE campaign_id='c' AND location_id='origin' AND age_band='child'"
            ).fetchone()
            flow = db.execute(
                "SELECT count FROM population_flows WHERE campaign_id='c' AND kind='birth'"
            ).fetchone()
        self.assertIsNotNone(child)
        self.assertEqual(float(flow["count"]), births)
        self.assertEqual(float(child["count"]), births)

    def test_mortality_is_bounded_by_cohort_count(self):
        self._settlement("origin")
        self._cohort(
            "elders",
            "origin",
            10,
            age_band="elder",
            birth_rate_annual=0,
            death_rate_annual=365,
            health=0,
        )
        result = self.e.advance_world("c", 120, "mortality day")
        self.assertEqual(10.0, result["simulation"]["population_deaths"])
        with self.e._db() as db:
            remaining = float(
                db.execute(
                    "SELECT count FROM population_cohorts WHERE campaign_id='c' AND id='elders'"
                ).fetchone()[0]
            )
        self.assertEqual(0.0, remaining)

    def test_configured_age_transition_moves_people_between_cohorts(self):
        self._settlement("origin")
        self.e.population_dispatch(
            "replace_cohorts",
            "c",
            {
                "location_id": "origin",
                "cohorts": [
                    {"id": "adults", "count": 0, "age_band": "adult", "birth_rate_annual": 0, "death_rate_annual": 0},
                    {"id": "children", "count": 20, "age_band": "child", "next_cohort_id": "adults", "transition_rate_annual": 365, "birth_rate_annual": 0, "death_rate_annual": 0},
                ],
            },
        )
        result = self.e.advance_world("c", 120, "age transition")
        self.assertEqual(20.0, result["simulation"]["population_transitions"])
        with self.e._db() as db:
            counts = {
                row["id"]: float(row["count"])
                for row in db.execute(
                    "SELECT id,count FROM population_cohorts WHERE campaign_id='c' AND location_id='origin'"
                )
            }
        self.assertEqual(0.0, counts["children"])
        self.assertEqual(20.0, counts["adults"])

    def _prepare_migration(self, *, linked: bool = True, destination_capacity: float = 1000):
        if not linked:
            with self.e._write_db() as db:
                db.execute(
                    "DELETE FROM location_links WHERE campaign_id='c' AND ((from_id='origin' AND to_id='destination') OR (from_id='destination' AND to_id='origin'))"
                )
        self._settlement(
            "origin",
            housing_capacity=10,
            water_capacity=1000,
            prosperity=0,
            stability=0,
            attractiveness=0,
        )
        self._settlement(
            "destination",
            housing_capacity=destination_capacity,
            water_capacity=1000,
            prosperity=1,
            stability=1,
            attractiveness=1,
            auto_rank=True,
            rank="empty",
        )
        self._cohort(
            "origin_adults",
            "origin",
            100,
            migration_affinity=2,
            labor_participation=1,
        )
        self.e.population_dispatch(
            "configure",
            "c",
            {"max_migration_fraction_per_day": 1, "minimum_pull_delta": 0},
        )
        with self.e._write_db() as db:
            db.execute(
                "UPDATE population_state SET migration_pressure=1 WHERE campaign_id='c' AND location_id='origin'"
            )

    def test_migration_requires_link_and_prefers_higher_pull_settlement(self):
        self._prepare_migration(linked=True)
        result = self.e.advance_world("c", 120, "migration day")
        moved = result["simulation"]["population_migration"]
        self.assertGreater(moved, 0)
        with self.e._db() as db:
            origin = float(db.execute("SELECT population FROM population_state WHERE campaign_id='c' AND location_id='origin'").fetchone()[0])
            destination = float(db.execute("SELECT population FROM population_state WHERE campaign_id='c' AND location_id='destination'").fetchone()[0])
        self.assertAlmostEqual(100.0, origin + destination)
        self.assertAlmostEqual(moved, destination)

    def test_unlinked_settlement_receives_no_autonomous_migration(self):
        self._prepare_migration(linked=False)
        result = self.e.advance_world("c", 120, "no route")
        self.assertEqual(0.0, result["simulation"]["population_migration"])

    def test_destination_capacity_caps_migration(self):
        self._prepare_migration(linked=True, destination_capacity=5)
        result = self.e.advance_world("c", 120, "capacity limited migration")
        self.assertLessEqual(result["simulation"]["population_migration"], 5.0)
        with self.e._db() as db:
            destination = float(db.execute("SELECT population FROM population_state WHERE campaign_id='c' AND location_id='destination'").fetchone()[0])
        self.assertLessEqual(destination, 5.0)

    def test_food_housing_and_water_shortage_raise_migration_pressure(self):
        self.e.world_systems_dispatch(
            "set_population",
            "c",
            {
                "location_id": "origin",
                "population": 100,
                "food_capacity": 20,
                "safety": 0.5,
                "employment": 0.5,
                "migration_pressure": 0,
                "state": {"food_capacity_known": True},
            },
        )
        self._settlement("origin", housing_capacity=20, water_capacity=20)
        result = self.e.advance_world("c", 120, "shortage day")
        self.assertEqual(1.0, result["simulation"]["population_settlements"])
        view = self.e.population_dispatch("public_snapshot", "c", {"location_id": "origin"})
        self.assertGreaterEqual(view["settlement"]["migration_pressure"], 0.2)

    def test_active_environmental_hazard_reduces_safety(self):
        self.e.world_systems_dispatch(
            "set_population", "c", {"location_id": "origin", "population": 100, "food_capacity": 100}
        )
        self._settlement("origin")
        self.e.environment_dispatch(
            "apply_effect",
            "c",
            {
                "effect_type": "fire",
                "target": {"type": "location", "id": "origin"},
                "intensity": 1,
            },
        )
        view = self.e.population_dispatch("public_snapshot", "c", {"location_id": "origin"})
        self.assertEqual(1.0, view["settlement"]["hazard"])
        self.assertLess(view["settlement"]["safety"], 0.5)

    def test_labor_supply_limits_opt_in_economic_production(self):
        self._settlement("origin")
        self._cohort(
            "smiths",
            "origin",
            10,
            livelihood="smith",
            labor_participation=1,
        )
        self.e.save_item_def("c", "ore", "Ore", base_price=1)
        self.e.save_item_def("c", "ingot", "Ingot", base_price=2)
        with self.e._write_db() as db:
            db.execute(
                "INSERT INTO recipes(campaign_id,id,kind,inputs_json,output_item_id,output_qty,skill,dc,hours,station_tag,metadata_json,updated_at) VALUES('c','smelt','craft','{\"ore\":1}','ingot',1,NULL,10,1,NULL,'{}',?)",
                (self.e._now(),),
            )
        self.e.set_inventory_item("c", "location", "origin", "ore", 100)
        self.e.economy_dispatch(
            "save_producer",
            "c",
            {
                "producer_id": "forge",
                "location_id": "origin",
                "owner_kind": "location",
                "owner_id": "origin",
                "recipe_id": "smelt",
                "batches_per_day": 24,
                "state": {"workers_required": 20, "occupation": "smith"},
            },
        )
        self.e.advance_world("c", 24 * 60, "labor-limited production")
        with self.e._db() as db:
            labor = db.execute(
                "SELECT productivity FROM settlement_labor WHERE campaign_id='c' AND location_id='origin' AND occupation='smith'"
            ).fetchone()
        self.assertAlmostEqual(0.5, float(labor["productivity"]))
        with self.e._db() as db:
            ingots = db.execute(
                "SELECT qty FROM inventories WHERE campaign_id='c' AND owner_kind='location' AND owner_id='origin' AND item_id='ingot'"
            ).fetchone()
        self.assertEqual(12.0, float(ingots["qty"]))

    def test_service_gaps_use_authored_services_without_creating_them(self):
        self.e.world_systems_dispatch(
            "set_population", "c", {"location_id": "origin", "population": 1000, "food_capacity": 1000}
        )
        self._settlement("origin")
        self.e.population_dispatch("refresh", "c", {})
        with self.e._db() as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM town_services WHERE campaign_id='c'").fetchone()[0])
            initial = db.execute(
                "SELECT gap FROM settlement_service_needs WHERE campaign_id='c' AND location_id='origin' AND service_kind='guard'"
            ).fetchone()[0]
        self.assertGreater(float(initial), 0)
        self.e.world_systems_dispatch(
            "save_service",
            "c",
            {"service_id": "watch", "location_id": "origin", "kind": "guard", "name": "Watch", "state": {"capacity": 10}},
        )
        self.e.population_dispatch("refresh", "c", {})
        with self.e._db() as db:
            gap = float(db.execute("SELECT gap FROM settlement_service_needs WHERE campaign_id='c' AND location_id='origin' AND service_kind='guard'").fetchone()[0])
        self.assertEqual(0.0, gap)

    def test_derived_household_aggregate_and_authored_precedence(self):
        self._settlement("origin", housing_capacity=50)
        self.e.population_dispatch(
            "replace_cohorts",
            "c",
            {
                "location_id": "origin",
                "cohorts": [
                    {"id": "adults", "count": 60, "age_band": "adult", "birth_rate_annual": 0, "death_rate_annual": 0},
                    {"id": "children", "count": 30, "age_band": "child", "birth_rate_annual": 0, "death_rate_annual": 0},
                    {"id": "elders", "count": 10, "age_band": "elder", "birth_rate_annual": 0, "death_rate_annual": 0},
                ],
            },
        )
        self.e.population_dispatch("refresh", "c", {})
        view = self.e.population_dispatch(
            "public_snapshot", "c", {"location_id": "origin"}
        )
        summary = view["settlement"]["household_summary"]
        self.assertEqual("aggregate", summary["mode"])
        self.assertEqual(100.0, summary["persons"])
        with self.e._db() as db:
            generated = db.execute("SELECT state_json,status FROM population_households WHERE campaign_id='c' AND location_id='origin'").fetchone()
        self.assertTrue(self.e._loads(generated["state_json"])["generated_aggregate"])
        self.assertEqual("displaced", generated["status"])
        self.e.population_dispatch(
            "save_household",
            "c",
            {"household_id": "authored", "location_id": "origin", "household_count": 2, "persons": 8, "adults": 4, "children": 4, "housing_units": 2},
        )
        self.e.population_dispatch("refresh", "c", {})
        with self.e._db() as db:
            rows = db.execute("SELECT id FROM population_households WHERE campaign_id='c' AND location_id='origin' ORDER BY id").fetchall()
        self.assertEqual(["authored"], [row["id"] for row in rows])

    def test_empty_candidate_can_be_founded_and_auto_ranked(self):
        self._prepare_migration(linked=True)
        self.e.advance_world("c", 120, "found outpost")
        view = self.e.population_dispatch("public_snapshot", "c", {"location_id": "destination"})
        self.assertGreater(view["settlement"]["population"], 0)
        self.assertNotEqual("empty", view["settlement"]["rank"])

    def test_population_inspect_alias_uses_actor_location(self):
        self.e.world_systems_dispatch(
            "set_population", "c", {"location_id": "origin", "population": 42, "food_capacity": 42}
        )
        result = self.e.resolve_turn(
            "c",
            actor_kind="character",
            actor_id="hero",
            intents=[{"type": "population", "parameters": {}}],
        )
        self.assertEqual("completed", result["status"])
        step = result["steps"][0]
        self.assertEqual("population.inspect", step["capability_id"])
        self.assertEqual(42.0, step["result"]["settlement"]["population"])

    def test_world_and_compiled_context_project_population(self):
        self.e.world_systems_dispatch(
            "set_population", "c", {"location_id": "origin", "population": 88, "food_capacity": 88}
        )
        world = self.e.get_world_context("c", "origin")
        self.assertEqual(88.0, world["population"]["settlement"]["population"])
        compiled = self.e.compile_turn_context(
            "c",
            actor_kind="character",
            actor_id="hero",
            location_id="origin",
            capability_ids=["population.inspect"],
        )
        components = compiled.get("included_components") or compiled.get("components") or []
        rendered = self.e._dumps(compiled)
        self.assertTrue(components or rendered)
        self.assertIn("world:population", rendered)

    def test_named_npc_lifecycle_remains_separate(self):
        self.e.upsert_npc("c", "elder", "Elder", location="origin")
        self.e.save_npc_lifecycle(
            "c", "elder", birth_year=1400, mortality={"enabled": False}, fertility={"enabled": False}
        )
        self._settlement("origin")
        self._cohort("residents", "origin", 100)
        result = self.e.advance_world("c", 120, "separate lifecycles")
        self.assertEqual(0, result["simulation"]["lifecycle"])
        self.assertTrue(self.e.get_npc_lifecycle("c", "elder")["alive"])
        self.assertEqual(100.0, self.e.population_dispatch("public_snapshot", "c", {"location_id": "origin"})["settlement"]["population"])

    def test_population_chunk_invariance(self):
        def run(path: Path, chunks: list[int]):
            e = WorldEngine(path)
            e.ensure_campaign("x", "X", "1492-01-01T00:00:00+00:00")
            e.upsert_location("x", "a", "A")
            e.upsert_location("x", "b", "B")
            e.save_location_link("x", "a", "b", 2, bidirectional=True)
            e.set_simulation_seed("x", 123)
            e.population_dispatch("save_settlement", "x", {"location_id": "a", "housing_capacity": 50, "water_capacity": 500, "prosperity": 0, "stability": 0, "attractiveness": 0})
            e.population_dispatch("save_settlement", "x", {"location_id": "b", "housing_capacity": 500, "water_capacity": 500, "prosperity": 1, "stability": 1, "attractiveness": 1})
            e.population_dispatch("save_cohort", "x", {"cohort_id": "a_adults", "location_id": "a", "count": 70, "age_band": "adult", "birth_rate_annual": .1, "death_rate_annual": .05, "migration_affinity": 1, "replace_legacy": True})
            e.population_dispatch("save_cohort", "x", {"cohort_id": "a_children", "location_id": "a", "count": 30, "age_band": "child", "birth_rate_annual": 0, "death_rate_annual": .02, "migration_affinity": 1, "next_cohort_id": "a_adults", "transition_rate_annual": .1})
            e.population_dispatch("configure", "x", {"max_migration_fraction_per_day": .25, "minimum_pull_delta": 0})
            with e._write_db() as db:
                db.execute("UPDATE population_state SET migration_pressure=1 WHERE campaign_id='x' AND location_id='a'")
            for minutes in chunks:
                e.advance_world("x", minutes, "chunk")
            with e._db() as db:
                cohorts = [
                    (
                        row["id"],
                        row["location_id"],
                        round(float(row["count"]), 9),
                        row["next_cohort_id"],
                        row["last_processed_world_time"],
                    )
                    for row in db.execute(
                        "SELECT id,location_id,count,next_cohort_id,last_processed_world_time FROM population_cohorts WHERE campaign_id='x' ORDER BY id"
                    )
                ]
                flows = [
                    (row["kind"], row["origin_location_id"], row["destination_location_id"], round(float(row["count"]), 9), row["world_time"])
                    for row in db.execute("SELECT kind,origin_location_id,destination_location_id,count,world_time FROM population_flows WHERE campaign_id='x' ORDER BY id")
                ]
                states = [
                    (row["location_id"], round(float(row["population"]), 9), round(float(row["migration_pressure"]), 9))
                    for row in db.execute("SELECT location_id,population,migration_pressure FROM population_state WHERE campaign_id='x' ORDER BY location_id")
                ]
            return cohorts, flows, states, e.get_campaign("x")["world_time"]

        one = run(Path(self.tmp.name) / "one.sqlite3", [3 * 24 * 60])
        chunks = run(Path(self.tmp.name) / "chunks.sqlite3", [6 * 60] * 12)
        self.assertEqual(one, chunks)

    def test_modeled_labor_does_not_overwrite_total_employment_without_opt_in(self):
        self._settlement("origin")
        self._cohort(
            "smiths",
            "origin",
            100,
            livelihood="smith",
            labor_participation=1,
        )
        with self.e._write_db() as db:
            db.execute(
                "UPDATE population_state SET employment=.8 WHERE campaign_id='c' AND location_id='origin'"
            )
        self.e.save_item_def("c", "ore-labor", "Ore", base_price=1)
        self.e.save_item_def("c", "ingot-labor", "Ingot", base_price=2)
        with self.e._write_db() as db:
            db.execute(
                "INSERT INTO recipes(campaign_id,id,kind,inputs_json,output_item_id,output_qty,skill,dc,hours,station_tag,metadata_json,updated_at) VALUES('c','smelt-labor','craft','{\"ore-labor\":1}','ingot-labor',1,NULL,10,1,NULL,'{}',?)",
                (self.e._now(),),
            )
        self.e.economy_dispatch(
            "save_producer",
            "c",
            {
                "producer_id": "small-forge",
                "location_id": "origin",
                "owner_kind": "location",
                "owner_id": "origin",
                "recipe_id": "smelt-labor",
                "batches_per_day": 1,
                "state": {"workers_required": 10, "occupation": "smith"},
            },
        )

        self.e.population_dispatch("refresh", "c", {})
        with self.e._db() as db:
            row = db.execute(
                "SELECT employment,state_json FROM population_state WHERE campaign_id='c' AND location_id='origin'"
            ).fetchone()
        self.assertAlmostEqual(0.8, float(row["employment"]))
        state = self.e._loads(row["state_json"])
        self.assertAlmostEqual(0.1, float(state["modeled_labor_coverage"]))

        self._settlement(
            "origin", state={"derive_employment_from_labor": True}
        )
        self.e.population_dispatch("refresh", "c", {})
        with self.e._db() as db:
            employment = float(
                db.execute(
                    "SELECT employment FROM population_state WHERE campaign_id='c' AND location_id='origin'"
                ).fetchone()[0]
            )
        self.assertAlmostEqual(0.1, employment)

    def test_generated_child_cohort_uses_explicit_or_default_child_mortality(self):
        self._settlement("origin", housing_capacity=10000, water_capacity=10000)
        self.e.population_dispatch(
            "configure",
            "c",
            {
                "mortality_enabled": False,
                "default_death_rate_annual": 0.02,
            },
        )
        self._cohort(
            "high-risk-adults",
            "origin",
            100,
            birth_rate_annual=365,
            death_rate_annual=5,
            health=1,
        )
        self.e.advance_world("c", 120, "birth without inherited adult mortality")
        with self.e._db() as db:
            child = db.execute(
                "SELECT death_rate_annual FROM population_cohorts WHERE campaign_id='c' AND location_id='origin' AND age_band='child'"
            ).fetchone()
        self.assertIsNotNone(child)
        self.assertAlmostEqual(0.02, float(child["death_rate_annual"]))

    def test_migrated_child_cohort_preserves_destination_aging_chain(self):
        self._settlement("origin", housing_capacity=1000, water_capacity=1000)
        self._settlement("destination", housing_capacity=1000, water_capacity=1000)
        self.e.population_dispatch(
            "replace_cohorts",
            "c",
            {
                "location_id": "origin",
                "cohorts": [
                    {
                        "id": "origin-adults",
                        "count": 0,
                        "age_band": "adult",
                        "birth_rate_annual": 0,
                        "death_rate_annual": 0,
                    },
                    {
                        "id": "origin-children",
                        "count": 20,
                        "age_band": "child",
                        "next_cohort_id": "origin-adults",
                        "transition_rate_annual": 365,
                        "birth_rate_annual": 0,
                        "death_rate_annual": 0,
                    },
                ],
            },
        )
        result = self.e.world_systems_dispatch(
            "migrate",
            "c",
            {"origin": "origin", "destination": "destination", "count": 20},
        )
        self.assertAlmostEqual(20.0, result["moved"])
        with self.e._db() as db:
            child = db.execute(
                "SELECT id,next_cohort_id FROM population_cohorts WHERE campaign_id='c' AND location_id='destination' AND age_band='child'"
            ).fetchone()
            adult = db.execute(
                "SELECT id,count FROM population_cohorts WHERE campaign_id='c' AND location_id='destination' AND age_band='adult'"
            ).fetchone()
        self.assertIsNotNone(child)
        self.assertIsNotNone(adult)
        self.assertEqual(adult["id"], child["next_cohort_id"])
        self.assertEqual(0.0, float(adult["count"]))

        transition = self.e.advance_world("c", 120, "migrant cohort ages")
        self.assertAlmostEqual(20.0, transition["simulation"]["population_transitions"])
        with self.e._db() as db:
            counts = {
                row["age_band"]: float(row["count"])
                for row in db.execute(
                    "SELECT age_band,count FROM population_cohorts WHERE campaign_id='c' AND location_id='destination'"
                )
            }
        self.assertAlmostEqual(0.0, counts["child"])
        self.assertAlmostEqual(20.0, counts["adult"])

    def test_fractional_explicit_migration_never_overdraws_cohorts(self):
        self._settlement("origin")
        self._settlement("destination")
        self.e.population_dispatch(
            "replace_cohorts",
            "c",
            {
                "location_id": "origin",
                "cohorts": [
                    {"id": "fraction-a", "count": 0.1, "age_band": "adult"},
                    {"id": "fraction-b", "count": 0.2, "age_band": "adult"},
                ],
            },
        )
        result = self.e.world_systems_dispatch(
            "migrate",
            "c",
            {"origin": "origin", "destination": "destination", "count": 0.3},
        )
        with self.e._db() as db:
            origin_counts = [
                float(row[0])
                for row in db.execute(
                    "SELECT count FROM population_cohorts WHERE campaign_id='c' AND location_id='origin'"
                )
            ]
            destination_total = float(
                db.execute(
                    "SELECT COALESCE(SUM(count),0) FROM population_cohorts WHERE campaign_id='c' AND location_id='destination'"
                ).fetchone()[0]
            )
        self.assertTrue(all(value >= 0 for value in origin_counts))
        self.assertAlmostEqual(destination_total, result["moved"])
        self.assertLessEqual(result["moved"], 0.3)

    def test_public_projection_is_location_bound_and_omits_cohort_ids(self):
        self._settlement("origin")
        self._settlement("destination")
        self._cohort("barons-assassin-roster", "origin", 12)
        self._cohort("distant-roster", "destination", 77)

        with self.assertRaisesRegex(ValueError, "actor-local location_id"):
            self.e.population_dispatch("public_snapshot", "c", {})
        public = self.e.population_dispatch(
            "public_snapshot", "c", {"location_id": "origin"}
        )
        self.assertEqual("origin", public["location_id"])
        self.assertNotIn("settlements", public)
        self.assertEqual(12.0, public["settlement"]["population"])
        self.assertTrue(public["settlement"]["cohorts"])
        self.assertTrue(
            all("id" not in cohort for cohort in public["settlement"]["cohorts"])
        )
        self.assertNotIn("barons-assassin-roster", repr(public))
        self.assertNotIn("distant-roster", repr(public))

    def test_service_model_off_deletes_all_stale_service_rows(self):
        self.e.world_systems_dispatch(
            "set_population",
            "c",
            {"location_id": "origin", "population": 1000, "food_capacity": 1000},
        )
        self._settlement("origin")
        self.e.population_dispatch("refresh", "c", {})
        with self.e._db() as db:
            self.assertGreater(
                db.execute(
                    "SELECT COUNT(*) FROM settlement_service_needs WHERE campaign_id='c' AND location_id='origin'"
                ).fetchone()[0],
                0,
            )
        self._settlement("origin", state={"service_model": "off"})
        self.e.population_dispatch("refresh", "c", {})
        with self.e._db() as db:
            self.assertEqual(
                0,
                db.execute(
                    "SELECT COUNT(*) FROM settlement_service_needs WHERE campaign_id='c' AND location_id='origin'"
                ).fetchone()[0],
            )

    def test_zero_labor_demand_clears_metadata_and_opted_in_employment(self):
        self._settlement(
            "origin", state={"derive_employment_from_labor": True}
        )
        self._cohort(
            "smiths-zero-demand",
            "origin",
            100,
            livelihood="smith",
            labor_participation=1,
        )
        self.e.save_item_def("c", "ore-zero-demand", "Ore", base_price=1)
        self.e.save_item_def("c", "ingot-zero-demand", "Ingot", base_price=2)
        with self.e._write_db() as db:
            db.execute(
                "INSERT INTO recipes(campaign_id,id,kind,inputs_json,output_item_id,output_qty,skill,dc,hours,station_tag,metadata_json,updated_at) VALUES('c','smelt-zero-demand','craft','{\"ore-zero-demand\":1}','ingot-zero-demand',1,NULL,10,1,NULL,'{}',?)",
                (self.e._now(),),
            )
        self.e.economy_dispatch(
            "save_producer",
            "c",
            {
                "producer_id": "forge-zero-demand",
                "location_id": "origin",
                "owner_kind": "location",
                "owner_id": "origin",
                "recipe_id": "smelt-zero-demand",
                "batches_per_day": 1,
                "state": {"workers_required": 10, "occupation": "smith"},
            },
        )
        self.e.population_dispatch("refresh", "c", {})
        with self.e._db() as db:
            state = self.e._loads(
                db.execute(
                    "SELECT state_json FROM population_state WHERE campaign_id='c' AND location_id='origin'"
                ).fetchone()[0]
            )
            self.assertIn("modeled_labor_demand", state)
            self.assertEqual(
                1,
                db.execute(
                    "SELECT COUNT(*) FROM settlement_labor WHERE campaign_id='c' AND location_id='origin'"
                ).fetchone()[0],
            )
            db.execute(
                "UPDATE economy_producers SET active=0 WHERE campaign_id='c' AND id='forge-zero-demand'"
            )

        self.e.population_dispatch("refresh", "c", {})
        with self.e._db() as db:
            row = db.execute(
                "SELECT employment,state_json FROM population_state WHERE campaign_id='c' AND location_id='origin'"
            ).fetchone()
            self.assertEqual(0.0, float(row["employment"]))
            state = self.e._loads(row["state_json"])
            self.assertNotIn("modeled_labor_coverage", state)
            self.assertNotIn("modeled_labor_demand", state)
            self.assertNotIn("modeled_labor_filled", state)
            self.assertEqual(
                0,
                db.execute(
                    "SELECT COUNT(*) FROM settlement_labor WHERE campaign_id='c' AND location_id='origin'"
                ).fetchone()[0],
            )

    def test_numeric_authoring_rejects_bool_nonfinite_and_out_of_range(self):
        invalid_calls = (
            lambda: self.e.population_dispatch(
                "configure", "c", {"default_birth_rate_annual": float("inf")}
            ),
            lambda: self.e.population_dispatch(
                "configure", "c", {"max_migration_fraction_per_day": 1.01}
            ),
            lambda: self.e.population_dispatch(
                "configure", "c", {"service_event_cooldown_days": True}
            ),
            lambda: self.e.population_dispatch(
                "save_settlement", "c", {"location_id": "origin", "sanitation": float("nan")}
            ),
            lambda: self.e.population_dispatch(
                "save_cohort",
                "c",
                {"cohort_id": "bad-bool", "location_id": "origin", "count": True},
            ),
            lambda: self.e.population_dispatch(
                "save_cohort",
                "c",
                {"cohort_id": "bad-health", "location_id": "origin", "health": 1.1},
            ),
            lambda: self.e.population_dispatch(
                "save_household",
                "c",
                {"household_id": "bad-household", "location_id": "origin", "persons": float("-inf")},
            ),
            lambda: self.e.population_dispatch(
                "snapshot", "c", {"location_id": "origin", "limit": True}
            ),
            lambda: self.e.population_dispatch(
                "snapshot", "c", {"location_id": "origin", "limit": 101}
            ),
        )
        for index, call in enumerate(invalid_calls):
            with self.subTest(case=index), self.assertRaises(ValueError):
                call()

    def test_refresh_explicitly_advances_revision_and_emits_one_event(self):
        self._settlement("origin")
        with self.e._db() as db:
            revision_before = db.execute(
                "SELECT revision FROM campaigns WHERE id='c'"
            ).fetchone()[0]
            events_before = db.execute(
                "SELECT COUNT(*) FROM events WHERE campaign_id='c' AND event_type='population_refreshed'"
            ).fetchone()[0]
        receipt = self.e.population_dispatch("refresh", "c", {})
        with self.e._db() as db:
            revision_after = db.execute(
                "SELECT revision FROM campaigns WHERE id='c'"
            ).fetchone()[0]
            events_after = db.execute(
                "SELECT COUNT(*) FROM events WHERE campaign_id='c' AND event_type='population_refreshed'"
            ).fetchone()[0]
        self.assertEqual(revision_before + 1, revision_after)
        self.assertEqual(events_before + 1, events_after)
        self.assertEqual(revision_after, receipt["revision"])
        self.assertTrue(receipt["mutation"]["revision_advanced"])

    def test_generated_population_promotion_is_atomic_additive_and_idempotent(self):
        sections = {
            "settlement_profiles": [
                {
                    "location_id": "origin",
                    "housing_capacity": 250,
                    "water_capacity": 300,
                    "state": {"source": "procedural"},
                }
            ],
            "population_cohorts": [
                {
                    "location_id": "origin",
                    "species": "human",
                    "culture": "riverfolk",
                    "age_band": "adult",
                    "livelihood": "fisher",
                    "count": 25,
                    "state": {"source": "procedural"},
                }
            ],
        }
        kernel = PopulationKernel(self.e)
        with self.e._db() as db:
            revision_before = db.execute(
                "SELECT revision FROM campaigns WHERE id='c'"
            ).fetchone()[0]
        with self.e._write_db() as db:
            first = kernel.promote_records_db(db, "c", sections)
        self.assertEqual(1, first["settlement_profiles_inserted"])
        self.assertEqual(1, first["population_cohorts_inserted"])

        with self.e._write_db() as db:
            changes_before = db.total_changes
            replay = kernel.promote_records_db(db, "c", sections)
            self.assertEqual(changes_before, db.total_changes)
        self.assertEqual(1, replay["settlement_profiles_replayed"])
        self.assertEqual(1, replay["population_cohorts_replayed"])
        with self.e._db() as db:
            revision_after = db.execute(
                "SELECT revision FROM campaigns WHERE id='c'"
            ).fetchone()[0]
        self.assertEqual(revision_before, revision_after)

        conflicting = {
            "settlement_profiles": [{"location_id": "isolated"}],
            "population_cohorts": [
                {
                    "location_id": "origin",
                    "species": "human",
                    "culture": "riverfolk",
                    "age_band": "adult",
                    "livelihood": "fisher",
                    "count": 26,
                    "state": {"source": "procedural"},
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "conflict"), self.e._write_db() as db:
            kernel.promote_records_db(db, "c", conflicting)
        with self.e._db() as db:
            self.assertIsNone(
                db.execute(
                    "SELECT 1 FROM settlement_profiles WHERE campaign_id='c' AND location_id='isolated'"
                ).fetchone()
            )

        private = {
            "population_cohorts": [
                {
                    "location_id": "isolated",
                    "count": 1,
                    "state": {"secret": "not canonical public generation"},
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "not allowed"), self.e._write_db() as db:
            kernel.promote_records_db(db, "c", private)

    def test_population_accounting_remains_finite_and_conservative(self):
        self._settlement("origin")
        self._settlement("destination")
        self._cohort(
            "conservation-adults",
            "origin",
            1000,
            birth_rate_annual=0.365,
            death_rate_annual=0.1825,
            migration_affinity=1,
        )
        with self.e._db() as db:
            before = float(
                db.execute(
                    "SELECT SUM(count) FROM population_cohorts WHERE campaign_id='c'"
                ).fetchone()[0]
            )
        self.e.advance_world("c", 24 * 60, "population conservation probe")
        with self.e._db() as db:
            after = float(
                db.execute(
                    "SELECT SUM(count) FROM population_cohorts WHERE campaign_id='c'"
                ).fetchone()[0]
            )
            flows = {
                row["kind"]: float(row["n"])
                for row in db.execute(
                    "SELECT kind,SUM(count) n FROM population_flows WHERE campaign_id='c' GROUP BY kind"
                )
            }
            counts = [
                float(row[0])
                for row in db.execute(
                    "SELECT count FROM population_cohorts WHERE campaign_id='c'"
                )
            ]
        self.assertTrue(all(value >= 0 and value < float("inf") for value in counts))
        self.assertAlmostEqual(
            before + flows.get("birth", 0.0) - flows.get("death", 0.0),
            after,
        )

    def test_pre_population_database_migrates_forward_without_losing_economy(self):
        self.e.save_item_def("c", "grain", "Grain", base_price=2)
        self.e.economy_dispatch(
            "save_market", "c", {"market_id": "market", "location_id": "origin", "name": "Market"}
        )
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            for table in (
                "population_flows",
                "settlement_service_needs",
                "settlement_labor",
                "population_households",
                "population_cohorts",
                "settlement_profiles",
                "population_config",
            ):
                db.execute(f"DROP TABLE IF EXISTS {table}")
            db.execute("DELETE FROM we42_schema_features WHERE feature_id='population_lifecycle_settlement_runtime'")
            db.execute("DELETE FROM we4_capability_manifests WHERE capability_id='population.inspect'")
            db.execute("PRAGMA user_version=18")
        migrated = WorldEngine(self.db)
        migrated.ensure_campaign("c")
        with migrated._db() as db:
            self.assertEqual(
                WorldEngine.SCHEMA_VERSION,
                db.execute("PRAGMA user_version").fetchone()[0],
            )
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM economy_markets WHERE campaign_id='c' AND id='market'").fetchone()[0])
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='population_cohorts'").fetchone())


if __name__ == "__main__":
    unittest.main()
