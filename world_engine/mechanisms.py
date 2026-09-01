from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from typing import Any, Iterable, Sequence, TYPE_CHECKING

from .environment import EFFECT_TYPES as ENVIRONMENT_EFFECT_TYPES
from .environment import EnvironmentKernel

if TYPE_CHECKING:
    from .engine import WorldEngine


CONTRACT_VERSION = "MOP-1.0"
MAX_BINDINGS = 16
MAX_PREDICATE_DEPTH = 8
MAX_PREDICATE_LEAVES = 128
MAX_TRANSITION_EFFECTS = 128
MAX_EMITS = 32
MAX_CONSIDERATIONS = 128
MAX_TAGS = 128
MAX_OPERATOR_BYTES = 131_072
MAX_RUNTIME_BYTES = 262_144
MAX_VALUE_DEPTH = 16
MAX_VALUE_NODES = 4096
MAX_QUERY_LIMIT = 200

MECHANISM_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS mechanism_operators (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    contract_version TEXT NOT NULL DEFAULT 'MOP-1.0',
    source_kind TEXT NOT NULL DEFAULT 'global',
    source_id TEXT,
    bindings_json TEXT NOT NULL DEFAULT '{}',
    preconditions_json TEXT NOT NULL DEFAULT '[]',
    costs_json TEXT NOT NULL DEFAULT '[]',
    effects_json TEXT NOT NULL DEFAULT '[]',
    emits_json TEXT NOT NULL DEFAULT '[]',
    considerations_json TEXT NOT NULL DEFAULT '[]',
    planning_preconditions_json TEXT NOT NULL DEFAULT '{}',
    planning_effects_json TEXT NOT NULL DEFAULT '{}',
    base_utility REAL NOT NULL DEFAULT 0,
    cost_hours REAL NOT NULL DEFAULT 0 CHECK(cost_hours >= 0),
    tags_json TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    operator_digest TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_mechanism_operators_source
    ON mechanism_operators(campaign_id,enabled,source_kind,source_id,id);

CREATE TABLE IF NOT EXISTS mechanism_execution_receipts (
    campaign_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    operator_id TEXT NOT NULL,
    idempotency_key TEXT,
    request_digest TEXT NOT NULL,
    operator_digest TEXT NOT NULL,
    bindings_json TEXT NOT NULL DEFAULT '{}',
    evaluation_json TEXT NOT NULL DEFAULT '{}',
    effect_results_json TEXT NOT NULL DEFAULT '[]',
    event_ids_json TEXT NOT NULL DEFAULT '[]',
    before_revision INTEGER NOT NULL,
    after_revision INTEGER NOT NULL,
    world_time TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    result_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,execution_id),
    UNIQUE(campaign_id,idempotency_key),
    FOREIGN KEY(campaign_id,operator_id) REFERENCES mechanism_operators(campaign_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_mechanism_receipts_operator
    ON mechanism_execution_receipts(campaign_id,operator_id,created_at DESC);
"""

_EXPECTED_COLUMNS = {
    "mechanism_operators": [
        ("campaign_id", "TEXT", 1, None, 1), ("id", "TEXT", 1, None, 2),
        ("name", "TEXT", 1, None, 0), ("contract_version", "TEXT", 1, "'MOP-1.0'", 0),
        ("source_kind", "TEXT", 1, "'global'", 0), ("source_id", "TEXT", 0, None, 0),
        ("bindings_json", "TEXT", 1, "'{}'", 0), ("preconditions_json", "TEXT", 1, "'[]'", 0),
        ("costs_json", "TEXT", 1, "'[]'", 0), ("effects_json", "TEXT", 1, "'[]'", 0),
        ("emits_json", "TEXT", 1, "'[]'", 0), ("considerations_json", "TEXT", 1, "'[]'", 0),
        ("planning_preconditions_json", "TEXT", 1, "'{}'", 0),
        ("planning_effects_json", "TEXT", 1, "'{}'", 0),
        ("base_utility", "REAL", 1, "0", 0), ("cost_hours", "REAL", 1, "0", 0),
        ("tags_json", "TEXT", 1, "'[]'", 0), ("enabled", "INTEGER", 1, "1", 0),
        ("metadata_json", "TEXT", 1, "'{}'", 0), ("operator_digest", "TEXT", 1, None, 0),
        ("updated_at", "TEXT", 1, None, 0),
    ],
    "mechanism_execution_receipts": [
        ("campaign_id", "TEXT", 1, None, 1), ("execution_id", "TEXT", 1, None, 2),
        ("operator_id", "TEXT", 1, None, 0), ("idempotency_key", "TEXT", 0, None, 0),
        ("request_digest", "TEXT", 1, None, 0), ("operator_digest", "TEXT", 1, None, 0),
        ("bindings_json", "TEXT", 1, "'{}'", 0), ("evaluation_json", "TEXT", 1, "'{}'", 0),
        ("effect_results_json", "TEXT", 1, "'[]'", 0), ("event_ids_json", "TEXT", 1, "'[]'", 0),
        ("before_revision", "INTEGER", 1, None, 0), ("after_revision", "INTEGER", 1, None, 0),
        ("world_time", "TEXT", 1, None, 0), ("result_json", "TEXT", 1, "'{}'", 0),
        ("result_digest", "TEXT", 1, None, 0), ("created_at", "TEXT", 1, None, 0),
    ],
}

_EXPECTED_FOREIGN_KEYS = {
    "mechanism_operators": {
        (("campaigns", "campaign_id", "id", "NO ACTION", "CASCADE"),),
    },
    "mechanism_execution_receipts": {
        (("campaigns", "campaign_id", "id", "NO ACTION", "CASCADE"),),
        (
            ("mechanism_operators", "campaign_id", "campaign_id", "NO ACTION", "RESTRICT"),
            ("mechanism_operators", "operator_id", "id", "NO ACTION", "RESTRICT"),
        ),
    },
}

_EXPECTED_INDEXES = {
    "mechanism_operators": {(('campaign_id', 'enabled', 'source_kind', 'source_id', 'id'), False)},
    "mechanism_execution_receipts": {
        (("campaign_id", "idempotency_key"), True),
        (("campaign_id", "operator_id", "created_at"), False),
    },
}


def _row_value(row: sqlite3.Row | tuple[Any, ...], key: str, index: int) -> Any:
    return row[key] if isinstance(row, sqlite3.Row) else row[index]


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _index_shapes(db: sqlite3.Connection, table: str) -> set[tuple[tuple[str, ...], bool]]:
    out: set[tuple[tuple[str, ...], bool]] = set()
    for row in db.execute(f'PRAGMA index_list("{table}")').fetchall():
        name = str(_row_value(row, "name", 1))
        unique = bool(_row_value(row, "unique", 2))
        cols = tuple(
            str(_row_value(item, "name", 2))
            for item in db.execute(f'PRAGMA index_info("{name}")').fetchall()
        )
        if cols:
            out.add((cols, unique))
    return out


def _foreign_key_shape(db: sqlite3.Connection, table: str) -> set[tuple[tuple[str, str, str, str, str], ...]]:
    groups: dict[int, list[tuple[int, tuple[str, str, str, str, str]]]] = {}
    for row in db.execute(f'PRAGMA foreign_key_list("{table}")').fetchall():
        key = int(_row_value(row, "id", 0))
        seq = int(_row_value(row, "seq", 1))
        item = (
            str(_row_value(row, "table", 2)), str(_row_value(row, "from", 3)),
            str(_row_value(row, "to", 4)), str(_row_value(row, "on_update", 5)).upper(),
            str(_row_value(row, "on_delete", 6)).upper(),
        )
        groups.setdefault(key, []).append((seq, item))
    return {tuple(item for _, item in sorted(group)) for group in groups.values()}


def _mechanism_table_shape_ok(db: sqlite3.Connection, table: str) -> bool:
    if not _table_exists(db, table):
        return False
    actual_columns = [
        (
            str(_row_value(row, "name", 1)), str(_row_value(row, "type", 2)).upper(),
            int(_row_value(row, "notnull", 3)), _row_value(row, "dflt_value", 4),
            int(_row_value(row, "pk", 5)),
        )
        for row in db.execute(f'PRAGMA table_info("{table}")').fetchall()
    ]
    if actual_columns != _EXPECTED_COLUMNS[table]:
        return False
    if _foreign_key_shape(db, table) != _EXPECTED_FOREIGN_KEYS[table]:
        return False
    if not _EXPECTED_INDEXES[table].issubset(_index_shapes(db, table)):
        return False
    if table == "mechanism_operators":
        row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        sql = re.sub(r"\s+", " ", str(_row_value(row, "sql", 0) or "")).lower()
        if "check(cost_hours >= 0)" not in sql:
            return False
    return True


def _next_legacy_name(db: sqlite3.Connection, table: str) -> str:
    base = f"{table}_legacy_pre_mop1"
    legacy = base
    suffix = 1
    while _table_exists(db, legacy):
        suffix += 1
        legacy = f"{base}_{suffix}"
    return legacy


def prepare_mechanism_schema_db(db: sqlite3.Connection) -> list[dict[str, str]]:
    """Preserve incompatible pre-MOP draft tables before installing MOP-1.0.

    World Engine 4.4.0 did not ship these tables, but development snapshots may
    have used the same names with a different schema. Renaming rather than
    dropping them prevents data loss and lets the canonical schema install
    deterministically. No legacy row is treated as a valid MOP-1.0 operator.
    """

    present = {table: _table_exists(db, table) for table in _EXPECTED_COLUMNS}
    incompatible = any(present[table] and not _mechanism_table_shape_ok(db, table) for table in present)
    if not incompatible:
        return []
    preserved: list[dict[str, str]] = []
    # Receipts must move first. Otherwise SQLite rewrites their composite FK to
    # the renamed operator table and leaves a superficially compatible orphan.
    for table in ("mechanism_execution_receipts", "mechanism_operators"):
        if not present[table]:
            continue
        legacy = _next_legacy_name(db, table)
        db.execute(f'ALTER TABLE "{table}" RENAME TO "{legacy}"')
        preserved.append({"table": table, "preserved_as": legacy})
    # Named indexes are database-global and keep their names after ALTER TABLE.
    # Drop only those derived indexes so CREATE INDEX can attach fresh copies to
    # the canonical tables; preserved rows and automatic constraint indexes stay.
    db.execute("DROP INDEX IF EXISTS idx_mechanism_receipts_operator")
    db.execute("DROP INDEX IF EXISTS idx_mechanism_operators_source")
    return preserved


def verify_mechanism_schema_db(db: sqlite3.Connection) -> None:
    invalid = [table for table in _EXPECTED_COLUMNS if not _mechanism_table_shape_ok(db, table)]
    if invalid:
        raise RuntimeError(f"mechanism schema verification failed: {', '.join(invalid)}")
    violations = []
    for table in _EXPECTED_COLUMNS:
        violations.extend(db.execute(f'PRAGMA foreign_key_check("{table}")').fetchall())
    if violations:
        raise RuntimeError("mechanism schema foreign-key verification failed")

_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
_ROLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_KIND_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")

_ALLOWED_OPERATOR_FIELDS = {
    "id", "name", "contract_version", "source_kind", "source_id", "bindings",
    "preconditions", "costs", "effects", "emits", "considerations",
    "planning_preconditions", "planning_effects", "base_utility", "cost_hours",
    "tags", "enabled", "metadata",
}
_ALLOWED_BINDING_FIELDS = {"required", "kinds", "kind", "default"}
_ALLOWED_PREDICATE_FIELDS = {"all", "any", "not", "read", "op", "value"}
_PREDICATE_OPS = {
    "eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains",
    "not_contains", "starts_with", "ends_with", "truthy", "falsy", "exists",
    "missing", "is_null", "not_null",
}
_READ_SOURCES = {
    "context", "constant", "entity", "need", "inventory", "resource",
    "resource_sum", "world_state", "world_state_any", "relationship", "belief",
    "fact", "environment", "mood", "legacy_belief", "legacy_goal",
}
_EFFECT_OPS = {
    "need.adjust", "inventory.adjust", "resource.adjust", "world_state.set",
    "relationship.adjust", "actor.move", "environment.apply", "fact.assert",
    "belief.set",
}
_COST_OPS = {"need.adjust", "inventory.adjust", "resource.adjust"}
_REL_FIELDS = {"trust", "fear", "respect", "affection"}
_ENTITY_TABLES: dict[str, tuple[str, str]] = {
    "character": ("characters", "name"),
    "npc": ("npcs", "name"),
    "location": ("locations", "name"),
    "faction": ("factions", "name"),
    "quest": ("quests", "title"),
    "item": ("item_defs", "name"),
    "scene": ("scenes", "id"),
    "combat": ("combats", "id"),
    "service": ("town_services", "name"),
    "homestead": ("homesteads", "id"),
}


class _Missing:
    pass


MISSING = _Missing()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _assert_json_limits(value: Any, label: str, *, max_bytes: int) -> None:
    nodes = 0
    pending: list[tuple[Any, int]] = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > MAX_VALUE_NODES:
            raise ValueError(f"{label} exceeds maximum node count {MAX_VALUE_NODES}")
        if depth > MAX_VALUE_DEPTH:
            raise ValueError(f"{label} exceeds maximum depth {MAX_VALUE_DEPTH}")
        if isinstance(item, dict):
            pending.extend((key, depth + 1) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend((child, depth + 1) for child in item)
    try:
        size = len(_canonical(value).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite JSON data") from exc
    if size > max_bytes:
        raise ValueError(f"{label} exceeds maximum encoded size {max_bytes}")


_VOLATILE_KEYS = {"created_at", "updated_at", "applied_at"}


def _stable_projection(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _stable_projection(item)
            for key, item in value.items()
            if str(key) not in _VOLATILE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_stable_projection(item) for item in value]
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite(value: Any, label: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(out):
        raise ValueError(f"{label} must be finite")
    return out


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _value_evidence(value: Any) -> dict[str, Any]:
    if value is MISSING:
        return {"present": False}
    if isinstance(value, sqlite3.Row):
        value = dict(value)
    stable = _stable_projection(value)
    return {
        "present": True,
        "type": type(stable).__name__,
        "digest": _digest(stable),
    }


class MechanismKernel:
    """Shared typed operator contract for existing World Engine kernels.

    Phase 1 deliberately provides only definition, binding, predicate evaluation,
    atomic effect dispatch, event emission, receipts, and compatibility adapters.
    It does not replace DECIDE, install affordance discovery, run GOAP plans,
    evaluate quest graphs, appraise emotions, or generate incidents.
    """

    def __init__(self, engine: "WorldEngine"):
        self.e = engine

    @staticmethod
    def _require_schema_db(db: sqlite3.Connection) -> None:
        verify_mechanism_schema_db(db)

    @staticmethod
    def _binding_refs(context: dict[str, Any]) -> dict[str, Any]:
        refs: dict[str, Any] = {}
        for role, entity in context.items():
            if role in {"world_time", "campaign_id"}:
                continue
            if entity is None:
                refs[role] = None
                continue
            if not isinstance(entity, dict):
                raise RuntimeError(f"invalid bound entity for role {role}")
            ref = {
                "kind": str(entity["kind"]),
                "id": str(entity["id"]),
                "key": str(entity["key"]),
            }
            if entity.get("name") is not None:
                ref["name"] = str(entity["name"])
            refs[role] = ref
        return refs

    # ------------------------------------------------------------------
    # Structural validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_id(value: Any, label: str) -> str:
        text = str(value or "").strip()
        if not _ID_RE.fullmatch(text):
            raise ValueError(f"{label} must be 1-100 characters using letters, digits, _, ., :, or -")
        return text

    @staticmethod
    def _validate_kind(value: Any, label: str) -> str:
        text = str(value or "").strip().lower()
        if not _KIND_RE.fullmatch(text):
            raise ValueError(f"{label} is invalid")
        return text

    @classmethod
    def validate_read_definition(cls, read: Any, *, path: str = "read") -> None:
        if not isinstance(read, dict):
            raise ValueError(f"{path} must be an object")
        source = str(read.get("source") or "").strip()
        if source not in _READ_SOURCES:
            raise ValueError(f"{path}.source is unsupported: {source}")
        allowed_by_source = {
            "context": {"source", "path"},
            "constant": {"source", "value"},
            "entity": {"source", "binding", "field"},
            "need": {"source", "binding", "key", "field", "transform"},
            "inventory": {"source", "binding", "owner_kind", "owner_id", "item_id"},
            "resource": {"source", "node_id", "field"},
            "resource_sum": {"source", "item_id", "location_binding", "location_id", "field"},
            "world_state": {"source", "scope_binding", "scope_type", "scope_id", "key"},
            "world_state_any": {"source", "key"},
            "relationship": {"source", "source_binding", "target_binding", "source_id", "target_id", "field"},
            "belief": {"source", "binding", "believer_key", "fact_id", "field"},
            "fact": {"source", "fact_id", "field", "authority"},
            "environment": {"source", "location_binding", "location_id", "effect_type"},
            "mood": {"source", "binding", "field"},
            "legacy_belief": {"source", "binding", "key", "match"},
            "legacy_goal": {"source", "binding", "key", "match"},
        }
        unknown = set(read) - allowed_by_source[source]
        if unknown:
            raise ValueError(f"{path} contains unsupported fields: {sorted(unknown)}")
        required = {
            "context": ("path",),
            "entity": ("binding", "field"),
            "need": ("binding", "key"),
            "inventory": ("item_id",),
            "resource": ("node_id",),
            "resource_sum": ("item_id",),
            "world_state": ("key",),
            "world_state_any": ("key",),
            "relationship": ("field",),
            "belief": ("fact_id",),
            "fact": ("fact_id", "authority"),
            "environment": ("effect_type",),
            "mood": ("binding",),
            "legacy_belief": ("binding", "key"),
            "legacy_goal": ("binding", "key"),
        }.get(source, ())
        for field in required:
            if read.get(field) in (None, ""):
                raise ValueError(f"{path}.{field} is required")
        if source == "relationship" and str(read.get("field")) not in _REL_FIELDS:
            raise ValueError(f"{path}.field must be one of {sorted(_REL_FIELDS)}")
        if source == "resource" and str(read.get("field", "qty")) not in {"qty", "qty_max", "regen_per_day", "location_id", "item_id"}:
            raise ValueError(f"{path}.field is unsupported")
        if source == "resource_sum" and str(read.get("field", "qty")) not in {"qty", "qty_max", "ratio"}:
            raise ValueError(f"{path}.field must be qty, qty_max, or ratio")
        if source == "need" and str(read.get("field", "value")) not in {"value", "baseline", "drift_per_day", "curve"}:
            raise ValueError(f"{path}.field is unsupported")
        if source == "need" and str(read.get("transform", "none")) not in {"none", "configured_curve"}:
            raise ValueError(f"{path}.transform must be none or configured_curve")
        if source == "mood" and str(read.get("field", "raw")) not in {"raw", "normalized"}:
            raise ValueError(f"{path}.field must be raw or normalized")
        if source == "environment" and str(read.get("effect_type")) not in set(ENVIRONMENT_EFFECT_TYPES) | {"hazard"}:
            raise ValueError(f"{path}.effect_type is unsupported")
        if source == "fact" and str(read.get("authority")) != "system":
            raise ValueError(f"{path}.authority must be system; actor-scoped mechanisms must read beliefs")
        if source in {"entity", "context"}:
            field = str(read.get("field") or read.get("path") or "")
            if set(field.split(".")) & _VOLATILE_KEYS:
                raise ValueError(f"{path} cannot read wall-clock metadata")
        if source in {"legacy_belief", "legacy_goal"} and str(read.get("match", "exact")) not in {"exact", "contains", "prefix"}:
            raise ValueError(f"{path}.match must be exact, contains, or prefix")

    @classmethod
    def validate_predicate_definition(
        cls,
        predicate: Any,
        *,
        path: str = "preconditions",
        depth: int = 0,
        counter: list[int] | None = None,
    ) -> None:
        if predicate in (None, [], {}):
            return
        if depth > MAX_PREDICATE_DEPTH:
            raise ValueError(f"{path} exceeds maximum depth {MAX_PREDICATE_DEPTH}")
        counter = counter if counter is not None else [0]
        if isinstance(predicate, list):
            for i, child in enumerate(predicate):
                cls.validate_predicate_definition(child, path=f"{path}[{i}]", depth=depth + 1, counter=counter)
            return
        if not isinstance(predicate, dict):
            raise ValueError(f"{path} must be an object or list")
        unknown = set(predicate) - _ALLOWED_PREDICATE_FIELDS
        if unknown:
            raise ValueError(f"{path} contains unsupported fields: {sorted(unknown)}")
        structural = [key for key in ("all", "any", "not", "read") if key in predicate]
        if len(structural) != 1:
            raise ValueError(f"{path} must contain exactly one of all, any, not, or read")
        key = structural[0]
        if key in {"all", "any"}:
            children = predicate[key]
            if not isinstance(children, list) or not children:
                raise ValueError(f"{path}.{key} must be a non-empty list")
            for i, child in enumerate(children):
                cls.validate_predicate_definition(child, path=f"{path}.{key}[{i}]", depth=depth + 1, counter=counter)
            return
        if key == "not":
            cls.validate_predicate_definition(predicate["not"], path=f"{path}.not", depth=depth + 1, counter=counter)
            return
        counter[0] += 1
        if counter[0] > MAX_PREDICATE_LEAVES:
            raise ValueError(f"{path} exceeds maximum leaf count {MAX_PREDICATE_LEAVES}")
        cls.validate_read_definition(predicate["read"], path=f"{path}.read")
        op = str(predicate.get("op", "eq"))
        if op not in _PREDICATE_OPS:
            raise ValueError(f"{path}.op is unsupported: {op}")
        if op not in {"truthy", "falsy", "exists", "missing", "is_null", "not_null"} and "value" not in predicate:
            raise ValueError(f"{path}.value is required for {op}")

    @classmethod
    def validate_effect_definition(cls, effect: Any, *, path: str, cost: bool = False) -> None:
        if not isinstance(effect, dict):
            raise ValueError(f"{path} must be an object")
        op = str(effect.get("op") or "")
        allowed = _COST_OPS if cost else _EFFECT_OPS
        if op not in allowed:
            raise ValueError(f"{path}.op is unsupported: {op}")
        common = {"op", "reason", "metadata"}
        fields = {
            "need.adjust": common | {"binding", "npc_id", "need", "delta"},
            "inventory.adjust": common | {"binding", "owner_kind", "owner_id", "item_id", "delta"},
            "resource.adjust": common | {"node_id", "delta", "allow_overflow"},
            "world_state.set": common | {"scope_binding", "scope_type", "scope_id", "key", "value"},
            "relationship.adjust": common | {"source_binding", "target_binding", "source_id", "target_id", "trust_delta", "fear_delta", "respect_delta", "affection_delta"},
            "actor.move": common | {"binding", "kind", "actor_id", "location_binding", "location_id"},
            "environment.apply": common | {"target_binding", "target_key", "target_type", "target_id", "effect_type", "intensity", "amount", "source_key", "state"},
            "fact.assert": common | {"fact_id", "subject_binding", "subject_key", "predicate", "object_type", "value", "confidence", "status", "source_event_id", "valid_from", "valid_to", "provenance"},
            "belief.set": common | {"binding", "believer_key", "fact_id", "value", "confidence", "source_binding", "source_key", "status", "provenance"},
        }[op]
        unknown = set(effect) - fields
        if unknown:
            raise ValueError(f"{path} contains unsupported fields: {sorted(unknown)}")
        required = {
            "need.adjust": ("need", "delta"),
            "inventory.adjust": ("item_id", "delta"),
            "resource.adjust": ("node_id", "delta"),
            "world_state.set": ("key", "value"),
            "relationship.adjust": (),
            "actor.move": (),
            "environment.apply": ("effect_type",),
            "fact.assert": ("fact_id", "predicate", "value"),
            "belief.set": ("fact_id", "value"),
        }[op]
        for field in required:
            if field not in effect or effect.get(field) in (None, ""):
                raise ValueError(f"{path}.{field} is required")
        for field in (
            "delta", "trust_delta", "fear_delta", "respect_delta", "affection_delta",
            "intensity", "amount", "confidence",
        ):
            if field in effect:
                _finite(effect[field], f"{path}.{field}")
        if cost and op in {"inventory.adjust", "resource.adjust"} and float(effect.get("delta", 0)) > 0:
            raise ValueError(f"{path}.delta must be <=0 for a cost")
        if op == "environment.apply" and str(effect.get("effect_type")) not in ENVIRONMENT_EFFECT_TYPES:
            raise ValueError(f"{path}.effect_type is unsupported")
        if op == "fact.assert" and str(effect.get("object_type", "literal")) not in {"literal", "entity"}:
            raise ValueError(f"{path}.object_type must be literal or entity")
        if op == "fact.assert" and str(effect.get("status", "active")) not in {"active", "disputed", "retracted"}:
            raise ValueError(f"{path}.status is invalid")
        if op == "belief.set" and str(effect.get("status", "believes")) not in {"believes", "doubts", "rejects", "unknown"}:
            raise ValueError(f"{path}.status is invalid")

    @classmethod
    def _validate_public_references(cls, value: Any, roles: set[str], path: str) -> None:
        pending: list[tuple[Any, str]] = [(value, path)]
        allowed_fields = {"kind", "id", "key", "name"}
        while pending:
            item, item_path = pending.pop()
            if isinstance(item, dict):
                pending.extend((child, f"{item_path}.{key}") for key, child in item.items())
            elif isinstance(item, list):
                pending.extend((child, f"{item_path}[{index}]") for index, child in enumerate(item))
            elif isinstance(item, str) and item.startswith("$"):
                ref = item[1:].split(".")
                if ref == ["world_time"] or ref == ["campaign_id"]:
                    continue
                if len(ref) != 2 or ref[0] not in roles or ref[1] not in allowed_fields:
                    raise ValueError(f"{item_path} may reference only stable binding fields")

    @classmethod
    def validate_operator_document(cls, document: Any) -> dict[str, Any]:
        if not isinstance(document, dict):
            raise ValueError("operator must be an object")
        _assert_json_limits(document, "operator", max_bytes=MAX_OPERATOR_BYTES)
        unknown = set(document) - _ALLOWED_OPERATOR_FIELDS
        if unknown:
            raise ValueError(f"operator contains unsupported fields: {sorted(unknown)}")
        operator_id = cls._validate_id(document.get("id"), "operator.id")
        name = str(document.get("name") or operator_id).strip()
        if not name or len(name) > 300:
            raise ValueError("operator.name must be 1-300 characters")
        contract_version = str(document.get("contract_version") or CONTRACT_VERSION)
        if contract_version != CONTRACT_VERSION:
            raise ValueError(f"operator.contract_version must be {CONTRACT_VERSION}")
        source_kind = cls._validate_kind(document.get("source_kind", "global"), "operator.source_kind")
        source_id = document.get("source_id")
        if source_id is not None:
            source_id = cls._validate_id(source_id, "operator.source_id")
        bindings = document.get("bindings") or {}
        if not isinstance(bindings, dict) or len(bindings) > MAX_BINDINGS:
            raise ValueError(f"operator.bindings must be an object with at most {MAX_BINDINGS} entries")
        normalized_bindings: dict[str, Any] = {}
        for role, spec in bindings.items():
            if not _ROLE_RE.fullmatch(str(role)):
                raise ValueError(f"invalid binding role: {role}")
            if not isinstance(spec, dict):
                raise ValueError(f"operator.bindings.{role} must be an object")
            unknown_binding = set(spec) - _ALLOWED_BINDING_FIELDS
            if unknown_binding:
                raise ValueError(f"operator.bindings.{role} contains unsupported fields: {sorted(unknown_binding)}")
            kinds_raw = spec.get("kinds", [spec.get("kind")] if spec.get("kind") else [])
            if not isinstance(kinds_raw, list) or not kinds_raw:
                raise ValueError(f"operator.bindings.{role}.kinds must be a non-empty list")
            kinds = sorted({cls._validate_kind(x, f"operator.bindings.{role}.kinds") for x in kinds_raw})
            normalized_bindings[str(role)] = {
                "required": bool(spec.get("required", True)),
                "kinds": kinds,
                **({"default": spec["default"]} if "default" in spec else {}),
            }
        preconditions = document.get("preconditions") or []
        cls.validate_predicate_definition(preconditions, path="operator.preconditions")
        costs = list(document.get("costs") or [])
        effects = list(document.get("effects") or [])
        if len(costs) + len(effects) > MAX_TRANSITION_EFFECTS:
            raise ValueError(f"operator transition exceeds {MAX_TRANSITION_EFFECTS} effects")
        for i, effect in enumerate(costs):
            cls.validate_effect_definition(effect, path=f"operator.costs[{i}]", cost=True)
        for i, effect in enumerate(effects):
            cls.validate_effect_definition(effect, path=f"operator.effects[{i}]", cost=False)
        emits = list(document.get("emits") or [])
        if len(emits) > MAX_EMITS:
            raise ValueError(f"operator.emits exceeds {MAX_EMITS}")
        for i, emit in enumerate(emits):
            if not isinstance(emit, dict):
                raise ValueError(f"operator.emits[{i}] must be an object")
            unknown_emit = set(emit) - {"event_type", "summary", "region", "actor_id", "target_id", "payload"}
            if unknown_emit:
                raise ValueError(f"operator.emits[{i}] contains unsupported fields: {sorted(unknown_emit)}")
            if not str(emit.get("event_type") or "").strip():
                raise ValueError(f"operator.emits[{i}].event_type is required")
            cls._validate_public_references(emit, set(normalized_bindings), f"operator.emits[{i}]")
        considerations = list(document.get("considerations") or [])
        if len(considerations) > MAX_CONSIDERATIONS:
            raise ValueError(f"operator.considerations exceeds {MAX_CONSIDERATIONS}")
        for i, consideration in enumerate(considerations):
            if not isinstance(consideration, dict):
                raise ValueError(f"operator.considerations[{i}] must be an object")
            unknown_consideration = set(consideration) - {"read", "weight", "min", "max", "invert", "equals", "label"}
            if unknown_consideration:
                raise ValueError(f"operator.considerations[{i}] contains unsupported fields: {sorted(unknown_consideration)}")
            cls.validate_read_definition(consideration.get("read"), path=f"operator.considerations[{i}].read")
            _finite(consideration.get("weight", 1), f"operator.considerations[{i}].weight")
        planning_preconditions = document.get("planning_preconditions") or {}
        planning_effects = document.get("planning_effects") or {}
        cls._validate_planning_mapping(planning_preconditions, effects=False, path="operator.planning_preconditions")
        cls._validate_planning_mapping(planning_effects, effects=True, path="operator.planning_effects")
        base_utility = _finite(document.get("base_utility", 0), "operator.base_utility")
        cost_hours = _finite(document.get("cost_hours", 0), "operator.cost_hours")
        if cost_hours < 0:
            raise ValueError("operator.cost_hours must be >=0")
        tags = document.get("tags") or []
        if not isinstance(tags, list) or any(not isinstance(x, str) for x in tags):
            raise ValueError("operator.tags must be a list of strings")
        if len(tags) > MAX_TAGS:
            raise ValueError(f"operator.tags exceeds {MAX_TAGS}")
        metadata = document.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("operator.metadata must be an object")
        normalized = {
            "id": operator_id,
            "name": name,
            "contract_version": contract_version,
            "source_kind": source_kind,
            "source_id": source_id,
            "bindings": normalized_bindings,
            "preconditions": preconditions,
            "costs": costs,
            "effects": effects,
            "emits": emits,
            "considerations": considerations,
            "planning_preconditions": planning_preconditions,
            "planning_effects": planning_effects,
            "base_utility": base_utility,
            "cost_hours": cost_hours,
            "tags": sorted(set(tags)),
            "enabled": bool(document.get("enabled", True)),
            "metadata": metadata,
        }
        _assert_json_limits(normalized, "normalized operator", max_bytes=MAX_OPERATOR_BYTES)
        normalized["operator_digest"] = _digest(normalized)
        return normalized

    @staticmethod
    def _validate_planning_mapping(values: Any, *, effects: bool, path: str) -> None:
        if not isinstance(values, dict):
            raise ValueError(f"{path} must be an object")
        allowed = {"set", "delta"} if effects else {"ge", "gt", "le", "lt", "eq"}
        for key, value in values.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{path} keys must be non-empty strings")
            if not isinstance(value, dict):
                if effects:
                    continue
                continue
            unknown = set(value) - allowed
            if unknown:
                raise ValueError(f"{path}.{key} contains unsupported fields: {sorted(unknown)}")
            if effects and len(set(value) & allowed) != 1:
                raise ValueError(f"{path}.{key} must contain exactly one of set or delta")
            if not effects and not (set(value) & allowed):
                raise ValueError(f"{path}.{key} must contain a comparison")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_operator_db(self, db: sqlite3.Connection, campaign_id: str, document: dict[str, Any]) -> dict[str, Any]:
        self._require_schema_db(db)
        # Adapters and the authoring validator may pass an already-normalized
        # document. ``operator_digest`` is derived, never caller-authoritative;
        # discard it and recompute from the canonical payload on every save.
        candidate = dict(document)
        candidate.pop("operator_digest", None)
        op = self.validate_operator_document(candidate)
        db.execute(
            """INSERT INTO mechanism_operators(
                   campaign_id,id,name,contract_version,source_kind,source_id,bindings_json,
                   preconditions_json,costs_json,effects_json,emits_json,considerations_json,
                   planning_preconditions_json,planning_effects_json,base_utility,cost_hours,
                   tags_json,enabled,metadata_json,operator_digest,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(campaign_id,id) DO UPDATE SET
                   name=excluded.name,contract_version=excluded.contract_version,
                   source_kind=excluded.source_kind,source_id=excluded.source_id,
                   bindings_json=excluded.bindings_json,preconditions_json=excluded.preconditions_json,
                   costs_json=excluded.costs_json,effects_json=excluded.effects_json,
                   emits_json=excluded.emits_json,considerations_json=excluded.considerations_json,
                   planning_preconditions_json=excluded.planning_preconditions_json,
                   planning_effects_json=excluded.planning_effects_json,
                   base_utility=excluded.base_utility,cost_hours=excluded.cost_hours,
                   tags_json=excluded.tags_json,enabled=excluded.enabled,
                   metadata_json=excluded.metadata_json,operator_digest=excluded.operator_digest,
                   updated_at=excluded.updated_at""",
            (
                campaign_id, op["id"], op["name"], op["contract_version"], op["source_kind"], op["source_id"],
                self.e._dumps(op["bindings"]), self.e._dumps(op["preconditions"]), self.e._dumps(op["costs"]),
                self.e._dumps(op["effects"]), self.e._dumps(op["emits"]), self.e._dumps(op["considerations"]),
                self.e._dumps(op["planning_preconditions"]), self.e._dumps(op["planning_effects"]),
                op["base_utility"], op["cost_hours"], self.e._dumps(op["tags"]), int(op["enabled"]),
                self.e._dumps(op["metadata"]), op["operator_digest"], self.e._now(),
            ),
        )
        return op

    def save_operator(self, campaign_id: str, document: dict[str, Any]) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        with self.e._write_db() as db:
            op = self._save_operator_db(db, campaign_id, document)
            revision = self.e._next_revision(db, campaign_id)
            event_id = self.e._insert_event(
                db, campaign_id, revision, "mechanism_operator_saved",
                f"Mechanism operator saved: {op['name']}",
                actor_id=op["id"],
                payload={"operator_id": op["id"], "operator_digest": op["operator_digest"], "contract_version": CONTRACT_VERSION},
            )
        out = self.get_operator(campaign_id, op["id"])
        out.update({"revision": revision, "event_id": event_id})
        return out

    def _decode_operator(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        for field in (
            "bindings", "preconditions", "costs", "effects", "emits", "considerations",
            "planning_preconditions", "planning_effects", "tags", "metadata",
        ):
            out[field] = self.e._loads(out.pop(field + "_json"))
        out["enabled"] = bool(out["enabled"])
        return out

    def get_operator(self, campaign_id: str, operator_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            row = db.execute(
                "SELECT * FROM mechanism_operators WHERE campaign_id=? AND id=?",
                (campaign_id, operator_id),
            ).fetchone()
        if not row:
            raise KeyError(f"unknown mechanism operator: {operator_id}")
        return self._decode_operator(row)

    def list_operators(self, campaign_id: str, *, enabled_only: bool = False, limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM mechanism_operators WHERE campaign_id=?"
        params: list[Any] = [campaign_id]
        if enabled_only:
            sql += " AND enabled=1"
        sql += " ORDER BY source_kind,source_id,id LIMIT ?"
        params.append(max(1, min(int(limit), MAX_QUERY_LIMIT)))
        with self.e._db() as db:
            self._require_schema_db(db)
            rows = db.execute(sql, params).fetchall()
        return [self._decode_operator(row) for row in rows]

    # ------------------------------------------------------------------
    # Binding and reads
    # ------------------------------------------------------------------

    @classmethod
    def _dig(cls, value: Any, path: str | Sequence[str]) -> Any:
        parts = path.split(".") if isinstance(path, str) else list(path)
        current = value
        for part in parts:
            if part == "":
                continue
            if isinstance(current, sqlite3.Row):
                current = dict(current)
            if isinstance(current, dict):
                if part not in current:
                    return MISSING
                current = current[part]
            elif isinstance(current, (list, tuple)) and part.isdigit():
                index = int(part)
                if index >= len(current):
                    return MISSING
                current = current[index]
            else:
                return MISSING
        return current

    def _resolve_ref(self, value: Any, context: dict[str, Any]) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            resolved = self._dig(context, value[1:])
            return None if resolved is MISSING else resolved
        if isinstance(value, dict):
            return {key: self._resolve_ref(item, context) for key, item in value.items()}
        if isinstance(value, list):
            return [self._resolve_ref(item, context) for item in value]
        return value

    def _entity_db(self, db: sqlite3.Connection, campaign_id: str, kind: str, entity_id: str) -> dict[str, Any] | None:
        kind = str(kind).lower()
        if kind == "world" and entity_id in {campaign_id, "global"}:
            campaign = db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
            if not campaign:
                return None
            data = dict(campaign)
            data["settings"] = self.e._loads(data.pop("settings_json"))
            data.update({"kind": "world", "id": campaign_id, "key": f"world:{campaign_id}"})
            return data
        table_spec = _ENTITY_TABLES.get(kind)
        if table_spec:
            try:
                row = db.execute(f"SELECT * FROM {table_spec[0]} WHERE campaign_id=? AND id=?", (campaign_id, entity_id)).fetchone()
            except sqlite3.OperationalError:
                row = None
            if row:
                data = dict(row)
                for key in list(data):
                    if key.endswith("_json"):
                        try:
                            data[key[:-5]] = self.e._loads(data[key])
                        except (TypeError, json.JSONDecodeError):
                            pass
                data.update({"kind": kind, "id": entity_id, "key": f"{kind}:{entity_id}", "name": str(row[table_spec[1]])})
                return data
        row = db.execute(
            "SELECT * FROM we4_entities WHERE campaign_id=? AND entity_type=? AND entity_id=? AND status<>'deleted'",
            (campaign_id, kind, entity_id),
        ).fetchone()
        if not row:
            return None
        return {
            "kind": kind,
            "id": entity_id,
            "key": row["entity_key"],
            "name": row["canonical_name"],
            "status": row["status"],
            "components": self.e._loads(row["components_json"]),
        }

    def _parse_entity_ref(self, value: Any, allowed_kinds: Sequence[str], context: dict[str, Any]) -> tuple[str, str]:
        value = self._resolve_ref(value, context)
        if isinstance(value, dict) and {"kind", "id"}.issubset(value):
            kind, entity_id = str(value["kind"]).lower(), str(value["id"])
        elif isinstance(value, dict):
            kind = str(value.get("kind") or value.get("type") or value.get("entity_type") or "").lower()
            entity_id = str(value.get("id") or value.get("entity_id") or "")
        elif isinstance(value, str) and ":" in value:
            kind, entity_id = value.split(":", 1)
            kind = kind.lower()
        elif len(allowed_kinds) == 1:
            kind, entity_id = allowed_kinds[0], str(value or "")
        else:
            raise ValueError("binding reference must include an entity kind")
        if kind not in allowed_kinds:
            raise ValueError(f"binding kind {kind!r} is not allowed; expected {list(allowed_kinds)}")
        self._validate_id(entity_id, "binding entity id")
        return kind, entity_id

    def _bind_operator_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        operator: dict[str, Any],
        supplied: dict[str, Any] | None,
    ) -> dict[str, Any]:
        supplied = dict(supplied or {})
        specs = operator["bindings"]
        unknown = set(supplied) - set(specs)
        if unknown:
            raise ValueError(f"undeclared bindings supplied: {sorted(unknown)}")
        context: dict[str, Any] = {}
        pending = dict(specs)
        errors: dict[str, str] = {}
        for _ in range(len(pending) + 1):
            progressed = False
            for role in list(pending):
                spec = pending[role]
                raw = supplied.get(role, spec.get("default", MISSING))
                if raw is MISSING:
                    if spec["required"]:
                        errors[role] = "required binding missing"
                    else:
                        context[role] = None
                        del pending[role]
                        progressed = True
                    continue
                try:
                    kind, entity_id = self._parse_entity_ref(raw, spec["kinds"], context)
                except ValueError as exc:
                    # A default may depend on a role that has not been bound yet.
                    if isinstance(raw, (dict, list, str)) and "$" in _canonical(raw) and len(pending) > 1:
                        continue
                    errors[role] = str(exc)
                    continue
                entity = self._entity_db(db, campaign_id, kind, entity_id)
                if entity is None:
                    errors[role] = f"unknown {kind}: {entity_id}"
                    continue
                context[role] = entity
                del pending[role]
                errors.pop(role, None)
                progressed = True
            if not pending or not progressed:
                break
        if pending:
            for role in pending:
                errors.setdefault(role, "binding could not be resolved")
        if errors:
            raise ValueError("; ".join(f"{role}: {message}" for role, message in sorted(errors.items())))
        campaign = db.execute("SELECT world_time,revision FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if not campaign:
            raise KeyError(f"unknown campaign: {campaign_id}")
        context["world_time"] = campaign["world_time"]
        context["campaign_id"] = campaign_id
        return context

    def bind_operator(self, campaign_id: str, operator_id: str, bindings: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.e._db() as db:
            operator = self._get_operator_db(db, campaign_id, operator_id)
            context = self._bind_operator_db(db, campaign_id, operator, bindings)
        return self._binding_refs(context)

    def _get_operator_db(self, db: sqlite3.Connection, campaign_id: str, operator_id: str) -> dict[str, Any]:
        self._require_schema_db(db)
        row = db.execute("SELECT * FROM mechanism_operators WHERE campaign_id=? AND id=?", (campaign_id, operator_id)).fetchone()
        if not row:
            raise KeyError(f"unknown mechanism operator: {operator_id}")
        return self._decode_operator(row)

    def _binding_entity(self, context: dict[str, Any], role: str | None) -> dict[str, Any] | None:
        if not role:
            return None
        value = context.get(str(role))
        return value if isinstance(value, dict) else None

    def _read_value_db(self, db: sqlite3.Connection, campaign_id: str, read: dict[str, Any], context: dict[str, Any]) -> Any:
        source = read["source"]
        if source == "constant":
            return self._resolve_ref(read.get("value"), context)
        if source == "context":
            return self._dig(context, str(read["path"]))
        if source == "entity":
            entity = self._binding_entity(context, str(read["binding"]))
            return self._dig(entity, str(read["field"])) if entity else MISSING
        if source == "need":
            entity = self._binding_entity(context, str(read["binding"]))
            if not entity or entity.get("kind") != "npc":
                return MISSING
            field = str(read.get("field", "value"))
            row = db.execute(
                "SELECT value,baseline,drift_per_day,curve FROM npc_needs WHERE campaign_id=? AND npc_id=? AND need=?",
                (campaign_id, entity["id"], self._resolve_ref(read["key"], context)),
            ).fetchone()
            if not row:
                return MISSING
            if str(read.get("transform", "none")) == "configured_curve":
                value = _clamp(float(row["value"]) / 100.0, 0.0, 1.0)
                curve = str(row["curve"] or "quadratic")
                if curve == "linear":
                    return value
                if curve == "urgent":
                    return math.sqrt(value)
                if curve == "threshold":
                    return 0.0 if value < 0.60 else (value - 0.60) / 0.40
                return value * value
            return row[field]
        if source == "inventory":
            entity = self._binding_entity(context, read.get("binding"))
            owner_kind = str(self._resolve_ref(read.get("owner_kind"), context) or (entity or {}).get("kind") or "")
            owner_id = str(self._resolve_ref(read.get("owner_id"), context) or (entity or {}).get("id") or "")
            item_id = str(self._resolve_ref(read["item_id"], context))
            row = db.execute(
                "SELECT qty FROM inventories WHERE campaign_id=? AND owner_kind=? AND owner_id=? AND item_id=?",
                (campaign_id, owner_kind, owner_id, item_id),
            ).fetchone()
            return float(row["qty"]) if row else 0.0
        if source == "resource":
            node_id = str(self._resolve_ref(read["node_id"], context))
            field = str(read.get("field", "qty"))
            row = db.execute(f"SELECT {field} AS value FROM resource_nodes WHERE campaign_id=? AND id=?", (campaign_id, node_id)).fetchone()
            return row["value"] if row else MISSING
        if source == "resource_sum":
            item_id = str(self._resolve_ref(read["item_id"], context))
            location_entity = self._binding_entity(context, read.get("location_binding"))
            location_id = str(self._resolve_ref(read.get("location_id"), context) or (location_entity or {}).get("id") or "")
            field = str(read.get("field", "qty"))
            row = db.execute(
                "SELECT COALESCE(SUM(qty),0) AS qty,COALESCE(SUM(qty_max),0) AS qty_max FROM resource_nodes WHERE campaign_id=? AND item_id=? AND location_id=?",
                (campaign_id, item_id, location_id),
            ).fetchone()
            if not row:
                return 0.0
            if field == "ratio":
                qty_max = float(row["qty_max"])
                return float(row["qty"]) / qty_max if qty_max > 0 else 0.0
            return float(row[field])
        if source == "world_state":
            scope = self._binding_entity(context, read.get("scope_binding"))
            scope_type = str(self._resolve_ref(read.get("scope_type"), context) or (scope or {}).get("kind") or "world")
            scope_id = str(self._resolve_ref(read.get("scope_id"), context) or (scope or {}).get("id") or "global")
            key = str(self._resolve_ref(read["key"], context))
            row = db.execute(
                "SELECT value_json FROM world_state WHERE campaign_id=? AND scope_type=? AND scope_id=? AND state_key=?",
                (campaign_id, scope_type, scope_id, key),
            ).fetchone()
            return self.e._loads(row["value_json"]) if row else MISSING
        if source == "world_state_any":
            key = str(self._resolve_ref(read["key"], context))
            row = db.execute(
                "SELECT value_json FROM world_state WHERE campaign_id=? AND state_key=? ORDER BY scope_type,scope_id LIMIT 1",
                (campaign_id, key),
            ).fetchone()
            return self.e._loads(row["value_json"]) if row else MISSING
        if source == "relationship":
            source_entity = self._binding_entity(context, read.get("source_binding"))
            target_entity = self._binding_entity(context, read.get("target_binding"))
            source_id = str(self._resolve_ref(read.get("source_id"), context) or (source_entity or {}).get("id") or "")
            target_id = str(self._resolve_ref(read.get("target_id"), context) or (target_entity or {}).get("id") or "")
            field = str(read["field"])
            row = db.execute(
                f"SELECT {field} AS value FROM relationships WHERE campaign_id=? AND source_id=? AND target_id=?",
                (campaign_id, source_id, target_id),
            ).fetchone()
            return float(row["value"]) if row else 0.0
        if source == "belief":
            believer = self._binding_entity(context, read.get("binding"))
            believer_key = str(self._resolve_ref(read.get("believer_key"), context) or (believer or {}).get("key") or "")
            fact_id = str(self._resolve_ref(read["fact_id"], context))
            row = db.execute(
                "SELECT * FROM we4_beliefs WHERE campaign_id=? AND believer_key=? AND fact_id=?",
                (campaign_id, believer_key, fact_id),
            ).fetchone()
            if not row:
                return MISSING
            data = dict(row)
            data["value"] = self.e._loads(data.pop("belief_value_json"))
            data["provenance"] = self.e._loads(data.pop("provenance_json"))
            field = str(read.get("field", "value"))
            return self._dig(data, field)
        if source == "fact":
            if str(read.get("authority")) != "system":
                raise PermissionError("direct fact reads require system authority")
            fact_id = str(self._resolve_ref(read["fact_id"], context))
            row = db.execute("SELECT * FROM we4_facts WHERE campaign_id=? AND fact_id=?", (campaign_id, fact_id)).fetchone()
            if not row:
                return MISSING
            data = dict(row)
            data["value"] = self.e._loads(data.pop("object_value_json"))
            data["provenance"] = self.e._loads(data.pop("provenance_json"))
            return self._dig(data, str(read.get("field", "value")))
        if source == "environment":
            location = self._binding_entity(context, read.get("location_binding"))
            location_id = str(self._resolve_ref(read.get("location_id"), context) or (location or {}).get("id") or "")
            effect_type = str(read["effect_type"])
            kernel = EnvironmentKernel(self.e)
            if effect_type == "hazard":
                return max(kernel.consideration_value_db(db, campaign_id, location_id, item) for item in ("fire", "smoke", "water", "gas", "blight", "corruption", "heat", "cold"))
            return kernel.consideration_value_db(db, campaign_id, location_id, effect_type)
        if source == "mood":
            entity = self._binding_entity(context, str(read["binding"]))
            if not entity or entity.get("kind") != "npc":
                return MISSING
            try:
                row = db.execute(
                    """SELECT COALESCE(SUM(mood_delta),0) AS mood
                       FROM npc_thoughts
                       WHERE campaign_id=? AND npc_id=? AND active=1
                         AND (expires_world_time IS NULL OR expires_world_time>(SELECT world_time FROM campaigns WHERE id=?))""",
                    (campaign_id, entity["id"], campaign_id),
                ).fetchone()
                raw = _clamp(float(row["mood"] if row else 0.0), -100.0, 100.0)
            except sqlite3.OperationalError:
                raw = 0.0
            if str(read.get("field", "raw")) == "normalized":
                return (raw + 100.0) / 200.0
            return raw
        if source in {"legacy_belief", "legacy_goal"}:
            entity = self._binding_entity(context, str(read["binding"]))
            if not entity or entity.get("kind") != "npc":
                return False
            field = "beliefs_json" if source == "legacy_belief" else "goals_json"
            row = db.execute(f"SELECT {field} AS values_json FROM npcs WHERE campaign_id=? AND id=?", (campaign_id, entity["id"])).fetchone()
            values = [str(x).strip().casefold() for x in self.e._loads(row["values_json"] or "[]")] if row else []
            key = str(self._resolve_ref(read["key"], context)).strip().casefold()
            match = str(read.get("match", "exact"))
            if match == "contains":
                return bool(key and any(key in value for value in values))
            if match == "prefix":
                return bool(key and any(value.startswith(key) for value in values))
            return bool(key and key in values)
        raise ValueError(f"unsupported read source: {source}")

    # ------------------------------------------------------------------
    # Predicate evaluation
    # ------------------------------------------------------------------

    @staticmethod
    def _compare(have: Any, op: str, want: Any = None) -> bool:
        if op in {"exists", "not_null"}:
            return have is not MISSING and have is not None
        if op in {"missing", "is_null"}:
            return have is MISSING or have is None
        if op == "truthy":
            return have is not MISSING and bool(have)
        if op == "falsy":
            return have is MISSING or not bool(have)
        if have is MISSING:
            return False
        if op == "eq":
            return have == want
        if op == "ne":
            return have != want
        if op in {"gt", "gte", "lt", "lte"}:
            try:
                left, right = float(have), float(want)
            except (TypeError, ValueError):
                return False
            return {"gt": left > right, "gte": left >= right, "lt": left < right, "lte": left <= right}[op]
        if op == "in":
            return have in want if isinstance(want, (list, tuple, set, dict, str)) else False
        if op == "not_in":
            return have not in want if isinstance(want, (list, tuple, set, dict, str)) else True
        if op == "contains":
            try:
                return want in have
            except TypeError:
                return False
        if op == "not_contains":
            try:
                return want not in have
            except TypeError:
                return True
        if op == "starts_with":
            return str(have).startswith(str(want))
        if op == "ends_with":
            return str(have).endswith(str(want))
        raise ValueError(f"unsupported predicate op: {op}")

    def _evaluate_predicate_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        predicate: Any,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if predicate in (None, [], {}):
            return {"kind": "empty", "passed": True}
        if isinstance(predicate, list):
            children = [self._evaluate_predicate_db(db, campaign_id, child, context) for child in predicate]
            return {"kind": "all", "passed": all(child["passed"] for child in children), "children": children}
        if "all" in predicate:
            children = [self._evaluate_predicate_db(db, campaign_id, child, context) for child in predicate["all"]]
            return {"kind": "all", "passed": all(child["passed"] for child in children), "children": children}
        if "any" in predicate:
            children = [self._evaluate_predicate_db(db, campaign_id, child, context) for child in predicate["any"]]
            return {"kind": "any", "passed": any(child["passed"] for child in children), "children": children}
        if "not" in predicate:
            child = self._evaluate_predicate_db(db, campaign_id, predicate["not"], context)
            return {"kind": "not", "passed": not child["passed"], "child": child}
        read = predicate["read"]
        op = str(predicate.get("op", "eq"))
        have = self._read_value_db(db, campaign_id, read, context)
        want = self._resolve_ref(predicate.get("value"), context)
        return {
            "kind": "leaf",
            "passed": self._compare(have, op, want),
            "read": read,
            "op": op,
            "evidence": _value_evidence(have),
            **({"expected": _value_evidence(want)} if "value" in predicate else {}),
        }

    def _evaluate_considerations_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        operator: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        components: list[dict[str, Any]] = []
        score = float(operator.get("base_utility", 0.0))
        for index, consideration in enumerate(operator.get("considerations") or []):
            have = self._read_value_db(db, campaign_id, consideration["read"], context)
            if "equals" in consideration:
                normalized = 1.0 if have is not MISSING and have == self._resolve_ref(consideration["equals"], context) else 0.0
            elif have is MISSING or have is None:
                normalized = 0.0
            elif isinstance(have, bool):
                normalized = 1.0 if have else 0.0
            else:
                lo = _finite(consideration.get("min", 0.0), f"consideration[{index}].min")
                hi = _finite(consideration.get("max", 1.0), f"consideration[{index}].max")
                if hi == lo:
                    normalized = 0.0
                else:
                    normalized = (float(have) - lo) / (hi - lo)
                normalized = _clamp(normalized, 0.0, 1.0)
            if bool(consideration.get("invert", False)):
                normalized = 1.0 - normalized
            weight = _finite(consideration.get("weight", 1.0), f"consideration[{index}].weight")
            contribution = normalized * weight
            score += contribution
            components.append({
                "index": index,
                "label": str(consideration.get("label") or consideration["read"].get("source") or index),
                "read": consideration["read"],
                "evidence": _value_evidence(have),
                "normalized": round(normalized, 12),
                "weight": weight,
                "contribution": round(contribution, 12),
            })
        return {
            "base_utility": float(operator.get("base_utility", 0.0)),
            "score": score,
            "components": components,
        }

    # ------------------------------------------------------------------
    # Effect preflight and execution
    # ------------------------------------------------------------------

    def _effect_target_entity(self, context: dict[str, Any], binding: Any) -> dict[str, Any] | None:
        return self._binding_entity(context, str(binding)) if binding else None

    def _effect_callback(self):
        callback = getattr(self.e, "_mechanism_apply_effect_db", None)
        if not callable(callback):
            raise RuntimeError("mechanism effect callback is unavailable")
        return callback

    @staticmethod
    def _private_bindings(context: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in context.items() if key not in {"world_time", "campaign_id"}}

    def _preflight_effects_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        effects: Sequence[dict[str, Any]],
        context: dict[str, Any],
        *,
        operator_id: str,
    ) -> dict[str, Any]:
        if not effects:
            return {"passed": True, "checks": []}
        try:
            callback = self._effect_callback()
        except RuntimeError:
            return {
                "passed": False,
                "checks": [
                    {
                        "index": index,
                        "op": str(effect.get("op") or ""),
                        "passed": False,
                        "reason_code": "callback_unavailable",
                    }
                    for index, effect in enumerate(effects)
                ],
            }
        overlay: dict[str, Any] = {}
        checks: list[dict[str, Any]] = []
        passed = True
        bindings = self._private_bindings(context)
        for index, raw in enumerate(effects):
            effect = self._resolve_ref(raw, context)
            try:
                result = callback(
                    db,
                    campaign_id,
                    effect,
                    bindings,
                    phase="preflight",
                    overlay=overlay,
                    revision=None,
                    world_time=str(context["world_time"]),
                    operator_id=operator_id,
                    execution_id=None,
                )
                ok = isinstance(result, dict) and result.get("passed") is True
                if ok:
                    reason_code = str(result.get("reason_code") or "ok")
                    if not _KIND_RE.fullmatch(reason_code):
                        reason_code = "callback_rejected"
                        ok = False
                else:
                    reason_code = "callback_rejected"
                if not ok:
                    passed = False
            except Exception:
                passed = False
                ok = False
                reason_code = "callback_error"
            checks.append({"index": index, "op": str(effect.get("op") or ""), "passed": ok, "reason_code": reason_code})
        return {"passed": passed, "checks": checks}
    def _apply_effects_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        effects: Sequence[dict[str, Any]],
        context: dict[str, Any],
        world_time: str,
        *,
        revision: int,
        operator_id: str,
        execution_id: str,
    ) -> list[dict[str, Any]]:
        if not effects:
            return []
        callback = self._effect_callback()
        bindings = self._private_bindings(context)
        overlay: dict[str, Any] = {}
        results: list[dict[str, Any]] = []
        for index, raw in enumerate(effects):
            effect = self._resolve_ref(raw, context)
            callback_result = callback(
                db,
                campaign_id,
                effect,
                bindings,
                phase="apply",
                overlay=overlay,
                revision=revision,
                world_time=world_time,
                operator_id=operator_id,
                execution_id=execution_id,
            )
            if not isinstance(callback_result, dict) or callback_result.get("applied") is not True:
                raise RuntimeError(f"mechanism effect callback did not apply effect {index}")
            public_result = _stable_projection(callback_result.get("result") or {})
            if not isinstance(public_result, dict):
                raise RuntimeError("mechanism effect callback result must be an object")
            item = {"index": index, "op": str(effect.get("op") or ""), **public_result}
            _assert_json_limits(item, f"effect result {index}", max_bytes=MAX_RUNTIME_BYTES)
            results.append(item)
        _assert_json_limits(results, "effect results", max_bytes=MAX_RUNTIME_BYTES)
        return results
    # Evaluation and execution
    # ------------------------------------------------------------------

    def _evaluate_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        operator: dict[str, Any],
        supplied_bindings: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        context = self._bind_operator_db(db, campaign_id, operator, supplied_bindings)
        predicate_trace = self._evaluate_predicate_db(db, campaign_id, operator["preconditions"], context)
        preflight = self._preflight_effects_db(
            db,
            campaign_id,
            list(operator["costs"]) + list(operator["effects"]),
            context,
            operator_id=operator["id"],
        )
        utility = self._evaluate_considerations_db(db, campaign_id, operator, context)
        reasons: list[str] = []
        if not operator["enabled"]:
            reasons.append("operator_disabled")
        if not predicate_trace["passed"]:
            reasons.append("preconditions_failed")
        if not preflight["passed"]:
            reasons.append("transition_preflight_failed")
        evaluation = {
            "eligible": not reasons,
            "reasons": reasons,
            "preconditions": predicate_trace,
            "preflight": preflight,
            "utility": utility,
            "operator_id": operator["id"],
            "operator_digest": operator["operator_digest"],
            "contract_version": operator["contract_version"],
        }
        return context, evaluation

    def evaluate_operator(self, campaign_id: str, operator_id: str, bindings: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.e._db() as db:
            operator = self._get_operator_db(db, campaign_id, operator_id)
            context, evaluation = self._evaluate_db(db, campaign_id, operator, bindings)
        evaluation["bindings"] = self._binding_refs(context)
        _assert_json_limits(evaluation, "mechanism evaluation", max_bytes=MAX_RUNTIME_BYTES)
        return evaluation

    def _emit_events_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        revision: int,
        operator: dict[str, Any],
        context: dict[str, Any],
    ) -> list[int]:
        emits = operator["emits"] or [{
            "event_type": "mechanism_executed",
            "summary": f"Mechanism operator executed: {operator['name']}",
            "payload": {},
        }]
        event_ids: list[int] = []
        binding_refs = self._binding_refs(context)
        for raw in emits:
            emit = _stable_projection(self._resolve_ref(raw, context))
            payload = {
                "operator_id": operator["id"],
                "operator_digest": operator["operator_digest"],
                "bindings": binding_refs,
                **dict(emit.get("payload") or {}),
            }
            _assert_json_limits(payload, "mechanism event payload", max_bytes=MAX_RUNTIME_BYTES)
            event_ids.append(self.e._insert_event(
                db,
                campaign_id,
                revision,
                str(emit["event_type"]),
                str(emit.get("summary") or f"Mechanism operator executed: {operator['name']}"),
                region=str(emit.get("region")) if emit.get("region") is not None else None,
                actor_id=str(emit.get("actor_id")) if emit.get("actor_id") is not None else None,
                target_id=str(emit.get("target_id")) if emit.get("target_id") is not None else None,
                payload=payload,
                world_time_override=str(context["world_time"]),
            ))
        return event_ids

    @staticmethod
    def _receipt_material(values: dict[str, Any]) -> dict[str, Any]:
        return {
            key: _stable_projection(values[key])
            for key in (
                "campaign_id", "execution_id", "operator_id", "idempotency_key",
                "request_digest", "operator_digest", "bindings", "evaluation",
                "effect_results", "event_ids", "before_revision", "after_revision",
                "world_time", "result",
            )
        }

    def execute_operator(
        self,
        campaign_id: str,
        operator_id: str,
        *,
        bindings: dict[str, Any] | None = None,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        request = {
            "campaign_id": campaign_id,
            "operator_id": operator_id,
            "bindings": bindings or {},
            "expected_revision": expected_revision,
            "idempotency_key": idempotency_key,
        }
        request_digest = _digest(request)
        if dry_run:
            result = self.evaluate_operator(campaign_id, operator_id, bindings)
            return {"dry_run": True, "executed": False, **result}
        if idempotency_key is not None:
            idempotency_key = self._validate_id(idempotency_key, "idempotency_key")
        with self.e._write_db() as db:
            if idempotency_key:
                prior = db.execute(
                    "SELECT * FROM mechanism_execution_receipts WHERE campaign_id=? AND idempotency_key=?",
                    (campaign_id, idempotency_key),
                ).fetchone()
                if prior:
                    if prior["request_digest"] != request_digest:
                        raise ValueError("idempotency key was already used for a different request")
                    receipt = self._decode_receipt(prior)
                    return {**receipt["result"], "idempotent_replay": True}
            operator = self._get_operator_db(db, campaign_id, operator_id)
            campaign = db.execute("SELECT revision,world_time FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
            if not campaign:
                raise KeyError(f"unknown campaign: {campaign_id}")
            before_revision = int(campaign["revision"])
            if expected_revision is not None and int(expected_revision) != before_revision:
                raise ValueError(f"revision conflict: expected {expected_revision}, current {before_revision}")
            context, evaluation = self._evaluate_db(db, campaign_id, operator, bindings)
            if not evaluation["eligible"]:
                raise ValueError("operator is not eligible: " + ", ".join(evaluation["reasons"]))
            after_revision = self.e._next_revision(db, campaign_id)
            public_bindings = self._binding_refs(context)
            execution_seed = {
                "campaign_id": campaign_id,
                "request_digest": request_digest,
                "operator_digest": operator["operator_digest"],
                "before_revision": before_revision,
                "after_revision": after_revision,
                "world_time": campaign["world_time"],
                "idempotency_key": idempotency_key,
            }
            execution_id = "mex_" + _digest(execution_seed)[:24]
            effect_results = self._apply_effects_db(
                db,
                campaign_id,
                list(operator["costs"]) + list(operator["effects"]),
                context,
                str(campaign["world_time"]),
                revision=after_revision,
                operator_id=operator_id,
                execution_id=execution_id,
            )
            event_ids = self._emit_events_db(db, campaign_id, after_revision, operator, context)
            result = {
                "campaign_id": campaign_id,
                "execution_id": execution_id,
                "operator_id": operator_id,
                "contract_version": CONTRACT_VERSION,
                "executed": True,
                "idempotent_replay": False,
                "before_revision": before_revision,
                "after_revision": after_revision,
                "world_time": campaign["world_time"],
                "bindings": public_bindings,
                "effect_results": effect_results,
                "event_ids": event_ids,
            }
            _assert_json_limits(evaluation, "receipt evaluation", max_bytes=MAX_RUNTIME_BYTES)
            _assert_json_limits(result, "mechanism result", max_bytes=MAX_RUNTIME_BYTES)
            receipt_values = {
                **result,
                "idempotency_key": idempotency_key,
                "request_digest": request_digest,
                "operator_digest": operator["operator_digest"],
                "evaluation": evaluation,
                "result": result,
            }
            result_digest = _digest(self._receipt_material(receipt_values))
            db.execute(
                """INSERT INTO mechanism_execution_receipts(
                       campaign_id,execution_id,operator_id,idempotency_key,request_digest,operator_digest,
                       bindings_json,evaluation_json,effect_results_json,event_ids_json,before_revision,
                       after_revision,world_time,result_json,result_digest,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    campaign_id, execution_id, operator_id, idempotency_key, request_digest, operator["operator_digest"],
                    self.e._dumps(public_bindings), self.e._dumps(evaluation), self.e._dumps(effect_results),
                    self.e._dumps(event_ids), before_revision, after_revision, campaign["world_time"],
                    self.e._dumps(result), result_digest, self.e._now(),
                ),
            )
        return result

    def get_receipt(self, campaign_id: str, execution_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            self._require_schema_db(db)
            row = db.execute("SELECT * FROM mechanism_execution_receipts WHERE campaign_id=? AND execution_id=?", (campaign_id, execution_id)).fetchone()
        if not row:
            raise KeyError(f"unknown mechanism execution: {execution_id}")
        return self._decode_receipt(row)

    def list_receipts(self, campaign_id: str, *, operator_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM mechanism_execution_receipts WHERE campaign_id=?"
        params: list[Any] = [campaign_id]
        if operator_id:
            sql += " AND operator_id=?"
            params.append(operator_id)
        sql += " ORDER BY created_at DESC,execution_id DESC LIMIT ?"
        params.append(max(1, min(int(limit), MAX_QUERY_LIMIT)))
        with self.e._db() as db:
            self._require_schema_db(db)
            rows = db.execute(sql, params).fetchall()
        return [self._decode_receipt(row) for row in rows]

    def _decode_receipt(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        for field in ("bindings", "evaluation", "effect_results", "event_ids", "result"):
            out[field] = self.e._loads(out.pop(field + "_json"))
        _assert_json_limits(out["result"], "stored mechanism result", max_bytes=MAX_RUNTIME_BYTES)
        expected = _digest(self._receipt_material(out))
        if not isinstance(out.get("result_digest"), str) or out["result_digest"] != expected:
            raise ValueError("mechanism receipt integrity verification failed")
        out["receipt_digest"] = out["result_digest"]
        return out

    # ------------------------------------------------------------------
    # Compatibility adapters (no autonomous integration in Phase 1)
    # ------------------------------------------------------------------

    def adapt_npc_action(
        self,
        campaign_id: str,
        npc_id: str,
        action_id: str,
        *,
        operator_id: str | None = None,
        save: bool = False,
    ) -> dict[str, Any]:
        with self.e._db() as db:
            row = db.execute("SELECT * FROM npc_actions WHERE campaign_id=? AND npc_id=? AND action_id=?", (campaign_id, npc_id, action_id)).fetchone()
            if not row:
                raise KeyError(f"unknown NPC action: {npc_id}/{action_id}")
            npc = db.execute("SELECT location FROM npcs WHERE campaign_id=? AND id=?", (campaign_id, npc_id)).fetchone()
        operator_id = operator_id or f"legacy.npc.{npc_id}.{action_id}"
        location_id = row["location"] or (npc["location"] if npc else None)
        bindings: dict[str, Any] = {
            "actor": {"kinds": ["npc"], "default": {"kind": "npc", "id": npc_id}},
        }
        if location_id:
            bindings["location"] = {"kinds": ["location"], "default": {"kind": "location", "id": location_id}}
        predicates: list[dict[str, Any]] = []
        requirements = self.e._loads(row["requirements_json"] or "{}")
        for item_id, qty in (requirements.get("item") or {}).items():
            predicates.append({"read": {"source": "inventory", "binding": "actor", "item_id": item_id}, "op": "gte", "value": qty})
        for key, value in (requirements.get("world_state") or {}).items():
            predicates.append({"read": {"source": "world_state_any", "key": key}, "op": "eq", "value": value})
        for effect_type, minimum in (requirements.get("environment") or {}).items():
            read = {"source": "environment", "effect_type": effect_type}
            if "location" in bindings:
                read["location_binding"] = "location"
            else:
                read["location_id"] = "$actor.location"
            predicates.append({"read": read, "op": "gte", "value": minimum})
        for belief in requirements.get("beliefs") or []:
            predicates.append({"read": {"source": "legacy_belief", "binding": "actor", "key": belief}, "op": "truthy"})
        for goal in requirements.get("goals") or []:
            predicates.append({"read": {"source": "legacy_goal", "binding": "actor", "key": goal}, "op": "truthy"})
        for item_id, qty in (requirements.get("resource") or {}).items():
            read = {"source": "resource_sum", "item_id": item_id}
            if "location" in bindings:
                read["location_binding"] = "location"
            else:
                read["location_id"] = "$actor.location"
            predicates.append({"read": read, "op": "gte", "value": qty})
        effects: list[dict[str, Any]] = []
        for legacy in self.e._loads(row["effects_json"] or "[]"):
            kind = str(legacy.get("type") or "")
            if kind == "need":
                effects.append({"op": "need.adjust", "binding": "actor", "need": legacy.get("need"), "delta": legacy.get("delta", 0)})
            elif kind == "inventory":
                effects.append({"op": "inventory.adjust", "binding": "actor", "item_id": legacy.get("item_id"), "delta": legacy.get("delta", 0)})
            elif kind == "resource":
                effects.append({"op": "resource.adjust", "node_id": legacy.get("node_id"), "delta": legacy.get("delta", 0)})
            elif kind == "environment":
                target = legacy.get("target") or {}
                target_type = target.get("type") or target.get("target_type")
                target_id = target.get("id") or target.get("target_id")
                effect = {
                    "op": "environment.apply",
                    "effect_type": legacy.get("effect_type", "smoke"),
                    "intensity": legacy.get("intensity", 0.3),
                    "amount": legacy.get("amount", 0),
                }
                if target_type and target_id:
                    effect.update({"target_type": target_type, "target_id": target_id})
                elif "location" in bindings:
                    effect["target_binding"] = "location"
                effects.append(effect)
            else:
                raise ValueError(f"legacy action effect is not adaptable in {CONTRACT_VERSION}: {kind}")
        considerations = [self._adapt_legacy_consideration(item) for item in self.e._loads(row["considerations_json"] or "[]")]
        document = self.validate_operator_document({
            "id": operator_id,
            "name": f"Legacy NPC action: {action_id}",
            "source_kind": "npc",
            "source_id": npc_id,
            "bindings": bindings,
            "preconditions": predicates,
            "effects": effects,
            "considerations": considerations,
            "base_utility": float(row["base_utility"]),
            "cost_hours": float(row["cost_hours"]),
            "tags": ["legacy_npc_action", *self.e._loads(row["tags_json"] or "[]")],
            "metadata": {"adapter": "npc_action_v440", "legacy_action_id": action_id},
        })
        if save:
            return self.save_operator(campaign_id, document)
        return document

    def _adapt_legacy_consideration(self, item: dict[str, Any]) -> dict[str, Any]:
        ctype = str(item.get("type", "need"))
        read: dict[str, Any]
        minimum, maximum = 0.0, 1.0
        if ctype == "need":
            read = {
                "source": "need",
                "binding": "actor",
                "key": item.get("key"),
                "field": "value",
                "transform": "configured_curve",
            }
        elif ctype == "inventory":
            read = {"source": "inventory", "binding": "actor", "item_id": item.get("item_id")}
            minimum, maximum = 0.0, float(item.get("scale", 1))
        elif ctype == "relationship":
            read = {"source": "relationship", "source_binding": "actor", "target_id": item.get("target_id", "player"), "field": item.get("field", "trust")}
            minimum, maximum = -100.0, 100.0
        elif ctype == "world_state":
            read = {"source": "world_state", "scope_type": item.get("scope_type", "world"), "scope_id": item.get("scope_id", "global"), "key": item.get("key")}
            minimum, maximum = float(item.get("min", 0)), float(item.get("max", 100))
        elif ctype == "environment":
            read = {"source": "environment", "location_id": "$actor.location", "effect_type": item.get("effect_type") or item.get("key") or "hazard"}
        elif ctype == "belief":
            read = {"source": "legacy_belief", "binding": "actor", "key": item.get("key"), "match": item.get("match", "exact")}
        elif ctype == "goal":
            read = {"source": "legacy_goal", "binding": "actor", "key": item.get("key"), "match": item.get("match", "exact")}
        elif ctype == "constant":
            read = {"source": "constant", "value": item.get("value", 0)}
        elif ctype == "resource":
            read = {"source": "resource_sum", "item_id": item.get("item_id"), "location_id": item.get("location_id") or "$actor.location", "field": "ratio"}
        elif ctype == "mood":
            read = {"source": "mood", "binding": "actor", "field": "normalized"}
        else:
            raise ValueError(f"legacy consideration is not adaptable in {CONTRACT_VERSION}: {ctype}")
        return {
            "read": read,
            "weight": float(item.get("weight", 1)),
            "min": minimum,
            "max": maximum,
            "invert": bool(item.get("invert", False)),
            "label": ctype,
        }

    def adapt_goap_action(self, action: dict[str, Any], *, operator_id: str | None = None) -> dict[str, Any]:
        if not isinstance(action, dict):
            raise ValueError("GOAP action must be an object")
        action_id = self._validate_id(action.get("id"), "GOAP action id")
        return self.validate_operator_document({
            "id": operator_id or f"goap.{action_id}",
            "name": str(action.get("name") or f"GOAP action: {action_id}"),
            "source_kind": "global",
            "bindings": action.get("bindings") or {},
            "planning_preconditions": action.get("preconditions") or {},
            "planning_effects": action.get("effects") or {},
            "cost_hours": float(action.get("cost", 1)),
            "tags": ["goap_adapter"],
            "metadata": {"adapter": "goap_v440", "legacy_action_id": action_id},
        })

    # ------------------------------------------------------------------
    # Snapshot / dispatch
    # ------------------------------------------------------------------

    def snapshot(self, campaign_id: str, *, limit: int = 50) -> dict[str, Any]:
        with self.e._db() as db:
            self._require_schema_db(db)
            operator_count = int(db.execute("SELECT COUNT(*) n FROM mechanism_operators WHERE campaign_id=?", (campaign_id,)).fetchone()["n"])
            enabled_count = int(db.execute("SELECT COUNT(*) n FROM mechanism_operators WHERE campaign_id=? AND enabled=1", (campaign_id,)).fetchone()["n"])
            receipt_count = int(db.execute("SELECT COUNT(*) n FROM mechanism_execution_receipts WHERE campaign_id=?", (campaign_id,)).fetchone()["n"])
            recent = db.execute(
                "SELECT execution_id,operator_id,before_revision,after_revision,world_time,result_digest,created_at FROM mechanism_execution_receipts WHERE campaign_id=? ORDER BY created_at DESC LIMIT ?",
                (campaign_id, max(1, min(int(limit), MAX_QUERY_LIMIT))),
            ).fetchall()
        return {
            "contract_version": CONTRACT_VERSION,
            "operators": {"total": operator_count, "enabled": enabled_count},
            "execution_receipts": {"total": receipt_count, "recent": [dict(row) for row in recent]},
            "phase": "shared_contract_only",
        }

    def dispatch(self, operation: str, campaign_id: str, payload: dict[str, Any] | None = None) -> Any:
        data = dict(payload or {})
        if operation == "validate":
            return self.validate_operator_document(data["operator"])
        if operation == "save":
            return self.save_operator(campaign_id, data["operator"])
        if operation == "get":
            return self.get_operator(campaign_id, data["operator_id"])
        if operation == "list":
            return self.list_operators(campaign_id, enabled_only=bool(data.get("enabled_only", False)), limit=int(data.get("limit", 200)))
        if operation == "bind":
            return self.bind_operator(campaign_id, data["operator_id"], data.get("bindings"))
        if operation == "evaluate":
            return self.evaluate_operator(campaign_id, data["operator_id"], data.get("bindings"))
        if operation == "execute":
            return self.execute_operator(
                campaign_id,
                data["operator_id"],
                bindings=data.get("bindings"),
                expected_revision=data.get("expected_revision"),
                idempotency_key=data.get("idempotency_key"),
                dry_run=bool(data.get("dry_run", False)),
            )
        if operation == "get_receipt":
            return self.get_receipt(campaign_id, data["execution_id"])
        if operation == "list_receipts":
            return self.list_receipts(campaign_id, operator_id=data.get("operator_id"), limit=int(data.get("limit", 50)))
        if operation == "adapt_npc_action":
            return self.adapt_npc_action(
                campaign_id,
                data["npc_id"],
                data["action_id"],
                operator_id=data.get("operator_id"),
                save=bool(data.get("save", False)),
            )
        if operation == "adapt_goap_action":
            return self.adapt_goap_action(data["action"], operator_id=data.get("operator_id"))
        if operation == "snapshot":
            return self.snapshot(campaign_id, limit=int(data.get("limit", 50)))
        raise ValueError(f"unknown mechanism operation: {operation}")
