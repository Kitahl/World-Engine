from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .engine import WorldEngine


POLITICS_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS politics_config (
    campaign_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    daily_strategy_enabled INTEGER NOT NULL DEFAULT 1,
    max_daily_decisions INTEGER NOT NULL DEFAULT 20 CHECK(max_daily_decisions BETWEEN 0 AND 1000),
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS politics_commitments (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('character','npc','faction','location')),
    actor_id TEXT NOT NULL,
    resource_kind TEXT NOT NULL CHECK(resource_kind IN ('currency','inventory','manpower','labor','route_capacity')),
    resource_key TEXT NOT NULL,
    location_id TEXT,
    amount REAL NOT NULL CHECK(amount > 0),
    consumed REAL NOT NULL DEFAULT 0 CHECK(consumed >= 0),
    released REAL NOT NULL DEFAULT 0 CHECK(released >= 0),
    status TEXT NOT NULL DEFAULT 'reserved' CHECK(status IN ('reserved','consumed','released','cancelled')),
    purpose_kind TEXT NOT NULL,
    purpose_id TEXT NOT NULL,
    created_world_time TEXT NOT NULL,
    expires_world_time TEXT,
    revision INTEGER NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    CHECK(consumed + released <= amount + 0.000000001),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id,location_id) REFERENCES locations(campaign_id,id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_politics_commitment_resource
    ON politics_commitments(campaign_id,resource_kind,resource_key,actor_kind,actor_id,status,id);
CREATE INDEX IF NOT EXISTS idx_politics_commitment_purpose
    ON politics_commitments(campaign_id,purpose_kind,purpose_id,status,id);

CREATE TABLE IF NOT EXISTS politics_projects (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    owner_faction_id TEXT NOT NULL,
    location_id TEXT NOT NULL,
    project_kind TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','active','completed','cancelled')),
    progress REAL NOT NULL DEFAULT 0 CHECK(progress >= 0),
    work_required REAL NOT NULL CHECK(work_required > 0),
    requirements_json TEXT NOT NULL DEFAULT '[]',
    started_world_time TEXT,
    completed_world_time TEXT,
    last_step_world_time TEXT,
    revision INTEGER NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,owner_faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,location_id) REFERENCES locations(campaign_id,id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_politics_projects_active
    ON politics_projects(campaign_id,status,location_id,id);

CREATE TABLE IF NOT EXISTS politics_claims (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    claimant_faction_id TEXT NOT NULL,
    target_kind TEXT NOT NULL CHECK(target_kind IN ('faction','location','resource','treaty')),
    target_id TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    strength REAL NOT NULL DEFAULT 0.5 CHECK(strength BETWEEN 0 AND 1),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','satisfied','renounced','expired')),
    source_fact_id TEXT,
    visibility TEXT NOT NULL DEFAULT 'public' CHECK(visibility IN ('public','private')),
    created_world_time TEXT NOT NULL,
    revision INTEGER NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,claimant_faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_politics_claims_target
    ON politics_claims(campaign_id,target_kind,target_id,status,id);

CREATE TABLE IF NOT EXISTS politics_grievances (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    aggrieved_faction_id TEXT NOT NULL,
    against_faction_id TEXT NOT NULL,
    grievance_type TEXT NOT NULL,
    severity REAL NOT NULL DEFAULT 0.5 CHECK(severity BETWEEN 0 AND 1),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','resolved','waived','expired')),
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'public' CHECK(visibility IN ('public','private')),
    created_world_time TEXT NOT NULL,
    revision INTEGER NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,aggrieved_faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,against_faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_politics_grievances_pair
    ON politics_grievances(campaign_id,aggrieved_faction_id,against_faction_id,status,id);

CREATE TABLE IF NOT EXISTS politics_territorial_control (
    campaign_id TEXT NOT NULL,
    location_id TEXT NOT NULL,
    controller_faction_id TEXT NOT NULL,
    control REAL NOT NULL DEFAULT 1 CHECK(control BETWEEN 0 AND 1),
    occupation_state TEXT NOT NULL DEFAULT 'controlled' CHECK(occupation_state IN ('controlled','contested','occupied')),
    war_id TEXT,
    since_world_time TEXT NOT NULL,
    revision INTEGER NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,location_id),
    FOREIGN KEY(campaign_id,location_id) REFERENCES locations(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,controller_faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS politics_control_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL,
    location_id TEXT NOT NULL,
    old_controller_faction_id TEXT,
    new_controller_faction_id TEXT NOT NULL,
    war_id TEXT,
    reason TEXT NOT NULL,
    world_time TEXT NOT NULL,
    revision INTEGER NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS politics_proposals (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    proposer_faction_id TEXT NOT NULL,
    recipient_faction_id TEXT NOT NULL,
    proposal_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected','countered','withdrawn','expired')),
    terms_json TEXT NOT NULL DEFAULT '{}',
    counter_of_id TEXT,
    treaty_id TEXT,
    visibility TEXT NOT NULL DEFAULT 'private' CHECK(visibility IN ('public','private')),
    created_world_time TEXT NOT NULL,
    expires_world_time TEXT,
    responded_world_time TEXT,
    revision INTEGER NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,proposer_faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,recipient_faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,counter_of_id) REFERENCES politics_proposals(campaign_id,id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_politics_proposals_recipient
    ON politics_proposals(campaign_id,recipient_faction_id,status,id);

CREATE TABLE IF NOT EXISTS politics_treaties (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    treaty_type TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','suspended','terminated','fulfilled')),
    visibility TEXT NOT NULL DEFAULT 'public' CHECK(visibility IN ('public','private')),
    source_proposal_id TEXT,
    effective_world_time TEXT NOT NULL,
    end_world_time TEXT,
    revision INTEGER NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,source_proposal_id) REFERENCES politics_proposals(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS politics_treaty_parties (
    campaign_id TEXT NOT NULL,
    treaty_id TEXT NOT NULL,
    faction_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'party',
    signed_world_time TEXT NOT NULL,
    PRIMARY KEY(campaign_id,treaty_id,faction_id),
    FOREIGN KEY(campaign_id,treaty_id) REFERENCES politics_treaties(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id,faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS politics_treaty_clauses (
    campaign_id TEXT NOT NULL,
    treaty_id TEXT NOT NULL,
    id TEXT NOT NULL,
    clause_type TEXT NOT NULL,
    terms_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','satisfied','breached','expired')),
    PRIMARY KEY(campaign_id,treaty_id,id),
    FOREIGN KEY(campaign_id,treaty_id) REFERENCES politics_treaties(campaign_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS politics_obligations (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    treaty_id TEXT NOT NULL,
    clause_id TEXT,
    debtor_faction_id TEXT NOT NULL,
    beneficiary_faction_id TEXT NOT NULL,
    obligation_type TEXT NOT NULL,
    due_world_time TEXT,
    amount REAL CHECK(amount IS NULL OR amount >= 0),
    resource_kind TEXT,
    resource_key TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','fulfilled','violated','waived')),
    terms_json TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,treaty_id) REFERENCES politics_treaties(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id,debtor_faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,beneficiary_faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_politics_obligations_due
    ON politics_obligations(campaign_id,status,due_world_time,id);

CREATE TABLE IF NOT EXISTS politics_treaty_violations (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    treaty_id TEXT NOT NULL,
    obligation_id TEXT,
    violator_faction_id TEXT NOT NULL,
    harmed_faction_id TEXT NOT NULL,
    violation_type TEXT NOT NULL,
    severity REAL NOT NULL DEFAULT 0.5 CHECK(severity BETWEEN 0 AND 1),
    world_time TEXT NOT NULL,
    revision INTEGER NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,treaty_id) REFERENCES politics_treaties(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id,obligation_id) REFERENCES politics_obligations(campaign_id,id) ON DELETE SET NULL,
    FOREIGN KEY(campaign_id,violator_faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,harmed_faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS politics_forces (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    faction_id TEXT NOT NULL,
    name TEXT NOT NULL,
    force_type TEXT NOT NULL DEFAULT 'levy',
    location_id TEXT NOT NULL,
    source_cohort_id TEXT NOT NULL,
    manpower REAL NOT NULL CHECK(manpower >= 0),
    readiness REAL NOT NULL DEFAULT 0.5 CHECK(readiness BETWEEN 0 AND 1),
    morale REAL NOT NULL DEFAULT 0.5 CHECK(morale BETWEEN 0 AND 1),
    status TEXT NOT NULL DEFAULT 'mobilized' CHECK(status IN ('mobilized','deployed','routed','demobilized','destroyed')),
    manpower_commitment_id TEXT NOT NULL,
    supply_item_id TEXT,
    supply_remaining REAL NOT NULL DEFAULT 0 CHECK(supply_remaining >= 0),
    mobilized_world_time TEXT NOT NULL,
    revision INTEGER NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,location_id) REFERENCES locations(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,source_cohort_id) REFERENCES population_cohorts(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,manpower_commitment_id) REFERENCES politics_commitments(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,supply_item_id) REFERENCES item_defs(campaign_id,id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_politics_forces_faction
    ON politics_forces(campaign_id,faction_id,status,location_id,id);

CREATE TABLE IF NOT EXISTS politics_force_losses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL,
    force_id TEXT NOT NULL,
    loss_kind TEXT NOT NULL CHECK(loss_kind IN ('casualty','desertion','capture')),
    count REAL NOT NULL CHECK(count > 0),
    cause_kind TEXT NOT NULL,
    cause_id TEXT NOT NULL,
    world_time TEXT NOT NULL,
    revision INTEGER NOT NULL,
    FOREIGN KEY(campaign_id,force_id) REFERENCES politics_forces(campaign_id,id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS politics_raids (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    attacker_faction_id TEXT NOT NULL,
    target_faction_id TEXT NOT NULL,
    force_id TEXT NOT NULL,
    origin_location_id TEXT NOT NULL,
    target_location_id TEXT NOT NULL,
    route_id TEXT NOT NULL,
    supply_commitment_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','resolved','cancelled')),
    outcome_json TEXT NOT NULL DEFAULT '{}',
    planned_world_time TEXT NOT NULL,
    resolved_world_time TEXT,
    revision INTEGER NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,force_id) REFERENCES politics_forces(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,supply_commitment_id) REFERENCES politics_commitments(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,attacker_faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,target_faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,origin_location_id) REFERENCES locations(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,target_location_id) REFERENCES locations(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,route_id) REFERENCES economy_routes(campaign_id,id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS politics_wars (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    attacker_faction_id TEXT NOT NULL,
    defender_faction_id TEXT NOT NULL,
    casus_belli_kind TEXT NOT NULL CHECK(casus_belli_kind IN ('claim','grievance','treaty_violation')),
    casus_belli_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','armistice','ended')),
    goals_json TEXT NOT NULL DEFAULT '[]',
    started_world_time TEXT NOT NULL,
    ended_world_time TEXT,
    peace_treaty_id TEXT,
    revision INTEGER NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,attacker_faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,defender_faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,peace_treaty_id) REFERENCES politics_treaties(campaign_id,id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS politics_war_participants (
    campaign_id TEXT NOT NULL,
    war_id TEXT NOT NULL,
    faction_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('attacker','defender')),
    joined_world_time TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','withdrawn','defeated')),
    PRIMARY KEY(campaign_id,war_id,faction_id),
    FOREIGN KEY(campaign_id,war_id) REFERENCES politics_wars(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id,faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS politics_jurisdictions (
    campaign_id TEXT NOT NULL,
    location_id TEXT NOT NULL,
    authority_faction_id TEXT NOT NULL,
    law_profile_json TEXT NOT NULL DEFAULT '{}',
    enforcement_capacity REAL NOT NULL DEFAULT 0 CHECK(enforcement_capacity >= 0),
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,location_id),
    FOREIGN KEY(campaign_id,location_id) REFERENCES locations(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,authority_faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS politics_legal_cases (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    location_id TEXT NOT NULL,
    authority_faction_id TEXT NOT NULL,
    offender_kind TEXT NOT NULL CHECK(offender_kind IN ('character','npc','faction')),
    offender_id TEXT NOT NULL,
    offense TEXT NOT NULL,
    severity REAL NOT NULL DEFAULT 0.5 CHECK(severity BETWEEN 0 AND 1),
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','adjudicated','dismissed','closed')),
    source_crime_id TEXT,
    disposition_json TEXT NOT NULL DEFAULT '{}',
    opened_world_time TEXT NOT NULL,
    closed_world_time TEXT,
    revision INTEGER NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,location_id) REFERENCES locations(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id,authority_faction_id) REFERENCES factions(campaign_id,id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS politics_action_receipts (
    campaign_id TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('character','npc','faction','location','system')),
    actor_id TEXT NOT NULL,
    request_key TEXT NOT NULL,
    operation TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    result_json TEXT NOT NULL,
    revision INTEGER NOT NULL,
    world_time TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,actor_kind,actor_id,request_key),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS politics_daily_steps (
    campaign_id TEXT NOT NULL,
    day_key TEXT NOT NULL,
    world_time TEXT NOT NULL,
    revision INTEGER NOT NULL,
    tally_json TEXT NOT NULL,
    PRIMARY KEY(campaign_id,day_key),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
"""


_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
_KINDS = {"character", "npc", "faction", "location"}
_MAX_NUMBER = 1_000_000_000.0
_MAX_JSON_BYTES = 131_072
_MAX_DEPTH = 12
_MAX_NODES = 4096


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc(value: datetime | str) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_guard(value: Any, field: str) -> None:
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > _MAX_NODES or depth > _MAX_DEPTH:
            raise ValueError(f"{field} is too complex")
        if isinstance(item, dict):
            pending.extend((str(key), depth + 1) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError(f"{field} contains a non-finite number")
    try:
        size = len(_canonical(value).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite JSON data") from exc
    if size > _MAX_JSON_BYTES:
        raise ValueError(f"{field} is too large")


class PoliticsKernel:
    """Transaction-aware aggregate politics, commitment, and conflict kernel.

    Currency and inventory reservations are escrowed immediately so another
    subsystem cannot spend them. Manpower, labour, and route capacity remain
    visible in their canonical stores and are protected by active commitments;
    consumers must call ``available_*_db`` rather than treating gross values as
    free capacity. All ``*_db`` mutators use the caller's transaction/revision.
    """

    def __init__(self, engine: WorldEngine):
        self.e = engine

    @staticmethod
    def _id(value: Any, field: str = "id") -> str:
        text = str(value or "").strip()
        if not _ID_RE.fullmatch(text):
            raise ValueError(f"{field} must be 1-100 safe identifier characters")
        return text

    @staticmethod
    def _number(
        value: Any,
        field: str,
        *,
        minimum: float = 0.0,
        maximum: float = _MAX_NUMBER,
        positive: bool = False,
    ) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{field} must be a finite number")  # noqa: TRY004
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be a finite number") from exc
        if not math.isfinite(parsed) or parsed < minimum or parsed > maximum:
            raise ValueError(f"{field} must be between {minimum} and {maximum}")
        if positive and parsed <= 0:
            raise ValueError(f"{field} must be positive")
        return parsed

    @staticmethod
    def _row_json(
        engine: WorldEngine, row: sqlite3.Row, *fields: str
    ) -> dict[str, Any]:
        out = dict(row)
        for field in fields:
            out[field] = engine._loads(
                out.pop(field + "_json") or ("[]" if field.endswith("s") else "{}")
            )
        return out

    @staticmethod
    def _table(db: sqlite3.Connection, name: str) -> bool:
        return (
            db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            is not None
        )

    def install_schema_db(self, db: sqlite3.Connection) -> None:
        """Install owned schema without committing or claiming PRAGMA user_version."""
        before = int(db.execute("PRAGMA user_version").fetchone()[0])
        # ``Connection.executescript`` may implicitly commit a caller-owned
        # transaction. Execute each additive DDL statement separately so a
        # failed stage-21 migration remains rollback-safe.
        for statement in POLITICS_SCHEMA.split(";"):
            if statement.strip():
                db.execute(statement)
        if int(db.execute("PRAGMA user_version").fetchone()[0]) != before:
            raise RuntimeError("politics schema must not claim shared user_version")
        violations = db.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError("politics schema foreign-key verification failed")

    def seed_defaults_db(self, db: sqlite3.Connection, campaign_id: str) -> None:
        now = self.e._now()
        db.execute(
            """INSERT INTO politics_config(campaign_id,updated_at) VALUES(?,?)
               ON CONFLICT(campaign_id) DO NOTHING""",
            (self._id(campaign_id, "campaign_id"), now),
        )

    def _campaign_db(self, db: sqlite3.Connection, campaign_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT id,revision,world_time FROM campaigns WHERE id=?", (campaign_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"unknown campaign: {campaign_id}")
        return row

    def _faction_db(
        self, db: sqlite3.Connection, campaign_id: str, faction_id: str
    ) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM factions WHERE campaign_id=? AND id=?",
            (campaign_id, faction_id),
        ).fetchone()
        if not row:
            raise KeyError(f"unknown faction: {faction_id}")
        return row

    def _location_db(
        self, db: sqlite3.Connection, campaign_id: str, location_id: str
    ) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM locations WHERE campaign_id=? AND id=?",
            (campaign_id, location_id),
        ).fetchone()
        if not row:
            raise KeyError(f"unknown location: {location_id}")
        return row

    def _owner_db(
        self, db: sqlite3.Connection, campaign_id: str, kind: str, owner_id: str
    ) -> None:
        kind = str(kind).lower()
        if kind not in _KINDS:
            raise ValueError("invalid actor_kind")
        table = {
            "character": "characters",
            "npc": "npcs",
            "faction": "factions",
            "location": "locations",
        }[kind]
        if not db.execute(
            f"SELECT 1 FROM {table} WHERE campaign_id=? AND id=?",
            (campaign_id, owner_id),
        ).fetchone():
            raise KeyError(f"unknown {kind}: {owner_id}")

    def _emit(
        self,
        emit: Callable[..., None] | None,
        event_type: str,
        summary: str,
        payload: dict[str, Any],
        location_id: str | None,
        when: datetime,
        *,
        sensitivity: str = "PUBLIC",
        scope_type: str = "WORLD",
        principal_kind: str | None = None,
        principal_id: str | None = None,
    ) -> None:
        if emit:
            sensitivity = str(sensitivity).upper()
            scope_type = str(scope_type).upper()
            if sensitivity not in {"PUBLIC", "PRIVATE", "SECRET"}:
                raise ValueError("invalid politics event sensitivity")
            if scope_type not in {"WORLD", "ENTITY", "GM", "SYSTEM"}:
                raise ValueError("invalid politics event scope")
            if scope_type == "ENTITY" and (not principal_kind or not principal_id):
                raise ValueError("ENTITY politics event requires a principal")
            emit(
                event_type,
                summary,
                payload,
                location_id,
                when,
                sensitivity=sensitivity,
                scope_type=scope_type,
                principal_kind=principal_kind,
                principal_id=principal_id,
            )

    def _action_event_policy_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        operation: str,
        payload: dict[str, Any],
        actor_kind: str,
        actor_id: str,
    ) -> dict[str, str | None]:
        """Classify the audit event without widening the public model context.

        Politics records are often deliberately private even when the mutation is
        trusted. Public world changes are explicit; everything else is scoped to
        its faction principal or to the GM for system work. Visibility-bearing
        diplomacy records may opt into public audit events.
        """

        public = operation in {"set_control", "declare_war", "occupy", "make_peace"}
        if operation in {"add_claim", "add_grievance", "create_treaty"}:
            public = str(payload.get("visibility", "public")).lower() == "public"
        elif operation == "create_proposal":
            public = str(payload.get("visibility", "private")).lower() == "public"
        elif operation == "respond_proposal":
            row = db.execute(
                "SELECT visibility FROM politics_proposals WHERE campaign_id=? AND id=?",
                (campaign_id, payload.get("proposal_id")),
            ).fetchone()
            public = bool(row and str(row["visibility"]).lower() == "public")
        elif operation in {"record_violation", "fulfill_obligation"}:
            if operation == "record_violation":
                row = db.execute(
                    "SELECT visibility FROM politics_treaties WHERE campaign_id=? AND id=?",
                    (campaign_id, payload.get("treaty_id")),
                ).fetchone()
            else:
                row = db.execute(
                    """SELECT t.visibility FROM politics_obligations o
                       JOIN politics_treaties t ON t.campaign_id=o.campaign_id
                                                AND t.id=o.treaty_id
                       WHERE o.campaign_id=? AND o.id=?""",
                    (campaign_id, payload.get("obligation_id")),
                ).fetchone()
            public = bool(row and str(row["visibility"]).lower() == "public")
        if public:
            return {
                "sensitivity": "PUBLIC",
                "scope_type": "WORLD",
                "principal_kind": None,
                "principal_id": None,
            }
        if actor_kind == "faction":
            return {
                "sensitivity": "PRIVATE",
                "scope_type": "ENTITY",
                "principal_kind": "faction",
                "principal_id": actor_id,
            }
        return {
            "sensitivity": "PRIVATE",
            "scope_type": "GM",
            "principal_kind": None,
            "principal_id": None,
        }

    def _reserved_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        resource_kind: str,
        resource_key: str,
        actor_kind: str,
        actor_id: str,
        *,
        exclude_id: str | None = None,
    ) -> float:
        params: list[Any] = [
            campaign_id,
            resource_kind,
            resource_key,
            actor_kind,
            actor_id,
        ]
        sql = """SELECT COALESCE(SUM(amount-consumed-released),0) n
                 FROM politics_commitments
                 WHERE campaign_id=? AND resource_kind=? AND resource_key=?
                   AND actor_kind=? AND actor_id=? AND status='reserved'"""
        if exclude_id:
            sql += " AND id<>?"
            params.append(exclude_id)
        row = db.execute(sql, params).fetchone()
        return max(0.0, float(row["n"] or 0.0))

    def reserved_amount_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        resource_kind: str,
        resource_key: str,
        actor_kind: str,
        actor_id: str,
    ) -> float:
        return self._reserved_db(
            db, campaign_id, resource_kind, resource_key, actor_kind, actor_id
        )

    def _reserved_total_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        resource_kind: str,
        resource_key: str,
        *,
        location_id: str | None = None,
    ) -> float:
        params: list[Any] = [campaign_id, resource_kind, resource_key]
        sql = """SELECT COALESCE(SUM(amount-consumed-released),0) n
                 FROM politics_commitments WHERE campaign_id=? AND resource_kind=?
                   AND resource_key=? AND status='reserved'"""
        if location_id is not None:
            sql += " AND location_id=?"
            params.append(location_id)
        row = db.execute(sql, params).fetchone()
        return max(0.0, float(row["n"] or 0.0))

    def _gross_available_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        resource_kind: str,
        resource_key: str,
        actor_kind: str,
        actor_id: str,
        location_id: str | None,
    ) -> float:
        if resource_kind == "currency":
            row = db.execute(
                """SELECT amount FROM owner_balances WHERE campaign_id=? AND owner_kind=?
                   AND owner_id=? AND currency_key=?""",
                (campaign_id, actor_kind, actor_id, resource_key),
            ).fetchone()
            return float(row["amount"] if row else 0.0)
        if resource_kind == "inventory":
            row = db.execute(
                """SELECT qty FROM inventories WHERE campaign_id=? AND owner_kind=?
                   AND owner_id=? AND item_id=?""",
                (campaign_id, actor_kind, actor_id, resource_key),
            ).fetchone()
            return float(row["qty"] if row else 0.0)
        if resource_kind == "manpower":
            row = db.execute(
                """SELECT count FROM population_cohorts WHERE campaign_id=? AND id=?
                   AND (? IS NULL OR location_id=?)""",
                (campaign_id, resource_key, location_id, location_id),
            ).fetchone()
            if not row:
                raise KeyError(f"unknown population cohort: {resource_key}")
            return float(row["count"])
        if resource_kind == "labor":
            if not location_id:
                raise ValueError("labor reservation requires location_id")
            row = db.execute(
                """SELECT supply FROM settlement_labor WHERE campaign_id=? AND location_id=?
                   AND occupation=?""",
                (campaign_id, location_id, resource_key),
            ).fetchone()
            return float(row["supply"] if row else 0.0)
        if resource_kind == "route_capacity":
            row = db.execute(
                """SELECT capacity_qty_per_day FROM economy_routes
                   WHERE campaign_id=? AND id=? AND active=1""",
                (campaign_id, resource_key),
            ).fetchone()
            if not row:
                raise KeyError(f"unknown or inactive economy route: {resource_key}")
            return float(row["capacity_qty_per_day"])
        raise ValueError("invalid resource_kind")

    def available_resource_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        resource_kind: str,
        resource_key: str,
        actor_kind: str,
        actor_id: str,
        *,
        location_id: str | None = None,
    ) -> float:
        gross = self._gross_available_db(
            db,
            campaign_id,
            resource_kind,
            resource_key,
            actor_kind,
            actor_id,
            location_id,
        )
        # Currency and inventory are physically escrowed at reservation time.
        if resource_kind in {"currency", "inventory"}:
            return max(0.0, gross)
        return max(
            0.0,
            gross
            - self._reserved_total_db(
                db,
                campaign_id,
                resource_kind,
                resource_key,
                location_id=location_id if resource_kind == "labor" else None,
            ),
        )

    def _escrow_adjust_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        kind: str,
        key: str,
        actor_kind: str,
        actor_id: str,
        delta: float,
    ) -> None:
        now = self.e._now()
        if kind == "currency":
            row = db.execute(
                """SELECT amount FROM owner_balances WHERE campaign_id=? AND owner_kind=?
                   AND owner_id=? AND currency_key=?""",
                (campaign_id, actor_kind, actor_id, key),
            ).fetchone()
            result = float(row["amount"] if row else 0.0) + delta
            if result < -1e-9:
                raise ValueError(f"insufficient currency: {key}")
            db.execute(
                """INSERT INTO owner_balances(campaign_id,owner_kind,owner_id,currency_key,amount,updated_at)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(campaign_id,owner_kind,owner_id,currency_key)
                   DO UPDATE SET amount=excluded.amount,updated_at=excluded.updated_at""",
                (campaign_id, actor_kind, actor_id, key, max(0.0, result), now),
            )
        elif kind == "inventory":
            row = db.execute(
                """SELECT qty FROM inventories WHERE campaign_id=? AND owner_kind=?
                   AND owner_id=? AND item_id=?""",
                (campaign_id, actor_kind, actor_id, key),
            ).fetchone()
            result = float(row["qty"] if row else 0.0) + delta
            if result < -1e-9:
                raise ValueError(f"insufficient inventory: {key}")
            db.execute(
                """INSERT INTO inventories(campaign_id,owner_kind,owner_id,item_id,qty,metadata_json,updated_at)
                   VALUES(?,?,?,?,?,'{}',?) ON CONFLICT(campaign_id,owner_kind,owner_id,item_id)
                   DO UPDATE SET qty=excluded.qty,updated_at=excluded.updated_at""",
                (campaign_id, actor_kind, actor_id, key, max(0.0, result), now),
            )

    def reserve_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        commitment_id: str,
        *,
        actor_kind: str,
        actor_id: str,
        resource_kind: str,
        resource_key: str,
        amount: float,
        purpose_kind: str,
        purpose_id: str,
        revision: int,
        when: datetime,
        location_id: str | None = None,
        expires_world_time: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        campaign_id = self._id(campaign_id, "campaign_id")
        commitment_id = self._id(commitment_id, "commitment_id")
        actor_kind = str(actor_kind).lower()
        actor_id = self._id(actor_id, "actor_id")
        resource_kind = str(resource_kind).lower()
        resource_key = self._id(resource_key, "resource_key")
        purpose_kind = self._id(purpose_kind, "purpose_kind")
        purpose_id = self._id(purpose_id, "purpose_id")
        amount = self._number(amount, "amount", positive=True)
        when = _utc(when)
        meta = dict(metadata or {})
        _json_guard(meta, "metadata")
        self._campaign_db(db, campaign_id)
        self._owner_db(db, campaign_id, actor_kind, actor_id)
        if db.execute(
            "SELECT 1 FROM politics_commitments WHERE campaign_id=? AND id=?",
            (campaign_id, commitment_id),
        ).fetchone():
            raise ValueError(f"commitment already exists: {commitment_id}")
        if location_id:
            location_id = self._id(location_id, "location_id")
            self._location_db(db, campaign_id, location_id)
        available = self.available_resource_db(
            db,
            campaign_id,
            resource_kind,
            resource_key,
            actor_kind,
            actor_id,
            location_id=location_id,
        )
        if available + 1e-9 < amount:
            raise ValueError(
                f"insufficient uncommitted {resource_kind}: {resource_key}"
            )
        if resource_kind in {"currency", "inventory"}:
            self._escrow_adjust_db(
                db,
                campaign_id,
                resource_kind,
                resource_key,
                actor_kind,
                actor_id,
                -amount,
            )
            meta = {**meta, "escrowed": True}
        db.execute(
            """INSERT INTO politics_commitments(
                   campaign_id,id,actor_kind,actor_id,resource_kind,resource_key,location_id,
                   amount,consumed,released,status,purpose_kind,purpose_id,created_world_time,
                   expires_world_time,revision,metadata_json,updated_at)
               VALUES(?,?,?,?,?,?,?, ?,0,0,'reserved',?,?,?,?,?,?,?)""",
            (
                campaign_id,
                commitment_id,
                actor_kind,
                actor_id,
                resource_kind,
                resource_key,
                location_id,
                amount,
                purpose_kind,
                purpose_id,
                when.isoformat(),
                expires_world_time,
                int(revision),
                self.e._dumps(meta),
                self.e._now(),
            ),
        )
        return self.get_commitment_db(db, campaign_id, commitment_id)

    def get_commitment_db(
        self, db: sqlite3.Connection, campaign_id: str, commitment_id: str
    ) -> dict[str, Any]:
        row = db.execute(
            "SELECT * FROM politics_commitments WHERE campaign_id=? AND id=?",
            (campaign_id, commitment_id),
        ).fetchone()
        if not row:
            raise KeyError(f"unknown commitment: {commitment_id}")
        return self._row_json(self.e, row, "metadata")

    # ------------------------------------------------------------------
    # Diplomacy, treaties, obligations, and violations
    # ------------------------------------------------------------------

    def _proposal_db(
        self, db: sqlite3.Connection, campaign_id: str, proposal_id: str
    ) -> dict[str, Any]:
        row = db.execute(
            "SELECT * FROM politics_proposals WHERE campaign_id=? AND id=?",
            (campaign_id, proposal_id),
        ).fetchone()
        if not row:
            raise KeyError(f"unknown proposal: {proposal_id}")
        return self._row_json(self.e, row, "terms", "metadata")

    def create_proposal_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        proposal_id: str,
        *,
        proposer_faction_id: str,
        recipient_faction_id: str,
        proposal_type: str,
        terms: dict[str, Any],
        revision: int,
        when: datetime,
        expires_world_time: str | None = None,
        counter_of_id: str | None = None,
        visibility: str = "private",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        proposal_id = self._id(proposal_id, "proposal_id")
        proposer_faction_id = self._id(proposer_faction_id, "proposer_faction_id")
        recipient_faction_id = self._id(recipient_faction_id, "recipient_faction_id")
        if proposer_faction_id == recipient_faction_id:
            raise ValueError("proposal factions must differ")
        self._faction_db(db, campaign_id, proposer_faction_id)
        self._faction_db(db, campaign_id, recipient_faction_id)
        if counter_of_id:
            counter_of_id = self._id(counter_of_id, "counter_of_id")
            if not db.execute(
                "SELECT 1 FROM politics_proposals WHERE campaign_id=? AND id=?",
                (campaign_id, counter_of_id),
            ).fetchone():
                raise KeyError(f"unknown proposal: {counter_of_id}")
        visibility = str(visibility).lower()
        if visibility not in {"public", "private"}:
            raise ValueError("invalid visibility")
        terms = dict(terms or {})
        meta = dict(metadata or {})
        _json_guard(terms, "terms")
        _json_guard(meta, "metadata")
        when = _utc(when)
        expiry = _utc(expires_world_time).isoformat() if expires_world_time else None
        if expiry and _utc(expiry) <= when:
            raise ValueError("proposal expiry must be after creation")
        db.execute(
            """INSERT INTO politics_proposals(
                   campaign_id,id,proposer_faction_id,recipient_faction_id,proposal_type,
                   status,terms_json,counter_of_id,visibility,created_world_time,
                   expires_world_time,revision,metadata_json,updated_at)
               VALUES(?,?,?,?,?,'pending',?,?,?,?,?,?,?,?)""",
            (
                campaign_id,
                proposal_id,
                proposer_faction_id,
                recipient_faction_id,
                self._id(proposal_type, "proposal_type"),
                self.e._dumps(terms),
                counter_of_id,
                visibility,
                when.isoformat(),
                expiry,
                int(revision),
                self.e._dumps(meta),
                self.e._now(),
            ),
        )
        return self._proposal_db(db, campaign_id, proposal_id)

    def _treaty_db(
        self, db: sqlite3.Connection, campaign_id: str, treaty_id: str
    ) -> dict[str, Any]:
        row = db.execute(
            "SELECT * FROM politics_treaties WHERE campaign_id=? AND id=?",
            (campaign_id, treaty_id),
        ).fetchone()
        if not row:
            raise KeyError(f"unknown treaty: {treaty_id}")
        out = self._row_json(self.e, row, "metadata")
        out["parties"] = [
            dict(item)
            for item in db.execute(
                """SELECT faction_id,role,signed_world_time FROM politics_treaty_parties
                   WHERE campaign_id=? AND treaty_id=? ORDER BY faction_id""",
                (campaign_id, treaty_id),
            ).fetchall()
        ]
        out["clauses"] = [
            self._row_json(self.e, item, "terms")
            for item in db.execute(
                """SELECT * FROM politics_treaty_clauses WHERE campaign_id=?
                   AND treaty_id=? ORDER BY id""",
                (campaign_id, treaty_id),
            ).fetchall()
        ]
        out["obligations"] = [
            self._row_json(self.e, item, "terms")
            for item in db.execute(
                """SELECT * FROM politics_obligations WHERE campaign_id=?
                   AND treaty_id=? ORDER BY id""",
                (campaign_id, treaty_id),
            ).fetchall()
        ]
        return out

    def create_treaty_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        treaty_id: str,
        *,
        treaty_type: str,
        name: str,
        parties: Iterable[str],
        clauses: Iterable[dict[str, Any]],
        obligations: Iterable[dict[str, Any]],
        revision: int,
        when: datetime,
        visibility: str = "public",
        source_proposal_id: str | None = None,
        end_world_time: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        treaty_id = self._id(treaty_id, "treaty_id")
        party_ids = sorted({self._id(item, "party") for item in parties})
        if not 2 <= len(party_ids) <= 32:
            raise ValueError("treaty requires 2-32 distinct parties")
        for faction_id in party_ids:
            self._faction_db(db, campaign_id, faction_id)
        clause_list = [dict(item) for item in clauses]
        obligation_list = [dict(item) for item in obligations]
        if len(clause_list) > 128 or len(obligation_list) > 256:
            raise ValueError("treaty is too large")
        _json_guard(clause_list, "clauses")
        _json_guard(obligation_list, "obligations")
        meta = dict(metadata or {})
        _json_guard(meta, "metadata")
        visibility = str(visibility).lower()
        if visibility not in {"public", "private"}:
            raise ValueError("invalid visibility")
        when = _utc(when)
        db.execute(
            """INSERT INTO politics_treaties(
                   campaign_id,id,treaty_type,name,status,visibility,source_proposal_id,
                   effective_world_time,end_world_time,revision,metadata_json,updated_at)
               VALUES(?,?,?,?,'active',?,?,?,?,?,?,?)""",
            (
                campaign_id,
                treaty_id,
                self._id(treaty_type, "treaty_type"),
                str(name)[:200],
                visibility,
                source_proposal_id,
                when.isoformat(),
                _utc(end_world_time).isoformat() if end_world_time else None,
                int(revision),
                self.e._dumps(meta),
                self.e._now(),
            ),
        )
        for faction_id in party_ids:
            db.execute(
                """INSERT INTO politics_treaty_parties(
                       campaign_id,treaty_id,faction_id,role,signed_world_time)
                   VALUES(?,?,?,'party',?)""",
                (campaign_id, treaty_id, faction_id, when.isoformat()),
            )
        clause_ids: set[str] = set()
        for index, clause in enumerate(clause_list):
            clause_id = self._id(clause.get("id") or f"clause-{index + 1}", "clause_id")
            if clause_id in clause_ids:
                raise ValueError("duplicate treaty clause id")
            clause_ids.add(clause_id)
            terms = dict(clause.get("terms") or {})
            _json_guard(terms, "clause terms")
            db.execute(
                """INSERT INTO politics_treaty_clauses(
                       campaign_id,treaty_id,id,clause_type,terms_json,status)
                   VALUES(?,?,?,?,?,'active')""",
                (
                    campaign_id,
                    treaty_id,
                    clause_id,
                    self._id(
                        clause.get("clause_type") or clause.get("type"), "clause_type"
                    ),
                    self.e._dumps(terms),
                ),
            )
        for index, obligation in enumerate(obligation_list):
            obligation_id = self._id(
                obligation.get("id") or f"{treaty_id}:obligation:{index + 1}",
                "obligation_id",
            )
            debtor = self._id(obligation.get("debtor_faction_id"), "debtor_faction_id")
            beneficiary = self._id(
                obligation.get("beneficiary_faction_id"), "beneficiary_faction_id"
            )
            if (
                debtor not in party_ids
                or beneficiary not in party_ids
                or debtor == beneficiary
            ):
                raise ValueError("obligation parties must be distinct treaty parties")
            clause_id = obligation.get("clause_id")
            if clause_id is not None and str(clause_id) not in clause_ids:
                raise ValueError("obligation references unknown clause")
            amount = obligation.get("amount")
            parsed_amount = (
                None if amount is None else self._number(amount, "obligation amount")
            )
            terms = dict(obligation.get("terms") or {})
            _json_guard(terms, "obligation terms")
            db.execute(
                """INSERT INTO politics_obligations(
                       campaign_id,id,treaty_id,clause_id,debtor_faction_id,
                       beneficiary_faction_id,obligation_type,due_world_time,amount,
                       resource_kind,resource_key,status,terms_json,revision,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?)""",
                (
                    campaign_id,
                    obligation_id,
                    treaty_id,
                    str(clause_id) if clause_id else None,
                    debtor,
                    beneficiary,
                    self._id(
                        obligation.get("obligation_type") or obligation.get("type"),
                        "obligation_type",
                    ),
                    _utc(obligation["due_world_time"]).isoformat()
                    if obligation.get("due_world_time")
                    else None,
                    parsed_amount,
                    str(obligation.get("resource_kind"))
                    if obligation.get("resource_kind")
                    else None,
                    str(obligation.get("resource_key"))
                    if obligation.get("resource_key")
                    else None,
                    self.e._dumps(terms),
                    int(revision),
                    self.e._now(),
                ),
            )
        return self._treaty_db(db, campaign_id, treaty_id)

    def respond_proposal_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        proposal_id: str,
        *,
        responder_faction_id: str,
        response: str,
        revision: int,
        when: datetime,
        counter_id: str | None = None,
        counter_terms: dict[str, Any] | None = None,
        treaty_id: str | None = None,
    ) -> dict[str, Any]:
        proposal = self._proposal_db(db, campaign_id, proposal_id)
        if proposal["status"] != "pending":
            raise ValueError("proposal is not pending")
        responder_faction_id = self._id(responder_faction_id, "responder_faction_id")
        if responder_faction_id != proposal["recipient_faction_id"]:
            raise ValueError("only the recipient may respond")
        response = str(response).lower()
        when = _utc(when)
        if response == "reject":
            status = "rejected"
            result: dict[str, Any] = {"proposal_id": proposal_id, "status": status}
        elif response == "counter":
            if not counter_id or counter_terms is None:
                raise ValueError("counter requires counter_id and counter_terms")
            status = "countered"
            result = self.create_proposal_db(
                db,
                campaign_id,
                counter_id,
                proposer_faction_id=responder_faction_id,
                recipient_faction_id=str(proposal["proposer_faction_id"]),
                proposal_type=str(proposal["proposal_type"]),
                terms=counter_terms,
                revision=revision,
                when=when,
                counter_of_id=proposal_id,
                visibility=str(proposal["visibility"]),
            )
        elif response == "accept":
            status = "accepted"
            treaty_id = self._id(treaty_id or f"treaty:{proposal_id}", "treaty_id")
            terms = dict(proposal["terms"])
            result = self.create_treaty_db(
                db,
                campaign_id,
                treaty_id,
                treaty_type=str(proposal["proposal_type"]),
                name=str(terms.get("name") or f"{proposal['proposal_type']} agreement"),
                parties=[str(proposal["proposer_faction_id"]), responder_faction_id],
                clauses=list(terms.get("clauses") or []),
                obligations=list(terms.get("obligations") or []),
                revision=revision,
                when=when,
                visibility=str(proposal["visibility"]),
                source_proposal_id=proposal_id,
                end_world_time=terms.get("end_world_time"),
            )
            db.execute(
                "UPDATE politics_proposals SET treaty_id=? WHERE campaign_id=? AND id=?",
                (treaty_id, campaign_id, proposal_id),
            )
        else:
            raise ValueError("response must be accept, reject, or counter")
        db.execute(
            """UPDATE politics_proposals SET status=?,responded_world_time=?,revision=?,updated_at=?
               WHERE campaign_id=? AND id=?""",
            (
                status,
                when.isoformat(),
                revision,
                self.e._now(),
                campaign_id,
                proposal_id,
            ),
        )
        return {
            "proposal": self._proposal_db(db, campaign_id, proposal_id),
            "result": result,
        }

    def record_violation_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        violation_id: str,
        *,
        treaty_id: str,
        violator_faction_id: str,
        harmed_faction_id: str,
        violation_type: str,
        severity: float,
        revision: int,
        when: datetime,
        obligation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        emit: Callable[..., None] | None = None,
    ) -> dict[str, Any]:
        violation_id = self._id(violation_id, "violation_id")
        treaty = self._treaty_db(db, campaign_id, treaty_id)
        parties = {str(item["faction_id"]) for item in treaty["parties"]}
        if (
            violator_faction_id not in parties
            or harmed_faction_id not in parties
            or violator_faction_id == harmed_faction_id
        ):
            raise ValueError("violation factions must be distinct treaty parties")
        if obligation_id:
            obligation = db.execute(
                """SELECT * FROM politics_obligations WHERE campaign_id=? AND id=?
                   AND treaty_id=?""",
                (campaign_id, obligation_id, treaty_id),
            ).fetchone()
            if not obligation:
                raise KeyError(f"unknown treaty obligation: {obligation_id}")
            db.execute(
                """UPDATE politics_obligations SET status='violated',revision=?,updated_at=?
                   WHERE campaign_id=? AND id=?""",
                (revision, self.e._now(), campaign_id, obligation_id),
            )
            if obligation["clause_id"]:
                db.execute(
                    """UPDATE politics_treaty_clauses SET status='breached'
                       WHERE campaign_id=? AND treaty_id=? AND id=?""",
                    (campaign_id, treaty_id, obligation["clause_id"]),
                )
        meta = dict(metadata or {})
        _json_guard(meta, "metadata")
        when = _utc(when)
        severity = self._number(severity, "severity", maximum=1.0)
        db.execute(
            """INSERT INTO politics_treaty_violations(
                   campaign_id,id,treaty_id,obligation_id,violator_faction_id,
                   harmed_faction_id,violation_type,severity,world_time,revision,metadata_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                campaign_id,
                violation_id,
                treaty_id,
                obligation_id,
                violator_faction_id,
                harmed_faction_id,
                self._id(violation_type, "violation_type"),
                severity,
                when.isoformat(),
                int(revision),
                self.e._dumps(meta),
            ),
        )
        grievance_id = f"grievance:{violation_id}"
        self.add_grievance_db(
            db,
            campaign_id,
            grievance_id,
            aggrieved_faction_id=harmed_faction_id,
            against_faction_id=violator_faction_id,
            grievance_type="treaty_violation",
            severity=severity,
            source_kind="treaty_violation",
            source_id=violation_id,
            revision=revision,
            when=when,
            visibility=str(treaty["visibility"]),
        )
        self._emit(
            emit,
            "politics_treaty_violated",
            f"{violator_faction_id} violated treaty {treaty_id}",
            {
                "violation_id": violation_id,
                "treaty_id": treaty_id,
                "grievance_id": grievance_id,
            },
            None,
            when,
            sensitivity=(
                "PUBLIC" if str(treaty["visibility"]) == "public" else "PRIVATE"
            ),
            scope_type="WORLD" if str(treaty["visibility"]) == "public" else "GM",
        )
        return {
            "violation_id": violation_id,
            "treaty_id": treaty_id,
            "obligation_id": obligation_id,
            "grievance_id": grievance_id,
        }

    def _settle_commitment_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        commitment_id: str,
        amount: float | None,
        *,
        consume: bool,
    ) -> dict[str, Any]:
        row = db.execute(
            "SELECT * FROM politics_commitments WHERE campaign_id=? AND id=?",
            (campaign_id, commitment_id),
        ).fetchone()
        if not row:
            raise KeyError(f"unknown commitment: {commitment_id}")
        if row["status"] != "reserved":
            raise ValueError("commitment is not active")
        remaining = (
            float(row["amount"]) - float(row["consumed"]) - float(row["released"])
        )
        qty = (
            remaining
            if amount is None
            else self._number(amount, "amount", positive=True)
        )
        if qty > remaining + 1e-9:
            raise ValueError("commitment settlement exceeds remaining amount")
        consumed = float(row["consumed"]) + (qty if consume else 0.0)
        released = float(row["released"]) + (0.0 if consume else qty)
        left = float(row["amount"]) - consumed - released
        status = (
            "reserved" if left > 1e-9 else ("consumed" if consumed > 0 else "released")
        )
        metadata = self.e._loads(row["metadata_json"] or "{}")
        if not consume and metadata.get("escrowed"):
            self._escrow_adjust_db(
                db,
                campaign_id,
                str(row["resource_kind"]),
                str(row["resource_key"]),
                str(row["actor_kind"]),
                str(row["actor_id"]),
                qty,
            )
        db.execute(
            """UPDATE politics_commitments SET consumed=?,released=?,status=?,updated_at=?
               WHERE campaign_id=? AND id=?""",
            (consumed, released, status, self.e._now(), campaign_id, commitment_id),
        )
        return self.get_commitment_db(db, campaign_id, commitment_id)

    def consume_commitment_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        commitment_id: str,
        amount: float | None = None,
    ) -> dict[str, Any]:
        return self._settle_commitment_db(
            db, campaign_id, commitment_id, amount, consume=True
        )

    def release_commitment_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        commitment_id: str,
        amount: float | None = None,
    ) -> dict[str, Any]:
        return self._settle_commitment_db(
            db, campaign_id, commitment_id, amount, consume=False
        )

    def _release_purpose_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        purpose_kind: str,
        purpose_id: str,
    ) -> int:
        rows = db.execute(
            """SELECT id FROM politics_commitments WHERE campaign_id=? AND purpose_kind=?
               AND purpose_id=? AND status='reserved' ORDER BY id""",
            (campaign_id, purpose_kind, purpose_id),
        ).fetchall()
        for row in rows:
            self.release_commitment_db(db, campaign_id, str(row["id"]))
        return len(rows)

    def _consume_project_resources_db(
        self, db: sqlite3.Connection, campaign_id: str, project_id: str
    ) -> None:
        rows = db.execute(
            """SELECT id,resource_kind FROM politics_commitments WHERE campaign_id=?
               AND purpose_kind='project' AND purpose_id=? AND status='reserved' ORDER BY id""",
            (campaign_id, project_id),
        ).fetchall()
        for row in rows:
            if row["resource_kind"] in {"currency", "inventory"}:
                self.consume_commitment_db(db, campaign_id, str(row["id"]))
            else:
                self.release_commitment_db(db, campaign_id, str(row["id"]))

    def create_project_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        project_id: str,
        *,
        owner_faction_id: str,
        location_id: str,
        project_kind: str,
        name: str,
        work_required: float,
        requirements: Iterable[dict[str, Any]],
        revision: int,
        when: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        project_id = self._id(project_id, "project_id")
        owner_faction_id = self._id(owner_faction_id, "owner_faction_id")
        location_id = self._id(location_id, "location_id")
        project_kind = self._id(project_kind, "project_kind")
        work_required = self._number(work_required, "work_required", positive=True)
        when = _utc(when)
        self._faction_db(db, campaign_id, owner_faction_id)
        self._location_db(db, campaign_id, location_id)
        reqs = [dict(item) for item in requirements]
        if len(reqs) > 64:
            raise ValueError("too many project requirements")
        _json_guard(reqs, "requirements")
        meta = dict(metadata or {})
        _json_guard(meta, "metadata")
        db.execute(
            """INSERT INTO politics_projects(
                   campaign_id,id,owner_faction_id,location_id,project_kind,name,status,
                   progress,work_required,requirements_json,revision,metadata_json,updated_at)
               VALUES(?,?,?,?,?,?,'planned',0,?,?,?,?,?)""",
            (
                campaign_id,
                project_id,
                owner_faction_id,
                location_id,
                project_kind,
                str(name)[:200],
                work_required,
                self.e._dumps(reqs),
                int(revision),
                self.e._dumps(meta),
                self.e._now(),
            ),
        )
        for index, req in enumerate(reqs):
            kind = str(req.get("resource_kind") or req.get("kind") or "")
            key = str(req.get("resource_key") or req.get("key") or "")
            actor_kind = str(req.get("actor_kind") or "faction")
            actor_id = str(req.get("actor_id") or owner_faction_id)
            req_location = req.get(
                "location_id", location_id if kind in {"labor", "manpower"} else None
            )
            self.reserve_db(
                db,
                campaign_id,
                f"project:{project_id}:{index}",
                actor_kind=actor_kind,
                actor_id=actor_id,
                resource_kind=kind,
                resource_key=key,
                amount=req.get("amount"),
                purpose_kind="project",
                purpose_id=project_id,
                revision=revision,
                when=when,
                location_id=str(req_location) if req_location else None,
                metadata={"requirement_index": index},
            )
        return self.get_project_db(db, campaign_id, project_id)

    # ------------------------------------------------------------------
    # Epistemic strategy, claims, grievances, and territory
    # ------------------------------------------------------------------

    def belief_view_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        faction_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return only facts explicitly held by this faction principal."""
        self._faction_db(db, campaign_id, faction_id)
        rows = db.execute(
            """SELECT b.fact_id,b.belief_value_json,b.confidence,b.status,
                      f.subject_key,f.predicate,f.object_type,f.object_value_json
               FROM we4_beliefs b
               JOIN we4_facts f ON f.campaign_id=b.campaign_id AND f.fact_id=b.fact_id
               WHERE b.campaign_id=? AND b.believer_key=?
                 AND b.status IN ('believes','doubts','rejects')
                 AND f.status IN ('active','disputed')
               ORDER BY b.confidence DESC,b.fact_id LIMIT ?""",
            (campaign_id, f"faction:{faction_id}", max(1, min(int(limit), 200))),
        ).fetchall()
        return [
            {
                "fact_id": str(row["fact_id"]),
                "subject_key": str(row["subject_key"]),
                "predicate": str(row["predicate"]),
                "belief_value": self.e._loads(row["belief_value_json"]),
                "object_type": str(row["object_type"]),
                "object_value": self.e._loads(row["object_value_json"]),
                "confidence": float(row["confidence"]),
                "status": str(row["status"]),
            }
            for row in rows
        ]

    def strategy_view_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        faction_id: str,
        *,
        limit: int = 50,
    ) -> dict[str, Any]:
        faction = self._faction_db(db, campaign_id, faction_id)
        claims = [
            self._row_json(self.e, row, "metadata")
            for row in db.execute(
                """SELECT * FROM politics_claims WHERE campaign_id=?
                   AND claimant_faction_id=? AND status='active'
                   ORDER BY strength DESC,id LIMIT ?""",
                (campaign_id, faction_id, max(1, min(int(limit), 100))),
            ).fetchall()
        ]
        grievances = [
            self._row_json(self.e, row, "metadata")
            for row in db.execute(
                """SELECT * FROM politics_grievances WHERE campaign_id=?
                   AND aggrieved_faction_id=? AND status='active'
                   ORDER BY severity DESC,id LIMIT ?""",
                (campaign_id, faction_id, max(1, min(int(limit), 100))),
            ).fetchall()
        ]
        return {
            "campaign_id": campaign_id,
            "faction_id": faction_id,
            "region": str(faction["region"]),
            "beliefs": self.belief_view_db(db, campaign_id, faction_id, limit=limit),
            "claims": claims,
            "grievances": grievances,
        }

    def add_claim_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        claim_id: str,
        *,
        claimant_faction_id: str,
        target_kind: str,
        target_id: str,
        claim_type: str,
        strength: float,
        revision: int,
        when: datetime,
        source_fact_id: str | None = None,
        visibility: str = "public",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        claim_id = self._id(claim_id, "claim_id")
        claimant_faction_id = self._id(claimant_faction_id, "claimant_faction_id")
        target_id = self._id(target_id, "target_id")
        target_kind = str(target_kind).lower()
        if target_kind not in {"faction", "location", "resource", "treaty"}:
            raise ValueError("invalid claim target_kind")
        self._faction_db(db, campaign_id, claimant_faction_id)
        if target_kind == "faction":
            self._faction_db(db, campaign_id, target_id)
        elif target_kind == "location":
            self._location_db(db, campaign_id, target_id)
        if (
            source_fact_id
            and not db.execute(
                """SELECT 1 FROM we4_beliefs WHERE campaign_id=? AND believer_key=?
               AND fact_id=? AND status IN ('believes','doubts')""",
                (campaign_id, f"faction:{claimant_faction_id}", source_fact_id),
            ).fetchone()
        ):
            raise ValueError("claim source fact is not known to claimant")
        visibility = str(visibility).lower()
        if visibility not in {"public", "private"}:
            raise ValueError("invalid visibility")
        meta = dict(metadata or {})
        _json_guard(meta, "metadata")
        db.execute(
            """INSERT INTO politics_claims(
                   campaign_id,id,claimant_faction_id,target_kind,target_id,claim_type,
                   strength,status,source_fact_id,visibility,created_world_time,revision,
                   metadata_json,updated_at)
               VALUES(?,?,?,?,?,?,?,'active',?,?,?,?,?,?)""",
            (
                campaign_id,
                claim_id,
                claimant_faction_id,
                target_kind,
                target_id,
                self._id(claim_type, "claim_type"),
                self._number(strength, "strength", maximum=1.0),
                source_fact_id,
                visibility,
                _utc(when).isoformat(),
                int(revision),
                self.e._dumps(meta),
                self.e._now(),
            ),
        )
        row = db.execute(
            "SELECT * FROM politics_claims WHERE campaign_id=? AND id=?",
            (campaign_id, claim_id),
        ).fetchone()
        return self._row_json(self.e, row, "metadata")

    def add_grievance_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        grievance_id: str,
        *,
        aggrieved_faction_id: str,
        against_faction_id: str,
        grievance_type: str,
        severity: float,
        source_kind: str,
        source_id: str,
        revision: int,
        when: datetime,
        visibility: str = "public",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        grievance_id = self._id(grievance_id, "grievance_id")
        aggrieved_faction_id = self._id(aggrieved_faction_id, "aggrieved_faction_id")
        against_faction_id = self._id(against_faction_id, "against_faction_id")
        if aggrieved_faction_id == against_faction_id:
            raise ValueError("grievance factions must differ")
        self._faction_db(db, campaign_id, aggrieved_faction_id)
        self._faction_db(db, campaign_id, against_faction_id)
        visibility = str(visibility).lower()
        if visibility not in {"public", "private"}:
            raise ValueError("invalid visibility")
        meta = dict(metadata or {})
        _json_guard(meta, "metadata")
        db.execute(
            """INSERT INTO politics_grievances(
                   campaign_id,id,aggrieved_faction_id,against_faction_id,grievance_type,
                   severity,status,source_kind,source_id,visibility,created_world_time,
                   revision,metadata_json,updated_at)
               VALUES(?,?,?,?,?,?,'active',?,?,?,?,?,?,?)""",
            (
                campaign_id,
                grievance_id,
                aggrieved_faction_id,
                against_faction_id,
                self._id(grievance_type, "grievance_type"),
                self._number(severity, "severity", maximum=1.0),
                self._id(source_kind, "source_kind"),
                self._id(source_id, "source_id"),
                visibility,
                _utc(when).isoformat(),
                int(revision),
                self.e._dumps(meta),
                self.e._now(),
            ),
        )
        row = db.execute(
            "SELECT * FROM politics_grievances WHERE campaign_id=? AND id=?",
            (campaign_id, grievance_id),
        ).fetchone()
        return self._row_json(self.e, row, "metadata")

    def set_control_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        location_id: str,
        controller_faction_id: str,
        *,
        control: float = 1.0,
        occupation_state: str = "controlled",
        war_id: str | None = None,
        reason: str,
        revision: int,
        when: datetime,
        metadata: dict[str, Any] | None = None,
        emit: Callable[..., None] | None = None,
    ) -> dict[str, Any]:
        location_id = self._id(location_id, "location_id")
        controller_faction_id = self._id(controller_faction_id, "controller_faction_id")
        self._location_db(db, campaign_id, location_id)
        self._faction_db(db, campaign_id, controller_faction_id)
        occupation_state = str(occupation_state).lower()
        if occupation_state not in {"controlled", "contested", "occupied"}:
            raise ValueError("invalid occupation_state")
        meta = dict(metadata or {})
        _json_guard(meta, "metadata")
        prior = db.execute(
            """SELECT controller_faction_id FROM politics_territorial_control
               WHERE campaign_id=? AND location_id=?""",
            (campaign_id, location_id),
        ).fetchone()
        when = _utc(when)
        db.execute(
            """INSERT INTO politics_territorial_control(
                   campaign_id,location_id,controller_faction_id,control,occupation_state,
                   war_id,since_world_time,revision,metadata_json,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(campaign_id,location_id) DO UPDATE SET
                   controller_faction_id=excluded.controller_faction_id,
                   control=excluded.control,occupation_state=excluded.occupation_state,
                   war_id=excluded.war_id,since_world_time=excluded.since_world_time,
                   revision=excluded.revision,metadata_json=excluded.metadata_json,
                   updated_at=excluded.updated_at""",
            (
                campaign_id,
                location_id,
                controller_faction_id,
                self._number(control, "control", maximum=1.0),
                occupation_state,
                war_id,
                when.isoformat(),
                int(revision),
                self.e._dumps(meta),
                self.e._now(),
            ),
        )
        old = str(prior["controller_faction_id"]) if prior else None
        db.execute(
            """INSERT INTO politics_control_events(
                   campaign_id,location_id,old_controller_faction_id,new_controller_faction_id,
                   war_id,reason,world_time,revision) VALUES(?,?,?,?,?,?,?,?)""",
            (
                campaign_id,
                location_id,
                old,
                controller_faction_id,
                war_id,
                str(reason)[:500],
                when.isoformat(),
                revision,
            ),
        )
        self._emit(
            emit,
            "politics_control_changed",
            f"Control of {location_id} changed to {controller_faction_id}",
            {
                "location_id": location_id,
                "old_controller": old,
                "new_controller": controller_faction_id,
                "war_id": war_id,
            },
            location_id,
            when,
        )
        row = db.execute(
            "SELECT * FROM politics_territorial_control WHERE campaign_id=? AND location_id=?",
            (campaign_id, location_id),
        ).fetchone()
        return self._row_json(self.e, row, "metadata")

    def get_project_db(
        self, db: sqlite3.Connection, campaign_id: str, project_id: str
    ) -> dict[str, Any]:
        row = db.execute(
            "SELECT * FROM politics_projects WHERE campaign_id=? AND id=?",
            (campaign_id, project_id),
        ).fetchone()
        if not row:
            raise KeyError(f"unknown project: {project_id}")
        out = self._row_json(self.e, row, "requirements", "metadata")
        out["commitments"] = [
            self.get_commitment_db(db, campaign_id, str(item["id"]))
            for item in db.execute(
                """SELECT id FROM politics_commitments WHERE campaign_id=?
                   AND purpose_kind='project' AND purpose_id=? ORDER BY id""",
                (campaign_id, project_id),
            ).fetchall()
        ]
        return out

    def start_project_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        project_id: str,
        *,
        revision: int,
        when: datetime,
    ) -> dict[str, Any]:
        row = self.get_project_db(db, campaign_id, project_id)
        if row["status"] != "planned":
            raise ValueError("only planned projects may start")
        when = _utc(when)
        db.execute(
            """UPDATE politics_projects SET status='active',started_world_time=?,
               last_step_world_time=?,revision=?,updated_at=? WHERE campaign_id=? AND id=?""",
            (
                when.isoformat(),
                when.isoformat(),
                revision,
                self.e._now(),
                campaign_id,
                project_id,
            ),
        )
        return self.get_project_db(db, campaign_id, project_id)

    def cancel_project_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        project_id: str,
        *,
        revision: int,
    ) -> dict[str, Any]:
        row = self.get_project_db(db, campaign_id, project_id)
        if row["status"] not in {"planned", "active"}:
            raise ValueError("project cannot be cancelled")
        self._release_purpose_db(db, campaign_id, "project", project_id)
        db.execute(
            """UPDATE politics_projects SET status='cancelled',revision=?,updated_at=?
               WHERE campaign_id=? AND id=?""",
            (revision, self.e._now(), campaign_id, project_id),
        )
        return self.get_project_db(db, campaign_id, project_id)

    # ------------------------------------------------------------------
    # Forces, supply, raids, wars, occupation, and peace
    # ------------------------------------------------------------------

    def _force_db(
        self, db: sqlite3.Connection, campaign_id: str, force_id: str
    ) -> dict[str, Any]:
        row = db.execute(
            "SELECT * FROM politics_forces WHERE campaign_id=? AND id=?",
            (campaign_id, force_id),
        ).fetchone()
        if not row:
            raise KeyError(f"unknown force: {force_id}")
        return self._row_json(self.e, row, "metadata")

    def mobilize_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        force_id: str,
        *,
        faction_id: str,
        name: str,
        location_id: str,
        source_cohort_id: str,
        manpower: float,
        revision: int,
        when: datetime,
        force_type: str = "levy",
        readiness: float = 0.5,
        morale: float = 0.5,
        currency_key: str | None = None,
        currency_cost: float = 0.0,
        supply_item_id: str | None = None,
        supply_qty: float = 0.0,
        metadata: dict[str, Any] | None = None,
        emit: Callable[..., None] | None = None,
    ) -> dict[str, Any]:
        force_id = self._id(force_id, "force_id")
        faction_id = self._id(faction_id, "faction_id")
        location_id = self._id(location_id, "location_id")
        source_cohort_id = self._id(source_cohort_id, "source_cohort_id")
        manpower = self._number(manpower, "manpower", positive=True)
        self._faction_db(db, campaign_id, faction_id)
        self._location_db(db, campaign_id, location_id)
        cohort = db.execute(
            """SELECT * FROM population_cohorts WHERE campaign_id=? AND id=?
               AND location_id=?""",
            (campaign_id, source_cohort_id, location_id),
        ).fetchone()
        if not cohort:
            raise KeyError(
                f"unknown cohort at mobilization location: {source_cohort_id}"
            )
        if cohort["faction_id"] is not None and str(cohort["faction_id"]) != faction_id:
            raise ValueError("faction may not mobilize another faction's cohort")
        when = _utc(when)
        manpower_commitment_id = f"force:{force_id}:manpower"
        self.reserve_db(
            db,
            campaign_id,
            manpower_commitment_id,
            actor_kind="faction",
            actor_id=faction_id,
            resource_kind="manpower",
            resource_key=source_cohort_id,
            amount=manpower,
            purpose_kind="force",
            purpose_id=force_id,
            revision=revision,
            when=when,
            location_id=location_id,
        )
        if self._number(currency_cost, "currency_cost") > 0:
            if not currency_key:
                raise ValueError("currency_cost requires currency_key")
            commitment = self.reserve_db(
                db,
                campaign_id,
                f"force:{force_id}:currency",
                actor_kind="faction",
                actor_id=faction_id,
                resource_kind="currency",
                resource_key=self._id(currency_key, "currency_key"),
                amount=currency_cost,
                purpose_kind="force",
                purpose_id=force_id,
                revision=revision,
                when=when,
            )
            self.consume_commitment_db(db, campaign_id, str(commitment["id"]))
        supply_qty = self._number(supply_qty, "supply_qty")
        if supply_qty > 0:
            if not supply_item_id:
                raise ValueError("supply_qty requires supply_item_id")
            commitment = self.reserve_db(
                db,
                campaign_id,
                f"force:{force_id}:supply",
                actor_kind="faction",
                actor_id=faction_id,
                resource_kind="inventory",
                resource_key=self._id(supply_item_id, "supply_item_id"),
                amount=supply_qty,
                purpose_kind="force",
                purpose_id=force_id,
                revision=revision,
                when=when,
            )
            self.consume_commitment_db(db, campaign_id, str(commitment["id"]))
        meta = dict(metadata or {})
        _json_guard(meta, "metadata")
        db.execute(
            """INSERT INTO politics_forces(
                   campaign_id,id,faction_id,name,force_type,location_id,source_cohort_id,
                   manpower,readiness,morale,status,manpower_commitment_id,supply_item_id,
                   supply_remaining,mobilized_world_time,revision,metadata_json,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,'mobilized',?,?,?,?,?,?,?)""",
            (
                campaign_id,
                force_id,
                faction_id,
                str(name)[:200],
                self._id(force_type, "force_type"),
                location_id,
                source_cohort_id,
                manpower,
                self._number(readiness, "readiness", maximum=1.0),
                self._number(morale, "morale", maximum=1.0),
                manpower_commitment_id,
                supply_item_id,
                supply_qty,
                when.isoformat(),
                int(revision),
                self.e._dumps(meta),
                self.e._now(),
            ),
        )
        self._emit(
            emit,
            "politics_force_mobilized",
            f"{faction_id} mobilized {name}",
            {"force_id": force_id, "faction_id": faction_id, "manpower": manpower},
            location_id,
            when,
            sensitivity="PRIVATE",
            scope_type="ENTITY",
            principal_kind="faction",
            principal_id=faction_id,
        )
        return self._force_db(db, campaign_id, force_id)

    def apply_force_losses_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        force_id: str,
        *,
        count: float,
        loss_kind: str,
        cause_kind: str,
        cause_id: str,
        revision: int,
        when: datetime,
        emit: Callable[..., None] | None = None,
    ) -> dict[str, Any]:
        force = self._force_db(db, campaign_id, force_id)
        if force["status"] in {"demobilized", "destroyed"}:
            raise ValueError("inactive force cannot take losses")
        count = self._number(count, "count", positive=True)
        count = min(count, float(force["manpower"]))
        loss_kind = str(loss_kind).lower()
        if loss_kind not in {"casualty", "desertion", "capture"}:
            raise ValueError("invalid loss_kind")
        cohort = db.execute(
            "SELECT count FROM population_cohorts WHERE campaign_id=? AND id=?",
            (campaign_id, force["source_cohort_id"]),
        ).fetchone()
        if not cohort or float(cohort["count"]) + 1e-9 < count:
            raise ValueError("source population cannot absorb force losses")
        # Mobilized people remain part of the resident cohort until they die,
        # desert, or are captured. A loss therefore reduces both the force and
        # canonical population while settling the corresponding reservation.
        db.execute(
            """UPDATE population_cohorts SET count=MAX(0,count-?),updated_at=?
               WHERE campaign_id=? AND id=?""",
            (count, self.e._now(), campaign_id, force["source_cohort_id"]),
        )
        self.consume_commitment_db(
            db, campaign_id, str(force["manpower_commitment_id"]), count
        )
        remaining = max(0.0, float(force["manpower"]) - count)
        status = "destroyed" if remaining <= 1e-9 else str(force["status"])
        db.execute(
            """UPDATE politics_forces SET manpower=?,status=?,revision=?,updated_at=?
               WHERE campaign_id=? AND id=?""",
            (remaining, status, revision, self.e._now(), campaign_id, force_id),
        )
        when = _utc(when)
        db.execute(
            """INSERT INTO politics_force_losses(
                   campaign_id,force_id,loss_kind,count,cause_kind,cause_id,world_time,revision)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                campaign_id,
                force_id,
                loss_kind,
                count,
                self._id(cause_kind, "cause_kind"),
                self._id(cause_id, "cause_id"),
                when.isoformat(),
                revision,
            ),
        )
        self._emit(
            emit,
            "politics_force_losses",
            f"{force_id} suffered {count:g} {loss_kind} loss(es)",
            {"force_id": force_id, "count": count, "loss_kind": loss_kind},
            str(force["location_id"]),
            when,
            sensitivity="PRIVATE",
            scope_type="ENTITY",
            principal_kind="faction",
            principal_id=str(force["faction_id"]),
        )
        return self._force_db(db, campaign_id, force_id)

    def demobilize_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        force_id: str,
        *,
        revision: int,
        when: datetime,
    ) -> dict[str, Any]:
        force = self._force_db(db, campaign_id, force_id)
        if force["status"] in {"demobilized", "destroyed"}:
            raise ValueError("force is already inactive")
        self.release_commitment_db(
            db, campaign_id, str(force["manpower_commitment_id"])
        )
        if force["supply_item_id"] and float(force["supply_remaining"]) > 0:
            self._escrow_adjust_db(
                db,
                campaign_id,
                "inventory",
                str(force["supply_item_id"]),
                "faction",
                str(force["faction_id"]),
                float(force["supply_remaining"]),
            )
        db.execute(
            """UPDATE politics_forces SET status='demobilized',supply_remaining=0,
               revision=?,updated_at=? WHERE campaign_id=? AND id=?""",
            (revision, self.e._now(), campaign_id, force_id),
        )
        return self._force_db(db, campaign_id, force_id)

    def deploy_force_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        force_id: str,
        *,
        destination_location_id: str,
        route_id: str,
        supply_cost: float,
        revision: int,
        when: datetime,
    ) -> dict[str, Any]:
        force = self._force_db(db, campaign_id, force_id)
        if force["status"] not in {"mobilized", "deployed"}:
            raise ValueError("only an active force may deploy")
        destination_location_id = self._id(
            destination_location_id, "destination_location_id"
        )
        self._location_db(db, campaign_id, destination_location_id)
        route = self._route_db(
            db,
            campaign_id,
            self._id(route_id, "route_id"),
            str(force["location_id"]),
            destination_location_id,
        )
        if float(route["capacity_qty_per_day"]) + 1e-9 < float(force["manpower"]):
            raise ValueError("route capacity is insufficient for the force")
        cost = self._number(supply_cost, "supply_cost")
        if float(force["supply_remaining"]) + 1e-9 < cost:
            raise ValueError("force has insufficient carried supply")
        when = _utc(when)
        deployment_key = (
            "deployment:"
            + _digest(
                {
                    "campaign_id": campaign_id,
                    "force_id": force_id,
                    "origin": force["location_id"],
                    "destination": destination_location_id,
                    "route_id": route_id,
                    "world_time": when.isoformat(),
                }
            )[:24]
        )
        next_boundary = datetime.combine(
            when.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
        )
        self.reserve_db(
            db,
            campaign_id,
            deployment_key,
            actor_kind="faction",
            actor_id=str(force["faction_id"]),
            resource_kind="route_capacity",
            resource_key=str(route["id"]),
            amount=max(1.0, float(force["manpower"])),
            purpose_kind="deployment",
            purpose_id=force_id,
            revision=revision,
            when=when,
            expires_world_time=next_boundary.isoformat(),
        )
        db.execute(
            """UPDATE politics_forces SET location_id=?,status='deployed',
               supply_remaining=MAX(0,supply_remaining-?),revision=?,updated_at=?
               WHERE campaign_id=? AND id=?""",
            (
                destination_location_id,
                cost,
                revision,
                self.e._now(),
                campaign_id,
                force_id,
            ),
        )
        return self._force_db(db, campaign_id, force_id)

    def _route_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        route_id: str,
        origin: str,
        target: str,
    ) -> sqlite3.Row:
        row = db.execute(
            """SELECT * FROM economy_routes WHERE campaign_id=? AND id=? AND active=1
               AND ((from_location_id=? AND to_location_id=?)
                    OR (from_location_id=? AND to_location_id=?))""",
            (campaign_id, route_id, origin, target, target, origin),
        ).fetchone()
        if not row:
            raise ValueError(
                "raid requires an active route connecting origin and target"
            )
        return row

    def plan_raid_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        raid_id: str,
        *,
        attacker_faction_id: str,
        target_faction_id: str,
        force_id: str,
        target_location_id: str,
        route_id: str,
        supply_item_id: str,
        supply_qty: float,
        revision: int,
        when: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raid_id = self._id(raid_id, "raid_id")
        force = self._force_db(db, campaign_id, force_id)
        if str(force["faction_id"]) != attacker_faction_id:
            raise ValueError("raid force is not controlled by attacker")
        if force["status"] not in {"mobilized", "deployed"}:
            raise ValueError("raid requires an active force")
        self._faction_db(db, campaign_id, target_faction_id)
        target_location_id = self._id(target_location_id, "target_location_id")
        self._location_db(db, campaign_id, target_location_id)
        route = self._route_db(
            db,
            campaign_id,
            self._id(route_id, "route_id"),
            str(force["location_id"]),
            target_location_id,
        )
        when = _utc(when)
        supply = self.reserve_db(
            db,
            campaign_id,
            f"raid:{raid_id}:supply",
            actor_kind="faction",
            actor_id=attacker_faction_id,
            resource_kind="inventory",
            resource_key=self._id(supply_item_id, "supply_item_id"),
            amount=supply_qty,
            purpose_kind="raid",
            purpose_id=raid_id,
            revision=revision,
            when=when,
            location_id=str(force["location_id"]),
        )
        self.reserve_db(
            db,
            campaign_id,
            f"raid:{raid_id}:route",
            actor_kind="faction",
            actor_id=attacker_faction_id,
            resource_kind="route_capacity",
            resource_key=str(route["id"]),
            amount=max(1.0, float(force["manpower"])),
            purpose_kind="raid",
            purpose_id=raid_id,
            revision=revision,
            when=when,
        )
        meta = dict(metadata or {})
        _json_guard(meta, "metadata")
        db.execute(
            """INSERT INTO politics_raids(
                   campaign_id,id,attacker_faction_id,target_faction_id,force_id,
                   origin_location_id,target_location_id,route_id,supply_commitment_id,
                   status,outcome_json,planned_world_time,revision,metadata_json,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,'planned','{}',?,?,?,?)""",
            (
                campaign_id,
                raid_id,
                attacker_faction_id,
                target_faction_id,
                force_id,
                force["location_id"],
                target_location_id,
                route_id,
                supply["id"],
                when.isoformat(),
                revision,
                self.e._dumps(meta),
                self.e._now(),
            ),
        )
        return self._raid_db(db, campaign_id, raid_id)

    def cancel_raid_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        raid_id: str,
        *,
        revision: int,
    ) -> dict[str, Any]:
        raid = self._raid_db(db, campaign_id, raid_id)
        if raid["status"] != "planned":
            raise ValueError("raid is not pending")
        self._release_purpose_db(db, campaign_id, "raid", raid_id)
        db.execute(
            """UPDATE politics_raids SET status='cancelled',revision=?,updated_at=?
               WHERE campaign_id=? AND id=?""",
            (revision, self.e._now(), campaign_id, raid_id),
        )
        return self._raid_db(db, campaign_id, raid_id)

    def _raid_db(
        self, db: sqlite3.Connection, campaign_id: str, raid_id: str
    ) -> dict[str, Any]:
        row = db.execute(
            "SELECT * FROM politics_raids WHERE campaign_id=? AND id=?",
            (campaign_id, raid_id),
        ).fetchone()
        if not row:
            raise KeyError(f"unknown raid: {raid_id}")
        return self._row_json(self.e, row, "outcome", "metadata")

    def resolve_raid_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        raid_id: str,
        *,
        revision: int,
        when: datetime,
        success: bool,
        attacker_losses: float = 0.0,
        emit: Callable[..., None] | None = None,
    ) -> dict[str, Any]:
        raid = self._raid_db(db, campaign_id, raid_id)
        if raid["status"] != "planned":
            raise ValueError("raid is not pending")
        self.consume_commitment_db(db, campaign_id, str(raid["supply_commitment_id"]))
        route_commitment = db.execute(
            """SELECT id FROM politics_commitments WHERE campaign_id=?
               AND purpose_kind='raid' AND purpose_id=? AND resource_kind='route_capacity'
               AND status='reserved'""",
            (campaign_id, raid_id),
        ).fetchone()
        if route_commitment:
            self.release_commitment_db(db, campaign_id, str(route_commitment["id"]))
        losses = self._number(attacker_losses, "attacker_losses")
        if losses > 0:
            self.apply_force_losses_db(
                db,
                campaign_id,
                str(raid["force_id"]),
                count=losses,
                loss_kind="casualty",
                cause_kind="raid",
                cause_id=raid_id,
                revision=revision,
                when=when,
                emit=emit,
            )
        outcome = {"success": bool(success), "attacker_losses": losses}
        db.execute(
            """UPDATE politics_raids SET status='resolved',outcome_json=?,
               resolved_world_time=?,revision=?,updated_at=? WHERE campaign_id=? AND id=?""",
            (
                self.e._dumps(outcome),
                _utc(when).isoformat(),
                revision,
                self.e._now(),
                campaign_id,
                raid_id,
            ),
        )
        return self._raid_db(db, campaign_id, raid_id)

    def declare_war_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        war_id: str,
        *,
        attacker_faction_id: str,
        defender_faction_id: str,
        casus_belli_kind: str,
        casus_belli_id: str,
        goals: Iterable[dict[str, Any] | str],
        revision: int,
        when: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        war_id = self._id(war_id, "war_id")
        attacker_faction_id = self._id(attacker_faction_id, "attacker_faction_id")
        defender_faction_id = self._id(defender_faction_id, "defender_faction_id")
        if attacker_faction_id == defender_faction_id:
            raise ValueError("war factions must differ")
        self._faction_db(db, campaign_id, attacker_faction_id)
        self._faction_db(db, campaign_id, defender_faction_id)
        casus_belli_kind = str(casus_belli_kind).lower()
        casus_belli_id = self._id(casus_belli_id, "casus_belli_id")
        if casus_belli_kind == "claim":
            lawful = db.execute(
                """SELECT 1 FROM politics_claims WHERE campaign_id=? AND id=?
                   AND claimant_faction_id=? AND status='active'""",
                (campaign_id, casus_belli_id, attacker_faction_id),
            ).fetchone()
        elif casus_belli_kind == "grievance":
            lawful = db.execute(
                """SELECT 1 FROM politics_grievances WHERE campaign_id=? AND id=?
                   AND aggrieved_faction_id=? AND against_faction_id=? AND status='active'""",
                (campaign_id, casus_belli_id, attacker_faction_id, defender_faction_id),
            ).fetchone()
        elif casus_belli_kind == "treaty_violation":
            lawful = db.execute(
                """SELECT 1 FROM politics_treaty_violations WHERE campaign_id=? AND id=?
                   AND harmed_faction_id=? AND violator_faction_id=?""",
                (campaign_id, casus_belli_id, attacker_faction_id, defender_faction_id),
            ).fetchone()
        else:
            raise ValueError("invalid casus_belli_kind")
        if not lawful:
            raise ValueError("casus belli is not valid for these factions")
        goal_list = list(goals)
        if not goal_list or len(goal_list) > 32:
            raise ValueError("war requires 1-32 goals")
        _json_guard(goal_list, "goals")
        meta = dict(metadata or {})
        _json_guard(meta, "metadata")
        when = _utc(when)
        db.execute(
            """INSERT INTO politics_wars(
                   campaign_id,id,attacker_faction_id,defender_faction_id,
                   casus_belli_kind,casus_belli_id,status,goals_json,
                   started_world_time,revision,metadata_json,updated_at)
               VALUES(?,?,?,?,?,?,'active',?,?,?,?,?)""",
            (
                campaign_id,
                war_id,
                attacker_faction_id,
                defender_faction_id,
                casus_belli_kind,
                casus_belli_id,
                self.e._dumps(goal_list),
                when.isoformat(),
                revision,
                self.e._dumps(meta),
                self.e._now(),
            ),
        )
        for faction_id, side in (
            (attacker_faction_id, "attacker"),
            (defender_faction_id, "defender"),
        ):
            db.execute(
                """INSERT INTO politics_war_participants(
                       campaign_id,war_id,faction_id,side,joined_world_time,status)
                   VALUES(?,?,?,?,?,'active')""",
                (campaign_id, war_id, faction_id, side, when.isoformat()),
            )
        return self._war_db(db, campaign_id, war_id)

    def _war_db(
        self, db: sqlite3.Connection, campaign_id: str, war_id: str
    ) -> dict[str, Any]:
        row = db.execute(
            "SELECT * FROM politics_wars WHERE campaign_id=? AND id=?",
            (campaign_id, war_id),
        ).fetchone()
        if not row:
            raise KeyError(f"unknown war: {war_id}")
        out = self._row_json(self.e, row, "goals", "metadata")
        out["participants"] = [
            dict(item)
            for item in db.execute(
                """SELECT faction_id,side,joined_world_time,status
                   FROM politics_war_participants WHERE campaign_id=? AND war_id=?
                   ORDER BY side,faction_id""",
                (campaign_id, war_id),
            ).fetchall()
        ]
        return out

    def occupy_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        war_id: str,
        *,
        force_id: str,
        location_id: str,
        control: float,
        revision: int,
        when: datetime,
        emit: Callable[..., None] | None = None,
    ) -> dict[str, Any]:
        war = self._war_db(db, campaign_id, war_id)
        if war["status"] != "active":
            raise ValueError("occupation requires an active war")
        force = self._force_db(db, campaign_id, force_id)
        if str(force["location_id"]) != location_id:
            raise ValueError("occupying force must be at the location")
        participants = {str(item["faction_id"]) for item in war["participants"]}
        if str(force["faction_id"]) not in participants:
            raise ValueError("occupying force is not a war participant")
        prior = db.execute(
            """SELECT controller_faction_id FROM politics_territorial_control
               WHERE campaign_id=? AND location_id=?""",
            (campaign_id, location_id),
        ).fetchone()
        if prior and str(prior["controller_faction_id"]) == str(force["faction_id"]):
            raise ValueError("faction already controls the location")
        return self.set_control_db(
            db,
            campaign_id,
            location_id,
            str(force["faction_id"]),
            control=control,
            occupation_state="occupied",
            war_id=war_id,
            reason=f"occupation during {war_id}",
            revision=revision,
            when=when,
            emit=emit,
        )

    def make_peace_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        war_id: str,
        *,
        treaty_id: str,
        clauses: Iterable[dict[str, Any]],
        obligations: Iterable[dict[str, Any]],
        revision: int,
        when: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        war = self._war_db(db, campaign_id, war_id)
        if war["status"] not in {"active", "armistice"}:
            raise ValueError("war is already ended")
        treaty = self.create_treaty_db(
            db,
            campaign_id,
            treaty_id,
            treaty_type="peace",
            name=f"Peace settlement for {war_id}",
            parties=[str(item["faction_id"]) for item in war["participants"]],
            clauses=clauses,
            obligations=obligations,
            revision=revision,
            when=when,
            visibility="public",
            metadata=dict(metadata or {}),
        )
        db.execute(
            """UPDATE politics_wars SET status='ended',ended_world_time=?,peace_treaty_id=?,
               revision=?,updated_at=? WHERE campaign_id=? AND id=?""",
            (
                _utc(when).isoformat(),
                treaty_id,
                revision,
                self.e._now(),
                campaign_id,
                war_id,
            ),
        )
        return {"war": self._war_db(db, campaign_id, war_id), "treaty": treaty}

    # ------------------------------------------------------------------
    # Treaty fulfilment and bounded jurisdiction hooks
    # ------------------------------------------------------------------

    def fulfill_obligation_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        obligation_id: str,
        *,
        revision: int,
        when: datetime,
    ) -> dict[str, Any]:
        row = db.execute(
            "SELECT * FROM politics_obligations WHERE campaign_id=? AND id=?",
            (campaign_id, obligation_id),
        ).fetchone()
        if not row:
            raise KeyError(f"unknown obligation: {obligation_id}")
        if row["status"] != "pending":
            raise ValueError("obligation is not pending")
        amount = float(row["amount"] or 0.0)
        kind = str(row["resource_kind"] or "")
        key = str(row["resource_key"] or "")
        if amount > 0 and kind in {"currency", "inventory"}:
            commitment = self.reserve_db(
                db,
                campaign_id,
                f"obligation:{obligation_id}",
                actor_kind="faction",
                actor_id=str(row["debtor_faction_id"]),
                resource_kind=kind,
                resource_key=key,
                amount=amount,
                purpose_kind="obligation",
                purpose_id=obligation_id,
                revision=revision,
                when=when,
            )
            self.consume_commitment_db(db, campaign_id, str(commitment["id"]))
            self._escrow_adjust_db(
                db,
                campaign_id,
                kind,
                key,
                "faction",
                str(row["beneficiary_faction_id"]),
                amount,
            )
        db.execute(
            """UPDATE politics_obligations SET status='fulfilled',revision=?,updated_at=?
               WHERE campaign_id=? AND id=?""",
            (revision, self.e._now(), campaign_id, obligation_id),
        )
        return self._row_json(
            self.e,
            db.execute(
                "SELECT * FROM politics_obligations WHERE campaign_id=? AND id=?",
                (campaign_id, obligation_id),
            ).fetchone(),
            "terms",
        )

    def set_jurisdiction_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        location_id: str,
        *,
        authority_faction_id: str,
        law_profile: dict[str, Any],
        enforcement_capacity: float,
        revision: int,
    ) -> dict[str, Any]:
        self._location_db(db, campaign_id, location_id)
        self._faction_db(db, campaign_id, authority_faction_id)
        profile = dict(law_profile or {})
        _json_guard(profile, "law_profile")
        db.execute(
            """INSERT INTO politics_jurisdictions(
                   campaign_id,location_id,authority_faction_id,law_profile_json,
                   enforcement_capacity,revision,updated_at)
               VALUES(?,?,?,?,?,?,?) ON CONFLICT(campaign_id,location_id) DO UPDATE SET
                   authority_faction_id=excluded.authority_faction_id,
                   law_profile_json=excluded.law_profile_json,
                   enforcement_capacity=excluded.enforcement_capacity,
                   revision=excluded.revision,updated_at=excluded.updated_at""",
            (
                campaign_id,
                location_id,
                authority_faction_id,
                self.e._dumps(profile),
                self._number(enforcement_capacity, "enforcement_capacity"),
                revision,
                self.e._now(),
            ),
        )
        row = db.execute(
            "SELECT * FROM politics_jurisdictions WHERE campaign_id=? AND location_id=?",
            (campaign_id, location_id),
        ).fetchone()
        return self._row_json(self.e, row, "law_profile")

    def open_legal_case_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        case_id: str,
        *,
        location_id: str,
        offender_kind: str,
        offender_id: str,
        offense: str,
        severity: float,
        revision: int,
        when: datetime,
        source_crime_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        case_id = self._id(case_id, "case_id")
        jurisdiction = db.execute(
            """SELECT * FROM politics_jurisdictions WHERE campaign_id=? AND location_id=?""",
            (campaign_id, location_id),
        ).fetchone()
        if not jurisdiction:
            raise ValueError("location has no configured jurisdiction")
        offender_kind = str(offender_kind).lower()
        if offender_kind not in {"character", "npc", "faction"}:
            raise ValueError("invalid offender_kind")
        self._owner_db(db, campaign_id, offender_kind, offender_id)
        if (
            source_crime_id
            and not db.execute(
                "SELECT 1 FROM crimes WHERE campaign_id=? AND id=?",
                (campaign_id, source_crime_id),
            ).fetchone()
        ):
            raise KeyError(f"unknown crime: {source_crime_id}")
        meta = dict(metadata or {})
        _json_guard(meta, "metadata")
        db.execute(
            """INSERT INTO politics_legal_cases(
                   campaign_id,id,location_id,authority_faction_id,offender_kind,
                   offender_id,offense,severity,status,source_crime_id,
                   opened_world_time,revision,metadata_json,updated_at)
               VALUES(?,?,?,?,?,?,?,?,'open',?,?,?,?,?)""",
            (
                campaign_id,
                case_id,
                location_id,
                jurisdiction["authority_faction_id"],
                offender_kind,
                self._id(offender_id, "offender_id"),
                str(offense)[:500],
                self._number(severity, "severity", maximum=1.0),
                source_crime_id,
                _utc(when).isoformat(),
                revision,
                self.e._dumps(meta),
                self.e._now(),
            ),
        )
        row = db.execute(
            "SELECT * FROM politics_legal_cases WHERE campaign_id=? AND id=?",
            (campaign_id, case_id),
        ).fetchone()
        return self._row_json(self.e, row, "disposition", "metadata")

    # ------------------------------------------------------------------
    # Deterministic daily integration and actor-safe projection
    # ------------------------------------------------------------------

    def has_activity_db(self, db: sqlite3.Connection, campaign_id: str) -> bool:
        self.seed_defaults_db(db, campaign_id)
        config = db.execute(
            "SELECT enabled FROM politics_config WHERE campaign_id=?", (campaign_id,)
        ).fetchone()
        if not config or not bool(config["enabled"]):
            return False
        row = db.execute(
            """SELECT
                 EXISTS(SELECT 1 FROM politics_commitments WHERE campaign_id=? AND status='reserved') OR
                 EXISTS(SELECT 1 FROM politics_projects WHERE campaign_id=? AND status IN ('planned','active')) OR
                 EXISTS(SELECT 1 FROM politics_proposals WHERE campaign_id=? AND status='pending') OR
                 EXISTS(SELECT 1 FROM politics_treaties WHERE campaign_id=? AND status='active') OR
                 EXISTS(SELECT 1 FROM politics_forces WHERE campaign_id=? AND status IN ('mobilized','deployed','routed')) OR
                 EXISTS(SELECT 1 FROM politics_wars WHERE campaign_id=? AND status IN ('active','armistice')) AS active""",
            (
                campaign_id, campaign_id, campaign_id,
                campaign_id, campaign_id, campaign_id,
            ),
        ).fetchone()
        return bool(row["active"])

    def step_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        revision: int,
        when: datetime,
        *,
        emit: Callable[..., None] | None = None,
    ) -> dict[str, int | float]:
        when = _utc(when)
        if when.hour or when.minute or when.second or when.microsecond:
            raise ValueError("politics step requires a canonical UTC day boundary")
        self.seed_defaults_db(db, campaign_id)
        day_key = when.date().isoformat()
        prior = db.execute(
            "SELECT tally_json FROM politics_daily_steps WHERE campaign_id=? AND day_key=?",
            (campaign_id, day_key),
        ).fetchone()
        if prior:
            return self.e._loads(prior["tally_json"])
        latest = db.execute(
            "SELECT MAX(world_time) latest FROM politics_daily_steps WHERE campaign_id=?",
            (campaign_id,),
        ).fetchone()
        if latest and latest["latest"] and _utc(str(latest["latest"])) > when:
            raise ValueError("politics steps must be applied in chronological order")
        config = db.execute(
            "SELECT * FROM politics_config WHERE campaign_id=?", (campaign_id,)
        ).fetchone()
        tally: dict[str, int | float] = {
            "projects_advanced": 0,
            "projects_completed": 0,
            "proposals_expired": 0,
            "obligations_violated": 0,
            "treaties_completed": 0,
            "strategy_candidates": 0,
            "commitments_expired": 0,
        }
        if bool(config["enabled"]):
            expired_commitments = db.execute(
                """SELECT id FROM politics_commitments WHERE campaign_id=?
                   AND status='reserved' AND expires_world_time IS NOT NULL
                   AND expires_world_time<=? ORDER BY id""",
                (campaign_id, when.isoformat()),
            ).fetchall()
            for commitment in expired_commitments:
                self.release_commitment_db(db, campaign_id, str(commitment["id"]))
                tally["commitments_expired"] += 1
            proposals = db.execute(
                """SELECT id FROM politics_proposals WHERE campaign_id=? AND status='pending'
                   AND expires_world_time IS NOT NULL AND expires_world_time<=?
                   ORDER BY id""",
                (campaign_id, when.isoformat()),
            ).fetchall()
            for proposal in proposals:
                db.execute(
                    """UPDATE politics_proposals SET status='expired',responded_world_time=?,
                       revision=?,updated_at=? WHERE campaign_id=? AND id=?""",
                    (
                        when.isoformat(),
                        revision,
                        self.e._now(),
                        campaign_id,
                        proposal["id"],
                    ),
                )
                tally["proposals_expired"] += 1
            obligations = db.execute(
                """SELECT * FROM politics_obligations WHERE campaign_id=? AND status='pending'
                   AND due_world_time IS NOT NULL AND due_world_time<=? ORDER BY id""",
                (campaign_id, when.isoformat()),
            ).fetchall()
            for obligation in obligations:
                violation_id = (
                    "overdue:"
                    + _digest(
                        {"campaign_id": campaign_id, "obligation_id": obligation["id"]}
                    )[:24]
                )
                self.record_violation_db(
                    db,
                    campaign_id,
                    violation_id,
                    treaty_id=str(obligation["treaty_id"]),
                    obligation_id=str(obligation["id"]),
                    violator_faction_id=str(obligation["debtor_faction_id"]),
                    harmed_faction_id=str(obligation["beneficiary_faction_id"]),
                    violation_type="overdue_obligation",
                    severity=0.5,
                    revision=revision,
                    when=when,
                    emit=emit,
                )
                tally["obligations_violated"] += 1
            projects = db.execute(
                """SELECT * FROM politics_projects WHERE campaign_id=? AND status='active'
                   ORDER BY id""",
                (campaign_id,),
            ).fetchall()
            for project in projects:
                last = _utc(
                    project["last_step_world_time"] or project["started_world_time"]
                )
                elapsed_days = max(0, int((when - last).total_seconds() // 86400))
                if elapsed_days <= 0:
                    continue
                labor = db.execute(
                    """SELECT COALESCE(SUM(amount-consumed-released),0) n
                       FROM politics_commitments WHERE campaign_id=? AND purpose_kind='project'
                       AND purpose_id=? AND resource_kind='labor' AND status='reserved'""",
                    (campaign_id, project["id"]),
                ).fetchone()
                work = max(0.0, float(labor["n"] or 0.0)) * elapsed_days
                progress = min(
                    float(project["work_required"]), float(project["progress"]) + work
                )
                status = (
                    "completed"
                    if progress + 1e-9 >= float(project["work_required"])
                    else "active"
                )
                completed_time = when.isoformat() if status == "completed" else None
                db.execute(
                    """UPDATE politics_projects SET progress=?,status=?,completed_world_time=?,
                       last_step_world_time=?,revision=?,updated_at=? WHERE campaign_id=? AND id=?""",
                    (
                        progress,
                        status,
                        completed_time,
                        when.isoformat(),
                        revision,
                        self.e._now(),
                        campaign_id,
                        project["id"],
                    ),
                )
                tally["projects_advanced"] += 1
                if status == "completed":
                    self._consume_project_resources_db(
                        db, campaign_id, str(project["id"])
                    )
                    tally["projects_completed"] += 1
                    self._emit(
                        emit,
                        "politics_project_completed",
                        f"Project {project['id']} completed",
                        {
                            "project_id": str(project["id"]),
                            "location_id": str(project["location_id"]),
                        },
                        str(project["location_id"]),
                        when,
                        sensitivity="PRIVATE",
                        scope_type="ENTITY",
                        principal_kind="faction",
                        principal_id=str(project["owner_faction_id"]),
                    )
            ended = db.execute(
                """SELECT id FROM politics_treaties WHERE campaign_id=? AND status='active'
                   AND end_world_time IS NOT NULL AND end_world_time<=? ORDER BY id""",
                (campaign_id, when.isoformat()),
            ).fetchall()
            for treaty in ended:
                db.execute(
                    """UPDATE politics_treaties SET status='fulfilled',revision=?,updated_at=?
                       WHERE campaign_id=? AND id=?""",
                    (revision, self.e._now(), campaign_id, treaty["id"]),
                )
                tally["treaties_completed"] += 1
            if bool(config["daily_strategy_enabled"]):
                candidate = db.execute(
                    """SELECT COUNT(*) n FROM (
                         SELECT claimant_faction_id faction_id FROM politics_claims
                           WHERE campaign_id=? AND status='active'
                         UNION SELECT aggrieved_faction_id FROM politics_grievances
                           WHERE campaign_id=? AND status='active'
                       )""",
                    (campaign_id, campaign_id),
                ).fetchone()
                tally["strategy_candidates"] = min(
                    int(candidate["n"]), int(config["max_daily_decisions"])
                )
        safe_tally = {
            key: int(value) if float(value).is_integer() else float(value)
            for key, value in tally.items()
        }
        db.execute(
            """INSERT INTO politics_daily_steps(campaign_id,day_key,world_time,revision,tally_json)
               VALUES(?,?,?,?,?)""",
            (
                campaign_id,
                day_key,
                when.isoformat(),
                revision,
                self.e._dumps(safe_tally),
            ),
        )
        return safe_tally

    def public_snapshot_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        *,
        actor_kind: str | None = None,
        actor_id: str | None = None,
        location_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        own_faction = actor_id if actor_kind == "faction" else None
        location_filter = " AND location_id=?" if location_id else ""
        location_params: tuple[Any, ...] = (
            (campaign_id, location_id, limit) if location_id else (campaign_id, limit)
        )
        controls = db.execute(
            """SELECT location_id,controller_faction_id,control,occupation_state,war_id,
                      since_world_time,revision FROM politics_territorial_control
               WHERE campaign_id=?"""
            + location_filter
            + " ORDER BY location_id LIMIT ?",
            location_params,
        ).fetchall()
        wars = db.execute(
            """SELECT id,attacker_faction_id,defender_faction_id,status,goals_json,
                      started_world_time,ended_world_time,peace_treaty_id,revision
               FROM politics_wars WHERE campaign_id=? ORDER BY started_world_time DESC,id LIMIT ?""",
            (campaign_id, limit),
        ).fetchall()
        claims = db.execute(
            """SELECT id,claimant_faction_id,target_kind,target_id,claim_type,strength,status,
                      visibility,created_world_time,revision FROM politics_claims
               WHERE campaign_id=? AND (visibility='public' OR claimant_faction_id=?)
               ORDER BY strength DESC,id LIMIT ?""",
            (campaign_id, own_faction or "", limit),
        ).fetchall()
        grievances = db.execute(
            """SELECT id,aggrieved_faction_id,against_faction_id,grievance_type,severity,
                      status,visibility,created_world_time,revision FROM politics_grievances
               WHERE campaign_id=? AND (visibility='public' OR aggrieved_faction_id=?
                    OR against_faction_id=?) ORDER BY severity DESC,id LIMIT ?""",
            (campaign_id, own_faction or "", own_faction or "", limit),
        ).fetchall()
        proposals = db.execute(
            """SELECT id,proposer_faction_id,recipient_faction_id,proposal_type,status,
                      terms_json,counter_of_id,treaty_id,visibility,created_world_time,
                      expires_world_time,revision FROM politics_proposals
               WHERE campaign_id=? AND (visibility='public' OR proposer_faction_id=?
                    OR recipient_faction_id=?) ORDER BY created_world_time DESC,id LIMIT ?""",
            (campaign_id, own_faction or "", own_faction or "", limit),
        ).fetchall()
        treaties = db.execute(
            """SELECT t.* FROM politics_treaties t WHERE t.campaign_id=?
               AND (t.visibility='public' OR EXISTS(
                   SELECT 1 FROM politics_treaty_parties p WHERE p.campaign_id=t.campaign_id
                   AND p.treaty_id=t.id AND p.faction_id=?))
               ORDER BY t.effective_world_time DESC,t.id LIMIT ?""",
            (campaign_id, own_faction or "", limit),
        ).fetchall()
        projects = db.execute(
            """SELECT id,owner_faction_id,location_id,project_kind,name,status,progress,
                      work_required,started_world_time,completed_world_time,revision
               FROM politics_projects WHERE campaign_id=?"""
            + location_filter
            + " ORDER BY id LIMIT ?",
            location_params,
        ).fetchall()
        forces: list[sqlite3.Row] = []
        commitments: list[sqlite3.Row] = []
        beliefs: list[dict[str, Any]] = []
        if own_faction:
            forces = db.execute(
                """SELECT id,faction_id,name,force_type,location_id,manpower,readiness,
                          morale,status,supply_item_id,supply_remaining,mobilized_world_time,
                          revision FROM politics_forces WHERE campaign_id=? AND faction_id=?
                   ORDER BY status,id LIMIT ?""",
                (campaign_id, own_faction, limit),
            ).fetchall()
            commitments = db.execute(
                """SELECT id,resource_kind,resource_key,location_id,amount,consumed,released,
                          status,purpose_kind,purpose_id,created_world_time,expires_world_time,
                          revision FROM politics_commitments WHERE campaign_id=?
                   AND actor_kind='faction' AND actor_id=? ORDER BY id LIMIT ?""",
                (campaign_id, own_faction, limit),
            ).fetchall()
            beliefs = self.belief_view_db(db, campaign_id, own_faction, limit=limit)
        treaty_views = []
        for row in treaties:
            treaty = self._treaty_db(db, campaign_id, str(row["id"]))
            treaty.pop("metadata", None)
            treaty_views.append(treaty)
        proposal_views = []
        for row in proposals:
            item = dict(row)
            item["terms"] = self.e._loads(item.pop("terms_json"))
            proposal_views.append(item)
        war_views = []
        for row in wars:
            item = dict(row)
            item["goals"] = self.e._loads(item.pop("goals_json"))
            war_views.append(item)
        return {
            "campaign_id": campaign_id,
            "actor": {"kind": actor_kind, "id": actor_id}
            if actor_kind and actor_id
            else None,
            "location_id": location_id,
            "territorial_control": [dict(row) for row in controls],
            "wars": war_views,
            "treaties": treaty_views,
            "proposals": proposal_views,
            "claims": [dict(row) for row in claims],
            "grievances": [dict(row) for row in grievances],
            "projects": [dict(row) for row in projects],
            "forces": [dict(row) for row in forces],
            "commitments": [dict(row) for row in commitments],
            "beliefs": beliefs,
        }

    def public_snapshot(
        self,
        campaign_id: str,
        *,
        actor_kind: str | None = None,
        actor_id: str | None = None,
        location_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        with self.e._db() as db:
            return self.public_snapshot_db(
                db,
                campaign_id,
                actor_kind=actor_kind,
                actor_id=actor_id,
                location_id=location_id,
                limit=limit,
            )

    # ------------------------------------------------------------------
    # Actor-scoped, replay-safe dispatch seam
    # ------------------------------------------------------------------

    def _authorize_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        operation: str,
        actor_kind: str,
        actor_id: str,
        payload: dict[str, Any],
    ) -> None:
        if actor_kind == "system":
            return
        self._owner_db(db, campaign_id, actor_kind, actor_id)
        if actor_kind != "faction":
            raise ValueError("politics mutations require a faction or system principal")
        owner_fields = {
            "create_project": "owner_faction_id",
            "add_claim": "claimant_faction_id",
            "create_proposal": "proposer_faction_id",
            "respond_proposal": "responder_faction_id",
            "mobilize": "faction_id",
            "plan_raid": "attacker_faction_id",
            "declare_war": "attacker_faction_id",
        }
        if operation in owner_fields:
            if str(payload.get(owner_fields[operation]) or "") != actor_id:
                raise ValueError("politics principal does not own this action")
            return
        if operation == "add_grievance":
            if str(payload.get("aggrieved_faction_id")) != actor_id:
                raise ValueError("politics principal does not own this action")
            source_kind = str(payload.get("source_kind") or "")
            source_id = str(payload.get("source_id") or "")
            against = str(payload.get("against_faction_id") or "")
            if source_kind == "raid":
                source = db.execute(
                    """SELECT attacker_faction_id,target_faction_id,status FROM politics_raids
                       WHERE campaign_id=? AND id=?""",
                    (campaign_id, source_id),
                ).fetchone()
                valid = bool(
                    source
                    and source["status"] == "resolved"
                    and str(source["target_faction_id"]) == actor_id
                    and str(source["attacker_faction_id"]) == against
                )
            elif source_kind == "treaty_violation":
                source = db.execute(
                    """SELECT violator_faction_id,harmed_faction_id
                       FROM politics_treaty_violations WHERE campaign_id=? AND id=?""",
                    (campaign_id, source_id),
                ).fetchone()
                valid = bool(
                    source
                    and str(source["harmed_faction_id"]) == actor_id
                    and str(source["violator_faction_id"]) == against
                )
            else:
                valid = False
            if not valid:
                raise ValueError(
                    "faction grievance requires a matching raid or treaty violation"
                )
            return
        if operation in {"start_project", "cancel_project"}:
            row = db.execute(
                """SELECT owner_faction_id FROM politics_projects
                   WHERE campaign_id=? AND id=?""",
                (campaign_id, payload.get("project_id")),
            ).fetchone()
            if not row or str(row["owner_faction_id"]) != actor_id:
                raise ValueError("politics principal does not own this project")
            return
        if operation == "demobilize":
            row = db.execute(
                "SELECT faction_id FROM politics_forces WHERE campaign_id=? AND id=?",
                (campaign_id, payload.get("force_id")),
            ).fetchone()
            if not row or str(row["faction_id"]) != actor_id:
                raise ValueError("politics principal does not own this force")
            return
        if operation == "cancel_raid":
            row = db.execute(
                """SELECT attacker_faction_id FROM politics_raids
                   WHERE campaign_id=? AND id=?""",
                (campaign_id, payload.get("raid_id")),
            ).fetchone()
            if not row or str(row["attacker_faction_id"]) != actor_id:
                raise ValueError("politics principal does not own this raid")
            return
        if operation == "deploy_force":
            row = db.execute(
                "SELECT faction_id FROM politics_forces WHERE campaign_id=? AND id=?",
                (campaign_id, payload.get("force_id")),
            ).fetchone()
            if not row or str(row["faction_id"]) != actor_id:
                raise ValueError("politics principal does not own this force")
            return
        if operation == "occupy":
            row = db.execute(
                "SELECT faction_id FROM politics_forces WHERE campaign_id=? AND id=?",
                (campaign_id, payload.get("force_id")),
            ).fetchone()
            if not row or str(row["faction_id"]) != actor_id:
                raise ValueError("politics principal does not own the occupying force")
            return
        if operation == "fulfill_obligation":
            row = db.execute(
                """SELECT debtor_faction_id FROM politics_obligations
                   WHERE campaign_id=? AND id=?""",
                (campaign_id, payload.get("obligation_id")),
            ).fetchone()
            if not row or str(row["debtor_faction_id"]) != actor_id:
                raise ValueError("only the debtor may fulfill this obligation")
            return
        raise ValueError("operation requires the system principal")

    def _execute_operation_db(
        self,
        db: sqlite3.Connection,
        operation: str,
        campaign_id: str,
        payload: dict[str, Any],
        *,
        revision: int,
        when: datetime,
        emit: Callable[..., None] | None,
    ) -> Any:
        p = dict(payload)
        if operation == "configure":
            allowed = {
                "enabled",
                "daily_strategy_enabled",
                "max_daily_decisions",
                "state",
            }
            unknown = set(p) - allowed
            if unknown:
                raise ValueError(f"unknown politics config fields: {sorted(unknown)}")
            self.seed_defaults_db(db, campaign_id)
            assignments: list[str] = []
            params: list[Any] = []
            for field in ("enabled", "daily_strategy_enabled"):
                if field in p:
                    if not isinstance(p[field], bool):
                        raise ValueError(f"{field} must be a boolean")
                    assignments.append(f"{field}=?")
                    params.append(int(p[field]))
            if "max_daily_decisions" in p:
                value = self._number(
                    p["max_daily_decisions"], "max_daily_decisions", maximum=1000
                )
                if not value.is_integer():
                    raise ValueError("max_daily_decisions must be an integer")
                assignments.append("max_daily_decisions=?")
                params.append(int(value))
            if "state" in p:
                state = dict(p["state"] or {})
                _json_guard(state, "state")
                assignments.append("state_json=?")
                params.append(self.e._dumps(state))
            if assignments:
                assignments.append("updated_at=?")
                params.extend([self.e._now(), campaign_id])
                db.execute(
                    f"UPDATE politics_config SET {','.join(assignments)} WHERE campaign_id=?",
                    params,
                )
            row = dict(
                db.execute(
                    "SELECT * FROM politics_config WHERE campaign_id=?", (campaign_id,)
                ).fetchone()
            )
            row["enabled"] = bool(row["enabled"])
            row["daily_strategy_enabled"] = bool(row["daily_strategy_enabled"])
            row["state"] = self.e._loads(row.pop("state_json"))
            return row
        if operation == "reserve":
            return self.reserve_db(db, campaign_id, revision=revision, when=when, **p)
        if operation == "consume_commitment":
            return self.consume_commitment_db(db, campaign_id, **p)
        if operation == "release_commitment":
            return self.release_commitment_db(db, campaign_id, **p)
        if operation == "create_project":
            return self.create_project_db(
                db, campaign_id, revision=revision, when=when, **p
            )
        if operation == "start_project":
            return self.start_project_db(
                db, campaign_id, revision=revision, when=when, **p
            )
        if operation == "cancel_project":
            return self.cancel_project_db(db, campaign_id, revision=revision, **p)
        if operation == "add_claim":
            return self.add_claim_db(db, campaign_id, revision=revision, when=when, **p)
        if operation == "add_grievance":
            return self.add_grievance_db(
                db, campaign_id, revision=revision, when=when, **p
            )
        if operation == "set_control":
            return self.set_control_db(
                db, campaign_id, revision=revision, when=when, emit=emit, **p
            )
        if operation == "create_proposal":
            return self.create_proposal_db(
                db, campaign_id, revision=revision, when=when, **p
            )
        if operation == "respond_proposal":
            return self.respond_proposal_db(
                db, campaign_id, revision=revision, when=when, **p
            )
        if operation == "create_treaty":
            return self.create_treaty_db(
                db, campaign_id, revision=revision, when=when, **p
            )
        if operation == "record_violation":
            return self.record_violation_db(
                db, campaign_id, revision=revision, when=when, emit=emit, **p
            )
        if operation == "fulfill_obligation":
            return self.fulfill_obligation_db(
                db, campaign_id, revision=revision, when=when, **p
            )
        if operation == "mobilize":
            return self.mobilize_db(
                db, campaign_id, revision=revision, when=when, emit=emit, **p
            )
        if operation == "apply_force_losses":
            return self.apply_force_losses_db(
                db, campaign_id, revision=revision, when=when, emit=emit, **p
            )
        if operation == "demobilize":
            return self.demobilize_db(
                db, campaign_id, revision=revision, when=when, **p
            )
        if operation == "deploy_force":
            return self.deploy_force_db(
                db, campaign_id, revision=revision, when=when, **p
            )
        if operation == "plan_raid":
            return self.plan_raid_db(db, campaign_id, revision=revision, when=when, **p)
        if operation == "resolve_raid":
            return self.resolve_raid_db(
                db, campaign_id, revision=revision, when=when, emit=emit, **p
            )
        if operation == "cancel_raid":
            return self.cancel_raid_db(db, campaign_id, revision=revision, **p)
        if operation == "declare_war":
            return self.declare_war_db(
                db, campaign_id, revision=revision, when=when, **p
            )
        if operation == "occupy":
            return self.occupy_db(
                db, campaign_id, revision=revision, when=when, emit=emit, **p
            )
        if operation == "make_peace":
            return self.make_peace_db(
                db, campaign_id, revision=revision, when=when, **p
            )
        if operation == "set_jurisdiction":
            return self.set_jurisdiction_db(db, campaign_id, revision=revision, **p)
        if operation == "open_legal_case":
            return self.open_legal_case_db(
                db, campaign_id, revision=revision, when=when, **p
            )
        raise ValueError(f"unknown politics operation: {operation}")

    def dispatch_db(
        self,
        db: sqlite3.Connection,
        operation: str,
        campaign_id: str,
        payload: dict[str, Any],
        *,
        revision: int,
        world_time: datetime | str,
        emit: Callable[..., None] | None = None,
    ) -> Any:
        operation = str(operation or "").strip().lower()
        data = dict(payload or {})
        if "principal_kind" in data:
            actor_kind = str(data.pop("principal_kind")).lower()
        else:
            actor_kind = str(data.pop("actor_kind", "")).lower()
        if "principal_id" in data:
            actor_id = self._id(data.pop("principal_id"), "principal_id")
        else:
            actor_id = self._id(data.pop("actor_id", ""), "principal_id")
        request_key = self._id(data.pop("request_key", ""), "request_key")
        if actor_kind not in _KINDS | {"system"}:
            raise ValueError("invalid politics principal_kind")
        request_digest = _digest(
            {
                "campaign_id": campaign_id,
                "operation": operation,
                "actor_kind": actor_kind,
                "actor_id": actor_id,
                "payload": data,
            }
        )
        prior = db.execute(
            """SELECT * FROM politics_action_receipts WHERE campaign_id=?
               AND actor_kind=? AND actor_id=? AND request_key=?""",
            (campaign_id, actor_kind, actor_id, request_key),
        ).fetchone()
        if prior:
            if str(prior["request_digest"]) != request_digest:
                raise ValueError("POLITICS_IDEMPOTENCY_CONFLICT")
            result = self.e._loads(prior["result_json"])
            return {**result, "idempotent_replay": True}
        self._authorize_db(db, campaign_id, operation, actor_kind, actor_id, data)
        when = _utc(world_time)
        result = self._execute_operation_db(
            db,
            operation,
            campaign_id,
            data,
            revision=int(revision),
            when=when,
            emit=emit,
        )
        if not isinstance(result, dict):
            result = {"result": result}
        result = {
            **result,
            "campaign_id": campaign_id,
            "operation": operation,
            "revision": int(revision),
            "world_time": when.isoformat(),
            "idempotent_replay": False,
        }
        _json_guard(result, "result")
        db.execute(
            """INSERT INTO politics_action_receipts(
                   campaign_id,actor_kind,actor_id,request_key,operation,request_digest,
                   result_json,revision,world_time,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                campaign_id,
                actor_kind,
                actor_id,
                request_key,
                operation,
                request_digest,
                self.e._dumps(result),
                int(revision),
                when.isoformat(),
                self.e._now(),
            ),
        )
        event_policy = self._action_event_policy_db(
            db, campaign_id, operation, data, actor_kind, actor_id
        )
        self._emit(
            emit,
            "politics_action",
            f"Politics action completed: {operation}",
            {
                "operation": operation,
                "principal_kind": actor_kind,
                "principal_id": actor_id,
            },
            data.get("location_id") or data.get("target_location_id"),
            when,
            **event_policy,
        )
        return result

    def dispatch(
        self,
        operation: str,
        campaign_id: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        operation = str(operation or "").strip().lower()
        data = dict(payload or {})
        if operation in {"snapshot", "public_snapshot", "inspect"}:
            return self.public_snapshot(campaign_id, **data)
        if operation == "strategy_view":
            faction_id = self._id(data.pop("faction_id"), "faction_id")
            actor_kind = str(data.pop("actor_kind", "faction"))
            actor_id = str(data.pop("actor_id", faction_id))
            if actor_kind != "faction" or actor_id != faction_id:
                raise ValueError("strategy view is faction-principal scoped")
            with self.e._db() as db:
                return self.strategy_view_db(db, campaign_id, faction_id, **data)
        with self.e._write_db() as db:
            if not self._table(db, "politics_action_receipts"):
                raise RuntimeError("politics schema is not installed")
            campaign = self._campaign_db(db, campaign_id)
            # Replay lookup must happen before revision allocation. dispatch_db
            # repeats the lookup and returns the stored result without writes.
            actor_kind = str(
                data["principal_kind"]
                if "principal_kind" in data
                else data.get("actor_kind", "")
            ).lower()
            actor_id = str(
                data["principal_id"]
                if "principal_id" in data
                else data.get("actor_id", "")
            )
            request_key = str(data.get("request_key", ""))
            if actor_kind and actor_id and request_key:
                request_data = dict(data)
                if "principal_kind" in request_data:
                    request_data.pop("principal_kind")
                else:
                    request_data.pop("actor_kind", None)
                if "principal_id" in request_data:
                    request_data.pop("principal_id")
                else:
                    request_data.pop("actor_id", None)
                request_data.pop("request_key", None)
                request_digest = _digest(
                    {
                        "campaign_id": campaign_id,
                        "operation": operation,
                        "actor_kind": actor_kind,
                        "actor_id": actor_id,
                        "payload": request_data,
                    }
                )
                prior = db.execute(
                    """SELECT * FROM politics_action_receipts WHERE campaign_id=?
                       AND actor_kind=? AND actor_id=? AND request_key=?""",
                    (campaign_id, actor_kind, actor_id, request_key),
                ).fetchone()
                if prior:
                    if str(prior["request_digest"]) != request_digest:
                        raise ValueError("POLITICS_IDEMPOTENCY_CONFLICT")
                    result = self.e._loads(prior["result_json"])
                    return {**result, "idempotent_replay": True}
            revision = self.e._next_revision(db, campaign_id)
            when = _utc(str(campaign["world_time"]))

            def persist_emit(
                event_type: str,
                summary: str,
                event_payload: dict[str, Any],
                location: str | None,
                event_when: datetime,
                **event_options: Any,
            ) -> None:
                self.e._insert_event(
                    db,
                    campaign_id,
                    revision,
                    event_type,
                    summary,
                    region=location,
                    payload=event_payload,
                    world_time_override=_utc(event_when).isoformat(),
                    **event_options,
                )

            return self.dispatch_db(
                db,
                operation,
                campaign_id,
                data,
                revision=revision,
                world_time=when,
                emit=persist_emit,
            )
