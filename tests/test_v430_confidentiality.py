from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from world_engine import WorldEngine
from world_engine.narrative import NarrativeKernel
from world_engine.public_projection import attach_turn_directives
from world_engine.turn_router import TurnRouter, _public_step_error


GOOD_PROSE = (
    "Rain taps the shutters while Mara studies the empty harness hook beside the stable door. "
    "She folds the road map once, sets it between two cups, and points toward the old bridge. "
    "Outside, a horse stamps in the wet yard. The eastern track remains open, but no wagon has "
    "crossed it since dusk. Mara asks for tracks, names, or survivors and waits beside the lamp."
)


class ConfidentialityV430Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "world.sqlite3"
        self.e = WorldEngine(self.path)
        self.e.ensure_campaign("c", "Campaign")
        self.e.upsert_location("c", "inn", "Wayfarer's Inn", region="coast")
        self.e.upsert_location("c", "road", "Eastern Road", region="coast")
        self.e.upsert_character("c", "hero", "Hero", location="inn", hp=18, max_hp=18, ac=14)
        self.e.upsert_npc(
            "c", "mara", "Mara", location="inn", faction_id="watch",
            hp=8, max_hp=8, ac=12, importance="major",
        )
        self.router = TurnRouter(self.e)
        self.k = NarrativeKernel(self.e)

    def tearDown(self):
        self.tmp.cleanup()

    def _dialogue_packet(self, key: str, *, secret: str | None = None) -> dict:
        if secret:
            self.k.save_beat(
                "c", "private-warning", kind="dialogue_scene",
                preconditions={"requires_dialogue": True},
                dramatic_objective=f"Warn the player without naming {secret}.",
                information_to_withhold=[secret], saliency=1.0, urgency=1.0,
                once=True,
            )
        intents = [{"type": "interact", "parameters": {"npc_id": "mara", "topic": "road"}}]
        turn = self.e.resolve_turn(
            "c", actor_kind="character", actor_id="hero",
            raw_player_text="Ask Mara about the road.", intents=intents,
            idempotency_key=key,
        )
        return self.e.build_narrative_packet(
            "c", turn_result=turn, task="dialogue", actor_kind="character", actor_id="hero",
            intents=intents, raw_player_text="Ask Mara about the road.", mode_override="enforce",
        )

    def test_believer_view_is_opt_in_and_never_substitutes_canonical_truth(self):
        known = self.router.assert_fact("c", "location:road", "threat.cause", "bandits")
        self.router.set_belief("c", "character:hero", known["fact_id"], belief_value="cultists")
        unknown = self.router.assert_fact("c", "location:road", "witness.name", "Ilyra")

        canonical = self.router.knowledge_snapshot("c", believer="character:hero")
        scoped = self.router.knowledge_snapshot(
            "c", believer="character:hero", fact_view="believer",
        )

        self.assertEqual("bandits", next(x for x in canonical["facts"] if x["fact_id"] == known["fact_id"])["object_value"])
        self.assertEqual("cultists", scoped["facts"][0]["object_value"])
        self.assertEqual("BELIEF", scoped["facts"][0]["epistemic_authority"])
        self.assertNotIn(unknown["fact_id"], {x["fact_id"] for x in scoped["facts"]})
    def test_character_knowledge_capability_is_bound_to_actor_belief(self):
        canonical_marker = "DIRECT-CANONICAL-SECRET"
        belief_marker = "DIRECT-FALSE-BELIEF"
        fact = self.router.assert_fact(
            "c", "location:road", "witness.identity", canonical_marker,
        )
        self.router.set_belief(
            "c", "character:hero", fact["fact_id"], belief_value=belief_marker,
        )
        result = self.router._execute_capability(
            "c", "character", "hero", "knowledge.read", {},
        )
        serialized = json.dumps(result, sort_keys=True)
        self.assertIn(belief_marker, serialized)
        self.assertNotIn(canonical_marker, serialized)
        with self.assertRaisesRegex(PermissionError, "KNOWLEDGE_PRINCIPAL_MISMATCH"):
            self.router._execute_capability(
                "c", "character", "hero", "knowledge.read",
                {"believer": "npc:mara"},
            )

    def test_compiled_context_excludes_world_truth_and_events_for_non_privileged_viewer(self):
        canonical_marker = "CANONICAL-UNKNOWN-MARKER"
        belief_marker = "FALSE-BELIEF-MARKER"
        event_marker = "SECRET-EVENT-MARKER"
        fact = self.router.assert_fact(
            "c", "location:road", "witness.identity", canonical_marker,
        )
        self.router.set_belief(
            "c", "character:hero", fact["fact_id"], belief_value=belief_marker,
        )
        self.e.commit_event("c", "secret_test_event", event_marker, payload={"marker": event_marker})
        for i in range(24):
            self.e.commit_event("c", "history", f"history event {i}")

        kwargs = {
            "campaign_id": "c",
            "actor_kind": "character",
            "actor_id": "hero",
            "location_id": "inn",
            "intents": [{"type": "interact", "parameters": {"topic": canonical_marker}}],
            "max_chars": 60_000,
            "include_archive": True,
        }
        player = self.router.compile_context(
            **kwargs, viewer_kind="player", viewer_id="player-one",
        )
        player_text = json.dumps(player, sort_keys=True)
        player_candidates = [item for tier in player["context"].values() for item in tier]
        self.assertNotIn(canonical_marker, player_text)
        self.assertNotIn(event_marker, player_text)
        self.assertIn(belief_marker, player_text)
        self.assertEqual(0, player["index_revision"])
        self.assertFalse(any(item["kind"] in {"events", "knowledge_claim"} for item in player_candidates))
        self.assertFalse(any(
            item["item_id"] in {"events:recent", "events:archive"}
            for item in player["activation_inspector"]["omitted"]
        ))

        gm = self.router.compile_context(
            **kwargs, viewer_kind="gm", viewer_id="local-gm",
        )
        gm_text = json.dumps(gm, sort_keys=True)
        gm_candidates = [item for tier in gm["context"].values() for item in tier]
        gm_by_id = {item["item_id"]: item for item in gm_candidates}
        self.assertIn(canonical_marker, gm_text)
        self.assertIn(event_marker, gm_text)
        self.assertIn(f"claim:fact:{fact['fact_id']}", gm_by_id)
        self.assertIn("events:recent", gm_by_id)
        self.assertIn("events:archive", gm_by_id)
        for item_id in ("events:recent", "events:archive"):
            self.assertEqual("GM", gm_by_id[item_id]["principal_scope"]["type"])
            self.assertEqual("PRIVATE", gm_by_id[item_id]["sensitivity"])

    def test_enforce_projection_is_allowlisted_and_drops_internal_tree(self):
        marker = "PRIVATE-CONTEXT-MARKER"
        packet = self._dialogue_packet("projection-allowlist")
        projected = attach_turn_directives(
            {
                "protocol_version": "WETP-1.0", "campaign_id": "c", "turn_id": "t",
                "mode": "execute", "status": "completed", "authoritative": True,
                "context_packet": {"context": {"WARM": [{"payload": marker}]}},
                "steps": [{"result": {"internal": marker}}],
                "_engine_receipt": {"signals": {"internal": marker}},
            },
            narrative_packet=packet,
        )
        encoded = json.dumps(projected, sort_keys=True)
        self.assertNotIn(marker, encoded)
        self.assertNotIn("context_packet", encoded)
        self.assertNotIn("steps", projected)
        self.assertIn("_narrative_render_packet", projected)

    def test_public_packet_omits_secret_and_quality_uses_immutable_stored_context(self):
        secret = "the protected witness is Ilyra"
        packet = self._dialogue_packet("private-context", secret=secret)
        self.assertEqual("NRP-1.2", packet["packet_version"])
        encoded_packet = json.dumps(packet, sort_keys=True).lower()
        self.assertNotIn(secret.lower(), encoded_packet)
        self.assertNotIn("information_to_withhold", encoded_packet)
        self.assertNotIn("context_packet", encoded_packet)
        self.assertNotIn("capability_id", encoded_packet)
        self.assertNotIn("revision_before", encoded_packet)
        self.assertNotIn(secret.lower(), json.dumps(self.k.get_packet("c", packet["packet_id"])).lower())

        with self.e._db() as db:
            row = db.execute(
                "SELECT validation_context_json FROM we43_narrative_validation_contexts WHERE campaign_id=? AND packet_id=?",
                ("c", packet["packet_id"]),
            ).fetchone()
        self.assertIn(secret, row["validation_context_json"])

        # Changing mutable storylet state after packet creation must not change
        # what the quality gate validates for this packet.
        self.k.save_beat(
            "c", "private-warning", kind="dialogue_scene",
            information_to_withhold=["a replacement secret"], saliency=1.0, urgency=1.0,
        )
        receipt = self.k.quality_check(
            "c", f"{GOOD_PROSE} {secret}.", packet_id=packet["packet_id"], record=False,
        )
        self.assertIn("withheld_information_leak", {x["code"] for x in receipt["hard_failures"]})
        self.assertNotIn(secret.lower(), json.dumps(receipt).lower())

    def test_validation_context_must_bind_to_packet_digest(self):
        packet = self._dialogue_packet("binding", secret="the hidden cellar key")
        with self.e._write_db() as db:
            db.execute(
                "UPDATE we43_narrative_validation_contexts SET packet_digest='wrong' WHERE campaign_id=? AND packet_id=?",
                ("c", packet["packet_id"]),
            )
        receipt = self.k.quality_check("c", GOOD_PROSE, packet_id=packet["packet_id"], record=False)
        self.assertIn("validation_context_binding", {x["code"] for x in receipt["hard_failures"]})

    def test_long_text_shingles_detect_moved_shared_prose(self):
        shared = " ".join(f"shared{i}" for i in range(120))
        first = f"{' '.join(f'alpha{i}' for i in range(40))} {shared}"
        second = f"{shared} {' '.join(f'omega{i}' for i in range(40))}"
        self.assertGreater(self.k._shingle_similarity(first, second, width=5), 0.72)

    def test_tense_contract_is_reported_and_requires_revision(self):
        packet = {
            "scene": {"target_words": {"min": 0, "max": 1000}},
            "style_profile": {"pov": "third_person_limited", "tense": "past"},
        }
        receipt = self.k.quality_check(
            "c", "Mara is at the door. She looks at the road, opens the map, and says the bridge is clear.",
            packet=packet, record=False,
        )
        self.assertIn("tense_contract_violation", {x["code"] for x in receipt["soft_warnings"]})
        self.assertTrue(receipt["revision_required"])

    def test_new_packets_require_explicit_beat_realization_before_consumption(self):
        packet = self._dialogue_packet("beat-not-realized", secret="the hidden courier")
        first = self.e.record_narrative_output("c", packet["packet_id"], GOOD_PROSE)
        self.assertTrue(first["accepted"], first["quality_receipt"])
        self.assertFalse(first["state_update"]["beat_realized"])
        self.assertEqual(0, self.k.get_beat("c", "private-warning")["use_count"])

        next_packet = self._dialogue_packet("beat-realized")
        second = self.e.record_narrative_output(
            "c", next_packet["packet_id"], GOOD_PROSE,
            beat_realizations=[{"beat_id": "private-warning"}],
        )
        self.assertTrue(second["state_update"]["beat_realized"])
        self.assertEqual(1, self.k.get_beat("c", "private-warning")["use_count"])

    def test_accepted_output_verifier_returns_only_digest_evidence(self):
        secret = "the hidden courier is Ilyra"
        packet = self._dialogue_packet("publish-proof", secret=secret)
        recorded = self.e.record_narrative_output(
            "c", packet["packet_id"], GOOD_PROSE,
            beat_realizations=[{"beat_id": "private-warning"}],
        )
        proof = self.k.verify_accepted_output(
            "c", packet["packet_id"],
            output_hash=recorded["output_hash"],
            receipt_id=recorded["quality_receipt"]["receipt_id"],
        )
        self.assertTrue(proof["accepted"])
        self.assertTrue(proof["hard_pass"])
        self.assertEqual(packet["digest"], proof["packet_digest"])
        self.assertEqual(packet["turn_id"], proof["turn_id"])
        self.assertIsInstance(proof["authoritative_revision"], int)
        self.assertEqual(
            packet["authority"]["authoritative_state"]["campaign"]["revision"],
            proof["authoritative_revision"],
        )
        self.assertEqual(recorded["output_hash"], proof["output_hash"])
        self.assertNotIn(secret.lower(), json.dumps(proof).lower())
        self.assertNotIn("forbidden_literals", proof)
        with self.assertRaisesRegex(KeyError, "no matching"):
            self.k.verify_accepted_output("c", packet["packet_id"], output_hash="0" * 64)

    def test_severe_soft_revision_required_cannot_become_accepted(self):
        self.e.configure_narrative("c", style_profile={"tense": "past"})
        packet = self._dialogue_packet("revision-required")
        present_tense = (
            "Mara is beside the door and looks across the yard. She opens the map and says the eastern "
            "road is quiet. The stable hand is near the gate, and the lantern is bright against the rain. "
            "Mara turns toward the bridge, asks for careful tracks, and waits while the horse stamps outside."
        )
        recorded = self.e.record_narrative_output("c", packet["packet_id"], present_tense)
        self.assertTrue(recorded["quality_receipt"]["hard_pass"])
        self.assertTrue(recorded["quality_receipt"]["revision_required"])
        self.assertFalse(recorded["accepted"])
        with self.assertRaisesRegex(ValueError, "NOT_ACCEPTED"):
            self.k.verify_accepted_output(
                "c", packet["packet_id"], output_hash=recorded["output_hash"],
            )

    def test_exact_accepted_replay_stays_accepted_provable_and_does_not_reconsume(self):
        packet = self._dialogue_packet("exact-replay", secret="the courier wears a silver ring")
        first = self.e.record_narrative_output(
            "c", packet["packet_id"], GOOD_PROSE,
            beat_realizations=[{"beat_id": "private-warning"}],
        )
        self.assertTrue(first["accepted"], first["quality_receipt"])
        turn_after_first = self.k.get_director_state("c")["turn_index"]

        replay = self.e.record_narrative_output(
            "c", packet["packet_id"], GOOD_PROSE,
            beat_realizations=[{"beat_id": "private-warning"}],
        )
        self.assertTrue(replay["accepted"], replay["quality_receipt"])
        self.assertFalse(replay["quality_receipt"]["revision_required"])
        self.assertFalse(replay["state_update"]["consumed"])
        self.assertEqual(turn_after_first, self.k.get_director_state("c")["turn_index"])
        proof = self.k.verify_accepted_output(
            "c", packet["packet_id"], output_hash=replay["output_hash"],
            receipt_id=replay["quality_receipt"]["receipt_id"],
        )
        self.assertTrue(proof["accepted"])

    def test_turn_step_error_never_reflects_exception_text(self):
        private_marker = "PRIVATE-ERROR-DETAIL-CANARY"
        value_error = _public_step_error(ValueError(private_marker))
        unknown_error = _public_step_error(Exception(private_marker))
        self.assertEqual({"code": "ACTION_REJECTED", "retryable": False}, value_error)
        self.assertEqual({"code": "ACTION_FAILED", "retryable": False}, unknown_error)
        self.assertNotIn(private_marker, json.dumps([value_error, unknown_error]))

    def test_projection_rejects_unknown_or_private_packet_fields(self):
        packet = self._dialogue_packet("closed-nrp-envelope")
        top_level = json.loads(json.dumps(packet))
        top_level["future_private_extension"] = "PRIVATE-TOP-LEVEL-CANARY"
        with self.assertRaisesRegex(ValueError, "NARRATIVE_PACKET_PRIVATE_FIELD"):
            attach_turn_directives({}, narrative_packet=top_level)

        nested = json.loads(json.dumps(packet))
        nested["authority"]["validation_context"] = {"forbidden_literals": ["PRIVATE-NESTED-CANARY"]}
        with self.assertRaisesRegex(ValueError, "NARRATIVE_PACKET_PRIVATE_FIELD"):
            attach_turn_directives({}, narrative_packet=nested)

    def test_projection_error_shape_ignores_caller_strings(self):
        marker = "PRIVATE-NARRATIVE-ERROR-CANARY"
        projected = attach_turn_directives(
            {"status": "failed"},
            narrative_error={"mode": marker, "code": marker, "baseline_preserved": True},
        )
        self.assertEqual("off", projected["_narrative_runtime_error"]["mode"])
        self.assertEqual("NARRATIVE_RUNTIME_FAILED", projected["_narrative_runtime_error"]["code"])
        self.assertNotIn(marker, json.dumps(projected))

if __name__ == "__main__":
    unittest.main()
