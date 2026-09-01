from __future__ import annotations

import copy
import json
import math
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from world_engine.agency import AgencyKernel
from world_engine.authoring import AuthoringKernel
from world_engine.engine import WorldEngine
from world_engine.incidents import IncidentKernel
from world_engine.politics import PoliticsKernel
from world_engine.procedural import (
    GENERATION_CONTRACT_VERSION,
    SUPPORTED_GENERATION_CONTRACTS,
    ProceduralWorldGenerator,
    _digest,
)
from world_engine.quests import QuestRuntimeKernel


class ProceduralRuntimeV500Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "runtime.sqlite3"
        self.engine = WorldEngine(self.path)
        self.engine.ensure_campaign("c", "Runtime generation")
        self.engine.set_simulation_seed("c", 470)
        self.config = {
            "location_count": 3,
            "faction_count": 2,
            "npcs_per_faction": 1,
            "resource_count": 1,
            "quest_count": 1,
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def generation(self, *, namespace: str = "runtime") -> dict:
        return self.engine.generate_world("runtime-seed", self.config, namespace=namespace)

    def promote(self, *, batch: str = "runtime", namespace: str = "runtime") -> tuple[dict, dict]:
        revision = self.engine.get_campaign("c")["revision"]
        staged = self.engine.stage_generated_world(
            "c",
            batch,
            "runtime-seed",
            self.config,
            namespace=namespace,
            expected_revision=revision,
        )
        validation = self.engine.author_validate("c", batch)
        self.assertTrue(validation["valid"], validation)
        dry_run = self.engine.author_dry_run("c", batch, days=1)
        self.assertTrue(dry_run["passed"], dry_run)
        self.assertTrue(any(check["name"] == "runtime_records_installed" for check in dry_run["checks"]))
        return staged, self.engine.author_promote("c", batch)

    def test_wegen_2_is_deterministic_bounded_and_keeps_legacy_contracts(self) -> None:
        first = self.generation()
        second = self.generation()
        self.assertEqual("WEGEN-2.0", GENERATION_CONTRACT_VERSION)
        self.assertEqual(first, second)
        self.assertTrue({"WEGEN-1.0", "WEGEN-1.1", "WEGEN-1.2", "WEGEN-2.0"} <= SUPPORTED_GENERATION_CONTRACTS)
        runtime = first["payload"]["_generation"]["runtime"]
        self.assertEqual(
            {
                "quest_templates", "agency_affordances", "agency_goals",
                "agency_personality_values", "politics_controls", "politics_claims",
                "politics_grievances", "incident_definitions",
            },
            set(runtime),
        )
        self.assertLessEqual(len(runtime["agency_affordances"]), 40)
        self.assertLessEqual(len(runtime["politics_claims"]), 40)
        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "AUTHORING_PAYLOAD_SCHEMA.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(set(first["payload"]) <= set(schema["properties"]))

        legacy = ProceduralWorldGenerator()
        legacy.contract_version = "WEGEN-1.2"
        legacy_payload = legacy.generate("legacy", self.config, namespace="legacy")["payload"]
        self.assertNotIn("runtime", legacy_payload["_generation"])
        legacy_payload["_generation"]["base_revision"] = self.engine.get_campaign("c")["revision"]
        core = {key: value for key, value in legacy_payload.items() if key != "_generation"}
        metadata = legacy_payload["_generation"]
        metadata["content_digest"] = _digest(
            {
                "contract_version": "WEGEN-1.2",
                "seed": metadata["seed"],
                "namespace": metadata["namespace"],
                "mode": metadata["mode"],
                "anchor_location_id": metadata["anchor_location_id"],
                "config": metadata["config"],
                "payload": core,
            }
        )
        self.assertTrue(AuthoringKernel(self.engine).validate_payload("c", legacy_payload)["valid"])

    def test_atomic_promotion_installs_executable_runtime_content_with_one_revision(self) -> None:
        before = self.engine.get_campaign("c")["revision"]
        staged, _ = self.promote()
        self.assertEqual(before + 1, self.engine.get_campaign("c")["revision"])
        runtime = staged["generation"]["payload"]["_generation"]["runtime"]
        with self.engine._db() as db:
            counts = {
                table: int(db.execute(f"SELECT COUNT(*) n FROM {table} WHERE campaign_id='c'").fetchone()["n"])
                for table in (
                    "quest_runtime_instances", "quest_nodes", "agency_affordances",
                    "agency_goals", "agency_personality_values",
                    "politics_territorial_control", "politics_claims",
                    "politics_grievances", "incident_definitions",
                )
            }
            politics = PoliticsKernel(self.engine).public_snapshot_db(db, "c")
        self.assertTrue(all(value > 0 for value in counts.values()), counts)
        self.assertEqual(
            len(runtime["politics_controls"]),
            len(politics["territorial_control"]),
        )

        goal_id = runtime["agency_goals"][0]["id"]
        plan = AgencyKernel(self.engine).create_plan("c", goal_id)
        outcome = AgencyKernel(self.engine).execute_next_step("c", plan["id"])
        self.assertIn(outcome["status"], {"completed", "advanced"})

        quest_template = runtime["quest_templates"][0]
        quest_id = quest_template["quest"]["id"]
        contact_id = quest_template["bindings"]["contact"]["default"].split(":", 1)[1]
        self.engine.commit_event(
            "c", "npc_interaction", "The generated contact was reached.", target_id=contact_id
        )
        QuestRuntimeKernel(self.engine).step("c")
        with self.engine._db() as db:
            node = db.execute(
                "SELECT status FROM quest_nodes WHERE campaign_id='c' AND quest_id=? AND id='contact'",
                (quest_id,),
            ).fetchone()
        self.assertEqual("completed", node["status"])

        incident = IncidentKernel(self.engine)
        with self.engine._write_db() as db:
            campaign = db.execute("SELECT revision,world_time FROM campaigns WHERE id='c'").fetchone()
            when = datetime.fromisoformat(str(campaign["world_time"]))
            incident.extract_pressures_db(db, "c", int(campaign["revision"]), when)
            candidates = incident.candidates_db(db, "c", when)
        self.assertTrue(candidates)
        self.assertTrue(all(item["definition"]["operator_id"] for item in candidates))

    def test_nonfinite_runtime_value_is_rejected_without_mutation(self) -> None:
        generation = self.generation()
        payload = copy.deepcopy(generation["payload"])
        metadata = payload["_generation"]
        metadata["base_revision"] = self.engine.get_campaign("c")["revision"]
        metadata["runtime"]["agency_personality_values"][0]["weight"] = math.nan
        core = {key: value for key, value in payload.items() if key != "_generation"}
        metadata["content_digest"] = _digest(
            {
                "contract_version": metadata["contract_version"],
                "seed": metadata["seed"],
                "namespace": metadata["namespace"],
                "mode": metadata["mode"],
                "anchor_location_id": metadata["anchor_location_id"],
                "config": metadata["config"],
                "payload": core,
                "runtime": metadata["runtime"],
            }
        )
        result = AuthoringKernel(self.engine).validate_payload("c", payload)
        self.assertFalse(result["valid"])
        self.assertTrue(any("must be finite" in item["message"] for item in result["errors"]))
        with self.engine._db() as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) n FROM locations WHERE campaign_id='c'").fetchone()["n"])

    def test_malformed_runtime_shapes_fail_closed_instead_of_raising(self) -> None:
        payload = copy.deepcopy(self.generation()["payload"])
        payload["_generation"]["base_revision"] = self.engine.get_campaign("c")["revision"]
        payload["_generation"]["runtime"] = ["not-an-object"]
        result = AuthoringKernel(self.engine).validate_payload("c", payload)
        self.assertFalse(result["valid"])
        self.assertTrue(
            any(item["path"] == "_generation.runtime" for item in result["errors"]),
            result,
        )

        payload = copy.deepcopy(self.generation()["payload"])
        metadata = payload["_generation"]
        metadata["base_revision"] = self.engine.get_campaign("c")["revision"]
        metadata["runtime"]["politics_controls"] = ["not-an-object"]
        core = {key: value for key, value in payload.items() if key != "_generation"}
        metadata["content_digest"] = _digest(
            {
                "contract_version": metadata["contract_version"],
                "seed": metadata["seed"],
                "namespace": metadata["namespace"],
                "mode": metadata["mode"],
                "anchor_location_id": metadata["anchor_location_id"],
                "config": metadata["config"],
                "payload": core,
                "runtime": metadata["runtime"],
            }
        )
        result = AuthoringKernel(self.engine).validate_payload("c", payload)
        self.assertFalse(result["valid"])
        self.assertTrue(
            any(item["path"] == "politics_controls[0]" for item in result["errors"]),
            result,
        )

    def test_late_runtime_lock_rolls_back_base_and_runtime_rows(self) -> None:
        revision = self.engine.get_campaign("c")["revision"]
        staged = self.engine.stage_generated_world(
            "c", "locked", "runtime-seed", self.config,
            namespace="locked", expected_revision=revision,
        )
        self.assertTrue(self.engine.author_validate("c", "locked")["valid"])
        self.assertTrue(self.engine.author_dry_run("c", "locked", days=1)["passed"])
        incident_id = staged["generation"]["payload"]["_generation"]["runtime"]["incident_definitions"][0]["id"]
        AuthoringKernel(self.engine).lock(
            "c", "incident_definition", incident_id, "late runtime collision"
        )
        with self.assertRaisesRegex(ValueError, "canon-locked incident_definition"):
            self.engine.author_promote("c", "locked")
        with self.engine._db() as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) n FROM locations WHERE campaign_id='c'").fetchone()["n"])
            self.assertEqual(0, db.execute("SELECT COUNT(*) n FROM mechanism_operators WHERE campaign_id='c'").fetchone()["n"])
            self.assertEqual(0, db.execute("SELECT COUNT(*) n FROM incident_definitions WHERE campaign_id='c'").fetchone()["n"])


if __name__ == "__main__":
    unittest.main()
