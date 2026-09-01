from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest

from world_engine import WorldEngine
from world_engine.mechanisms import (
    MAX_CONSIDERATIONS,
    MAX_OPERATOR_BYTES,
    MAX_QUERY_LIMIT,
    MAX_TAGS,
    MECHANISM_SCHEMA,
    MechanismKernel,
    prepare_mechanism_schema_db,
    verify_mechanism_schema_db,
)
from world_engine.turn_router import TurnRouter


class MechanismContractV470Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "world.sqlite3"
        self.e = WorldEngine(self.db_path)
        self._install(self.e)
        self.e.ensure_campaign("c", "Mechanism tests", "1492-01-01T08:00:00+00:00")
        self.e.upsert_location("c", "club", "Club")
        self.e.upsert_npc(
            "c",
            "mara",
            "Mara",
            location="club",
            beliefs=["PRIVATE_SECRET_NEVER_SHIP"],
            goals=["HIDDEN_GOAL"],
        )
        self.k = MechanismKernel(self.e)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _install(engine: WorldEngine) -> list[dict[str, str]]:
        with engine._write_db() as db:
            preserved = prepare_mechanism_schema_db(db)
            db.executescript(MECHANISM_SCHEMA)
            verify_mechanism_schema_db(db)
        return preserved

    @staticmethod
    def _noop() -> dict:
        return {
            "id": "noop",
            "bindings": {
                "actor": {"kind": "npc", "default": {"kind": "npc", "id": "mara"}},
            },
        }

    def _install_callback(self, callback) -> None:
        self.e._mechanism_apply_effect_db = callback

    def test_schema_install_is_exact_and_does_not_write_user_version(self) -> None:
        with self.e._db() as db:
            before = int(db.execute("PRAGMA user_version").fetchone()[0])
            verify_mechanism_schema_db(db)
            targets = {
                row["table"]
                for row in db.execute('PRAGMA foreign_key_list("mechanism_execution_receipts")').fetchall()
            }
        self._install(self.e)
        with self.e._db() as db:
            after = int(db.execute("PRAGMA user_version").fetchone()[0])
        self.assertEqual(before, after)
        self.assertEqual({"campaigns", "mechanism_operators"}, targets)

    def test_one_sided_incompatible_table_preserves_pair_receipt_first(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("DROP TABLE mechanism_operators")
            db.execute(
                "CREATE TABLE mechanism_operators(campaign_id TEXT NOT NULL,id TEXT NOT NULL,name TEXT NOT NULL,PRIMARY KEY(campaign_id,id))"
            )
        preserved = self._install(self.e)
        self.assertEqual(
            ["mechanism_execution_receipts", "mechanism_operators"],
            [item["table"] for item in preserved],
        )
        with self.e._db() as db:
            targets = {
                row["table"]
                for row in db.execute('PRAGMA foreign_key_list("mechanism_execution_receipts")').fetchall()
            }
            self.assertEqual([], db.execute('PRAGMA foreign_key_check("mechanism_execution_receipts")').fetchall())
        self.assertEqual({"campaigns", "mechanism_operators"}, targets)
        self.k.save_operator("c", self._noop())
        self.assertTrue(self.k.execute_operator("c", "noop")["executed"])

    def test_extra_required_column_is_incompatible_not_additive(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("ALTER TABLE mechanism_operators ADD COLUMN poison TEXT NOT NULL")
        preserved = self._install(self.e)
        self.assertEqual(2, len(preserved))
        self.k.save_operator("c", self._noop())
        self.assertEqual("noop", self.k.get_operator("c", "noop")["id"])

    def test_binding_refs_and_events_never_serialize_private_entity_rows(self) -> None:
        self.k.save_operator("c", self._noop())
        bound = self.k.bind_operator("c", "noop")
        result = self.k.execute_operator("c", "noop", idempotency_key="once")
        receipt = self.k.get_receipt("c", result["execution_id"])
        mechanism_event = next(
            event for event in self.e.recent_events("c", 20) if event["event_type"] == "mechanism_executed"
        )
        serialized = json.dumps(
            {"bound": bound, "result": result, "receipt": receipt, "event": mechanism_event},
            sort_keys=True,
        )
        self.assertNotIn("PRIVATE_SECRET_NEVER_SHIP", serialized)
        self.assertNotIn("HIDDEN_GOAL", serialized)
        self.assertNotIn("beliefs_json", serialized)
        self.assertEqual(
            {"kind": "npc", "id": "mara", "key": "npc:mara", "name": "Mara"},
            result["bindings"]["actor"],
        )

    def test_direct_fact_reads_require_system_authority_and_trace_is_redacted(self) -> None:
        with self.assertRaisesRegex(ValueError, "authority"):
            self.k.validate_operator_document(
                {"id": "bad", "preconditions": [{"read": {"source": "fact", "fact_id": "secret"}, "op": "truthy"}]}
            )
        TurnRouter(self.e).assert_fact(
            "c", {"type": "npc", "id": "mara"}, "secret", "FACT_VALUE_SECRET", fact_id="secret"
        )
        operator = {
            "id": "system.read",
            "preconditions": [
                {
                    "read": {"source": "fact", "fact_id": "secret", "authority": "system"},
                    "op": "eq",
                    "value": "FACT_VALUE_SECRET",
                }
            ],
        }
        self.k.save_operator("c", operator)
        evaluation = self.k.evaluate_operator("c", "system.read")
        self.assertTrue(evaluation["eligible"])
        self.assertNotIn("FACT_VALUE_SECRET", json.dumps(evaluation, sort_keys=True))

    def test_receipt_tamper_fails_get_list_and_replay(self) -> None:
        self.k.save_operator("c", self._noop())
        result = self.k.execute_operator("c", "noop", idempotency_key="once")
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute(
                "UPDATE mechanism_execution_receipts SET result_json=? WHERE campaign_id=? AND execution_id=?",
                ('{"tampered":true}', "c", result["execution_id"]),
            )
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.k.get_receipt("c", result["execution_id"])
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.k.list_receipts("c")
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.k.execute_operator("c", "noop", idempotency_key="once")

    def test_logically_equal_databases_have_equal_execution_and_receipt_digests(self) -> None:
        values = []
        for index in range(2):
            path = Path(self.tmp.name) / f"determinism-{index}.sqlite3"
            engine = WorldEngine(path)
            self._install(engine)
            engine.ensure_campaign("x", "X", "1492-01-01T08:00:00+00:00")
            engine.upsert_location("x", "club", "Club")
            engine.upsert_npc("x", "mara", "Mara", location="club", beliefs=["private"])
            kernel = MechanismKernel(engine)
            kernel.save_operator("x", self._noop())
            result = kernel.execute_operator("x", "noop", idempotency_key="same")
            receipt = kernel.get_receipt("x", result["execution_id"])
            values.append((result["execution_id"], receipt["receipt_digest"]))
        self.assertEqual(values[0], values[1])

    def test_callback_receives_single_revision_and_execution_id(self) -> None:
        calls = []

        def callback(db, campaign_id, effect, bindings, **context):
            calls.append((effect["op"], bindings, dict(context)))
            if context["phase"] == "preflight":
                return {"passed": True, "reason_code": "ok"}
            return {
                "applied": True,
                "result": {"key": effect["key"], "after": effect["value"], "updated_at": "volatile"},
            }

        self._install_callback(callback)
        operator = {
            "id": "callback",
            "bindings": {"actor": {"kind": "npc", "default": "npc:mara"}},
            "effects": [{"op": "world_state.set", "key": "flag", "value": True}],
        }
        self.k.save_operator("c", operator)
        result = self.k.execute_operator("c", "callback")
        apply_call = next(item for item in calls if item[2]["phase"] == "apply")
        self.assertEqual(result["after_revision"], apply_call[2]["revision"])
        self.assertEqual(result["execution_id"], apply_call[2]["execution_id"])
        self.assertEqual("PRIVATE_SECRET_NEVER_SHIP", apply_call[1]["actor"]["beliefs"][0])
        self.assertNotIn("updated_at", result["effect_results"][0])

    def test_integrated_callback_applies_world_state_with_one_revision(self) -> None:
        self.k.save_operator(
            "c",
            {
                "id": "integrated.flag",
                "effects": [
                    {"op": "world_state.set", "key": "gate_open", "value": True},
                    {"op": "world_state.set", "key": "alarm", "value": False},
                ],
            },
        )
        before = self.e.get_campaign("c")["revision"]
        result = self.k.execute_operator("c", "integrated.flag")
        self.assertEqual(before + 1, result["after_revision"])
        state = {
            row["state_key"]: row["value"]
            for row in self.e.get_world_state("c", "world", "global")
        }
        self.assertEqual(True, state["gate_open"])
        self.assertEqual(False, state["alarm"])
        effect_events = [
            event for event in self.e.recent_events("c", 20)
            if event["event_type"] == "world_state_change"
        ]
        self.assertEqual(2, len(effect_events))
        self.assertEqual({result["after_revision"]}, {event["revision"] for event in effect_events})

    def test_integrated_callback_cumulative_costs_fail_without_mutation(self) -> None:
        self.e.save_item_def("c", "coin", "Coin")
        self.e.set_inventory_item("c", "npc", "mara", "coin", 5)
        self.k.save_operator(
            "c",
            {
                "id": "integrated.cost",
                "bindings": {"actor": {"kind": "npc", "default": "npc:mara"}},
                "costs": [
                    {"op": "inventory.adjust", "binding": "actor", "item_id": "coin", "delta": -3},
                    {"op": "inventory.adjust", "binding": "actor", "item_id": "coin", "delta": -3},
                ],
            },
        )
        before = self.e.get_campaign("c")["revision"]
        evaluation = self.k.evaluate_operator("c", "integrated.cost")
        self.assertFalse(evaluation["eligible"])
        with self.assertRaisesRegex(ValueError, "not eligible"):
            self.k.execute_operator("c", "integrated.cost")
        self.assertEqual(before, self.e.get_campaign("c")["revision"])
        self.assertEqual(5, self.e.get_inventory_items("c", "npc", "mara")[0]["qty"])

    def test_integrated_fact_and_belief_effects_keep_secret_out_of_receipts_and_events(self) -> None:
        secret = "MECHANISM_PRIVATE_VALUE"
        self.k.save_operator(
            "c",
            {
                "id": "integrated.knowledge",
                "bindings": {"actor": {"kind": "npc", "default": "npc:mara"}},
                "effects": [
                    {
                        "op": "fact.assert",
                        "fact_id": "hidden_fact",
                        "subject_binding": "actor",
                        "predicate": "knows.route",
                        "value": secret,
                    },
                    {
                        "op": "belief.set",
                        "binding": "actor",
                        "fact_id": "hidden_fact",
                        "value": secret,
                        "status": "believes",
                    },
                ],
            },
        )
        before_event = max((event["id"] for event in self.e.recent_events("c", 100)), default=0)
        result = self.k.execute_operator("c", "integrated.knowledge")
        receipt = self.k.get_receipt("c", result["execution_id"])
        events = [event for event in self.e.recent_events("c", 100) if event["id"] > before_event]
        self.assertNotIn(secret, json.dumps({"result": result, "receipt": receipt, "events": events}, sort_keys=True))
        with self.e._db() as db:
            fact = db.execute(
                "SELECT object_value_json FROM we4_facts WHERE campaign_id='c' AND fact_id='hidden_fact'"
            ).fetchone()
            belief = db.execute(
                "SELECT belief_value_json FROM we4_beliefs WHERE campaign_id='c' AND fact_id='hidden_fact'"
            ).fetchone()
        self.assertEqual(secret, json.loads(fact["object_value_json"]))
        self.assertEqual(secret, json.loads(belief["belief_value_json"]))

    def test_callback_exception_rolls_back_all_effects_and_revision(self) -> None:
        with self.e._write_db() as db:
            db.execute("CREATE TABLE mechanism_callback_test(value TEXT NOT NULL)")

        def callback(db, campaign_id, effect, bindings, **context):
            if context["phase"] == "preflight":
                return {"passed": True, "reason_code": "ok"}
            if effect["key"] == "second":
                raise RuntimeError("fail after first mutation")
            db.execute("INSERT INTO mechanism_callback_test(value) VALUES(?)", (effect["key"],))
            return {"applied": True, "result": {"key": effect["key"]}}

        self._install_callback(callback)
        self.k.save_operator(
            "c",
            {
                "id": "rollback",
                "effects": [
                    {"op": "world_state.set", "key": "first", "value": True},
                    {"op": "world_state.set", "key": "second", "value": True},
                ],
            },
        )
        before = self.e.get_campaign("c")["revision"]
        with self.assertRaisesRegex(RuntimeError, "fail after first"):
            self.k.execute_operator("c", "rollback")
        self.assertEqual(before, self.e.get_campaign("c")["revision"])
        with self.e._db() as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM mechanism_callback_test").fetchone()[0])
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM mechanism_execution_receipts WHERE operator_id='rollback'").fetchone()[0])

    def test_missing_callback_fails_closed_for_effects(self) -> None:
        # The integrated WorldEngine supplies the callback. Shadow it here to
        # exercise the kernel's fail-closed behavior when a host omits one.
        self.e._mechanism_apply_effect_db = None
        self.k.save_operator(
            "c", {"id": "needs.callback", "effects": [{"op": "world_state.set", "key": "x", "value": True}]}
        )
        evaluation = self.k.evaluate_operator("c", "needs.callback")
        self.assertFalse(evaluation["eligible"])
        self.assertIn("transition_preflight_failed", evaluation["reasons"])
        with self.assertRaisesRegex(ValueError, "not eligible"):
            self.k.execute_operator("c", "needs.callback")

    def test_aggregate_limits_and_query_cap(self) -> None:
        with self.assertRaisesRegex(ValueError, "depth"):
            deep = value = {}
            for _ in range(20):
                value["next"] = {}
                value = value["next"]
            self.k.validate_operator_document({"id": "deep", "metadata": deep})
        with self.assertRaisesRegex(ValueError, "encoded size"):
            self.k.validate_operator_document({"id": "large", "metadata": {"x": "a" * MAX_OPERATOR_BYTES}})
        with self.assertRaisesRegex(ValueError, "considerations"):
            self.k.validate_operator_document(
                {
                    "id": "many.considerations",
                    "considerations": [
                        {"read": {"source": "constant", "value": 1}} for _ in range(MAX_CONSIDERATIONS + 1)
                    ],
                }
            )
        with self.assertRaisesRegex(ValueError, "tags"):
            self.k.validate_operator_document({"id": "many.tags", "tags": [str(i) for i in range(MAX_TAGS + 1)]})
        with self.e._write_db() as db:
            for index in range(MAX_QUERY_LIMIT + 5):
                self.k._save_operator_db(db, "c", {"id": f"bulk.{index:03d}"})
        self.assertEqual(MAX_QUERY_LIMIT, len(self.k.list_operators("c", limit=10_000)))


if __name__ == "__main__":
    unittest.main()
