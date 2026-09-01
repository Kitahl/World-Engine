from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from world_engine import WorldEngine
from world_engine.narrative import NarrativeKernel
from world_engine.turn_router import DEFAULT_CAPABILITIES


GOOD_DIALOGUE = """Rain ticks against the inn's warped shutters while Mara sorts three damp notices into a careful row. She leaves the fourth folded beneath her palm. Mud darkens the hem of her watch coat, and a fresh nick crosses the brass badge at her collar. “The eastern road is open, but that does not make it safe,” she says. Her gaze moves toward the stable door, where an empty hook sways beside the caravan harness. “Two wagons missed the dusk bell. I need someone who can follow a trail without turning every frightened farmer into a suspect.” She slides a rough map across the table. The ink stops at the old stone bridge. Beyond it, the paper is blank. “Start there. Bring back names, tracks, or survivors. Rumors can wait until we have something solid.” Outside, a horse stamps once in the wet yard, then falls still."""


class NarrativeKernelV402Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "narrative.sqlite3"
        self.e = WorldEngine(self.path)
        self.e.ensure_campaign("c", "Campaign")
        self.e.upsert_location("c", "inn", "Wayfarer's Inn", region="coast")
        self.e.upsert_location("c", "road", "Eastern Road", region="coast")
        self.e.upsert_character("c", "hero", "Hero", location="inn", hp=18, max_hp=18, ac=14)
        self.e.upsert_npc(
            "c", "mara", "Mara", location="inn", faction_id="watch", hp=8, max_hp=8, ac=12,
            importance="major", beliefs=["The eastern road is dangerous"],
            goals=["Protect the village"], memory=["A caravan vanished last week"],
        )
        self.k = NarrativeKernel(self.e)

    def tearDown(self):
        self.tmp.cleanup()

    def _dialogue_result(self, key: str = "dialogue-turn") -> tuple[dict, list[dict]]:
        intents = [{"type": "interact", "parameters": {"npc_id": "mara", "topic": "missing caravan"}}]
        result = self.e.resolve_turn(
            "c", actor_kind="character", actor_id="hero", raw_player_text="Ask Mara about the caravan.",
            intents=intents, idempotency_key=key,
        )
        return result, intents

    def _dialogue_packet(self, key: str = "dialogue-turn", **kwargs) -> dict:
        result, intents = self._dialogue_result(key)
        kwargs.setdefault("mode_override", "shadow")
        return self.e.build_narrative_packet(
            "c", turn_result=result, task="dialogue", actor_kind="character", actor_id="hero",
            intents=intents, raw_player_text="Ask Mara about the caravan.", **kwargs,
        )

    def test_schema_15_and_narrative_tables_exist(self):
        expected = {
            "we4_narrative_config", "we4_npc_voice_profiles", "we4_narrative_beats",
            "we4_motif_threads", "we4_dialogue_state", "we4_narrative_packets",
            "we4_narrative_outputs", "we4_narrative_quality_receipts",
            "we4_narrative_director_state",
        }
        with self.e._db() as db:
            self.assertEqual(WorldEngine.SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(expected.issubset(tables))

    def test_default_mode_is_off_and_configuration_is_revisioned(self):
        config = self.e.get_narrative_config("c")
        self.assertEqual("off", config["mode"])
        self.assertEqual("second_person", config["style_profile"]["pov"])
        before = self.e.get_campaign("c")["revision"]
        changed = self.e.configure_narrative(
            "c", mode="compare", style_profile={"metaphor_density": 0, "horror_intensity": 2},
        )
        self.assertEqual("compare", changed["mode"])
        self.assertEqual(0, changed["style_profile"]["metaphor_density"])
        self.assertEqual(before + 1, changed["revision"])

    def test_voice_profile_requires_two_to_five_original_examples(self):
        with self.assertRaisesRegex(ValueError, "2-5"):
            self.k.save_voice_profile("c", "mara", {"example_utterances": ["One line."]})
        saved = self.k.save_voice_profile(
            "c", "mara",
            {
                "formality": "formal", "directness": "high", "dialect_strength": "light",
                "religious_idiom": ["by the watchfire"],
                "example_utterances": [
                    {"text": "The road keeps its own counsel after dusk.", "contexts": ["dialogue_scene"]},
                    {"text": "Bring me tracks, not tavern guesses.", "contexts": ["missing caravan"]},
                ],
            },
        )
        self.assertTrue(saved["stored"])
        self.assertTrue(saved["profile"]["voice_anchor_ready"])
        self.assertEqual(2, len(saved["profile"]["example_utterances"]))

    def test_voice_profile_rejects_author_imitation_fields(self):
        with self.assertRaisesRegex(ValueError, "imitation"):
            self.k.save_voice_profile(
                "c", "mara",
                {"author_style": "named writer", "example_utterances": ["A.", "B."]},
            )

    def test_dialogue_plan_uses_semantic_cache_not_literal_lines(self):
        self.k.save_voice_profile(
            "c", "mara", {"example_utterances": ["The watch keeps records.", "Rumor is not evidence."]},
        )
        plan = self.k.plan_dialogue(
            "c", "mara", listener_kind="character", listener_id="hero", topic="missing caravan",
            hint={"speech_act": "warn", "facts_to_conceal": ["witness_identity"]},
        )
        self.assertEqual("npc:mara", plan["speaker"])
        self.assertEqual("character:hero", plan["listener"])
        self.assertEqual("warn", plan["speech_act"])
        self.assertFalse(plan["literal_line_cache"])
        self.assertIn("semantic intent", plan["repeat_policy"])
        self.assertEqual(2, len(plan["voice_anchors"]))

    def test_false_belief_boundary_is_preserved_in_dialogue_plan(self):
        fact = self.e.turn_router_dispatch("assert_fact", "c", {
            "subject": "location:road", "predicate": "threat.cause", "object_value": "bandits",
        })
        self.e.turn_router_dispatch("set_belief", "c", {
            "believer": "npc:mara", "fact_id": fact["fact_id"], "belief_value": "cultists", "confidence": 0.8,
        })
        plan = self.k.plan_dialogue(
            "c", "mara", listener_kind="character", listener_id="hero", topic="road",
            hint={"facts_to_reveal": [fact["fact_id"]]},
        )
        belief = next(x for x in plan["facts_authorized_to_reveal"] if x["fact_id"] == fact["fact_id"])
        self.assertEqual("cultists", belief["belief_value"])
        self.assertIn(fact["fact_id"], plan["facts_to_reveal"])
        self.assertIn("false beliefs", plan["knowledge_boundary"])
        self.assertTrue(plan["private_cognition_redacted"])

    def test_storylet_selection_is_deterministic_and_not_consumed_on_compile(self):
        self.k.save_beat(
            "c", "warn-road", kind="dialogue_scene", preconditions={"requires_dialogue": True},
            dramatic_objective="Warn the player without exposing the protected witness.",
            information_to_withhold=["protected witness"], saliency=0.95, urgency=0.8,
            tension_target=0.6, cooldown_turns=5, once=True,
        )
        first = self._dialogue_packet("beat-a")
        # Rebuild from the same authoritative turn: selection and digest remain stable.
        turn = self.e.turn_router_dispatch("get_turn", "c", {"turn_id": first["turn_id"]})["result"]
        intents = [{"type": "interact", "parameters": {"npc_id": "mara", "topic": "missing caravan"}}]
        second = self.e.build_narrative_packet(
            "c", turn_result=turn, task="dialogue", actor_kind="character", actor_id="hero", intents=intents,
            raw_player_text="Ask Mara about the caravan.", mode_override="shadow",
        )
        self.assertEqual("warn-road", first["narrative_director"]["selected_beat"]["beat_id"])
        self.assertEqual(first["packet_id"], second["packet_id"])
        self.assertEqual(0, self.k.get_beat("c", "warn-road")["use_count"])

    def test_accepted_output_consumes_storylet_once_and_is_idempotent(self):
        self.k.save_beat(
            "c", "warn-road", kind="dialogue_scene", preconditions={"requires_dialogue": True},
            dramatic_objective="Warn the player.", saliency=1.0, urgency=1.0, once=True,
        )
        packet = self._dialogue_packet("consume")
        recorded = self.e.record_narrative_output(
            "c", packet["packet_id"], GOOD_DIALOGUE,
            beat_realizations=[{"beat_id": "warn-road"}],
        )
        self.assertTrue(recorded["accepted"], recorded["quality_receipt"])
        self.assertEqual(1, self.k.get_beat("c", "warn-road")["use_count"])
        replay = self.e.record_narrative_output(
            "c", packet["packet_id"], GOOD_DIALOGUE,
            beat_realizations=[{"beat_id": "warn-road"}],
        )
        self.assertTrue(replay["accepted"])
        self.assertFalse(replay["state_update"]["consumed"])
        self.assertEqual(1, self.k.get_beat("c", "warn-road")["use_count"])
        next_packet = self._dialogue_packet("consume-next")
        self.assertNotEqual("warn-road", next_packet["narrative_director"]["selected_beat"]["beat_id"])

    def test_motif_is_backend_selected_but_consumed_only_when_explicitly_recorded(self):
        self.k.save_motif(
            "c", "cracked-bell", symbol="cracked bell", meaning="warnings that arrived too late",
            linked_entities=["npc:mara"], eligible_scene_types=["dialogue_scene"],
            cooldown_turns=10, max_recurrences=3, metadata={"priority": 1.0},
        )
        packet = self._dialogue_packet("motif")
        self.assertEqual("cracked-bell", packet["motif_thread"]["motif_id"])
        self.assertEqual(0, self.k.get_motif("c", "cracked-bell")["use_count"])
        result = self.e.record_narrative_output(
            "c", packet["packet_id"], GOOD_DIALOGUE, motifs_used=["cracked-bell"],
        )
        self.assertTrue(result["accepted"])
        self.assertEqual(1, self.k.get_motif("c", "cracked-bell")["use_count"])
        next_packet = self._dialogue_packet("motif-next")
        self.assertIsNone(next_packet["motif_thread"])

    def test_rejected_output_does_not_consume_storylet_or_motif(self):
        self.k.save_beat(
            "c", "do-not-consume", kind="dialogue_scene",
            preconditions={"requires_dialogue": True}, dramatic_objective="Wait for an accepted render.",
            saliency=1.0, urgency=1.0, once=True,
        )
        self.k.save_motif(
            "c", "unspent-motif", symbol="unlit lantern", meaning="knowledge withheld",
            linked_entities=["npc:mara"], eligible_scene_types=["dialogue_scene"],
            cooldown_turns=5, max_recurrences=2, metadata={"priority": 1.0},
        )
        packet = self._dialogue_packet("rejected")
        result = self.e.record_narrative_output(
            "c", packet["packet_id"], "You decide to confess. ::WST[SECRET]",
            motifs_used=["unspent-motif"],
        )
        self.assertFalse(result["accepted"])
        self.assertFalse(result["state_update"]["consumed"])
        self.assertEqual(0, self.k.get_beat("c", "do-not-consume")["use_count"])
        self.assertEqual(0, self.k.get_motif("c", "unspent-motif")["use_count"])

    def test_cutscene_packet_is_typed_hidden_and_player_safe(self):
        turn, intents = self._dialogue_result("cutscene")
        packet = self.e.build_narrative_packet(
            "c", turn_result=turn, task="cutscene", actor_kind="character", actor_id="hero",
            intents=intents, raw_player_text="Listen.",
            mode_override="shadow",
            narrative_hint={"cutscene_packet": {
                "cutscene_id": "bell-warning",
                "scene_goal": "Reveal that the eastern road has gone silent without exposing the witness.",
                "location": "inn",
                "participants": ["character:hero", "npc:mara"],
                "beats": [
                    {"objective": "Establish the empty harness hook."},
                    {"objective": "Mara warns the player through an authorized dialogue intent."},
                ],
                "physical_actions": [
                    {"actor": "npc:mara", "action": "slides the map across the table", "authority": "authored_npc_action"}
                ],
                "choices": ["Ask who last saw the wagons", "Inspect the map"],
                "ending_state": {"world_mutation": False},
            }},
        )
        cutscene = packet["cutscene_packet"]
        self.assertEqual("CUT-1.0", cutscene["cutscene_version"])
        self.assertTrue(cutscene["hidden_structure"])
        self.assertEqual("cutscene", packet["scene"]["scene_function"])
        self.assertEqual(2, len(cutscene["choices"]))
        with self.assertRaisesRegex(ValueError, "player-character cutscene actions require"):
            self.k.validate_cutscene_packet("c", {
                "scene_goal": "Force a voluntary confession.",
                "beats": ["The player confesses."],
                "physical_actions": [{"actor": "character:hero", "action": "confesses"}],
            })

    def test_quality_gate_detects_internal_mechanics_leakage(self):
        packet = {"scene": {"target_words": {"min": 0, "max": 1000}}, "style_profile": {"pov": "second_person"}}
        receipt = self.k.quality_check(
            "c", "The door opens. ::WST[ROOM|SECRET] context_packet revision_before", packet=packet, record=False,
        )
        self.assertFalse(receipt["hard_pass"])
        codes = {x["code"] for x in receipt["hard_failures"]}
        self.assertTrue(any(x.startswith("mechanics_leak") for x in codes))

    def test_quality_gate_detects_player_authorship_violations(self):
        packet = {"scene": {"target_words": {"min": 0, "max": 1000}}, "style_profile": {"pov": "second_person"}, "render_contract": {"player_agency": {"mechanically_supported_effects": []}}}
        receipt = self.k.quality_check(
            "c", "You decide to forgive her. You say that everything is fine. You feel relieved.",
            packet=packet, record=False,
        )
        self.assertFalse(receipt["hard_pass"])
        codes = {x["code"] for x in receipt["hard_failures"]}
        self.assertIn("player_agency:invented_decision", codes)
        self.assertIn("player_agency:invented_player_dialogue", codes)
        self.assertIn("player_agency:invented_emotional_conclusion", codes)

    def test_mechanically_supported_involuntary_effect_can_be_narrated(self):
        packet = {
            "scene": {"target_words": {"min": 0, "max": 1000}},
            "style_profile": {"pov": "second_person"},
            "render_contract": {"player_agency": {"mechanically_supported_effects": ["frightened"]}},
        }
        receipt = self.k.quality_check("c", "The sigil flares. You are afraid while its magic holds.", packet=packet, record=False)
        self.assertTrue(receipt["hard_pass"], receipt["hard_failures"])

    def test_quality_gate_flags_near_duplicate_recent_output(self):
        packet = self._dialogue_packet("duplicate")
        first = self.e.record_narrative_output("c", packet["packet_id"], GOOD_DIALOGUE)
        self.assertTrue(first["accepted"])
        near_duplicate = GOOD_DIALOGUE.replace("caravan", "wagon", 1)
        second = self.k.quality_check("c", near_duplicate, packet_id=packet["packet_id"], record=False)
        self.assertTrue(any(x["code"] == "near_duplicate_recent_output" for x in second["soft_warnings"]))
        self.assertTrue(second["revision_required"])

    def test_narrative_capability_is_present_in_current_manifest_and_dispatches(self):
        self.assertEqual(33, len(DEFAULT_CAPABILITIES))
        manifest = next(x for x in self.e.list_capabilities("c") if x["capability_id"] == "narrative.manage")
        self.assertEqual("narrative_kernel", manifest["provider"])
        result = self.e.resolve_turn(
            "c", intents=[{"type": "narrative", "parameters": {"operation": "get_config"}}],
            idempotency_key="narrative-capability",
        )
        self.assertEqual("completed", result["status"])
        self.assertEqual("off", result["steps"][0]["result"]["mode"])

    def test_schema_13_database_migrates_to_15_without_losing_campaign(self):
        path = Path(self.tmp.name) / "old.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute(
                """CREATE TABLE campaigns(
                    id TEXT PRIMARY KEY,name TEXT NOT NULL,world_time TEXT NOT NULL,weather TEXT NOT NULL DEFAULT 'clear',
                    revision INTEGER NOT NULL DEFAULT 0,settings_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"""
            )
            db.execute(
                "INSERT INTO campaigns VALUES('old','Old','1492-01-01T08:00:00+00:00','clear',0,'{}','now','now')"
            )
            db.execute("PRAGMA user_version=13")
            db.commit()
        db.close()
        migrated = WorldEngine(path)
        self.assertEqual("Old", migrated.get_campaign("old")["name"])
        with migrated._db() as db:
            self.assertEqual(WorldEngine.SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='we4_narrative_packets'").fetchone())


class NarrativeApiV402Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.key = "narrative-api-secret-0123456789"
        self.old_env = os.environ.get("WORLD_ENGINE_API_KEY")
        os.environ["WORLD_ENGINE_API_KEY"] = self.key
        import app as api
        self.api = api
        self.old_engine = api.engine
        api.engine = WorldEngine(Path(self.tmp.name) / "api.sqlite3")
        api.engine.ensure_campaign("c")
        api.engine.upsert_location("c", "inn", "Inn")
        api.engine.upsert_character("c", "hero", "Hero", location="inn", hp=10, max_hp=10)
        api.engine.upsert_npc("c", "mara", "Mara", location="inn", hp=5, max_hp=5)
        self.client = TestClient(api.app)
        self.headers = {"Authorization": f"Bearer {self.key}"}

    def tearDown(self):
        self.client.close()
        self.api.engine = self.old_engine
        if self.old_env is None:
            os.environ.pop("WORLD_ENGINE_API_KEY", None)
        else:
            os.environ["WORLD_ENGINE_API_KEY"] = self.old_env
        self.tmp.cleanup()

    def _call(self, key: str, override: str | None = None) -> dict:
        revision = self.api.engine.get_campaign("c")["revision"]
        payload = {
            "campaign_id": "c", "actor_kind": "character", "actor_id": "hero",
            "expected_revision": revision, "idempotency_key": key,
            "player_text": "Ask Mara about the road.",
            "intents": [{"type": "interact", "parameters": {"npc_id": "mara", "topic": "road"}}],
            "narrative_hint": {"speaker_id": "mara", "speech_act": "warn"},
        }
        if override:
            payload["narrative_mode_override"] = override
        response = self.client.post("/api/turn", headers=self.headers, json=payload)
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def test_api_returns_shadow_packet_and_v420_receipt(self):
        body = self._call("shadow", "shadow")
        self.assertEqual("5.1.0", body["_engine_receipt"]["engine_version"])
        self.assertEqual(WorldEngine.SCHEMA_VERSION, body["_engine_receipt"]["schema_version"])
        self.assertIn("_narrative_shadow", body)
        packet = body["_narrative_shadow"]
        self.assertEqual("shadow", packet["mode"])
        self.assertEqual("NRP-1.2", packet["packet_version"])
        self.assertEqual("npc:mara", packet["dialogue_plan"]["speaker"])
        self.assertEqual("shadow", body["_turn_directives"]["narrative_runtime"]["mode"])

    def test_compare_and_enforce_modes_preserve_explicit_activation(self):
        compare = self._call("compare", "compare")
        self.assertIn("_narrative_compare", compare)
        self.assertEqual("baseline", compare["_narrative_compare"]["player_facing_default"])
        enforce = self._call("enforce", "enforce")
        self.assertIn("_narrative_render_packet", enforce)
        self.assertTrue(enforce["_turn_directives"]["narrative_runtime"]["player_facing_candidate"])


if __name__ == "__main__":
    unittest.main()
