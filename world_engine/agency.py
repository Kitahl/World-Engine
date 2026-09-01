from __future__ import annotations

import hashlib
import heapq
import json
import math
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .mechanisms import MechanismKernel
from .npc_life import NpcLifeKernel

if TYPE_CHECKING:
    from .engine import WorldEngine


AGENCY_CONTRACT_VERSION = "AGENCY-1.0"
AGENCY_SCHEMA_STAGE = 23
MAX_AFFORDANCES = 256
MAX_PLAN_DEPTH = 8
MAX_PLAN_EXPANDED = 512
MAX_RECALL = 20
MAX_ACTIVE_ACTORS = 256
MAX_EVENTS_PER_ACTOR_STEP = 100
MAX_JSON_BYTES = 64_000

_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
_KIND_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_ACTOR_TABLES = {"character": "characters", "npc": "npcs"}
_SOURCE_TABLES = {
    "character": ("characters", "id"),
    "npc": ("npcs", "id"),
    "location": ("locations", "id"),
    "faction": ("factions", "id"),
    "quest": ("quests", "id"),
    "item": ("item_defs", "id"),
    "market": ("economy_markets", "id"),
    "shipment": ("economy_shipments", "id"),
    "producer": ("economy_producers", "id"),
    "extractor": ("economy_extractors", "id"),
    "route": ("economy_routes", "id"),
    "settlement": ("settlement_profiles", "location_id"),
    "population_cohort": ("population_cohorts", "id"),
    "service": ("services", "id"),
    "homestead": ("homesteads", "id"),
    "job": ("npc_jobs", "id"),
}
_VISIBILITIES = {"public", "actor", "private", "undiscovered"}
_BELIEF_STATUSES = {"believes", "doubts", "rejects", "unknown"}
_GOAL_STATUSES = {"active", "completed", "abandoned", "blocked"}
_PLAN_STATUSES = {"active", "completed", "failed", "replanned", "abandoned"}
_STEP_STATUSES = {"pending", "completed", "failed", "skipped"}
_EMOTION_TYPES = {
    "joy",
    "satisfaction",
    "hope",
    "distress",
    "fear",
    "anger",
    "gratitude",
    "sadness",
}


AGENCY_SCHEMA = r'''
CREATE TABLE IF NOT EXISTS agency_affordances (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'stored',
    operator_id TEXT NOT NULL,
    source_kind TEXT NOT NULL DEFAULT 'global',
    source_id TEXT,
    location_id TEXT,
    visibility TEXT NOT NULL DEFAULT 'public'
        CHECK(visibility IN ('public','actor','private','undiscovered')),
    actor_kinds_json TEXT NOT NULL DEFAULT '["character","npc"]',
    bindings_json TEXT NOT NULL DEFAULT '{}',
    permission_json TEXT NOT NULL DEFAULT '{}',
    belief_requirements_json TEXT NOT NULL DEFAULT '[]',
    base_utility REAL NOT NULL DEFAULT 0,
    value_modifiers_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id,operator_id)
        REFERENCES mechanism_operators(campaign_id,id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_agency_affordances_source
    ON agency_affordances(campaign_id,source_kind,source_id,enabled,id);
CREATE INDEX IF NOT EXISTS idx_agency_affordances_location
    ON agency_affordances(campaign_id,location_id,visibility,enabled,id);

CREATE TABLE IF NOT EXISTS agency_goals (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('character','npc')),
    actor_id TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    desired_state_json TEXT NOT NULL,
    initial_state_json TEXT NOT NULL DEFAULT '{}',
    priority REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active','completed','abandoned','blocked')),
    source_kind TEXT NOT NULL DEFAULT 'authored',
    source_id TEXT,
    visibility TEXT NOT NULL DEFAULT 'private'
        CHECK(visibility IN ('public','private')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_world_time TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_agency_goals_actor
    ON agency_goals(campaign_id,actor_kind,actor_id,status,priority DESC,id);

CREATE TABLE IF NOT EXISTS agency_plans (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('character','npc')),
    actor_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active','completed','failed','replanned','abandoned')),
    current_step INTEGER NOT NULL DEFAULT 0 CHECK(current_step >= 0),
    replan_count INTEGER NOT NULL DEFAULT 0 CHECK(replan_count BETWEEN 0 AND 100),
    plan_digest TEXT NOT NULL,
    planner_json TEXT NOT NULL DEFAULT '{}',
    created_world_time TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,goal_id)
        REFERENCES agency_goals(campaign_id,id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_agency_plans_actor
    ON agency_plans(campaign_id,actor_kind,actor_id,status,goal_id,id);

CREATE TABLE IF NOT EXISTS agency_plan_steps (
    campaign_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    step_index INTEGER NOT NULL CHECK(step_index >= 0),
    affordance_id TEXT NOT NULL,
    operator_id TEXT NOT NULL,
    bindings_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','completed','failed','skipped')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 100),
    execution_id TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,plan_id,step_index),
    FOREIGN KEY(campaign_id,plan_id)
        REFERENCES agency_plans(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id,operator_id)
        REFERENCES mechanism_operators(campaign_id,id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS agency_personality_values (
    campaign_id TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('character','npc')),
    actor_id TEXT NOT NULL,
    value_key TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 0 CHECK(weight BETWEEN -10 AND 10),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,actor_kind,actor_id,value_key),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS agency_emotions (
    campaign_id TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('character','npc')),
    actor_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    emotion_type TEXT NOT NULL
        CHECK(emotion_type IN ('joy','satisfaction','hope','distress','fear','anger','gratitude','sadness')),
    valence REAL NOT NULL CHECK(valence BETWEEN -1 AND 1),
    arousal REAL NOT NULL CHECK(arousal BETWEEN 0 AND 1),
    intensity REAL NOT NULL CHECK(intensity BETWEEN 0 AND 1),
    decay_per_day REAL NOT NULL DEFAULT 0.1 CHECK(decay_per_day BETWEEN 0 AND 1),
    appraisal_json TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    created_world_time TEXT NOT NULL,
    last_updated_world_time TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,actor_kind,actor_id,event_id,emotion_type),
    FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_agency_emotions_actor
    ON agency_emotions(campaign_id,actor_kind,actor_id,active,intensity DESC,event_id DESC);

CREATE TABLE IF NOT EXISTS agency_memories (
    campaign_id TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('character','npc')),
    actor_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    importance REAL NOT NULL CHECK(importance BETWEEN 0 AND 1),
    decay_per_day REAL NOT NULL DEFAULT 0.002 CHECK(decay_per_day BETWEEN 0 AND 1),
    appraisal_json TEXT NOT NULL DEFAULT '{}',
    created_world_time TEXT NOT NULL,
    last_recalled_world_time TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,actor_kind,actor_id,event_id),
    FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_agency_memories_actor
    ON agency_memories(campaign_id,actor_kind,actor_id,importance DESC,event_id DESC);

CREATE TABLE IF NOT EXISTS agency_actor_state (
    campaign_id TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('character','npc')),
    actor_id TEXT NOT NULL,
    last_appraised_event_id INTEGER NOT NULL DEFAULT 0 CHECK(last_appraised_event_id >= 0),
    last_step_world_time TEXT,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,actor_kind,actor_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
'''


_REQUIRED_COLUMNS = {
    "agency_affordances": {
        "campaign_id",
        "id",
        "operator_id",
        "bindings_json",
        "belief_requirements_json",
        "visibility",
    },
    "agency_goals": {"campaign_id", "id", "actor_kind", "actor_id", "desired_state_json"},
    "agency_plans": {"campaign_id", "id", "goal_id", "plan_digest", "status"},
    "agency_plan_steps": {"campaign_id", "plan_id", "step_index", "operator_id", "bindings_json"},
    "agency_personality_values": {"campaign_id", "actor_kind", "actor_id", "value_key", "weight"},
    "agency_emotions": {"campaign_id", "actor_kind", "actor_id", "event_id", "emotion_type", "intensity"},
    "agency_memories": {"campaign_id", "actor_kind", "actor_id", "event_id", "importance"},
    "agency_actor_state": {"campaign_id", "actor_kind", "actor_id", "last_appraised_event_id"},
}


def prepare_agency_schema_db(db: sqlite3.Connection) -> None:
    """Install the additive agency schema without claiming PRAGMA user_version."""

    db.executescript(AGENCY_SCHEMA)
    for table, required in _REQUIRED_COLUMNS.items():
        columns = {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
        missing = required - columns
        if missing:
            raise RuntimeError(f"incompatible {table} schema; missing columns: {sorted(missing)}")


def _validate_id(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not _ID_RE.fullmatch(text):
        raise ValueError(f"{field} must match {_ID_RE.pattern}")
    return text


def _validate_kind(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    if not _KIND_RE.fullmatch(text):
        raise ValueError(f"{field} must match {_KIND_RE.pattern}")
    return text


def _finite(value: Any, field: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be a finite number")
    if not minimum <= number <= maximum:
        raise ValueError(f"{field} must be between {minimum:g} and {maximum:g}")
    return number


def _json_safe(value: Any, field: str, *, depth: int = 0) -> Any:
    if depth > 12:
        raise ValueError(f"{field} is too deeply nested")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        if len(value) > 256:
            raise ValueError(f"{field} has too many keys")
        return {
            str(key)[:200]: _json_safe(child, f"{field}.{key}", depth=depth + 1)
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        if len(value) > 512:
            raise ValueError(f"{field} has too many items")
        return [_json_safe(child, f"{field}[{index}]", depth=depth + 1) for index, child in enumerate(value)]
    raise ValueError(f"{field} contains an unsupported value")


def _canonical(value: Any) -> str:
    safe = _json_safe(value, "value")
    encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError("JSON value is too large")
    return encoded


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class AgencyKernel:
    """Deterministic actor agency over MOP operators and authoritative beliefs.

    Affordances contain references and bindings only. Mechanical preconditions,
    planning effects, costs, and runtime effects remain owned by MOP and its
    domain delegates. The optional executor is deliberately transaction-aware:
    scheduler calls already own the SQLite write transaction.
    """

    def __init__(
        self,
        engine: WorldEngine,
        *,
        operator_executor_db: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.e = engine
        self.operator_executor_db = operator_executor_db
        self._providers: dict[str, Callable[..., Iterable[Mapping[str, Any]]]] = {
            "stored": self._stored_affordance_provider_db,
        }

    def register_affordance_provider(
        self,
        name: str,
        provider: Callable[..., Iterable[Mapping[str, Any]]],
    ) -> None:
        provider_name = _validate_kind(name, "provider name")
        if provider_name == "stored":
            raise ValueError("the stored affordance provider cannot be replaced")
        if not callable(provider):
            raise TypeError("provider must be callable")
        self._providers[provider_name] = provider

    @staticmethod
    def _loads(value: str | None, fallback: Any) -> Any:
        if not value:
            return fallback
        try:
            return json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("stored agency JSON is invalid") from exc

    def _actor_db(self, db: sqlite3.Connection, campaign_id: str, actor_kind: str, actor_id: str) -> sqlite3.Row:
        actor_kind = str(actor_kind).lower()
        table = _ACTOR_TABLES.get(actor_kind)
        if not table:
            raise ValueError("actor_kind must be character or npc")
        row = db.execute(
            f"SELECT * FROM {table} WHERE campaign_id=? AND id=?",
            (campaign_id, actor_id),
        ).fetchone()
        if not row:
            raise KeyError(f"unknown {actor_kind}: {actor_id}")
        return row

    @staticmethod
    def _actor_key(actor_kind: str, actor_id: str) -> str:
        return f"{actor_kind}:{actor_id}"

    @staticmethod
    def _table_exists_db(db: sqlite3.Connection, table: str) -> bool:
        return db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone() is not None

    def _source_exists_db(self, db: sqlite3.Connection, campaign_id: str, kind: str, source_id: str | None) -> bool:
        if kind == "global":
            return source_id in {None, "global"}
        if not source_id:
            return False
        table_spec = _SOURCE_TABLES.get(kind)
        if not table_spec or not self._table_exists_db(db, table_spec[0]):
            return False
        table, key = table_spec
        return db.execute(
            f"SELECT 1 FROM {table} WHERE campaign_id=? AND {key}=?",
            (campaign_id, source_id),
        ).fetchone() is not None

    def _source_location_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        kind: str,
        source_id: str | None,
    ) -> str | None:
        if not source_id:
            return None
        if kind in {"character", "npc"}:
            row = db.execute(
                f"SELECT location FROM {_ACTOR_TABLES[kind]} WHERE campaign_id=? AND id=?",
                (campaign_id, source_id),
            ).fetchone()
            return str(row["location"]) if row else None
        if kind == "location":
            return source_id if self._source_exists_db(db, campaign_id, kind, source_id) else None
        if kind in {"market", "producer", "extractor"}:
            table = _SOURCE_TABLES[kind][0]
            if not self._table_exists_db(db, table):
                return None
            row = db.execute(
                f"SELECT location_id FROM {table} WHERE campaign_id=? AND id=?",
                (campaign_id, source_id),
            ).fetchone()
            return str(row["location_id"]) if row else None
        if kind in {"service", "homestead"}:
            table = _SOURCE_TABLES[kind][0]
            row = db.execute(
                f"SELECT location_id FROM {table} WHERE campaign_id=? AND id=?",
                (campaign_id, source_id),
            ).fetchone()
            return str(row["location_id"]) if row else None
        if kind == "settlement":
            return source_id if self._source_exists_db(db, campaign_id, kind, source_id) else None
        if kind == "population_cohort" and self._table_exists_db(db, "population_cohorts"):
            row = db.execute(
                "SELECT location_id FROM population_cohorts WHERE campaign_id=? AND id=?",
                (campaign_id, source_id),
            ).fetchone()
            return str(row["location_id"]) if row else None
        return None

    def _normalize_belief_requirements(self, values: Any) -> list[dict[str, Any]]:
        if not isinstance(values, list):
            raise TypeError("belief_requirements must be a list")
        if len(values) > 64:
            raise ValueError("belief_requirements has too many items")
        normalized: list[dict[str, Any]] = []
        for index, raw in enumerate(values):
            if not isinstance(raw, Mapping):
                raise TypeError(f"belief_requirements[{index}] must be an object")
            allowed = {"fact_id", "statuses", "min_confidence", "value", "utility_weight"}
            unknown = set(raw) - allowed
            if unknown:
                raise ValueError(f"belief_requirements[{index}] has unknown keys: {sorted(unknown)}")
            fact_id = _validate_id(raw.get("fact_id"), f"belief_requirements[{index}].fact_id")
            statuses = raw.get("statuses", ["believes"])
            if not isinstance(statuses, list) or not statuses:
                raise ValueError(f"belief_requirements[{index}].statuses must be a non-empty list")
            clean_statuses = sorted({str(item).lower() for item in statuses})
            if any(item not in _BELIEF_STATUSES for item in clean_statuses):
                raise ValueError(f"belief_requirements[{index}].statuses is invalid")
            item: dict[str, Any] = {
                "fact_id": fact_id,
                "statuses": clean_statuses,
                "min_confidence": _finite(
                    raw.get("min_confidence", 0.5),
                    f"belief_requirements[{index}].min_confidence",
                    minimum=0,
                    maximum=1,
                ),
                "utility_weight": _finite(
                    raw.get("utility_weight", 0),
                    f"belief_requirements[{index}].utility_weight",
                    minimum=-100,
                    maximum=100,
                ),
            }
            if "value" in raw:
                item["value"] = _json_safe(raw["value"], f"belief_requirements[{index}].value")
            normalized.append(item)
        return normalized

    def _normalize_permission(self, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise TypeError("permission must be an object")
        allowed = {
            "actor_keys",
            "faction_id",
            "requires_owned_by_actor",
            "owner_kind",
            "owner_id",
            "relationship_target_id",
            "minimum_trust",
            "max_travel_hours",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"permission has unknown keys: {sorted(unknown)}")
        result = dict(_json_safe(value, "permission"))
        if "actor_keys" in result:
            if not isinstance(result["actor_keys"], list):
                raise ValueError("permission.actor_keys must be a list")
            result["actor_keys"] = sorted({_validate_id(item, "permission.actor_keys") for item in result["actor_keys"]})
        if "faction_id" in result:
            result["faction_id"] = _validate_id(result["faction_id"], "permission.faction_id")
        if "owner_kind" in result:
            result["owner_kind"] = _validate_kind(result["owner_kind"], "permission.owner_kind")
        if "owner_id" in result:
            result["owner_id"] = _validate_id(result["owner_id"], "permission.owner_id")
        if "relationship_target_id" in result:
            result["relationship_target_id"] = _validate_id(
                result["relationship_target_id"], "permission.relationship_target_id"
            )
        if "minimum_trust" in result:
            result["minimum_trust"] = _finite(
                result["minimum_trust"], "permission.minimum_trust", minimum=-100, maximum=100
            )
        if "max_travel_hours" in result:
            result["max_travel_hours"] = _finite(
                result["max_travel_hours"], "permission.max_travel_hours", minimum=0, maximum=100_000
            )
        result["requires_owned_by_actor"] = bool(result.get("requires_owned_by_actor", False))
        return result

    def _save_affordance_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        affordance_id: str,
        operator_id: str,
        *,
        provider: str = "stored",
        source_kind: str = "global",
        source_id: str | None = None,
        location_id: str | None = None,
        visibility: str = "public",
        actor_kinds: Iterable[str] = ("character", "npc"),
        bindings: Mapping[str, Any] | None = None,
        permission: Mapping[str, Any] | None = None,
        belief_requirements: list[Mapping[str, Any]] | None = None,
        base_utility: float = 0,
        value_modifiers: Mapping[str, Any] | None = None,
        enabled: bool = True,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        affordance_id = _validate_id(affordance_id, "affordance_id")
        operator_id = _validate_id(operator_id, "operator_id")
        provider = _validate_kind(provider, "provider")
        source_kind = _validate_kind(source_kind, "source_kind")
        source_id = _validate_id(source_id, "source_id") if source_id is not None else None
        location_id = _validate_id(location_id, "location_id") if location_id is not None else None
        visibility = str(visibility).strip().lower()
        if visibility not in _VISIBILITIES:
            raise ValueError(f"visibility must be one of {sorted(_VISIBILITIES)}")
        kinds = sorted({str(item).strip().lower() for item in actor_kinds})
        if not kinds or any(item not in _ACTOR_TABLES for item in kinds):
            raise ValueError("actor_kinds must contain character and/or npc")
        if not db.execute(
            "SELECT 1 FROM mechanism_operators WHERE campaign_id=? AND id=? AND enabled=1",
            (campaign_id, operator_id),
        ).fetchone():
            raise KeyError(f"unknown or disabled mechanism operator: {operator_id}")
        if not self._source_exists_db(db, campaign_id, source_kind, source_id):
            raise KeyError(f"unknown affordance source: {source_kind}:{source_id}")
        if location_id and not self._source_exists_db(db, campaign_id, "location", location_id):
            raise KeyError(f"unknown location: {location_id}")
        clean_bindings = _json_safe(dict(bindings or {}), "bindings")
        clean_permission = self._normalize_permission(permission)
        clean_beliefs = self._normalize_belief_requirements(list(belief_requirements or []))
        utility = _finite(base_utility, "base_utility", minimum=-1_000, maximum=1_000)
        clean_modifiers = _json_safe(dict(value_modifiers or {}), "value_modifiers")
        for key, value in clean_modifiers.items():
            _validate_kind(key, "value modifier key")
            clean_modifiers[key] = _finite(value, f"value_modifiers.{key}", minimum=-100, maximum=100)
        clean_metadata = _json_safe(dict(metadata or {}), "metadata")
        now = self.e._now()
        db.execute(
            """INSERT INTO agency_affordances(
                   campaign_id,id,provider,operator_id,source_kind,source_id,location_id,visibility,
                   actor_kinds_json,bindings_json,permission_json,belief_requirements_json,
                   base_utility,value_modifiers_json,enabled,metadata_json,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(campaign_id,id) DO UPDATE SET
                   provider=excluded.provider,operator_id=excluded.operator_id,
                   source_kind=excluded.source_kind,source_id=excluded.source_id,
                   location_id=excluded.location_id,visibility=excluded.visibility,
                   actor_kinds_json=excluded.actor_kinds_json,bindings_json=excluded.bindings_json,
                   permission_json=excluded.permission_json,
                   belief_requirements_json=excluded.belief_requirements_json,
                   base_utility=excluded.base_utility,value_modifiers_json=excluded.value_modifiers_json,
                   enabled=excluded.enabled,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
            (
                campaign_id,
                affordance_id,
                provider,
                operator_id,
                source_kind,
                source_id,
                location_id,
                visibility,
                self.e._dumps(kinds),
                self.e._dumps(clean_bindings),
                self.e._dumps(clean_permission),
                self.e._dumps(clean_beliefs),
                utility,
                self.e._dumps(clean_modifiers),
                int(bool(enabled)),
                self.e._dumps(clean_metadata),
                now,
            ),
        )
        return self._decode_affordance(
            db.execute(
                "SELECT * FROM agency_affordances WHERE campaign_id=? AND id=?",
                (campaign_id, affordance_id),
            ).fetchone()
        )

    def save_affordance(self, campaign_id: str, affordance_id: str, operator_id: str, **kwargs: Any) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        with self.e._write_db() as db:
            result = self._save_affordance_db(db, campaign_id, affordance_id, operator_id, **kwargs)
            revision = self.e._next_revision(db, campaign_id)
            event_id = self.e._insert_event(
                db,
                campaign_id,
                revision,
                "agency_affordance_saved",
                f"Affordance saved: {affordance_id}",
                actor_id=affordance_id,
                payload={"affordance_id": affordance_id, "operator_id": operator_id},
            )
        return {**result, "revision": revision, "event_id": event_id}

    def remove_affordance(self, campaign_id: str, affordance_id: str) -> dict[str, Any]:
        affordance_id = _validate_id(affordance_id, "affordance_id")
        with self.e._write_db() as db:
            cursor = db.execute(
                "DELETE FROM agency_affordances WHERE campaign_id=? AND id=?",
                (campaign_id, affordance_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown affordance: {affordance_id}")
            revision = self.e._next_revision(db, campaign_id)
            event_id = self.e._insert_event(
                db,
                campaign_id,
                revision,
                "agency_affordance_removed",
                f"Affordance removed: {affordance_id}",
                actor_id=affordance_id,
            )
        return {"campaign_id": campaign_id, "id": affordance_id, "revision": revision, "event_id": event_id}

    def _decode_affordance(self, row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for field in ("actor_kinds", "bindings", "permission", "belief_requirements", "value_modifiers", "metadata"):
            result[field] = self._loads(result.pop(field + "_json"), [] if field in {"actor_kinds", "belief_requirements"} else {})
        result["enabled"] = bool(result["enabled"])
        result["base_utility"] = _finite(result["base_utility"], "stored base_utility", minimum=-1_000, maximum=1_000)
        return result

    def _stored_affordance_provider_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        _actor_kind: str,
        _actor_id: str,
    ) -> Iterable[Mapping[str, Any]]:
        rows = db.execute(
            "SELECT * FROM agency_affordances WHERE campaign_id=? AND enabled=1 ORDER BY id LIMIT ?",
            (campaign_id, MAX_AFFORDANCES),
        ).fetchall()
        return [self._decode_affordance(row) for row in rows]

    def _shortest_hours_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        start: str | None,
        goal: str | None,
        maximum: float,
    ) -> float | None:
        if not start or not goal:
            return None
        if start == goal:
            return 0.0
        rows = db.execute(
            "SELECT from_id,to_id,travel_hours FROM location_links WHERE campaign_id=? ORDER BY from_id,to_id",
            (campaign_id,),
        ).fetchall()
        adjacency: dict[str, list[tuple[str, float]]] = {}
        for row in rows:
            hours = _finite(row["travel_hours"], "stored travel_hours", minimum=0, maximum=100_000)
            adjacency.setdefault(str(row["from_id"]), []).append((str(row["to_id"]), hours))
        queue: list[tuple[float, str]] = [(0.0, start)]
        best = {start: 0.0}
        while queue:
            distance, node = heapq.heappop(queue)
            if distance != best.get(node) or distance > maximum:
                continue
            if node == goal:
                return distance
            for target, weight in adjacency.get(node, []):
                candidate = distance + weight
                if candidate <= maximum and candidate < best.get(target, math.inf):
                    best[target] = candidate
                    heapq.heappush(queue, (candidate, target))
        return None

    def _permission_allowed_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        actor_kind: str,
        actor_id: str,
        actor: sqlite3.Row,
        candidate: Mapping[str, Any],
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        permission = dict(candidate.get("permission") or {})
        actor_key = self._actor_key(actor_kind, actor_id)
        allowed_keys = set(permission.get("actor_keys") or [])
        if allowed_keys and actor_key not in allowed_keys and actor_id not in allowed_keys:
            reasons.append("actor is not permitted")
        faction_id = permission.get("faction_id")
        if faction_id and str(dict(actor).get("faction_id", "")) != str(faction_id):
            reasons.append("actor faction is not permitted")
        source_kind = str(candidate.get("source_kind") or "global")
        source_id = candidate.get("source_id")
        ownership = None
        if source_id and self._table_exists_db(db, "ownership"):
            ownership = db.execute(
                "SELECT owner_kind,owner_id FROM ownership WHERE campaign_id=? AND asset_kind=? AND asset_id=?",
                (campaign_id, source_kind, source_id),
            ).fetchone()
        if permission.get("requires_owned_by_actor") and (
            not ownership or ownership["owner_kind"] != actor_kind or ownership["owner_id"] != actor_id
        ):
            reasons.append("actor does not own the source")
        if permission.get("owner_kind") or permission.get("owner_id"):
            if not ownership:
                reasons.append("source has no required owner")
            elif permission.get("owner_kind") and ownership["owner_kind"] != permission["owner_kind"]:
                reasons.append("source owner kind does not match")
            elif permission.get("owner_id") and ownership["owner_id"] != permission["owner_id"]:
                reasons.append("source owner does not match")
        relationship_target = permission.get("relationship_target_id")
        if relationship_target:
            relation = db.execute(
                "SELECT trust FROM relationships WHERE campaign_id=? AND source_id=? AND target_id=?",
                (campaign_id, actor_id, relationship_target),
            ).fetchone()
            minimum = float(permission.get("minimum_trust", 0))
            if not relation or float(relation["trust"]) < minimum:
                reasons.append("relationship permission is not satisfied")
        return not reasons, reasons

    def _visibility_allowed_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        actor_kind: str,
        actor_id: str,
        candidate: Mapping[str, Any],
    ) -> bool:
        visibility = str(candidate.get("visibility") or "private")
        if visibility == "public":
            return True
        source_kind = str(candidate.get("source_kind") or "global")
        source_id = candidate.get("source_id")
        permission = dict(candidate.get("permission") or {})
        actor_key = self._actor_key(actor_kind, actor_id)
        explicitly_allowed = actor_key in set(permission.get("actor_keys") or []) or actor_id in set(
            permission.get("actor_keys") or []
        )
        if visibility == "actor":
            return explicitly_allowed or (source_kind == actor_kind and source_id == actor_id)
        if visibility == "private":
            return explicitly_allowed or bool(permission.get("requires_owned_by_actor"))
        if visibility == "undiscovered":
            if explicitly_allowed:
                return True
            if not source_id or not self._table_exists_db(db, "discoverables"):
                return False
            return db.execute(
                "SELECT 1 FROM discoverables WHERE campaign_id=? AND id=? AND revealed=1",
                (campaign_id, source_id),
            ).fetchone() is not None
        return False

    def _beliefs_eligible_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        actor_kind: str,
        actor_id: str,
        requirements: list[Mapping[str, Any]],
    ) -> tuple[bool, float, list[str]]:
        believer_key = self._actor_key(actor_kind, actor_id)
        utility = 0.0
        reasons: list[str] = []
        for requirement in requirements:
            fact_id = str(requirement["fact_id"])
            row = db.execute(
                """SELECT belief_value_json,confidence,status FROM we4_beliefs
                   WHERE campaign_id=? AND believer_key=? AND fact_id=?""",
                (campaign_id, believer_key, fact_id),
            ).fetchone()
            if not row:
                reasons.append(f"missing belief: {fact_id}")
                continue
            confidence = _finite(row["confidence"], "stored belief confidence", minimum=0, maximum=1)
            if str(row["status"]) not in set(requirement.get("statuses") or ["believes"]):
                reasons.append(f"belief status not allowed: {fact_id}")
                continue
            if confidence < float(requirement.get("min_confidence", 0.5)):
                reasons.append(f"belief confidence too low: {fact_id}")
                continue
            if "value" in requirement:
                stored = self._loads(row["belief_value_json"], None)
                if _canonical(stored) != _canonical(requirement["value"]):
                    reasons.append(f"belief value does not match: {fact_id}")
                    continue
            utility += confidence * float(requirement.get("utility_weight", 0))
        return not reasons, utility, reasons

    def _personality_db(self, db: sqlite3.Connection, campaign_id: str, actor_kind: str, actor_id: str) -> dict[str, float]:
        rows = db.execute(
            """SELECT value_key,weight FROM agency_personality_values
               WHERE campaign_id=? AND actor_kind=? AND actor_id=? ORDER BY value_key""",
            (campaign_id, actor_kind, actor_id),
        ).fetchall()
        return {
            str(row["value_key"]): _finite(row["weight"], "stored personality weight", minimum=-10, maximum=10)
            for row in rows
        }

    def _evaluate_candidate_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        actor_kind: str,
        actor_id: str,
        candidate: Mapping[str, Any],
    ) -> dict[str, Any]:
        actor = self._actor_db(db, campaign_id, actor_kind, actor_id)
        reasons: list[str] = []
        if str(actor["status"]) != "alive":
            reasons.append("actor is not alive")
        if actor_kind not in set(candidate.get("actor_kinds") or []):
            reasons.append("actor kind is not supported")
        source_kind = str(candidate.get("source_kind") or "global")
        source_id = candidate.get("source_id")
        if not self._source_exists_db(db, campaign_id, source_kind, source_id):
            reasons.append("affordance source no longer exists")
        if not self._visibility_allowed_db(db, campaign_id, actor_kind, actor_id, candidate):
            reasons.append("affordance is not visible")
        permitted, permission_reasons = self._permission_allowed_db(
            db, campaign_id, actor_kind, actor_id, actor, candidate
        )
        if not permitted:
            reasons.extend(permission_reasons)
        target_location = candidate.get("location_id") or self._source_location_db(
            db, campaign_id, source_kind, source_id
        )
        travel_hours = 0.0
        if target_location and str(target_location) != str(actor["location"]):
            maximum = float((candidate.get("permission") or {}).get("max_travel_hours", 0))
            distance = self._shortest_hours_db(
                db, campaign_id, str(actor["location"]), str(target_location), maximum
            )
            if distance is None:
                reasons.append("affordance location is not reachable")
            else:
                travel_hours = distance
        beliefs_ok, belief_utility, belief_reasons = self._beliefs_eligible_db(
            db,
            campaign_id,
            actor_kind,
            actor_id,
            list(candidate.get("belief_requirements") or []),
        )
        if not beliefs_ok:
            reasons.extend(belief_reasons)
        operator_id = str(candidate.get("operator_id") or "")
        try:
            mechanism = MechanismKernel(self.e)
            operator = mechanism._get_operator_db(db, campaign_id, operator_id)
            bindings = dict(candidate.get("bindings") or {})
            bindings["actor"] = {"kind": actor_kind, "id": actor_id}
            _context, mop_evaluation = mechanism._evaluate_db(db, campaign_id, operator, bindings)
            if not mop_evaluation["eligible"]:
                reasons.append("mechanism preconditions are not satisfied")
        except (KeyError, PermissionError, ValueError) as exc:
            reasons.append(f"mechanism validation failed: {type(exc).__name__}")
            operator = None
        utility = float(candidate.get("base_utility", 0)) + belief_utility
        if operator is not None:
            utility += float(operator.get("base_utility", 0))
        personality = self._personality_db(db, campaign_id, actor_kind, actor_id)
        for value_key, multiplier in dict(candidate.get("value_modifiers") or {}).items():
            utility += personality.get(value_key, 0.0) * float(multiplier)
        utility -= travel_hours
        return {
            "eligible": not reasons,
            "reasons": sorted(set(reasons)),
            "utility": round(utility, 8),
            "travel_hours": round(travel_hours, 8),
            "operator": operator,
        }

    def _provider_candidates_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        actor_kind: str,
        actor_id: str,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for name in sorted(self._providers):
            provider = self._providers[name]
            for raw in provider(db, campaign_id, actor_kind, actor_id):
                if not isinstance(raw, Mapping):
                    raise TypeError(f"agency provider {name} returned a non-object")
                candidate = dict(raw)
                candidate_id = _validate_id(candidate.get("id"), f"agency provider {name} id")
                if candidate_id in seen:
                    raise ValueError(f"duplicate affordance id from providers: {candidate_id}")
                seen.add(candidate_id)
                candidate["id"] = candidate_id
                candidate["operator_id"] = _validate_id(
                    candidate.get("operator_id"), f"agency provider {name} operator_id"
                )
                candidate.setdefault("provider", name)
                candidate.setdefault("source_kind", "global")
                candidate.setdefault("source_id", None)
                candidate.setdefault("location_id", None)
                candidate.setdefault("visibility", "private")
                candidate.setdefault("actor_kinds", ["character", "npc"])
                candidate.setdefault("bindings", {})
                candidate.setdefault("permission", {})
                candidate.setdefault("belief_requirements", [])
                candidate.setdefault("base_utility", 0.0)
                candidate.setdefault("value_modifiers", {})
                candidate.setdefault("metadata", {})
                candidate["provider"] = _validate_kind(candidate["provider"], "provider")
                candidate["source_kind"] = _validate_kind(candidate["source_kind"], "source_kind")
                if candidate["source_id"] is not None:
                    candidate["source_id"] = _validate_id(candidate["source_id"], "source_id")
                if candidate["location_id"] is not None:
                    candidate["location_id"] = _validate_id(candidate["location_id"], "location_id")
                candidate["visibility"] = str(candidate["visibility"]).strip().lower()
                if candidate["visibility"] not in _VISIBILITIES:
                    raise ValueError("provider affordance has invalid visibility")
                kinds = sorted({str(item).strip().lower() for item in candidate["actor_kinds"]})
                if not kinds or any(item not in _ACTOR_TABLES for item in kinds):
                    raise ValueError("provider affordance has invalid actor_kinds")
                candidate["actor_kinds"] = kinds
                candidate["bindings"] = _json_safe(candidate["bindings"], "provider bindings")
                candidate["permission"] = self._normalize_permission(candidate["permission"])
                candidate["belief_requirements"] = self._normalize_belief_requirements(
                    candidate["belief_requirements"]
                )
                candidate["base_utility"] = _finite(
                    candidate["base_utility"], "provider base_utility", minimum=-1_000, maximum=1_000
                )
                candidate["value_modifiers"] = _json_safe(
                    candidate["value_modifiers"], "provider value_modifiers"
                )
                for value_key, multiplier in candidate["value_modifiers"].items():
                    _validate_kind(value_key, "provider value modifier key")
                    candidate["value_modifiers"][value_key] = _finite(
                        multiplier,
                        f"provider value modifier {value_key}",
                        minimum=-100,
                        maximum=100,
                    )
                candidate["metadata"] = _json_safe(candidate["metadata"], "provider metadata")
                candidates.append(candidate)
                if len(candidates) > MAX_AFFORDANCES:
                    raise ValueError("too many affordances returned by providers")
        return candidates

    def _discover_affordances_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        actor_kind: str,
        actor_id: str,
        *,
        include_private: bool = True,
    ) -> list[dict[str, Any]]:
        self._actor_db(db, campaign_id, actor_kind, actor_id)
        available: list[dict[str, Any]] = []
        for candidate in self._provider_candidates_db(db, campaign_id, actor_kind, actor_id):
            if not include_private and candidate.get("visibility") != "public":
                continue
            evaluation = self._evaluate_candidate_db(db, campaign_id, actor_kind, actor_id, candidate)
            if not evaluation["eligible"]:
                continue
            bindings = dict(candidate.get("bindings") or {})
            bindings["actor"] = {"kind": actor_kind, "id": actor_id}
            available.append(
                {
                    **candidate,
                    "bindings": bindings,
                    "utility": evaluation["utility"],
                    "travel_hours": evaluation["travel_hours"],
                    "operator": evaluation["operator"],
                }
            )
        available.sort(key=lambda item: (-float(item["utility"]), str(item["id"])))
        return available

    def discover_affordances(
        self,
        campaign_id: str,
        actor_kind: str,
        actor_id: str,
    ) -> list[dict[str, Any]]:
        with self.e._db() as db:
            values = self._discover_affordances_db(db, campaign_id, actor_kind, actor_id)
        return [self._private_affordance_projection(item) for item in values]

    def evaluate_affordance(
        self,
        campaign_id: str,
        actor_kind: str,
        actor_id: str,
        affordance_id: str,
    ) -> dict[str, Any]:
        affordance_id = _validate_id(affordance_id, "affordance_id")
        with self.e._db() as db:
            candidate = next(
                (item for item in self._provider_candidates_db(db, campaign_id, actor_kind, actor_id) if item["id"] == affordance_id),
                None,
            )
            if candidate is None:
                raise KeyError(f"unknown affordance: {affordance_id}")
            result = self._evaluate_candidate_db(db, campaign_id, actor_kind, actor_id, candidate)
        return {
            "campaign_id": campaign_id,
            "actor": {"kind": actor_kind, "id": actor_id},
            "affordance_id": affordance_id,
            "eligible": result["eligible"],
            "reasons": result["reasons"],
            "utility": result["utility"],
            "travel_hours": result["travel_hours"],
        }

    @staticmethod
    def _private_affordance_projection(item: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": item["id"],
            "operator_id": item["operator_id"],
            "source": {"kind": item.get("source_kind"), "id": item.get("source_id")},
            "location_id": item.get("location_id"),
            "visibility": item.get("visibility"),
            "bindings": _json_safe(item.get("bindings") or {}, "bindings"),
            "utility": float(item.get("utility", 0)),
            "travel_hours": float(item.get("travel_hours", 0)),
            "metadata": _json_safe(item.get("metadata") or {}, "metadata"),
        }

    def _world_time_db(self, db: sqlite3.Connection, campaign_id: str) -> str:
        row = db.execute("SELECT world_time FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if not row:
            raise KeyError(f"unknown campaign: {campaign_id}")
        return str(row["world_time"])

    def _save_goal_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        goal_id: str,
        actor_kind: str,
        actor_id: str,
        desired_state: Mapping[str, Any],
        *,
        description: str = "",
        initial_state: Mapping[str, Any] | None = None,
        priority: float = 0,
        status: str = "active",
        source_kind: str = "authored",
        source_id: str | None = None,
        visibility: str = "private",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        goal_id = _validate_id(goal_id, "goal_id")
        actor_kind = str(actor_kind).lower()
        actor_id = _validate_id(actor_id, "actor_id")
        self._actor_db(db, campaign_id, actor_kind, actor_id)
        desired = _json_safe(dict(desired_state), "desired_state")
        if not desired:
            raise ValueError("desired_state must not be empty")
        initial = _json_safe(dict(initial_state or {}), "initial_state")
        priority_value = _finite(priority, "priority", minimum=-1_000, maximum=1_000)
        status = str(status).lower()
        if status not in _GOAL_STATUSES:
            raise ValueError(f"invalid goal status: {status}")
        source_kind = _validate_kind(source_kind, "source_kind")
        source_id = _validate_id(source_id, "source_id") if source_id is not None else None
        visibility = str(visibility).lower()
        if visibility not in {"public", "private"}:
            raise ValueError("goal visibility must be public or private")
        now = self.e._now()
        world_time = self._world_time_db(db, campaign_id)
        db.execute(
            """INSERT INTO agency_goals(
                   campaign_id,id,actor_kind,actor_id,description,desired_state_json,initial_state_json,
                   priority,status,source_kind,source_id,visibility,metadata_json,created_world_time,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(campaign_id,id) DO UPDATE SET
                   actor_kind=excluded.actor_kind,actor_id=excluded.actor_id,description=excluded.description,
                   desired_state_json=excluded.desired_state_json,initial_state_json=excluded.initial_state_json,
                   priority=excluded.priority,status=excluded.status,source_kind=excluded.source_kind,
                   source_id=excluded.source_id,visibility=excluded.visibility,
                   metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
            (
                campaign_id,
                goal_id,
                actor_kind,
                actor_id,
                str(description)[:1_000],
                self.e._dumps(desired),
                self.e._dumps(initial),
                priority_value,
                status,
                source_kind,
                source_id,
                visibility,
                self.e._dumps(_json_safe(dict(metadata or {}), "metadata")),
                world_time,
                now,
            ),
        )
        return self._decode_goal(
            db.execute(
                "SELECT * FROM agency_goals WHERE campaign_id=? AND id=?",
                (campaign_id, goal_id),
            ).fetchone()
        )

    def save_goal(
        self,
        campaign_id: str,
        goal_id: str,
        actor_kind: str,
        actor_id: str,
        desired_state: Mapping[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        with self.e._write_db() as db:
            goal = self._save_goal_db(
                db, campaign_id, goal_id, actor_kind, actor_id, desired_state, **kwargs
            )
            revision = self.e._next_revision(db, campaign_id)
            event_id = self.e._insert_event(
                db,
                campaign_id,
                revision,
                "agency_goal_saved",
                f"Agency goal saved: {goal_id}",
                actor_id=actor_id,
                payload={"goal_id": goal_id, "actor_kind": actor_kind},
            )
        return {**goal, "revision": revision, "event_id": event_id}

    def _decode_goal(self, row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for field in ("desired_state", "initial_state", "metadata"):
            result[field] = self._loads(result.pop(field + "_json"), {})
        result["priority"] = _finite(result["priority"], "stored goal priority", minimum=-1_000, maximum=1_000)
        return result

    def _belief_state_db(self, db: sqlite3.Connection, campaign_id: str, actor_kind: str, actor_id: str) -> dict[str, Any]:
        rows = db.execute(
            """SELECT fact_id,belief_value_json,confidence,status FROM we4_beliefs
               WHERE campaign_id=? AND believer_key=? ORDER BY fact_id""",
            (campaign_id, self._actor_key(actor_kind, actor_id)),
        ).fetchall()
        state: dict[str, Any] = {}
        for row in rows:
            confidence = _finite(row["confidence"], "stored belief confidence", minimum=0, maximum=1)
            if row["status"] != "believes" or confidence < 0.5:
                continue
            value = self._loads(row["belief_value_json"], None)
            state[str(row["fact_id"])] = value
            state[f"belief:{row['fact_id']}"] = value
        return state

    def _create_plan_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        goal_id: str,
        *,
        replan_count: int = 0,
    ) -> dict[str, Any]:
        row = db.execute(
            "SELECT * FROM agency_goals WHERE campaign_id=? AND id=?",
            (campaign_id, goal_id),
        ).fetchone()
        if not row:
            raise KeyError(f"unknown agency goal: {goal_id}")
        goal = self._decode_goal(row)
        if goal["status"] != "active":
            raise ValueError("only an active goal can be planned")
        actor_kind, actor_id = str(goal["actor_kind"]), str(goal["actor_id"])
        state = self._belief_state_db(db, campaign_id, actor_kind, actor_id)
        # Trusted authoring may supply non-epistemic symbolic state. It never
        # replaces a structured belief with a legacy or world-fact value.
        known_fact_ids = {
            str(fact["fact_id"])
            for fact in db.execute(
                "SELECT fact_id FROM we4_facts WHERE campaign_id=?", (campaign_id,)
            ).fetchall()
        }
        for key, value in goal["initial_state"].items():
            fact_key = key.split(":", 1)[1] if key.startswith("belief:") else key
            if actor_kind == "npc" and fact_key in known_fact_ids and key not in state:
                continue
            if key not in state:
                state[key] = value
        available = self._discover_affordances_db(db, campaign_id, actor_kind, actor_id)
        planning_actions: list[dict[str, Any]] = []
        by_id: dict[str, dict[str, Any]] = {}
        for candidate in available:
            operator = candidate.get("operator") or {}
            effects = dict(operator.get("planning_effects") or {})
            if not effects:
                continue
            action = {
                "id": candidate["id"],
                "preconditions": dict(operator.get("planning_preconditions") or {}),
                "effects": effects,
                "cost": max(0.0, float(operator.get("cost_hours", 0))),
            }
            planning_actions.append(action)
            by_id[str(candidate["id"])] = candidate
        result = NpcLifeKernel.plan(
            state,
            dict(goal["desired_state"]),
            planning_actions,
            max_depth=MAX_PLAN_DEPTH,
            max_expanded=MAX_PLAN_EXPANDED,
        )
        if not result.get("found"):
            raise ValueError("no eligible bounded plan satisfies the goal")
        plan_affordances = [str(item) for item in result.get("plan") or []]
        material = {
            "actor": [actor_kind, actor_id],
            "goal_id": goal_id,
            "state": state,
            "desired": goal["desired_state"],
            "affordances": [
                {
                    "id": item,
                    "operator_id": by_id[item]["operator_id"],
                    "bindings": by_id[item]["bindings"],
                }
                for item in plan_affordances
            ],
            "replan_count": replan_count,
        }
        plan_digest = _digest(material)
        plan_id = _validate_id(f"plan:{goal_id}:{plan_digest[:20]}", "plan_id")
        existing = db.execute(
            "SELECT * FROM agency_plans WHERE campaign_id=? AND id=?",
            (campaign_id, plan_id),
        ).fetchone()
        if existing:
            return self._decode_plan_db(db, existing)
        db.execute(
            """UPDATE agency_plans SET status='replanned',updated_at=?
               WHERE campaign_id=? AND goal_id=? AND status='active'""",
            (self.e._now(), campaign_id, goal_id),
        )
        world_time = self._world_time_db(db, campaign_id)
        now = self.e._now()
        db.execute(
            """INSERT INTO agency_plans(
                   campaign_id,id,actor_kind,actor_id,goal_id,status,current_step,replan_count,
                   plan_digest,planner_json,created_world_time,updated_at)
               VALUES(?,?,?,?,?,'active',0,?,?,?,?,?)""",
            (
                campaign_id,
                plan_id,
                actor_kind,
                actor_id,
                goal_id,
                replan_count,
                plan_digest,
                self.e._dumps(
                    {
                        "planner": "bounded_goap",
                        "expanded": int(result.get("expanded", 0)),
                        "depth": int(result.get("depth", len(plan_affordances))),
                        "cost": float(result.get("cost", 0)),
                    }
                ),
                world_time,
                now,
            ),
        )
        for index, affordance_id in enumerate(plan_affordances):
            candidate = by_id[affordance_id]
            db.execute(
                """INSERT INTO agency_plan_steps(
                       campaign_id,plan_id,step_index,affordance_id,operator_id,bindings_json,
                       status,attempts,execution_id,last_error,updated_at)
                   VALUES(?,?,?,?,?,?,'pending',0,NULL,NULL,?)""",
                (
                    campaign_id,
                    plan_id,
                    index,
                    affordance_id,
                    candidate["operator_id"],
                    self.e._dumps(candidate["bindings"]),
                    now,
                ),
            )
        return self._decode_plan_db(
            db,
            db.execute(
                "SELECT * FROM agency_plans WHERE campaign_id=? AND id=?",
                (campaign_id, plan_id),
            ).fetchone(),
        )

    def create_plan(self, campaign_id: str, goal_id: str) -> dict[str, Any]:
        with self.e._write_db() as db:
            plan = self._create_plan_db(db, campaign_id, goal_id)
            revision = self.e._next_revision(db, campaign_id)
            event_id = self.e._insert_event(
                db,
                campaign_id,
                revision,
                "agency_plan_created",
                f"Agency plan created: {plan['id']}",
                actor_id=plan["actor_id"],
                payload={"plan_id": plan["id"], "goal_id": goal_id, "step_count": len(plan["steps"])},
            )
        return {**plan, "revision": revision, "event_id": event_id}

    def _decode_plan_db(self, db: sqlite3.Connection, row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["planner"] = self._loads(result.pop("planner_json"), {})
        steps = db.execute(
            """SELECT * FROM agency_plan_steps WHERE campaign_id=? AND plan_id=?
               ORDER BY step_index""",
            (result["campaign_id"], result["id"]),
        ).fetchall()
        result["steps"] = []
        for raw in steps:
            step = dict(raw)
            step["bindings"] = self._loads(step.pop("bindings_json"), {})
            result["steps"].append(step)
        return result

    def replan(self, campaign_id: str, plan_id: str) -> dict[str, Any]:
        plan_id = _validate_id(plan_id, "plan_id")
        with self.e._write_db() as db:
            row = db.execute(
                "SELECT * FROM agency_plans WHERE campaign_id=? AND id=?",
                (campaign_id, plan_id),
            ).fetchone()
            if not row:
                raise KeyError(f"unknown plan: {plan_id}")
            count = min(100, int(row["replan_count"]) + 1)
            db.execute(
                "UPDATE agency_plans SET status='replanned',updated_at=? WHERE campaign_id=? AND id=?",
                (self.e._now(), campaign_id, plan_id),
            )
            db.execute(
                """UPDATE agency_plan_steps SET status='skipped',updated_at=?
                   WHERE campaign_id=? AND plan_id=? AND status='pending'""",
                (self.e._now(), campaign_id, plan_id),
            )
            plan = self._create_plan_db(db, campaign_id, str(row["goal_id"]), replan_count=count)
            revision = self.e._next_revision(db, campaign_id)
            event_id = self.e._insert_event(
                db,
                campaign_id,
                revision,
                "agency_plan_replanned",
                f"Agency plan replaced: {plan_id}",
                actor_id=plan["actor_id"],
                payload={"old_plan_id": plan_id, "new_plan_id": plan["id"]},
            )
        return {**plan, "revision": revision, "event_id": event_id}

    def _resolve_executor(self) -> Callable[..., dict[str, Any]] | None:
        if self.operator_executor_db is not None:
            return self.operator_executor_db
        candidate = getattr(MechanismKernel(self.e), "execute_operator_db", None)
        return candidate if callable(candidate) else None

    def _execute_next_step_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        plan_id: str,
        revision: int,
        *,
        world_time: str | None = None,
    ) -> dict[str, Any]:
        row = db.execute(
            "SELECT * FROM agency_plans WHERE campaign_id=? AND id=?",
            (campaign_id, plan_id),
        ).fetchone()
        if not row:
            raise KeyError(f"unknown plan: {plan_id}")
        if row["status"] != "active":
            raise ValueError("plan is not active")
        step = db.execute(
            """SELECT * FROM agency_plan_steps WHERE campaign_id=? AND plan_id=? AND status='pending'
               ORDER BY step_index LIMIT 1""",
            (campaign_id, plan_id),
        ).fetchone()
        if not step:
            db.execute(
                "UPDATE agency_plans SET status='completed',updated_at=? WHERE campaign_id=? AND id=?",
                (self.e._now(), campaign_id, plan_id),
            )
            db.execute(
                "UPDATE agency_goals SET status='completed',updated_at=? WHERE campaign_id=? AND id=?",
                (self.e._now(), campaign_id, row["goal_id"]),
            )
            return {"status": "completed", "plan_id": plan_id, "execution_id": None}
        available = {
            item["id"]: item
            for item in self._discover_affordances_db(
                db, campaign_id, str(row["actor_kind"]), str(row["actor_id"])
            )
        }
        if step["affordance_id"] not in available:
            db.execute(
                "UPDATE agency_plans SET status='failed',updated_at=? WHERE campaign_id=? AND id=?",
                (self.e._now(), campaign_id, plan_id),
            )
            db.execute(
                """UPDATE agency_plan_steps SET status='failed',attempts=attempts+1,
                   last_error='affordance no longer eligible',updated_at=?
                   WHERE campaign_id=? AND plan_id=? AND step_index=?""",
                (self.e._now(), campaign_id, plan_id, step["step_index"]),
            )
            return {"status": "needs_replan", "plan_id": plan_id, "execution_id": None}
        executor = self._resolve_executor()
        if executor is None:
            return {"status": "blocked_executor", "plan_id": plan_id, "execution_id": None}
        bindings = self._loads(step["bindings_json"], {})
        key = _validate_id(f"plan:{plan_id}:{step['step_index']}", "idempotency_key")
        db.execute("SAVEPOINT agency_operator_execution")
        try:
            execution = executor(
                db=db,
                campaign_id=campaign_id,
                operator_id=str(step["operator_id"]),
                bindings=bindings,
                revision=int(revision),
                idempotency_key=key,
                world_time=world_time,
                execution_scope="agency",
                step_identity=f"{plan_id}:{step['step_index']}",
            )
            if not isinstance(execution, Mapping) or not execution.get("executed", False):
                raise ValueError("operator executor did not return an executed result")
            db.execute("RELEASE SAVEPOINT agency_operator_execution")
        except Exception as exc:  # noqa: BLE001 - trusted executor failures must roll back the savepoint
            db.execute("ROLLBACK TO SAVEPOINT agency_operator_execution")
            db.execute("RELEASE SAVEPOINT agency_operator_execution")
            db.execute(
                """UPDATE agency_plan_steps SET status='failed',attempts=attempts+1,last_error=?,updated_at=?
                   WHERE campaign_id=? AND plan_id=? AND step_index=?""",
                (type(exc).__name__, self.e._now(), campaign_id, plan_id, step["step_index"]),
            )
            db.execute(
                "UPDATE agency_plans SET status='failed',updated_at=? WHERE campaign_id=? AND id=?",
                (self.e._now(), campaign_id, plan_id),
            )
            return {"status": "failed", "plan_id": plan_id, "execution_id": None, "error": type(exc).__name__}
        execution_id = str(execution.get("execution_id") or "") or None
        now = self.e._now()
        db.execute(
            """UPDATE agency_plan_steps SET status='completed',attempts=attempts+1,
               execution_id=?,last_error=NULL,updated_at=?
               WHERE campaign_id=? AND plan_id=? AND step_index=?""",
            (execution_id, now, campaign_id, plan_id, step["step_index"]),
        )
        next_index = int(step["step_index"]) + 1
        pending = db.execute(
            "SELECT 1 FROM agency_plan_steps WHERE campaign_id=? AND plan_id=? AND status='pending' LIMIT 1",
            (campaign_id, plan_id),
        ).fetchone()
        status = "active" if pending else "completed"
        db.execute(
            "UPDATE agency_plans SET current_step=?,status=?,updated_at=? WHERE campaign_id=? AND id=?",
            (next_index, status, now, campaign_id, plan_id),
        )
        if status == "completed":
            db.execute(
                "UPDATE agency_goals SET status='completed',updated_at=? WHERE campaign_id=? AND id=?",
                (now, campaign_id, row["goal_id"]),
            )
        return {
            "status": status,
            "plan_id": plan_id,
            "step_index": int(step["step_index"]),
            "operator_id": str(step["operator_id"]),
            "execution_id": execution_id,
        }

    def execute_next_step(self, campaign_id: str, plan_id: str) -> dict[str, Any]:
        plan_id = _validate_id(plan_id, "plan_id")
        if self._resolve_executor() is None:
            raise RuntimeError("agency plan execution requires a transaction-aware MOP executor")
        with self.e._write_db() as db:
            revision = self.e._next_revision(db, campaign_id)
            result = self._execute_next_step_db(
                db,
                campaign_id,
                plan_id,
                revision,
                world_time=self._world_time_db(db, campaign_id),
            )
            self.e._insert_event(
                db,
                campaign_id,
                revision,
                "agency_plan_step",
                f"Agency plan step: {result['status']}",
                actor_id=plan_id,
                payload={key: value for key, value in result.items() if key != "error"},
            )
        return {**result, "revision": revision}

    def set_personality_value(
        self,
        campaign_id: str,
        actor_kind: str,
        actor_id: str,
        value_key: str,
        weight: float,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        actor_kind = str(actor_kind).lower()
        actor_id = _validate_id(actor_id, "actor_id")
        value_key = _validate_kind(value_key, "value_key")
        weight_value = _finite(weight, "weight", minimum=-10, maximum=10)
        with self.e._write_db() as db:
            self._actor_db(db, campaign_id, actor_kind, actor_id)
            db.execute(
                """INSERT INTO agency_personality_values(
                       campaign_id,actor_kind,actor_id,value_key,weight,metadata_json,updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,actor_kind,actor_id,value_key) DO UPDATE SET
                       weight=excluded.weight,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                (
                    campaign_id,
                    actor_kind,
                    actor_id,
                    value_key,
                    weight_value,
                    self.e._dumps(_json_safe(dict(metadata or {}), "metadata")),
                    self.e._now(),
                ),
            )
            revision = self.e._next_revision(db, campaign_id)
            self.e._insert_event(
                db,
                campaign_id,
                revision,
                "agency_personality_value",
                f"Personality value updated: {value_key}",
                actor_id=actor_id,
                payload={"actor_kind": actor_kind, "value_key": value_key},
            )
        return {
            "campaign_id": campaign_id,
            "actor": {"kind": actor_kind, "id": actor_id},
            "value_key": value_key,
            "weight": weight_value,
            "revision": revision,
        }

    def _event_perceived_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        actor_kind: str,
        actor_id: str,
        actor: sqlite3.Row,
        event: sqlite3.Row,
    ) -> bool:
        actor_key = self._actor_key(actor_kind, actor_id)
        sensitivity = str(event["sensitivity"] or "PUBLIC").upper()
        scope_type = str(event["scope_type"] or "WORLD").upper()
        if scope_type == "ENTITY":
            if (
                str(event["principal_kind"] or "").lower() != actor_kind
                or str(event["principal_id"] or "") != actor_id
            ):
                return False
        elif scope_type != "WORLD":
            return False
        if sensitivity != "PUBLIC" and scope_type != "ENTITY":
            return False
        if str(event["actor_id"] or "") in {actor_id, actor_key} or str(event["target_id"] or "") in {
            actor_id,
            actor_key,
        }:
            return True
        payload = self._loads(event["payload_json"], {})
        if not isinstance(payload, Mapping) or payload.get("perceivable") is False:
            return False
        visible_to = {str(item) for item in payload.get("visible_to", [])} if isinstance(payload.get("visible_to", []), list) else set()
        if actor_key in visible_to or actor_id in visible_to:
            return True
        visibility = str(payload.get("visibility", "local")).lower()
        if visibility in {"private", "secret", "none"}:
            return False
        if visibility == "public" and event["region"] is None:
            return True
        return event["region"] is not None and str(event["region"]) == str(actor["location"])

    def _goal_appraisal_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        actor_kind: str,
        actor_id: str,
        event_type: str,
    ) -> tuple[float, list[str]]:
        rows = db.execute(
            """SELECT id,desired_state_json FROM agency_goals
               WHERE campaign_id=? AND actor_kind=? AND actor_id=? AND status='active'
               ORDER BY priority DESC,id LIMIT 32""",
            (campaign_id, actor_kind, actor_id),
        ).fetchall()
        score = 0.0
        matches: list[str] = []
        for row in rows:
            desired = self._loads(row["desired_state_json"], {})
            positive = {str(item) for item in desired.get("success_event_types", [])} if isinstance(desired, Mapping) else set()
            negative = {str(item) for item in desired.get("failure_event_types", [])} if isinstance(desired, Mapping) else set()
            if event_type in positive:
                score += 0.45
                matches.append(str(row["id"]))
            if event_type in negative:
                score -= 0.55
                matches.append(str(row["id"]))
        return max(-1.0, min(1.0, score)), matches

    def _appraise_event_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        actor_kind: str,
        actor_id: str,
        event_id: int,
        *,
        when: str | datetime | None = None,
    ) -> dict[str, Any]:
        actor = self._actor_db(db, campaign_id, actor_kind, actor_id)
        event = db.execute(
            "SELECT * FROM events WHERE campaign_id=? AND id=?",
            (campaign_id, int(event_id)),
        ).fetchone()
        if not event:
            raise KeyError(f"unknown event: {event_id}")
        if not self._event_perceived_db(db, campaign_id, actor_kind, actor_id, actor, event):
            return {
                "perceived": False,
                "already_appraised": False,
                "event_id": int(event_id),
                "emotion": None,
                "memory_created": False,
            }
        existing = db.execute(
            """SELECT emotion_type,valence,intensity FROM agency_emotions
               WHERE campaign_id=? AND actor_kind=? AND actor_id=? AND event_id=?
               ORDER BY intensity DESC,emotion_type LIMIT 1""",
            (campaign_id, actor_kind, actor_id, int(event_id)),
        ).fetchone()
        if existing:
            return {
                "perceived": True,
                "already_appraised": True,
                "event_id": int(event_id),
                "emotion": {
                    "type": str(existing["emotion_type"]),
                    "valence": float(existing["valence"]),
                    "intensity": float(existing["intensity"]),
                },
                "memory_created": False,
            }
        payload = self._loads(event["payload_json"], {})
        event_type = str(event["event_type"]).lower()
        positive_tokens = ("success", "help", "gain", "birth", "rescue", "gift", "victory")
        negative_tokens = ("death", "loss", "damage", "fail", "threat", "attack", "shortage")
        lexical = 0.0
        if any(token in event_type for token in positive_tokens):
            lexical += 0.35
        if any(token in event_type for token in negative_tokens):
            lexical -= 0.45
        payload_valence = 0.0
        if isinstance(payload, Mapping) and "valence" in payload:
            payload_valence = _finite(payload["valence"], "event payload valence", minimum=-1, maximum=1)
        goal_score, goal_matches = self._goal_appraisal_db(
            db, campaign_id, actor_kind, actor_id, str(event["event_type"])
        )
        relationship_score = 0.0
        related_id = str(event["target_id"] or event["actor_id"] or "")
        if related_id and related_id not in {actor_id, self._actor_key(actor_kind, actor_id)}:
            relation = db.execute(
                """SELECT trust,affection,fear FROM relationships
                   WHERE campaign_id=? AND source_id=? AND target_id=?""",
                (campaign_id, actor_id, related_id.split(":", 1)[-1]),
            ).fetchone()
            if relation:
                affinity = (float(relation["trust"]) + float(relation["affection"]) - float(relation["fear"])) / 300.0
                relationship_score = max(-0.35, min(0.35, affinity * (lexical or payload_valence or -0.25)))
        personality = self._personality_db(db, campaign_id, actor_kind, actor_id)
        sensitivity = max(0.25, min(2.0, 1.0 + personality.get("emotional_sensitivity", 0) / 10.0))
        loss_aversion = max(0.5, min(2.0, 1.0 + personality.get("loss_aversion", 0) / 10.0))
        valence = max(-1.0, min(1.0, lexical + payload_valence + goal_score + relationship_score))
        if valence < 0:
            valence = max(-1.0, valence * loss_aversion)
        direct = str(event["actor_id"] or "") in {actor_id, self._actor_key(actor_kind, actor_id)} or str(
            event["target_id"] or ""
        ) in {actor_id, self._actor_key(actor_kind, actor_id)}
        relevance = min(1.0, 0.35 + (0.35 if direct else 0) + min(0.3, abs(goal_score) + abs(relationship_score)))
        intensity = min(1.0, abs(valence) * relevance * sensitivity)
        if intensity <= 1e-9:
            return {
                "perceived": True,
                "already_appraised": False,
                "event_id": int(event_id),
                "emotion": None,
                "memory_created": False,
            }
        if valence >= 0:
            emotion_type = "satisfaction" if goal_score > 0 else "joy"
        elif "death" in event_type or "loss" in event_type:
            emotion_type = "sadness"
        elif "attack" in event_type or "damage" in event_type:
            emotion_type = "anger" if relationship_score < 0 else "fear"
        else:
            emotion_type = "distress"
        if emotion_type not in _EMOTION_TYPES:
            raise AssertionError("unreachable emotion type")
        event_time = _utc(when or event["world_time"]).isoformat()
        appraisal = {
            "goal_matches": goal_matches,
            "goal_score": round(goal_score, 8),
            "relationship_score": round(relationship_score, 8),
            "relevance": round(relevance, 8),
        }
        now = self.e._now()
        db.execute(
            """INSERT INTO agency_emotions(
                   campaign_id,actor_kind,actor_id,event_id,emotion_type,valence,arousal,intensity,
                   decay_per_day,appraisal_json,active,created_world_time,last_updated_world_time,updated_at)
               VALUES(?,?,?,?,?,?,?,?,0.1,?,1,?,?,?)
               ON CONFLICT(campaign_id,actor_kind,actor_id,event_id,emotion_type) DO UPDATE SET
                   valence=excluded.valence,arousal=excluded.arousal,intensity=excluded.intensity,
                   appraisal_json=excluded.appraisal_json,active=1,
                   last_updated_world_time=excluded.last_updated_world_time,updated_at=excluded.updated_at""",
            (
                campaign_id,
                actor_kind,
                actor_id,
                int(event_id),
                emotion_type,
                round(valence, 12),
                round(min(1.0, intensity + (0.15 if direct else 0)), 12),
                round(intensity, 12),
                self.e._dumps(appraisal),
                event_time,
                event_time,
                now,
            ),
        )
        importance = min(1.0, relevance * 0.6 + intensity * 0.4)
        memory_created = importance >= 0.25
        if memory_created:
            db.execute(
                """INSERT INTO agency_memories(
                       campaign_id,actor_kind,actor_id,event_id,importance,decay_per_day,
                       appraisal_json,created_world_time,last_recalled_world_time,updated_at)
                   VALUES(?,?,?,?,?,0.002,?,?,NULL,?)
                   ON CONFLICT(campaign_id,actor_kind,actor_id,event_id) DO UPDATE SET
                       importance=MAX(agency_memories.importance,excluded.importance),
                       appraisal_json=excluded.appraisal_json,updated_at=excluded.updated_at""",
                (
                    campaign_id,
                    actor_kind,
                    actor_id,
                    int(event_id),
                    round(importance, 12),
                    self.e._dumps({**appraisal, "emotion_type": emotion_type, "valence": round(valence, 8)}),
                    event_time,
                    now,
                ),
            )
        return {
            "perceived": True,
            "already_appraised": False,
            "event_id": int(event_id),
            "emotion": {
                "type": emotion_type,
                "valence": round(valence, 8),
                "intensity": round(intensity, 8),
            },
            "memory_created": memory_created,
        }

    def appraise_event(
        self,
        campaign_id: str,
        actor_kind: str,
        actor_id: str,
        event_id: int,
    ) -> dict[str, Any]:
        with self.e._write_db() as db:
            result = self._appraise_event_db(db, campaign_id, actor_kind, actor_id, int(event_id))
        return result

    def _decay_emotions_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        actor_kind: str,
        actor_id: str,
        when: str | datetime,
    ) -> int:
        boundary = _utc(when)
        rows = db.execute(
            """SELECT * FROM agency_emotions
               WHERE campaign_id=? AND actor_kind=? AND actor_id=? AND active=1
               ORDER BY event_id,emotion_type""",
            (campaign_id, actor_kind, actor_id),
        ).fetchall()
        changed = 0
        for row in rows:
            previous = _utc(str(row["last_updated_world_time"]))
            if boundary < previous:
                raise ValueError("agency emotion decay cannot move backwards in world time")
            days = (boundary - previous).total_seconds() / 86_400.0
            old = _finite(row["intensity"], "stored emotion intensity", minimum=0, maximum=1)
            rate = _finite(row["decay_per_day"], "stored emotion decay", minimum=0, maximum=1)
            value = max(0.0, old - rate * days)
            active = int(value > 1e-9)
            if days > 0:
                db.execute(
                    """UPDATE agency_emotions SET intensity=?,active=?,last_updated_world_time=?,updated_at=?
                       WHERE campaign_id=? AND actor_kind=? AND actor_id=? AND event_id=? AND emotion_type=?""",
                    (
                        round(value, 12),
                        active,
                        boundary.isoformat(),
                        self.e._now(),
                        campaign_id,
                        actor_kind,
                        actor_id,
                        row["event_id"],
                        row["emotion_type"],
                    ),
                )
                changed += 1
        return changed

    def _recall_memories_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        actor_kind: str,
        actor_id: str,
        *,
        limit: int = 8,
        when: str | datetime | None = None,
        mark_recalled: bool = False,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), MAX_RECALL))
        boundary = _utc(when or self._world_time_db(db, campaign_id))
        rows = db.execute(
            """SELECT m.*,e.event_type,e.world_time FROM agency_memories m
               JOIN events e ON e.id=m.event_id AND e.campaign_id=m.campaign_id
               WHERE m.campaign_id=? AND m.actor_kind=? AND m.actor_id=?
               ORDER BY m.importance DESC,m.event_id DESC LIMIT 200""",
            (campaign_id, actor_kind, actor_id),
        ).fetchall()
        ranked: list[dict[str, Any]] = []
        for row in rows:
            age_days = max(0.0, (boundary - _utc(str(row["created_world_time"]))).total_seconds() / 86_400.0)
            importance = _finite(row["importance"], "stored memory importance", minimum=0, maximum=1)
            decay = _finite(row["decay_per_day"], "stored memory decay", minimum=0, maximum=1)
            salience = max(0.0, importance - decay * age_days)
            if salience <= 0:
                continue
            ranked.append(
                {
                    "event_id": int(row["event_id"]),
                    "event_type": str(row["event_type"]),
                    "event_world_time": str(row["world_time"]),
                    "importance": round(importance, 8),
                    "salience": round(salience, 8),
                    "appraisal": self._loads(row["appraisal_json"], {}),
                }
            )
        ranked.sort(key=lambda item: (-float(item["salience"]), -int(item["event_id"])))
        result = ranked[:limit]
        if mark_recalled and result:
            db.executemany(
                """UPDATE agency_memories SET last_recalled_world_time=?,updated_at=?
                   WHERE campaign_id=? AND actor_kind=? AND actor_id=? AND event_id=?""",
                [
                    (
                        boundary.isoformat(),
                        self.e._now(),
                        campaign_id,
                        actor_kind,
                        actor_id,
                        item["event_id"],
                    )
                    for item in result
                ],
            )
        return result

    def recall_memories(
        self,
        campaign_id: str,
        actor_kind: str,
        actor_id: str,
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        with self.e._write_db() as db:
            self._actor_db(db, campaign_id, actor_kind, actor_id)
            return self._recall_memories_db(
                db, campaign_id, actor_kind, actor_id, limit=limit, mark_recalled=True
            )

    def legacy_goal_candidates(self, campaign_id: str, npc_id: str) -> list[dict[str, Any]]:
        """Expose legacy strings only as non-authoritative migration suggestions."""

        with self.e._db() as db:
            npc = self._actor_db(db, campaign_id, "npc", npc_id)
            values = self._loads(npc["goals_json"], [])
        return [
            {
                "legacy_value": str(value),
                "authoritative": False,
                "requires_structured_goal": True,
            }
            for value in values[:32]
        ]

    def legacy_action_candidates(self, campaign_id: str, npc_id: str) -> list[dict[str, Any]]:
        """Return legacy actions only after a saved MOP adapter exists."""

        with self.e._db() as db:
            self._actor_db(db, campaign_id, "npc", npc_id)
            rows = db.execute(
                """SELECT id,source_id,metadata_json FROM mechanism_operators
                   WHERE campaign_id=? AND source_kind='npc' AND source_id=? AND enabled=1 ORDER BY id""",
                (campaign_id, npc_id),
            ).fetchall()
            result = []
            for row in rows:
                metadata = self._loads(row["metadata_json"], {})
                if metadata.get("adapter") != "npc_action_v440":
                    continue
                result.append(
                    {
                        "legacy_action_id": metadata.get("legacy_action_id"),
                        "operator_id": row["id"],
                        "authoritative": False,
                        "mop_required": True,
                    }
                )
            return result

    def _actor_rows_db(self, db: sqlite3.Connection, campaign_id: str) -> list[tuple[str, str]]:
        rows = db.execute(
            """SELECT actor_kind,actor_id FROM agency_actor_state WHERE campaign_id=?
               UNION SELECT actor_kind,actor_id FROM agency_goals WHERE campaign_id=? AND status='active'
               UNION SELECT actor_kind,actor_id FROM agency_plans WHERE campaign_id=? AND status='active'
               UNION SELECT actor_kind,actor_id FROM agency_emotions WHERE campaign_id=? AND active=1
               UNION SELECT actor_kind,actor_id FROM agency_personality_values WHERE campaign_id=?
               ORDER BY actor_kind,actor_id LIMIT ?""",
            (campaign_id, campaign_id, campaign_id, campaign_id, campaign_id, MAX_ACTIVE_ACTORS),
        ).fetchall()
        return [(str(row["actor_kind"]), str(row["actor_id"])) for row in rows]

    def has_activity_db(self, db: sqlite3.Connection, campaign_id: str) -> bool:
        return db.execute(
            """SELECT 1 FROM agency_goals WHERE campaign_id=? AND status='active'
               UNION ALL SELECT 1 FROM agency_emotions WHERE campaign_id=? AND active=1
               UNION ALL SELECT 1 FROM agency_personality_values WHERE campaign_id=?
               LIMIT 1""",
            (campaign_id, campaign_id, campaign_id),
        ).fetchone() is not None

    def step_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        revision: int,
        when: str | datetime,
        emit: Callable[..., Any],
    ) -> dict[str, int]:
        boundary = _utc(when)
        tally = {
            "actors": 0,
            "events_appraised": 0,
            "emotions_decayed": 0,
            "memories_created": 0,
            "plan_steps": 0,
            "plans_replanned": 0,
            "plans_requiring_replan": 0,
            "blocked_executor": 0,
        }
        max_event_row = db.execute(
            "SELECT COALESCE(MAX(id),0) AS id FROM events WHERE campaign_id=?",
            (campaign_id,),
        ).fetchone()
        maximum_event_id = int(max_event_row["id"])
        for actor_kind, actor_id in self._actor_rows_db(db, campaign_id):
            actor = self._actor_db(db, campaign_id, actor_kind, actor_id)
            tally["actors"] += 1
            state = db.execute(
                """SELECT * FROM agency_actor_state
                   WHERE campaign_id=? AND actor_kind=? AND actor_id=?""",
                (campaign_id, actor_kind, actor_id),
            ).fetchone()
            if state and state["last_step_world_time"]:
                last_boundary = _utc(str(state["last_step_world_time"]))
                if last_boundary == boundary:
                    continue
                if last_boundary > boundary:
                    raise ValueError("agency steps must be applied in chronological order")
            cursor = int(state["last_appraised_event_id"]) if state else 0
            events = db.execute(
                """SELECT * FROM events WHERE campaign_id=? AND id>? AND id<=? AND world_time<=?
                   ORDER BY id LIMIT ?""",
                (
                    campaign_id,
                    cursor,
                    maximum_event_id,
                    boundary.isoformat(),
                    MAX_EVENTS_PER_ACTOR_STEP,
                ),
            ).fetchall()
            last_seen = cursor
            for event in events:
                appraisal = self._appraise_event_db(
                    db,
                    campaign_id,
                    actor_kind,
                    actor_id,
                    int(event["id"]),
                    when=event["world_time"],
                )
                last_seen = int(event["id"])
                if (
                    not appraisal["perceived"]
                    or appraisal["already_appraised"]
                    or not appraisal["emotion"]
                ):
                    continue
                tally["events_appraised"] += 1
                tally["memories_created"] += int(bool(appraisal["memory_created"]))
                emit(
                    "agency_appraisal",
                    f"{actor_id} appraised event {event['id']}",
                    {
                        "actor_kind": actor_kind,
                        "actor_id": actor_id,
                        "source_event_id": int(event["id"]),
                        "emotion_type": appraisal["emotion"]["type"],
                        "perceivable": False,
                    },
                    str(actor["location"]),
                    boundary,
                    sensitivity="PRIVATE",
                    scope_type="ENTITY",
                    principal_kind=actor_kind,
                    principal_id=actor_id,
                    causal_parent_event_id=int(event["id"]),
                )
            # Decay after appraisal so catch-up to this boundary also ages
            # newly perceived events. Linear decay preserves chunk invariance.
            tally["emotions_decayed"] += self._decay_emotions_db(
                db, campaign_id, actor_kind, actor_id, boundary
            )
            db.execute(
                """INSERT INTO agency_actor_state(
                       campaign_id,actor_kind,actor_id,last_appraised_event_id,last_step_world_time,state_json,updated_at)
                   VALUES(?,?,?,?,?,'{}',?)
                   ON CONFLICT(campaign_id,actor_kind,actor_id) DO UPDATE SET
                       last_appraised_event_id=MAX(agency_actor_state.last_appraised_event_id,excluded.last_appraised_event_id),
                       last_step_world_time=excluded.last_step_world_time,updated_at=excluded.updated_at""",
                (campaign_id, actor_kind, actor_id, last_seen, boundary.isoformat(), self.e._now()),
            )
            plan = db.execute(
                """SELECT id FROM agency_plans
                   WHERE campaign_id=? AND actor_kind=? AND actor_id=? AND status='active'
                   ORDER BY created_world_time,id LIMIT 1""",
                (campaign_id, actor_kind, actor_id),
            ).fetchone()
            if plan:
                outcome = self._execute_next_step_db(
                    db,
                    campaign_id,
                    str(plan["id"]),
                    int(revision),
                    world_time=boundary.isoformat(),
                )
                if outcome["status"] == "blocked_executor":
                    tally["blocked_executor"] += 1
                elif outcome["status"] == "needs_replan":
                    old = db.execute(
                        "SELECT goal_id,replan_count FROM agency_plans WHERE campaign_id=? AND id=?",
                        (campaign_id, plan["id"]),
                    ).fetchone()
                    try:
                        replacement = self._create_plan_db(
                            db,
                            campaign_id,
                            str(old["goal_id"]),
                            replan_count=min(100, int(old["replan_count"]) + 1),
                        )
                    except (KeyError, ValueError):
                        tally["plans_requiring_replan"] += 1
                    else:
                        tally["plans_replanned"] += 1
                        emit(
                            "agency_plan_replanned",
                            f"{actor_id} replanned {plan['id']}",
                            {
                                "old_plan_id": plan["id"],
                                "new_plan_id": replacement["id"],
                                "perceivable": False,
                            },
                            str(actor["location"]),
                            boundary,
                            sensitivity="PRIVATE",
                            scope_type="ENTITY",
                            principal_kind=actor_kind,
                            principal_id=actor_id,
                        )
                elif outcome["status"] != "failed":
                    tally["plan_steps"] += 1
                    emit(
                        "agency_plan_step",
                        f"{actor_id} advanced plan {plan['id']}",
                        {
                            **{key: value for key, value in outcome.items() if key != "error"},
                            "perceivable": False,
                        },
                        str(actor["location"]),
                        boundary,
                        sensitivity="PRIVATE",
                        scope_type="ENTITY",
                        principal_kind=actor_kind,
                        principal_id=actor_id,
                    )
        return tally

    def _private_snapshot_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        actor_kind: str,
        actor_id: str,
    ) -> dict[str, Any]:
        actor = self._actor_db(db, campaign_id, actor_kind, actor_id)
        goals = [
            self._decode_goal(row)
            for row in db.execute(
                """SELECT * FROM agency_goals WHERE campaign_id=? AND actor_kind=? AND actor_id=?
                   ORDER BY status,priority DESC,id LIMIT 50""",
                (campaign_id, actor_kind, actor_id),
            ).fetchall()
        ]
        plans = [
            self._decode_plan_db(db, row)
            for row in db.execute(
                """SELECT * FROM agency_plans WHERE campaign_id=? AND actor_kind=? AND actor_id=?
                   ORDER BY created_world_time DESC,id DESC LIMIT 20""",
                (campaign_id, actor_kind, actor_id),
            ).fetchall()
        ]
        emotions = []
        for row in db.execute(
            """SELECT event_id,emotion_type,valence,arousal,intensity,active,appraisal_json
               FROM agency_emotions WHERE campaign_id=? AND actor_kind=? AND actor_id=?
               ORDER BY active DESC,intensity DESC,event_id DESC LIMIT 20""",
            (campaign_id, actor_kind, actor_id),
        ).fetchall():
            item = dict(row)
            item["active"] = bool(item["active"])
            item["appraisal"] = self._loads(item.pop("appraisal_json"), {})
            emotions.append(item)
        return {
            "contract_version": AGENCY_CONTRACT_VERSION,
            "actor": {"kind": actor_kind, "id": actor_id, "location": actor["location"]},
            "affordances": [
                self._private_affordance_projection(item)
                for item in self._discover_affordances_db(db, campaign_id, actor_kind, actor_id)
            ],
            "goals": goals,
            "plans": plans,
            "personality_values": self._personality_db(db, campaign_id, actor_kind, actor_id),
            "emotions": emotions,
            "memories": self._recall_memories_db(
                db, campaign_id, actor_kind, actor_id, limit=8, mark_recalled=False
            ),
        }

    def private_snapshot(self, campaign_id: str, actor_kind: str, actor_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            return self._private_snapshot_db(db, campaign_id, actor_kind, actor_id)

    def public_snapshot_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        actor_kind: str,
        actor_id: str,
    ) -> dict[str, Any]:
        """Return a closed projection; cognition, goals, plans and memory are absent."""

        actor = self._actor_db(db, campaign_id, actor_kind, actor_id)
        affordances = self._discover_affordances_db(
            db, campaign_id, actor_kind, actor_id, include_private=False
        )
        result = {
            "contract_version": AGENCY_CONTRACT_VERSION,
            "actor": {"kind": actor_kind, "id": actor_id, "location": actor["location"]},
            "available_affordances": [
                {
                    "id": item["id"],
                    "operator_id": item["operator_id"],
                    "location_id": item.get("location_id"),
                }
                for item in affordances
            ],
        }
        _canonical(result)
        return result

    def public_snapshot(self, campaign_id: str, actor_kind: str, actor_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            return self.public_snapshot_db(db, campaign_id, actor_kind, actor_id)

    def dispatch(self, operation: str, campaign_id: str, payload: Mapping[str, Any] | None = None) -> Any:
        data = dict(payload or {})
        if operation == "save_affordance":
            return self.save_affordance(campaign_id, **data)
        if operation == "remove_affordance":
            return self.remove_affordance(campaign_id, **data)
        if operation == "discover_affordances":
            return self.discover_affordances(campaign_id, **data)
        if operation == "evaluate_affordance":
            return self.evaluate_affordance(campaign_id, **data)
        if operation == "save_goal":
            return self.save_goal(campaign_id, **data)
        if operation == "create_plan":
            return self.create_plan(campaign_id, **data)
        if operation == "replan":
            return self.replan(campaign_id, **data)
        if operation == "execute_next_step":
            return self.execute_next_step(campaign_id, **data)
        if operation == "set_personality_value":
            return self.set_personality_value(campaign_id, **data)
        if operation == "appraise_event":
            return self.appraise_event(campaign_id, **data)
        if operation == "recall_memories":
            return self.recall_memories(campaign_id, **data)
        if operation == "legacy_goal_candidates":
            return self.legacy_goal_candidates(campaign_id, **data)
        if operation == "legacy_action_candidates":
            return self.legacy_action_candidates(campaign_id, **data)
        if operation == "private_snapshot":
            return self.private_snapshot(campaign_id, **data)
        if operation == "public_snapshot":
            return self.public_snapshot(campaign_id, **data)
        raise KeyError(f"unknown agency operation: {operation}")
