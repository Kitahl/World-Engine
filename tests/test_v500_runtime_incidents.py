from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from world_engine import WorldEngine
from world_engine.incidents import IncidentKernel
from world_engine.mechanisms import MechanismKernel


class RuntimeConvergenceV500Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = WorldEngine(Path(self.tmp.name) / "world.sqlite3")
        self.engine.ensure_campaign(
            "c", "Runtime convergence", "2020-01-01T00:00:00+00:00"
        )
        self.engine.upsert_location("c", "harbor", "Harbor")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_event_visibility_and_causal_roots_are_canonical(self) -> None:
        root = self.engine.commit_event("c", "root", "A public cause.")
        child = self.engine.commit_event(
            "c", "child", "A private consequence.",
            sensitivity="SECRET", scope_type="GM",
            causal_parent_event_id=root["id"],
        )
        grandchild = self.engine.commit_event(
            "c", "grandchild", "A public consequence.",
            causal_parent_event_id=child["id"],
        )
        self.assertEqual(root["id"], root["causal_root_event_id"])
        self.assertEqual(root["id"], child["causal_root_event_id"])
        self.assertEqual(root["id"], grandchild["causal_root_event_id"])
        context = self.engine.get_world_context("c")
        summaries = {event["summary"] for event in context["recent_events"]}
        self.assertNotIn("A private consequence.", summaries)
        self.assertIn("A public consequence.", summaries)
        self.engine.commit_event(
            "c", "entity_notice", "Ada alone may see this.",
            scope_type="ENTITY", principal_kind="npc", principal_id="ada",
        )
        summaries = {
            event["summary"]
            for event in self.engine.get_world_context("c")["recent_events"]
        }
        self.assertNotIn("Ada alone may see this.", summaries)
        with self.assertRaisesRegex(ValueError, "requires principal"):
            self.engine.commit_event(
                "c", "bad", "Bad scope", scope_type="ENTITY"
            )

    def test_in_transaction_operator_uses_outer_revision_and_scoped_idempotency(self) -> None:
        kernel = MechanismKernel(self.engine)
        kernel.save_operator(
            "c",
            {
                "id": "runtime.flag",
                "effects": [
                    {"op": "world_state.set", "key": "runtime_flag", "value": True}
                ],
            },
        )
        before = self.engine.get_campaign("c")["revision"]
        with self.engine._write_db() as db:
            revision = self.engine._next_revision(db, "c")
            first = kernel.execute_operator_db(
                db, "c", "runtime.flag", revision=revision,
                execution_scope="incident.alpha", idempotency_key="boundary",
            )
            replay = kernel.execute_operator_db(
                db, "c", "runtime.flag", revision=revision,
                execution_scope="incident.alpha", idempotency_key="boundary",
            )
            second_scope = kernel.execute_operator_db(
                db, "c", "runtime.flag", revision=revision,
                execution_scope="incident.beta", idempotency_key="boundary",
            )
        self.assertEqual(before + 1, self.engine.get_campaign("c")["revision"])
        self.assertEqual(revision, first["after_revision"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertNotEqual(first["execution_id"], second_scope["execution_id"])

    def test_daily_incident_executes_mop_and_propagates_secrecy(self) -> None:
        kernel = MechanismKernel(self.engine)
        kernel.save_operator(
            "c",
            {
                "id": "incident.raise_alarm",
                "effects": [
                    {"op": "world_state.set", "key": "harbor_alarm", "value": True}
                ],
            },
        )
        IncidentKernel(self.engine).save_definition(
            "c", "harbor.unrest", "political", "harbor_unrest",
            "Unrest rises at {scope_id}.", operator_id="incident.raise_alarm",
            cooldown_minutes=60 * 24 * 30, suppression_minutes=0,
        )
        result = self.engine.advance_world("c", 24 * 60)
        self.assertEqual(1, result["simulation"]["incidents_selected"])
        state = {
            row["state_key"]: row["value"]
            for row in self.engine.get_world_state("c", "world", "global")
        }
        self.assertTrue(state["harbor_alarm"])
        snapshot = IncidentKernel(self.engine).public_snapshot(
            "c", location_id="harbor"
        )
        self.assertEqual("harbor.unrest", snapshot["incidents"][0]["definition_id"])
        incident = next(
            event for event in self.engine.recent_events("c", 30)
            if event["event_type"] == "harbor_unrest"
        )
        effect = next(
            event for event in self.engine.recent_events("c", 30)
            if event["event_type"] == "world_state_change"
        )
        self.assertEqual(incident["id"], effect["causal_parent_event_id"])
        self.assertEqual(incident["id"], effect["causal_root_event_id"])

        IncidentKernel(self.engine).save_definition(
            "c", "secret.unrest", "political", "secret_unrest",
            "Hidden unrest rises at {scope_id}.",
            operator_id="incident.raise_alarm", sensitivity="SECRET",
            cooldown_minutes=60 * 24 * 30, suppression_minutes=0,
        )
        self.engine.advance_world("c", 24 * 60)
        with self.engine._db() as db:
            secret_root = db.execute(
                "SELECT id FROM events WHERE campaign_id='c' AND event_type='secret_unrest'"
            ).fetchone()
            self.assertIsNotNone(secret_root)
            children = db.execute(
                "SELECT sensitivity,scope_type FROM events WHERE campaign_id='c' "
                "AND causal_parent_event_id=?",
                (secret_root["id"],),
            ).fetchall()
            self.assertTrue(children)
            self.assertTrue(all(row["sensitivity"] == "SECRET" for row in children))
            self.assertTrue(all(row["scope_type"] == "GM" for row in children))
        public_types = {
            event["event_type"]
            for event in self.engine.get_world_context("c")["recent_events"]
        }
        self.assertNotIn("secret_unrest", public_types)

    def test_instance_visibility_is_immutable_and_public_dispatch_is_closed(self) -> None:
        kernel = IncidentKernel(self.engine)
        kernel.save_definition(
            "c", "hidden", "secret", "hidden_event", "Hidden at {scope_id}.",
            sensitivity="SECRET", cooldown_minutes=0, suppression_minutes=0,
        )
        self.engine.advance_world("c", 24 * 60)
        self.assertEqual([], kernel.public_snapshot("c")["incidents"])
        kernel.save_definition(
            "c", "hidden", "secret", "hidden_event", "Hidden at {scope_id}.",
            sensitivity="PUBLIC", cooldown_minutes=0, suppression_minutes=0,
        )
        self.assertEqual([], kernel.public_snapshot("c")["incidents"])
        with self.assertRaisesRegex(ValueError, "unknown incident operation"):
            kernel.dispatch("save_definition", "c", {})
        public_history = kernel.dispatch(
            "history", "c", {"privileged": True}
        )["events"]
        self.assertNotIn(
            "hidden_event", {event["event_type"] for event in public_history}
        )

    def test_long_definition_id_and_chunked_advances_have_stable_identities(self) -> None:
        def run(chunks: list[int]) -> list[tuple[str, str, str]]:
            temp = tempfile.TemporaryDirectory()
            self.addCleanup(temp.cleanup)
            engine = WorldEngine(Path(temp.name) / "world.sqlite3")
            engine.ensure_campaign("c", "Chunk", "2020-01-01T00:00:00+00:00")
            engine.upsert_location("c", "harbor", "Harbor")
            MechanismKernel(engine).save_operator(
                "c", {"id": "mark", "effects": [
                    {"op": "world_state.set", "key": "marked", "value": True}
                ]},
            )
            IncidentKernel(engine).save_definition(
                "c", "x" * 100, "test", "chunk_event", "Chunk at {scope_id}.",
                operator_id="mark", cooldown_minutes=0, suppression_minutes=0,
            )
            for minutes in chunks:
                engine.advance_world("c", minutes)
            with engine._db() as db:
                return [
                    (row["id"], row["selection_key"], row["operator_execution_id"])
                    for row in db.execute(
                        "SELECT id,selection_key,operator_execution_id "
                        "FROM incident_instances WHERE campaign_id='c' "
                        "ORDER BY selected_world_time,id"
                    ).fetchall()
                ]

        self.assertEqual(run([2 * 24 * 60]), run([24 * 60, 24 * 60]))


if __name__ == "__main__":
    unittest.main()
