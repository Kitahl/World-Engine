"""Bounded post-release stress qualification for World Engine 5.0.

Predeclared workloads and pass criteria
---------------------------------------
* SQLite concurrency: 48 calls (16 logical purchases, each delivered three
  times) through 12 independent ``WorldEngine`` connections.  Pass means all
  calls complete, every replay for a logical key is identical, exactly 16
  transactions commit, stock/holdings and money are conserved, SQLite reports
  no integrity/FK errors, wall time is below 30 s, peak traced memory below
  256 MiB, and the temporary database family remains below 16 MiB.
* Populated scheduler equivalence: the same 72 hours are advanced once and in
  three daily chunks after enabling environment, economy, population,
  politics, agency, incidents, quests, and the legacy simulation rules.  Pass
  means each domain demonstrably runs, state-affecting tallies and normalized
  durable state are identical, integrity/FKs are clean, each run is below 30 s,
  peak traced memory below 256 MiB, and each database family is below 32 MiB.
  Request-shape-dependent observation counters are measured and reported.
* Event/quest pressure: 250 simultaneously completable terminal quests produce
  500 transitions (within the configured 512-transition step cap), followed by
  300 inert events drained as 256 + 44 (the configured event-step cap).  Pass
  means exact counts, an idempotent zero-transition replay, clean integrity/FKs,
  wall time below 60 s, peak traced memory below 512 MiB, and DB size below
  64 MiB.
* Actor/reopen pressure: 270 active agency actors exceed the configured 256
  actor scheduler cap and the environment's 200-NPC auto-binding cap.  Pass
  means exactly those bounded counts are processed/materialized, six fresh
  reopen cycles retain stable counts, schema version 24, clean integrity/FKs,
  wall time below 60 s, peak traced memory below 512 MiB, and DB size below
  64 MiB.
* Constructor pressure: 12 threads reopen one initialized DB and eight sibling
  processes initialize one fresh DB. Pass means zero constructor failures and
  a clean schema-24 database after both workloads.

All databases live under unittest temporary directories; no network or
external service is used.  Timing limits are regression tripwires, not product
throughput claims.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
import unittest
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import Any, TypeVar

from world_engine import WorldEngine
from world_engine.agency import MAX_ACTIVE_ACTORS, AgencyKernel
from world_engine.companion import CompanionMigrationError
from world_engine.economy import EconomyKernel
from world_engine.incidents import IncidentKernel
from world_engine.mechanisms import MechanismKernel
from world_engine.politics import PoliticsKernel
from world_engine.quests import (
    MAX_EVENTS_PER_STEP,
    MAX_TRANSITIONS_PER_STEP,
    QuestRuntimeKernel,
)
from world_engine.turn_router import TurnRouter

T = TypeVar("T")
MIB = 1024 * 1024
ENVIRONMENT_NPCS_PER_LOCATION_CAP = 200


class WorldEngineV500StressQualification(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _database_family_bytes(path: Path) -> int:
        return sum(
            candidate.stat().st_size
            for candidate in path.parent.glob(path.name + "*")
            if candidate.is_file()
        )

    def _measured(
        self,
        label: str,
        db_path: Path,
        operation: Callable[[], T],
        *,
        max_seconds: float,
        max_peak_mib: float,
        max_db_mib: float,
    ) -> T:
        tracemalloc.start()
        started = time.perf_counter()
        try:
            result = operation()
            elapsed = time.perf_counter() - started
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        db_bytes = self._database_family_bytes(db_path)
        print(
            "STRESS_METRIC "
            f"label={label} wall_s={elapsed:.6f} "
            f"peak_mib={peak / MIB:.3f} db_mib={db_bytes / MIB:.3f}"
        )
        self.assertLess(elapsed, max_seconds, f"{label} exceeded wall-time budget")
        self.assertLess(peak / MIB, max_peak_mib, f"{label} exceeded memory budget")
        self.assertLess(db_bytes / MIB, max_db_mib, f"{label} exceeded DB-size budget")
        return result

    def _assert_sqlite_clean(self, path: Path) -> None:
        with closing(sqlite3.connect(path)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            self.assertEqual("ok", db.execute("PRAGMA integrity_check").fetchone()[0])
            self.assertEqual([], db.execute("PRAGMA foreign_key_check").fetchall())

    @staticmethod
    def _set_balance(
        engine: WorldEngine, campaign_id: str, owner_kind: str, owner_id: str, amount: float
    ) -> None:
        with engine._write_db() as db:
            db.execute(
                """INSERT INTO owner_balances(
                       campaign_id,owner_kind,owner_id,currency_key,amount,updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,owner_kind,owner_id,currency_key)
                   DO UPDATE SET amount=excluded.amount,updated_at=excluded.updated_at""",
                (campaign_id, owner_kind, owner_id, "gp", amount, engine._now()),
            )

    def test_concurrent_writers_preserve_idempotency_and_conservation(self) -> None:
        path = self.root / "concurrent.sqlite3"
        engine = WorldEngine(path)
        engine.ensure_campaign("c", "Concurrency", "2020-01-01T00:00:00+00:00")
        engine.upsert_location("c", "town", "Town")
        engine.upsert_character("c", "hero", "Hero", location="town")
        engine.save_item_def("c", "bread", "Bread", base_price=1)
        economy = EconomyKernel(engine)
        economy.dispatch(
            "save_market",
            "c",
            {"market_id": "shop", "location_id": "town", "name": "Shop"},
        )
        economy.dispatch(
            "set_market_item",
            "c",
            {"market_id": "shop", "item_id": "bread", "target_stock": 16},
        )
        engine.set_inventory_item("c", "location", "town", "bread", 16)
        self._set_balance(engine, "c", "character", "hero", 100)
        self._set_balance(engine, "c", "location", "town", 0)

        tasks = [key for key in range(16) for _duplicate in range(3)]
        # This deterministic permutation maximizes overlap between repeated keys.
        tasks = tasks[::2] + tasks[1::2]
        workers = [EconomyKernel(WorldEngine(path)) for _ in range(12)]

        def run() -> list[tuple[int, dict[str, Any]]]:
            def purchase_batch(slot: int) -> list[tuple[int, dict[str, Any]]]:
                results = []
                for key in tasks[slot::12]:
                    result = workers[slot].interact(
                        "c",
                        action="buy",
                        actor_kind="character",
                        actor_id="hero",
                        market_id="shop",
                        item_id="bread",
                        qty=1,
                        transaction_key=f"purchase-{key:02d}",
                    )
                    results.append((key, result))
                return results

            with ThreadPoolExecutor(max_workers=12) as pool:
                batches = list(pool.map(purchase_batch, range(12)))
            return [item for batch in batches for item in batch]

        outcomes = self._measured(
            "concurrent-idempotency",
            path,
            run,
            max_seconds=30,
            max_peak_mib=256,
            max_db_mib=16,
        )
        by_key: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for key, result in outcomes:
            by_key[key].append(result)
        self.assertEqual(set(range(16)), set(by_key))
        for results in by_key.values():
            self.assertEqual(3, len(results))
            self.assertTrue(all(item == results[0] for item in results[1:]))

        with engine._db() as db:
            tx_count = db.execute(
                "SELECT COUNT(*) FROM economy_transactions WHERE campaign_id='c'"
            ).fetchone()[0]
            stock = db.execute(
                """SELECT qty FROM inventories WHERE campaign_id='c'
                   AND owner_kind='location' AND owner_id='town' AND item_id='bread'"""
            ).fetchone()[0]
            holdings = db.execute(
                """SELECT qty FROM inventories WHERE campaign_id='c'
                   AND owner_kind='character' AND owner_id='hero' AND item_id='bread'"""
            ).fetchone()[0]
            balances = db.execute(
                "SELECT amount FROM owner_balances WHERE campaign_id='c' ORDER BY owner_kind,owner_id"
            ).fetchall()
            fingerprints = db.execute(
                "SELECT request_fingerprint FROM economy_transactions WHERE campaign_id='c'"
            ).fetchall()
        self.assertEqual(16, tx_count)
        self.assertEqual(0.0, float(stock))
        self.assertEqual(16.0, float(holdings))
        self.assertAlmostEqual(100.0, sum(float(row[0]) for row in balances), places=9)
        self.assertTrue(all(len(str(row[0])) == 64 for row in fingerprints))
        self._assert_sqlite_clean(path)

    def test_concurrent_reopen_schema_install_is_lock_safe(self) -> None:
        """Concurrent constructors must serialize schema installation."""
        path = self.root / "concurrent-reopen.sqlite3"
        WorldEngine(path).ensure_campaign("c", "Concurrent reopen")
        barrier = threading.Barrier(12)

        def reopen(_slot: int) -> str | None:
            barrier.wait()
            try:
                WorldEngine(path)
            except CompanionMigrationError as exc:
                return f"{type(exc).__name__}: {exc}"
            return None

        with ThreadPoolExecutor(max_workers=12) as pool:
            errors = [error for error in pool.map(reopen, range(12)) if error]
        print(f"STRESS_METRIC label=concurrent-reopen failures={len(errors)}")
        self.assertEqual([], errors)
        with WorldEngine(path)._db() as db:
            self.assertEqual(24, int(db.execute("PRAGMA user_version").fetchone()[0]))
        self._assert_sqlite_clean(path)

    def test_concurrent_process_schema_install_is_lock_safe(self) -> None:
        path = self.root / "concurrent-process.sqlite3"
        code = (
            "import sys; from world_engine import WorldEngine; "
            "WorldEngine(sys.argv[1])"
        )
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", code, str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(8)
        ]
        failures: list[str] = []
        try:
            for process in processes:
                stdout, stderr = process.communicate(timeout=30)
                if process.returncode:
                    failures.append(f"{process.returncode}: {stdout}\n{stderr}")
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
        print(f"STRESS_METRIC label=concurrent-process failures={len(failures)}")
        self.assertEqual([], failures)
        with WorldEngine(path)._db() as db:
            self.assertEqual(24, int(db.execute("PRAGMA user_version").fetchone()[0]))
        self._assert_sqlite_clean(path)

    def _build_populated_world(self, path: Path) -> WorldEngine:
        engine = WorldEngine(path)
        engine.ensure_campaign("c", "Full stack", "2020-01-01T00:00:00+00:00")
        engine.set_simulation_seed("c", 500)
        engine.upsert_location("c", "town", "Town", tags=["outdoors"])
        engine.upsert_location("c", "farm", "Farm", tags=["outdoors"])
        engine.save_location_link("c", "farm", "town", 2, bidirectional=True)
        engine.upsert_character("c", "hero", "Hero", location="town")
        engine.upsert_npc("c", "ada", "Ada", location="town")
        engine.upsert_faction("c", "guild", "Guild")

        engine.world_systems_dispatch(
            "set_climate",
            "c",
            {
                "scope_type": "location",
                "scope_id": "town",
                "climate": "temperate",
                "weather_weights": {"rain": 1.0},
                "state": {"actor_exposure": False},
            },
        )
        engine.save_item_def("c", "grain", "Grain", base_price=2)
        engine.save_resource_node(
            "c", "grain-field", "farm", "grain", qty=100, qty_max=200, regen_per_day=2
        )
        economy = EconomyKernel(engine)
        economy.dispatch(
            "save_market",
            "c",
            {"market_id": "town-market", "location_id": "town", "name": "Market"},
        )
        economy.dispatch(
            "set_market_item",
            "c",
            {
                "market_id": "town-market",
                "item_id": "grain",
                "target_stock": 20,
                "demand_per_day": 1,
            },
        )
        economy.dispatch(
            "save_extractor",
            "c",
            {
                "extractor_id": "farmers",
                "location_id": "farm",
                "owner_kind": "location",
                "owner_id": "farm",
                "resource_node_id": "grain-field",
                "units_per_day": 4,
            },
        )
        engine.set_inventory_item("c", "location", "town", "grain", 20)

        for location, attractiveness in (("farm", 0.2), ("town", 0.8)):
            engine.population_dispatch(
                "save_settlement",
                "c",
                {
                    "location_id": location,
                    "housing_capacity": 500,
                    "water_capacity": 500,
                    "prosperity": attractiveness,
                    "stability": 0.7,
                    "attractiveness": attractiveness,
                },
            )
        engine.population_dispatch(
            "save_cohort",
            "c",
            {
                "cohort_id": "farm-adults",
                "location_id": "farm",
                "count": 100,
                "age_band": "adult",
                "birth_rate_annual": 0.1,
                "death_rate_annual": 0.02,
                "labor_participation": 0.5,
                "migration_affinity": 0.2,
                "replace_legacy": True,
            },
        )

        politics = PoliticsKernel(engine)
        politics.dispatch(
            "create_project",
            "c",
            {
                "principal_kind": "faction",
                "principal_id": "guild",
                "request_key": "stress-project-create",
                "project_id": "granary",
                "owner_faction_id": "guild",
                "location_id": "farm",
                "project_kind": "granary",
                "name": "Granary",
                "work_required": 2,
                "requirements": [],
            },
        )
        politics.dispatch(
            "start_project",
            "c",
            {
                "principal_kind": "faction",
                "principal_id": "guild",
                "request_key": "stress-project-start",
                "project_id": "granary",
            },
        )

        TurnRouter(engine).sync_existing_entities("c")
        mechanisms = MechanismKernel(engine)
        mechanisms.save_operator(
            "c",
            {
                "id": "agency.finish",
                "bindings": {"actor": {"kinds": ["npc"]}},
                "planning_effects": {"finished": True},
                "effects": [{"op": "world_state.set", "key": "agency_finished", "value": True}],
            },
        )
        agency = AgencyKernel(engine)
        agency.save_affordance(
            "c", "finish", "agency.finish", source_kind="location", source_id="town"
        )
        agency.save_goal("c", "finish-goal", "npc", "ada", {"finished": True})
        agency.create_plan("c", "finish-goal")

        mechanisms.save_operator(
            "c",
            {
                "id": "incident.mark",
                "effects": [{"op": "world_state.set", "key": "incident_seen", "value": True}],
            },
        )
        IncidentKernel(engine).save_definition(
            "c",
            "stress-incident",
            "test",
            "stress_incident",
            "Stress incident at {scope_id}.",
            operator_id="incident.mark",
            cooldown_minutes=24 * 60,
            suppression_minutes=0,
        )

        quest = QuestRuntimeKernel(engine)
        quest.bind_template(
            "c",
            {
                "template_id": "incident-quest",
                "visibility": "public",
                "bindings": {
                    "owner": {"kind": "character"},
                    "target": {"kind": "npc"},
                    "place": {"kind": "location"},
                },
                "quest": {
                    "id": "incident-quest",
                    "title": "Observe the incident",
                    "owner_id": "$owner.id",
                    "region": "$place.id",
                    "objectives": ["Observe"],
                },
                "nodes": [
                    {
                        "id": "observe",
                        "status": "active",
                        "success": {"event": {"event_type": "stress_incident"}},
                        "state": {"terminal": True},
                    }
                ],
                "edges": [],
            },
            {"owner": "character:hero", "target": "npc:ada", "place": "location:town"},
            dry_run=False,
        )

        engine.save_npc_need("c", "ada", "hunger", 20, baseline=20, drift_per_day=0.1)
        engine.save_npc_action("c", "ada", "work", location="farm", base_utility=1)
        engine.save_simulation_rule("c", "stock", "stock", cadence="day", target="resource_nodes.qty")
        engine.save_simulation_rule("c", "decide", "decide", cadence="day")
        engine.save_simulation_rule(
            "c",
            "news",
            "chance",
            cadence="day",
            params={"p": 1.0, "event_type": "daily_news", "summary": "Daily news."},
        )
        return engine

    @staticmethod
    def _sum_tallies(results: list[dict[str, Any]]) -> dict[str, float]:
        total: dict[str, float] = defaultdict(float)
        for result in results:
            for key, value in result["simulation"].items():
                total[key] += float(value)
        return dict(total)

    @staticmethod
    def _normalized_state(engine: WorldEngine) -> dict[str, list[tuple[Any, ...]]]:
        queries = {
            "campaign": "SELECT world_time,weather FROM campaigns WHERE id='c'",
            "npc": "SELECT id,location,status,hp FROM npcs WHERE campaign_id='c' ORDER BY id",
            "resources": "SELECT id,ROUND(qty,9) FROM resource_nodes WHERE campaign_id='c' ORDER BY id",
            "weather": "SELECT scope_type,scope_id,condition,precipitation,ROUND(temperature_c,9),generated_world_time FROM environment_weather WHERE campaign_id='c' ORDER BY scope_type,scope_id",
            "inventory": "SELECT owner_kind,owner_id,item_id,ROUND(qty,9) FROM inventories WHERE campaign_id='c' ORDER BY owner_kind,owner_id,item_id",
            "markets": "SELECT market_id,item_id,ROUND(demand_pressure,9) FROM economy_market_items WHERE campaign_id='c' ORDER BY market_id,item_id",
            "cohorts": "SELECT id,location_id,ROUND(count,9),age_band FROM population_cohorts WHERE campaign_id='c' ORDER BY id",
            "population": "SELECT location_id,ROUND(population,9),ROUND(migration_pressure,9) FROM population_state WHERE campaign_id='c' ORDER BY location_id",
            "projects": "SELECT id,status,ROUND(progress,9) FROM politics_projects WHERE campaign_id='c' ORDER BY id",
            "goals": "SELECT id,status FROM agency_goals WHERE campaign_id='c' ORDER BY id",
            "plans": "SELECT goal_id,status,replan_count FROM agency_plans WHERE campaign_id='c' ORDER BY goal_id,id",
            "incidents": "SELECT definition_id,scope_id,selected_world_time FROM incident_instances WHERE campaign_id='c' ORDER BY selected_world_time,definition_id,scope_id",
            "quests": "SELECT id,status FROM quests WHERE campaign_id='c' ORDER BY id",
            "quest_nodes": "SELECT quest_id,id,status FROM quest_nodes WHERE campaign_id='c' ORDER BY quest_id,id",
            "world_state": "SELECT scope_type,scope_id,state_key,value_json FROM world_state WHERE campaign_id='c' ORDER BY scope_type,scope_id,state_key",
        }
        with engine._db() as db:
            return {
                name: [tuple(row) for row in db.execute(sql).fetchall()]
                for name, sql in queries.items()
            }

    def test_populated_scheduler_is_exact_under_chunked_catchup(self) -> None:
        whole_path = self.root / "whole.sqlite3"
        chunked_path = self.root / "chunked.sqlite3"
        whole = self._build_populated_world(whole_path)
        chunked = self._build_populated_world(chunked_path)

        whole_results = self._measured(
            "full-stack-whole",
            whole_path,
            lambda: [whole.advance_world("c", 3 * 24 * 60, "stress")],
            max_seconds=30,
            max_peak_mib=256,
            max_db_mib=32,
        )
        chunked_results = self._measured(
            "full-stack-chunked",
            chunked_path,
            lambda: [chunked.advance_world("c", 24 * 60, "stress") for _ in range(3)],
            max_seconds=30,
            max_peak_mib=256,
            max_db_mib=32,
        )
        whole_tally = self._sum_tallies(whole_results)
        chunked_tally = self._sum_tallies(chunked_results)
        # These two observation counters are request-shape dependent in v5.0:
        # a one-shot request keeps initially-active domains on every boundary,
        # while chunked requests re-evaluate activity after each chunk.  Durable
        # state and every state-affecting tally remain the equivalence contract.
        observation_only = {"agency_actors", "quest_events_processed"}
        self.assertEqual(
            {k: v for k, v in whole_tally.items() if k not in observation_only},
            {k: v for k, v in chunked_tally.items() if k not in observation_only},
        )
        print(
            "STRESS_METRIC label=chunk-observation-delta "
            f"agency={whole_tally['agency_actors']}/{chunked_tally['agency_actors']} "
            f"quest_events={whole_tally['quest_events_processed']}/"
            f"{chunked_tally['quest_events_processed']}"
        )
        self.assertEqual(self._normalized_state(whole), self._normalized_state(chunked))
        for key in (
            "environment_weather",
            "economy_extraction",
            "population_settlements",
            "politics_projects_advanced",
            "agency_actors",
            "incidents_selected",
            "quest_transitions",
            "chance",
        ):
            self.assertGreater(whole_tally[key], 0, f"domain did not execute: {key}")
        self._assert_sqlite_clean(whole_path)
        self._assert_sqlite_clean(chunked_path)

    def test_quest_transition_and_event_caps_are_bounded_and_idempotent(self) -> None:
        path = self.root / "quest-pressure.sqlite3"
        engine = WorldEngine(path)
        engine.ensure_campaign("c", "Quest pressure", "2020-01-01T00:00:00+00:00")
        engine.upsert_location("c", "town", "Town")
        engine.upsert_character("c", "hero", "Hero", location="town")
        kernel = QuestRuntimeKernel(engine)

        def run() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
            for index in range(250):
                quest_id = f"pressure-{index:03d}"
                kernel.bind_template(
                    "c",
                    {
                        "template_id": quest_id,
                        "visibility": "public",
                        "bindings": {"owner": {"kind": "character"}},
                        "quest": {
                            "id": quest_id,
                            "title": quest_id,
                            "owner_id": "$owner.id",
                            "objectives": ["Finish"],
                        },
                        "nodes": [
                            {
                                "id": "done",
                                "status": "active",
                                "success": {"event": {"event_type": "pressure_done"}},
                                "state": {"terminal": True},
                            }
                        ],
                        "edges": [],
                    },
                    {"owner": "character:hero"},
                    dry_run=False,
                )
            # Skip setup events but retain the per-quest instantiation floors.
            with engine._write_db() as db:
                setup_max = int(
                    db.execute("SELECT COALESCE(MAX(id),0) FROM events WHERE campaign_id='c'").fetchone()[0]
                )
                db.execute(
                    """INSERT INTO quest_event_cursors(campaign_id,last_event_id,updated_at)
                       VALUES('c',?,?) ON CONFLICT(campaign_id) DO UPDATE SET
                       last_event_id=excluded.last_event_id,updated_at=excluded.updated_at""",
                    (setup_max, engine._now()),
                )
            engine.commit_event("c", "pressure_done", "Complete all pressure quests.")
            first = kernel.step("c", when="2020-01-02T00:00:00+00:00")
            replay = kernel.step("c", when="2020-01-02T00:00:00+00:00")
            while replay["more_events"]:
                replay = kernel.step("c", when="2020-01-02T00:00:00+00:00")
                self.assertEqual(0, replay["transitions"])

            with engine._write_db() as db:
                revision = engine._next_revision(db, "c")
                for index in range(300):
                    engine._insert_event(
                        db,
                        "c",
                        revision,
                        "pressure_noise",
                        f"noise {index}",
                        world_time_override="2020-01-02T00:00:00+00:00",
                    )
            drains: list[dict[str, Any]] = []
            while True:
                result = kernel.step("c", when="2020-01-02T00:00:00+00:00")
                drains.append(result)
                if not result["more_events"]:
                    break
                self.assertLess(len(drains), 10, "quest event cursor failed to drain")
            return first, replay, drains

        first, replay, drains = self._measured(
            "quest-event-pressure",
            path,
            run,
            max_seconds=60,
            max_peak_mib=512,
            max_db_mib=64,
        )
        self.assertEqual(500, first["transitions"])
        self.assertLess(first["transitions"], MAX_TRANSITIONS_PER_STEP)
        self.assertEqual(0, replay["transitions"])
        self.assertEqual(MAX_EVENTS_PER_STEP, drains[0]["processed_events"])
        self.assertTrue(drains[0]["more_events"])
        self.assertEqual(300, sum(item["processed_events"] for item in drains))
        self.assertFalse(drains[-1]["more_events"])
        with engine._db() as db:
            self.assertEqual(
                250,
                db.execute("SELECT COUNT(*) FROM quests WHERE campaign_id='c' AND status='completed'").fetchone()[0],
            )
            self.assertEqual(
                500,
                db.execute("SELECT COUNT(*) FROM quest_transition_receipts WHERE campaign_id='c'").fetchone()[0],
            )
        self._assert_sqlite_clean(path)

    def test_actor_caps_survive_repeated_reopen_and_integrity_checks(self) -> None:
        path = self.root / "actor-pressure.sqlite3"
        engine = WorldEngine(path)
        engine.ensure_campaign("c", "Actor pressure", "2020-01-01T00:00:00+00:00")
        engine.upsert_location("c", "field", "Field", tags=["outdoors"])
        engine.world_systems_dispatch(
            "set_climate",
            "c",
            {
                "scope_type": "location",
                "scope_id": "field",
                "climate": "temperate",
                "weather_weights": {"clear": 1.0},
                "state": {"actor_exposure": True},
            },
        )
        agency = AgencyKernel(engine)
        for index in range(270):
            actor_id = f"npc-{index:03d}"
            engine.upsert_npc("c", actor_id, actor_id, location="field")
            agency.set_personality_value("c", "npc", actor_id, "duty", 1)
        # Avoid turning setup ledger volume into an appraisal benchmark; this
        # test isolates active-actor scheduling and environmental materialization.
        with engine._write_db() as db:
            max_event = int(db.execute("SELECT COALESCE(MAX(id),0) FROM events WHERE campaign_id='c'").fetchone()[0])
            db.execute(
                """INSERT INTO agency_actor_state(
                       campaign_id,actor_kind,actor_id,last_appraised_event_id,
                       last_step_world_time,state_json,updated_at)
                   SELECT campaign_id,'npc',actor_id,?,NULL,'{}',?
                   FROM agency_personality_values WHERE campaign_id='c'
                   GROUP BY campaign_id,actor_id""",
                (max_event, engine._now()),
            )

        def run() -> tuple[dict[str, Any], tuple[int, int]]:
            result = engine.advance_world("c", 24 * 60, "actor cap")
            with engine._db() as db:
                counts = (
                    int(
                        db.execute(
                            "SELECT COUNT(*) FROM agency_actor_state WHERE campaign_id='c' AND last_step_world_time IS NOT NULL"
                        ).fetchone()[0]
                    ),
                    int(
                        db.execute(
                            "SELECT COUNT(*) FROM environment_targets WHERE campaign_id='c' AND target_type='actor'"
                        ).fetchone()[0]
                    ),
                )
            return result, counts

        result, counts = self._measured(
            "actor-reopen-pressure",
            path,
            run,
            max_seconds=60,
            max_peak_mib=512,
            max_db_mib=64,
        )
        self.assertEqual(MAX_ACTIVE_ACTORS, result["simulation"]["agency_actors"])
        self.assertEqual((MAX_ACTIVE_ACTORS, ENVIRONMENT_NPCS_PER_LOCATION_CAP), counts)

        expected = (270, MAX_ACTIVE_ACTORS, ENVIRONMENT_NPCS_PER_LOCATION_CAP)
        for _ in range(6):
            reopened = WorldEngine(path)
            with reopened._db() as db:
                observed = (
                    int(db.execute("SELECT COUNT(*) FROM npcs WHERE campaign_id='c'").fetchone()[0]),
                    int(
                        db.execute(
                            "SELECT COUNT(*) FROM agency_actor_state WHERE campaign_id='c' AND last_step_world_time IS NOT NULL"
                        ).fetchone()[0]
                    ),
                    int(
                        db.execute(
                            "SELECT COUNT(*) FROM environment_targets WHERE campaign_id='c' AND target_type='actor'"
                        ).fetchone()[0]
                    ),
                )
                self.assertEqual(WorldEngine.SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])
                self.assertEqual("ok", db.execute("PRAGMA integrity_check").fetchone()[0])
                self.assertEqual([], db.execute("PRAGMA foreign_key_check").fetchall())
            self.assertEqual(expected, observed)


if __name__ == "__main__":
    unittest.main()
