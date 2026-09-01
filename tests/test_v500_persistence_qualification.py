"""Persistence qualification for the World Engine 5.0 schema spine.

The fixtures in this module use only temporary SQLite files.  They exercise
real ``WorldEngine`` reopen/migration paths rather than copying schema text out
of the implementation under test.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from world_engine import WorldEngine
from world_engine.agency import AgencyKernel
from world_engine.incidents import IncidentKernel
from world_engine.politics import PoliticsKernel
from world_engine.quests import QuestRuntimeKernel

UTC = timezone.utc
STAGE_FEATURES = {
    21: "event_incident_runtime",
    22: "politics_commitment_runtime",
    23: "actor_agency_runtime",
    24: "quest_graph_runtime",
}
INCIDENT_TABLES = (
    "incident_runtime_state",
    "incident_instances",
    "incident_pressures",
    "incident_definitions",
)
QUEST_RUNTIME_TABLES = (
    "quest_transition_receipts",
    "quest_event_cursors",
    "quest_runtime_instances",
)
REQUIRED_STAGE_TABLES = {
    "incident_definitions",
    "incident_instances",
    "incident_pressures",
    "incident_runtime_state",
    "politics_config",
    "politics_action_receipts",
    "agency_affordances",
    "agency_goals",
    "agency_actor_state",
    "quest_runtime_instances",
    "quest_event_cursors",
    "quest_transition_receipts",
}


class WorldEngineV500PersistenceQualification(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _assert_sqlite_clean(test: unittest.TestCase, path: Path) -> None:
        with closing(sqlite3.connect(path)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            test.assertEqual("ok", db.execute("PRAGMA integrity_check").fetchone()[0])
            test.assertEqual([], db.execute("PRAGMA foreign_key_check").fetchall())

    @staticmethod
    def _create_schema_20_fixture(path: Path) -> None:
        """Create the v4.7 foundation shape needed to exercise event ALTERs."""
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(
                """
                PRAGMA foreign_keys=ON;
                CREATE TABLE campaigns (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    world_time TEXT NOT NULL,
                    weather TEXT NOT NULL DEFAULT 'clear',
                    revision INTEGER NOT NULL DEFAULT 0,
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    world_time TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    region TEXT,
                    actor_id TEXT,
                    target_id TEXT,
                    summary TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
                );
                INSERT INTO campaigns(
                    id,name,world_time,weather,revision,settings_json,created_at,updated_at
                ) VALUES(
                    'c','schema-20-sentinel','2020-01-01T00:00:00+00:00','clear',7,
                    '{"preserved":true}','2020-01-01T00:00:00+00:00',
                    '2020-01-01T00:00:00+00:00'
                );
                INSERT INTO events(
                    campaign_id,revision,world_time,event_type,region,actor_id,target_id,
                    summary,payload_json,created_at
                ) VALUES(
                    'c',7,'2020-01-01T00:00:00+00:00','legacy_event','old-town',
                    NULL,NULL,'preserve this event','{"schema":20,"sentinel":"kept"}',
                    '2020-01-01T00:00:00+00:00'
                );
                PRAGMA user_version=20;
                """
            )

    def _create_staged_fixture(self, path: Path, version: int) -> set[str]:
        if version == 20:
            self._create_schema_20_fixture(path)
            return {
                "incident_definitions",
                "politics_config",
                "agency_goals",
                "quest_runtime_instances",
            }

        engine = WorldEngine(path)
        engine.ensure_campaign(
            "c", f"schema-{version}-sentinel", "2020-01-01T00:00:00+00:00"
        )
        engine.upsert_character("c", "hero", "Preserved Hero", location="old-town")
        engine.upsert_npc("c", "ada", "Preserved Ada", location="old-town")
        engine.commit_event(
            "c",
            "legacy_event",
            "preserve this event",
            region="old-town",
            payload={"schema": version, "sentinel": "kept"},
        )
        IncidentKernel(engine).save_definition(
            "c", "legacy-incident", "fixture", "legacy_incident", "Legacy incident."
        )
        if version >= 22:
            with engine._write_db() as db:
                db.execute(
                    """INSERT INTO politics_config(
                           campaign_id,enabled,daily_strategy_enabled,max_daily_decisions,
                           state_json,updated_at)
                       VALUES('c',1,0,3,'{"sentinel":"kept"}',?)
                       ON CONFLICT(campaign_id) DO UPDATE SET
                           state_json=excluded.state_json,
                           max_daily_decisions=excluded.max_daily_decisions""",
                    (engine._now(),),
                )
        if version >= 23:
            AgencyKernel(engine).set_personality_value(
                "c", "npc", "ada", "duty", 2.5, metadata={"sentinel": "kept"}
            )

        missing: set[str] = set()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("PRAGMA foreign_keys=OFF")
            table_names = {
                str(row[0])
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if version < 24:
                for name in QUEST_RUNTIME_TABLES:
                    if name in table_names:
                        db.execute(f'DROP TABLE "{name}"')
                missing.add("quest_runtime_instances")
            if version < 23:
                for name in sorted(
                    name for name in table_names if name.startswith("agency_")
                ):
                    db.execute(f'DROP TABLE "{name}"')
                missing.add("agency_goals")
            if version < 22:
                for name in sorted(
                    name for name in table_names if name.startswith("politics_")
                ):
                    db.execute(f'DROP TABLE "{name}"')
                missing.add("politics_config")
            if version < 21:
                for name in INCIDENT_TABLES:
                    if name in table_names:
                        db.execute(f'DROP TABLE "{name}"')
                missing.add("incident_definitions")
            for stage, feature in STAGE_FEATURES.items():
                if stage > version:
                    db.execute(
                        "DELETE FROM we42_schema_features WHERE feature_id=?",
                        (feature,),
                    )
            db.execute(f"PRAGMA user_version={version}")
        return missing

    def _assert_migrated_state(self, path: Path, source_version: int) -> None:
        with closing(sqlite3.connect(path)) as db:
            db.row_factory = sqlite3.Row
            self.assertEqual(24, db.execute("PRAGMA user_version").fetchone()[0])
            tables = {
                str(row[0])
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertTrue(REQUIRED_STAGE_TABLES <= tables)
            self.assertEqual(
                f"schema-{source_version}-sentinel",
                db.execute("SELECT name FROM campaigns WHERE id='c'").fetchone()[0],
            )
            event = db.execute(
                "SELECT summary,payload_json FROM events WHERE event_type='legacy_event'"
            ).fetchone()
            self.assertEqual("preserve this event", event["summary"])
            self.assertEqual(
                {"schema": source_version, "sentinel": "kept"},
                json.loads(event["payload_json"]),
            )
            event_columns = {
                str(row[1])
                for row in db.execute("PRAGMA table_info(events)").fetchall()
            }
            self.assertTrue(
                {
                    "sensitivity",
                    "scope_type",
                    "principal_kind",
                    "principal_id",
                    "causal_parent_event_id",
                    "causal_root_event_id",
                }
                <= event_columns
            )
            features = {
                str(row[0])
                for row in db.execute(
                    "SELECT feature_id FROM we42_schema_features WHERE feature_id IN (?,?,?,?)",
                    tuple(STAGE_FEATURES.values()),
                ).fetchall()
            }
            self.assertEqual(set(STAGE_FEATURES.values()), features)
            if source_version >= 21:
                self.assertEqual(
                    1,
                    db.execute(
                        "SELECT COUNT(*) FROM incident_definitions WHERE id='legacy-incident'"
                    ).fetchone()[0],
                )
            if source_version >= 22:
                row = db.execute(
                    "SELECT max_daily_decisions,state_json FROM politics_config WHERE campaign_id='c'"
                ).fetchone()
                self.assertEqual(3, row["max_daily_decisions"])
                self.assertEqual({"sentinel": "kept"}, json.loads(row["state_json"]))
            if source_version >= 23:
                row = db.execute(
                    """SELECT weight,metadata_json FROM agency_personality_values
                       WHERE campaign_id='c' AND actor_kind='npc' AND actor_id='ada'
                         AND value_key='duty'"""
                ).fetchone()
                self.assertEqual(2.5, row["weight"])
                self.assertEqual({"sentinel": "kept"}, json.loads(row["metadata_json"]))

    def test_schema_20_through_23_upgrade_preserves_data_and_reopens_cleanly(
        self,
    ) -> None:
        for source_version in range(20, 24):
            with self.subTest(source_version=source_version):
                path = self.root / f"schema-{source_version}.sqlite3"
                missing = self._create_staged_fixture(path, source_version)
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual(
                        source_version, db.execute("PRAGMA user_version").fetchone()[0]
                    )
                    present = {
                        str(row[0])
                        for row in db.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                self.assertTrue(missing.isdisjoint(present))

                WorldEngine(path)
                self._assert_migrated_state(path, source_version)
                self._assert_sqlite_clean(self, path)

                # Reopening is itself part of the contract: additive installers
                # must be idempotent and must not duplicate preserved rows.
                WorldEngine(path)
                self._assert_migrated_state(path, source_version)
                self._assert_sqlite_clean(self, path)

    def test_late_schema_installer_failure_rolls_back_the_whole_upgrade(self) -> None:
        path = self.root / "rollback.sqlite3"
        missing_before = self._create_staged_fixture(path, 23)
        self.assertEqual({"quest_runtime_instances"}, missing_before)

        def fail_late(db: sqlite3.Connection, _now: int) -> None:
            db.execute("UPDATE campaigns SET name='partial-upgrade' WHERE id='c'")
            raise RuntimeError("late schema installer fault")

        with mock.patch("world_engine.engine.install_companion_schema_db", fail_late):  # noqa: SIM117
            with self.assertRaisesRegex(RuntimeError, "late schema installer fault"):
                WorldEngine(path)

        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(23, db.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual(
                "schema-23-sentinel",
                db.execute("SELECT name FROM campaigns WHERE id='c'").fetchone()[0],
            )
            tables = {
                str(row[0])
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertNotIn("quest_runtime_instances", tables)

        WorldEngine(path)
        self._assert_migrated_state(path, 23)
        self._assert_sqlite_clean(self, path)

    def test_json_persistence_rejects_nonfinite_numbers_and_stays_canonical(
        self,
    ) -> None:
        path = self.root / "json.sqlite3"
        engine = WorldEngine(path)
        engine.ensure_campaign("c", "JSON")
        engine.upsert_npc("c", "ada", "Ada")
        agency = AgencyKernel(engine)
        quests = QuestRuntimeKernel(engine)

        cases = {
            "event_nan": lambda: engine.commit_event(
                "c", "bad_json", "bad", payload={"value": float("nan")}
            ),
            "world_state_infinity": lambda: engine.set_world_state(
                "c", "world", "global", "bad_json", {"value": float("inf")}
            ),
            "agency_negative_infinity": lambda: agency.save_goal(
                "c", "bad-goal", "npc", "ada", {"value": float("-inf")}
            ),
            "quest_nan": lambda: quests.bind_template(
                "c",
                {
                    "template_id": "bad-template",
                    "quest": {
                        "id": "bad-quest",
                        "title": "Bad quest",
                        "state": {"value": float("nan")},
                    },
                    "nodes": [
                        {
                            "id": "objective",
                            "status": "active",
                            "state": {"terminal": True},
                        }
                    ],
                    "edges": [],
                },
                {},
                dry_run=True,
            ),
        }
        for label, operation in cases.items():
            with self.subTest(label=label):  # noqa: SIM117
                with self.assertRaises((TypeError, ValueError)):
                    operation()

        valid = engine.commit_event(
            "c", "finite_json", "finite", payload={"z": [0, -2.5], "a": 1.25}
        )
        with engine._db() as db:
            stored = db.execute(
                "SELECT payload_json FROM events WHERE id=?", (valid["id"],)
            ).fetchone()["payload_json"]
            bad_events = db.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='bad_json'"
            ).fetchone()[0]
            bad_state = db.execute(
                "SELECT COUNT(*) FROM world_state WHERE state_key='bad_json'"
            ).fetchone()[0]
        self.assertEqual('{"a":1.25,"z":[0,-2.5]}', stored)
        self.assertEqual(0, bad_events)
        self.assertEqual(0, bad_state)
        self.assertEqual(
            {"a": 1.25, "z": [0, -2.5]},
            json.loads(stored, parse_constant=lambda value: self.fail(value)),
        )

    def test_politics_idempotency_normalizes_aliases_case_and_object_order(
        self,
    ) -> None:
        path = self.root / "idempotency.sqlite3"
        engine = WorldEngine(path)
        engine.ensure_campaign("c", "Idempotency", "2020-01-01T00:00:00+00:00")
        engine.upsert_location("c", "town", "Town")
        engine.upsert_faction("c", "guild", "Guild")
        politics = PoliticsKernel(engine)
        first = politics.dispatch(
            "CREATE_PROJECT",
            "c",
            {
                "principal_kind": "FACTION",
                "principal_id": "guild",
                "request_key": "canonical-project",
                "project_id": "bridge",
                "owner_faction_id": "guild",
                "location_id": "town",
                "project_kind": "bridge",
                "name": "Bridge",
                "work_required": 4,
                "requirements": [],
                "metadata": {"second": 2, "first": 1},
            },
        )
        revision_after_first = engine.get_campaign("c")["revision"]
        replay = politics.dispatch(
            "create_project",
            "c",
            {
                "metadata": {"first": 1, "second": 2},
                "requirements": [],
                "work_required": 4,
                "name": "Bridge",
                "project_kind": "bridge",
                "location_id": "town",
                "owner_faction_id": "guild",
                "project_id": "bridge",
                "request_key": " canonical-project ",
                "actor_id": " guild ",
                "actor_kind": " faction ",
            },
        )
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(
            {key: value for key, value in first.items() if key != "idempotent_replay"},
            {key: value for key, value in replay.items() if key != "idempotent_replay"},
        )
        self.assertEqual(revision_after_first, engine.get_campaign("c")["revision"])
        with engine._db() as db:
            receipts = db.execute(
                """SELECT request_digest FROM politics_action_receipts
                   WHERE campaign_id='c' AND request_key='canonical-project'"""
            ).fetchall()
        self.assertEqual(1, len(receipts))
        self.assertEqual(64, len(receipts[0]["request_digest"]))

        with self.assertRaisesRegex(ValueError, "POLITICS_IDEMPOTENCY_CONFLICT"):
            politics.dispatch(
                "create_project",
                "c",
                {
                    "actor_kind": "faction",
                    "actor_id": "guild",
                    "request_key": "canonical-project",
                    "project_id": "bridge",
                    "owner_faction_id": "guild",
                    "location_id": "town",
                    "project_kind": "bridge",
                    "name": "Changed",
                    "work_required": 4,
                    "requirements": [],
                    "metadata": {"first": 1, "second": 2},
                },
            )
        self.assertEqual(revision_after_first, engine.get_campaign("c")["revision"])

    def test_quest_and_agency_cursors_advance_only_at_chronological_boundaries(
        self,
    ) -> None:
        path = self.root / "cursors.sqlite3"
        engine = WorldEngine(path)
        engine.ensure_campaign("c", "Cursors", "2020-01-01T00:00:00+00:00")
        engine.upsert_npc("c", "ada", "Ada", location="town")
        agency = AgencyKernel(engine)
        agency.set_personality_value("c", "npc", "ada", "duty", 1)
        quests = QuestRuntimeKernel(engine)

        with engine._write_db() as db:
            floor = int(
                db.execute(
                    "SELECT COALESCE(MAX(id),0) FROM events WHERE campaign_id='c'"
                ).fetchone()[0]
            )
            db.execute(
                """INSERT INTO quest_event_cursors(campaign_id,last_event_id,updated_at)
                   VALUES('c',?,?)""",
                (floor, engine._now()),
            )
            db.execute(
                """INSERT INTO agency_actor_state(
                       campaign_id,actor_kind,actor_id,last_appraised_event_id,
                       last_step_world_time,state_json,updated_at)
                   VALUES('c','npc','ada',?,NULL,'{}',?)""",
                (floor, engine._now()),
            )
            revision = engine._next_revision(db, "c")
            day_one_event = engine._insert_event(
                db,
                "c",
                revision,
                "day_one",
                "Day one",
                payload={"valence": 0.5, "visibility": "public"},
                world_time_override="2020-01-02T00:00:00+00:00",
            )
            day_two_event = engine._insert_event(
                db,
                "c",
                revision,
                "day_two",
                "Day two",
                payload={"valence": -0.5, "visibility": "public"},
                world_time_override="2020-01-03T00:00:00+00:00",
            )

        quest_day_one = quests.step("c", when="2020-01-02T00:00:00+00:00")
        self.assertEqual(1, quest_day_one["processed_events"])
        self.assertEqual(day_one_event, quest_day_one["last_event_id"])
        quest_day_two = quests.step("c", when="2020-01-03T00:00:00+00:00")
        self.assertEqual(1, quest_day_two["processed_events"])
        self.assertEqual(day_two_event, quest_day_two["last_event_id"])
        self.assertEqual(
            0,
            quests.step("c", when="2020-01-03T00:00:00+00:00")["processed_events"],
        )

        emitted: list[tuple[str, int]] = []

        def capture(event_type, _summary, payload, *_args, **_kwargs):
            emitted.append((event_type, int(payload["source_event_id"])))

        with engine._write_db() as db:
            first = agency.step_db(
                db,
                "c",
                engine._next_revision(db, "c"),
                datetime(2020, 1, 2, tzinfo=UTC),
                capture,
            )
        self.assertEqual(1, first["events_appraised"])
        with engine._db() as db:
            row = db.execute(
                """SELECT last_appraised_event_id,last_step_world_time
                   FROM agency_actor_state
                   WHERE campaign_id='c' AND actor_kind='npc' AND actor_id='ada'"""
            ).fetchone()
        self.assertEqual(day_one_event, row["last_appraised_event_id"])
        self.assertEqual("2020-01-02T00:00:00+00:00", row["last_step_world_time"])

        with engine._write_db() as db:
            second = agency.step_db(
                db,
                "c",
                engine._next_revision(db, "c"),
                datetime(2020, 1, 3, tzinfo=UTC),
                capture,
            )
        self.assertEqual(1, second["events_appraised"])
        self.assertEqual(
            [("agency_appraisal", day_one_event), ("agency_appraisal", day_two_event)],
            emitted,
        )

        with self.assertRaisesRegex(ValueError, "chronological order"):  # noqa: SIM117
            with engine._write_db() as db:
                agency.step_db(
                    db,
                    "c",
                    engine._next_revision(db, "c"),
                    datetime(2020, 1, 2, tzinfo=UTC),
                    capture,
                )
        with engine._db() as db:
            row = db.execute(
                """SELECT last_appraised_event_id,last_step_world_time
                   FROM agency_actor_state
                   WHERE campaign_id='c' AND actor_kind='npc' AND actor_id='ada'"""
            ).fetchone()
        self.assertEqual(day_two_event, row["last_appraised_event_id"])
        self.assertEqual("2020-01-03T00:00:00+00:00", row["last_step_world_time"])
        self._assert_sqlite_clean(self, path)


if __name__ == "__main__":
    unittest.main()
