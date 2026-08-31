from __future__ import annotations

import ast
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from world_engine import WorldEngine
from world_engine.companion import (
    CompanionService,
    PresentationConflict,
    PresentationEnvelope,
    canonical_json_bytes,
)
from world_engine.narrative import NarrativeKernel


GOOD_PROSE = (
    "Rain taps the shutters while Mara studies the empty harness hook beside the stable door. "
    "She folds the road map once, sets it between two cups, and points toward the old bridge. "
    "Outside, a horse stamps in the wet yard. The eastern track remains open, but no wagon has "
    "crossed it since dusk. Mara asks for tracks, names, or survivors and waits beside the lamp."
)
CHOICES = ["Ask who last saw the wagons", "Inspect the map"]
ALT_PROSE = (
    "Lantern smoke curls beneath the rafters as Mara unrolls a salt-stained chart. "
    "Three charcoal marks follow the coastline, each ending beside a different watch post. "
    "A brass compass rests near the margin, its needle trembling whenever thunder crosses the bay. "
    "Mara names the northern path, closes the shutters, and waits for the room to settle."
)
OTHER_PROSE = (
    "Morning light reaches the courtyard where Mara measures wheel ruts beside the fountain. "
    "Fresh clay clings to one groove, while crushed fern lies beneath the other. "
    "She places a ribbon across the wider track, checks the distance between its edges, and calls "
    "for a stable ledger. The bell above the gate rings once as she finishes the comparison."
)


@pytest.fixture
def engine(tmp_path):
    item = WorldEngine(tmp_path / "publication.sqlite3")
    item.ensure_campaign("c", "Campaign")
    item.upsert_location("c", "inn", "Wayfarer's Inn", region="coast")
    item.upsert_character(
        "c", "hero", "Hero", location="inn", hp=18, max_hp=18, ac=14
    )
    item.upsert_npc(
        "c", "mara", "Mara", location="inn", hp=8, max_hp=8, ac=12,
        importance="major",
    )
    return item


def packet(engine: WorldEngine, key: str, *, semantic: bool, secret: str | None = None):
    engine.configure_narrative(
        "c",
        quality_config={"semantic_authority_review_required": semantic},
    )
    if secret:
        engine.narrative_dispatch(
            "save_beat",
            "c",
            {
                "beat_id": f"beat_{key}",
                "kind": "dialogue_scene",
                "preconditions": {"requires_dialogue": True},
                "dramatic_objective": "Warn the party without exposing protected detail.",
                "information_to_withhold": [secret],
                "saliency": 1.0,
                "urgency": 1.0,
            },
        )
    intents = [
        {"type": "interact", "parameters": {"npc_id": "mara", "topic": "road"}}
    ]
    turn = engine.resolve_turn(
        "c",
        actor_kind="character",
        actor_id="hero",
        raw_player_text="Ask Mara about the road.",
        intents=intents,
        idempotency_key=key,
    )
    return engine.build_narrative_packet(
        "c",
        turn_result=turn,
        task="dialogue",
        actor_kind="character",
        actor_id="hero",
        intents=intents,
        raw_player_text="Ask Mara about the road.",
        choice_options=CHOICES,
        mode_override="enforce",
    )


def publish(engine: WorldEngine, item: dict, **updates):
    values = {
        "campaign_id": "c",
        "presentation_id": "pres_" + item["packet_id"][-16:],
        "packet_id": item["packet_id"],
        "narration": GOOD_PROSE,
        "expected_revision": item["authority"]["authoritative_state"]["campaign"]["revision"],
        "turn_id": item["turn_id"],
        "choices": list(item["scene"]["choice_options"]),
        "presentation": {},
    }
    values.update(updates)
    return engine.publish_presentation(**values)


def counts(engine: WorldEngine) -> dict[str, int]:
    tables = (
        "we43_narrative_publication_attempts",
        "we43_narrative_packet_acceptances",
        "we43_narrative_semantic_attestations",
        "we4_narrative_quality_receipts",
        "we4_narrative_outputs",
        "we_companion_presentations",
        "we_companion_outbox",
    )
    with engine._db() as db:
        return {
            table: db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }


def test_current_schema_contains_atomic_publication_fences(engine):
    with engine._db() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 17
        acceptance_pk = {
            row[1]: row[5]
            for row in db.execute(
                'PRAGMA table_info("we43_narrative_packet_acceptances")'
            )
        }
        presentation_columns = {
            row[1] for row in db.execute('PRAGMA table_info("we_companion_presentations")')
        }
        outbox_columns = {
            row[1] for row in db.execute('PRAGMA table_info("we_companion_outbox")')
        }
    assert acceptance_pk["campaign_id"] == 1
    assert acceptance_pk["packet_id"] == 2
    assert {"packet_id", "accepted_output_id"} <= presentation_columns
    assert {"packet_id", "accepted_output_id"} <= outbox_columns


def test_exact_replay_is_one_acceptance_and_does_not_reconsume(engine):
    item = packet(engine, "replay", semantic=False)
    before = engine.narrative_dispatch("get_director_state", "c")
    first = publish(
        engine,
        item,
        communicated_fact_ids=["untrusted-fact"],
        motifs_used=["untrusted-motif"],
        beat_realizations=[{"beat_id": "untrusted-beat"}],
    )
    after_first = engine.narrative_dispatch("get_director_state", "c")
    replay = publish(engine, item)
    after_replay = engine.narrative_dispatch("get_director_state", "c")

    assert first["status"] == "accepted"
    assert first["replayed"] is False
    assert replay["status"] == "accepted"
    assert replay["replayed"] is True
    for key in ("candidate_digest", "accepted_output_id", "presentation_id", "outbox_id"):
        assert replay[key] == first[key]
    assert after_replay["turn_index"] == after_first["turn_index"]
    assert after_first["turn_index"] == before["turn_index"] + 1
    result_counts = counts(engine)
    assert result_counts["we43_narrative_packet_acceptances"] == 1
    assert result_counts["we4_narrative_outputs"] == 1
    assert result_counts["we_companion_outbox"] == 1


def test_different_candidate_after_acceptance_conflicts(engine):
    item = packet(engine, "conflict", semantic=False)
    publish(engine, item)
    with pytest.raises(ValueError, match="PRESENTATION_PACKET_ALREADY_ACCEPTED"):
        publish(engine, item, narration=GOOD_PROSE + " The lamp burns lower.")
    assert counts(engine)["we43_narrative_packet_acceptances"] == 1


def test_exact_race_replays_and_conflicting_race_has_one_winner(
    engine, monkeypatch
):
    original_quality_check = NarrativeKernel.quality_check
    barrier = Barrier(2)

    def synchronized_quality_check(self, *args, **kwargs):
        result = original_quality_check(self, *args, **kwargs)
        if kwargs.get("publication_read_only"):
            barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(NarrativeKernel, "quality_check", synchronized_quality_check)
    exact_packet = packet(engine, "race-exact", semantic=False)
    peer = WorldEngine(engine.db_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        exact = list(pool.map(lambda e: publish(e, exact_packet), (engine, peer)))
    assert sorted(result["replayed"] for result in exact) == [False, True]

    conflict_packet = packet(engine, "race-conflict", semantic=False)
    barrier = Barrier(2)
    peer = WorldEngine(engine.db_path)

    def candidate(args):
        target, prose = args
        try:
            return publish(target, conflict_packet, narration=prose)
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        raced = list(pool.map(candidate, ((engine, ALT_PROSE), (peer, OTHER_PROSE))))
    assert sum(isinstance(result, dict) for result in raced) == 1
    assert raced.count("PRESENTATION_PACKET_ALREADY_ACCEPTED") == 1
    assert counts(engine)["we43_narrative_packet_acceptances"] == 2


def test_deterministic_rejection_is_audit_only_and_redacted(engine):
    secret = "the protected witness is Ilyra"
    item = packet(engine, "rejected", semantic=False, secret=secret)
    result = publish(engine, item, narration=f"{GOOD_PROSE} {secret}.")
    assert result["status"] == "rejected"
    assert result["reason_codes"] == ["DETERMINISTIC_QUALITY_REJECTED"]
    result_counts = counts(engine)
    assert result_counts["we43_narrative_publication_attempts"] == 1
    for table in (
        "we43_narrative_packet_acceptances",
        "we4_narrative_quality_receipts",
        "we4_narrative_outputs",
        "we_companion_presentations",
        "we_companion_outbox",
    ):
        assert result_counts[table] == 0
    with engine._db() as db:
        attempt = db.execute(
            "SELECT canonical_candidate_json,reason_codes_json "
            "FROM we43_narrative_publication_attempts"
        ).fetchone()
    assert attempt["canonical_candidate_json"] is None
    assert secret.lower() not in json.dumps(dict(attempt)).lower()


def test_semantic_pending_is_attempt_only_and_approval_accepts_exact_candidate(engine):
    item = packet(engine, "pending", semantic=True)
    pending = publish(engine, item)
    assert pending["status"] == "semantic_review_required"
    assert pending["accepted"] is False
    pending_counts = counts(engine)
    assert pending_counts["we43_narrative_publication_attempts"] == 1
    assert pending_counts["we4_narrative_outputs"] == 0
    assert pending_counts["we_companion_outbox"] == 0

    accepted = engine.attest_publication_attempt(
        "c",
        pending["attempt_id"],
        authority_kind="human",
        reviewer_id="reviewer-1",
        decision="approve",
    )
    assert accepted["status"] == "accepted"
    assert accepted["candidate_digest"] == pending["candidate_digest"]
    assert accepted["acceptance_mode"] == "semantic_attested"
    accepted_counts = counts(engine)
    assert accepted_counts["we43_narrative_semantic_attestations"] == 1
    assert accepted_counts["we43_narrative_packet_acceptances"] == 1
    assert accepted_counts["we_companion_outbox"] == 1


def test_semantic_rejection_never_creates_output_or_outbox(engine):
    item = packet(engine, "semantic-reject", semantic=True)
    pending = publish(engine, item)
    rejected = engine.attest_publication_attempt(
        "c",
        pending["attempt_id"],
        authority_kind="trusted_server",
        reviewer_id="semantic-gate",
        decision="reject",
    )
    assert rejected["status"] == "rejected"
    result_counts = counts(engine)
    assert result_counts["we43_narrative_semantic_attestations"] == 1
    assert result_counts["we43_narrative_packet_acceptances"] == 0
    assert result_counts["we4_narrative_outputs"] == 0
    assert result_counts["we_companion_outbox"] == 0
    with pytest.raises(ValueError, match="SEMANTIC_ATTESTATION_BINDING_FAILED"):
        engine.attest_publication_attempt(
            "c",
            pending["attempt_id"],
            authority_kind="human",
            reviewer_id="reviewer-2",
            decision="approve",
        )


def test_candidate_and_stored_attempt_tamper_fail_closed(engine):
    item = packet(engine, "tamper", semantic=True)
    with pytest.raises(ValueError, match="PRESENTATION_SCHEMA_CLOSED"):
        publish(engine, item, presentation={"candidate_digest": "0" * 64})
    with pytest.raises(ValueError, match="PRESENTATION_CHOICES_MISMATCH"):
        publish(engine, item, choices=list(reversed(CHOICES)))
    assert counts(engine)["we43_narrative_publication_attempts"] == 0

    pending = publish(engine, item)
    with engine._write_db() as db:
        db.execute(
            "UPDATE we43_narrative_publication_attempts "
            "SET canonical_candidate_json=canonical_candidate_json||' ' "
            "WHERE campaign_id='c' AND attempt_id=?",
            (pending["attempt_id"],),
        )
    with pytest.raises(ValueError, match="PUBLICATION_ATTEMPT_INTEGRITY_FAILED"):
        engine.attest_publication_attempt(
            "c",
            pending["attempt_id"],
            authority_kind="human",
            reviewer_id="reviewer-1",
            decision="approve",
        )
    assert counts(engine)["we_companion_outbox"] == 0


@pytest.mark.parametrize(
    "unsafe_narration",
    (
        "Read https://example.test before continuing.",
        "Read example.test before continuing.",
        "<b>The gate opens.</b>",
        "# The gate opens",
        "The *gate* opens.",
    ),
)
def test_publication_boundary_rejects_urls_html_and_markdown_before_audit(
    engine, unsafe_narration
):
    item = packet(engine, "unsafe-boundary", semantic=False)
    with pytest.raises(ValueError, match="PUBLIC_PRESENTATION_CONTENT_INVALID"):
        publish(engine, item, narration=unsafe_narration)
    assert all(value == 0 for value in counts(engine).values())


def test_missing_narrative_config_fails_without_initialization_or_audit(engine):
    item = packet(engine, "missing-config", semantic=False)
    with engine._write_db() as db:
        db.execute("DELETE FROM we4_narrative_config WHERE campaign_id='c'")
    with pytest.raises(ValueError, match="NARRATIVE_CONFIG_MISSING"):
        publish(engine, item)
    with engine._db() as db:
        assert db.execute(
            "SELECT 1 FROM we4_narrative_config WHERE campaign_id='c'"
        ).fetchone() is None
    assert all(value == 0 for value in counts(engine).values())


def test_full_engine_companion_wrapper_cannot_preseed_self_attested_evidence(engine):
    item = packet(engine, "preseed", semantic=False)
    presentation_id = "pres-forged"
    evidence = {
        "verification_version": "NOV-1.0",
        "campaign_id": "c",
        "turn_id": item["turn_id"],
        "authoritative_revision": item["authority"]["authoritative_state"][
            "campaign"
        ]["revision"],
        "packet_id": item["packet_id"],
        "packet_digest": item["digest"],
        "packet_version": item["packet_version"],
        "output_id": "nout-forged",
        "output_hash": hashlib.sha256(GOOD_PROSE.encode("utf-8")).hexdigest(),
        "receipt_id": "nqr-forged",
        "receipt_version": "NQR-1.2",
        "accepted": True,
        "hard_pass": True,
    }
    evidence["evidence_digest"] = hashlib.sha256(
        canonical_json_bytes(evidence)
    ).hexdigest()
    envelope = PresentationEnvelope(
        campaign_id="c",
        presentation_id=presentation_id,
        revision=evidence["authoritative_revision"],
        narration=GOOD_PROSE,
        turn_id=item["turn_id"],
        choices=tuple(CHOICES),
        presentation={
            "presentation_version": "WEP-1.0",
            "kind": "narrative",
            "presentation_id": presentation_id,
            "narrative_evidence": evidence,
        },
    )
    with pytest.raises(PresentationConflict, match="COMPANION_ACCEPTANCE_REQUIRED"):
        CompanionService(engine).publish(envelope)
    assert all(value == 0 for value in counts(engine).values())


def test_failure_after_companion_enqueue_rolls_back_every_acceptance_write(
    engine, monkeypatch
):
    item = packet(engine, "rollback", semantic=False)
    before = engine.narrative_dispatch("get_director_state", "c")
    original = CompanionService.enqueue_presentation_db

    def fail_after_enqueue(self, db, envelope, **kwargs):
        original(self, db, envelope, **kwargs)
        raise RuntimeError("injected-after-enqueue")

    monkeypatch.setattr(CompanionService, "enqueue_presentation_db", fail_after_enqueue)
    with pytest.raises(RuntimeError, match="injected-after-enqueue"):
        publish(engine, item)
    after = engine.narrative_dispatch("get_director_state", "c")
    assert after["turn_index"] == before["turn_index"]
    assert all(value == 0 for value in counts(engine).values())


def test_static_acceptance_structure_has_one_connection_and_final_fence():
    source_path = Path(__file__).resolve().parents[1] / "world_engine" / "engine.py"
    source = source_path.read_text(encoding="utf8")
    tree = ast.parse(source)
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_accept_publication_candidate"
    )
    segment = ast.get_source_segment(source, function)
    assert segment is not None
    assert "accept_publication_output_db(" in segment
    assert "enqueue_presentation_db(db, envelope)" in segment
    assert "INSERT INTO we43_narrative_packet_acceptances" in segment
    assert segment.index("enqueue_presentation_db(db, envelope)") < segment.index(
        "INSERT INTO we43_narrative_packet_acceptances"
    )
    assert ".publish(envelope)" not in segment
    write_function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_write_db"
    )
    write_segment = ast.get_source_segment(source, write_function)
    assert write_segment is not None
    assert 'conn.execute("BEGIN IMMEDIATE")' in write_segment


def test_latest_accepted_presentation_returns_only_closed_public_envelope(engine):
    item = packet(engine, "latest-safe", semantic=False)
    accepted = publish(engine, item)
    latest = engine.latest_accepted_presentation("c")

    assert set(latest) == {
        "campaign_id", "presentation", "content_sha256", "accepted_at",
    }
    public = latest["presentation"]
    assert set(public) == {
        "campaign_id", "presentation_id", "revision", "narration",
        "turn_id", "choices", "presentation",
    }
    assert public["presentation_id"] == accepted["presentation_id"]
    assert public["narration"] == GOOD_PROSE
    assert public["choices"] == CHOICES
    evidence = public["presentation"]["narrative_evidence"]
    assert evidence["accepted"] is True
    assert evidence["packet_id"] == item["packet_id"]
    serialized = json.dumps(latest)
    for private_key in ("validation_context", "forbidden_literals", "information_to_withhold"):
        assert private_key not in serialized


def test_latest_accepted_presentation_fails_closed_on_tampered_bytes(engine):
    item = packet(engine, "latest-tamper", semantic=False)
    publish(engine, item)
    with engine._write_db() as db:
        row = db.execute(
            "SELECT canonical_bytes FROM we_companion_presentations WHERE campaign_id=?",
            ("c",),
        ).fetchone()
        forged_envelope = json.loads(bytes(row["canonical_bytes"]).decode("utf-8"))
        forged_envelope["narration"] = "FORGED PLAYER-FACING NARRATION"
        forged_bytes = canonical_json_bytes(forged_envelope)
        db.execute(
            """UPDATE we_companion_presentations
               SET canonical_bytes=?,content_sha256=? WHERE campaign_id=?""",
            (forged_bytes, hashlib.sha256(forged_bytes).hexdigest(), "c"),
        )
    with pytest.raises(ValueError, match="PUBLICATION_ACCEPTANCE_INTEGRITY_FAILED"):
        engine.latest_accepted_presentation("c")
