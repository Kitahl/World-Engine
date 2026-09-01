from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from world_engine.agency import (
    AGENCY_SCHEMA_STAGE,
    AgencyKernel,
    prepare_agency_schema_db,
)
from world_engine.engine import WorldEngine
from world_engine.mechanisms import MechanismKernel
from world_engine.turn_router import TurnRouter


class AgencyKernelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "world.db"
        self.engine = WorldEngine(self.db_path)
        self.engine.ensure_campaign("c", "Agency test")
        self.engine.upsert_location("c", "square", "Town Square", region="town")
        self.engine.upsert_location("c", "vault", "Vault", region="town")
        self.engine.save_location_link("c", "square", "vault", 1.0)
        self.engine.upsert_npc("c", "ada", "Ada", location="square", hp=5, max_hp=5)
        self.engine.upsert_npc("c", "ben", "Ben", location="square", hp=5, max_hp=5)
        TurnRouter(self.engine).sync_existing_entities("c")
        with self.engine._db() as db:
            before = int(db.execute("PRAGMA user_version").fetchone()[0])
            prepare_agency_schema_db(db)
            after = int(db.execute("PRAGMA user_version").fetchone()[0])
        self.assertEqual(before, after)
        self.kernel = AgencyKernel(self.engine)
        self.mechanisms = MechanismKernel(self.engine)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def save_operator(
        self,
        operator_id: str,
        *,
        preconditions: list[dict] | None = None,
        planning_preconditions: dict | None = None,
        planning_effects: dict | None = None,
        base_utility: float = 0,
    ) -> None:
        self.mechanisms.save_operator(
            "c",
            {
                "id": operator_id,
                "bindings": {"actor": {"kinds": ["npc"]}},
                "preconditions": preconditions or [],
                "planning_preconditions": planning_preconditions or {},
                "planning_effects": planning_effects or {},
                "base_utility": base_utility,
            },
        )

    def test_schema_stage_is_additive_and_object_lifecycle_changes_affordance(self) -> None:
        self.assertEqual(23, AGENCY_SCHEMA_STAGE)
        self.save_operator("inspect.vault")
        self.kernel.save_affordance(
            "c",
            "inspect-vault",
            "inspect.vault",
            source_kind="location",
            source_id="vault",
            location_id="vault",
            permission={"max_travel_hours": 2},
        )
        self.assertEqual(["inspect-vault"], [item["id"] for item in self.kernel.discover_affordances("c", "npc", "ada")])
        with self.engine._write_db() as db:
            db.execute("DELETE FROM locations WHERE campaign_id='c' AND id='vault'")
        self.assertEqual([], self.kernel.discover_affordances("c", "npc", "ada"))

    def test_location_ownership_permission_and_mop_prerequisites_fail_closed(self) -> None:
        self.save_operator(
            "open.vault",
            preconditions=[
                {
                    "read": {
                        "source": "world_state",
                        "scope_type": "world",
                        "scope_id": "global",
                        "key": "vault_unlocked",
                    },
                    "op": "eq",
                    "value": True,
                }
            ],
        )
        self.engine.save_ownership("c", "location", "vault", "npc", "ben")
        self.kernel.save_affordance(
            "c",
            "open-vault",
            "open.vault",
            source_kind="location",
            source_id="vault",
            location_id="vault",
            permission={"requires_owned_by_actor": True, "max_travel_hours": 2},
        )
        denied = self.kernel.evaluate_affordance("c", "npc", "ada", "open-vault")
        self.assertFalse(denied["eligible"])
        self.assertIn("actor does not own the source", denied["reasons"])
        owner = self.kernel.evaluate_affordance("c", "npc", "ben", "open-vault")
        self.assertFalse(owner["eligible"])
        self.assertIn("mechanism preconditions are not satisfied", owner["reasons"])
        self.assertEqual([], self.kernel.discover_affordances("c", "npc", "ben"))

    def test_unknown_secret_is_excluded_and_confidence_controls_eligibility_and_utility(self) -> None:
        self.save_operator("use.secret", base_utility=1)
        router = TurnRouter(self.engine)
        router.assert_fact(
            "c",
            {"type": "location", "id": "vault"},
            "has_secret_passage",
            True,
            fact_id="secret-passage",
        )
        self.kernel.save_affordance(
            "c",
            "use-secret",
            "use.secret",
            source_kind="location",
            source_id="square",
            belief_requirements=[
                {
                    "fact_id": "secret-passage",
                    "min_confidence": 0.6,
                    "value": True,
                    "utility_weight": 5,
                }
            ],
        )
        unknown = self.kernel.evaluate_affordance("c", "npc", "ada", "use-secret")
        self.assertFalse(unknown["eligible"])
        self.assertIn("missing belief: secret-passage", unknown["reasons"])
        self.assertNotIn("use-secret", [item["id"] for item in self.kernel.discover_affordances("c", "npc", "ada")])
        router.set_belief(
            "c",
            {"type": "npc", "id": "ada"},
            "secret-passage",
            belief_value=True,
            confidence=0.55,
        )
        low = self.kernel.evaluate_affordance("c", "npc", "ada", "use-secret")
        self.assertFalse(low["eligible"])
        router.set_belief(
            "c",
            {"type": "npc", "id": "ada"},
            "secret-passage",
            belief_value=True,
            confidence=0.8,
        )
        eligible = self.kernel.evaluate_affordance("c", "npc", "ada", "use-secret")
        self.assertTrue(eligible["eligible"])
        self.assertAlmostEqual(5.0, eligible["utility"])
        with self.engine._db() as db:
            db.execute("UPDATE we4_facts SET object_value_json='false' WHERE campaign_id='c' AND fact_id='secret-passage'")
        # The canonical fact changes, but the NPC's structured belief remains its
        # only epistemic input. There is deliberately no world-truth fallback.
        self.assertTrue(self.kernel.evaluate_affordance("c", "npc", "ada", "use-secret")["eligible"])

        self.save_operator(
            "escape.secret",
            planning_preconditions={"secret-passage": True},
            planning_effects={"escaped": True},
        )
        self.kernel.save_affordance(
            "c", "escape-secret", "escape.secret", source_kind="location", source_id="square"
        )
        self.kernel.save_goal(
            "c",
            "escape-goal",
            "npc",
            "ben",
            {"escaped": True},
            initial_state={"secret-passage": True},
        )
        with self.assertRaisesRegex(ValueError, "no eligible bounded plan"):
            self.kernel.create_plan("c", "escape-goal")

    def test_personality_values_modify_utility_without_a_closed_enum(self) -> None:
        self.save_operator("explore")
        self.kernel.save_affordance(
            "c",
            "explore-vault",
            "explore",
            source_kind="location",
            source_id="vault",
            location_id="vault",
            permission={"max_travel_hours": 2},
            value_modifiers={"curiosity": 2.5},
        )
        baseline = self.kernel.evaluate_affordance("c", "npc", "ada", "explore-vault")["utility"]
        self.kernel.set_personality_value("c", "npc", "ada", "curiosity", 2)
        modified = self.kernel.evaluate_affordance("c", "npc", "ada", "explore-vault")["utility"]
        self.assertAlmostEqual(5.0, modified - baseline)

    def test_bounded_planner_persists_and_replans_from_dynamic_affordances(self) -> None:
        self.save_operator("find.key", planning_effects={"has_key": True})
        self.save_operator(
            "open.door",
            planning_preconditions={"has_key": True},
            planning_effects={"door_open": True},
        )
        self.save_operator("force.door", planning_effects={"door_open": True})
        self.kernel.save_affordance("c", "find-key", "find.key", source_kind="location", source_id="square")
        self.kernel.save_affordance("c", "open-door", "open.door", source_kind="location", source_id="square")
        self.kernel.save_goal("c", "open-goal", "npc", "ada", {"door_open": True})
        first = self.kernel.create_plan("c", "open-goal")
        self.assertEqual(["find-key", "open-door"], [step["affordance_id"] for step in first["steps"]])
        self.kernel.remove_affordance("c", "find-key")
        self.kernel.save_affordance("c", "force-door", "force.door", source_kind="location", source_id="square")
        second = self.kernel.replan("c", first["id"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(["force-door"], [step["affordance_id"] for step in second["steps"]])
        with self.engine._db() as db:
            old = db.execute("SELECT status FROM agency_plans WHERE campaign_id='c' AND id=?", (first["id"],)).fetchone()
        self.assertEqual("replanned", old["status"])

    def test_plan_execution_uses_transaction_aware_operator_callback(self) -> None:
        calls: list[dict] = []

        def execute(**kwargs):
            self.assertIsInstance(kwargs["db"], sqlite3.Connection)
            calls.append(kwargs)
            kwargs["db"].execute(
                """INSERT INTO world_state(campaign_id,scope_type,scope_id,state_key,value_json,updated_at)
                   VALUES(?,'world','global','agency_executed','true',?)
                   ON CONFLICT(campaign_id,scope_type,scope_id,state_key) DO UPDATE SET value_json='true'""",
                (kwargs["campaign_id"], self.engine._now()),
            )
            return {"executed": True, "execution_id": "mop-execution"}

        kernel = AgencyKernel(self.engine, operator_executor_db=execute)
        self.save_operator("finish.goal", planning_effects={"done": True})
        kernel.save_affordance("c", "finish", "finish.goal", source_kind="location", source_id="square")
        kernel.save_goal("c", "finish-goal", "npc", "ada", {"done": True})
        plan = kernel.create_plan("c", "finish-goal")
        outcome = kernel.execute_next_step("c", plan["id"])
        self.assertEqual("completed", outcome["status"])
        self.assertEqual("finish.goal", calls[0]["operator_id"])
        self.assertEqual({"kind": "npc", "id": "ada"}, calls[0]["bindings"]["actor"])
        with self.engine._db() as db:
            value = db.execute("SELECT value_json FROM world_state WHERE campaign_id='c' AND state_key='agency_executed'").fetchone()
        self.assertEqual("true", value["value_json"])

    def test_daily_step_uses_canonical_boundary_and_native_mop_executor(self) -> None:
        self.save_operator("daily.goal", planning_effects={"daily_done": True})
        self.kernel.save_affordance(
            "c", "daily-finish", "daily.goal", source_kind="location", source_id="square"
        )
        self.kernel.save_goal("c", "daily-goal", "npc", "ada", {"daily_done": True})
        plan = self.kernel.create_plan("c", "daily-goal")
        emitted: list[tuple[tuple, dict]] = []

        def capture(*args, **event_options):
            emitted.append((args, event_options))

        with self.engine._write_db() as db:
            revision = self.engine._next_revision(db, "c")
            world_time = db.execute("SELECT world_time FROM campaigns WHERE id='c'").fetchone()[
                "world_time"
            ]
            tally = self.kernel.step_db(
                db,
                "c",
                revision,
                datetime.fromisoformat(world_time) + timedelta(days=1),
                capture,
            )
            status = db.execute(
                "SELECT status FROM agency_plans WHERE campaign_id='c' AND id=?", (plan["id"],)
            ).fetchone()["status"]
            receipt = db.execute(
                """SELECT idempotency_key FROM mechanism_execution_receipts
                   WHERE campaign_id='c' AND operator_id='daily.goal'"""
            ).fetchone()
        self.assertEqual(1, tally["plan_steps"])
        self.assertEqual("completed", status)
        self.assertTrue(receipt["idempotency_key"].startswith("agency:plan:"))
        self.assertTrue(any(args[0] == "agency_plan_step" for args, _ in emitted))
        for args, event_options in emitted:
            if args[0].startswith("agency_"):
                self.assertIs(args[2].get("perceivable"), False)
                self.assertEqual("PRIVATE", event_options["sensitivity"])
                self.assertEqual("ENTITY", event_options["scope_type"])
                self.assertEqual("npc", event_options["principal_kind"])
                self.assertEqual("ada", event_options["principal_id"])

    def test_daily_step_replay_does_not_advance_a_second_plan_step(self) -> None:
        self.save_operator("daily.find", planning_effects={"daily_key": True})
        self.save_operator(
            "daily.open",
            planning_preconditions={"daily_key": True},
            planning_effects={"daily_open": True},
        )
        self.kernel.save_affordance(
            "c", "daily-find", "daily.find", source_kind="location", source_id="square"
        )
        self.kernel.save_affordance(
            "c", "daily-open", "daily.open", source_kind="location", source_id="square"
        )
        self.kernel.save_goal("c", "daily-two", "npc", "ada", {"daily_open": True})
        plan = self.kernel.create_plan("c", "daily-two")
        emitted: list[tuple] = []

        def capture(*args, **_event_options):
            emitted.append(args)

        with self.engine._write_db() as db:
            revision = self.engine._next_revision(db, "c")
            world_time = db.execute(
                "SELECT world_time FROM campaigns WHERE id='c'"
            ).fetchone()["world_time"]
            boundary = datetime.fromisoformat(world_time) + timedelta(days=1)
            first = self.kernel.step_db(db, "c", revision, boundary, capture)
            replay = self.kernel.step_db(db, "c", revision, boundary, capture)
            state = db.execute(
                "SELECT current_step,status FROM agency_plans "
                "WHERE campaign_id='c' AND id=?",
                (plan["id"],),
            ).fetchone()
        self.assertEqual(1, first["plan_steps"])
        self.assertEqual(0, replay["plan_steps"])
        self.assertEqual(1, state["current_step"])
        self.assertEqual("active", state["status"])
        self.assertEqual(
            1, sum(1 for args in emitted if args[0] == "agency_plan_step")
        )

    def test_step_events_are_entity_private_and_absent_from_public_world_context(self) -> None:
        self.save_operator("private.daily", planning_effects={"private_done": True})
        sentinel = "private-agency-sentinel"
        self.kernel.save_affordance(
            "c", sentinel, "private.daily", source_kind="location", source_id="square"
        )
        self.kernel.save_goal("c", "private-agency-goal", "npc", "ada", {"private_done": True})
        self.kernel.create_plan("c", "private-agency-goal")

        with self.engine._write_db() as db:
            revision = self.engine._next_revision(db, "c")
            world_time = db.execute(
                "SELECT world_time FROM campaigns WHERE id='c'"
            ).fetchone()["world_time"]

            def persist(event_type, summary, payload, region, when, **event_options):
                return self.engine._insert_event(
                    db,
                    "c",
                    revision,
                    event_type,
                    summary,
                    region=region,
                    payload=payload,
                    world_time_override=when.isoformat(),
                    **event_options,
                )

            self.kernel.step_db(
                db,
                "c",
                revision,
                datetime.fromisoformat(world_time) + timedelta(days=1),
                persist,
            )

        with self.engine._db() as db:
            rows = db.execute(
                """SELECT event_type,sensitivity,scope_type,principal_kind,principal_id,payload_json
                   FROM events WHERE campaign_id='c'
                     AND event_type IN ('agency_appraisal','agency_plan_replanned','agency_plan_step')
                   ORDER BY id"""
            ).fetchall()
            public_db_snapshot = self.kernel.public_snapshot_db(db, "c", "npc", "ada")
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual("PRIVATE", row["sensitivity"])
            self.assertEqual("ENTITY", row["scope_type"])
            self.assertEqual("npc", row["principal_kind"])
            self.assertEqual("ada", row["principal_id"])
            self.assertIs(json.loads(row["payload_json"])["perceivable"], False)

        public_context = self.engine.get_world_context("c", location="square")
        public_event_types = {item["event_type"] for item in public_context["recent_events"]}
        self.assertTrue(
            public_event_types.isdisjoint(
                {"agency_appraisal", "agency_plan_replanned", "agency_plan_step"}
            )
        )
        public_payloads = [item["payload"] for item in public_context["recent_events"]]
        for row in rows:
            self.assertNotIn(json.loads(row["payload_json"]), public_payloads)
        self.assertEqual(self.kernel.public_snapshot("c", "npc", "ada"), public_db_snapshot)

    def test_appraisal_uses_goals_and_relationships_and_memory_references_event(self) -> None:
        self.kernel.save_goal(
            "c",
            "festival-goal",
            "npc",
            "ada",
            {"success_event_types": ["festival_success"]},
        )
        with self.engine._write_db() as db:
            db.execute(
                """INSERT INTO relationships(campaign_id,source_id,target_id,trust,fear,respect,affection,notes_json,updated_at)
                   VALUES('c','ada','ben',80,0,0,80,'{}',?)""",
                (self.engine._now(),),
            )
        event = self.engine.commit_event(
            "c",
            "festival_success",
            "The festival succeeded.",
            region="square",
            target_id="ben",
            payload={"visibility": "public"},
        )
        ada = self.kernel.appraise_event("c", "npc", "ada", event["id"])
        ben = self.kernel.appraise_event("c", "npc", "ben", event["id"])
        self.assertTrue(ada["perceived"])
        self.assertGreater(ada["emotion"]["intensity"], ben["emotion"]["intensity"])
        memories = self.kernel.recall_memories("c", "npc", "ada")
        self.assertEqual(event["id"], memories[0]["event_id"])
        with self.engine._db() as db:
            row = db.execute("SELECT * FROM agency_memories WHERE campaign_id='c' AND actor_id='ada'").fetchone()
        self.assertEqual(event["id"], row["event_id"])
        self.assertNotIn("the festival succeeded", row["appraisal_json"].lower())
        replay = self.kernel.appraise_event("c", "npc", "ada", event["id"])
        self.assertTrue(replay["already_appraised"])
        with self.engine._db() as db:
            self.assertEqual(
                1,
                db.execute(
                    "SELECT COUNT(*) n FROM agency_memories WHERE campaign_id='c' AND actor_id='ada' AND event_id=?",
                    (event["id"],),
                ).fetchone()["n"],
            )

    def test_hidden_event_is_not_perceived_or_memorized(self) -> None:
        event = self.engine.commit_event(
            "c",
            "secret_loss",
            "A secret loss occurred.",
            region="square",
            payload={"visibility": "private", "visible_to": ["npc:ben"]},
        )
        result = self.kernel.appraise_event("c", "npc", "ada", event["id"])
        self.assertFalse(result["perceived"])
        with self.engine._db() as db:
            emotion_count = db.execute("SELECT COUNT(*) n FROM agency_emotions WHERE campaign_id='c' AND actor_id='ada'").fetchone()["n"]
            memory_count = db.execute("SELECT COUNT(*) n FROM agency_memories WHERE campaign_id='c' AND actor_id='ada'").fetchone()["n"]
        self.assertEqual(0, emotion_count)
        self.assertEqual(0, memory_count)

    def test_canonical_event_scope_precedes_payload_and_actor_fields(self) -> None:
        secret = self.engine.commit_event(
            "c",
            "secret_order",
            "Ada is named but the event is GM-only.",
            region="square",
            actor_id="ada",
            payload={"visibility": "public", "visible_to": ["npc:ada"]},
            sensitivity="SECRET",
            scope_type="GM",
        )
        private_ben = self.engine.commit_event(
            "c",
            "private_order",
            "This is scoped only to Ben.",
            region="square",
            target_id="ada",
            payload={"visibility": "public", "visible_to": ["npc:ada"]},
            sensitivity="PRIVATE",
            scope_type="ENTITY",
            principal_kind="npc",
            principal_id="ben",
        )
        self.assertFalse(
            self.kernel.appraise_event("c", "npc", "ada", secret["id"])["perceived"]
        )
        self.assertFalse(
            self.kernel.appraise_event("c", "npc", "ada", private_ben["id"])["perceived"]
        )
        self.assertTrue(
            self.kernel.appraise_event("c", "npc", "ben", private_ben["id"])["perceived"]
        )
        with self.engine._db() as db:
            ada_memories = db.execute(
                "SELECT COUNT(*) n FROM agency_memories "
                "WHERE campaign_id='c' AND actor_id='ada'"
            ).fetchone()["n"]
        self.assertEqual(0, ada_memories)

    def test_linear_emotion_decay_is_chunk_invariant(self) -> None:
        event = self.engine.commit_event(
            "c",
            "public_loss",
            "A public loss occurred.",
            region="square",
            payload={"visibility": "public", "valence": -0.5},
        )
        self.kernel.appraise_event("c", "npc", "ada", event["id"])
        self.kernel.appraise_event("c", "npc", "ben", event["id"])
        start = datetime.fromisoformat(event["world_time"])
        with self.engine._write_db() as db:
            self.kernel._decay_emotions_db(db, "c", "npc", "ada", start + timedelta(days=2))
            self.kernel._decay_emotions_db(db, "c", "npc", "ben", start + timedelta(days=1))
            self.kernel._decay_emotions_db(db, "c", "npc", "ben", start + timedelta(days=2))
            ada = db.execute("SELECT intensity FROM agency_emotions WHERE campaign_id='c' AND actor_id='ada'").fetchone()["intensity"]
            ben = db.execute("SELECT intensity FROM agency_emotions WHERE campaign_id='c' AND actor_id='ben'").fetchone()["intensity"]
        self.assertAlmostEqual(ada, ben, places=12)

    def test_public_projection_is_closed_and_strict_json_safe(self) -> None:
        self.save_operator("public.act")
        self.save_operator("private.act")
        self.kernel.save_affordance("c", "public-act", "public.act", source_kind="location", source_id="square")
        self.kernel.save_affordance(
            "c",
            "private-act",
            "private.act",
            source_kind="npc",
            source_id="ada",
            visibility="actor",
        )
        self.kernel.save_goal(
            "c",
            "secret-goal",
            "npc",
            "ada",
            {"secret_objective": "NEVER_SHIP_THIS"},
        )
        public = self.kernel.public_snapshot("c", "npc", "ada")
        self.assertEqual(
            {"contract_version", "actor", "available_affordances"},
            set(public),
        )
        self.assertEqual(["public-act"], [item["id"] for item in public["available_affordances"]])
        encoded = json.dumps(public, allow_nan=False, sort_keys=True)
        self.assertNotIn("NEVER_SHIP_THIS", encoded)
        for private_key in ("goals", "plans", "beliefs", "emotions", "memories", "personality_values"):
            self.assertNotIn(private_key, encoded)

    def test_legacy_strings_are_suggestions_not_authority(self) -> None:
        self.engine.upsert_npc("c", "ada", "Ada", location="square", hp=5, max_hp=5, goals=["find the key"])
        suggestions = self.kernel.legacy_goal_candidates("c", "ada")
        self.assertEqual("find the key", suggestions[0]["legacy_value"])
        self.assertFalse(suggestions[0]["authoritative"])
        with self.engine._db() as db:
            count = db.execute("SELECT COUNT(*) n FROM agency_goals WHERE campaign_id='c' AND actor_id='ada'").fetchone()["n"]
        self.assertEqual(0, count)

    def test_external_provider_output_is_validated_and_nonfinite_fails_closed(self) -> None:
        self.save_operator("provider.op")
        self.kernel.register_affordance_provider(
            "bad_provider",
            lambda *_args: [
                {
                    "id": "bad-provider-affordance",
                    "operator_id": "provider.op",
                    "source_kind": "location",
                    "source_id": "square",
                    "base_utility": float("nan"),
                }
            ],
        )
        with self.assertRaisesRegex(ValueError, "finite"):
            self.kernel.discover_affordances("c", "npc", "ada")

    def test_deterministic_replay_on_identical_database_copies(self) -> None:
        self.save_operator("find.key", planning_effects={"has_key": True})
        self.save_operator(
            "open.door",
            planning_preconditions={"has_key": True},
            planning_effects={"door_open": True},
        )
        self.kernel.save_affordance("c", "find-key", "find.key", source_kind="location", source_id="square")
        self.kernel.save_affordance("c", "open-door", "open.door", source_kind="location", source_id="square")
        self.kernel.save_goal("c", "goal", "npc", "ada", {"door_open": True})
        clone_path = Path(self.temp.name) / "clone.db"
        clone = sqlite3.connect(clone_path)
        try:
            with self.engine._db() as source:
                source.backup(clone)
        finally:
            clone.close()
        first = self.kernel.create_plan("c", "goal")
        clone_engine = WorldEngine(clone_path)
        clone_kernel = AgencyKernel(clone_engine)
        second = clone_kernel.create_plan("c", "goal")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["plan_digest"], second["plan_digest"])
        self.assertEqual(
            [(step["affordance_id"], step["operator_id"], step["bindings"]) for step in first["steps"]],
            [(step["affordance_id"], step["operator_id"], step["bindings"]) for step in second["steps"]],
        )


if __name__ == "__main__":
    unittest.main()
