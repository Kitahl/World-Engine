"""Executable qualification for the confined World Engine 5.1.1 player CLI."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts import headless_player_v511 as player
from world_engine import WorldEngine


@pytest.fixture(scope="module")
def base_session(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("headless-player-base")
    result = player.controller_new(
        path,
        seed="v511-headless-player-tests",
        config=player.DEFAULT_CONFIG,
    )
    assert result["ok"] is True
    assert result["gates"] == {
        "generated": True,
        "validated": True,
        "dry_run": True,
        "promoted": True,
        "confidentiality": "pass",
    }
    return path


@pytest.fixture()
def session(base_session: Path, tmp_path: Path) -> Path:
    destination = tmp_path / "session"
    destination.mkdir()
    for name in (player.DATABASE_NAME, player.SESSION_NAME):
        shutil.copy2(base_session / name, destination / name)
    return destination


def _metadata(path: Path) -> dict[str, Any]:
    return json.loads((path / player.SESSION_NAME).read_text(encoding="utf-8"))


def _engine(path: Path) -> WorldEngine:
    return WorldEngine(path / player.DATABASE_NAME)


def _destination(observation: dict[str, Any]) -> str:
    current = observation["player"]["location_id"]
    for link in observation["world_map"]["links"]:
        if link["from_id"] == current and link["to_id"] != current:
            return str(link["to_id"])
    raise AssertionError("generated player location has no public adjacent destination")


def _private_markers(path: Path) -> list[str]:
    metadata = _metadata(path)
    return player._private_canaries(_engine(path), metadata["campaign_id"])


def _encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def test_controller_bootstrap_is_explicit_idempotent_and_private(base_session: Path) -> None:
    replay = player.controller_new(
        base_session,
        seed="v511-headless-player-tests",
        config=player.DEFAULT_CONFIG,
    )
    assert replay["phase"] == "controller_setup"
    assert replay["idempotent_replay"] is True
    assert replay["observation"]["player"]
    assert replay["observation"]["projection_sequence"] == replay["observation"]["campaign"]["revision"]
    public = _encoded(replay)
    for marker in _private_markers(base_session):
        assert marker not in public
    with pytest.raises(player.PlayerError, match="different world request") as conflict:
        player.controller_new(base_session, seed="different", config=player.DEFAULT_CONFIG)
    assert conflict.value.code == "SESSION_CREATE_CONFLICT"


@pytest.mark.parametrize("intent_type", ["combat_start", "combat_next", "combat_end"])
def test_combat_lifecycle_intents_are_absent_and_reject_before_mutation(
    session: Path,
    intent_type: str,
) -> None:
    engine = _engine(session)
    metadata = _metadata(session)
    campaign_id = metadata["campaign_id"]
    before_revision = int(engine.get_campaign(campaign_id)["revision"])
    with engine._db() as db:
        before_turns = db.execute(
            "SELECT COUNT(*) FROM we4_turn_records WHERE campaign_id=?",
            (campaign_id,),
        ).fetchone()[0]
    advertised = player.player_observe(session)["allowed_intents"]["types"]
    assert intent_type not in advertised

    with pytest.raises(player.PlayerError) as rejected:
        player.player_act(
            session,
            text="Attempt remote combat lifecycle control.",
            intents=[
                {
                    "type": intent_type,
                    "parameters": {
                        "combat_id": "remote_unobserved_combat",
                        "target_id": "remote_unobserved_npc",
                    },
                }
            ],
            expected_revision=before_revision,
            idempotency_key=f"rejected-{intent_type}",
        )
    assert rejected.value.code == "PLAYER_INTENT_NOT_ALLOWED"
    reopened = _engine(session)
    assert int(reopened.get_campaign(campaign_id)["revision"]) == before_revision
    with reopened._db() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM we4_turn_records WHERE campaign_id=?",
            (campaign_id,),
        ).fetchone()[0] == before_turns


def test_prepublish_interruption_cleans_staging_and_exact_retry_succeeds(tmp_path: Path) -> None:
    session_path = tmp_path / "prepublish"
    with pytest.raises(player.PlayerError) as interrupted:
        player.controller_new(
            session_path,
            seed="prepublish-fault",
            config=player.DEFAULT_CONFIG,
            fault_stage="before_database_publish",
        )
    assert interrupted.value.code == "TEST_FAULT_INJECTED"
    assert not (session_path / player.DATABASE_NAME).exists()
    assert not (session_path / player.SESSION_NAME).exists()
    assert not (session_path / f"{player.SESSION_NAME.removesuffix('.json')}.tmp").exists()
    assert not list(session_path.glob(".headless-build-*"))

    created = player.controller_new(
        session_path,
        seed="prepublish-fault",
        config=player.DEFAULT_CONFIG,
    )
    assert created["ok"] is True
    assert created["idempotent_replay"] is False


def test_subprocess_postpublish_interruption_recovers_exactly_and_conflict_preserves_db(
    tmp_path: Path,
) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "headless_player_v511.py"
    session_path = tmp_path / "postpublish"
    command = [
        sys.executable,
        str(script),
        "new",
        "--session-dir",
        str(session_path),
        "--seed",
        "postpublish-fault",
        "--config-json",
        player._canonical(player.DEFAULT_CONFIG),
    ]
    base_env = os.environ.copy()
    base_env.pop("WORLD_ENGINE_HEADLESS_TEST_FAULT", None)
    fault_env = dict(base_env)
    fault_env["WORLD_ENGINE_HEADLESS_TEST_FAULT"] = "after_database_publish"
    interrupted = subprocess.run(
        command,
        cwd=script.parents[1],
        env=fault_env,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert interrupted.returncode == 2
    interrupted_body = json.loads(interrupted.stdout)
    assert interrupted_body["error"]["code"] == "TEST_FAULT_INJECTED"
    db_path = session_path / player.DATABASE_NAME
    metadata_path = session_path / player.SESSION_NAME
    assert db_path.is_file()
    assert not metadata_path.exists()
    before_revision = int(
        player._open_published_engine_without_initialization(db_path).get_campaign("headless")["revision"]
    )
    before_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()

    recovered = subprocess.run(
        command,
        cwd=script.parents[1],
        env=base_env,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert recovered.returncode == 0, recovered.stdout
    recovered_body = json.loads(recovered.stdout)
    assert recovered_body["idempotent_replay"] is True
    assert metadata_path.is_file()
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before_hash
    assert int(
        player._open_published_engine_without_initialization(db_path).get_campaign("headless")["revision"]
    ) == before_revision

    with pytest.raises(player.PlayerError) as conflict:
        player.controller_new(
            session_path,
            seed="different-request",
            config=player.DEFAULT_CONFIG,
        )
    assert conflict.value.code == "SESSION_CREATE_CONFLICT"
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before_hash
    assert int(
        player._open_published_engine_without_initialization(db_path).get_campaign("headless")["revision"]
    ) == before_revision


def test_unknown_db_only_and_metadata_only_sessions_fail_closed_and_are_preserved(
    tmp_path: Path,
) -> None:
    db_only = tmp_path / "db-only"
    db_only.mkdir()
    unknown_db = db_only / player.DATABASE_NAME
    WorldEngine(unknown_db)
    before_db = unknown_db.read_bytes()
    with pytest.raises(player.PlayerError) as invalid_db:
        player.controller_new(
            db_only,
            seed="unknown-db",
            config=player.DEFAULT_CONFIG,
        )
    assert invalid_db.value.code == "SESSION_RECOVERY_INVALID"
    assert unknown_db.read_bytes() == before_db
    assert not (db_only / player.SESSION_NAME).exists()

    metadata_only = tmp_path / "metadata-only"
    metadata_only.mkdir()
    metadata_path = metadata_only / player.SESSION_NAME
    metadata_path.write_bytes(b'{"preserve":"exactly"}\n')
    before_metadata = metadata_path.read_bytes()
    with pytest.raises(player.PlayerError) as invalid_metadata:
        player.controller_new(
            metadata_only,
            seed="metadata-only",
            config=player.DEFAULT_CONFIG,
        )
    assert invalid_metadata.value.code == "SESSION_RECOVERY_INVALID"
    assert metadata_path.read_bytes() == before_metadata
    assert not (metadata_only / player.DATABASE_NAME).exists()


def test_setup_receipt_is_unique_secret_system_state(base_session: Path) -> None:
    metadata = _metadata(base_session)
    with _engine(base_session)._db() as db:
        rows = db.execute(
            "SELECT summary,sensitivity,scope_type,payload_json FROM events "
            "WHERE campaign_id=? AND event_type=?",
            (metadata["campaign_id"], player.SETUP_EVENT_TYPE),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["summary"] == player.SETUP_EVENT_SUMMARY
    assert rows[0]["sensitivity"] == "SECRET"
    assert rows[0]["scope_type"] == "SYSTEM"
    payload = json.loads(rows[0]["payload_json"])
    assert payload["metadata"] == metadata
    assert len(payload["request_sha256"]) == 64
    assert player.SETUP_EVENT_SUMMARY not in _encoded(player.player_observe(base_session))


def test_observe_is_closed_bounded_and_actor_revision_coherent(session: Path) -> None:

    response = player.player_observe(session)
    metadata = _metadata(session)
    assert response["phase"] == "player"
    assert response["session"]["character_id"] == metadata["character_id"]
    observation = response["observation"]
    assert set(observation) <= set(player.PLAYER_OBSERVATION_FIELDS)
    assert observation["player"]["id"] == metadata["character_id"]
    assert observation["projection_sequence"] == observation["campaign"]["revision"]
    encoded = player._canonical(response)
    assert len(encoded) <= player.OUTPUT_CHAR_LIMIT
    for forbidden in (
        "context_packet",
        "capability_plan",
        "authoring",
        "world_bible",
        "raw_player_text",
        "beliefs_json",
    ):
        assert forbidden not in encoded
    for marker in _private_markers(session):
        assert marker not in encoded


def test_observe_advertises_and_enforces_player_time_advance_bounds(session: Path) -> None:
    observed = player.player_observe(session)
    constraint = observed["allowed_intents"]["constraints"]["advance_time"]
    assert constraint == {
        "required_parameters": ["minutes"],
        "minutes": {"type": "integer", "minimum": 0, "maximum": 1440},
        "server_forced": {"simulate": True},
        "ignored_player_overrides": ["weather", "season", "simulate"],
    }

    before = observed["observation"]
    revision = int(before["campaign"]["revision"])
    denied = player.player_act(
        session,
        text="I try to wait for a full year.",
        intents=[{"type": "advance_time", "parameters": {"minutes": 365 * 1440}}],
        expected_revision=revision,
        idempotency_key="headless-year-denied",
    )
    assert denied["turn"]["status"] == "failed"
    assert denied["turn"]["pbem"]["decisions"][0]["code"] == "PBEM_TIME_ADVANCE_OUT_OF_RANGE"
    assert denied["observation"]["campaign"]["revision"] == revision
    assert denied["observation"]["campaign"]["world_time"] == before["campaign"]["world_time"]

    allowed = player.player_act(
        session,
        text="I make camp until tomorrow.",
        intents=[
            {
                "type": "advance_time",
                "parameters": {
                    "minutes": 1440,
                    "simulate": False,
                    "weather": "player-forged",
                    "season": "player-forged",
                },
            }
        ],
        expected_revision=revision,
        idempotency_key="headless-day-allowed",
    )
    assert allowed["turn"]["status"] == "completed"
    assert allowed["turn"]["pbem"]["decisions"][0]["code"] == "PBEM_TIME_ADVANCE_ALLOWED"
    assert allowed["observation"]["campaign"]["revision"] > revision


def test_act_uses_raw_text_normalized_intent_pbem_and_exact_replay(session: Path) -> None:
    before = player.player_observe(session)["observation"]
    destination = _destination(before)
    revision = before["campaign"]["revision"]
    raw_text = "I take the marked road toward the next settlement."
    intent = {"intent_id": "travel", "type": "move", "parameters": {"destination": destination}}
    first = player.player_act(
        session,
        text=raw_text,
        intents=[intent],
        expected_revision=revision,
        idempotency_key="headless-move-1",
    )
    assert first["turn"]["pbem"]["enforced"] is True
    assert first["turn"]["pbem"]["decisions"][0]["decision"] == "allow"
    assert first["turn"]["status"] == "completed"
    assert first["observation"]["player"]["location_id"] == destination
    assert first["observation"]["campaign"]["revision"] > revision
    assert "context_packet" not in _encoded(first)

    replay = player.player_act(
        session,
        text=raw_text,
        intents=[intent],
        expected_revision=revision,
        idempotency_key="headless-move-1",
    )
    assert replay["turn"]["idempotent_replay"] is True
    assert replay["observation"]["campaign"]["revision"] == first["observation"]["campaign"]["revision"]

    metadata = _metadata(session)
    with _engine(session)._db() as db:
        row = db.execute(
            "SELECT raw_player_text,intents_json FROM we4_turn_records WHERE campaign_id=? AND turn_id=?",
            (metadata["campaign_id"], first["turn"]["turn_id"]),
        ).fetchone()
    assert row["raw_player_text"] == raw_text
    stored = json.loads(row["intents_json"])
    assert stored[0]["capability"] == "actor.move"
    assert stored[0]["parameters"] == {"destination": destination}
    for marker in _private_markers(session):
        assert marker not in _encoded(first)
        assert marker not in _encoded(replay)


@pytest.mark.parametrize(
    "intent",
    [
        {"type": "author", "parameters": {"action": "promote"}},
        {"type": "event", "parameters": {"event_type": "forged", "summary": "forged"}},
        {"type": "resources", "parameters": {"resource_delta": {"gold": 999}}},
        {"type": "attack", "parameters": {"attack_bonus": 99, "damage_expression": "99d99"}},
        {"type": "move", "capability": "world.event.commit", "parameters": {"destination": "x"}},
        {"type": "rules", "parameters": {"operation": "define_object", "payload": {"id": "forged"}}},
    ],
)
def test_admin_direct_event_and_unsafe_intents_reject_before_mutation(
    session: Path, intent: dict[str, Any]
) -> None:
    engine = _engine(session)
    metadata = _metadata(session)
    before = engine.get_campaign(metadata["campaign_id"])["revision"]
    with pytest.raises(player.PlayerError):
        player.player_act(
            session,
            text="Try an unsafe action.",
            intents=[intent],
            expected_revision=before,
            idempotency_key="unsafe-attempt",
        )
    reopened = _engine(session)
    assert reopened.get_campaign(metadata["campaign_id"])["revision"] == before
    with reopened._db() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM we4_turn_records WHERE campaign_id=?",
            (metadata["campaign_id"],),
        ).fetchone()[0] == 0


def test_stale_revision_and_idempotency_conflict_do_not_mutate(session: Path) -> None:
    observed = player.player_observe(session)["observation"]
    current = observed["campaign"]["revision"]
    same_location = observed["player"]["location_id"]
    intent = {"type": "move", "parameters": {"destination": same_location}}
    with pytest.raises(player.PlayerError) as stale:
        player.player_act(
            session,
            text="I wait where I am.",
            intents=[intent],
            expected_revision=max(0, current - 1),
            idempotency_key="stale-turn",
        )
    assert stale.value.code == "REVISION_CONFLICT"
    assert _engine(session).get_campaign(_metadata(session)["campaign_id"])["revision"] == current

    first = player.player_act(
        session,
        text="I remain here.",
        intents=[intent],
        expected_revision=current,
        idempotency_key="same-key",
    )
    after = first["observation"]["campaign"]["revision"]
    with pytest.raises(player.PlayerError) as conflict:
        player.player_act(
            session,
            text="Different text under the same key.",
            intents=[intent],
            expected_revision=current,
            idempotency_key="same-key",
        )
    assert conflict.value.code == "IDEMPOTENCY_KEY_CONFLICT"
    assert _engine(session).get_campaign(_metadata(session)["campaign_id"])["revision"] == after


def test_real_separate_process_observe_act_and_replay(session: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "headless_player_v511.py"

    def run(*args: str) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
        completed = subprocess.run(
            [sys.executable, str(script), *args],
            cwd=script.parents[1],
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        lines = completed.stdout.splitlines()
        assert len(lines) == 1, (completed.stdout, completed.stderr)
        assert len(lines[0]) <= player.OUTPUT_CHAR_LIMIT
        return completed, json.loads(lines[0])

    observed_process, observed = run("observe", "--session-dir", str(session))
    assert observed_process.returncode == 0, observed
    assert observed_process.stderr == ""
    revision = observed["observation"]["campaign"]["revision"]
    destination = _destination(observed["observation"])
    intent = json.dumps({"type": "move", "parameters": {"destination": destination}})
    args = (
        "act",
        "--session-dir",
        str(session),
        "--text",
        "I travel onward.",
        "--intent-json",
        intent,
        "--expected-revision",
        str(revision),
        "--idempotency-key",
        "subprocess-move",
    )
    first_process, first = run(*args)
    assert first_process.returncode == 0, first
    assert first["turn"]["pbem"]["enforced"] is True
    assert first["observation"]["player"]["location_id"] == destination
    replay_process, replay = run(*args)
    assert replay_process.returncode == 0, replay
    assert replay["turn"]["idempotent_replay"] is True
    for marker in _private_markers(session):
        assert marker not in _encoded(observed)
        assert marker not in _encoded(first)
        assert marker not in _encoded(replay)


def test_cli_errors_are_typed_bounded_json_without_exception_details(session: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "headless_player_v511.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "act",
            "--session-dir",
            str(session),
            "--text",
            "forged",
            "--intent-json",
            '{"type":"event","parameters":{"summary":"forged"}}',
            "--expected-revision",
            "0",
            "--idempotency-key",
            "forged",
        ],
        cwd=script.parents[1],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 2
    body = json.loads(completed.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "PLAYER_INTENT_NOT_ALLOWED"
    assert "Traceback" not in completed.stdout + completed.stderr
    assert len(completed.stdout) <= player.OUTPUT_CHAR_LIMIT
