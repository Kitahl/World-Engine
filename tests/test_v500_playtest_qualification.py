"""Release-qualification play sessions for the World Engine 5.0 runtime.

These are deliberately campaign-shaped tests.  Each scenario generates and promotes
one WEGEN-2.0 world, routes player turns, lets several simulated days pass, and reads
the same player-facing projections a local companion would consume.

Scenario ``ordinary``
    A player speaks with a generated contact, records the observed interaction,
    travels to the generated quest destination, completes the quest, receives a
    narrative presentation, and reopens the campaign.  Acceptance requires explicit
    authoring gates, successful routed/idempotent turns, an executable generated
    quest and agency plan, useful player context, a published companion envelope,
    persisted state, and zero disclosure of planted private sentinels.

Scenario ``adverse``
    A generated settlement loses its provisions while a forced storm and drought
    coincide with generated territorial claims and grievances.  Acceptance requires
    economy, population, environment, politics, agency, incidents, and quests to
    remain readable after three days; one-shot and daily-chunk play must converge on
    the same normalized state; and a reopened database must preserve that state.

Scenario ``public-turn integrity regressions``
    A player attempts a forged consequence event and remote dialogue, repeats a
    move to the current location, makes an unrelated check while a quest is active,
    and speaks after more than one quest-event batch has accumulated.  Acceptance
    requires PBEM denials to preserve movement and quest state, no-op and idle quest
    synchronization to preserve revisions, and bounded catch-up to reach the current
    dialogue without a stale-backlog warning or false quest transition.

The tests use temporary SQLite databases only.  They make no network, browser, GUI,
wall-clock, or external-model assumptions.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from world_engine import WorldEngine
from world_engine.agency import AgencyKernel
from world_engine.desktop import DESKTOP_PROJECTION_VERSION, DesktopProjectionKernel
from world_engine.quests import QuestRuntimeKernel

CAMPAIGN_ID = "playtest"
NAMESPACE = "qual"
SEED = "v500-release-playtest"
CONFIG = {
    "location_count": 3,
    "faction_count": 2,
    "npcs_per_faction": 1,
    "resource_count": 2,
    "quest_count": 1,
}
PRIVATE_EVENT = "PRIVATE_EVENT_SENTINEL_7D31"
PRIVATE_NPC = "PRIVATE_NPC_SENTINEL_4A92"
PROSE = (
    "Rain rattles the shutters while the guide studies an empty harness hook beside "
    "the stable door. She folds the road map once, sets it between two cups, and "
    "points toward the next settlement. Outside, a horse stamps in the wet yard. "
    "The eastern track remains open, but no wagon has crossed it since dusk. The "
    "guide asks for tracks, names, or survivors and waits beside the lamp."
)


def _promote_generated_world(path: Path) -> tuple[WorldEngine, dict[str, Any]]:
    engine = WorldEngine(path)
    engine.ensure_campaign(CAMPAIGN_ID, "V5 release playtest", "1492-01-01T08:00:00+00:00")
    engine.set_simulation_seed(CAMPAIGN_ID, 50_005)
    before = engine.get_campaign(CAMPAIGN_ID)["revision"]
    staged = engine.stage_generated_world(
        CAMPAIGN_ID,
        "release_world",
        SEED,
        CONFIG,
        namespace=NAMESPACE,
        expected_revision=before,
    )
    assert staged["generation"]["contract_version"] == "WEGEN-2.0"
    validation = engine.author_validate(CAMPAIGN_ID, "release_world")
    assert validation["valid"], validation
    dry_run = engine.author_dry_run(CAMPAIGN_ID, "release_world", days=1)
    assert dry_run["passed"], dry_run
    assert any(
        check["name"] == "runtime_records_installed" and check["passed"]
        for check in dry_run["checks"]
    )
    promoted = engine.author_promote(CAMPAIGN_ID, "release_world")
    assert promoted["status"] == "promoted"
    return engine, staged["generation"]["payload"]


def _ids(payload: dict[str, Any]) -> dict[str, str]:
    runtime = payload["_generation"]["runtime"]
    quest = runtime["quest_templates"][0]
    return {
        "hero": payload["characters"][0]["id"],
        "start": payload["characters"][0]["location"],
        "contact": quest["bindings"]["contact"]["default"].split(":", 1)[1],
        "destination": quest["bindings"]["place"]["default"].split(":", 1)[1],
        "quest": quest["quest"]["id"],
        "goal": runtime["agency_goals"][0]["id"],
        "provisions": next(
            item["id"] for item in payload["items"] if "provisions" in item["id"]
        ),
    }


def _encoded(*values: Any) -> str:
    return json.dumps(values, sort_keys=True, ensure_ascii=False)


def _assert_player_safe(*values: Any) -> None:
    public_text = _encoded(*values)
    assert PRIVATE_EVENT not in public_text
    assert PRIVATE_NPC not in public_text


def _quest_status(engine: WorldEngine, quest_id: str) -> tuple[str, list[tuple[str, str]]]:
    with engine._db() as db:
        quest = db.execute(
            "SELECT status FROM quests WHERE campaign_id=? AND id=?",
            (CAMPAIGN_ID, quest_id),
        ).fetchone()
        nodes = db.execute(
            "SELECT id,status FROM quest_nodes WHERE campaign_id=? AND quest_id=? ORDER BY id",
            (CAMPAIGN_ID, quest_id),
        ).fetchall()
    return str(quest["status"]), [(str(row["id"]), str(row["status"])) for row in nodes]


def _event_count(engine: WorldEngine, event_type: str) -> int:
    with engine._db() as db:
        return int(
            db.execute(
                "SELECT COUNT(*) FROM events WHERE campaign_id=? AND event_type=?",
                (CAMPAIGN_ID, event_type),
            ).fetchone()[0]
        )


def _quest_cursor(engine: WorldEngine) -> int:
    with engine._db() as db:
        row = db.execute(
            "SELECT last_event_id FROM quest_event_cursors WHERE campaign_id=?",
            (CAMPAIGN_ID,),
        ).fetchone()
    return int(row["last_event_id"]) if row else 0


def _normalized_adverse_state(engine: WorldEngine, ids: dict[str, str]) -> dict[str, Any]:
    """Return state expected to be invariant to catch-up chunking.

    Revisions and per-call rollup events are intentionally excluded: they describe
    request packaging, not simulated world state.
    """
    desktop = DesktopProjectionKernel(engine, CAMPAIGN_ID, ids["hero"]).snapshot()
    with engine._db() as db:
        inventories = [
            (str(row["owner_id"]), str(row["item_id"]), round(float(row["qty"]), 8))
            for row in db.execute(
                "SELECT owner_id,item_id,qty FROM inventories WHERE campaign_id=? "
                "AND owner_kind='location' ORDER BY owner_id,item_id",
                (CAMPAIGN_ID,),
            ).fetchall()
        ]
        cohorts = [
            (str(row["id"]), str(row["location_id"]), round(float(row["count"]), 8))
            for row in db.execute(
                "SELECT id,location_id,count FROM population_cohorts "
                "WHERE campaign_id=? ORDER BY id",
                (CAMPAIGN_ID,),
            ).fetchall()
        ]
        incidents = [
            (
                str(row["definition_id"]),
                str(row["scope_id"]),
                str(row["status"]),
                str(row["selected_world_time"]),
            )
            for row in db.execute(
                "SELECT definition_id,scope_id,status,selected_world_time "
                "FROM incident_instances WHERE campaign_id=? "
                "ORDER BY selected_world_time,definition_id,scope_id",
                (CAMPAIGN_ID,),
            ).fetchall()
        ]
        weather = [
            (
                str(row["scope_id"]),
                str(row["condition"]),
                round(float(row["severity"]), 8),
                round(float(row["temperature_c"]), 8),
            )
            for row in db.execute(
                "SELECT scope_id,condition,severity,temperature_c "
                "FROM environment_weather WHERE campaign_id=? ORDER BY scope_type,scope_id",
                (CAMPAIGN_ID,),
            ).fetchall()
        ]
    return {
        "world_time": engine.get_campaign(CAMPAIGN_ID)["world_time"],
        "hero": engine.get_character(CAMPAIGN_ID, ids["hero"]),
        "inventories": inventories,
        "cohorts": cohorts,
        "incidents": incidents,
        "weather": weather,
        "environment": desktop["environment"],
        "economy": desktop["economy"],
        "population": desktop["population"],
        "politics": desktop["politics"],
        "agency": desktop["agency"],
        "executable_quests": desktop["executable_quests"],
    }


def test_ordinary_generated_campaign_plays_to_publication_and_reopen(tmp_path: Path) -> None:
    """Ordinary campaign acceptance: generate -> act -> quest -> publish -> reopen.

    The generated content must pass every authoring gate, expose a playable character
    and contact, accept replay-safe player turns, execute its quest and an NPC agency
    plan, compile coherent bounded context, publish reviewed narration to the desktop
    outbox, and preserve all public results after reopening without leaking either
    planted private sentinel.
    """
    path = tmp_path / "ordinary.sqlite3"
    engine, payload = _promote_generated_world(path)
    ids = _ids(payload)

    engine.commit_event(
        CAMPAIGN_ID,
        "gm_pressure_note",
        PRIVATE_EVENT,
        sensitivity="SECRET",
        scope_type="GM",
    )
    engine.update_npc_state(
        CAMPAIGN_ID,
        ids["contact"],
        add_beliefs=[PRIVATE_NPC],
        reason="private playtest belief",
    )

    talk = engine.resolve_turn(
        CAMPAIGN_ID,
        actor_kind="character",
        actor_id=ids["hero"],
        raw_player_text="I ask the local guide about the road ahead.",
        intents=[
            {
                "intent_id": "talk",
                "type": "interact",
                "parameters": {"npc_id": ids["contact"], "topic": "road"},
            }
        ],
        idempotency_key="ordinary-contact-turn",
        enforce_pbem=True,
    )
    talk_replay = engine.resolve_turn(
        CAMPAIGN_ID,
        actor_kind="character",
        actor_id=ids["hero"],
        raw_player_text="I ask the local guide about the road ahead.",
        intents=[
            {
                "intent_id": "talk",
                "type": "interact",
                "parameters": {"npc_id": ids["contact"], "topic": "road"},
            }
        ],
        idempotency_key="ordinary-contact-turn",
        enforce_pbem=True,
    )
    assert talk["status"] == "completed"
    assert talk_replay["idempotent_replay"] is True
    assert talk_replay["turn_id"] == talk["turn_id"]

    assert dict(_quest_status(engine, ids["quest"])[1])["contact"] == "completed"
    travel = engine.resolve_turn(
        CAMPAIGN_ID,
        actor_kind="character",
        actor_id=ids["hero"],
        raw_player_text="I follow the road to the next settlement.",
        intents=[
            {
                "intent_id": "travel",
                "type": "move",
                "parameters": {"destination": ids["destination"]},
            }
        ],
        idempotency_key="ordinary-travel-turn",
        enforce_pbem=True,
    )
    assert travel["status"] == "completed"
    assert _quest_status(engine, ids["quest"])[0] == "completed"

    plan = AgencyKernel(engine).create_plan(CAMPAIGN_ID, ids["goal"])
    plan_step = AgencyKernel(engine).execute_next_step(CAMPAIGN_ID, plan["id"])
    assert plan_step["status"] in {"advanced", "completed"}

    for day in range(3):
        result = engine.advance_world(
            CAMPAIGN_ID, 24 * 60, reason=f"ordinary play day {day + 1}"
        )
        assert result["simulation"]

    context = engine.compile_turn_context(
        CAMPAIGN_ID,
        actor_kind="character",
        actor_id=ids["hero"],
        location_id=ids["destination"],
        intents=[{"type": "interact", "parameters": {"npc_id": ids["contact"]}}],
        max_chars=16_000,
    )
    assert context["context"]["HOT"]
    assert context["budget"]["used_chars"] > 100
    assert context["budget"]["used_chars"] <= 16_000
    assert context["compile_hash"]

    choices = ["Inspect the road map", "Ask about the missing wagons"]
    engine.configure_narrative(
        CAMPAIGN_ID,
        quality_config={"semantic_authority_review_required": False},
    )
    packet = engine.build_narrative_packet(
        CAMPAIGN_ID,
        turn_result=talk,
        task="dialogue",
        actor_kind="character",
        actor_id=ids["hero"],
        intents=[{"type": "interact", "parameters": {"npc_id": ids["contact"]}}],
        raw_player_text="I ask the local guide about the road ahead.",
        choice_options=choices,
        mode_override="enforce",
    )
    publication = engine.publish_presentation(
        campaign_id=CAMPAIGN_ID,
        presentation_id="ordinary-presentation",
        packet_id=packet["packet_id"],
        narration=PROSE,
        expected_revision=packet["authority"]["authoritative_state"]["campaign"]["revision"],
        turn_id=packet["turn_id"],
        choices=choices,
        presentation={},
    )
    assert publication["status"] == "accepted"
    assert publication["replayed"] is False

    desktop = DesktopProjectionKernel(engine, CAMPAIGN_ID, ids["hero"]).snapshot()
    assert desktop["schema"] == DESKTOP_PROJECTION_VERSION
    assert desktop["mode"] == "STORY"
    assert desktop["player"]["id"] == ids["hero"]
    assert desktop["location"]["id"] == ids["destination"]
    assert desktop["presentation"]["narration"] == PROSE
    assert desktop["journal"]["accepted_presentation_id"] == "ordinary-presentation"
    assert desktop["world_map"]["locations"]
    assert desktop["population"]["settlement"]
    assert desktop["economy"]["markets"]
    assert desktop["politics"]["territorial_control"]
    assert desktop["agency"] is not None
    _assert_player_safe(talk, travel, context, packet, publication, desktop)

    persisted_time = engine.get_campaign(CAMPAIGN_ID)["world_time"]
    del engine
    reopened = WorldEngine(path)
    reopened_desktop = DesktopProjectionKernel(reopened, CAMPAIGN_ID, ids["hero"]).snapshot()
    assert reopened.get_campaign(CAMPAIGN_ID)["world_time"] == persisted_time
    assert reopened.get_character(CAMPAIGN_ID, ids["hero"])["location"] == ids["destination"]
    assert _quest_status(reopened, ids["quest"])[0] == "completed"
    assert reopened_desktop["presentation"]["presentation_id"] == "ordinary-presentation"
    _assert_player_safe(reopened_desktop)


def test_adverse_campaign_converges_across_time_chunks_and_reopens(tmp_path: Path) -> None:
    """Adverse campaign acceptance: shortage + storm + politics over three days.

    A zero-provisions settlement under deterministic storm and drought must still
    expose coherent public environment/economy/population/politics/agency/incident/
    quest state. Advancing 72 hours once or in three daily turns must yield identical
    normalized world state, and reopening must not alter that state or reveal secrets.
    """
    baseline_path = tmp_path / "adverse-baseline.sqlite3"
    baseline, payload = _promote_generated_world(baseline_path)
    ids = _ids(payload)
    for location in payload["locations"]:
        baseline.set_inventory_item(
            CAMPAIGN_ID, "location", location["id"], ids["provisions"], 0
        )
    baseline.world_systems_dispatch(
        "set_climate",
        CAMPAIGN_ID,
        {
            "scope_type": "location",
            "scope_id": ids["start"],
            "climate": "temperate",
            "weather_weights": {"storm": 1.0},
            "state": {"auto_weather": True, "actor_exposure": True},
        },
    )
    baseline.environment_dispatch(
        "apply_effect",
        CAMPAIGN_ID,
        {
            "effect_type": "drought",
            "target": {"type": "location", "id": ids["start"]},
            "intensity": 0.85,
            "amount": 4.0,
        },
    )
    baseline.commit_event(
        CAMPAIGN_ID,
        "gm_adversity_note",
        PRIVATE_EVENT,
        sensitivity="SECRET",
        scope_type="GM",
    )
    del baseline

    whole_path = tmp_path / "adverse-whole.sqlite3"
    chunked_path = tmp_path / "adverse-chunked.sqlite3"
    shutil.copy2(baseline_path, whole_path)
    shutil.copy2(baseline_path, chunked_path)
    whole = WorldEngine(whole_path)
    chunked = WorldEngine(chunked_path)

    whole_result = whole.advance_world(
        CAMPAIGN_ID, 3 * 24 * 60, reason="three-day adverse campaign"
    )
    chunk_results = [
        chunked.advance_world(
            CAMPAIGN_ID, 24 * 60, reason=f"adverse campaign day {day}"
        )
        for day in range(1, 4)
    ]
    assert whole_result["simulation"]
    assert all(result["simulation"] for result in chunk_results)

    whole_state = _normalized_adverse_state(whole, ids)
    chunked_state = _normalized_adverse_state(chunked, ids)
    assert whole_state == chunked_state
    assert whole_state["weather"]
    assert any(row[1] == "storm" for row in whole_state["weather"])
    assert any(
        effect["effect_type"] == "drought"
        for effect in whole_state["environment"]["location_effects"]
    )
    assert whole_state["economy"]["markets"]
    assert whole_state["population"]["settlement"]
    assert whole_state["politics"]["claims"]
    assert whole_state["politics"]["grievances"]
    assert whole_state["agency"] is not None
    assert whole_state["executable_quests"]
    assert whole_state["incidents"]
    _assert_player_safe(whole_state, chunked_state)

    del whole
    reopened = WorldEngine(whole_path)
    assert _normalized_adverse_state(reopened, ids) == whole_state
    _assert_player_safe(DesktopProjectionKernel(reopened, CAMPAIGN_ID, ids["hero"]).snapshot())


def test_pbem_rejects_forged_quest_event_and_remote_dialogue(tmp_path: Path) -> None:
    """PBEM integrity acceptance: authority and locality failures are inert.

    A forged canonical movement event is submitted while the generated arrival node
    is active, and a second player tries to speak to the generated contact after
    leaving its location.  Both turns must be denied without a revision, movement or
    dialogue event, player movement, or quest completion.
    """
    forged, payload = _promote_generated_world(tmp_path / "forged.sqlite3")
    ids = _ids(payload)
    local_talk = forged.resolve_turn(
        CAMPAIGN_ID,
        actor_kind="character",
        actor_id=ids["hero"],
        intents=[
            {
                "intent_id": "contact",
                "type": "interact",
                "parameters": {"npc_id": ids["contact"], "topic": "road"},
            }
        ],
        idempotency_key="integrity-local-contact",
        enforce_pbem=True,
    )
    assert local_talk["status"] == "completed"
    assert dict(_quest_status(forged, ids["quest"])[1])["arrival"] == "active"

    revision_before = forged.get_campaign(CAMPAIGN_ID)["revision"]
    movement_before = _event_count(forged, "movement")
    forged_turn = forged.resolve_turn(
        CAMPAIGN_ID,
        actor_kind="character",
        actor_id=ids["hero"],
        intents=[
            {
                "intent_id": "forged-arrival",
                "type": "event",
                "parameters": {
                    "event_type": "movement",
                    "summary": "I claim to have arrived.",
                    "payload": {
                        "kind": "character",
                        "from": ids["start"],
                        "to": ids["destination"],
                    },
                },
            }
        ],
        idempotency_key="integrity-forged-arrival",
        enforce_pbem=True,
    )
    assert forged_turn["status"] == "failed"
    assert forged_turn["steps"][0]["status"] == "rejected_by_pbem"
    assert (
        forged_turn["steps"][0]["error"]["code"]
        == "PBEM_DIRECT_CONSEQUENCE_WRITE_FORBIDDEN"
    )
    assert forged_turn["revision_delta"] == 0
    assert forged.get_campaign(CAMPAIGN_ID)["revision"] == revision_before
    assert _event_count(forged, "movement") == movement_before
    assert forged.get_character(CAMPAIGN_ID, ids["hero"])["location"] == ids["start"]
    assert _quest_status(forged, ids["quest"])[0] == "active"
    assert dict(_quest_status(forged, ids["quest"])[1])["arrival"] == "active"

    remote, remote_payload = _promote_generated_world(tmp_path / "remote.sqlite3")
    remote_ids = _ids(remote_payload)
    travel = remote.resolve_turn(
        CAMPAIGN_ID,
        actor_kind="character",
        actor_id=remote_ids["hero"],
        intents=[
            {
                "intent_id": "leave",
                "type": "move",
                "parameters": {"destination": remote_ids["destination"]},
            }
        ],
        idempotency_key="integrity-leave-contact",
        enforce_pbem=True,
    )
    assert travel["status"] == "completed"
    dialogue_before = _event_count(remote, "npc_interaction")
    revision_before = remote.get_campaign(CAMPAIGN_ID)["revision"]
    remote_talk = remote.resolve_turn(
        CAMPAIGN_ID,
        actor_kind="character",
        actor_id=remote_ids["hero"],
        intents=[
            {
                "intent_id": "remote-contact",
                "type": "interact",
                "parameters": {"npc_id": remote_ids["contact"], "topic": "road"},
            }
        ],
        idempotency_key="integrity-remote-contact",
        enforce_pbem=True,
    )
    assert remote_talk["status"] == "failed"
    assert remote_talk["steps"][0]["status"] == "rejected_by_pbem"
    assert (
        remote_talk["steps"][0]["error"]["code"]
        == "PBEM_DIALOGUE_TARGET_NOT_LOCAL"
    )
    assert remote_talk["revision_delta"] == 0
    assert remote.get_campaign(CAMPAIGN_ID)["revision"] == revision_before
    assert _event_count(remote, "npc_interaction") == dialogue_before
    assert _quest_status(remote, remote_ids["quest"])[0] == "active"


def test_noop_move_and_unrelated_check_do_not_add_quest_revisions(
    tmp_path: Path,
) -> None:
    """Revision acceptance: inert movement and unrelated checks have exact costs.

    Moving to the current location must emit neither a movement event nor a campaign
    revision.  Once the active generated quest is caught up, a routed ability check
    must cost exactly its own revision: quest synchronization must not create an
    additional revision or a false node transition.
    """
    engine, payload = _promote_generated_world(tmp_path / "revision-integrity.sqlite3")
    ids = _ids(payload)

    revision_before = engine.get_campaign(CAMPAIGN_ID)["revision"]
    movement_before = _event_count(engine, "movement")
    noop = engine.resolve_turn(
        CAMPAIGN_ID,
        actor_kind="character",
        actor_id=ids["hero"],
        intents=[
            {
                "intent_id": "stay",
                "type": "move",
                "parameters": {"destination": ids["start"]},
            }
        ],
        idempotency_key="integrity-noop-move",
        enforce_pbem=True,
    )
    assert noop["status"] == "completed"
    assert noop["steps"][0]["revision_delta"] == 0
    assert noop["revision_delta"] == 0
    assert engine.get_campaign(CAMPAIGN_ID)["revision"] == revision_before
    assert _event_count(engine, "movement") == movement_before

    QuestRuntimeKernel(engine).step_if_active(CAMPAIGN_ID)
    quest_before = _quest_status(engine, ids["quest"])
    revision_before = engine.get_campaign(CAMPAIGN_ID)["revision"]
    check = engine.resolve_turn(
        CAMPAIGN_ID,
        actor_kind="character",
        actor_id=ids["hero"],
        intents=[
            {
                "intent_id": "look-around",
                "type": "check",
                "parameters": {"modifier": 0, "dc": 10},
            }
        ],
        idempotency_key="integrity-unrelated-check",
        enforce_pbem=True,
    )
    assert check["status"] == "completed"
    assert check["steps"][0]["revision_delta"] == 1
    assert check["revision_delta"] == 1
    assert engine.get_campaign(CAMPAIGN_ID)["revision"] == revision_before + 1
    assert _quest_status(engine, ids["quest"]) == quest_before


def test_turn_quest_sync_drains_backlog_through_current_dialogue(tmp_path: Path) -> None:
    """Catch-up acceptance: a current action survives a multi-batch backlog.

    After the active quest cursor is current, 257 unrelated events are committed and
    the player speaks to the local generated contact.  The turn must drain through
    the dialogue event, transition the contact node, and return without claiming a
    quest-runtime backlog remains.
    """
    engine, payload = _promote_generated_world(tmp_path / "backlog.sqlite3")
    ids = _ids(payload)
    QuestRuntimeKernel(engine).step_if_active(CAMPAIGN_ID)
    for index in range(257):
        engine.commit_event(
            CAMPAIGN_ID,
            "ambient_observation",
            f"Ambient backlog observation {index}.",
            payload={"index": index},
        )

    talk = engine.resolve_turn(
        CAMPAIGN_ID,
        actor_kind="character",
        actor_id=ids["hero"],
        intents=[
            {
                "intent_id": "current-dialogue",
                "type": "interact",
                "parameters": {"npc_id": ids["contact"], "topic": "road"},
            }
        ],
        idempotency_key="integrity-dialogue-after-backlog",
        enforce_pbem=True,
    )
    assert talk["status"] == "completed"
    dialogue_event_id = int(talk["steps"][0]["result"]["event_id"])
    assert _quest_cursor(engine) >= dialogue_event_id
    assert dict(_quest_status(engine, ids["quest"])[1])["contact"] == "completed"
    warning_codes = {
        str(warning.get("code")) for warning in talk.get("runtime_warnings", [])
    }
    assert "QUEST_RUNTIME_BACKLOG_REMAINS" not in warning_codes
