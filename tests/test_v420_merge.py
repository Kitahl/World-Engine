from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from world_engine import WorldEngine
from world_engine.narrative import NarrativeKernel


V41_SCHEMA = """
CREATE TABLE we41_narrative_config (
    campaign_id TEXT PRIMARY KEY,
    rollout_mode TEXT NOT NULL,
    style_json TEXT NOT NULL,
    quality_json TEXT NOT NULL,
    output_counter INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE we41_npc_voice_profiles (
    campaign_id TEXT NOT NULL,
    npc_id TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,npc_id)
);
CREATE TABLE we41_narrative_beats (
    campaign_id TEXT NOT NULL,
    beat_id TEXT NOT NULL,
    beat_json TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    use_count INTEGER NOT NULL,
    last_used_counter INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,beat_id)
);
CREATE TABLE we41_motif_threads (
    campaign_id TEXT NOT NULL,
    motif_id TEXT NOT NULL,
    motif_json TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    use_count INTEGER NOT NULL,
    last_used_counter INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,motif_id)
);
CREATE TABLE we41_dialogue_memory (
    campaign_id TEXT NOT NULL,
    npc_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,npc_id,thread_id)
);
CREATE TABLE we41_narrative_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL,
    packet_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    turn_id TEXT,
    scene_key TEXT,
    rollout_mode TEXT NOT NULL,
    hard_pass INTEGER NOT NULL,
    receipt_json TEXT NOT NULL,
    output_excerpt TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class V420MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "merge.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def _seed_v41_shape(self) -> None:
        engine = WorldEngine(self.path)
        engine.ensure_campaign("c", "Upgrade")
        engine.upsert_npc("c", "mara", "Mara", hp=8, max_hp=8, ac=12)
        with engine._write_db() as db:
            db.execute("DELETE FROM we42_schema_features WHERE feature_id='v41_narrative_import'")
            db.executescript(V41_SCHEMA)
            now = "2026-08-30T00:00:00+00:00"
            db.execute(
                "INSERT INTO we41_narrative_config VALUES(?,?,?,?,?,?)",
                (
                    "c", "enforce", json.dumps({"pov": "second_person", "metaphor_density": 0}),
                    json.dumps({"near_duplicate_threshold": 0.91}), 7, now,
                ),
            )
            db.execute(
                "INSERT INTO we41_npc_voice_profiles VALUES(?,?,?,?)",
                (
                    "c", "mara",
                    json.dumps({
                        "author_style": "named living writer", "imitate": "copy style",
                        "example_utterances": ["Bring me evidence, not rumors."],
                    }),
                    now,
                ),
            )
            db.execute(
                "INSERT INTO we41_narrative_beats VALUES(?,?,?,?,?,?,?)",
                (
                    "c", "warning", json.dumps({
                        "kind": "dialogue_scene", "dramatic_objective": "Warn the player.",
                        "information_to_withhold": ["protected witness"], "tension_before": None,
                        "tension_target": None, "saliency": 0.9, "cooldown": 4,
                    }), 1, 2, 5, now,
                ),
            )
            db.execute(
                "INSERT INTO we41_motif_threads VALUES(?,?,?,?,?,?,?)",
                (
                    "c", "bell", json.dumps({
                        "symbol": "cracked bell", "meaning": "warnings arriving late",
                        "cooldown": 3, "max_recurrences": 4,
                    }), 1, 1, 3, now,
                ),
            )
            db.execute(
                "INSERT INTO we41_dialogue_memory VALUES(?,?,?,?,?)",
                (
                    "c", "mara", "road", json.dumps({
                        "facts_communicated": ["fact-1"], "facts_concealed": ["fact-2"],
                        "speech_acts": ["warn"], "recent_realization_hashes": ["abc"],
                        "private_subtext": "PRIVATE-V41-SUBTEXT",
                    }), now,
                ),
            )
            db.execute(
                """INSERT INTO we41_narrative_receipts(
                       campaign_id,packet_hash,output_hash,turn_id,scene_key,rollout_mode,
                       hard_pass,receipt_json,output_excerpt,created_at)
                   VALUES('c','p','o','t','s','enforce',1,'{}','excerpt',?)""",
                (now,),
            )
            db.execute("PRAGMA user_version=14")

    def test_v41_rows_import_once_without_deleting_source(self):
        self._seed_v41_shape()
        migrated = WorldEngine(self.path)
        with sqlite3.connect(self.path) as check_db:
            self.assertEqual(20, check_db.execute("PRAGMA user_version").fetchone()[0])
        check_db.close()
        self.assertEqual("enforce", migrated.get_narrative_config("c")["mode"])

        voice = migrated.get_narrative_config("c")
        self.assertEqual("second_person", voice["style_profile"]["pov"])
        stored_voice = NarrativeKernel(migrated).get_voice_profile("c", "mara")["profile"]
        self.assertNotIn("author_style", stored_voice)
        self.assertNotIn("imitate", stored_voice)
        self.assertFalse(stored_voice["voice_anchor_ready"])
        self.assertEqual(["author_style", "imitate"], stored_voice["migration_removed_fields"])

        beat = NarrativeKernel(migrated).get_beat("c", "warning")
        self.assertEqual(2, beat["use_count"])
        self.assertEqual(4, beat["cooldown_turns"])
        self.assertEqual(["protected witness"], beat["information_to_withhold"])
        motif = NarrativeKernel(migrated).get_motif("c", "bell")
        self.assertEqual(1, motif["use_count"])

        with migrated._db() as db:
            source_counts = {
                table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "we41_narrative_config", "we41_npc_voice_profiles", "we41_narrative_beats",
                    "we41_motif_threads", "we41_dialogue_memory", "we41_narrative_receipts",
                )
            }
            details = json.loads(db.execute(
                "SELECT details_json FROM we42_schema_features WHERE feature_id='v41_narrative_import'"
            ).fetchone()[0])
            imported_dialogue = db.execute(
                "SELECT subtext_state_json FROM we4_dialogue_state WHERE campaign_id='c' AND speaker_key='npc:mara'"
            ).fetchone()[0]
        self.assertEqual({name: 1 for name in source_counts}, source_counts)
        self.assertEqual(1, details["historical_receipts_preserved"])
        self.assertEqual(2, details["voice_fields_removed"])
        self.assertNotIn("PRIVATE-V41-SUBTEXT", imported_dialogue)
        self.assertIn("we41_dialogue_memory", imported_dialogue)
        active_dialogue = NarrativeKernel(migrated).plan_dialogue("c", "mara", topic="road")
        compiled = migrated.compile_turn_context(
            "c", actor_kind=None, actor_id=None,
            intents=[{"type": "interact", "parameters": {"npc_id": "mara", "topic": "road"}}],
        )
        self.assertNotIn("PRIVATE-V41-SUBTEXT", json.dumps({
            "dialogue": active_dialogue, "context": compiled,
        }, sort_keys=True))

        WorldEngine(self.path)
        with migrated._db() as db:
            self.assertEqual(1, db.execute(
                "SELECT COUNT(*) FROM we4_narrative_beats WHERE campaign_id='c' AND beat_id='warning'"
            ).fetchone()[0])

    def test_failed_v41_import_rolls_back_and_retries_cleanly(self):
        self._seed_v41_shape()
        original = NarrativeKernel.migrate_v41_rows_db

        def fail_after_import(kernel, db):
            original(kernel, db)
            raise RuntimeError("injected migration failure")

        with patch.object(NarrativeKernel, "migrate_v41_rows_db", fail_after_import):
            with self.assertRaisesRegex(RuntimeError, "injected migration failure"):
                WorldEngine(self.path)

        db = sqlite3.connect(self.path)
        try:
            self.assertEqual(14, db.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual(0, db.execute(
                "SELECT COUNT(*) FROM we42_schema_features WHERE feature_id='v41_narrative_import'"
            ).fetchone()[0])
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM we4_narrative_config").fetchone()[0])
        finally:
            db.close()

        recovered = WorldEngine(self.path)
        self.assertEqual("enforce", recovered.get_narrative_config("c")["mode"])
        with recovered._db() as db:
            self.assertEqual(20, db.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual(1, db.execute(
                "SELECT COUNT(*) FROM we42_schema_features WHERE feature_id='v41_narrative_import'"
            ).fetchone()[0])


class V420SecurityAndContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "security.sqlite3"
        self.e = WorldEngine(self.path)
        self.e.ensure_campaign("c")
        self.e.upsert_location("c", "inn", "Inn")
        self.e.upsert_character("c", "hero", "Hero", location="inn", hp=10, max_hp=10, ac=12)
        self.e.upsert_npc(
            "c", "mara", "Mara", location="inn", hp=8, max_hp=8, ac=11,
            beliefs=["PRIVATE-BELIEF-ALPHA"], goals=["PRIVATE-GOAL-BETA"],
            memory=["PRIVATE-MEMORY-GAMMA"], routine={"route": "PRIVATE-ROUTE-DELTA"},
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _turn(self, key: str) -> tuple[dict, list[dict]]:
        intents = [{"type": "interact", "parameters": {"npc_id": "mara", "topic": "road"}}]
        result = self.e.resolve_turn(
            "c", actor_kind="character", actor_id="hero", intents=intents,
            raw_player_text="Ask Mara about the road.", idempotency_key=key,
        )
        return result, intents

    def test_private_npc_cognition_stays_out_of_context_turn_and_render_packets(self):
        result, intents = self._turn("private-boundary")
        packet = self.e.build_narrative_packet(
            "c", turn_result=result, task="dialogue", actor_kind="character", actor_id="hero",
            intents=intents, mode_override="shadow",
        )
        rendered = json.dumps({"turn": result, "packet": packet}, sort_keys=True)
        for marker in (
            "PRIVATE-BELIEF-ALPHA", "PRIVATE-GOAL-BETA", "PRIVATE-MEMORY-GAMMA", "PRIVATE-ROUTE-DELTA",
        ):
            self.assertNotIn(marker, rendered)

    def test_packet_hash_tampering_is_a_hard_failure(self):
        result, intents = self._turn("hash-boundary")
        packet = self.e.build_narrative_packet(
            "c", turn_result=result, task="dialogue", actor_kind="character", actor_id="hero",
            intents=intents, mode_override="shadow", persist=True,
        )
        self.assertEqual("NRP-1.2", packet["packet_version"])
        packet["scene"]["task"] = "tampered"
        receipt = NarrativeKernel(self.e).quality_check(
            "c", "Rain ticks against the window while Mara points to the road.", packet=packet, record=False,
        )
        self.assertFalse(receipt["hard_pass"])
        self.assertIn("packet_source_mismatch", {item["code"] for item in receipt["hard_failures"]})

    def test_rehashed_caller_packet_cannot_override_stored_packet(self):
        result, intents = self._turn("stored-hash-boundary")
        packet = self.e.build_narrative_packet(
            "c", turn_result=result, task="dialogue", actor_kind="character", actor_id="hero",
            intents=intents, mode_override="shadow", persist=True,
        )
        forged = json.loads(json.dumps(packet))
        forged["scene"]["task"] = "forged"
        core = dict(forged)
        for field in ("packet_id", "digest", "packet_hash"):
            core.pop(field, None)
        forged["digest"] = forged["packet_hash"] = NarrativeKernel._digest(core)
        receipt = NarrativeKernel(self.e).quality_check(
            "c", "Rain ticks against the window while Mara points to the road.",
            packet_id=packet["packet_id"], packet=forged, record=False,
        )
        self.assertFalse(receipt["hard_pass"])
        self.assertIn("packet_source_mismatch", {item["code"] for item in receipt["hard_failures"]})


class V420ApiFailClosedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_key = os.environ.get("WORLD_ENGINE_API_KEY")
        os.environ["WORLD_ENGINE_API_KEY"] = "v420-test-secret-0123456789"
        import app as api

        self.api = api
        self.old_engine = api.engine
        api.engine = WorldEngine(Path(self.tmp.name) / "api.sqlite3")
        api.engine.ensure_campaign("c")
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()
        self.api.engine = self.old_engine
        if self.old_key is None:
            os.environ.pop("WORLD_ENGINE_API_KEY", None)
        else:
            os.environ["WORLD_ENGINE_API_KEY"] = self.old_key
        self.tmp.cleanup()

    def test_enforce_packet_failure_returns_http_500(self):
        payload = {
            "campaign_id": "c", "player_text": "Wait.", "intents": [],
            "idempotency_key": "enforce-fail-closed", "narrative_mode_override": "enforce",
        }
        with patch.object(self.api.engine, "build_narrative_packet", side_effect=RuntimeError("forced")):
            response = self.client.post(
                "/api/turn",
                headers={"Authorization": "Bearer v420-test-secret-0123456789"},
                json=payload,
            )
        self.assertEqual(500, response.status_code)
        self.assertIn("failed closed", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
