from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from world_engine.engine import WorldEngine
from world_engine.turn_router import TurnRouter


class ContextCompilerV401Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "context.sqlite3"
        self.e = WorldEngine(self.db_path)
        self.e.ensure_campaign("c", "Context Test")
        self.e.upsert_location("c", "village", "Village", region="north")
        self.e.upsert_location("c", "road", "Road", region="north")
        self.e.upsert_character(
            "c", "hero", "Hero", location="village", hp=12, max_hp=12, ac=14,
            inventory=["rope", "torch"], notes={"public": "adventurer"},
        )
        self.e.upsert_npc(
            "c", "mara", "Mara", location="village", hp=8, max_hp=8, ac=12,
            beliefs=["NPC-BELIEF-SECRET-ALPHA"],
            goals=["NPC-GOAL-SECRET-BETA"],
            routine={"private_route": "NPC-ROUTE-SECRET-GAMMA"},
            memory=["NPC-MEMORY-SECRET-DELTA"], importance="major",
        )
        self.r = TurnRouter(self.e)
        self.r.sync_existing_entities("c")

    def tearDown(self):
        self.tmp.cleanup()

    def _all_text(self, packet: dict) -> str:
        return json.dumps(packet, sort_keys=True, ensure_ascii=False)

    def test_schema_14_claim_receipt_and_fts_structures_exist(self):
        with self.e._db() as db:
            self.assertEqual(16, db.execute("PRAGMA user_version").fetchone()[0])
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        for name in ("knowledge_claims", "context_compile_receipts", "context_compile_items", "context_index_state", "knowledge_fts"):
            self.assertIn(name, tables)

    def test_player_context_does_not_contain_target_npc_private_cognition(self):
        packet = self.r.compile_context(
            "c", actor_kind="character", actor_id="hero", location_id="village",
            intents=[{"type": "interact", "parameters": {"npc_id": "mara", "topic": "caravan"}}],
            max_chars=30000,
        )
        text = self._all_text(packet)
        for marker in ("NPC-BELIEF-SECRET-ALPHA", "NPC-GOAL-SECRET-BETA", "NPC-ROUTE-SECRET-GAMMA", "NPC-MEMORY-SECRET-DELTA"):
            self.assertNotIn(marker, text)
        omitted = {x["item_id"]: x["reason"] for x in packet["activation_inspector"]["omitted"]}
        self.assertIn("npc:cognition:mara", omitted)
        self.assertEqual("NOT_KNOWN_TO_PRINCIPAL", omitted["npc:cognition:mara"])

    def test_gm_principal_can_inspect_private_npc_cognition(self):
        packet = self.r.compile_context(
            "c", actor_kind="character", actor_id="hero", viewer_kind="gm", viewer_id="local-gm",
            location_id="village", max_chars=30000,
        )
        ids = {x["item_id"] for tier in packet["context"].values() for x in tier}
        self.assertIn("npc:cognition:mara", ids)

    def test_private_belief_never_enters_public_fts(self):
        fact = self.r.assert_fact("c", "location:road", "danger.kind", "PUBLIC-BANDITS-EPSILON")
        self.r.set_belief(
            "c", "npc:mara", fact["fact_id"], belief_value="PRIVATE-CULTISTS-ZETA", confidence=0.8,
        )
        self.r.compile_context(
            "c", actor_kind="character", actor_id="hero",
            viewer_kind="gm", viewer_id="test-gm",
            intents=[{"type": "interact", "parameters": {"topic": "bandits"}}],
            max_chars=30000,
        )
        with self.e._db() as db:
            corpus = "\n".join(str(r[0]) for r in db.execute("SELECT object_text FROM knowledge_fts WHERE campaign_id='c'").fetchall())
        self.assertIn("PUBLIC-BANDITS-EPSILON", corpus)
        self.assertNotIn("PRIVATE-CULTISTS-ZETA", corpus)

    def test_retracted_world_claim_is_removed_from_safe_fts(self):
        fact = self.r.assert_fact("c", "location:road", "bridge.state", "RETRACT-ME-THETA")
        self.r.compile_context(
            "c", actor_kind="character", actor_id="hero",
            viewer_kind="gm", viewer_id="test-gm",
            intents=[{"type": "interact", "parameters": {"topic": "bridge"}}],
            max_chars=30000,
        )
        self.r.retract_fact("c", fact["fact_id"])
        self.r.compile_context(
            "c", actor_kind="character", actor_id="hero",
            viewer_kind="gm", viewer_id="test-gm",
            intents=[{"type": "interact", "parameters": {"topic": "bridge"}}],
            max_chars=30000,
        )
        with self.e._db() as db:
            corpus = "\n".join(str(r[0]) for r in db.execute("SELECT object_text FROM knowledge_fts WHERE campaign_id='c'").fetchall())
            row = db.execute("SELECT superseded_revision,status FROM knowledge_claims WHERE campaign_id='c' AND claim_id=?", (f"fact:{fact['fact_id']}",)).fetchone()
        self.assertNotIn("RETRACT-ME-THETA", corpus)
        self.assertEqual("retracted", row["status"])
        self.assertIsNotNone(row["superseded_revision"])

    def test_mandatory_exact_state_overflow_fails_instead_of_truncating(self):
        huge = [{"name": f"item-{i}", "description": "X" * 250} for i in range(80)]
        self.e.upsert_character("c", "hero", "Hero", location="village", hp=12, max_hp=12, ac=14, inventory=huge)
        with self.assertRaisesRegex(ValueError, "CONTEXT_BUDGET_UNSAT"):
            self.r.compile_context("c", actor_kind="character", actor_id="hero", location_id="village", max_chars=2000)

    def test_compile_hash_is_identical_for_100_repetitions_at_same_revision(self):
        kwargs = dict(
            campaign_id="c", actor_kind="character", actor_id="hero", location_id="village",
            intents=[{"type": "interact", "parameters": {"npc_id": "mara", "topic": "caravan"}}],
            max_chars=12000,
        )
        hashes = {self.r.compile_context(**kwargs)["compile_hash"] for _ in range(100)}
        self.assertEqual(1, len(hashes))

    def test_unauthorized_payload_is_not_persisted_in_receipts(self):
        packet = self.r.compile_context("c", actor_kind="character", actor_id="hero", location_id="village", max_chars=30000)
        compile_id = packet["compilation_id"]
        with self.e._db() as db:
            receipt = db.execute("SELECT principal_json,counts_json FROM context_compile_receipts WHERE campaign_id='c' AND compile_id=?", (compile_id,)).fetchone()
            items = db.execute("SELECT candidate_id,authorized,included,exclusion_reason FROM context_compile_items WHERE campaign_id='c' AND compile_id=? ORDER BY candidate_id", (compile_id,)).fetchall()
        persisted = json.dumps({"receipt": dict(receipt), "items": [dict(x) for x in items]}, sort_keys=True)
        for marker in ("NPC-BELIEF-SECRET-ALPHA", "NPC-GOAL-SECRET-BETA", "NPC-ROUTE-SECRET-GAMMA", "NPC-MEMORY-SECRET-DELTA"):
            self.assertNotIn(marker, persisted)

    def test_mutating_turn_returns_post_commit_context(self):
        before = self.e.get_campaign("c")["revision"]
        result = self.e.resolve_turn(
            "c", actor_kind="character", actor_id="hero", expected_revision=before,
            intents=[{"type": "move", "parameters": {"destination": "road"}}],
            max_context_chars=16000,
        )
        self.assertEqual("post_commit", result["context_phase"])
        self.assertEqual(result["revision_after"], result["context_packet"]["snapshot_revision"])
        actor_items = [x for x in result["context_packet"]["context"]["HOT"] if x["item_id"] == "actor:character:hero"]
        self.assertEqual("road", actor_items[0]["payload"]["location"])

    def test_fts_operational_failure_is_logged_and_degrades_to_no_candidates(self):
        class BrokenFts:
            def execute(self, *_args, **_kwargs):
                raise sqlite3.OperationalError("fts unavailable")

        with self.assertLogs("world_engine.turn_router", level="WARNING") as captured:
            result = self.r._fts_claim_candidates(BrokenFts(), "c", "known secret")

        self.assertEqual([], result)
        self.assertIn("fts unavailable", "\n".join(captured.output))

    def test_fts_unexpected_programming_error_is_not_silenced(self):
        class BrokenProgram:
            def execute(self, *_args, **_kwargs):
                raise RuntimeError("programming defect")

        with self.assertRaisesRegex(RuntimeError, "programming defect"):
            self.r._fts_claim_candidates(BrokenProgram(), "c", "known secret")

if __name__ == "__main__":
    unittest.main()
