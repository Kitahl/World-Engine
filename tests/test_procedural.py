import copy
import json
import tempfile
import unittest
from pathlib import Path

from world_engine import WorldEngine
from world_engine.authoring import AuthoringKernel
from world_engine.environment import EnvironmentKernel
from world_engine.procedural import GENERATION_CONTRACT_VERSION, ProceduralWorldGenerator, _digest


class ProceduralWorldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "procedural.sqlite3"
        self.engine = WorldEngine(self.path)
        self.engine.ensure_campaign("c", "Generated")
        self.engine.set_simulation_seed("c", 777)

    def tearDown(self):
        self.tmp.cleanup()

    def _counts(self):
        tables = (
            "world_bible", "item_defs", "locations", "location_links", "factions",
            "npc_archetypes", "npcs", "characters", "resource_nodes", "quests",
            "faction_relations", "regional_climate",
        )
        with self.engine._db() as db:
            return {table: db.execute(f"SELECT COUNT(*) FROM {table} WHERE campaign_id='c'").fetchone()[0] for table in tables}

    def _stage(self, *, batch="generated", seed="same-seed", namespace="bootstrap", mode="bootstrap"):
        revision = self.engine.get_campaign("c")["revision"]
        return self.engine.stage_generated_world(
            "c", batch, seed, namespace=namespace, mode=mode, expected_revision=revision
        )

    def _promote(self, *, batch="generated", seed="same-seed", namespace="bootstrap", mode="bootstrap"):
        staged = self._stage(batch=batch, seed=seed, namespace=namespace, mode=mode)
        validation = self.engine.author_validate("c", batch)
        self.assertTrue(validation["valid"], validation)
        dry_run = self.engine.author_dry_run("c", batch, days=1)
        self.assertTrue(dry_run["passed"], dry_run)
        promoted = self.engine.author_promote("c", batch)
        return staged, promoted

    def test_same_seed_and_config_repeat_exactly(self):
        first = self.engine.generate_world(42, {"location_count": 7}, namespace="alpha")
        second = self.engine.generate_world(42, {"location_count": 7}, namespace="alpha")
        self.assertEqual(first, second)
        self.assertEqual(
            json.dumps(first, sort_keys=True, separators=(",", ":")),
            json.dumps(second, sort_keys=True, separators=(",", ":")),
        )
        self.assertEqual(GENERATION_CONTRACT_VERSION, first["contract_version"])
        self.assertEqual(first["content_digest"], first["manifest"]["content_digest"])

    def test_different_seed_changes_content(self):
        first = self.engine.generate_world("one", namespace="alpha")
        second = self.engine.generate_world("two", namespace="alpha")
        self.assertNotEqual(first["content_digest"], second["content_digest"])
        self.assertNotEqual(first["payload"], second["payload"])

    def test_generated_biome_links_are_geographically_coherent(self):
        incompatible = {frozenset({"desert", "tundra"})}
        for seed in range(100):
            payload = self.engine.generate_world(seed, namespace="geography")["payload"]
            biome = {row["id"]: row["state"]["biome"] for row in payload["locations"]}
            for link in payload["location_links"]:
                if link["from_id"] not in biome or link["to_id"] not in biome:
                    continue
                self.assertNotIn(
                    frozenset({biome[link["from_id"]], biome[link["to_id"]]}),
                    incompatible,
                    (seed, link),
                )
            for location in payload["locations"]:
                if location["state"]["biome"] == "coast":
                    self.assertEqual("Old Coast", location["region"])
                if location["state"]["biome"] == "tundra":
                    self.assertEqual("North Reach", location["region"])

    def test_connected_topology_and_reference_integrity(self):
        result = self._stage()
        payload = result["generation"]["payload"]
        location_ids = {row["id"] for row in payload["locations"]}
        graph = {location_id: set() for location_id in location_ids}
        for link in payload["location_links"]:
            self.assertIn(link["from_id"], location_ids)
            self.assertIn(link["to_id"], location_ids)
            graph[link["from_id"]].add(link["to_id"])
            graph[link["to_id"]].add(link["from_id"])
        reached = {next(iter(location_ids))}
        pending = list(reached)
        while pending:
            for neighbor in graph[pending.pop()] - reached:
                reached.add(neighbor)
                pending.append(neighbor)
        self.assertEqual(location_ids, reached)
        validation = self.engine.author_validate("c", "generated")
        self.assertTrue(validation["valid"], validation)
        self.assertGreater(validation["counts"]["factions"], 0)
        self.assertGreater(validation["counts"]["npcs"], 0)
        self.assertEqual(1, validation["counts"]["characters"])
        self.assertGreater(validation["counts"]["resource_nodes"], 0)
        self.assertGreater(validation["counts"]["quests"], 0)
        self.assertEqual(len(location_ids), validation["counts"]["climates"])

    def test_generated_biomes_drive_authoritative_climate_after_promotion(self):
        generated = self.engine.generate_world("weather-seed", namespace="weather")
        locations = generated["payload"]["locations"]
        climates = generated["payload"]["climates"]
        self.assertEqual(len(locations), len(climates))
        self.assertEqual(
            {row["id"] for row in locations},
            {row["scope_id"] for row in climates},
        )
        self.assertTrue(all(row["scope_type"] == "location" for row in climates))
        self.assertTrue(all(row["state"]["actor_exposure"] is False for row in climates))
        self._promote(seed="weather-seed", namespace="weather")
        with self.engine._db() as db:
            self.assertTrue(EnvironmentKernel(self.engine).has_activity_db(db, "c"))
            stored = db.execute(
                "SELECT COUNT(*) FROM regional_climate WHERE campaign_id='c'"
            ).fetchone()[0]
        self.assertEqual(len(climates), stored)
        self.engine.advance_world("c", 360, "generated weather")
        context = self.engine.get_world_context("c", location="weather__location_01")
        self.assertTrue(context["environment"]["weather"])

    def test_generate_and_stage_are_nonmutating_and_stage_only(self):
        before_revision = self.engine.get_campaign("c")["revision"]
        before_rng = self.engine.simulation_config("c")["rng_counter"]
        generated = self.engine.generate_world("no-side-effect", namespace="alpha")
        self.assertEqual({}, {key: value for key, value in self._counts().items() if value})
        staged = self.engine.stage_generated_world(
            "c", "only_stage", "no-side-effect", namespace="alpha",
            expected_revision=before_revision,
        )
        self.assertEqual("staged", staged["batch"]["status"])
        self.assertEqual(generated["content_digest"], staged["generation"]["content_digest"])
        self.assertEqual({}, {key: value for key, value in self._counts().items() if value})
        self.assertEqual(before_revision, self.engine.get_campaign("c")["revision"])
        self.assertEqual(before_rng, self.engine.simulation_config("c")["rng_counter"])

    def test_dry_run_does_not_mutate_live_world_or_rng(self):
        self._stage()
        self.assertTrue(self.engine.author_validate("c", "generated")["valid"])
        before_revision = self.engine.get_campaign("c")["revision"]
        before_rng = self.engine.simulation_config("c")["rng_counter"]
        before_counts = self._counts()
        result = self.engine.author_dry_run("c", "generated", days=1)
        self.assertTrue(result["passed"], result)
        self.assertEqual(before_counts, self._counts())
        self.assertEqual(before_revision, self.engine.get_campaign("c")["revision"])
        self.assertEqual(before_rng, self.engine.simulation_config("c")["rng_counter"])

    def test_generated_dry_run_is_bounded_to_one_year(self):
        self._stage()
        result = self.engine.author_dry_run("c", "generated", days=1000)
        self.assertEqual(365, result["days"])

    def test_atomic_promotion_materializes_all_sections(self):
        before_revision = self.engine.get_campaign("c")["revision"]
        staged, promoted = self._promote()
        self.assertEqual("promoted", promoted["status"])
        self.assertEqual(before_revision + 1, self.engine.get_campaign("c")["revision"])
        counts = self._counts()
        for table in ("world_bible", "item_defs", "locations", "location_links", "factions", "npc_archetypes", "npcs", "characters", "resource_nodes", "quests", "faction_relations", "regional_climate"):
            self.assertGreater(counts[table], 0, table)
        manifest_counts = staged["generation"]["manifest"]["counts"]
        self.assertEqual(manifest_counts["locations"], counts["locations"])
        self.assertEqual(manifest_counts["factions"], counts["factions"])
        self.assertEqual(manifest_counts["climates"], counts["regional_climate"])

    def test_late_canon_lock_collision_rolls_back_entire_promotion(self):
        staged = self._stage()
        self.assertTrue(self.engine.author_validate("c", "generated")["valid"])
        self.assertTrue(self.engine.author_dry_run("c", "generated", days=1)["passed"])
        relation = staged["generation"]["payload"]["faction_relations"][-1]
        lock_id = "|".join(sorted((relation["faction_a"], relation["faction_b"])))
        self.engine.author_lock("c", "faction_relation", lock_id, reason="established diplomacy")
        with self.assertRaisesRegex(ValueError, "canon-locked"):
            self.engine.author_promote("c", "generated")
        self.assertEqual({}, {key: value for key, value in self._counts().items() if value})

    def test_bootstrap_refuses_materially_nonempty_campaign(self):
        self.engine.upsert_location("c", "existing", "Existing")
        revision = self.engine.get_campaign("c")["revision"]
        with self.assertRaisesRegex(ValueError, "materially empty"):
            self.engine.stage_generated_world(
                "c", "bootstrap", "seed", namespace="bootstrap",
                mode="bootstrap", expected_revision=revision,
            )

    def test_additive_expansion_and_exact_replay_idempotency(self):
        bootstrap_staged, _ = self._promote()
        bootstrap_counts = self._counts()
        before_expansion_revision = self.engine.get_campaign("c")["revision"]
        staged = self.engine.stage_generated_world(
            "c", "east_batch", "east-seed", namespace="east", mode="expansion",
            expected_revision=before_expansion_revision,
        )
        self.assertNotIn("world_bible", staged["generation"]["payload"])
        bootstrap_location_ids = {
            row["id"]
            for row in bootstrap_staged["generation"]["payload"]["locations"]
        }
        self.assertTrue(any(
            link["from_id"] in bootstrap_location_ids and link["to_id"].startswith("east__")
            for link in staged["generation"]["payload"]["location_links"]
        ))
        self.assertTrue(self.engine.author_validate("c", "east_batch")["valid"])
        self.assertTrue(self.engine.author_dry_run("c", "east_batch", days=1)["passed"])
        self.engine.author_promote("c", "east_batch")
        expanded_counts = self._counts()
        for table in ("item_defs", "locations", "factions", "npcs", "characters", "resource_nodes", "quests"):
            self.assertGreater(expanded_counts[table], bootstrap_counts[table], table)
        revision_after = self.engine.get_campaign("c")["revision"]
        replay = self.engine.stage_generated_world(
            "c", "east_batch", "east-seed", namespace="east", mode="expansion",
            expected_revision=before_expansion_revision,
        )
        self.assertTrue(replay["batch"]["replayed"])
        self.assertEqual("promoted", replay["batch"]["status"])
        self.assertEqual(revision_after, self.engine.get_campaign("c")["revision"])
        self.assertEqual(expanded_counts, self._counts())
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.engine.stage_generated_world(
                "c", "east_batch", "different", namespace="east", mode="expansion",
            )

    def test_stale_revision_and_closed_config_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "unknown procedural config"):
            self.engine.generate_world("seed", {"surprise": 1})
        stale = self.engine.get_campaign("c")["revision"]
        self.engine.upsert_location("c", "changed", "Changed")
        with self.assertRaisesRegex(ValueError, "stale campaign revision"):
            self.engine.stage_generated_world(
                "c", "stale", "seed", namespace="west", mode="expansion",
                expected_revision=stale,
            )
        current = self.engine.get_campaign("c")["revision"]
        self.engine.stage_generated_world(
            "c", "staged_then_stale", "seed", namespace="west", mode="expansion",
            expected_revision=current,
        )
        self.engine.upsert_location("c", "changed_again", "Changed Again")
        validation = self.engine.author_validate("c", "staged_then_stale")
        self.assertFalse(validation["valid"])
        self.assertTrue(any(error["path"] == "_generation.base_revision" for error in validation["errors"]))

    def test_staged_wegen_1_0_payload_remains_validatable_after_upgrade(self):
        legacy_generator = ProceduralWorldGenerator()
        legacy_generator.contract_version = "WEGEN-1.0"
        payload = legacy_generator.generate("legacy-seed", namespace="legacy")["payload"]
        payload.pop("climates", None)
        generation = payload["_generation"]
        generation["base_revision"] = self.engine.get_campaign("c")["revision"]
        core_payload = {key: value for key, value in payload.items() if key != "_generation"}
        generation["content_digest"] = _digest({
            "contract_version": generation["contract_version"],
            "seed": generation["seed"],
            "namespace": generation["namespace"],
            "mode": generation["mode"],
            "anchor_location_id": generation["anchor_location_id"],
            "config": generation["config"],
            "payload": core_payload,
        })
        validation = AuthoringKernel(self.engine).validate_payload("c", payload)
        self.assertTrue(validation["valid"], validation)

    def test_generated_climate_validation_rejects_nonfinite_weights(self):
        payload = copy.deepcopy(self.engine.generate_world("bad-climate", namespace="badclimate")["payload"])
        generation = payload["_generation"]
        generation["base_revision"] = self.engine.get_campaign("c")["revision"]
        payload["climates"][0]["weather_weights"] = {"rain": float("nan")}
        core_payload = {key: value for key, value in payload.items() if key != "_generation"}
        generation["content_digest"] = _digest({
            "contract_version": generation["contract_version"],
            "seed": generation["seed"],
            "namespace": generation["namespace"],
            "mode": generation["mode"],
            "anchor_location_id": generation["anchor_location_id"],
            "config": generation["config"],
            "payload": core_payload,
        })
        validation = AuthoringKernel(self.engine).validate_payload("c", payload)
        self.assertFalse(validation["valid"])
        self.assertTrue(any("weather_weights.rain" in error["path"] for error in validation["errors"]))


if __name__ == "__main__":
    unittest.main()
