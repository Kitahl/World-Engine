from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from world_engine import WorldEngine
from world_engine.quests import QUEST_SCHEMA, QuestRuntimeKernel


class QuestRuntimeV500Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.e = WorldEngine(Path(self.tmp.name) / "world.sqlite3")
        self.e.ensure_campaign("c", "Quest Runtime", "1492-01-01T08:00:00+00:00")
        self.e.upsert_location("c", "village", "Village")
        self.e.upsert_character("c", "hero", "Hero", location="village")
        self.e.upsert_npc("c", "target", "Target", location="village")
        with self.e._write_db() as db:
            db.executescript(QUEST_SCHEMA)
        self.k = QuestRuntimeKernel(self.e)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _event_condition(event_type: str, **fields: object) -> dict:
        return {"event": {"event_type": event_type, **fields}}

    def _template(
        self,
        template_id: str,
        nodes: list[dict],
        edges: list[dict] | None = None,
        *,
        visibility: str = "private",
    ) -> dict:
        return {
            "template_id": template_id,
            "visibility": visibility,
            "bindings": {
                "owner": {"kind": "character"},
                "target": {"kind": "npc"},
                "place": {"kind": "location"},
            },
            "quest": {
                "id": template_id,
                "title": f"Quest {template_id}",
                "owner_id": "$owner.id",
                "region": "$place.id",
                "objectives": ["Do the thing"],
            },
            "nodes": nodes,
            "edges": edges or [],
        }

    def _bindings(self) -> dict:
        return {
            "owner": "character:hero",
            "target": "npc:target",
            "place": "location:village",
        }

    def _promote(self, template: dict) -> dict:
        return self.k.bind_template(
            "c", template, self._bindings(), dry_run=False
        )

    def _event(
        self,
        event_type: str,
        *,
        target_id: str | None = None,
        actor_id: str | None = None,
        world_time: str | None = None,
        payload: dict | None = None,
    ) -> int:
        with self.e._write_db() as db:
            revision = self.e._next_revision(db, "c")
            return self.e._insert_event(
                db,
                "c",
                revision,
                event_type,
                event_type,
                region="village",
                actor_id=actor_id,
                target_id=target_id,
                payload=payload or {},
                world_time_override=world_time,
            )

    def _status(self, quest_id: str, node_id: str) -> tuple[str, str]:
        with self.e._db() as db:
            quest = db.execute(
                "SELECT status FROM quests WHERE campaign_id='c' AND id=?", (quest_id,)
            ).fetchone()
            node = db.execute(
                """SELECT status FROM quest_nodes
                   WHERE campaign_id='c' AND quest_id=? AND id=?""",
                (quest_id, node_id),
            ).fetchone()
        return str(quest["status"]), str(node["status"])

    def test_activation_then_success_completes_terminal_quest(self) -> None:
        template = self._template(
            "activation_success",
            [
                {
                    "id": "objective",
                    "status": "inactive",
                    "trigger": self._event_condition("quest_offer"),
                    "success": self._event_condition(
                        "objective_done", target_id="$target.id"
                    ),
                    "state": {"terminal": True},
                }
            ],
        )
        self._promote(template)
        self._event("quest_offer")
        first = self.k.step("c")
        self.assertGreaterEqual(first["transitions"], 1)
        self.assertEqual(("active", "active"), self._status("activation_success", "objective"))

        self._event("objective_done", target_id="target")
        second = self.k.step("c")
        self.assertEqual(("completed", "completed"), self._status("activation_success", "objective"))
        self.assertEqual(
            ["completed", "quest_completed"],
            [
                item["transition_kind"]
                for item in second["receipts"]
                if item["quest_id"] == "activation_success"
            ],
        )

    def test_failure_and_deadline_paths_are_explicit(self) -> None:
        failure = self._template(
            "failure_path",
            [
                {
                    "id": "objective",
                    "status": "active",
                    "failure": self._event_condition("objective_failed"),
                    "success": self._event_condition("objective_done"),
                }
            ],
        )
        self._promote(failure)
        self._event("objective_failed")
        self.k.step("c")
        self.assertEqual(("failed", "failed"), self._status("failure_path", "objective"))

        deadline = self._template(
            "deadline_path",
            [
                {
                    "id": "objective",
                    "status": "active",
                    "success": self._event_condition("objective_done"),
                    "deadline_world_time": "1492-01-02T08:00:00+00:00",
                }
            ],
        )
        self._promote(deadline)
        result = self.k.step("c", when="1492-01-03T08:00:00+00:00")
        self.assertEqual(("failed", "failed"), self._status("deadline_path", "objective"))
        self.assertIn("deadline", [item["transition_kind"] for item in result["receipts"]])

    def test_simultaneous_policy_failure_wins_and_in_time_success_beats_deadline(self) -> None:
        ambiguous = self._template(
            "ambiguous",
            [
                {
                    "id": "objective",
                    "status": "active",
                    "success": self._event_condition("ambiguous_event"),
                    "failure": self._event_condition("ambiguous_event"),
                    "deadline_world_time": "1492-01-02T08:00:00+00:00",
                }
            ],
        )
        self._promote(ambiguous)
        self._event(
            "ambiguous_event", world_time="1492-01-02T08:00:00+00:00"
        )
        result = self.k.step("c", when="1492-01-02T08:00:00+00:00")
        receipt = next(x for x in result["receipts"] if x["quest_id"] == "ambiguous")
        self.assertEqual("failed", receipt["transition_kind"])
        self.assertEqual("failure_then_in_time_success_then_deadline", receipt["detail"]["policy"])

        exact = self._template(
            "exact_deadline",
            [
                {
                    "id": "objective",
                    "status": "active",
                    "success": self._event_condition("just_in_time"),
                    "deadline_world_time": "1492-01-04T08:00:00+00:00",
                    "state": {"terminal": True},
                }
            ],
        )
        self._promote(exact)
        self._event("just_in_time", world_time="1492-01-04T08:00:00+00:00")
        self.k.step("c", when="1492-01-04T08:00:00+00:00")
        self.assertEqual(("completed", "completed"), self._status("exact_deadline", "objective"))

    def test_branches_use_priority_then_target_and_skip_exclusive_alternatives(self) -> None:
        template = self._template(
            "branches",
            [
                {
                    "id": "root",
                    "status": "active",
                    "success": self._event_condition("choose"),
                    "state": {"branch_mode": "first"},
                },
                {"id": "alpha", "status": "inactive"},
                {"id": "beta", "status": "inactive"},
            ],
            [
                {"from_node": "root", "to_node": "beta", "priority": 20},
                {"from_node": "root", "to_node": "alpha", "priority": 10},
            ],
        )
        self._promote(template)
        self._event("choose")
        self.k.step("c")
        with self.e._db() as db:
            rows = db.execute(
                """SELECT id,status FROM quest_nodes WHERE campaign_id='c'
                   AND quest_id='branches' ORDER BY id"""
            ).fetchall()
        self.assertEqual(
            [("alpha", "active"), ("beta", "skipped"), ("root", "completed")],
            [(row["id"], row["status"]) for row in rows],
        )

    def test_target_death_entity_condition_fails_quest(self) -> None:
        template = self._template(
            "protect_target",
            [
                {
                    "id": "protect",
                    "status": "active",
                    "failure": {
                        "entity": {
                            "binding": "target",
                            "field": "status",
                            "op": "eq",
                            "value": "dead",
                        }
                    },
                    "success": self._event_condition("protected"),
                }
            ],
        )
        self._promote(template)
        self.k.step("c")
        self.e.set_actor_status("c", "npc", "target", "dead", reason="test death")
        self.k.step("c")
        self.assertEqual(("failed", "failed"), self._status("protect_target", "protect"))

    def test_shared_mop_predicate_can_complete_node(self) -> None:
        template = self._template(
            "predicate",
            [
                {
                    "id": "check",
                    "status": "active",
                    "success": {
                        "predicate": {
                            "read": {
                                "source": "world_state",
                                "scope_type": "world",
                                "scope_id": "global",
                                "key": "gate_open",
                            },
                            "op": "eq",
                            "value": True,
                        }
                    },
                    "state": {"terminal": True},
                }
            ],
        )
        self._promote(template)
        self.k.step("c")
        self.e.set_world_state("c", "world", "global", "gate_open", True)
        self.k.step("c")
        self.assertEqual(("completed", "completed"), self._status("predicate", "check"))

    def test_transition_receipts_and_event_cursor_are_idempotent(self) -> None:
        template = self._template(
            "idempotent",
            [
                {
                    "id": "objective",
                    "status": "active",
                    "success": self._event_condition("once"),
                    "state": {"terminal": True},
                }
            ],
        )
        self._promote(template)
        source_id = self._event("once")
        first = self.k.step("c")
        receipt_count = len(self.k.list_receipts("c", quest_id="idempotent"))
        second = self.k.step("c")
        self.assertEqual(2, receipt_count)
        self.assertEqual(receipt_count, len(self.k.list_receipts("c", quest_id="idempotent")))
        self.assertEqual(0, second["transitions"])
        self.assertGreaterEqual(first["last_event_id"], source_id)

    def test_new_quest_does_not_replay_pre_instantiation_events(self) -> None:
        old_event_id = self._event("historical")
        template = self._template(
            "no_history_replay",
            [
                {
                    "id": "objective",
                    "status": "active",
                    "success": self._event_condition("historical"),
                    "state": {"terminal": True},
                }
            ],
        )
        self._promote(template)
        with self.e._db() as db:
            floor = db.execute(
                "SELECT start_event_id FROM quest_runtime_instances "
                "WHERE campaign_id='c' AND quest_id='no_history_replay'"
            ).fetchone()["start_event_id"]
        self.assertGreaterEqual(floor, old_event_id)
        first = self.k.step("c")
        self.assertEqual(0, first["transitions"])
        self.assertEqual(
            ("active", "active"),
            self._status("no_history_replay", "objective"),
        )
        self._event("historical")
        second = self.k.step("c")
        self.assertGreater(second["transitions"], 0)
        self.assertEqual(
            ("completed", "completed"),
            self._status("no_history_replay", "objective"),
        )

    def test_world_time_condition_runs_at_canonical_daily_boundary(self) -> None:
        template = self._template(
            "time_boundary",
            [
                {
                    "id": "wait_until_midnight",
                    "status": "active",
                    "success": {
                        "world_time": {
                            "op": "gte",
                            "value": "1492-01-02T00:00:00+00:00",
                        }
                    },
                    "state": {"terminal": True},
                }
            ],
        )
        self._promote(template)

        result = self.e.advance_world("c", 16 * 60)

        self.assertEqual(
            ("completed", "completed"),
            self._status("time_boundary", "wait_until_midnight"),
        )
        self.assertEqual(2, result["simulation"]["quest_transitions"])

    def test_graph_validation_rejects_missing_references_and_cycles(self) -> None:
        missing = self._template(
            "missing",
            [{"id": "one", "status": "active"}],
            [{"from_node": "one", "to_node": "absent"}],
        )
        with self.assertRaisesRegex(ValueError, "missing node"):
            self.k.bind_template("c", missing, self._bindings(), dry_run=True)

        cycle = self._template(
            "cycle",
            [{"id": "one"}, {"id": "two"}],
            [
                {"from_node": "one", "to_node": "two"},
                {"from_node": "two", "to_node": "one"},
            ],
        )
        with self.assertRaisesRegex(ValueError, "contains a cycle"):
            self.k.bind_template("c", cycle, self._bindings(), dry_run=True)

    def test_template_dry_run_does_not_mutate_and_unknown_binding_rejects(self) -> None:
        template = self._template(
            "dry_run",
            [{"id": "objective", "status": "active"}],
        )
        with self.e._db() as db:
            before = {
                "quests": db.execute("SELECT COUNT(*) n FROM quests").fetchone()["n"],
                "events": db.execute("SELECT COUNT(*) n FROM events").fetchone()["n"],
                "revision": db.execute(
                    "SELECT revision FROM campaigns WHERE id='c'"
                ).fetchone()["revision"],
            }
        result = self.k.bind_template("c", template, self._bindings(), dry_run=True)
        self.assertTrue(result["dry_run"])
        with self.e._db() as db:
            after = {
                "quests": db.execute("SELECT COUNT(*) n FROM quests").fetchone()["n"],
                "events": db.execute("SELECT COUNT(*) n FROM events").fetchone()["n"],
                "revision": db.execute(
                    "SELECT revision FROM campaigns WHERE id='c'"
                ).fetchone()["revision"],
            }
        self.assertEqual(before, after)

        invalid = self._bindings()
        invalid["target"] = "npc:not-real"
        with self.assertRaisesRegex(KeyError, "unknown authoritative binding"):
            self.k.bind_template("c", template, invalid, dry_run=True)

    def test_private_projection_redacts_conditions_and_bindings(self) -> None:
        template = self._template(
            "private_quest",
            [
                {
                    "id": "objective",
                    "status": "active",
                    "success": self._event_condition(
                        "secret_done", target_id="$target.id"
                    ),
                    "state": {"secret": "NEVER_EXPOSE"},
                }
            ],
            visibility="private",
        )
        self._promote(template)
        projection = self.k.public_projection("c", "private_quest")
        self.assertEqual(
            {
                "id": "private_quest",
                "status": "active",
                "visibility": "private",
                "redacted": True,
            },
            projection,
        )
        self.assertNotIn("conditions", str(projection))
        self.assertNotIn("target", str(projection))

    def test_transition_event_retains_causal_source_and_receipt_provenance(self) -> None:
        template = self._template(
            "provenance",
            [
                {
                    "id": "objective",
                    "status": "active",
                    "success": self._event_condition("source_event"),
                    "state": {"terminal": True},
                }
            ],
            visibility="public",
        )
        self._promote(template)
        source_id = self._event("source_event", payload={"proof": "source"})
        self.k.step("c")
        receipts = self.k.list_receipts("c", quest_id="provenance")
        self.assertEqual({source_id}, {item["source_event_id"] for item in receipts})
        with self.e._db() as db:
            events = db.execute(
                """SELECT event_type,causal_parent_event_id,causal_root_event_id
                   FROM events WHERE campaign_id='c' AND event_type IN
                   ('quest_node_completed','quest_completed') ORDER BY id"""
            ).fetchall()
        self.assertEqual(2, len(events))
        self.assertTrue(all(row["causal_parent_event_id"] == source_id for row in events))
        self.assertTrue(all(row["causal_root_event_id"] == source_id for row in events))


if __name__ == "__main__":
    unittest.main()
