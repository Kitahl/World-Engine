"""Long-horizon regression coverage for guarded routine-event rollups."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from world_engine import WorldEngine
from world_engine.agency import AgencyKernel
from world_engine.economy import EconomyKernel
from world_engine.incidents import IncidentKernel
from world_engine.quests import QuestRuntimeKernel
from world_engine.simulation import SimulationKernel


def test_benchmark_cli_requires_disposable_copy_acknowledgement(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_headless_horizon_v511.py"
    database = tmp_path / "must-not-be-created.sqlite3"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--database",
            str(database),
            "--campaign-id",
            "c",
            "--years",
            "1",
        ],
        cwd=script.parents[1],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 2
    assert "--confirm-disposable-copy is required" in completed.stderr
    assert not database.exists()


def _build_economy(path: Path) -> WorldEngine:
    engine = WorldEngine(path)
    engine.ensure_campaign("c", "Catch-up", "1492-01-01T00:00:00+00:00")
    engine.upsert_location("c", "town", "Town")
    engine.upsert_location("c", "farm", "Farm")
    engine.upsert_character("c", "hero", "Hero", location="town")
    engine.upsert_npc("c", "trigger", "economy_consumption", location="town")
    engine.save_item_def("c", "grain", "Grain", base_price=2)
    engine.save_resource_node(
        "c", "field", "farm", "grain", qty=100, qty_max=100, regen_per_day=0
    )
    economy = EconomyKernel(engine)
    economy.dispatch(
        "save_market",
        "c",
        {"market_id": "market", "location_id": "town", "name": "Market"},
    )
    economy.dispatch(
        "set_market_item",
        "c",
        {
            "market_id": "market",
            "item_id": "grain",
            "target_stock": 20,
            "demand_per_day": 1,
        },
    )
    economy.dispatch(
        "save_extractor",
        "c",
        {
            "extractor_id": "harvest",
            "location_id": "farm",
            "owner_kind": "location",
            "owner_id": "farm",
            "resource_node_id": "field",
            "units_per_day": 2,
        },
    )
    engine.set_inventory_item("c", "location", "town", "grain", 20)
    return engine


def _economy_state(engine: WorldEngine) -> dict[str, list[tuple[object, ...]]]:
    queries = {
        "resources": "SELECT id,ROUND(qty,9) FROM resource_nodes WHERE campaign_id='c' ORDER BY id",
        "inventory": "SELECT owner_kind,owner_id,item_id,ROUND(qty,9) FROM inventories WHERE campaign_id='c' ORDER BY owner_kind,owner_id,item_id",
        "market": "SELECT market_id,item_id,ROUND(demand_pressure,9),last_demand_world_time FROM economy_market_items WHERE campaign_id='c' ORDER BY market_id,item_id",
        "extractor": "SELECT id,last_processed_world_time FROM economy_extractors WHERE campaign_id='c' ORDER BY id",
    }
    with engine._db() as db:
        return {
            name: [tuple(row) for row in db.execute(sql).fetchall()]
            for name, sql in queries.items()
        }


def test_dynamic_quest_event_type_is_a_conservative_compaction_blocker() -> None:
    condition = {"event": {"event_type": "$bindings.expected_event_type"}}
    assert SimulationKernel._condition_observes_event_type(
        condition, "economy_consumption"
    )


def test_long_catchup_rolls_up_routine_economy_events_without_state_drift(tmp_path: Path) -> None:
    whole = _build_economy(tmp_path / "whole.sqlite3")
    chunked = _build_economy(tmp_path / "chunked.sqlite3")

    whole_result = whole.advance_world("c", 8 * 1440, "whole catch-up")
    chunked_results = [
        chunked.advance_world("c", 1440, "daily catch-up") for _ in range(8)
    ]

    assert _economy_state(whole) == _economy_state(chunked)
    assert whole_result["simulation"]["economy_consumption"] == sum(
        item["simulation"]["economy_consumption"] for item in chunked_results
    )
    assert whole_result["simulation"]["economy_extraction"] == sum(
        item["simulation"]["economy_extraction"] for item in chunked_results
    )

    with whole._db() as db:
        counts = {
            row["event_type"]: int(row["n"])
            for row in db.execute(
                "SELECT event_type,COUNT(*) n FROM events WHERE campaign_id='c' GROUP BY event_type"
            ).fetchall()
        }
        rollups = db.execute(
            "SELECT event_type,payload_json FROM events WHERE campaign_id='c' AND event_type LIKE '%_rollup' ORDER BY event_type"
        ).fetchall()
    assert counts.get("economy_consumption", 0) == 0
    assert counts.get("economy_resource_extracted", 0) == 0
    assert counts["economy_consumption_rollup"] == 1
    assert counts["economy_resource_extracted_rollup"] == 1
    payloads = {row["event_type"]: json.loads(row["payload_json"]) for row in rollups}
    assert payloads["economy_consumption_rollup"]["count"] == 8 * 24
    assert payloads["economy_resource_extracted_rollup"]["count"] == 8 * 24


def test_long_catchup_preserves_individual_events_when_a_reaction_depends_on_them(
    tmp_path: Path,
) -> None:
    engine = _build_economy(tmp_path / "reaction.sqlite3")
    engine.save_simulation_reaction(
        "c", "observe-consumption", "economy_consumption", []
    )
    engine.advance_world("c", 8 * 1440, "reaction-sensitive catch-up")

    with engine._db() as db:
        individual = int(
            db.execute(
                "SELECT COUNT(*) FROM events WHERE campaign_id='c' AND event_type='economy_consumption'"
            ).fetchone()[0]
        )
        rollups = int(
            db.execute(
                "SELECT COUNT(*) FROM events WHERE campaign_id='c' AND event_type='economy_consumption_rollup'"
            ).fetchone()[0]
        )
    assert individual == 8 * 24
    assert rollups == 0


def test_long_catchup_preserves_dynamic_quest_event_and_completes_quest(
    tmp_path: Path,
) -> None:
    engine = _build_economy(tmp_path / "quest.sqlite3")
    QuestRuntimeKernel(engine).bind_template(
        "c",
        {
            "template_id": "consume-quest",
            "visibility": "public",
            "bindings": {
                "owner": {"kind": "character"},
                "target": {"kind": "npc"},
                "place": {"kind": "location"},
            },
            "quest": {
                "id": "consume-quest",
                "title": "Observe the market",
                "owner_id": "$owner.id",
                "region": "$place.id",
                "objectives": ["Observe demand"],
            },
            "nodes": [
                {
                    "id": "observe",
                    "status": "active",
                    "success": {"event": {"event_type": "$target.name"}},
                    "state": {"terminal": True},
                }
            ],
            "edges": [],
        },
        {"owner": "character:hero", "target": "npc:trigger", "place": "location:town"},
        dry_run=False,
    )

    engine.advance_world("c", 8 * 1440, "quest-sensitive catch-up")
    with engine._db() as db:
        quest_status = db.execute(
            "SELECT status FROM quests WHERE campaign_id='c' AND id='consume-quest'"
        ).fetchone()[0]
        event_count = int(
            db.execute(
                "SELECT COUNT(*) FROM events WHERE campaign_id='c' AND event_type='economy_consumption'"
            ).fetchone()[0]
        )
        rollup_count = int(
            db.execute(
                "SELECT COUNT(*) FROM events WHERE campaign_id='c' AND event_type='economy_consumption_rollup'"
            ).fetchone()[0]
        )
    assert quest_status == "completed"
    assert event_count == 8 * 24
    assert rollup_count == 0


def test_long_catchup_preserves_goal_events_and_creates_agency_memory(
    tmp_path: Path,
) -> None:
    engine = _build_economy(tmp_path / "agency.sqlite3")
    AgencyKernel(engine).save_goal(
        "c",
        "market-goal",
        "npc",
        "trigger",
        {"success_event_types": ["economy_consumption"]},
    )

    engine.advance_world("c", 8 * 1440, "agency-sensitive catch-up")
    with engine._db() as db:
        memories = int(
            db.execute(
                "SELECT COUNT(*) FROM agency_memories WHERE campaign_id='c' AND actor_id='trigger'"
            ).fetchone()[0]
        )
        event_count = int(
            db.execute(
                "SELECT COUNT(*) FROM events WHERE campaign_id='c' AND event_type='economy_consumption'"
            ).fetchone()[0]
        )
        rollup_count = int(
            db.execute(
                "SELECT COUNT(*) FROM events WHERE campaign_id='c' AND event_type='economy_consumption_rollup'"
            ).fetchone()[0]
        )
    assert memories > 0
    assert event_count == 8 * 24
    assert rollup_count == 0


def test_long_catchup_preserves_events_used_by_incident_suppression(
    tmp_path: Path,
) -> None:
    engine = _build_economy(tmp_path / "incident.sqlite3")
    incidents = IncidentKernel(engine)
    incidents.save_definition(
        "c",
        "consumption-suppression",
        "economic",
        "economy_consumption",
        "Market demand changed at {scope_id}",
        suppression_minutes=30 * 24 * 60,
        cooldown_minutes=0,
    )

    engine.advance_world("c", 8 * 1440, "incident-sensitive catch-up")
    with engine._db() as db:
        campaign_time = db.execute(
            "SELECT world_time FROM campaigns WHERE id='c'"
        ).fetchone()[0]
        candidates = incidents.candidates_db(
            db,
            "c",
            datetime.fromisoformat(campaign_time),
        )
        event_count = int(
            db.execute(
                "SELECT COUNT(*) FROM events WHERE campaign_id='c' AND event_type='economy_consumption'"
            ).fetchone()[0]
        )
        rollup_count = int(
            db.execute(
                "SELECT COUNT(*) FROM events WHERE campaign_id='c' AND event_type='economy_consumption_rollup'"
            ).fetchone()[0]
        )
        incident_count = int(
            db.execute(
                "SELECT COUNT(*) FROM incident_instances WHERE campaign_id='c' AND definition_id='consumption-suppression'"
            ).fetchone()[0]
        )
    assert candidates == []
    # Town's 192 hourly demand events suppress Town throughout. Farm has no
    # market event, so it selects once; that incident's same-typed event then
    # suppresses subsequent Farm candidates.
    assert event_count == 8 * 24 + 1
    assert incident_count == 1
    assert rollup_count == 0
