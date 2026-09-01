from __future__ import annotations

import hashlib
import math
import sqlite3
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .engine import WorldEngine


POPULATION_SCHEMA = r'''
CREATE TABLE IF NOT EXISTS population_config (
    campaign_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    births_enabled INTEGER NOT NULL DEFAULT 1,
    mortality_enabled INTEGER NOT NULL DEFAULT 1,
    migration_enabled INTEGER NOT NULL DEFAULT 1,
    households_enabled INTEGER NOT NULL DEFAULT 1,
    settlement_enabled INTEGER NOT NULL DEFAULT 1,
    service_gaps_enabled INTEGER NOT NULL DEFAULT 1,
    default_birth_rate_annual REAL NOT NULL DEFAULT 0 CHECK(default_birth_rate_annual >= 0),
    default_death_rate_annual REAL NOT NULL DEFAULT 0 CHECK(default_death_rate_annual >= 0),
    max_migration_fraction_per_day REAL NOT NULL DEFAULT 0.02 CHECK(max_migration_fraction_per_day BETWEEN 0 AND 1),
    minimum_pull_delta REAL NOT NULL DEFAULT 0.05 CHECK(minimum_pull_delta BETWEEN 0 AND 2),
    service_event_cooldown_days INTEGER NOT NULL DEFAULT 30 CHECK(service_event_cooldown_days BETWEEN 0 AND 36500),
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settlement_profiles (
    campaign_id TEXT NOT NULL,
    location_id TEXT NOT NULL,
    settlement_type TEXT NOT NULL DEFAULT 'settlement',
    rank TEXT NOT NULL DEFAULT 'unranked',
    housing_capacity REAL NOT NULL DEFAULT 0 CHECK(housing_capacity >= 0),
    water_capacity REAL NOT NULL DEFAULT 0 CHECK(water_capacity >= 0),
    sanitation REAL NOT NULL DEFAULT 0.5 CHECK(sanitation BETWEEN 0 AND 1),
    healthcare REAL NOT NULL DEFAULT 0.5 CHECK(healthcare BETWEEN 0 AND 1),
    prosperity REAL NOT NULL DEFAULT 0.5 CHECK(prosperity BETWEEN 0 AND 1),
    stability REAL NOT NULL DEFAULT 0.5 CHECK(stability BETWEEN 0 AND 1),
    attractiveness REAL NOT NULL DEFAULT 0.5 CHECK(attractiveness BETWEEN 0 AND 1),
    auto_rank INTEGER NOT NULL DEFAULT 0,
    founded_world_time TEXT,
    last_processed_world_time TEXT NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,location_id),
    FOREIGN KEY(campaign_id,location_id) REFERENCES locations(campaign_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS population_cohorts (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    location_id TEXT NOT NULL,
    species TEXT NOT NULL DEFAULT 'unspecified',
    culture TEXT NOT NULL DEFAULT 'unspecified',
    faction_id TEXT,
    age_band TEXT NOT NULL DEFAULT 'mixed' CHECK(age_band IN ('child','adult','elder','mixed')),
    livelihood TEXT NOT NULL DEFAULT 'mixed',
    count REAL NOT NULL DEFAULT 0 CHECK(count >= 0),
    birth_rate_annual REAL NOT NULL DEFAULT 0 CHECK(birth_rate_annual >= 0),
    death_rate_annual REAL NOT NULL DEFAULT 0 CHECK(death_rate_annual >= 0),
    labor_participation REAL NOT NULL DEFAULT 0.55 CHECK(labor_participation BETWEEN 0 AND 1),
    migration_affinity REAL NOT NULL DEFAULT 1 CHECK(migration_affinity BETWEEN 0 AND 2),
    health REAL NOT NULL DEFAULT 0.75 CHECK(health BETWEEN 0 AND 1),
    wealth REAL NOT NULL DEFAULT 0.5 CHECK(wealth BETWEEN 0 AND 1),
    next_cohort_id TEXT,
    transition_rate_annual REAL NOT NULL DEFAULT 0 CHECK(transition_rate_annual >= 0),
    state_json TEXT NOT NULL DEFAULT '{}',
    last_processed_world_time TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,location_id) REFERENCES locations(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id,faction_id) REFERENCES factions(campaign_id,id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS population_households (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    location_id TEXT NOT NULL,
    cohort_id TEXT,
    household_count REAL NOT NULL DEFAULT 0 CHECK(household_count >= 0),
    persons REAL NOT NULL DEFAULT 0 CHECK(persons >= 0),
    adults REAL NOT NULL DEFAULT 0 CHECK(adults >= 0),
    children REAL NOT NULL DEFAULT 0 CHECK(children >= 0),
    elders REAL NOT NULL DEFAULT 0 CHECK(elders >= 0),
    housing_units REAL NOT NULL DEFAULT 0 CHECK(housing_units >= 0),
    wealth REAL NOT NULL DEFAULT 0.5 CHECK(wealth BETWEEN 0 AND 1),
    food_reserve_days REAL NOT NULL DEFAULT 0 CHECK(food_reserve_days >= 0),
    livelihood TEXT NOT NULL DEFAULT 'mixed',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','displaced','dissolved')),
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,location_id) REFERENCES locations(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id,cohort_id) REFERENCES population_cohorts(campaign_id,id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS settlement_labor (
    campaign_id TEXT NOT NULL,
    location_id TEXT NOT NULL,
    occupation TEXT NOT NULL,
    demand REAL NOT NULL DEFAULT 0 CHECK(demand >= 0),
    supply REAL NOT NULL DEFAULT 0 CHECK(supply >= 0),
    filled REAL NOT NULL DEFAULT 0 CHECK(filled >= 0),
    productivity REAL NOT NULL DEFAULT 1 CHECK(productivity BETWEEN 0 AND 1),
    wage_index REAL NOT NULL DEFAULT 1 CHECK(wage_index >= 0),
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_world_time TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,location_id,occupation),
    FOREIGN KEY(campaign_id,location_id) REFERENCES locations(campaign_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settlement_service_needs (
    campaign_id TEXT NOT NULL,
    location_id TEXT NOT NULL,
    service_kind TEXT NOT NULL,
    required_capacity REAL NOT NULL DEFAULT 0 CHECK(required_capacity >= 0),
    available_capacity REAL NOT NULL DEFAULT 0 CHECK(available_capacity >= 0),
    gap REAL NOT NULL DEFAULT 0 CHECK(gap >= 0),
    last_event_world_time TEXT,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_world_time TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,location_id,service_kind),
    FOREIGN KEY(campaign_id,location_id) REFERENCES locations(campaign_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS population_flows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL,
    flow_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('birth','death','migration','age_transition','reconciliation')),
    origin_location_id TEXT,
    destination_location_id TEXT,
    cohort_id TEXT,
    destination_cohort_id TEXT,
    count REAL NOT NULL DEFAULT 0 CHECK(count >= 0),
    reason TEXT NOT NULL,
    world_time TEXT NOT NULL,
    revision INTEGER NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(campaign_id,flow_key),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_population_cohorts_location
    ON population_cohorts(campaign_id,location_id,age_band,livelihood,id);
CREATE INDEX IF NOT EXISTS idx_population_households_location
    ON population_households(campaign_id,location_id,status,id);
CREATE INDEX IF NOT EXISTS idx_population_flows_time
    ON population_flows(campaign_id,world_time,id);
CREATE INDEX IF NOT EXISTS idx_settlement_service_gap
    ON settlement_service_needs(campaign_id,location_id,gap DESC,service_kind);
'''


# Engineering defaults, not claims about historical settlement requirements.
# Campaigns can replace these per settlement through state.service_requirements.
DEFAULT_SERVICE_PEOPLE_PER_UNIT: dict[str, float] = {
    "food_market": 300.0,
    "guard": 250.0,
    "healer": 500.0,
    "lodging": 400.0,
    "sanitation": 500.0,
}

DEFAULT_RANK_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (25000.0, "metropolis"),
    (5000.0, "city"),
    (1000.0, "town"),
    (200.0, "village"),
    (50.0, "hamlet"),
    (1.0, "outpost"),
    (0.0, "empty"),
)

DANGEROUS_EFFECTS: dict[str, float] = {
    "fire": 1.0,
    "smoke": 0.45,
    "water": 0.55,
    "heat": 0.6,
    "cold": 0.6,
    "gas": 1.0,
    "blight": 0.75,
    "corrosion": 0.45,
    "ice": 0.35,
    "snow": 0.25,
    "mud": 0.15,
    "corruption": 0.9,
    "disease": 0.9,
    "electricity": 0.75,
    "explosion": 1.0,
    "drought": 0.8,
}


class PopulationKernel:
    """Sparse population, household, labour, migration, and settlement runtime.

    `population_state` remains the compatibility summary used by older systems.
    Cohorts and households are aggregate records; this runtime deliberately does
    not instantiate every resident as an NPC. Named NPC lifecycle remains owned
    by SimulationKernel and is not counted again here unless an author explicitly
    includes it in a cohort count.
    """

    def __init__(self, engine: WorldEngine):
        self.e = engine

    @staticmethod
    def _finite_number(
        value: Any,
        field: str,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float:
        """Parse an authored number without accepting Python's bool-as-int quirk."""
        if isinstance(value, bool):
            raise ValueError(f"{field} must be a finite number, not a boolean")
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be a finite number") from exc
        if not math.isfinite(parsed):
            raise ValueError(f"{field} must be finite")
        if minimum is not None and parsed < minimum:
            raise ValueError(f"{field} must be at least {minimum}")
        if maximum is not None and parsed > maximum:
            raise ValueError(f"{field} must be at most {maximum}")
        return parsed

    @classmethod
    def _finite_integer(
        cls,
        value: Any,
        field: str,
        *,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        parsed = cls._finite_number(
            value,
            field,
            minimum=None if minimum is None else float(minimum),
            maximum=None if maximum is None else float(maximum),
        )
        if not parsed.is_integer():
            raise ValueError(f"{field} must be an integer")
        return int(parsed)

    @classmethod
    def _clamp(cls, value: float, low: float = 0.0, high: float = 1.0) -> float:
        """Clamp derived runtime values, while failing closed on numeric poison."""
        parsed = cls._finite_number(value, "derived value")
        return max(low, min(high, parsed))

    @staticmethod
    def _require_bool(value: Any, field: str) -> bool:
        if not isinstance(value, bool):
            raise ValueError(f"{field} must be a boolean")
        return value

    @classmethod
    def _validate_promoted_state(cls, value: Any, path: str = "state") -> None:
        """Reject generated records that try to embed private/hidden material."""
        private_keys = {
            "secret",
            "secrets",
            "private",
            "private_notes",
            "gm_only",
            "hidden",
            "concealed",
        }
        if isinstance(value, dict):
            for raw_key, child in value.items():
                key = str(raw_key).strip().lower()
                if key in private_keys:
                    raise ValueError(f"{path}.{raw_key} is not allowed in generated population state")
                if key == "visibility" and str(child).strip().lower() != "public":
                    raise ValueError(f"{path}.{raw_key} must be public")
                cls._validate_promoted_state(child, f"{path}.{raw_key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                cls._validate_promoted_state(child, f"{path}[{index}]")
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")

    @classmethod
    def _validate_state_numbers(cls, value: Any, path: str = "state") -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                cls._validate_state_numbers(child, f"{path}.{raw_key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                cls._validate_state_numbers(child, f"{path}[{index}]")
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")

    @classmethod
    def _validate_profile_state(cls, state: dict[str, Any]) -> None:
        cls._validate_state_numbers(state)
        for key in (
            "derive_employment_from_labor",
            "emit_service_events",
            "food_capacity_known",
            "housing_capacity_known",
            "water_capacity_known",
        ):
            if key in state:
                cls._require_bool(state[key], f"state.{key}")
        if "average_household_size" in state:
            cls._finite_number(
                state["average_household_size"],
                "state.average_household_size",
                minimum=1.0,
            )
        thresholds = state.get("rank_thresholds")
        if thresholds is not None:
            if not isinstance(thresholds, dict):
                raise ValueError("state.rank_thresholds must be an object")
            for rank, minimum in thresholds.items():
                cls._finite_number(
                    minimum,
                    f"state.rank_thresholds.{rank}",
                    minimum=0.0,
                )
        requirements = state.get("service_requirements")
        if requirements is not None:
            if not isinstance(requirements, dict):
                raise ValueError("state.service_requirements must be an object")
            for kind, raw in requirements.items():
                if isinstance(raw, dict) and "required_capacity" in raw:
                    cls._finite_number(
                        raw["required_capacity"],
                        f"state.service_requirements.{kind}.required_capacity",
                        minimum=0.0,
                    )
                else:
                    per = raw.get("people_per_unit", 1) if isinstance(raw, dict) else raw
                    cls._finite_number(
                        per,
                        f"state.service_requirements.{kind}.people_per_unit",
                        minimum=1e-9,
                    )

    @classmethod
    def _validate_cohort_state(cls, state: dict[str, Any]) -> None:
        cls._validate_state_numbers(state)
        if "child_death_rate_annual" in state:
            cls._finite_number(
                state["child_death_rate_annual"],
                "state.child_death_rate_annual",
                minimum=0.0,
            )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _campaign_time_db(self, db: sqlite3.Connection, campaign_id: str) -> datetime:
        row = db.execute("SELECT world_time FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if not row:
            raise KeyError(f"unknown campaign: {campaign_id}")
        return self._utc(datetime.fromisoformat(str(row["world_time"])))

    @staticmethod
    def _table_exists_db(db: sqlite3.Connection, table: str) -> bool:
        return db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None

    def seed_defaults_db(self, db: sqlite3.Connection, campaign_id: str) -> None:
        now = self.e._now()
        db.execute(
            """INSERT INTO population_config(
                   campaign_id,enabled,births_enabled,mortality_enabled,migration_enabled,
                   households_enabled,settlement_enabled,service_gaps_enabled,
                   default_birth_rate_annual,default_death_rate_annual,
                   max_migration_fraction_per_day,minimum_pull_delta,
                   service_event_cooldown_days,state_json,updated_at)
               VALUES(?,1,1,1,1,1,1,1,0,0,0.02,0.05,30,'{}',?)
               ON CONFLICT(campaign_id) DO NOTHING""",
            (campaign_id, now),
        )

    def _config_db(self, db: sqlite3.Connection, campaign_id: str) -> sqlite3.Row:
        self.seed_defaults_db(db, campaign_id)
        row = db.execute(
            "SELECT * FROM population_config WHERE campaign_id=?", (campaign_id,)
        ).fetchone()
        if row is None:
            raise RuntimeError("population config installation failed")
        return row

    def configure(self, campaign_id: str, **values: Any) -> dict[str, Any]:
        allowed_bool = {
            "enabled",
            "births_enabled",
            "mortality_enabled",
            "migration_enabled",
            "households_enabled",
            "settlement_enabled",
            "service_gaps_enabled",
        }
        allowed_float = {
            "default_birth_rate_annual",
            "default_death_rate_annual",
            "max_migration_fraction_per_day",
            "minimum_pull_delta",
        }
        allowed_int = {"service_event_cooldown_days"}
        unknown = set(values) - allowed_bool - allowed_float - allowed_int - {"state"}
        if unknown:
            raise ValueError(f"unknown population config fields: {sorted(unknown)}")
        assignments: list[str] = []
        params: list[Any] = []
        for key in sorted(allowed_bool & set(values)):
            assignments.append(f"{key}=?")
            params.append(int(self._require_bool(values[key], key)))
        for key in sorted(allowed_float & set(values)):
            maximum = (
                1.0
                if key == "max_migration_fraction_per_day"
                else (2.0 if key == "minimum_pull_delta" else None)
            )
            v = self._finite_number(values[key], key, minimum=0.0, maximum=maximum)
            assignments.append(f"{key}=?")
            params.append(v)
        for key in sorted(allowed_int & set(values)):
            v = self._finite_integer(values[key], key, minimum=0, maximum=36500)
            assignments.append(f"{key}=?")
            params.append(v)
        if "state" in values:
            if values["state"] is not None and not isinstance(values["state"], dict):
                raise ValueError("state must be an object")
            self._validate_state_numbers(values.get("state") or {})
            assignments.append("state_json=?")
            params.append(self.e._dumps(dict(values.get("state") or {})))
        with self.e._write_db() as db:
            self._config_db(db, campaign_id)
            if assignments:
                assignments.append("updated_at=?")
                params.append(self.e._now())
                params.append(campaign_id)
                db.execute(
                    f"UPDATE population_config SET {','.join(assignments)} WHERE campaign_id=?",
                    params,
                )
            row = db.execute(
                "SELECT * FROM population_config WHERE campaign_id=?", (campaign_id,)
            ).fetchone()
        return self._decode_config(row)

    def _decode_config(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for key in (
            "enabled",
            "births_enabled",
            "mortality_enabled",
            "migration_enabled",
            "households_enabled",
            "settlement_enabled",
            "service_gaps_enabled",
        ):
            data[key] = bool(data[key])
        data["state"] = self.e._loads(data.pop("state_json") or "{}")
        return data

    def has_activity_db(self, db: sqlite3.Connection, campaign_id: str) -> bool:
        cfg = self._config_db(db, campaign_id)
        if not bool(cfg["enabled"]):
            return False
        row = db.execute(
            """SELECT
                 EXISTS(SELECT 1 FROM population_state WHERE campaign_id=? AND population>0) OR
                 EXISTS(SELECT 1 FROM population_cohorts WHERE campaign_id=? AND count>0) OR
                 EXISTS(SELECT 1 FROM settlement_profiles WHERE campaign_id=?) AS active""",
            (campaign_id, campaign_id, campaign_id),
        ).fetchone()
        return bool(row["active"])

    # ------------------------------------------------------------------
    # Authoritative setup / compatibility synchronization
    # ------------------------------------------------------------------

    def _validate_location_db(
        self, db: sqlite3.Connection, campaign_id: str, location_id: str
    ) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM locations WHERE campaign_id=? AND id=?",
            (campaign_id, location_id),
        ).fetchone()
        if not row:
            raise KeyError(f"unknown location: {location_id}")
        return row

    def _population_state_db(
        self, db: sqlite3.Connection, campaign_id: str, location_id: str
    ) -> sqlite3.Row | None:
        return db.execute(
            "SELECT * FROM population_state WHERE campaign_id=? AND location_id=?",
            (campaign_id, location_id),
        ).fetchone()

    def _ensure_population_state_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        location_id: str,
        *,
        population: float = 0.0,
        food_capacity: float | None = None,
    ) -> sqlite3.Row:
        self._validate_location_db(db, campaign_id, location_id)
        now = self.e._now()
        pop = self._finite_number(population, "population", minimum=0.0)
        food = (
            pop
            if food_capacity is None
            else self._finite_number(food_capacity, "food_capacity", minimum=0.0)
        )
        db.execute(
            """INSERT INTO population_state(
                   campaign_id,location_id,population,food_capacity,safety,employment,migration_pressure,state_json,updated_at)
               VALUES(?,?,?, ?,0.5,0.5,0,'{}',?)
               ON CONFLICT(campaign_id,location_id) DO NOTHING""",
            (campaign_id, location_id, pop, food, now),
        )
        row = self._population_state_db(db, campaign_id, location_id)
        if row is None:
            raise RuntimeError("population_state installation failed")
        return row

    def _profile_db(
        self, db: sqlite3.Connection, campaign_id: str, location_id: str
    ) -> sqlite3.Row | None:
        return db.execute(
            "SELECT * FROM settlement_profiles WHERE campaign_id=? AND location_id=?",
            (campaign_id, location_id),
        ).fetchone()

    def _population_state_view_db(
        self, db: sqlite3.Connection, campaign_id: str, location_id: str
    ) -> dict[str, Any]:
        """Return a read-only compatibility summary without materializing rows."""
        row = self._population_state_db(db, campaign_id, location_id)
        cohort = db.execute(
            "SELECT COUNT(*) n,COALESCE(SUM(count),0) population FROM population_cohorts WHERE campaign_id=? AND location_id=?",
            (campaign_id, location_id),
        ).fetchone()
        cohort_rows = int(cohort["n"]) if cohort else 0
        cohort_population = max(0.0, float(cohort["population"])) if cohort else 0.0
        if row is not None:
            data = dict(row)
            if cohort_rows:
                data["population"] = cohort_population
            return data
        return {
            "campaign_id": campaign_id,
            "location_id": location_id,
            "population": cohort_population,
            "food_capacity": 0.0,
            "safety": 0.5,
            "employment": 0.5,
            "migration_pressure": 0.0,
            "state_json": "{}",
            "updated_at": None,
        }

    def _profile_view_db(
        self, db: sqlite3.Connection, campaign_id: str, location_id: str, population: float
    ) -> dict[str, Any]:
        """Return a read-only settlement profile, synthesizing neutral defaults."""
        row = self._profile_db(db, campaign_id, location_id)
        if row is not None:
            return dict(row)
        world_time = self._campaign_time_db(db, campaign_id).isoformat()
        population = self._finite_number(population, "population", minimum=0.0)
        return {
            "campaign_id": campaign_id,
            "location_id": location_id,
            "settlement_type": "settlement",
            "rank": "unranked",
            "housing_capacity": population,
            "water_capacity": population,
            "sanitation": 0.5,
            "healthcare": 0.5,
            "prosperity": 0.5,
            "stability": 0.5,
            "attractiveness": 0.5,
            "auto_rank": 0,
            "founded_world_time": world_time,
            "last_processed_world_time": world_time,
            "state_json": self.e._dumps({"virtual_default": True}),
            "updated_at": None,
        }

    def _ensure_profile_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        location_id: str,
        *,
        population_hint: float | None = None,
    ) -> sqlite3.Row:
        self._validate_location_db(db, campaign_id, location_id)
        row = self._profile_db(db, campaign_id, location_id)
        if row:
            return row
        ps = self._population_state_db(db, campaign_id, location_id)
        pop = self._finite_number(
            population_hint if population_hint is not None else (ps["population"] if ps else 0.0),
            "population_hint",
            minimum=0.0,
        )
        now_world = self._campaign_time_db(db, campaign_id).isoformat()
        db.execute(
            """INSERT INTO settlement_profiles(
                   campaign_id,location_id,settlement_type,rank,housing_capacity,water_capacity,
                   sanitation,healthcare,prosperity,stability,attractiveness,auto_rank,
                   founded_world_time,last_processed_world_time,state_json,updated_at)
               VALUES(?,?,'settlement','unranked',?,?,0.5,0.5,0.5,0.5,0.5,0,?,?,?,?)""",
            (
                campaign_id,
                location_id,
                pop,
                pop,
                now_world,
                now_world,
                self.e._dumps({"capacity_defaults_from_population": True}),
                self.e._now(),
            ),
        )
        result = self._profile_db(db, campaign_id, location_id)
        if result is None:
            raise RuntimeError("settlement profile installation failed")
        return result

    def save_settlement(
        self,
        campaign_id: str,
        location_id: str,
        *,
        settlement_type: str = "settlement",
        rank: str = "unranked",
        housing_capacity: float | None = None,
        water_capacity: float | None = None,
        sanitation: float = 0.5,
        healthcare: float = 0.5,
        prosperity: float = 0.5,
        stability: float = 0.5,
        attractiveness: float = 0.5,
        auto_rank: bool = False,
        founded_world_time: str | None = None,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        campaign_id = self.e._clean_id(campaign_id)
        location_id = self.e._clean_id(location_id)
        settlement_type = str(settlement_type or "settlement")[:80]
        rank = str(rank or "unranked")[:80]
        if state is not None and not isinstance(state, dict):
            raise ValueError("state must be an object")
        self._validate_profile_state(state or {})
        with self.e._write_db() as db:
            self._validate_location_db(db, campaign_id, location_id)
            ps = self._ensure_population_state_db(db, campaign_id, location_id)
            pop = self._finite_number(ps["population"], "population", minimum=0.0)
            current = self._profile_db(db, campaign_id, location_id)
            housing = self._finite_number(
                housing_capacity
                if housing_capacity is not None
                else (current["housing_capacity"] if current else pop),
                "housing_capacity",
                minimum=0.0,
            )
            water = self._finite_number(
                water_capacity
                if water_capacity is not None
                else (current["water_capacity"] if current else pop),
                "water_capacity",
                minimum=0.0,
            )
            now_world = self._campaign_time_db(db, campaign_id).isoformat()
            founded = founded_world_time or (current["founded_world_time"] if current else now_world)
            datetime.fromisoformat(str(founded))
            db.execute(
                """INSERT INTO settlement_profiles(
                       campaign_id,location_id,settlement_type,rank,housing_capacity,water_capacity,
                       sanitation,healthcare,prosperity,stability,attractiveness,auto_rank,
                       founded_world_time,last_processed_world_time,state_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,location_id) DO UPDATE SET
                       settlement_type=excluded.settlement_type,rank=excluded.rank,
                       housing_capacity=excluded.housing_capacity,water_capacity=excluded.water_capacity,
                       sanitation=excluded.sanitation,healthcare=excluded.healthcare,
                       prosperity=excluded.prosperity,stability=excluded.stability,
                       attractiveness=excluded.attractiveness,auto_rank=excluded.auto_rank,
                       founded_world_time=excluded.founded_world_time,state_json=excluded.state_json,
                       updated_at=excluded.updated_at""",
                (
                    campaign_id,
                    location_id,
                    settlement_type,
                    rank,
                    housing,
                    water,
                    self._finite_number(sanitation, "sanitation", minimum=0.0, maximum=1.0),
                    self._finite_number(healthcare, "healthcare", minimum=0.0, maximum=1.0),
                    self._finite_number(prosperity, "prosperity", minimum=0.0, maximum=1.0),
                    self._finite_number(stability, "stability", minimum=0.0, maximum=1.0),
                    self._finite_number(attractiveness, "attractiveness", minimum=0.0, maximum=1.0),
                    int(self._require_bool(auto_rank, "auto_rank")),
                    founded,
                    current["last_processed_world_time"] if current else now_world,
                    self.e._dumps(state or {}),
                    self.e._now(),
                ),
            )
            self._bootstrap_location_db(db, campaign_id, location_id)
            rev = self.e._next_revision(db, campaign_id)
            self.e._insert_event(
                db,
                campaign_id,
                rev,
                "settlement_profile_saved",
                f"Settlement profile saved: {location_id}",
                region=location_id,
                payload={"location_id": location_id, "rank": rank, "settlement_type": settlement_type},
            )
            snapshot = self._snapshot_location_db(db, campaign_id, location_id)
            snapshot["revision"] = rev
            return snapshot

    def _legacy_cohort_id(self, location_id: str) -> str:
        digest = hashlib.sha256(location_id.encode("utf-8")).hexdigest()[:16]
        return f"legacy:{digest}"

    def _auto_cohort_id(self, identity: Iterable[Any], prefix: str) -> str:
        raw = "|".join("" if v is None else str(v) for v in identity)
        return f"{prefix}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"

    def _bootstrap_location_db(
        self, db: sqlite3.Connection, campaign_id: str, location_id: str
    ) -> int:
        ps = self._ensure_population_state_db(db, campaign_id, location_id)
        self._ensure_profile_db(
            db, campaign_id, location_id, population_hint=float(ps["population"])
        )
        count = int(
            db.execute(
                "SELECT COUNT(*) n FROM population_cohorts WHERE campaign_id=? AND location_id=?",
                (campaign_id, location_id),
            ).fetchone()["n"]
        )
        if count:
            return 0
        pop = max(0.0, float(ps["population"]))
        if pop <= 0:
            return 0
        cfg = self._config_db(db, campaign_id)
        now_world = self._campaign_time_db(db, campaign_id).isoformat()
        cohort_id = self._legacy_cohort_id(location_id)
        db.execute(
            """INSERT INTO population_cohorts(
                   campaign_id,id,location_id,species,culture,faction_id,age_band,livelihood,count,
                   birth_rate_annual,death_rate_annual,labor_participation,migration_affinity,
                   health,wealth,next_cohort_id,transition_rate_annual,state_json,
                   last_processed_world_time,updated_at)
               VALUES(?,?,?,'unspecified','unspecified',NULL,'mixed','mixed',?,?,?,?,1,0.75,0.5,NULL,0,?,?,?)""",
            (
                campaign_id,
                cohort_id,
                location_id,
                pop,
                float(cfg["default_birth_rate_annual"]),
                float(cfg["default_death_rate_annual"]),
                0.55,
                self.e._dumps({"legacy_aggregate": True}),
                now_world,
                self.e._now(),
            ),
        )
        return 1

    def bootstrap_all_db(self, db: sqlite3.Connection, campaign_id: str) -> int:
        rows = db.execute(
            """SELECT location_id FROM population_state WHERE campaign_id=?
               UNION SELECT location_id FROM settlement_profiles WHERE campaign_id=?
               ORDER BY location_id""",
            (campaign_id, campaign_id),
        ).fetchall()
        return sum(
            self._bootstrap_location_db(db, campaign_id, str(row["location_id"]))
            for row in rows
        )

    @staticmethod
    def _canonical_row_matches(
        row: sqlite3.Row, expected: dict[str, Any]
    ) -> bool:
        return all(row[key] == value for key, value in expected.items())

    def promote_records_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        sections: dict[str, Any],
    ) -> dict[str, int]:
        """Promote bounded generated population records inside the caller's transaction.

        The operation is additive. Missing rows are inserted, byte-canonical
        equivalents are idempotent, and a reused key with different content aborts
        the entire promotion savepoint. It deliberately does not advance revision,
        emit an event, commit, or alter ``PRAGMA user_version``; the owning authoring
        transaction remains responsible for its receipt and commit.
        """
        campaign_id = self.e._clean_id(campaign_id)
        if not isinstance(sections, dict):
            raise ValueError("population sections must be an object")
        supported = {"settlement_profiles", "population_cohorts"}
        unknown = set(sections) - supported
        if unknown:
            raise ValueError(f"unsupported population sections: {sorted(unknown)}")
        settlement_rows = sections.get("settlement_profiles", [])
        cohort_rows = sections.get("population_cohorts", [])
        if not isinstance(settlement_rows, list) or not isinstance(cohort_rows, list):
            raise ValueError("population sections must be arrays")
        if len(settlement_rows) > 100:
            raise ValueError("at most 100 settlement profiles may be promoted")
        if len(cohort_rows) > 500:
            raise ValueError("at most 500 population cohorts may be promoted")

        result = {
            "settlement_profiles_inserted": 0,
            "settlement_profiles_replayed": 0,
            "population_cohorts_inserted": 0,
            "population_cohorts_replayed": 0,
        }
        touched_locations: set[str] = set()
        savepoint = "population_promote_records"
        db.execute(f"SAVEPOINT {savepoint}")
        try:
            now_world = self._campaign_time_db(db, campaign_id).isoformat()
            for index, raw_record in enumerate(settlement_rows):
                if not isinstance(raw_record, dict):
                    raise ValueError(f"settlement_profiles[{index}] must be an object")
                record = dict(raw_record)
                unknown_fields = set(record) - {
                    "id",
                    "location_id",
                    "settlement_type",
                    "rank",
                    "housing_capacity",
                    "water_capacity",
                    "sanitation",
                    "healthcare",
                    "prosperity",
                    "stability",
                    "attractiveness",
                    "auto_rank",
                    "founded_world_time",
                    "state",
                    "visibility",
                }
                if unknown_fields:
                    raise ValueError(
                        f"settlement_profiles[{index}] has unsupported fields: {sorted(unknown_fields)}"
                    )
                visibility = str(record.pop("visibility", "public")).strip().lower()
                if visibility != "public":
                    raise ValueError(
                        f"settlement_profiles[{index}].visibility must be public"
                    )
                raw_location = record.pop("location_id", record.pop("id", None))
                if raw_location is None:
                    raise ValueError(f"settlement_profiles[{index}] requires location_id")
                location_id = self.e._clean_id(str(raw_location))
                self._validate_location_db(db, campaign_id, location_id)
                state = record.pop("state", {}) or {}
                if not isinstance(state, dict):
                    raise ValueError(f"settlement_profiles[{index}].state must be an object")
                self._validate_promoted_state(state, f"settlement_profiles[{index}].state")
                self._validate_profile_state(state)
                founded = str(record.pop("founded_world_time", now_world))
                datetime.fromisoformat(founded)
                expected = {
                    "location_id": location_id,
                    "settlement_type": str(record.pop("settlement_type", "settlement") or "settlement")[:80],
                    "rank": str(record.pop("rank", "unranked") or "unranked")[:80],
                    "housing_capacity": self._finite_number(
                        record.pop("housing_capacity", 0),
                        f"settlement_profiles[{index}].housing_capacity",
                        minimum=0.0,
                    ),
                    "water_capacity": self._finite_number(
                        record.pop("water_capacity", 0),
                        f"settlement_profiles[{index}].water_capacity",
                        minimum=0.0,
                    ),
                    "sanitation": self._finite_number(
                        record.pop("sanitation", 0.5),
                        f"settlement_profiles[{index}].sanitation",
                        minimum=0.0,
                        maximum=1.0,
                    ),
                    "healthcare": self._finite_number(
                        record.pop("healthcare", 0.5),
                        f"settlement_profiles[{index}].healthcare",
                        minimum=0.0,
                        maximum=1.0,
                    ),
                    "prosperity": self._finite_number(
                        record.pop("prosperity", 0.5),
                        f"settlement_profiles[{index}].prosperity",
                        minimum=0.0,
                        maximum=1.0,
                    ),
                    "stability": self._finite_number(
                        record.pop("stability", 0.5),
                        f"settlement_profiles[{index}].stability",
                        minimum=0.0,
                        maximum=1.0,
                    ),
                    "attractiveness": self._finite_number(
                        record.pop("attractiveness", 0.5),
                        f"settlement_profiles[{index}].attractiveness",
                        minimum=0.0,
                        maximum=1.0,
                    ),
                    "auto_rank": int(
                        self._require_bool(
                            record.pop("auto_rank", False),
                            f"settlement_profiles[{index}].auto_rank",
                        )
                    ),
                    "founded_world_time": founded,
                    "state_json": self.e._dumps(state),
                }
                existing = self._profile_db(db, campaign_id, location_id)
                if existing:
                    if not self._canonical_row_matches(existing, expected):
                        raise ValueError(
                            f"settlement profile conflict for location {location_id}"
                        )
                    result["settlement_profiles_replayed"] += 1
                else:
                    db.execute(
                        """INSERT INTO settlement_profiles(
                               campaign_id,location_id,settlement_type,rank,
                               housing_capacity,water_capacity,sanitation,healthcare,
                               prosperity,stability,attractiveness,auto_rank,
                               founded_world_time,last_processed_world_time,state_json,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            campaign_id,
                            expected["location_id"],
                            expected["settlement_type"],
                            expected["rank"],
                            expected["housing_capacity"],
                            expected["water_capacity"],
                            expected["sanitation"],
                            expected["healthcare"],
                            expected["prosperity"],
                            expected["stability"],
                            expected["attractiveness"],
                            expected["auto_rank"],
                            expected["founded_world_time"],
                            now_world,
                            expected["state_json"],
                            self.e._now(),
                        ),
                    )
                    result["settlement_profiles_inserted"] += 1
            config = db.execute(
                "SELECT * FROM population_config WHERE campaign_id=?",
                (campaign_id,),
            ).fetchone()
            default_birth_rate = float(config["default_birth_rate_annual"]) if config else 0.0
            default_death_rate = float(config["default_death_rate_annual"]) if config else 0.0
            next_references: list[str] = []
            for index, raw_record in enumerate(cohort_rows):
                if not isinstance(raw_record, dict):
                    raise ValueError(f"population_cohorts[{index}] must be an object")
                record = dict(raw_record)
                unknown_fields = set(record) - {
                    "id",
                    "cohort_id",
                    "location_id",
                    "species",
                    "culture",
                    "faction_id",
                    "age_band",
                    "livelihood",
                    "count",
                    "birth_rate_annual",
                    "death_rate_annual",
                    "labor_participation",
                    "migration_affinity",
                    "health",
                    "wealth",
                    "next_cohort_id",
                    "transition_rate_annual",
                    "state",
                    "visibility",
                }
                if unknown_fields:
                    raise ValueError(
                        f"population_cohorts[{index}] has unsupported fields: {sorted(unknown_fields)}"
                    )
                visibility = str(record.pop("visibility", "public")).strip().lower()
                if visibility != "public":
                    raise ValueError(f"population_cohorts[{index}].visibility must be public")
                if "location_id" not in record:
                    raise ValueError(f"population_cohorts[{index}] requires location_id")
                location_id = self.e._clean_id(str(record.pop("location_id")))
                self._validate_location_db(db, campaign_id, location_id)
                species = str(record.pop("species", "unspecified") or "unspecified")[:100]
                culture = str(record.pop("culture", "unspecified") or "unspecified")[:100]
                faction_raw = record.pop("faction_id", None)
                faction_id = self.e._clean_id(str(faction_raw)) if faction_raw else None
                if faction_id and not db.execute(
                    "SELECT 1 FROM factions WHERE campaign_id=? AND id=?",
                    (campaign_id, faction_id),
                ).fetchone():
                    raise KeyError(f"unknown faction: {faction_id}")
                age_band = str(record.pop("age_band", "mixed"))
                if age_band not in {"child", "adult", "elder", "mixed"}:
                    raise ValueError("age_band must be child, adult, elder, or mixed")
                livelihood = str(record.pop("livelihood", "mixed") or "mixed")[:100]
                raw_id = record.pop("cohort_id", record.pop("id", None))
                cohort_id = (
                    self.e._clean_id(str(raw_id))
                    if raw_id is not None
                    else self._auto_cohort_id(
                        (location_id, species, culture, faction_id, age_band, livelihood),
                        "cohort",
                    )
                )
                next_raw = record.pop("next_cohort_id", None)
                next_cohort_id = self.e._clean_id(str(next_raw)) if next_raw else None
                state = record.pop("state", {}) or {}
                if not isinstance(state, dict):
                    raise ValueError(f"population_cohorts[{index}].state must be an object")
                self._validate_promoted_state(state, f"population_cohorts[{index}].state")
                self._validate_cohort_state(state)
                expected = {
                    "id": cohort_id,
                    "location_id": location_id,
                    "species": species,
                    "culture": culture,
                    "faction_id": faction_id,
                    "age_band": age_band,
                    "livelihood": livelihood,
                    "count": self._finite_number(
                        record.pop("count", 0),
                        f"population_cohorts[{index}].count",
                        minimum=0.0,
                    ),
                    "birth_rate_annual": self._finite_number(
                        record.pop("birth_rate_annual", default_birth_rate),
                        f"population_cohorts[{index}].birth_rate_annual",
                        minimum=0.0,
                    ),
                    "death_rate_annual": self._finite_number(
                        record.pop("death_rate_annual", default_death_rate),
                        f"population_cohorts[{index}].death_rate_annual",
                        minimum=0.0,
                    ),
                    "labor_participation": self._finite_number(
                        record.pop("labor_participation", 0.55),
                        f"population_cohorts[{index}].labor_participation",
                        minimum=0.0,
                        maximum=1.0,
                    ),
                    "migration_affinity": self._finite_number(
                        record.pop("migration_affinity", 1.0),
                        f"population_cohorts[{index}].migration_affinity",
                        minimum=0.0,
                        maximum=2.0,
                    ),
                    "health": self._finite_number(
                        record.pop("health", 0.75),
                        f"population_cohorts[{index}].health",
                        minimum=0.0,
                        maximum=1.0,
                    ),
                    "wealth": self._finite_number(
                        record.pop("wealth", 0.5),
                        f"population_cohorts[{index}].wealth",
                        minimum=0.0,
                        maximum=1.0,
                    ),
                    "next_cohort_id": next_cohort_id,
                    "transition_rate_annual": self._finite_number(
                        record.pop("transition_rate_annual", 0),
                        f"population_cohorts[{index}].transition_rate_annual",
                        minimum=0.0,
                    ),
                    "state_json": self.e._dumps(state),
                }
                existing = db.execute(
                    "SELECT * FROM population_cohorts WHERE campaign_id=? AND id=?",
                    (campaign_id, cohort_id),
                ).fetchone()
                if existing:
                    if not self._canonical_row_matches(existing, expected):
                        raise ValueError(f"population cohort conflict for id {cohort_id}")
                    result["population_cohorts_replayed"] += 1
                else:
                    self._ensure_population_state_db(db, campaign_id, location_id)
                    self._upsert_cohort_db(
                        db,
                        campaign_id,
                        cohort_id,
                        location_id,
                        species=species,
                        culture=culture,
                        faction_id=faction_id,
                        age_band=age_band,
                        livelihood=livelihood,
                        count=expected["count"],
                        birth_rate_annual=expected["birth_rate_annual"],
                        death_rate_annual=expected["death_rate_annual"],
                        labor_participation=expected["labor_participation"],
                        migration_affinity=expected["migration_affinity"],
                        health=expected["health"],
                        wealth=expected["wealth"],
                        next_cohort_id=next_cohort_id,
                        transition_rate_annual=expected["transition_rate_annual"],
                        state=state,
                        preserve_cursor=False,
                    )
                    result["population_cohorts_inserted"] += 1
                    touched_locations.add(location_id)
                if next_cohort_id:
                    next_references.append(next_cohort_id)

            for next_cohort_id in next_references:
                if not db.execute(
                    "SELECT 1 FROM population_cohorts WHERE campaign_id=? AND id=?",
                    (campaign_id, next_cohort_id),
                ).fetchone():
                    raise KeyError(f"unknown next population cohort: {next_cohort_id}")

            for location_id in sorted(touched_locations):
                if db.execute(
                    "SELECT 1 FROM population_cohorts WHERE campaign_id=? AND location_id=?",
                    (campaign_id, location_id),
                ).fetchone():
                    self._sync_population_summary_db(db, campaign_id, location_id)
            db.execute(f"RELEASE SAVEPOINT {savepoint}")
            return result
        except Exception:
            db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            db.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise

    def _upsert_cohort_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        cohort_id: str,
        location_id: str,
        *,
        species: str = "unspecified",
        culture: str = "unspecified",
        faction_id: str | None = None,
        age_band: str = "mixed",
        livelihood: str = "mixed",
        count: float = 0,
        birth_rate_annual: float | None = None,
        death_rate_annual: float | None = None,
        labor_participation: float = 0.55,
        migration_affinity: float = 1.0,
        health: float = 0.75,
        wealth: float = 0.5,
        next_cohort_id: str | None = None,
        transition_rate_annual: float = 0,
        state: dict[str, Any] | None = None,
        preserve_cursor: bool = True,
    ) -> None:
        campaign_id = self.e._clean_id(campaign_id)
        cohort_id = self.e._clean_id(cohort_id)
        location_id = self.e._clean_id(location_id)
        faction_id = self.e._clean_id(faction_id) if faction_id else None
        next_cohort_id = self.e._clean_id(next_cohort_id) if next_cohort_id else None
        self._validate_location_db(db, campaign_id, location_id)
        if age_band not in {"child", "adult", "elder", "mixed"}:
            raise ValueError("age_band must be child, adult, elder, or mixed")
        if faction_id and not db.execute(
            "SELECT 1 FROM factions WHERE campaign_id=? AND id=?",
            (campaign_id, faction_id),
        ).fetchone():
            raise KeyError(f"unknown faction: {faction_id}")
        cfg = self._config_db(db, campaign_id)
        existing = db.execute(
            "SELECT last_processed_world_time FROM population_cohorts WHERE campaign_id=? AND id=?",
            (campaign_id, cohort_id),
        ).fetchone()
        now_world = self._campaign_time_db(db, campaign_id).isoformat()
        cursor = (
            str(existing["last_processed_world_time"])
            if existing and preserve_cursor
            else now_world
        )
        birth_rate = self._finite_number(
            cfg["default_birth_rate_annual"] if birth_rate_annual is None else birth_rate_annual,
            "birth_rate_annual",
            minimum=0.0,
        )
        death_rate = self._finite_number(
            cfg["default_death_rate_annual"] if death_rate_annual is None else death_rate_annual,
            "death_rate_annual",
            minimum=0.0,
        )
        if state is not None and not isinstance(state, dict):
            raise ValueError("state must be an object")
        self._validate_cohort_state(state or {})
        db.execute(
            """INSERT INTO population_cohorts(
                   campaign_id,id,location_id,species,culture,faction_id,age_band,livelihood,count,
                   birth_rate_annual,death_rate_annual,labor_participation,migration_affinity,
                   health,wealth,next_cohort_id,transition_rate_annual,state_json,
                   last_processed_world_time,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(campaign_id,id) DO UPDATE SET
                   location_id=excluded.location_id,species=excluded.species,culture=excluded.culture,
                   faction_id=excluded.faction_id,age_band=excluded.age_band,livelihood=excluded.livelihood,
                   count=excluded.count,birth_rate_annual=excluded.birth_rate_annual,
                   death_rate_annual=excluded.death_rate_annual,labor_participation=excluded.labor_participation,
                   migration_affinity=excluded.migration_affinity,health=excluded.health,wealth=excluded.wealth,
                   next_cohort_id=excluded.next_cohort_id,transition_rate_annual=excluded.transition_rate_annual,
                   state_json=excluded.state_json,last_processed_world_time=excluded.last_processed_world_time,
                   updated_at=excluded.updated_at""",
            (
                campaign_id,
                cohort_id,
                location_id,
                str(species or "unspecified")[:100],
                str(culture or "unspecified")[:100],
                faction_id,
                age_band,
                str(livelihood or "mixed")[:100],
                self._finite_number(count, "count", minimum=0.0),
                birth_rate,
                death_rate,
                self._finite_number(labor_participation, "labor_participation", minimum=0.0, maximum=1.0),
                self._finite_number(migration_affinity, "migration_affinity", minimum=0.0, maximum=2.0),
                self._finite_number(health, "health", minimum=0.0, maximum=1.0),
                self._finite_number(wealth, "wealth", minimum=0.0, maximum=1.0),
                next_cohort_id,
                self._finite_number(transition_rate_annual, "transition_rate_annual", minimum=0.0),
                self.e._dumps(state or {}),
                cursor,
                self.e._now(),
            ),
        )

    def save_cohort(self, campaign_id: str, cohort_id: str, location_id: str, **values: Any) -> dict[str, Any]:
        campaign_id = self.e._clean_id(campaign_id)
        cohort_id = self.e._clean_id(cohort_id)
        location_id = self.e._clean_id(location_id)
        replace_legacy_raw = values.pop("replace_legacy", False)
        replace_legacy = self._require_bool(replace_legacy_raw, "replace_legacy")
        with self.e._write_db() as db:
            self._ensure_population_state_db(db, campaign_id, location_id)
            self._ensure_profile_db(db, campaign_id, location_id)
            if replace_legacy:
                db.execute(
                    "DELETE FROM population_cohorts WHERE campaign_id=? AND location_id=? AND json_extract(state_json,'$.legacy_aggregate')=1",
                    (campaign_id, location_id),
                )
            self._upsert_cohort_db(
                db, campaign_id, cohort_id, location_id, **values
            )
            self._sync_population_summary_db(db, campaign_id, location_id)
            rev = self.e._next_revision(db, campaign_id)
            self.e._insert_event(
                db,
                campaign_id,
                rev,
                "population_cohort_saved",
                f"Population cohort saved: {cohort_id}",
                region=location_id,
                payload={"cohort_id": cohort_id, "location_id": location_id},
            )
            result = self._cohort_dict(
                db.execute(
                    "SELECT * FROM population_cohorts WHERE campaign_id=? AND id=?",
                    (campaign_id, cohort_id),
                ).fetchone()
            )
            result["revision"] = rev
            return result

    def replace_cohorts(
        self, campaign_id: str, location_id: str, cohorts: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if len(cohorts) > 200:
            raise ValueError("at most 200 cohorts may be replaced at once")
        campaign_id = self.e._clean_id(campaign_id)
        location_id = self.e._clean_id(location_id)
        with self.e._write_db() as db:
            self._ensure_population_state_db(db, campaign_id, location_id)
            self._ensure_profile_db(db, campaign_id, location_id)
            db.execute(
                "DELETE FROM population_cohorts WHERE campaign_id=? AND location_id=?",
                (campaign_id, location_id),
            )
            for index, raw in enumerate(cohorts):
                spec = dict(raw)
                cohort_id = self.e._clean_id(
                    str(spec.pop("id", f"cohort:{location_id}:{index + 1}"))
                )
                self._upsert_cohort_db(
                    db,
                    campaign_id,
                    cohort_id,
                    location_id,
                    preserve_cursor=False,
                    **spec,
                )
            self._sync_population_summary_db(db, campaign_id, location_id)
            rev = self.e._next_revision(db, campaign_id)
            self.e._insert_event(
                db,
                campaign_id,
                rev,
                "population_cohorts_replaced",
                f"Population cohorts replaced for {location_id}",
                region=location_id,
                payload={"location_id": location_id, "cohort_count": len(cohorts)},
            )
            result = self._snapshot_location_db(db, campaign_id, location_id)
            result["revision"] = rev
            return result

    def save_household(
        self,
        campaign_id: str,
        household_id: str,
        location_id: str,
        *,
        cohort_id: str | None = None,
        household_count: float = 0,
        persons: float = 0,
        adults: float = 0,
        children: float = 0,
        elders: float = 0,
        housing_units: float = 0,
        wealth: float = 0.5,
        food_reserve_days: float = 0,
        livelihood: str = "mixed",
        status: str = "active",
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in {"active", "displaced", "dissolved"}:
            raise ValueError("invalid household status")
        campaign_id = self.e._clean_id(campaign_id)
        household_id = self.e._clean_id(household_id)
        location_id = self.e._clean_id(location_id)
        cohort_id = self.e._clean_id(cohort_id) if cohort_id else None
        if state is not None and not isinstance(state, dict):
            raise ValueError("state must be an object")
        self._validate_state_numbers(state or {})
        with self.e._write_db() as db:
            self._validate_location_db(db, campaign_id, location_id)
            if cohort_id and not db.execute(
                "SELECT 1 FROM population_cohorts WHERE campaign_id=? AND id=?",
                (campaign_id, cohort_id),
            ).fetchone():
                raise KeyError(f"unknown population cohort: {cohort_id}")
            db.execute(
                """INSERT INTO population_households(
                       campaign_id,id,location_id,cohort_id,household_count,persons,adults,children,elders,
                       housing_units,wealth,food_reserve_days,livelihood,status,state_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET
                       location_id=excluded.location_id,cohort_id=excluded.cohort_id,
                       household_count=excluded.household_count,persons=excluded.persons,
                       adults=excluded.adults,children=excluded.children,elders=excluded.elders,
                       housing_units=excluded.housing_units,wealth=excluded.wealth,
                       food_reserve_days=excluded.food_reserve_days,livelihood=excluded.livelihood,
                       status=excluded.status,state_json=excluded.state_json,updated_at=excluded.updated_at""",
                (
                    campaign_id,
                    household_id,
                    location_id,
                    cohort_id,
                    self._finite_number(household_count, "household_count", minimum=0.0),
                    self._finite_number(persons, "persons", minimum=0.0),
                    self._finite_number(adults, "adults", minimum=0.0),
                    self._finite_number(children, "children", minimum=0.0),
                    self._finite_number(elders, "elders", minimum=0.0),
                    self._finite_number(housing_units, "housing_units", minimum=0.0),
                    self._finite_number(wealth, "wealth", minimum=0.0, maximum=1.0),
                    self._finite_number(food_reserve_days, "food_reserve_days", minimum=0.0),
                    str(livelihood or "mixed")[:100],
                    status,
                    self.e._dumps(state or {}),
                    self.e._now(),
                ),
            )
            rev = self.e._next_revision(db, campaign_id)
            self.e._insert_event(
                db,
                campaign_id,
                rev,
                "population_household_saved",
                f"Household aggregate saved: {household_id}",
                region=location_id,
                payload={"household_id": household_id, "location_id": location_id},
            )
            row = db.execute(
                "SELECT * FROM population_households WHERE campaign_id=? AND id=?",
                (campaign_id, household_id),
            ).fetchone()
            result = self._household_dict(row)
            result["revision"] = rev
            return result

    def _cohort_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["state"] = self.e._loads(data.pop("state_json") or "{}")
        return data

    def _household_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["state"] = self.e._loads(data.pop("state_json") or "{}")
        return data

    def _profile_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["auto_rank"] = bool(data["auto_rank"])
        data["state"] = self.e._loads(data.pop("state_json") or "{}")
        return data

    def _sync_population_summary_db(
        self, db: sqlite3.Connection, campaign_id: str, location_id: str
    ) -> float:
        total = float(
            db.execute(
                "SELECT COALESCE(SUM(count),0) n FROM population_cohorts WHERE campaign_id=? AND location_id=?",
                (campaign_id, location_id),
            ).fetchone()["n"]
        )
        ps = self._ensure_population_state_db(
            db, campaign_id, location_id, population=total
        )
        state = self.e._loads(ps["state_json"] or "{}")
        state["population_runtime"] = "cohort_sum"
        state["cohort_count"] = int(
            db.execute(
                "SELECT COUNT(*) n FROM population_cohorts WHERE campaign_id=? AND location_id=?",
                (campaign_id, location_id),
            ).fetchone()["n"]
        )
        db.execute(
            "UPDATE population_state SET population=?,state_json=?,updated_at=? WHERE campaign_id=? AND location_id=?",
            (
                max(0.0, total),
                self.e._dumps(state),
                self.e._now(),
                campaign_id,
                location_id,
            ),
        )
        return max(0.0, total)

    # ------------------------------------------------------------------
    # Derived settlement state
    # ------------------------------------------------------------------

    def _hazard_db(
        self, db: sqlite3.Connection, campaign_id: str, location_id: str
    ) -> tuple[float, dict[str, float]]:
        if not self._table_exists_db(db, "environment_effects"):
            return 0.0, {}
        rows = db.execute(
            """SELECT e.effect_type,MAX(e.intensity) intensity
               FROM environment_effects e
               JOIN environment_targets t
                 ON t.campaign_id=e.campaign_id AND t.target_key=e.target_key
               WHERE e.campaign_id=? AND e.active=1 AND t.active=1 AND t.location_id=?
               GROUP BY e.effect_type ORDER BY e.effect_type""",
            (campaign_id, location_id),
        ).fetchall()
        effects = {
            str(row["effect_type"]): self._clamp(float(row["intensity"]))
            for row in rows
        }
        hazard = max(
            (intensity * DANGEROUS_EFFECTS.get(kind, 0.25) for kind, intensity in effects.items()),
            default=0.0,
        )
        return self._clamp(hazard), effects

    def _configured_food_units_db(
        self, db: sqlite3.Connection, campaign_id: str, location_id: str
    ) -> float:
        """Count only inventory units with explicit population_food_units metadata.

        This avoids inventing a conversion between arbitrary RPG items and a
        settlement's daily carrying capacity.
        """
        rows = db.execute(
            """SELECT i.qty,d.metadata_json
               FROM inventories i JOIN item_defs d
                 ON d.campaign_id=i.campaign_id AND d.id=i.item_id
               WHERE i.campaign_id=? AND i.qty>0 AND (
                    (i.owner_kind='location' AND i.owner_id=?) OR
                    EXISTS(SELECT 1 FROM economy_markets m
                           WHERE m.campaign_id=i.campaign_id AND m.location_id=?
                             AND m.owner_kind=i.owner_kind AND m.owner_id=i.owner_id AND m.active=1)
               )""",
            (campaign_id, location_id, location_id),
        ).fetchall()
        total = 0.0
        for row in rows:
            metadata = self.e._loads(row["metadata_json"] or "{}")
            units = metadata.get("population_food_units")
            if units is None:
                continue
            try:
                total += max(0.0, float(row["qty"])) * max(0.0, float(units))
            except (TypeError, ValueError):
                continue
        return total

    def _settlement_metrics_db(
        self, db: sqlite3.Connection, campaign_id: str, location_id: str
    ) -> dict[str, Any]:
        ps = self._population_state_view_db(db, campaign_id, location_id)
        population = max(0.0, float(ps["population"]))
        profile = self._profile_view_db(db, campaign_id, location_id, population)
        pstate = self.e._loads(ps["state_json"] or "{}")
        profile_state = self.e._loads(profile["state_json"] or "{}")

        explicit_food = float(ps["food_capacity"])
        configured_inventory_food = self._configured_food_units_db(
            db, campaign_id, location_id
        )
        food_known = explicit_food > 0 or configured_inventory_food > 0 or bool(
            pstate.get("food_capacity_known")
        )
        food_capacity = max(0.0, explicit_food + configured_inventory_food)
        food_ratio = 1.0 if not food_known or population <= 0 else food_capacity / population

        housing_capacity = max(0.0, float(profile["housing_capacity"]))
        housing_known = housing_capacity > 0 or bool(profile_state.get("housing_capacity_known"))
        housing_ratio = 1.0 if not housing_known or population <= 0 else housing_capacity / population

        water_capacity = max(0.0, float(profile["water_capacity"]))
        water_known = water_capacity > 0 or bool(profile_state.get("water_capacity_known"))
        water_ratio = 1.0 if not water_known or population <= 0 else water_capacity / population

        hazard, effect_levels = self._hazard_db(db, campaign_id, location_id)
        safety = self._clamp(float(ps["safety"]))
        employment = self._clamp(float(ps["employment"]))
        old_pressure = self._clamp(float(ps["migration_pressure"]))
        service_gap_ratio = self._service_gap_ratio_db(db, campaign_id, location_id)

        derived_pressure = max(
            hazard,
            self._clamp(1.0 - min(1.0, food_ratio)),
            self._clamp(1.0 - min(1.0, housing_ratio)),
            self._clamp(1.0 - min(1.0, water_ratio)),
            self._clamp(max(0.0, 0.5 - employment) * 2.0),
            self._clamp(max(0.0, 0.5 - safety) * 2.0),
            self._clamp(max(0.0, 0.5 - float(profile["stability"])) * 2.0),
            service_gap_ratio * 0.6,
        )
        pressure = self._clamp(old_pressure * 0.75 + derived_pressure * 0.25)
        effective_safety = self._clamp(safety * (1.0 - 0.35 * hazard))
        pull = self._clamp(
            0.22 * float(profile["attractiveness"])
            + 0.18 * float(profile["prosperity"])
            + 0.17 * effective_safety
            + 0.14 * employment
            + 0.10 * min(1.0, food_ratio)
            + 0.08 * min(1.0, housing_ratio)
            + 0.05 * min(1.0, water_ratio)
            + 0.06 * (1.0 - service_gap_ratio)
            - 0.22 * pressure
            - 0.18 * hazard
        )
        return {
            "population": population,
            "food_capacity": food_capacity,
            "food_capacity_known": food_known,
            "food_ratio": max(0.0, food_ratio),
            "housing_capacity": housing_capacity,
            "housing_capacity_known": housing_known,
            "housing_ratio": max(0.0, housing_ratio),
            "water_capacity": water_capacity,
            "water_capacity_known": water_known,
            "water_ratio": max(0.0, water_ratio),
            "safety": effective_safety,
            "employment": employment,
            "migration_pressure": pressure,
            "hazard": hazard,
            "effects": effect_levels,
            "service_gap_ratio": service_gap_ratio,
            "prosperity": float(profile["prosperity"]),
            "stability": float(profile["stability"]),
            "healthcare": float(profile["healthcare"]),
            "sanitation": float(profile["sanitation"]),
            "pull_score": pull,
        }

    def _service_requirements_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        location_id: str,
        population: float,
    ) -> dict[str, float]:
        profile = self._ensure_profile_db(
            db, campaign_id, location_id, population_hint=population
        )
        state = self.e._loads(profile["state_json"] or "{}")
        configured = state.get("service_requirements")
        requirements: dict[str, float] = {}
        if isinstance(configured, dict):
            for key, raw in configured.items():
                if isinstance(raw, dict):
                    if "required_capacity" in raw:
                        required = self._finite_number(
                            raw["required_capacity"],
                            f"service_requirements.{key}.required_capacity",
                            minimum=0.0,
                        )
                    else:
                        per = self._finite_number(
                            raw.get("people_per_unit", 1),
                            f"service_requirements.{key}.people_per_unit",
                            minimum=1e-9,
                        )
                        required = math.ceil(population / per) if population > 0 else 0.0
                else:
                    per = self._finite_number(
                        raw,
                        f"service_requirements.{key}.people_per_unit",
                        minimum=1e-9,
                    )
                    required = math.ceil(population / per) if population > 0 else 0.0
                requirements[str(key)[:100]] = required
            return requirements
        if state.get("service_model", "basic") == "off":
            return {}
        for kind, people_per_unit in DEFAULT_SERVICE_PEOPLE_PER_UNIT.items():
            requirements[kind] = (
                float(math.ceil(population / people_per_unit)) if population > 0 else 0.0
            )
        return requirements

    def _available_services_db(
        self, db: sqlite3.Connection, campaign_id: str, location_id: str
    ) -> dict[str, float]:
        rows = db.execute(
            "SELECT kind,state_json FROM town_services WHERE campaign_id=? AND location_id=? ORDER BY id",
            (campaign_id, location_id),
        ).fetchall()
        available: dict[str, float] = {}
        aliases = {
            "market": "food_market",
            "general_store": "food_market",
            "grocer": "food_market",
            "watch": "guard",
            "militia": "guard",
            "clinic": "healer",
            "temple_healer": "healer",
            "inn": "lodging",
            "tavern": "lodging",
            "bathhouse": "sanitation",
            "sewer": "sanitation",
        }
        for row in rows:
            raw_kind = str(row["kind"]).strip().lower()
            kind = aliases.get(raw_kind, raw_kind)
            state = self.e._loads(row["state_json"] or "{}")
            capacity = self._finite_number(
                state.get("capacity", 1.0),
                f"town_service.{raw_kind}.capacity",
                minimum=0.0,
            )
            available[kind] = available.get(kind, 0.0) + capacity
        return available

    def _service_gap_ratio_db(
        self, db: sqlite3.Connection, campaign_id: str, location_id: str
    ) -> float:
        rows = db.execute(
            "SELECT required_capacity,gap FROM settlement_service_needs WHERE campaign_id=? AND location_id=?",
            (campaign_id, location_id),
        ).fetchall()
        total_required = sum(float(row["required_capacity"]) for row in rows)
        total_gap = sum(float(row["gap"]) for row in rows)
        return 0.0 if total_required <= 0 else self._clamp(total_gap / total_required)

    def _update_services_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        location_id: str,
        population: float,
        when: datetime,
        config: sqlite3.Row,
        emit: Callable[..., None] | None,
    ) -> int:
        if not bool(config["service_gaps_enabled"]):
            return db.execute(
                "DELETE FROM settlement_service_needs WHERE campaign_id=? AND location_id=?",
                (campaign_id, location_id),
            ).rowcount
        profile = self._profile_db(db, campaign_id, location_id)
        profile_state = self.e._loads(profile["state_json"] or "{}") if profile else {}
        if str(profile_state.get("service_model", "basic")).lower() == "off":
            return db.execute(
                "DELETE FROM settlement_service_needs WHERE campaign_id=? AND location_id=?",
                (campaign_id, location_id),
            ).rowcount
        required = self._service_requirements_db(
            db, campaign_id, location_id, population
        )
        available = self._available_services_db(db, campaign_id, location_id)
        active_kinds = sorted(set(required) | set(available))
        if active_kinds:
            placeholders = ",".join("?" for _ in active_kinds)
            changed = db.execute(
                f"DELETE FROM settlement_service_needs WHERE campaign_id=? AND location_id=? AND service_kind NOT IN ({placeholders})",
                (campaign_id, location_id, *active_kinds),
            ).rowcount
        else:
            changed = db.execute(
                "DELETE FROM settlement_service_needs WHERE campaign_id=? AND location_id=?",
                (campaign_id, location_id),
            ).rowcount
        for kind in active_kinds:
            need = max(0.0, float(required.get(kind, 0.0)))
            have = max(0.0, float(available.get(kind, 0.0)))
            gap = max(0.0, need - have)
            old = db.execute(
                "SELECT gap,last_event_world_time FROM settlement_service_needs WHERE campaign_id=? AND location_id=? AND service_kind=?",
                (campaign_id, location_id, kind),
            ).fetchone()
            last_event = old["last_event_world_time"] if old else None
            db.execute(
                """INSERT INTO settlement_service_needs(
                       campaign_id,location_id,service_kind,required_capacity,available_capacity,gap,
                       last_event_world_time,state_json,updated_world_time,updated_at)
                   VALUES(?,?,?,?,?,?,?,'{}',?,?)
                   ON CONFLICT(campaign_id,location_id,service_kind) DO UPDATE SET
                       required_capacity=excluded.required_capacity,
                       available_capacity=excluded.available_capacity,gap=excluded.gap,
                       updated_world_time=excluded.updated_world_time,updated_at=excluded.updated_at""",
                (
                    campaign_id,
                    location_id,
                    kind,
                    need,
                    have,
                    gap,
                    last_event,
                    when.isoformat(),
                    self.e._now(),
                ),
            )
            if not old or abs(float(old["gap"]) - gap) > 1e-9:
                changed += 1
            if gap > 0 and emit and bool(profile_state.get("emit_service_events", False)):
                due = True
                if last_event:
                    last = self._utc(datetime.fromisoformat(str(last_event)))
                    due = (when - last).total_seconds() >= int(
                        config["service_event_cooldown_days"]
                    ) * 86400
                if due:
                    emit(
                        "settlement_service_gap",
                        f"Settlement service capacity is insufficient: {kind}",
                        {
                            "location_id": location_id,
                            "service_kind": kind,
                            "required_capacity": need,
                            "available_capacity": have,
                            "gap": gap,
                        },
                        location_id,
                        when,
                    )
                    db.execute(
                        "UPDATE settlement_service_needs SET last_event_world_time=? WHERE campaign_id=? AND location_id=? AND service_kind=?",
                        (when.isoformat(), campaign_id, location_id, kind),
                    )
        return changed

    def _labor_demands_db(
        self, db: sqlite3.Connection, campaign_id: str, location_id: str
    ) -> dict[str, float]:
        demand: dict[str, float] = {}
        if not self._table_exists_db(db, "economy_producers"):
            return demand
        for table in ("economy_extractors", "economy_producers"):
            rows = db.execute(
                f"SELECT state_json FROM {table} WHERE campaign_id=? AND location_id=? AND active=1 ORDER BY id",
                (campaign_id, location_id),
            ).fetchall()
            for row in rows:
                state = self.e._loads(row["state_json"] or "{}")
                workers = self._finite_number(
                    state.get("workers_required", 0.0),
                    f"{table}.workers_required",
                    minimum=0.0,
                )
                if workers <= 0:
                    continue
                occupation = str(state.get("occupation") or "general")[:100]
                demand[occupation] = demand.get(occupation, 0.0) + workers
        return demand

    def _labor_supplies_db(
        self, db: sqlite3.Connection, campaign_id: str, location_id: str
    ) -> tuple[dict[str, float], float]:
        dedicated: dict[str, float] = {}
        general = 0.0
        rows = db.execute(
            "SELECT age_band,livelihood,count,labor_participation FROM population_cohorts WHERE campaign_id=? AND location_id=? AND count>0 ORDER BY id",
            (campaign_id, location_id),
        ).fetchall()
        for row in rows:
            age_band = str(row["age_band"])
            if age_band == "child":
                continue
            age_factor = 0.25 if age_band == "elder" else 1.0
            supply = max(0.0, float(row["count"])) * self._clamp(
                float(row["labor_participation"])
            ) * age_factor
            livelihood = str(row["livelihood"] or "mixed")
            if livelihood in {"mixed", "general", "unassigned", "none"}:
                general += supply
            else:
                dedicated[livelihood] = dedicated.get(livelihood, 0.0) + supply
        return dedicated, general

    def refresh_labor_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        location_id: str,
        when: datetime,
    ) -> dict[str, float]:
        demand = self._labor_demands_db(db, campaign_id, location_id)
        dedicated, general_remaining = self._labor_supplies_db(
            db, campaign_id, location_id
        )
        rows: dict[str, float] = {}
        total_supply = general_remaining + sum(dedicated.values())
        total_filled = 0.0
        for occupation in sorted(demand):
            need = max(0.0, demand[occupation])
            specific = max(0.0, dedicated.get(occupation, 0.0))
            use_specific = min(need, specific)
            remaining_need = max(0.0, need - use_specific)
            use_general = min(remaining_need, general_remaining)
            general_remaining -= use_general
            filled = use_specific + use_general
            total_filled += filled
            productivity = 1.0 if need <= 0 else self._clamp(filled / need)
            available_supply = specific + use_general
            wage_index = self._clamp(
                need / max(1e-9, specific + general_remaining + use_general), 0.5, 3.0
            )
            db.execute(
                """INSERT INTO settlement_labor(
                       campaign_id,location_id,occupation,demand,supply,filled,productivity,wage_index,
                       state_json,updated_world_time,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?, ?,?)
                   ON CONFLICT(campaign_id,location_id,occupation) DO UPDATE SET
                       demand=excluded.demand,supply=excluded.supply,filled=excluded.filled,
                       productivity=excluded.productivity,wage_index=excluded.wage_index,
                       state_json=excluded.state_json,updated_world_time=excluded.updated_world_time,
                       updated_at=excluded.updated_at""",
                (
                    campaign_id,
                    location_id,
                    occupation,
                    need,
                    available_supply,
                    filled,
                    productivity,
                    wage_index,
                    self.e._dumps({"general_labor_used": use_general}),
                    when.isoformat(),
                    self.e._now(),
                ),
            )
            rows[occupation] = productivity
        population_state = self._ensure_population_state_db(
            db, campaign_id, location_id
        )
        population_meta = self.e._loads(population_state["state_json"] or "{}")
        profile = self._ensure_profile_db(db, campaign_id, location_id)
        profile_state = self.e._loads(profile["state_json"] or "{}")
        derives_employment = profile_state.get("derive_employment_from_labor") is True
        if demand:
            modeled_coverage = self._clamp(total_filled / max(1e-9, total_supply))
            population_meta.update(
                {
                    "modeled_labor_coverage": round(modeled_coverage, 6),
                    "modeled_labor_demand": round(sum(demand.values()), 6),
                    "modeled_labor_filled": round(total_filled, 6),
                }
            )
        else:
            modeled_coverage = 0.0
            for key in (
                "modeled_labor_coverage",
                "modeled_labor_demand",
                "modeled_labor_filled",
            ):
                population_meta.pop(key, None)
        if derives_employment:
            db.execute(
                """UPDATE population_state
                   SET employment=?,state_json=?,updated_at=?
                   WHERE campaign_id=? AND location_id=?""",
                (
                    modeled_coverage,
                    self.e._dumps(population_meta),
                    self.e._now(),
                    campaign_id,
                    location_id,
                ),
            )
        else:
            db.execute(
                """UPDATE population_state
                   SET state_json=?,updated_at=?
                   WHERE campaign_id=? AND location_id=?""",
                (
                    self.e._dumps(population_meta),
                    self.e._now(),
                    campaign_id,
                    location_id,
                ),
            )
        # Remove obsolete occupation rows so stale labour shortages do not affect production.
        if demand:
            placeholders = ",".join("?" for _ in demand)
            db.execute(
                f"DELETE FROM settlement_labor WHERE campaign_id=? AND location_id=? AND occupation NOT IN ({placeholders})",
                (campaign_id, location_id, *sorted(demand)),
            )
        else:
            db.execute(
                "DELETE FROM settlement_labor WHERE campaign_id=? AND location_id=?",
                (campaign_id, location_id),
            )
        return rows

    def labor_factor_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        location_id: str,
        occupation: str = "general",
        workers_required: float = 0,
    ) -> float:
        workers = self._finite_number(
            workers_required, "workers_required", minimum=0.0
        )
        if workers <= 0 or not self._table_exists_db(db, "settlement_labor"):
            return 1.0
        row = db.execute(
            "SELECT productivity FROM settlement_labor WHERE campaign_id=? AND location_id=? AND occupation=?",
            (campaign_id, location_id, occupation),
        ).fetchone()
        if row is None and occupation != "general":
            row = db.execute(
                "SELECT productivity FROM settlement_labor WHERE campaign_id=? AND location_id=? AND occupation='general'",
                (campaign_id, location_id),
            ).fetchone()
        # workers_required is an explicit opt-in. No matching labour record means
        # no demonstrated workforce, not unconstrained production.
        return self._clamp(float(row["productivity"])) if row else 0.0

    def _generated_household_id(self, location_id: str) -> str:
        digest = hashlib.sha256(location_id.encode("utf-8")).hexdigest()[:16]
        return f"aggregate:{digest}"

    def reconcile_households_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        location_id: str,
        when: datetime,
        config: sqlite3.Row,
    ) -> int:
        """Maintain one bounded derived household aggregate when none is authored.

        Authored household groups always take precedence. The derived row exists to
        expose housing/displacement and age-composition pressure without inventing
        named families or per-resident identities.
        """
        generated_id = self._generated_household_id(location_id)
        rows = db.execute(
            "SELECT * FROM population_households WHERE campaign_id=? AND location_id=? ORDER BY id",
            (campaign_id, location_id),
        ).fetchall()
        authored = []
        for row in rows:
            state = self.e._loads(row["state_json"] or "{}")
            if not bool(state.get("generated_aggregate")):
                authored.append(row)
        if not bool(config["households_enabled"]):
            cur = db.execute(
                "DELETE FROM population_households WHERE campaign_id=? AND id=?",
                (campaign_id, generated_id),
            )
            return int(cur.rowcount > 0)
        if authored:
            cur = db.execute(
                "DELETE FROM population_households WHERE campaign_id=? AND id=?",
                (campaign_id, generated_id),
            )
            return int(cur.rowcount > 0)

        profile = self._profile_view_db(
            db, campaign_id, location_id,
            float(self._population_state_view_db(db, campaign_id, location_id)["population"]),
        )
        profile_state = self.e._loads(profile["state_json"] or "{}")
        if str(profile_state.get("household_model", "derived")).lower() == "off":
            cur = db.execute(
                "DELETE FROM population_households WHERE campaign_id=? AND id=?",
                (campaign_id, generated_id),
            )
            return int(cur.rowcount > 0)
        try:
            average_size = self._finite_number(
                profile_state.get("average_household_size", 3.5),
                "average_household_size",
                minimum=1.0,
            )
        except (TypeError, ValueError):
            average_size = 3.5
        cohort_rows = db.execute(
            "SELECT age_band,COALESCE(SUM(count),0) n FROM population_cohorts WHERE campaign_id=? AND location_id=? GROUP BY age_band",
            (campaign_id, location_id),
        ).fetchall()
        ages = {str(row["age_band"]): max(0.0, float(row["n"])) for row in cohort_rows}
        persons = sum(ages.values())
        children = ages.get("child", 0.0)
        elders = ages.get("elder", 0.0)
        adults = max(0.0, persons - children - elders)
        household_count = persons / average_size if persons > 0 else 0.0
        metrics = self._settlement_metrics_db(db, campaign_id, location_id)
        housing_ratio = min(1.0, max(0.0, float(metrics["housing_ratio"])))
        housing_units = household_count * housing_ratio
        status = "displaced" if persons > 0 and housing_ratio < 0.999999 else "active"
        state = {
            "generated_aggregate": True,
            "average_household_size": average_size,
            "unclassified_mixed_assigned_to_adults": ages.get("mixed", 0.0),
            "housing_ratio": housing_ratio,
        }
        old = db.execute(
            "SELECT household_count,persons,adults,children,elders,housing_units,status,state_json FROM population_households WHERE campaign_id=? AND id=?",
            (campaign_id, generated_id),
        ).fetchone()
        new_tuple = (household_count, persons, adults, children, elders, housing_units, status)
        changed = old is None or any(
            abs(float(old[key]) - float(value)) > 1e-9
            for key, value in zip(
                ("household_count", "persons", "adults", "children", "elders", "housing_units"),
                new_tuple[:6],
            )
        ) or str(old["status"]) != status or self.e._loads(old["state_json"] or "{}") != state
        db.execute(
            """INSERT INTO population_households(
                   campaign_id,id,location_id,cohort_id,household_count,persons,adults,children,elders,
                   housing_units,wealth,food_reserve_days,livelihood,status,state_json,updated_at)
               VALUES(?,?,?,NULL,?,?,?,?,?,?,0.5,0,'mixed',?,?,?)
               ON CONFLICT(campaign_id,id) DO UPDATE SET
                   location_id=excluded.location_id,cohort_id=NULL,household_count=excluded.household_count,
                   persons=excluded.persons,adults=excluded.adults,children=excluded.children,elders=excluded.elders,
                   housing_units=excluded.housing_units,status=excluded.status,state_json=excluded.state_json,
                   updated_at=excluded.updated_at""",
            (
                campaign_id, generated_id, location_id, household_count, persons, adults,
                children, elders, housing_units, status, self.e._dumps(state), self.e._now(),
            ),
        )
        return int(changed)

    # ------------------------------------------------------------------
    # Daily deterministic simulation
    # ------------------------------------------------------------------

    def _rand_keyed_db(
        self, db: sqlite3.Connection, campaign_id: str, namespace: str
    ) -> float:
        row = db.execute(
            "SELECT seed FROM sim_config WHERE campaign_id=?", (campaign_id,)
        ).fetchone()
        seed = int(row["seed"]) if row else int(
            hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:16], 16
        )
        digest = hashlib.sha256(
            f"population|{seed}|{campaign_id}|{namespace}".encode()
        ).digest()
        return int.from_bytes(digest[:8], "big") / float(2**64)

    def _stochastic_count_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        expected: float,
        namespace: str,
        maximum: float | None = None,
    ) -> float:
        expected = max(0.0, float(expected))
        whole = math.floor(expected + 1e-12)
        frac = max(0.0, min(1.0, expected - whole))
        value = float(whole + (1 if frac and self._rand_keyed_db(db, campaign_id, namespace) < frac else 0))
        if maximum is not None:
            value = min(value, max(0.0, float(maximum)))
        return value

    def _record_flow_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        revision: int,
        *,
        flow_key: str,
        kind: str,
        count: float,
        reason: str,
        when: datetime,
        origin: str | None = None,
        destination: str | None = None,
        cohort_id: str | None = None,
        destination_cohort_id: str | None = None,
        state: dict[str, Any] | None = None,
    ) -> bool:
        cur = db.execute(
            """INSERT OR IGNORE INTO population_flows(
                   campaign_id,flow_key,kind,origin_location_id,destination_location_id,
                   cohort_id,destination_cohort_id,count,reason,world_time,revision,state_json,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                campaign_id,
                flow_key,
                kind,
                origin,
                destination,
                cohort_id,
                destination_cohort_id,
                max(0.0, float(count)),
                reason[:500],
                when.isoformat(),
                revision,
                self.e._dumps(state or {}),
                self.e._now(),
            ),
        )
        return cur.rowcount > 0

    def _child_cohort_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        parent: sqlite3.Row,
        when: datetime,
    ) -> str:
        state = self.e._loads(parent["state_json"] or "{}")
        configured = state.get("child_cohort_id")
        if configured:
            cid = self.e._clean_id(str(configured))
            row = db.execute(
                "SELECT 1 FROM population_cohorts WHERE campaign_id=? AND id=?",
                (campaign_id, cid),
            ).fetchone()
            if row:
                return cid
        cid = self._auto_cohort_id(
            (
                parent["location_id"],
                parent["species"],
                parent["culture"],
                parent["faction_id"],
                "child",
            ),
            "child",
        )
        row = db.execute(
            "SELECT 1 FROM population_cohorts WHERE campaign_id=? AND id=?",
            (campaign_id, cid),
        ).fetchone()
        if not row:
            config = self._config_db(db, campaign_id)
            configured_child_death = state.get("child_death_rate_annual")
            child_death_rate = self._finite_number(
                config["default_death_rate_annual"]
                if configured_child_death is None
                else configured_child_death,
                "child_death_rate_annual",
                minimum=0.0,
            )
            self._upsert_cohort_db(
                db,
                campaign_id,
                cid,
                str(parent["location_id"]),
                species=str(parent["species"]),
                culture=str(parent["culture"]),
                faction_id=parent["faction_id"],
                age_band="child",
                livelihood="dependent",
                count=0,
                birth_rate_annual=0,
                death_rate_annual=child_death_rate,
                labor_participation=0,
                migration_affinity=float(parent["migration_affinity"]),
                health=float(parent["health"]),
                wealth=float(parent["wealth"]),
                state={"generated_from_births": True},
                preserve_cursor=False,
            )
        return cid

    def _process_vitals_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        revision: int,
        location_id: str,
        metrics: dict[str, Any],
        when: datetime,
        config: sqlite3.Row,
        emit: Callable[..., None] | None,
    ) -> dict[str, float]:
        births_total = 0.0
        deaths_total = 0.0
        transitions_total = 0.0
        rows = db.execute(
            "SELECT * FROM population_cohorts WHERE campaign_id=? AND location_id=? AND count>0 ORDER BY id",
            (campaign_id, location_id),
        ).fetchall()
        changes: dict[str, float] = {str(row["id"]): 0.0 for row in rows}
        births: list[tuple[str, float, sqlite3.Row]] = []
        transitions: list[tuple[str, str, float]] = []
        date_key = when.date().isoformat()

        for row in rows:
            cohort_id = str(row["id"])
            last_processed = self._utc(datetime.fromisoformat(str(row["last_processed_world_time"])))
            if last_processed >= when:
                continue
            count = max(0.0, float(row["count"]))
            health = self._clamp(float(row["health"]))
            age_multiplier = {"child": 1.15, "adult": 1.0, "elder": 3.5, "mixed": 1.0}[str(row["age_band"])]
            deprivation = max(
                0.0,
                1.0 - min(1.0, float(metrics["food_ratio"])),
                1.0 - min(1.0, float(metrics["water_ratio"])),
            )
            mortality_multiplier = (
                age_multiplier
                * (1.0 + 2.5 * (1.0 - health))
                * (1.0 + 2.0 * float(metrics["hazard"]))
                * (1.0 + 1.5 * deprivation)
                * (1.0 + 0.8 * (1.0 - float(metrics["safety"])))
                * (1.15 - 0.3 * float(metrics["healthcare"]))
                * (1.10 - 0.2 * float(metrics["sanitation"]))
            )
            expected_deaths = (
                count
                * max(0.0, float(row["death_rate_annual"]))
                / 365.0
                * mortality_multiplier
            )
            deaths = (
                self._stochastic_count_db(
                    db,
                    campaign_id,
                    expected_deaths,
                    f"death:{cohort_id}:{date_key}",
                    maximum=count,
                )
                if bool(config["mortality_enabled"])
                else 0.0
            )
            if deaths > 0:
                changes[cohort_id] -= deaths
                deaths_total += deaths
                flow_key = f"death:{cohort_id}:{date_key}"
                self._record_flow_db(
                    db,
                    campaign_id,
                    revision,
                    flow_key=flow_key,
                    kind="death",
                    count=deaths,
                    reason="aggregate demographic mortality",
                    when=when,
                    origin=location_id,
                    cohort_id=cohort_id,
                    state={"expected": expected_deaths, "hazard": metrics["hazard"]},
                )
                if emit:
                    emit(
                        "population_deaths",
                        f"{deaths:g} aggregate death(s) occurred",
                        {
                            "location_id": location_id,
                            "cohort_id": cohort_id,
                            "count": deaths,
                            "hazard": metrics["hazard"],
                        },
                        location_id,
                        when,
                    )

            remaining = max(0.0, count - deaths)
            if bool(config["births_enabled"]) and str(row["age_band"]) in {"adult", "mixed"}:
                fertility_modifier = (
                    min(1.1, max(0.0, float(metrics["food_ratio"])))
                    * min(1.1, max(0.0, float(metrics["housing_ratio"])))
                    * min(1.1, max(0.0, float(metrics["water_ratio"])))
                    * (0.4 + 0.6 * health)
                    * (0.65 + 0.35 * float(metrics["safety"]))
                    * (1.0 - 0.6 * float(metrics["hazard"]))
                )
                expected_births = (
                    remaining
                    * max(0.0, float(row["birth_rate_annual"]))
                    / 365.0
                    * max(0.0, fertility_modifier)
                )
                birth_count = self._stochastic_count_db(
                    db,
                    campaign_id,
                    expected_births,
                    f"birth:{cohort_id}:{date_key}",
                )
                if birth_count > 0:
                    child_id = self._child_cohort_db(db, campaign_id, row, when)
                    births.append((child_id, birth_count, row))
                    births_total += birth_count
                    self._record_flow_db(
                        db,
                        campaign_id,
                        revision,
                        flow_key=f"birth:{cohort_id}:{date_key}",
                        kind="birth",
                        count=birth_count,
                        reason="aggregate demographic births",
                        when=when,
                        origin=location_id,
                        cohort_id=cohort_id,
                        destination_cohort_id=child_id,
                        state={"expected": expected_births},
                    )
                    if emit:
                        emit(
                            "population_births",
                            f"{birth_count:g} aggregate birth(s) occurred",
                            {
                                "location_id": location_id,
                                "parent_cohort_id": cohort_id,
                                "child_cohort_id": child_id,
                                "count": birth_count,
                            },
                            location_id,
                            when,
                        )

            transition_rate = max(0.0, float(row["transition_rate_annual"]))
            next_id = row["next_cohort_id"]
            if transition_rate > 0 and next_id:
                next_row = db.execute(
                    "SELECT 1 FROM population_cohorts WHERE campaign_id=? AND id=? AND location_id=?",
                    (campaign_id, next_id, location_id),
                ).fetchone()
                if next_row:
                    expected_transition = remaining * transition_rate / 365.0
                    move = self._stochastic_count_db(
                        db,
                        campaign_id,
                        expected_transition,
                        f"age:{cohort_id}:{date_key}",
                        maximum=remaining,
                    )
                    if move > 0:
                        changes[cohort_id] -= move
                        transitions.append((cohort_id, str(next_id), move))
                        transitions_total += move

        for cohort_id, delta in changes.items():
            if abs(delta) > 1e-12:
                db.execute(
                    "UPDATE population_cohorts SET count=MAX(0,count+?),last_processed_world_time=?,updated_at=? WHERE campaign_id=? AND id=?",
                    (delta, when.isoformat(), self.e._now(), campaign_id, cohort_id),
                )
            else:
                db.execute(
                    "UPDATE population_cohorts SET last_processed_world_time=?,updated_at=? WHERE campaign_id=? AND id=?",
                    (when.isoformat(), self.e._now(), campaign_id, cohort_id),
                )
        for child_id, count, _parent in births:
            db.execute(
                "UPDATE population_cohorts SET count=count+?,last_processed_world_time=?,updated_at=? WHERE campaign_id=? AND id=?",
                (count, when.isoformat(), self.e._now(), campaign_id, child_id),
            )
        for source_id, dest_id, count in transitions:
            db.execute(
                "UPDATE population_cohorts SET count=count+?,last_processed_world_time=?,updated_at=? WHERE campaign_id=? AND id=?",
                (count, when.isoformat(), self.e._now(), campaign_id, dest_id),
            )
            self._record_flow_db(
                db,
                campaign_id,
                revision,
                flow_key=f"age:{source_id}:{date_key}",
                kind="age_transition",
                count=count,
                reason="configured age-cohort transition",
                when=when,
                origin=location_id,
                cohort_id=source_id,
                destination_cohort_id=dest_id,
            )
        return {
            "births": births_total,
            "deaths": deaths_total,
            "transitions": transitions_total,
        }

    def _migration_identity(
        self, row: sqlite3.Row, destination: str
    ) -> tuple[Any, ...]:
        # Preserve source-cohort lineage. Two source cohorts with the same visible
        # demographic labels may still have different mortality/transition rules
        # and therefore must not be silently merged at the destination.
        return (destination, row["id"])

    def _destination_cohort_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        source: sqlite3.Row,
        destination: str,
        *,
        when: datetime | None = None,
        _memo: dict[tuple[str, str], str] | None = None,
    ) -> str:
        memo = _memo if _memo is not None else {}
        source_id = str(source["id"])
        key = (destination, source_id)
        if key in memo:
            return memo[key]

        cid = self._auto_cohort_id(
            self._migration_identity(source, destination), "migrant"
        )
        existing = db.execute(
            "SELECT id FROM population_cohorts WHERE campaign_id=? AND id=?",
            (campaign_id, cid),
        ).fetchone()
        if not existing:
            self._upsert_cohort_db(
                db,
                campaign_id,
                cid,
                destination,
                species=str(source["species"]),
                culture=str(source["culture"]),
                faction_id=source["faction_id"],
                age_band=str(source["age_band"]),
                livelihood=str(source["livelihood"]),
                count=0,
                birth_rate_annual=float(source["birth_rate_annual"]),
                death_rate_annual=float(source["death_rate_annual"]),
                labor_participation=float(source["labor_participation"]),
                migration_affinity=float(source["migration_affinity"]),
                health=float(source["health"]),
                wealth=float(source["wealth"]),
                next_cohort_id=None,
                transition_rate_annual=float(source["transition_rate_annual"]),
                state={
                    "generated_by_migration": True,
                    "source_cohort_id": source_id,
                },
                preserve_cursor=False,
            )
            # `_upsert_cohort_db(..., preserve_cursor=False)` normally uses the
            # campaign's currently committed clock. During a large catch-up
            # call that committed clock can still be the interval start, while
            # the same cohort created through smaller calls sees a later clock.
            # Bind generated migration-chain placeholders to the actual
            # simulation/event boundary instead so persisted state is chunk
            # invariant, including zero-count cohorts created only to preserve
            # a future age-transition chain.
            if when is not None:
                db.execute(
                    "UPDATE population_cohorts SET last_processed_world_time=?,updated_at=? WHERE campaign_id=? AND id=?",
                    (
                        self._utc(when).isoformat(),
                        self.e._now(),
                        campaign_id,
                        cid,
                    ),
                )
        memo[key] = cid

        # Reconstruct the source aging chain at the destination. Creating the
        # current row before recursion makes even accidental cyclic authoring
        # bounded and deterministic.
        mapped_next: str | None = None
        next_source_id = source["next_cohort_id"]
        if next_source_id:
            next_source = db.execute(
                "SELECT * FROM population_cohorts WHERE campaign_id=? AND id=?",
                (campaign_id, str(next_source_id)),
            ).fetchone()
            if next_source is not None:
                mapped_next = self._destination_cohort_db(
                    db,
                    campaign_id,
                    next_source,
                    destination,
                    when=when,
                    _memo=memo,
                )
        db.execute(
            """UPDATE population_cohorts
               SET next_cohort_id=?,updated_at=?
               WHERE campaign_id=? AND id=?""",
            (mapped_next, self.e._now(), campaign_id, cid),
        )
        return cid

    @staticmethod
    def _capacity_spare(metrics: dict[str, Any]) -> float:
        population = float(metrics["population"])
        limits: list[float] = []
        if metrics["housing_capacity_known"]:
            limits.append(max(0.0, float(metrics["housing_capacity"]) - population))
        if metrics["food_capacity_known"]:
            limits.append(max(0.0, float(metrics["food_capacity"]) - population))
        if metrics["water_capacity_known"]:
            limits.append(max(0.0, float(metrics["water_capacity"]) - population))
        return min(limits) if limits else float("inf")

    def _plan_migration_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        metrics: dict[str, dict[str, Any]],
        when: datetime,
        config: sqlite3.Row,
    ) -> list[dict[str, Any]]:
        if not bool(config["migration_enabled"]):
            return []
        plans: list[dict[str, Any]] = []
        reserved: dict[str, float] = {}
        links = db.execute(
            "SELECT from_id,to_id,travel_hours FROM location_links WHERE campaign_id=? ORDER BY from_id,travel_hours,to_id",
            (campaign_id,),
        ).fetchall()
        adjacency: dict[str, list[tuple[str, float]]] = {}
        for link in links:
            adjacency.setdefault(str(link["from_id"]), []).append(
                (str(link["to_id"]), float(link["travel_hours"]))
            )
        date_key = when.date().isoformat()
        for origin in sorted(metrics):
            origin_metrics = metrics[origin]
            pressure = self._clamp(float(origin_metrics["migration_pressure"]))
            if pressure <= 0:
                continue
            candidates = []
            for destination, travel_hours in adjacency.get(origin, []):
                dest_metrics = metrics.get(destination)
                if not dest_metrics:
                    continue
                travel_penalty = min(0.25, max(0.0, travel_hours) / (24.0 * 30.0) * 0.1)
                delta = float(dest_metrics["pull_score"]) - float(origin_metrics["pull_score"]) - travel_penalty
                if delta >= float(config["minimum_pull_delta"]):
                    candidates.append((delta, -travel_hours, destination))
            if not candidates:
                continue
            candidates.sort(reverse=True)
            pull_delta, _neg_hours, destination = candidates[0]
            spare = self._capacity_spare(metrics[destination]) - reserved.get(destination, 0.0)
            if spare <= 0:
                continue
            rows = db.execute(
                "SELECT * FROM population_cohorts WHERE campaign_id=? AND location_id=? AND count>0 ORDER BY id",
                (campaign_id, origin),
            ).fetchall()
            for row in rows:
                count = max(0.0, float(row["count"]))
                if count <= 0:
                    continue
                affinity = self._clamp(float(row["migration_affinity"]), 0.0, 2.0)
                desired = (
                    count
                    * float(config["max_migration_fraction_per_day"])
                    * pressure
                    * affinity
                    * min(1.0, max(0.0, pull_delta) * 2.0)
                )
                move = self._stochastic_count_db(
                    db,
                    campaign_id,
                    desired,
                    f"migration:{row['id']}:{destination}:{date_key}",
                    maximum=min(count, spare),
                )
                if move <= 0:
                    continue
                plans.append(
                    {
                        "source": row,
                        "origin": origin,
                        "destination": destination,
                        "count": move,
                        "pull_delta": pull_delta,
                        "pressure": pressure,
                    }
                )
                reserved[destination] = reserved.get(destination, 0.0) + move
                spare -= move
                if spare <= 0:
                    break
        return plans

    def _apply_migration_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        revision: int,
        plans: list[dict[str, Any]],
        when: datetime,
        emit: Callable[..., None] | None,
    ) -> float:
        total = 0.0
        date_key = when.date().isoformat()
        touched: set[str] = set()
        for plan in plans:
            source = plan["source"]
            current = db.execute(
                "SELECT count FROM population_cohorts WHERE campaign_id=? AND id=?",
                (campaign_id, source["id"]),
            ).fetchone()
            available = max(0.0, float(current["count"])) if current else 0.0
            move = min(available, max(0.0, float(plan["count"])))
            if move <= 0:
                continue
            destination_id = self._destination_cohort_db(
                db,
                campaign_id,
                source,
                str(plan["destination"]),
                when=when,
            )
            flow_key = f"migration:{source['id']}:{plan['destination']}:{date_key}"
            if not self._record_flow_db(
                db,
                campaign_id,
                revision,
                flow_key=flow_key,
                kind="migration",
                count=move,
                reason="aggregate migration toward a linked higher-pull settlement",
                when=when,
                origin=str(plan["origin"]),
                destination=str(plan["destination"]),
                cohort_id=str(source["id"]),
                destination_cohort_id=destination_id,
                state={
                    "pull_delta": plan["pull_delta"],
                    "origin_pressure": plan["pressure"],
                },
            ):
                continue
            db.execute(
                "UPDATE population_cohorts SET count=MAX(0,count-?),last_processed_world_time=?,updated_at=? WHERE campaign_id=? AND id=?",
                (move, when.isoformat(), self.e._now(), campaign_id, source["id"]),
            )
            db.execute(
                "UPDATE population_cohorts SET count=count+?,last_processed_world_time=?,updated_at=? WHERE campaign_id=? AND id=?",
                (move, when.isoformat(), self.e._now(), campaign_id, destination_id),
            )
            total += move
            touched.add(str(plan["origin"]))
            touched.add(str(plan["destination"]))
            if emit:
                emit(
                    "population_migration",
                    f"{move:g} people migrated from {plan['origin']} to {plan['destination']}",
                    {
                        "origin": plan["origin"],
                        "destination": plan["destination"],
                        "source_cohort_id": source["id"],
                        "destination_cohort_id": destination_id,
                        "count": move,
                    },
                    str(plan["origin"]),
                    when,
                )
        for location_id in sorted(touched):
            self._sync_population_summary_db(db, campaign_id, location_id)
        return total

    def _rank_for_population(
        self, population: float, state: dict[str, Any]
    ) -> str:
        raw = state.get("rank_thresholds")
        thresholds: list[tuple[float, str]] = []
        if isinstance(raw, dict):
            for rank, minimum in raw.items():
                thresholds.append(
                    (
                        self._finite_number(
                            minimum,
                            f"rank_thresholds.{rank}",
                            minimum=0.0,
                        ),
                        str(rank)[:80],
                    )
                )
            thresholds.sort(reverse=True)
        if not thresholds:
            thresholds = list(DEFAULT_RANK_THRESHOLDS)
        for minimum, rank in thresholds:
            if population >= minimum:
                return rank
        return "empty"

    def _update_rank_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        location_id: str,
        population: float,
        when: datetime,
        emit: Callable[..., None] | None,
    ) -> int:
        profile = self._profile_db(db, campaign_id, location_id)
        if not profile or not bool(profile["auto_rank"]):
            return 0
        state = self.e._loads(profile["state_json"] or "{}")
        new_rank = self._rank_for_population(population, state)
        if new_rank == profile["rank"]:
            return 0
        old_rank = str(profile["rank"])
        db.execute(
            "UPDATE settlement_profiles SET rank=?,updated_at=? WHERE campaign_id=? AND location_id=?",
            (new_rank, self.e._now(), campaign_id, location_id),
        )
        if emit:
            emit(
                "settlement_rank_changed",
                f"Settlement rank changed from {old_rank} to {new_rank}",
                {
                    "location_id": location_id,
                    "old_rank": old_rank,
                    "new_rank": new_rank,
                    "population": population,
                },
                location_id,
                when,
            )
        return 1

    def step_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        revision: int,
        when: datetime,
        *,
        emit: Callable[..., None] | None = None,
    ) -> dict[str, float]:
        when = self._utc(when)
        config = self._config_db(db, campaign_id)
        tally: dict[str, float] = {
            "births": 0.0,
            "deaths": 0.0,
            "transitions": 0.0,
            "migration": 0.0,
            "settlements": 0.0,
            "labor": 0.0,
            "service_updates": 0.0,
            "household_updates": 0.0,
            "rank_changes": 0.0,
            "bootstrapped": 0.0,
        }
        if not bool(config["enabled"]):
            return tally
        tally["bootstrapped"] = float(self.bootstrap_all_db(db, campaign_id))
        locations = [
            str(row["location_id"])
            for row in db.execute(
                """SELECT location_id FROM population_state WHERE campaign_id=?
                   UNION SELECT location_id FROM settlement_profiles WHERE campaign_id=?
                   UNION SELECT location_id FROM population_cohorts WHERE campaign_id=?
                   ORDER BY location_id""",
                (campaign_id, campaign_id, campaign_id),
            ).fetchall()
        ]
        if not locations:
            return tally

        metrics: dict[str, dict[str, Any]] = {}
        for location_id in locations:
            self._sync_population_summary_db(db, campaign_id, location_id)
            self.refresh_labor_db(db, campaign_id, location_id, when)
            labor_rows = db.execute(
                "SELECT COUNT(*) n FROM settlement_labor WHERE campaign_id=? AND location_id=?",
                (campaign_id, location_id),
            ).fetchone()
            tally["labor"] += float(labor_rows["n"])
            metrics[location_id] = self._settlement_metrics_db(
                db, campaign_id, location_id
            )
            vitals = self._process_vitals_db(
                db,
                campaign_id,
                revision,
                location_id,
                metrics[location_id],
                when,
                config,
                emit,
            )
            tally["births"] += vitals["births"]
            tally["deaths"] += vitals["deaths"]
            tally["transitions"] += vitals["transitions"]
            population = self._sync_population_summary_db(db, campaign_id, location_id)
            tally["service_updates"] += float(
                self._update_services_db(
                    db,
                    campaign_id,
                    location_id,
                    population,
                    when,
                    config,
                    emit,
                )
            )
            # Recompute after service/vitals changes and persist bounded pressure.
            metrics[location_id] = self._settlement_metrics_db(
                db, campaign_id, location_id
            )
            ps = self._population_state_db(db, campaign_id, location_id)
            pstate = self.e._loads(ps["state_json"] or "{}") if ps else {}
            pstate.update(
                {
                    "pull_score": round(metrics[location_id]["pull_score"], 6),
                    "hazard": round(metrics[location_id]["hazard"], 6),
                    "food_ratio": round(metrics[location_id]["food_ratio"], 6),
                    "housing_ratio": round(metrics[location_id]["housing_ratio"], 6),
                    "water_ratio": round(metrics[location_id]["water_ratio"], 6),
                }
            )
            db.execute(
                "UPDATE population_state SET safety=?,migration_pressure=?,state_json=?,updated_at=? WHERE campaign_id=? AND location_id=?",
                (
                    metrics[location_id]["safety"],
                    metrics[location_id]["migration_pressure"],
                    self.e._dumps(pstate),
                    self.e._now(),
                    campaign_id,
                    location_id,
                ),
            )
            tally["rank_changes"] += float(
                self._update_rank_db(
                    db,
                    campaign_id,
                    location_id,
                    population,
                    when,
                    emit,
                )
            )
            db.execute(
                "UPDATE settlement_profiles SET last_processed_world_time=?,updated_at=? WHERE campaign_id=? AND location_id=?",
                (when.isoformat(), self.e._now(), campaign_id, location_id),
            )
            tally["settlements"] += 1.0

        # Migration is planned against one frozen post-vitals snapshot, then applied,
        # so row order cannot make migrants move twice in the same day.
        if bool(config["migration_enabled"]):
            metrics = {
                location_id: self._settlement_metrics_db(
                    db, campaign_id, location_id
                )
                for location_id in locations
            }
            plans = self._plan_migration_db(
                db, campaign_id, metrics, when, config
            )
            tally["migration"] = self._apply_migration_db(
                db, campaign_id, revision, plans, when, emit
            )
        # Final post-migration aggregate refresh. Migration can found or shrink a
        # settlement after its first daily rank/service pass, so recompute every
        # derived projection from the committed post-migration cohort totals.
        for location_id in locations:
            population = self._sync_population_summary_db(
                db, campaign_id, location_id
            )
            self.refresh_labor_db(db, campaign_id, location_id, when)
            tally["service_updates"] += float(
                self._update_services_db(
                    db,
                    campaign_id,
                    location_id,
                    population,
                    when,
                    config,
                    emit,
                )
            )
            tally["household_updates"] += float(
                self.reconcile_households_db(
                    db, campaign_id, location_id, when, config
                )
            )
            final_metrics = self._settlement_metrics_db(
                db, campaign_id, location_id
            )
            ps = self._population_state_db(db, campaign_id, location_id)
            pstate = self.e._loads(ps["state_json"] or "{}") if ps else {}
            pstate.update(
                {
                    "pull_score": round(final_metrics["pull_score"], 6),
                    "hazard": round(final_metrics["hazard"], 6),
                    "food_ratio": round(final_metrics["food_ratio"], 6),
                    "housing_ratio": round(final_metrics["housing_ratio"], 6),
                    "water_ratio": round(final_metrics["water_ratio"], 6),
                }
            )
            db.execute(
                "UPDATE population_state SET safety=?,migration_pressure=?,state_json=?,updated_at=? WHERE campaign_id=? AND location_id=?",
                (
                    final_metrics["safety"],
                    final_metrics["migration_pressure"],
                    self.e._dumps(pstate),
                    self.e._now(),
                    campaign_id,
                    location_id,
                ),
            )
            tally["rank_changes"] += float(
                self._update_rank_db(
                    db,
                    campaign_id,
                    location_id,
                    population,
                    when,
                    emit,
                )
            )
        return tally

    # ------------------------------------------------------------------
    # Snapshots / public inspection / dispatch
    # ------------------------------------------------------------------

    def _snapshot_location_db(
        self, db: sqlite3.Connection, campaign_id: str, location_id: str
    ) -> dict[str, Any]:
        self._validate_location_db(db, campaign_id, location_id)
        ps = self._population_state_view_db(db, campaign_id, location_id)
        pstate = dict(ps)
        pstate["state"] = self.e._loads(pstate.pop("state_json") or "{}")
        profile = self._profile_dict(
            self._profile_view_db(db, campaign_id, location_id, float(pstate["population"]))
        )
        cohorts = [
            self._cohort_dict(row)
            for row in db.execute(
                "SELECT * FROM population_cohorts WHERE campaign_id=? AND location_id=? ORDER BY age_band,species,culture,livelihood,id",
                (campaign_id, location_id),
            ).fetchall()
        ]
        if not cohorts and float(pstate["population"]) > 0:
            cohorts = [{
                "campaign_id": campaign_id,
                "id": self._legacy_cohort_id(location_id),
                "location_id": location_id,
                "species": "unspecified",
                "culture": "unspecified",
                "faction_id": None,
                "age_band": "mixed",
                "livelihood": "mixed",
                "count": float(pstate["population"]),
                "birth_rate_annual": 0.0,
                "death_rate_annual": 0.0,
                "labor_participation": 0.55,
                "migration_affinity": 1.0,
                "health": 0.75,
                "wealth": 0.5,
                "next_cohort_id": None,
                "transition_rate_annual": 0.0,
                "last_processed_world_time": profile["last_processed_world_time"],
                "updated_at": None,
                "state": {"virtual_legacy_aggregate": True},
            }]
        households = [
            self._household_dict(row)
            for row in db.execute(
                "SELECT * FROM population_households WHERE campaign_id=? AND location_id=? ORDER BY status,id LIMIT 100",
                (campaign_id, location_id),
            ).fetchall()
        ]
        labor = []
        for row in db.execute(
            "SELECT * FROM settlement_labor WHERE campaign_id=? AND location_id=? ORDER BY occupation",
            (campaign_id, location_id),
        ).fetchall():
            data = dict(row)
            data["state"] = self.e._loads(data.pop("state_json") or "{}")
            labor.append(data)
        services = []
        for row in db.execute(
            "SELECT * FROM settlement_service_needs WHERE campaign_id=? AND location_id=? ORDER BY gap DESC,service_kind",
            (campaign_id, location_id),
        ).fetchall():
            data = dict(row)
            data["state"] = self.e._loads(data.pop("state_json") or "{}")
            services.append(data)
        metrics = self._settlement_metrics_db(db, campaign_id, location_id)
        household_summary = {
            "mode": "aggregate" if households else "unmodeled",
            "groups": len(households),
            "households": sum(float(row["household_count"]) for row in households),
            "persons": sum(float(row["persons"]) for row in households),
            "housing_units": sum(float(row["housing_units"]) for row in households),
        }
        return {
            "campaign_id": campaign_id,
            "location_id": location_id,
            "population_state": pstate,
            "settlement": profile,
            "metrics": metrics,
            "cohorts": cohorts,
            "households": households,
            "household_summary": household_summary,
            "labor": labor,
            "service_needs": services,
        }

    def snapshot_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        *,
        location_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        limit = self._finite_integer(limit, "limit", minimum=1, maximum=100)
        if location_id:
            return self._snapshot_location_db(db, campaign_id, location_id)
        rows = db.execute(
            """SELECT location_id FROM settlement_profiles WHERE campaign_id=?
               UNION SELECT location_id FROM population_state WHERE campaign_id=?
               ORDER BY location_id LIMIT ?""",
            (campaign_id, campaign_id, limit),
        ).fetchall()
        settlements = [
            self._snapshot_location_db(db, campaign_id, str(row["location_id"]))
            for row in rows
        ]
        return {
            "campaign_id": campaign_id,
            "location_id": None,
            "settlements": settlements,
            "settlement_count": len(settlements),
        }

    @staticmethod
    def _public_location_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        ps = snapshot["population_state"]
        settlement = snapshot["settlement"]
        metrics = snapshot["metrics"]
        return {
            "location_id": snapshot["location_id"],
            "population": ps["population"],
            "settlement_type": settlement["settlement_type"],
            "rank": settlement["rank"],
            "housing_capacity": settlement["housing_capacity"],
            "water_capacity": settlement["water_capacity"],
            "food_capacity": ps["food_capacity"],
            "safety": round(float(metrics["safety"]), 6),
            "employment": round(float(metrics["employment"]), 6),
            "migration_pressure": round(float(metrics["migration_pressure"]), 6),
            "prosperity": round(float(metrics["prosperity"]), 6),
            "stability": round(float(metrics["stability"]), 6),
            "hazard": round(float(metrics["hazard"]), 6),
            "cohorts": [
                {
                    "species": c["species"],
                    "culture": c["culture"],
                    "age_band": c["age_band"],
                    "livelihood": c["livelihood"],
                    "count": c["count"],
                }
                for c in snapshot["cohorts"][:30]
            ],
            "household_summary": snapshot["household_summary"],
            "labor": [
                {
                    "occupation": row["occupation"],
                    "demand": row["demand"],
                    "supply": row["supply"],
                    "filled": row["filled"],
                    "productivity": row["productivity"],
                    "wage_index": row["wage_index"],
                }
                for row in snapshot["labor"][:30]
            ],
            "service_gaps": [
                {
                    "service_kind": row["service_kind"],
                    "required_capacity": row["required_capacity"],
                    "available_capacity": row["available_capacity"],
                    "gap": row["gap"],
                }
                for row in snapshot["service_needs"]
                if float(row["gap"]) > 0
            ][:30],
            "authority_note": "Aggregate cohort/settlement state is authoritative; named residents are materialized separately and are not fabricated by this projection.",
        }

    def public_snapshot_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        *,
        location_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        if not location_id:
            raise ValueError(
                "public population inspection requires an actor-local location_id"
            )
        # Older campaigns may store actor locations as free-text labels without a
        # corresponding row in `locations`. World-context compilation must remain
        # read-only and backward compatible, so expose an explicit unregistered
        # projection rather than materializing state or raising from a legacy label.
        if location_id and not db.execute(
            "SELECT 1 FROM locations WHERE campaign_id=? AND id=?",
            (campaign_id, location_id),
        ).fetchone():
            return {
                "campaign_id": campaign_id,
                "location_id": location_id,
                "settlement": {
                    "location_id": location_id,
                    "population": 0.0,
                    "settlement_type": "unregistered",
                    "rank": "unregistered",
                    "housing_capacity": 0.0,
                    "water_capacity": 0.0,
                    "food_capacity": 0.0,
                    "safety": 0.5,
                    "employment": 0.5,
                    "migration_pressure": 0.0,
                    "prosperity": 0.5,
                    "stability": 0.5,
                    "hazard": 0.0,
                    "cohorts": [],
                    "household_summary": {
                        "mode": "unregistered",
                        "groups": 0,
                        "households": 0.0,
                        "persons": 0.0,
                        "housing_units": 0.0,
                    },
                    "labor": [],
                    "service_gaps": [],
                    "authority_note": "No registered location/population authority exists for this legacy location label; no state was fabricated.",
                },
            }
        snap = self.snapshot_db(db, campaign_id, location_id=location_id, limit=limit)
        return {
            "campaign_id": campaign_id,
            "location_id": location_id,
            "settlement": self._public_location_snapshot(snap),
        }

    def snapshot(self, campaign_id: str, **kwargs: Any) -> dict[str, Any]:
        with self.e._db() as db:
            return self.snapshot_db(db, campaign_id, **kwargs)

    def public_snapshot(self, campaign_id: str, **kwargs: Any) -> dict[str, Any]:
        with self.e._db() as db:
            return self.public_snapshot_db(db, campaign_id, **kwargs)

    def refresh(self, campaign_id: str) -> dict[str, Any]:
        campaign_id = self.e._clean_id(campaign_id)
        with self.e._write_db() as db:
            self.bootstrap_all_db(db, campaign_id)
            when = self._campaign_time_db(db, campaign_id)
            locations = [
                str(row["location_id"])
                for row in db.execute(
                    """SELECT location_id FROM population_state WHERE campaign_id=?
                       UNION SELECT location_id FROM settlement_profiles WHERE campaign_id=?
                       ORDER BY location_id""",
                    (campaign_id, campaign_id),
                ).fetchall()
            ]
            for location_id in locations:
                self._sync_population_summary_db(db, campaign_id, location_id)
                self.refresh_labor_db(db, campaign_id, location_id, when)
                cfg = self._config_db(db, campaign_id)
                pop = float(
                    self._population_state_db(db, campaign_id, location_id)["population"]
                )
                self._update_services_db(
                    db, campaign_id, location_id, pop, when, cfg, None
                )
                self.reconcile_households_db(
                    db, campaign_id, location_id, when, cfg
                )
            revision = self.e._next_revision(db, campaign_id)
            self.e._insert_event(
                db,
                campaign_id,
                revision,
                "population_refreshed",
                "Population projections refreshed",
                payload={"locations_refreshed": len(locations)},
            )
            return {
                "campaign_id": campaign_id,
                "status": "completed",
                "locations_refreshed": len(locations),
                "revision": revision,
                "mutation": {
                    "revision_advanced": True,
                    "event_type": "population_refreshed",
                },
            }

    def dispatch(
        self,
        operation: str,
        campaign_id: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        p = dict(payload or {})
        operation = str(operation or "").strip().lower()
        if operation == "configure":
            return self.configure(campaign_id, **p)
        if operation in {"save_settlement", "set_settlement"}:
            location_id = p.pop("location_id")
            return self.save_settlement(campaign_id, location_id, **p)
        if operation in {"save_cohort", "set_cohort"}:
            cohort_id = p.pop("cohort_id", p.pop("id", None))
            if not cohort_id:
                raise ValueError("save_cohort requires cohort_id")
            location_id = p.pop("location_id")
            return self.save_cohort(campaign_id, cohort_id, location_id, **p)
        if operation == "replace_cohorts":
            location_id = p.pop("location_id")
            return self.replace_cohorts(
                campaign_id, location_id, list(p.pop("cohorts", []))
            )
        if operation in {"save_household", "set_household"}:
            household_id = p.pop("household_id", p.pop("id", None))
            if not household_id:
                raise ValueError("save_household requires household_id")
            location_id = p.pop("location_id")
            return self.save_household(
                campaign_id, household_id, location_id, **p
            )
        if operation == "refresh":
            return self.refresh(campaign_id)
        if operation == "snapshot":
            return self.snapshot(campaign_id, **p)
        if operation in {"public_snapshot", "inspect"}:
            return self.public_snapshot(campaign_id, **p)
        raise ValueError(f"unknown population operation: {operation}")
