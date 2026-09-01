"""Pressure-derived autonomous incidents and event-ledger history views."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import sqlite3
from typing import Any

from .mechanisms import MechanismKernel


INCIDENT_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS incident_definitions (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    category TEXT NOT NULL,
    scope_mode TEXT NOT NULL DEFAULT 'location'
        CHECK(scope_mode IN ('world','location')),
    eligibility_json TEXT NOT NULL DEFAULT '{}',
    weights_json TEXT NOT NULL DEFAULT '[]',
    bindings_json TEXT NOT NULL DEFAULT '{}',
    operator_id TEXT,
    event_type TEXT NOT NULL,
    summary_template TEXT NOT NULL,
    cooldown_minutes INTEGER NOT NULL DEFAULT 10080 CHECK(cooldown_minutes>=0),
    suppression_minutes INTEGER NOT NULL DEFAULT 1440 CHECK(suppression_minutes>=0),
    sensitivity TEXT NOT NULL DEFAULT 'PUBLIC'
        CHECK(sensitivity IN ('PUBLIC','PRIVATE','SECRET')),
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS incident_pressures (
    campaign_id TEXT NOT NULL,
    scope_type TEXT NOT NULL CHECK(scope_type IN ('world','location')),
    scope_id TEXT NOT NULL,
    pressure_key TEXT NOT NULL,
    magnitude REAL NOT NULL CHECK(magnitude BETWEEN 0 AND 1),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    source_revision INTEGER NOT NULL,
    observed_world_time TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,scope_type,scope_id,pressure_key),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS incident_instances (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    definition_id TEXT NOT NULL,
    selection_key TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    sensitivity TEXT NOT NULL DEFAULT 'SECRET'
        CHECK(sensitivity IN ('PUBLIC','PRIVATE','SECRET')),
    visibility_scope TEXT NOT NULL DEFAULT 'GM'
        CHECK(visibility_scope IN ('WORLD','ENTITY','GM','SYSTEM')),
    status TEXT NOT NULL DEFAULT 'resolved'
        CHECK(status IN ('selected','active','resolved','failed','cancelled')),
    weight REAL NOT NULL CHECK(weight>=0),
    bindings_json TEXT NOT NULL,
    pressures_json TEXT NOT NULL,
    operator_execution_id TEXT,
    source_event_id INTEGER,
    selected_world_time TEXT NOT NULL,
    resolved_world_time TEXT,
    result_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    UNIQUE(campaign_id,selection_key),
    FOREIGN KEY(campaign_id,definition_id)
        REFERENCES incident_definitions(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_incidents_recent
    ON incident_instances(campaign_id,definition_id,scope_id,selected_world_time DESC);
CREATE TABLE IF NOT EXISTS incident_runtime_state (
    campaign_id TEXT PRIMARY KEY,
    max_per_boundary INTEGER NOT NULL DEFAULT 3 CHECK(max_per_boundary BETWEEN 0 AND 20),
    last_boundary_world_time TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
"""


_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "exists", "missing"}
_MAX_DEFINITIONS = 256
_MAX_BINDINGS = 16
_MAX_HISTORY = 500
_MAX_LOCATIONS = 512
_MAX_CANDIDATES = 4096
_MAX_JSON_BYTES = 65536
_MAX_CONDITION_DEPTH = 8
_MAX_CONDITION_NODES = 128
_MAX_WEIGHTS = 32


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc(value: str | datetime) -> datetime:
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


class IncidentKernel:
    """Select bounded legal incidents from derived authoritative pressures."""

    def __init__(self, engine: Any):
        self.e = engine

    @staticmethod
    def _id(value: Any, label: str) -> str:
        text = str(value)
        if not _ID_RE.fullmatch(text):
            raise ValueError(f"{label} must match {_ID_RE.pattern}")
        return text

    @staticmethod
    def _finite(value: Any, label: str, *, minimum: float = 0.0) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{label} must be finite")
        number = float(value)
        if not math.isfinite(number) or number < minimum:
            raise ValueError(f"{label} must be finite and >= {minimum}")
        return number

    @staticmethod
    def _tables(db: sqlite3.Connection) -> set[str]:
        return {
            str(row["name"])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    @staticmethod
    def _validate_json_size(value: Any, label: str) -> None:
        if len(_canonical(value).encode("utf-8")) > _MAX_JSON_BYTES:
            raise ValueError(f"{label} exceeds {_MAX_JSON_BYTES} encoded bytes")

    @classmethod
    def _validate_condition_shape(
        cls, condition: Any, *, depth: int = 0, count: list[int] | None = None
    ) -> None:
        count = count if count is not None else [0]
        count[0] += 1
        if count[0] > _MAX_CONDITION_NODES:
            raise ValueError("incident eligibility exceeds node limit")
        if depth > _MAX_CONDITION_DEPTH:
            raise ValueError("incident eligibility exceeds depth limit")
        if condition in (None, {}, []):
            return
        if isinstance(condition, list):
            for item in condition:
                cls._validate_condition_shape(item, depth=depth + 1, count=count)
            return
        if not isinstance(condition, Mapping):
            raise ValueError("incident eligibility must be an object or list")
        recognized = [key for key in ("all", "any", "not", "pressure", "entity_count") if key in condition]
        if len(recognized) != 1 or len(condition) != 1:
            raise ValueError("incident eligibility must contain exactly one supported clause")
        kind = recognized[0]
        value = condition[kind]
        if kind in {"all", "any"}:
            if not isinstance(value, list):
                raise ValueError(f"incident {kind} clause must be a list")
            for item in value:
                cls._validate_condition_shape(item, depth=depth + 1, count=count)
        elif kind == "not":
            cls._validate_condition_shape(value, depth=depth + 1, count=count)
        elif not isinstance(value, Mapping):
            raise ValueError(f"incident {kind} clause must be an object")
        elif kind == "pressure":
            if not value.get("key") or str(value.get("op", "gte")) not in _OPS:
                raise ValueError("invalid incident pressure clause")
            if str(value.get("scope_type", "location")) not in {"world", "location"}:
                raise ValueError("invalid incident pressure scope")
        elif str(value.get("kind")) not in {
            "npc", "character", "faction", "location", "market", "route"
        } or str(value.get("op", "gte")) not in _OPS:
            raise ValueError("invalid incident entity_count clause")

    def save_definition(
        self,
        campaign_id: str,
        definition_id: str,
        category: str,
        event_type: str,
        summary_template: str,
        *,
        scope_mode: str = "location",
        eligibility: Mapping[str, Any] | None = None,
        weights: Sequence[Mapping[str, Any]] | None = None,
        bindings: Mapping[str, Any] | None = None,
        operator_id: str | None = None,
        cooldown_minutes: int = 10080,
        suppression_minutes: int = 1440,
        sensitivity: str = "PUBLIC",
        enabled: bool = True,
    ) -> dict[str, Any]:
        definition_id = self._id(definition_id, "definition_id")
        scope_mode = str(scope_mode)
        if scope_mode not in {"world", "location"}:
            raise ValueError("scope_mode must be world or location")
        eligibility_data = dict(eligibility or {})
        weights_data = list(weights or [])
        bindings_data = dict(bindings or {})
        if len(bindings_data) > _MAX_BINDINGS:
            raise ValueError("incident binding count exceeds limit")
        if len(weights_data) > _MAX_WEIGHTS:
            raise ValueError("incident weight count exceeds limit")
        self._validate_condition_shape(eligibility_data)
        for index, rule in enumerate(weights_data):
            if not isinstance(rule, Mapping) or not rule.get("pressure"):
                raise ValueError(f"incident weight[{index}] must name a pressure")
            self._finite(rule.get("coefficient", 1.0), f"weight[{index}].coefficient")
            if str(rule.get("scope_type", scope_mode)) not in {"world", "location"}:
                raise ValueError(f"invalid incident weight[{index}] scope")
        for role, spec in bindings_data.items():
            self._id(role, "binding role")
            if not isinstance(spec, Mapping):
                raise ValueError(f"incident binding {role} must be an object")
        for label, value in (
            ("eligibility", eligibility_data), ("weights", weights_data),
            ("bindings", bindings_data),
        ):
            self._validate_json_size(value, label)
        sensitivity = str(sensitivity).upper()
        if sensitivity not in {"PUBLIC", "PRIVATE", "SECRET"}:
            raise ValueError("invalid incident sensitivity")
        cooldown_minutes = int(cooldown_minutes)
        suppression_minutes = int(suppression_minutes)
        if min(cooldown_minutes, suppression_minutes) < 0:
            raise ValueError("incident cooldowns must be non-negative")
        if operator_id is not None:
            operator_id = self._id(operator_id, "operator_id")
        with self.e._write_db() as db:
            if operator_id and not db.execute(
                "SELECT 1 FROM mechanism_operators WHERE campaign_id=? AND id=?",
                (campaign_id, operator_id),
            ).fetchone():
                raise KeyError(f"unknown incident operator: {operator_id}")
            db.execute(
                """INSERT INTO incident_definitions(
                       campaign_id,id,category,scope_mode,eligibility_json,weights_json,
                       bindings_json,operator_id,event_type,summary_template,
                       cooldown_minutes,suppression_minutes,sensitivity,enabled,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET
                       category=excluded.category,scope_mode=excluded.scope_mode,
                       eligibility_json=excluded.eligibility_json,
                       weights_json=excluded.weights_json,bindings_json=excluded.bindings_json,
                       operator_id=excluded.operator_id,event_type=excluded.event_type,
                       summary_template=excluded.summary_template,
                       cooldown_minutes=excluded.cooldown_minutes,
                       suppression_minutes=excluded.suppression_minutes,
                       sensitivity=excluded.sensitivity,enabled=excluded.enabled,
                       updated_at=excluded.updated_at""",
                (
                    campaign_id, definition_id, str(category)[:80], scope_mode,
                    self.e._dumps(eligibility_data),
                    self.e._dumps(weights_data),
                    self.e._dumps(bindings_data), operator_id,
                    str(event_type)[:80], str(summary_template)[:500],
                    cooldown_minutes, suppression_minutes, sensitivity,
                    int(bool(enabled)), self.e._now(),
                ),
            )
        return {
            "campaign_id": campaign_id, "id": definition_id,
            "scope_mode": scope_mode, "enabled": bool(enabled),
        }

    def _set_pressure_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        scope_type: str,
        scope_id: str,
        key: str,
        magnitude: float,
        evidence: Mapping[str, Any],
        revision: int,
        world_time: str,
    ) -> None:
        db.execute(
            """INSERT INTO incident_pressures(
                   campaign_id,scope_type,scope_id,pressure_key,magnitude,evidence_json,
                   source_revision,observed_world_time,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(campaign_id,scope_type,scope_id,pressure_key) DO UPDATE SET
                   magnitude=excluded.magnitude,evidence_json=excluded.evidence_json,
                   source_revision=excluded.source_revision,
                   observed_world_time=excluded.observed_world_time,
                   updated_at=excluded.updated_at""",
            (
                campaign_id, scope_type, scope_id, key, _clamp(magnitude),
                self.e._dumps(dict(evidence)), int(revision), world_time, self.e._now(),
            ),
        )

    def extract_pressures_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        revision: int,
        when: datetime,
    ) -> int:
        """Refresh revision-keyed derived observations; never mutate source domains."""
        world_time = _utc(when).isoformat()
        tables = self._tables(db)
        location_rows = db.execute(
            "SELECT id FROM locations WHERE campaign_id=? ORDER BY id LIMIT ?",
            (campaign_id, _MAX_LOCATIONS + 1),
        ).fetchall()
        if len(location_rows) > _MAX_LOCATIONS:
            raise ValueError(
                f"incident pressure extraction exceeds {_MAX_LOCATIONS} locations"
            )
        locations = [
            str(row["id"])
            for row in location_rows
        ]
        # Every aggregation boundary is a fresh observation. Expire records for
        # removed/unobserved locations before rebuilding the world maximums.
        db.execute(
            """DELETE FROM incident_pressures
               WHERE campaign_id=? AND scope_type='location'
                 AND (source_revision<>? OR NOT EXISTS (
                     SELECT 1 FROM locations l
                     WHERE l.campaign_id=incident_pressures.campaign_id
                       AND l.id=incident_pressures.scope_id))""",
            (campaign_id, int(revision)),
        )
        db.execute(
            "DELETE FROM incident_pressures WHERE campaign_id=? AND scope_type='world'",
            (campaign_id,),
        )
        written = 0
        for location_id in locations:
            pressure: dict[str, tuple[float, dict[str, Any]]] = {}
            if "population_state" in tables:
                row = db.execute(
                    """SELECT employment,migration_pressure,population
                       FROM population_state WHERE campaign_id=? AND location_id=?""",
                    (campaign_id, location_id),
                ).fetchone()
                if row:
                    pressure["unemployment"] = (
                        _clamp(1.0 - float(row["employment"])),
                        {"employment": float(row["employment"]), "population": float(row["population"])},
                    )
                    pressure["migration"] = (
                        _clamp(abs(float(row["migration_pressure"]))),
                        {"migration_pressure": float(row["migration_pressure"])},
                    )
            if "settlement_profiles" in tables:
                row = db.execute(
                    """SELECT stability,prosperity FROM settlement_profiles
                       WHERE campaign_id=? AND location_id=?""",
                    (campaign_id, location_id),
                ).fetchone()
                if row:
                    pressure["instability"] = (
                        _clamp(1.0 - float(row["stability"])),
                        {"stability": float(row["stability"])},
                    )
                    pressure["prosperity"] = (
                        _clamp(float(row["prosperity"])),
                        {"prosperity": float(row["prosperity"])},
                    )
            if "settlement_service_needs" in tables:
                row = db.execute(
                    """SELECT COALESCE(MAX(
                           CASE WHEN required_capacity>0
                                THEN gap/required_capacity ELSE 0 END),0) AS ratio
                       FROM settlement_service_needs
                       WHERE campaign_id=? AND location_id=?""",
                    (campaign_id, location_id),
                ).fetchone()
                pressure["service_shortage"] = (
                    _clamp(float(row["ratio"] if row else 0.0)),
                    {"source": "settlement_service_needs"},
                )
            if {"economy_markets", "economy_market_items", "inventories"} <= tables:
                row = db.execute(
                    """SELECT COALESCE(MAX(
                           CASE WHEN mi.target_stock>0 THEN
                             MAX(0,MIN(1,(mi.target_stock-COALESCE(i.qty,0))/mi.target_stock))
                           ELSE 0 END),0) AS scarcity
                       FROM economy_markets m
                       JOIN economy_market_items mi
                         ON mi.campaign_id=m.campaign_id AND mi.market_id=m.id
                       LEFT JOIN inventories i
                         ON i.campaign_id=m.campaign_id
                        AND i.owner_kind=m.owner_kind AND i.owner_id=m.owner_id
                        AND i.item_id=mi.item_id
                       WHERE m.campaign_id=? AND m.location_id=? AND m.active=1
                         AND mi.enabled=1""",
                    (campaign_id, location_id),
                ).fetchone()
                pressure["market_shortage"] = (
                    _clamp(float(row["scarcity"] if row else 0.0)),
                    {"source": "finite market inventory"},
                )
            if "economy_routes" in tables:
                row = db.execute(
                    """SELECT COUNT(*) AS total,
                              SUM(CASE WHEN active=0 THEN 1 ELSE 0 END) AS blocked
                       FROM economy_routes WHERE campaign_id=?
                         AND (from_location_id=? OR to_location_id=?)""",
                    (campaign_id, location_id, location_id),
                ).fetchone()
                total = int(row["total"] or 0)
                blocked = int(row["blocked"] or 0)
                pressure["route_disruption"] = (
                    _clamp(blocked / total if total else 0.0),
                    {"routes": total, "blocked": blocked},
                )
            if "environment_effects" in tables:
                row = db.execute(
                    """SELECT COALESCE(MAX(e.intensity),0) AS hazard
                       FROM environment_effects e
                       JOIN environment_targets t
                         ON t.campaign_id=e.campaign_id AND t.target_key=e.target_key
                       WHERE e.campaign_id=? AND t.location_id=?
                         AND e.active=1 AND t.active=1""",
                    (campaign_id, location_id),
                ).fetchone()
                pressure["environment_hazard"] = (
                    _clamp(float(row["hazard"] if row else 0.0)),
                    {"source": "active environment effects"},
                )
            for key, (magnitude, evidence) in sorted(pressure.items()):
                self._set_pressure_db(
                    db, campaign_id, "location", location_id, key, magnitude,
                    evidence, revision, world_time,
                )
                written += 1
        # World pressure is the maximum observed local pressure of each type.
        keys = db.execute(
            """SELECT pressure_key,MAX(magnitude) AS magnitude
               FROM incident_pressures WHERE campaign_id=? AND scope_type='location'
                 AND source_revision=?
               GROUP BY pressure_key ORDER BY pressure_key""",
            (campaign_id, int(revision)),
        ).fetchall()
        for row in keys:
            self._set_pressure_db(
                db, campaign_id, "world", "global", str(row["pressure_key"]),
                float(row["magnitude"]), {"aggregation": "max_location"},
                revision, world_time,
            )
            written += 1
        return written

    @staticmethod
    def _compare(have: Any, op: str, want: Any) -> bool:
        if op not in _OPS:
            raise ValueError(f"unsupported incident comparison: {op}")
        if op == "exists":
            return have is not None
        if op == "missing":
            return have is None
        if op == "eq":
            return have == want
        if op == "ne":
            return have != want
        try:
            left, right = float(have), float(want)
        except (TypeError, ValueError):
            return False
        return {
            "gt": left > right,
            "gte": left >= right,
            "lt": left < right,
            "lte": left <= right,
        }[op]

    def _pressure_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        scope_type: str,
        scope_id: str,
        key: str,
    ) -> float | None:
        row = db.execute(
            """SELECT magnitude FROM incident_pressures
               WHERE campaign_id=? AND scope_type=? AND scope_id=? AND pressure_key=?""",
            (campaign_id, scope_type, scope_id, key),
        ).fetchone()
        return float(row["magnitude"]) if row else None

    def _eligible_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        condition: Any,
        *,
        scope_type: str,
        scope_id: str,
    ) -> bool:
        if condition in (None, {}, []):
            return True
        if isinstance(condition, list):
            return all(
                self._eligible_db(
                    db, campaign_id, item, scope_type=scope_type, scope_id=scope_id
                )
                for item in condition
            )
        if not isinstance(condition, Mapping):
            raise ValueError("incident eligibility must be an object or list")
        if "all" in condition:
            return all(
                self._eligible_db(
                    db, campaign_id, item, scope_type=scope_type, scope_id=scope_id
                )
                for item in condition["all"]
            )
        if "any" in condition:
            return any(
                self._eligible_db(
                    db, campaign_id, item, scope_type=scope_type, scope_id=scope_id
                )
                for item in condition["any"]
            )
        if "not" in condition:
            return not self._eligible_db(
                db, campaign_id, condition["not"],
                scope_type=scope_type, scope_id=scope_id,
            )
        if "pressure" in condition:
            spec = dict(condition["pressure"])
            selected_scope = str(spec.get("scope_type", scope_type))
            selected_id = str(spec.get("scope_id", scope_id if selected_scope == scope_type else "global"))
            value = self._pressure_db(
                db, campaign_id, selected_scope, selected_id, str(spec["key"])
            )
            return self._compare(value, str(spec.get("op", "gte")), spec.get("value", 0))
        if "entity_count" in condition:
            spec = dict(condition["entity_count"])
            table = {
                "npc": "npcs", "character": "characters", "faction": "factions",
                "location": "locations", "market": "economy_markets",
                "route": "economy_routes",
            }.get(str(spec.get("kind")))
            if not table:
                return False
            clauses, params = ["campaign_id=?"], [campaign_id]
            if scope_type == "location":
                location_column = {
                    "npcs": "location", "characters": "location",
                    "economy_markets": "location_id",
                }.get(table)
                if location_column:
                    clauses.append(f"{location_column}=?")
                    params.append(scope_id)
            row = db.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE " + " AND ".join(clauses),
                params,
            ).fetchone()
            return self._compare(
                int(row["n"]), str(spec.get("op", "gte")), int(spec.get("value", 1))
            )
        raise ValueError("unsupported incident eligibility clause")

    def _bind_role_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        role: str,
        spec: Mapping[str, Any],
        *,
        scope_type: str,
        scope_id: str,
    ) -> dict[str, str] | None:
        kind = str(spec.get("kind", ""))
        table_info = {
            "npc": ("npcs", "id", "location"),
            "character": ("characters", "id", "location"),
            "faction": ("factions", "id", None),
            "location": ("locations", "id", "id"),
            "market": ("economy_markets", "id", "location_id"),
            "route": ("economy_routes", "id", None),
            "quest": ("quests", "id", None),
        }.get(kind)
        if not table_info:
            raise ValueError(f"unsupported incident binding kind for {role}: {kind}")
        table, id_col, location_col = table_info
        clauses, params = ["campaign_id=?"], [campaign_id]
        if scope_type == "location" and bool(spec.get("local", True)) and location_col:
            clauses.append(f"{location_col}=?")
            params.append(scope_id)
        if table in {"npcs", "characters", "quests"} and spec.get("status"):
            clauses.append("status=?")
            params.append(str(spec["status"]))
        if spec.get("id"):
            clauses.append(f"{id_col}=?")
            params.append(str(spec["id"]))
        row = db.execute(
            f"SELECT {id_col} AS id FROM {table} WHERE "
            + " AND ".join(clauses)
            + f" ORDER BY {id_col} LIMIT 1",
            params,
        ).fetchone()
        if not row:
            return None
        return {"kind": kind, "id": str(row["id"])}

    def _bindings_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        definitions: Mapping[str, Any],
        *,
        scope_type: str,
        scope_id: str,
    ) -> dict[str, dict[str, str]] | None:
        out: dict[str, dict[str, str]] = {}
        if scope_type == "location":
            out["location"] = {"kind": "location", "id": scope_id}
        for role, spec in sorted(definitions.items()):
            if role == "location" and scope_type == "location":
                continue
            if not isinstance(spec, Mapping):
                raise ValueError(f"incident binding {role} must be an object")
            bound = self._bind_role_db(
                db, campaign_id, str(role), spec,
                scope_type=scope_type, scope_id=scope_id,
            )
            if not bound and bool(spec.get("required", True)):
                return None
            if bound:
                out[str(role)] = bound
        return out

    def _weight_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        rules: Sequence[Mapping[str, Any]],
        *,
        scope_type: str,
        scope_id: str,
    ) -> tuple[float, dict[str, float]]:
        total = 1.0
        components: dict[str, float] = {}
        for index, raw in enumerate(rules):
            spec = dict(raw)
            key = str(spec["pressure"])
            selected_scope = str(spec.get("scope_type", scope_type))
            selected_id = str(spec.get("scope_id", scope_id if selected_scope == scope_type else "global"))
            magnitude = self._pressure_db(
                db, campaign_id, selected_scope, selected_id, key
            )
            magnitude = float(magnitude or 0.0)
            coefficient = self._finite(
                spec.get("coefficient", 1.0), f"weight[{index}].coefficient"
            )
            contribution = magnitude * coefficient
            total += contribution
            components[key] = round(contribution, 12)
        return max(0.0, total), components

    def _cooldown_clear_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        definition: sqlite3.Row,
        scope_id: str,
        when: datetime,
    ) -> bool:
        row = db.execute(
            """SELECT selected_world_time FROM incident_instances
               WHERE campaign_id=? AND definition_id=? AND scope_id=?
               ORDER BY selected_world_time DESC,id DESC LIMIT 1""",
            (campaign_id, definition["id"], scope_id),
        ).fetchone()
        if row and _utc(row["selected_world_time"]) + timedelta(
            minutes=int(definition["cooldown_minutes"])
        ) > when:
            return False
        if int(definition["suppression_minutes"]) > 0:
            cutoff = (
                when - timedelta(minutes=int(definition["suppression_minutes"]))
            ).isoformat()
            recent = db.execute(
                """SELECT 1 FROM events WHERE campaign_id=? AND event_type=?
                   AND world_time>=? AND (region=? OR ?='global') LIMIT 1""",
                (
                    campaign_id, definition["event_type"], cutoff,
                    scope_id, scope_id,
                ),
            ).fetchone()
            if recent:
                return False
        return True

    def candidates_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        when: datetime,
    ) -> list[dict[str, Any]]:
        definitions = db.execute(
            """SELECT * FROM incident_definitions
               WHERE campaign_id=? AND enabled=1 ORDER BY id LIMIT ?""",
            (campaign_id, _MAX_DEFINITIONS + 1),
        ).fetchall()
        if len(definitions) > _MAX_DEFINITIONS:
            raise ValueError(
                f"incident selection exceeds {_MAX_DEFINITIONS} enabled definitions"
            )
        location_rows = db.execute(
            "SELECT id FROM locations WHERE campaign_id=? ORDER BY id LIMIT ?",
            (campaign_id, _MAX_LOCATIONS + 1),
        ).fetchall()
        if len(location_rows) > _MAX_LOCATIONS:
            raise ValueError(
                f"incident selection exceeds {_MAX_LOCATIONS} locations"
            )
        location_ids = [
            str(row["id"])
            for row in location_rows
        ]
        candidates: list[dict[str, Any]] = []
        for definition in definitions:
            scopes = [("world", "global")]
            if definition["scope_mode"] == "location":
                scopes = [("location", item) for item in location_ids]
            eligibility = self.e._loads(definition["eligibility_json"])
            weights = self.e._loads(definition["weights_json"])
            binding_spec = self.e._loads(definition["bindings_json"])
            for scope_type, scope_id in scopes:
                if not self._cooldown_clear_db(
                    db, campaign_id, definition, scope_id, when
                ):
                    continue
                if not self._eligible_db(
                    db, campaign_id, eligibility,
                    scope_type=scope_type, scope_id=scope_id,
                ):
                    continue
                bindings = self._bindings_db(
                    db, campaign_id, binding_spec,
                    scope_type=scope_type, scope_id=scope_id,
                )
                if bindings is None:
                    continue
                weight, components = self._weight_db(
                    db, campaign_id, weights,
                    scope_type=scope_type, scope_id=scope_id,
                )
                if weight <= 0:
                    continue
                pressures = {
                    row["pressure_key"]: float(row["magnitude"])
                    for row in db.execute(
                        """SELECT pressure_key,magnitude FROM incident_pressures
                           WHERE campaign_id=? AND scope_type=? AND scope_id=?
                           ORDER BY pressure_key""",
                        (campaign_id, scope_type, scope_id),
                    ).fetchall()
                }
                candidates.append({
                    "definition": definition,
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                    "bindings": bindings,
                    "weight": weight,
                    "weight_components": components,
                    "pressures": pressures,
                })
                if len(candidates) > _MAX_CANDIDATES:
                    raise ValueError(
                        f"incident selection exceeds {_MAX_CANDIDATES} candidates"
                    )
        return candidates

    def _seed_db(self, db: sqlite3.Connection, campaign_id: str) -> int:
        row = db.execute(
            "SELECT seed FROM sim_config WHERE campaign_id=?", (campaign_id,)
        ).fetchone()
        return int(row["seed"]) if row else 0

    def _select(
        self,
        candidates: Sequence[dict[str, Any]],
        *,
        seed: int,
        boundary: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Deterministic weighted sampling without replacement."""
        ranked: list[tuple[float, str, dict[str, Any]]] = []
        for candidate in candidates:
            definition = candidate["definition"]
            identity = f"{definition['id']}:{candidate['scope_type']}:{candidate['scope_id']}"
            raw = hashlib.sha256(
                f"{seed}:incident:{boundary}:{identity}".encode("utf-8")
            ).digest()
            uniform = max(
                int.from_bytes(raw[:8], "big") / float(1 << 64),
                2.0 ** -53,
            )
            # Exponential race: smaller key wins; selection probability is
            # proportional to the positive state-derived weight.
            key = -math.log(uniform) / float(candidate["weight"])
            ranked.append((key, identity, candidate))
        ranked.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in ranked[: max(0, min(int(limit), 20))]]

    @staticmethod
    def _summary(template: str, candidate: Mapping[str, Any]) -> str:
        summary = str(template)
        summary = summary.replace("{scope_id}", str(candidate["scope_id"]))
        for role, ref in sorted(candidate["bindings"].items()):
            summary = summary.replace("{" + str(role) + "}", str(ref["id"]))
        return summary[:2000]

    def _execute_candidate_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        revision: int,
        when: datetime,
        candidate: dict[str, Any],
        *,
        emit: Callable[..., Any] | None,
    ) -> dict[str, Any]:
        definition = candidate["definition"]
        boundary = _utc(when).isoformat()
        selection_material = {
            "definition_id": definition["id"],
            "scope_type": candidate["scope_type"],
            "scope_id": candidate["scope_id"],
            "boundary": boundary,
            "bindings": candidate["bindings"],
        }
        selection_key = "isel_" + _digest(selection_material)[:24]
        prior = db.execute(
            """SELECT * FROM incident_instances
               WHERE campaign_id=? AND selection_key=?""",
            (campaign_id, selection_key),
        ).fetchone()
        if prior:
            return {**dict(prior), "idempotent_replay": True}
        operator_id = definition["operator_id"]
        operator_preview = None
        operator_bindings: dict[str, Any] = {}
        if operator_id:
            mechanism = MechanismKernel(self.e)
            operator = mechanism._get_operator_db(db, campaign_id, str(operator_id))
            execution_scope = "incident." + _digest(str(definition["id"]))[:32]
            operator_bindings = {
                role: binding for role, binding in candidate["bindings"].items()
                if role in operator["bindings"]
            }
            operator_preview = mechanism.execute_operator_db(
                db, campaign_id, str(operator_id),
                bindings=operator_bindings, dry_run=True,
                revision=revision, world_time=boundary,
                execution_scope=execution_scope,
                step_identity=selection_key,
            )
            if not operator_preview["evaluation"]["eligible"]:
                raise ValueError("incident operator is not eligible")
        payload = {
            "incident_definition_id": definition["id"],
            "category": definition["category"],
            "scope_type": candidate["scope_type"],
            "scope_id": candidate["scope_id"],
            "bindings": candidate["bindings"],
            "weight": round(float(candidate["weight"]), 12),
            "weight_components": candidate["weight_components"],
            "pressures": candidate["pressures"],
            "selection_key": selection_key,
        }
        summary = self._summary(definition["summary_template"], candidate)
        event_id: int | None = None
        visibility_scope = (
            "WORLD" if definition["sensitivity"] == "PUBLIC" else "GM"
        )
        if emit:
            emitted = emit(
                str(definition["event_type"]), summary, payload,
                None if candidate["scope_type"] == "world" else candidate["scope_id"],
                _utc(when),
                sensitivity=str(definition["sensitivity"]),
                scope_type=visibility_scope,
            )
            if isinstance(emitted, int):
                event_id = emitted
        else:
            event_id = self.e._insert_event(
                db, campaign_id, revision, str(definition["event_type"]), summary,
                region=(
                    None if candidate["scope_type"] == "world"
                    else candidate["scope_id"]
                ),
                payload=payload, world_time_override=boundary,
                sensitivity=str(definition["sensitivity"]),
                scope_type=visibility_scope,
            )
        operator_result = None
        if operator_id:
            operator_result = MechanismKernel(self.e).execute_operator_db(
                db, campaign_id, str(operator_id),
                bindings=operator_bindings, revision=revision,
                world_time=boundary,
                execution_scope=execution_scope,
                idempotency_key=selection_key,
                step_identity=selection_key,
            )
            if event_id:
                child_event_ids = set(operator_result.get("event_ids") or [])
                for row in db.execute(
                    """SELECT id,payload_json FROM events
                       WHERE campaign_id=? AND revision=? AND id<>?""",
                    (campaign_id, revision, event_id),
                ).fetchall():
                    event_payload = self.e._loads(row["payload_json"] or "{}")
                    if event_payload.get("execution_id") == operator_result.get("execution_id"):
                        child_event_ids.add(int(row["id"]))
                for child_event_id in sorted(child_event_ids):
                    db.execute(
                        """UPDATE events
                           SET causal_parent_event_id=?,causal_root_event_id=?,
                               sensitivity=?,scope_type=?,
                               principal_kind=NULL,principal_id=NULL
                           WHERE campaign_id=? AND id=?""",
                        (
                            event_id, event_id, str(definition["sensitivity"]),
                            visibility_scope, campaign_id, int(child_event_id),
                        ),
                    )
        incident_id = "inc_" + _digest({"selection_key": selection_key})[:24]
        result = {
            "event_id": event_id,
            "operator": operator_result,
            "operator_preview_digest": (
                _digest(operator_preview["evaluation"]) if operator_preview else None
            ),
        }
        db.execute(
            """INSERT INTO incident_instances(
                   campaign_id,id,definition_id,selection_key,scope_type,scope_id,
                   sensitivity,visibility_scope,
                   status,weight,bindings_json,pressures_json,operator_execution_id,
                   source_event_id,selected_world_time,resolved_world_time,result_json,updated_at)
               VALUES(?,?,?,?,?,?,?,?,'resolved',?,?,?,?,?,?,?,?,?)""",
            (
                campaign_id, incident_id, definition["id"], selection_key,
                candidate["scope_type"], candidate["scope_id"],
                str(definition["sensitivity"]), visibility_scope,
                float(candidate["weight"]), self.e._dumps(candidate["bindings"]),
                self.e._dumps(candidate["pressures"]),
                (operator_result or {}).get("execution_id"), event_id,
                boundary, boundary, self.e._dumps(result), self.e._now(),
            ),
        )
        return {
            "campaign_id": campaign_id, "id": incident_id,
            "definition_id": definition["id"], "selection_key": selection_key,
            "scope_type": candidate["scope_type"],
            "scope_id": candidate["scope_id"], "status": "resolved",
            "weight": float(candidate["weight"]), "bindings": candidate["bindings"],
            "pressures": candidate["pressures"], "source_event_id": event_id,
            "operator_execution_id": (operator_result or {}).get("execution_id"),
            "idempotent_replay": False,
        }

    def has_activity_db(self, db: sqlite3.Connection, campaign_id: str) -> bool:
        return db.execute(
            """SELECT 1 FROM incident_definitions
               WHERE campaign_id=? AND enabled=1 LIMIT 1""",
            (campaign_id,),
        ).fetchone() is not None

    def step_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        revision: int,
        when: datetime,
        *,
        emit: Callable[..., Any] | None = None,
    ) -> dict[str, Any]:
        when = _utc(when)
        boundary = when.isoformat()
        state = db.execute(
            "SELECT * FROM incident_runtime_state WHERE campaign_id=?",
            (campaign_id,),
        ).fetchone()
        if state and state["last_boundary_world_time"] == boundary:
            return {
                "pressures": 0, "eligible": 0, "selected": 0,
                "incidents": [], "idempotent_replay": True,
            }
        if not state:
            db.execute(
                """INSERT INTO incident_runtime_state(
                       campaign_id,max_per_boundary,last_boundary_world_time,updated_at)
                   VALUES(?,3,NULL,?)""",
                (campaign_id, self.e._now()),
            )
            max_per_boundary = 3
        else:
            max_per_boundary = int(state["max_per_boundary"])
        pressure_count = self.extract_pressures_db(
            db, campaign_id, revision, when
        )
        candidates = self.candidates_db(db, campaign_id, when)
        selected = self._select(
            candidates, seed=self._seed_db(db, campaign_id),
            boundary=boundary, limit=max_per_boundary,
        )
        incidents = [
            self._execute_candidate_db(
                db, campaign_id, revision, when, candidate, emit=emit
            )
            for candidate in selected
        ]
        db.execute(
            """UPDATE incident_runtime_state
               SET last_boundary_world_time=?,updated_at=? WHERE campaign_id=?""",
            (boundary, self.e._now(), campaign_id),
        )
        return {
            "pressures": pressure_count,
            "eligible": len(candidates),
            "selected": len(incidents),
            "incidents": incidents,
            "idempotent_replay": False,
        }

    def history(
        self,
        campaign_id: str,
        *,
        scope_id: str | None = None,
        event_type: str | None = None,
        privileged: bool = False,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return a derived causal view; events remain the only history authority."""
        limit = max(1, min(int(limit), _MAX_HISTORY))
        clauses, params = ["campaign_id=?"], [campaign_id]
        if not privileged:
            clauses.append("sensitivity='PUBLIC'")
            clauses.append("scope_type='WORLD'")
        if scope_id:
            clauses.append("(region=? OR actor_id=? OR target_id=?)")
            params.extend([scope_id, scope_id, scope_id])
        if event_type:
            clauses.append("event_type=?")
            params.append(str(event_type))
        params.append(limit)
        with self.e._db() as db:
            rows = db.execute(
                """SELECT id,revision,world_time,event_type,region,actor_id,target_id,
                          summary,sensitivity,scope_type,causal_parent_event_id,
                          causal_root_event_id,payload_json
                   FROM events WHERE """
                + " AND ".join(clauses)
                + " ORDER BY revision DESC,id DESC LIMIT ?",
                params,
            ).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            item["payload"] = self.e._loads(item.pop("payload_json"))
            events.append(item)
        return {"campaign_id": campaign_id, "events": events}

    def public_snapshot_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        *,
        location_id: str | None = None,
        limit: int = 30,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        clauses = [
            "i.campaign_id=?", "i.sensitivity='PUBLIC'",
            "i.visibility_scope='WORLD'",
            "i.status IN ('active','resolved')",
        ]
        params: list[Any] = [campaign_id]
        if location_id:
            clauses.append("(i.scope_type='world' OR i.scope_id=?)")
            params.append(location_id)
        params.append(limit)
        rows = db.execute(
            """SELECT i.id,i.definition_id,d.category,i.scope_type,i.scope_id,
                      i.status,i.source_event_id,i.selected_world_time,
                      i.resolved_world_time
               FROM incident_instances i JOIN incident_definitions d
                 ON d.campaign_id=i.campaign_id AND d.id=i.definition_id
               WHERE """
            + " AND ".join(clauses)
            + " ORDER BY i.selected_world_time DESC,i.id DESC LIMIT ?",
            params,
        ).fetchall()
        return {"campaign_id": campaign_id, "incidents": [dict(row) for row in rows]}

    def public_snapshot(
        self, campaign_id: str, *, location_id: str | None = None, limit: int = 30
    ) -> dict[str, Any]:
        with self.e._db() as db:
            return self.public_snapshot_db(
                db, campaign_id, location_id=location_id, limit=limit
            )

    def dispatch(
        self, operation: str, campaign_id: str, payload: Mapping[str, Any] | None = None
    ) -> Any:
        """Closed public projection dispatcher; never accepts a privilege switch."""
        data = dict(payload or {})
        if operation == "public_snapshot":
            return self.public_snapshot(campaign_id, **data)
        if operation == "history":
            data.pop("privileged", None)
            return self.history(campaign_id, privileged=False, **data)
        raise ValueError(f"unknown incident operation: {operation}")

    def trusted_dispatch(
        self, operation: str, campaign_id: str, payload: Mapping[str, Any] | None = None
    ) -> Any:
        """Internal authoring/GM dispatcher. It is intentionally not an HTTP Action."""
        data = dict(payload or {})
        if operation == "save_definition":
            return self.save_definition(campaign_id, **data)
        if operation == "history":
            return self.history(campaign_id, **data)
        return self.dispatch(operation, campaign_id, data)
