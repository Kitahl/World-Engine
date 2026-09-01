#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from world_engine import WorldEngine
from world_engine.narrative import NarrativeKernel
from world_engine.turn_router import DEFAULT_CAPABILITIES

GOOD = (
    "Rain ticks against the inn's warped shutters while Mara sorts three damp notices into a careful row. "
    "She leaves the fourth folded beneath her palm. Mud darkens the hem of her watch coat, and a fresh nick "
    "crosses the brass badge at her collar. “The eastern road is open, but that does not make it safe,” she says. "
    "Her gaze moves toward the stable door, where an empty hook sways beside the caravan harness. “Two wagons "
    "missed the dusk bell. Bring back names, tracks, or survivors. Rumors can wait until we have something solid.”"
)
BAD = "You decide to confess. You say you are afraid. ::WST[ROAD|SECRET] context_packet revision_before"


def run(*, release: str = "5.0.1") -> dict:
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "audit.sqlite3"
        engine = WorldEngine(db_path)
        engine.ensure_campaign("audit", "Narrative audit")
        engine.upsert_location("audit", "inn", "Wayfarer's Inn", region="coast")
        engine.upsert_character("audit", "hero", "Hero", location="inn", hp=18, max_hp=18, ac=14)
        engine.upsert_npc(
            "audit", "mara", "Mara", location="inn", faction_id="watch", hp=8, max_hp=8, ac=12,
            importance="major", beliefs=["The road is dangerous"], goals=["Protect the village"],
            memory=["A caravan vanished last week"],
        )
        kernel = NarrativeKernel(engine)
        default_mode = kernel.get_config("audit")["mode"]
        voice = kernel.save_voice_profile(
            "audit", "mara",
            {"formality": "formal", "directness": "high", "example_utterances": [
                {"text": "Bring me tracks, not tavern guesses.", "contexts": ["missing caravan"]},
                {"text": "The watch keeps records because memory is a poor witness.", "contexts": ["dialogue_scene"]},
            ]},
        )
        kernel.save_beat(
            "audit", "warn-road", kind="dialogue_scene", preconditions={"requires_dialogue": True},
            dramatic_objective="Warn without leaking the witness identity.",
            information_to_withhold=["witness identity"], saliency=1.0, urgency=0.8,
            tension_target=0.6, cooldown_turns=4, once=True,
        )
        kernel.save_motif(
            "audit", "cracked-bell", symbol="cracked bell", meaning="warnings arriving too late",
            linked_entities=["npc:mara"], eligible_scene_types=["dialogue_scene"],
            cooldown_turns=5, max_recurrences=3, metadata={"priority": 1.0},
        )
        kernel.configure(
            "audit", mode="shadow", reason="release audit packet generation",
            quality_config={"semantic_authority_review_required": False},
        )
        intents = [{"type": "interact", "parameters": {"npc_id": "mara", "topic": "missing caravan"}}]
        turn = engine.resolve_turn(
            "audit", actor_kind="character", actor_id="hero", raw_player_text="Ask Mara about the caravan.",
            intents=intents, idempotency_key="audit-turn",
        )
        packet = engine.build_narrative_packet(
            "audit", turn_result=turn, task="dialogue", actor_kind="character", actor_id="hero",
            intents=intents, raw_player_text="Ask Mara about the caravan.",
            narrative_hint={"speaker_id": "mara", "speech_act": "warn", "facts_to_conceal": ["witness identity"]},
        )
        good_receipt = engine.check_narrative_quality("audit", GOOD, packet_id=packet["packet_id"], record=False)
        bad_receipt = engine.check_narrative_quality("audit", BAD, packet_id=packet["packet_id"], record=False)
        before_turn = kernel.get_director_state("audit")["turn_index"]
        publish_args = {
            "campaign_id": "audit",
            "presentation_id": "audit-presentation",
            "packet_id": packet["packet_id"],
            "narration": GOOD,
            "expected_revision": packet["authority"]["authoritative_state"]["campaign"]["revision"],
            "turn_id": packet["turn_id"],
            "choices": list(packet["scene"]["choice_options"]),
        }
        accepted = engine.publish_presentation(**publish_args)
        after_turn = kernel.get_director_state("audit")["turn_index"]
        replay = engine.publish_presentation(**publish_args)
        latest = engine.latest_accepted_presentation("audit")
        compare = engine.configure_narrative("audit", mode="compare", reason="release audit")
        with closing(sqlite3.connect(db_path)) as db, db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            schema_version = db.execute("PRAGMA user_version").fetchone()[0]
            expected_tables = {
                "we4_narrative_config", "we4_npc_voice_profiles", "we4_narrative_beats",
                "we4_motif_threads", "we4_dialogue_state", "we4_narrative_packets",
                "we4_narrative_outputs", "we4_narrative_quality_receipts",
                "we4_narrative_director_state",
            }
            actual_tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            narrative_tables = len(expected_tables.intersection(actual_tables))
        checks = {
            "schema_current": schema_version == WorldEngine.SCHEMA_VERSION,
            "nine_narrative_tables": narrative_tables == 9,
            "thirty_three_capabilities": len(DEFAULT_CAPABILITIES) == 33,
            "new_campaign_default_off": default_mode == "off",
            "explicit_shadow_packet": packet["mode"] == "shadow",
            "packet_hash_present": len(packet.get("packet_hash", "")) == 64,
            "voice_anchors": len(voice["profile"]["example_utterances"]) == 2,
            "semantic_not_literal_cache": packet["dialogue_plan"]["literal_line_cache"] is False,
            "persistent_storylet_selected": packet["narrative_director"]["selected_beat"]["beat_id"] == "warn-road",
            "motif_selected": packet["motif_thread"]["motif_id"] == "cracked-bell",
            "good_hard_pass": good_receipt["hard_pass"] is True,
            "bad_hard_fail": bad_receipt["hard_pass"] is False,
            "accepted_consumes_once": accepted["status"] == "accepted" and after_turn == before_turn + 1,
            "idempotent_replay_no_reconsume": replay["replayed"] is True,
            "accepted_presentation_safe": latest["presentation"]["presentation_id"] == "audit-presentation",
            "atomic_outbox_bound": accepted["outbox_id"] == replay["outbox_id"],
            "compare_configured": compare["mode"] == "compare",
            "sqlite_integrity": integrity == "ok",
        }
        return {
            "release": release,
            "packet_version": packet["packet_version"],
            "quality_receipt_version": good_receipt["receipt_version"],
            "schema_version": schema_version,
            "narrative_table_count": narrative_tables,
            "capability_manifest_count": len(DEFAULT_CAPABILITIES),
            "sqlite_integrity": integrity,
            "checks": checks,
            "passed": all(checks.values()),
            "packet_digest": packet["digest"],
            "good_receipt_id": good_receipt["receipt_id"],
            "bad_failure_codes": [x["code"] for x in bad_receipt["hard_failures"]],
            "accepted_publication": accepted,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the World Engine narrative publication runtime.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--release", default="5.0.1")
    args = parser.parse_args()
    result = run(release=args.release)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
