from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .mechanisms import MechanismKernel

if TYPE_CHECKING:
    from .engine import WorldEngine


QUEST_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS quest_runtime_instances (
    campaign_id TEXT NOT NULL,
    quest_id TEXT NOT NULL,
    template_id TEXT,
    bindings_json TEXT NOT NULL DEFAULT '{}',
    visibility TEXT NOT NULL DEFAULT 'public'
        CHECK(visibility IN ('public','private','secret')),
    start_event_id INTEGER NOT NULL DEFAULT 0 CHECK(start_event_id >= 0),
    created_world_time TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,quest_id),
    FOREIGN KEY(campaign_id,quest_id) REFERENCES quests(campaign_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS quest_event_cursors (
    campaign_id TEXT PRIMARY KEY,
    last_event_id INTEGER NOT NULL DEFAULT 0 CHECK(last_event_id >= 0),
    updated_at TEXT NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS quest_transition_receipts (
    campaign_id TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    quest_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    evaluation_key TEXT NOT NULL,
    transition_kind TEXT NOT NULL CHECK(transition_kind IN (
        'activated','completed','failed','deadline','branch_activated',
        'branch_skipped','quest_completed','quest_failed'
    )),
    before_status TEXT NOT NULL,
    after_status TEXT NOT NULL,
    source_event_id INTEGER,
    emitted_event_ids_json TEXT NOT NULL DEFAULT '[]',
    detail_json TEXT NOT NULL DEFAULT '{}',
    world_time TEXT NOT NULL,
    revision INTEGER NOT NULL,
    result_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,receipt_id),
    UNIQUE(campaign_id,quest_id,node_id,evaluation_key,transition_kind),
    FOREIGN KEY(campaign_id,quest_id,node_id)
        REFERENCES quest_nodes(campaign_id,quest_id,id) ON DELETE CASCADE,
    FOREIGN KEY(source_event_id) REFERENCES events(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_quest_runtime_visibility
    ON quest_runtime_instances(campaign_id,visibility,quest_id);
CREATE INDEX IF NOT EXISTS idx_quest_receipts_quest
    ON quest_transition_receipts(campaign_id,quest_id,world_time,receipt_id);
CREATE INDEX IF NOT EXISTS idx_quest_receipts_source
    ON quest_transition_receipts(campaign_id,source_event_id,receipt_id);
"""


_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,199}$")
_NODE_STATUSES = {"inactive", "active", "completed", "failed", "skipped"}
_QUEST_STATUSES = {"inactive", "active", "completed", "failed", "abandoned"}
_CONDITION_KEYS = {"all", "any", "not", "event", "world_time", "entity", "predicate"}
_COMPARE_OPS = {
    "eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains",
    "not_contains", "starts_with", "ends_with", "truthy", "falsy", "exists",
    "missing", "is_null", "not_null",
}

MAX_QUEST_NODES = 128
MAX_QUEST_EDGES = 512
MAX_CONDITION_DEPTH = 10
MAX_CONDITION_LEAVES = 128
MAX_EVENTS_PER_STEP = 256
MAX_TRANSITIONS_PER_STEP = 512
MAX_TEMPLATE_BINDINGS = 32
MAX_JSON_BYTES = 262_144
MAX_JSON_NODES = 4096


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


def _json_guard(value: Any, label: str) -> None:
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_CONDITION_DEPTH + 6:
            raise ValueError(f"{label} is too complex")
        if isinstance(item, dict):
            pending.extend((str(key), depth + 1) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError(f"{label} contains a non-finite number")
    try:
        encoded = _canonical(value).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite JSON data") from exc
    if len(encoded) > MAX_JSON_BYTES:
        raise ValueError(f"{label} exceeds {MAX_JSON_BYTES} encoded bytes")


class QuestRuntimeKernel:
    """Deterministic event-driven runtime over canonical quest graph tables.

    `quests`, `quest_nodes`, and `quest_edges` remain authoritative. The additive
    tables above contain only runtime bindings, the event-subscription cursor,
    and idempotent transition receipts.
    """

    def __init__(self, engine: WorldEngine):
        self.e = engine
        self.mechanisms = MechanismKernel(engine)

    @staticmethod
    def install_schema_db(db: sqlite3.Connection) -> None:
        statement = ""
        for char in QUEST_SCHEMA:
            statement += char
            if char == ";" and sqlite3.complete_statement(statement):
                if statement.strip():
                    db.execute(statement)
                statement = ""
        if statement.strip():
            if not sqlite3.complete_statement(statement):
                raise sqlite3.OperationalError("incomplete quest schema statement")
            db.execute(statement)

    def install_schema(self) -> None:
        with self.e._write_db() as db:
            self.install_schema_db(db)

    @staticmethod
    def _require_schema_db(db: sqlite3.Connection) -> None:
        required = {
            "quest_runtime_instances",
            "quest_event_cursors",
            "quest_transition_receipts",
        }
        present = {
            str(row["name"])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'quest_%'"
            ).fetchall()
        }
        missing = required - present
        if missing:
            raise RuntimeError(f"quest runtime schema is not installed: {sorted(missing)}")

    @staticmethod
    def _id(value: Any, label: str) -> str:
        text = str(value or "").strip()
        if not _ID_RE.fullmatch(text):
            raise ValueError(f"{label} is invalid")
        return text

    @staticmethod
    def _loads(engine: WorldEngine, value: str | None) -> Any:
        return engine._loads(value or "{}")

    @classmethod
    def validate_condition(
        cls,
        condition: Any,
        *,
        path: str = "condition",
        depth: int = 0,
        counter: list[int] | None = None,
    ) -> None:
        if condition in (None, {}, []):
            return
        if depth > MAX_CONDITION_DEPTH:
            raise ValueError(f"{path} exceeds maximum depth {MAX_CONDITION_DEPTH}")
        counter = counter if counter is not None else [0]
        if not isinstance(condition, dict):
            raise TypeError(f"{path} must be an object")
        keys = set(condition)
        structural = keys & _CONDITION_KEYS
        if len(structural) != 1 or keys != structural:
            raise ValueError(f"{path} must contain exactly one typed condition key")
        kind = next(iter(structural))
        if kind in {"all", "any"}:
            children = condition[kind]
            if not isinstance(children, list) or not children:
                raise ValueError(f"{path}.{kind} must be a non-empty list")
            for index, child in enumerate(children):
                cls.validate_condition(
                    child,
                    path=f"{path}.{kind}[{index}]",
                    depth=depth + 1,
                    counter=counter,
                )
            return
        if kind == "not":
            cls.validate_condition(
                condition["not"],
                path=f"{path}.not",
                depth=depth + 1,
                counter=counter,
            )
            return
        counter[0] += 1
        if counter[0] > MAX_CONDITION_LEAVES:
            raise ValueError(f"{path} exceeds maximum leaf count {MAX_CONDITION_LEAVES}")
        body = condition[kind]
        if not isinstance(body, dict):
            raise TypeError(f"{path}.{kind} must be an object")
        if kind == "predicate":
            MechanismKernel.validate_predicate_definition(body, path=f"{path}.predicate")
            return
        if kind == "event":
            allowed = {
                "event_type", "actor_id", "target_id", "region", "sensitivity",
                "scope_type", "payload",
            }
            unknown = set(body) - allowed
            if unknown:
                raise ValueError(f"{path}.event has unsupported fields: {sorted(unknown)}")
            if not body:
                raise ValueError(f"{path}.event must constrain at least one field")
            if "payload" in body and not isinstance(body["payload"], dict):
                raise ValueError(f"{path}.event.payload must be an object")
            _json_guard(body, f"{path}.event")
            return
        if kind == "world_time":
            unknown = set(body) - {"op", "value"}
            if unknown or body.get("op") not in {"eq", "ne", "gt", "gte", "lt", "lte"}:
                raise ValueError(f"{path}.world_time has an invalid operator or field")
            if body.get("value") in (None, ""):
                raise ValueError(f"{path}.world_time.value is required")
            _utc(str(body["value"]))
            return
        unknown = set(body) - {"binding", "kind", "id", "field", "op", "value"}
        if unknown:
            raise ValueError(f"{path}.entity has unsupported fields: {sorted(unknown)}")
        if not body.get("binding") and not (body.get("kind") and body.get("id")):
            raise ValueError(f"{path}.entity requires binding or kind+id")
        field = str(body.get("field") or "")
        if not _FIELD_RE.fullmatch(field) or set(field.split(".")) & {
            "created_at", "updated_at", "applied_at"
        }:
            raise ValueError(f"{path}.entity.field is invalid")
        op = str(body.get("op") or "eq")
        if op not in _COMPARE_OPS:
            raise ValueError(f"{path}.entity.op is invalid")
        if op not in {"truthy", "falsy", "exists", "missing", "is_null", "not_null"} and "value" not in body:
            raise ValueError(f"{path}.entity.value is required for {op}")

    @classmethod
    def _validate_graph_data(
        cls,
        nodes: Iterable[dict[str, Any]],
        edges: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        node_list = [dict(node) for node in nodes]
        edge_list = [dict(edge) for edge in edges]
        if not node_list:
            raise ValueError("quest graph requires at least one node")
        if len(node_list) > MAX_QUEST_NODES:
            raise ValueError(f"quest graph exceeds {MAX_QUEST_NODES} nodes")
        if len(edge_list) > MAX_QUEST_EDGES:
            raise ValueError(f"quest graph exceeds {MAX_QUEST_EDGES} edges")
        ids: set[str] = set()
        for index, node in enumerate(node_list):
            node_id = cls._id(node.get("id"), f"nodes[{index}].id")
            if node_id in ids:
                raise ValueError(f"duplicate quest node: {node_id}")
            ids.add(node_id)
            status = str(node.get("status", "inactive"))
            if status not in _NODE_STATUSES:
                raise ValueError(f"nodes[{index}].status is invalid")
            for field in ("trigger", "success", "failure"):
                cls.validate_condition(node.get(field) or {}, path=f"nodes[{index}].{field}")
            deadline = node.get("deadline_world_time")
            if deadline:
                _utc(str(deadline))
            state = node.get("state") or {}
            if not isinstance(state, dict):
                raise TypeError(f"nodes[{index}].state must be an object")
            if str(state.get("branch_mode", "first")) not in {"first", "all"}:
                raise ValueError(f"nodes[{index}].state.branch_mode must be first or all")
            _json_guard(state, f"nodes[{index}].state")
        adjacency = {node_id: [] for node_id in ids}
        indegree = {node_id: 0 for node_id in ids}
        seen_edges: set[tuple[str, str]] = set()
        for index, edge in enumerate(edge_list):
            source = cls._id(edge.get("from_node"), f"edges[{index}].from_node")
            target = cls._id(edge.get("to_node"), f"edges[{index}].to_node")
            if source not in ids or target not in ids:
                raise ValueError(f"edges[{index}] references a missing node")
            if source == target:
                raise ValueError("quest graph self-cycles are not allowed")
            if (source, target) in seen_edges:
                raise ValueError(f"duplicate quest edge: {source}->{target}")
            seen_edges.add((source, target))
            cls.validate_condition(edge.get("condition") or {}, path=f"edges[{index}].condition")
            try:
                int(edge.get("priority", 100))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"edges[{index}].priority must be an integer") from exc
            adjacency[source].append(target)
            indegree[target] += 1
        queue = sorted(node_id for node_id, degree in indegree.items() if degree == 0)
        ordered: list[str] = []
        work = dict(indegree)
        while queue:
            node_id = queue.pop(0)
            ordered.append(node_id)
            for target in sorted(adjacency[node_id]):
                work[target] -= 1
                if work[target] == 0:
                    queue.append(target)
                    queue.sort()
        if len(ordered) != len(ids):
            raise ValueError("quest graph contains a cycle")
        roots = sorted(node_id for node_id, degree in indegree.items() if degree == 0)
        return {
            "valid": True,
            "node_count": len(ids),
            "edge_count": len(edge_list),
            "roots": roots,
            "topological_order": ordered,
        }

    def validate_graph_db(
        self, db: sqlite3.Connection, campaign_id: str, quest_id: str
    ) -> dict[str, Any]:
        quest_id = self._id(quest_id, "quest_id")
        if not db.execute(
            "SELECT 1 FROM quests WHERE campaign_id=? AND id=?", (campaign_id, quest_id)
        ).fetchone():
            raise KeyError(f"unknown quest: {quest_id}")
        nodes = []
        for row in db.execute(
            "SELECT * FROM quest_nodes WHERE campaign_id=? AND quest_id=? ORDER BY id",
            (campaign_id, quest_id),
        ).fetchall():
            node = dict(row)
            for field in ("trigger", "success", "failure", "state"):
                node[field] = self.e._loads(node.pop(f"{field}_json"))
            nodes.append(node)
        edges = []
        for row in db.execute(
            """SELECT * FROM quest_edges WHERE campaign_id=? AND quest_id=?
               ORDER BY priority,from_node,to_node""",
            (campaign_id, quest_id),
        ).fetchall():
            edge = dict(row)
            edge["condition"] = self.e._loads(edge.pop("condition_json"))
            edges.append(edge)
        return {"quest_id": quest_id, **self._validate_graph_data(nodes, edges)}

    def validate_graph(self, campaign_id: str, quest_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            self._require_schema_db(db)
            return self.validate_graph_db(db, campaign_id, quest_id)

    @staticmethod
    def _deep_get(value: Any, path: str) -> Any:
        current = value
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, (list, tuple)) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            else:
                return None
        return current

    def _resolve_value(self, value: Any, context: dict[str, Any]) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            return self._deep_get(context, value[1:])
        if isinstance(value, dict):
            return {key: self._resolve_value(item, context) for key, item in value.items()}
        if isinstance(value, list):
            return [self._resolve_value(item, context) for item in value]
        return value

    @staticmethod
    def _partial_match(have: Any, expected: Any) -> bool:
        if isinstance(expected, dict):
            return isinstance(have, dict) and all(
                key in have and QuestRuntimeKernel._partial_match(have[key], item)
                for key, item in expected.items()
            )
        if isinstance(expected, list):
            return isinstance(have, list) and have == expected
        return have == expected

    def _bindings_context_db(
        self, db: sqlite3.Connection, campaign_id: str, quest_id: str
    ) -> dict[str, Any]:
        row = db.execute(
            """SELECT bindings_json FROM quest_runtime_instances
               WHERE campaign_id=? AND quest_id=?""",
            (campaign_id, quest_id),
        ).fetchone()
        refs = self.e._loads(row["bindings_json"]) if row else {}
        context: dict[str, Any] = {}
        for role in sorted(refs):
            ref = refs[role]
            entity = self.mechanisms._entity_db(
                db, campaign_id, str(ref["kind"]), str(ref["id"])
            )
            if not entity:
                raise ValueError(f"quest binding is no longer authoritative: {role}")
            context[role] = entity
        return context

    def evaluate_condition_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        condition: Any,
        *,
        context: dict[str, Any],
        when: datetime,
        event: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if condition in (None, {}, []):
            return {"kind": "empty", "passed": True}
        self.validate_condition(condition)
        if "all" in condition:
            children = [
                self.evaluate_condition_db(
                    db, campaign_id, child, context=context, when=when, event=event
                )
                for child in condition["all"]
            ]
            return {"kind": "all", "passed": all(x["passed"] for x in children), "children": children}
        if "any" in condition:
            children = [
                self.evaluate_condition_db(
                    db, campaign_id, child, context=context, when=when, event=event
                )
                for child in condition["any"]
            ]
            return {"kind": "any", "passed": any(x["passed"] for x in children), "children": children}
        if "not" in condition:
            child = self.evaluate_condition_db(
                db, campaign_id, condition["not"], context=context, when=when, event=event
            )
            return {"kind": "not", "passed": not child["passed"], "child": child}
        if "predicate" in condition:
            result = self.mechanisms._evaluate_predicate_db(
                db, campaign_id, condition["predicate"], context
            )
            return {"kind": "predicate", **result}
        if "event" in condition:
            if event is None:
                return {"kind": "event", "passed": False, "reason": "no_event"}
            matcher = self._resolve_value(condition["event"], {**context, "event": event})
            event_view = event
            compatibility = None
            # v5.0 generated arrival nodes named a semantic event that the
            # movement runtime never emitted. Preserve already-instantiated
            # quests by projecting the canonical character movement event into
            # that legacy condition shape only while evaluating the condition.
            if (
                matcher.get("event_type") == "character_arrived"
                and event.get("event_type") == "movement"
                and (event.get("payload") or {}).get("kind") == "character"
            ):
                event_view = {
                    **event,
                    "event_type": "character_arrived",
                    "target_id": (event.get("payload") or {}).get("to"),
                }
                compatibility = "v5.0-character-arrived"
            passed = all(
                self._partial_match(event_view.get(key), value)
                for key, value in matcher.items()
            )
            result = {
                "kind": "event",
                "passed": passed,
                "matched_fields": sorted(matcher),
            }
            if compatibility:
                result["compatibility"] = compatibility
            return result
        if "world_time" in condition:
            body = condition["world_time"]
            want = _utc(str(body["value"]))
            op = str(body["op"])
            passed = {
                "eq": when == want,
                "ne": when != want,
                "gt": when > want,
                "gte": when >= want,
                "lt": when < want,
                "lte": when <= want,
            }[op]
            return {"kind": "world_time", "passed": passed, "op": op, "value": want.isoformat()}
        body = condition["entity"]
        if body.get("binding"):
            entity = context.get(str(body["binding"]))
        else:
            kind = str(self._resolve_value(body.get("kind"), context) or "")
            entity_id = str(self._resolve_value(body.get("id"), context) or "")
            entity = self.mechanisms._entity_db(db, campaign_id, kind, entity_id)
        have = self._deep_get(entity, str(body["field"])) if entity else None
        want = self._resolve_value(body.get("value"), context)
        op = str(body.get("op", "eq"))
        passed = self.mechanisms._compare(have, op, want)
        return {"kind": "entity", "passed": passed, "op": op, "field": body["field"]}

    @staticmethod
    def _decode_event(engine: WorldEngine, row: sqlite3.Row) -> dict[str, Any]:
        event = dict(row)
        event["payload"] = engine._loads(event.pop("payload_json"))
        return event

    def _emit_event_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        revision: int,
        when: datetime,
        emit: Callable[..., Any] | None,
        *,
        event_type: str,
        summary: str,
        payload: dict[str, Any],
        region: str | None,
        actor_id: str | None,
        target_id: str | None,
        source_event_id: int | None,
        visibility: str,
    ) -> int:
        payload = {**payload, "source_event_id": source_event_id}
        sensitivity = {"public": "PUBLIC", "private": "PRIVATE", "secret": "SECRET"}[visibility]
        before = int(db.execute("SELECT COALESCE(MAX(id),0) n FROM events").fetchone()["n"])
        returned = None
        if emit:
            returned = emit(
                event_type,
                summary,
                payload,
                region,
                when,
                sensitivity=sensitivity,
                scope_type="WORLD" if visibility == "public" else "GM",
                causal_parent_event_id=source_event_id,
            )
        event_id = int(returned) if isinstance(returned, int) and not isinstance(returned, bool) else None
        if event_id is None:
            row = db.execute(
                """SELECT id FROM events WHERE campaign_id=? AND id>? AND event_type=?
                   ORDER BY id DESC LIMIT 1""",
                (campaign_id, before, event_type),
            ).fetchone()
            event_id = int(row["id"]) if row else None
        if event_id is None:
            event_id = self.e._insert_event(
                db,
                campaign_id,
                revision,
                event_type,
                summary,
                region=region,
                actor_id=actor_id,
                target_id=target_id,
                payload=payload,
                world_time_override=when.isoformat(),
                sensitivity=sensitivity,
                scope_type="WORLD" if visibility == "public" else "GM",
                causal_parent_event_id=source_event_id,
            )
        else:
            parent = None
            if source_event_id is not None:
                parent = db.execute(
                    "SELECT causal_root_event_id FROM events WHERE campaign_id=? AND id=?",
                    (campaign_id, source_event_id),
                ).fetchone()
                if not parent:
                    raise ValueError("quest transition source event is missing")
            db.execute(
                """UPDATE events SET causal_parent_event_id=?,causal_root_event_id=?,
                   sensitivity=?,scope_type=? WHERE campaign_id=? AND id=?""",
                (
                    source_event_id,
                    int(parent["causal_root_event_id"] or source_event_id)
                    if parent is not None
                    else event_id,
                    sensitivity,
                    "WORLD" if visibility == "public" else "GM",
                    campaign_id,
                    event_id,
                ),
            )
        return event_id

    def _receipt_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        quest_id: str,
        node_id: str,
        *,
        evaluation_key: str,
        transition_kind: str,
        before_status: str,
        after_status: str,
        source_event_id: int | None,
        emitted_event_ids: list[int],
        detail: dict[str, Any],
        when: datetime,
        revision: int,
    ) -> tuple[dict[str, Any], bool]:
        existing = db.execute(
            """SELECT * FROM quest_transition_receipts WHERE campaign_id=? AND quest_id=?
               AND node_id=? AND evaluation_key=? AND transition_kind=?""",
            (campaign_id, quest_id, node_id, evaluation_key, transition_kind),
        ).fetchone()
        if existing:
            return self._decode_receipt(existing), False
        material = {
            "campaign_id": campaign_id,
            "quest_id": quest_id,
            "node_id": node_id,
            "evaluation_key": evaluation_key,
            "transition_kind": transition_kind,
            "before_status": before_status,
            "after_status": after_status,
            "source_event_id": source_event_id,
            "emitted_event_ids": emitted_event_ids,
            "detail": detail,
            "world_time": when.isoformat(),
            "revision": int(revision),
        }
        result_digest = _digest(material)
        receipt_id = "qtr_" + _digest(
            [campaign_id, quest_id, node_id, evaluation_key, transition_kind]
        )[:24]
        db.execute(
            """INSERT INTO quest_transition_receipts(
                   campaign_id,receipt_id,quest_id,node_id,evaluation_key,transition_kind,
                   before_status,after_status,source_event_id,emitted_event_ids_json,
                   detail_json,world_time,revision,result_digest,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                campaign_id,
                receipt_id,
                quest_id,
                node_id,
                evaluation_key,
                transition_kind,
                before_status,
                after_status,
                source_event_id,
                self.e._dumps(emitted_event_ids),
                self.e._dumps(detail),
                when.isoformat(),
                int(revision),
                result_digest,
                self.e._now(),
            ),
        )
        row = db.execute(
            """SELECT * FROM quest_transition_receipts
               WHERE campaign_id=? AND receipt_id=?""",
            (campaign_id, receipt_id),
        ).fetchone()
        return self._decode_receipt(row), True

    def _decode_receipt(self, row: sqlite3.Row) -> dict[str, Any]:
        receipt = dict(row)
        receipt["emitted_event_ids"] = self.e._loads(
            receipt.pop("emitted_event_ids_json")
        )
        receipt["detail"] = self.e._loads(receipt.pop("detail_json"))
        material = {
            key: receipt[key]
            for key in (
                "campaign_id", "quest_id", "node_id", "evaluation_key",
                "transition_kind", "before_status", "after_status", "source_event_id",
                "emitted_event_ids", "detail", "world_time", "revision",
            )
        }
        if _digest(material) != receipt["result_digest"]:
            raise ValueError("quest transition receipt integrity verification failed")
        return receipt

    def _visibility_db(
        self, db: sqlite3.Connection, campaign_id: str, quest_id: str
    ) -> str:
        row = db.execute(
            """SELECT visibility FROM quest_runtime_instances
               WHERE campaign_id=? AND quest_id=?""",
            (campaign_id, quest_id),
        ).fetchone()
        return str(row["visibility"]) if row else "public"

    def _transition_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        quest_id: str,
        node_id: str,
        *,
        transition_kind: str,
        before_status: str,
        after_status: str,
        evaluation_key: str,
        source_event_id: int | None,
        detail: dict[str, Any],
        revision: int,
        when: datetime,
        emit: Callable[..., Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        prior = db.execute(
            """SELECT 1 FROM quest_transition_receipts WHERE campaign_id=? AND quest_id=?
               AND node_id=? AND evaluation_key=? AND transition_kind=?""",
            (campaign_id, quest_id, node_id, evaluation_key, transition_kind),
        ).fetchone()
        if prior:
            row = db.execute(
                """SELECT * FROM quest_transition_receipts WHERE campaign_id=? AND quest_id=?
                   AND node_id=? AND evaluation_key=? AND transition_kind=?""",
                (campaign_id, quest_id, node_id, evaluation_key, transition_kind),
            ).fetchone()
            return self._decode_receipt(row), False
        if transition_kind in {"quest_completed", "quest_failed"}:
            db.execute(
                "UPDATE quests SET status=?,updated_at=? WHERE campaign_id=? AND id=?",
                (after_status, self.e._now(), campaign_id, quest_id),
            )
        else:
            db.execute(
                """UPDATE quest_nodes SET status=?,updated_at=?
                   WHERE campaign_id=? AND quest_id=? AND id=?""",
                (after_status, self.e._now(), campaign_id, quest_id, node_id),
            )
        labels = {
            "activated": "activated",
            "completed": "completed",
            "failed": "failed",
            "deadline": "failed at its deadline",
            "branch_activated": "activated by a branch",
            "branch_skipped": "was skipped by an exclusive branch",
            "quest_completed": "completed",
            "quest_failed": "failed",
        }
        event_type = {
            "activated": "quest_node_activated",
            "completed": "quest_node_completed",
            "failed": "quest_node_failed",
            "deadline": "quest_node_deadline_failed",
            "branch_activated": "quest_branch_activated",
            "branch_skipped": "quest_branch_skipped",
            "quest_completed": "quest_completed",
            "quest_failed": "quest_failed",
        }[transition_kind]
        event_id = self._emit_event_db(
            db,
            campaign_id,
            revision,
            when,
            emit,
            event_type=event_type,
            summary=f"Quest {quest_id} node {node_id} {labels[transition_kind]}",
            payload={
                "quest_id": quest_id,
                "node_id": node_id,
                "transition_kind": transition_kind,
                "before_status": before_status,
                "after_status": after_status,
                "detail": detail,
            },
            region=detail.get("region"),
            actor_id=detail.get("actor_id"),
            target_id=detail.get("target_id"),
            source_event_id=source_event_id,
            visibility=self._visibility_db(db, campaign_id, quest_id),
        )
        return self._receipt_db(
            db,
            campaign_id,
            quest_id,
            node_id,
            evaluation_key=evaluation_key,
            transition_kind=transition_kind,
            before_status=before_status,
            after_status=after_status,
            source_event_id=source_event_id,
            emitted_event_ids=[event_id],
            detail=detail,
            when=when,
            revision=revision,
        )

    def _outcome_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        quest_id: str,
        node: sqlite3.Row,
        *,
        context: dict[str, Any],
        event: dict[str, Any] | None,
        when: datetime,
    ) -> dict[str, Any]:
        failure_condition = self.e._loads(node["failure_json"])
        success_condition = self.e._loads(node["success_json"])
        failure = bool(failure_condition) and self.evaluate_condition_db(
            db,
            campaign_id,
            failure_condition,
            context=context,
            when=when,
            event=event,
        )["passed"]
        success = bool(success_condition) and self.evaluate_condition_db(
            db,
            campaign_id,
            success_condition,
            context=context,
            when=when,
            event=event,
        )["passed"]
        deadline = _utc(node["deadline_world_time"]) if node["deadline_world_time"] else None
        deadline_due = deadline is not None and when >= deadline
        success_in_time = success and (deadline is None or when <= deadline)
        if failure:
            selected = "failed"
        elif success_in_time:
            selected = "completed"
        elif deadline_due:
            selected = "deadline"
        else:
            selected = None
        return {
            "selected": selected,
            "failure": failure,
            "success": success,
            "success_in_time": success_in_time,
            "deadline_due": deadline_due,
            "deadline_world_time": deadline.isoformat() if deadline else None,
            "policy": "failure_then_in_time_success_then_deadline",
        }

    def _branches_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        quest_id: str,
        node: sqlite3.Row,
        *,
        context: dict[str, Any],
        event: dict[str, Any] | None,
        when: datetime,
        revision: int,
        evaluation_key: str,
        source_event_id: int | None,
        emit: Callable[..., Any] | None,
    ) -> list[dict[str, Any]]:
        edges = db.execute(
            """SELECT * FROM quest_edges WHERE campaign_id=? AND quest_id=?
               AND from_node=? ORDER BY priority,to_node""",
            (campaign_id, quest_id, node["id"]),
        ).fetchall()
        if not edges:
            return []
        eligible = []
        for edge in edges:
            condition = self.e._loads(edge["condition_json"])
            if not condition or self.evaluate_condition_db(
                db,
                campaign_id,
                condition,
                context=context,
                when=when,
                event=event,
            )["passed"]:
                eligible.append(edge)
        state = self.e._loads(node["state_json"])
        mode = str(state.get("branch_mode", "first"))
        selected = eligible[:1] if mode == "first" else eligible
        receipts = []
        selected_targets = {str(edge["to_node"]) for edge in selected}
        for edge in selected:
            target = db.execute(
                """SELECT status FROM quest_nodes WHERE campaign_id=? AND quest_id=? AND id=?""",
                (campaign_id, quest_id, edge["to_node"]),
            ).fetchone()
            if target and target["status"] == "inactive":
                receipt, created = self._transition_db(
                    db,
                    campaign_id,
                    quest_id,
                    str(edge["to_node"]),
                    transition_kind="branch_activated",
                    before_status="inactive",
                    after_status="active",
                    evaluation_key=evaluation_key,
                    source_event_id=source_event_id,
                    detail={"from_node": node["id"], "priority": int(edge["priority"])},
                    revision=revision,
                    when=when,
                    emit=emit,
                )
                if created:
                    receipts.append(receipt)
        if mode == "first" and selected:
            for edge in edges:
                target_id = str(edge["to_node"])
                if target_id in selected_targets:
                    continue
                target = db.execute(
                    """SELECT status FROM quest_nodes WHERE campaign_id=? AND quest_id=? AND id=?""",
                    (campaign_id, quest_id, target_id),
                ).fetchone()
                if target and target["status"] == "inactive":
                    receipt, created = self._transition_db(
                        db,
                        campaign_id,
                        quest_id,
                        target_id,
                        transition_kind="branch_skipped",
                        before_status="inactive",
                        after_status="skipped",
                        evaluation_key=evaluation_key,
                        source_event_id=source_event_id,
                        detail={"from_node": node["id"], "selected": sorted(selected_targets)},
                        revision=revision,
                        when=when,
                        emit=emit,
                    )
                    if created:
                        receipts.append(receipt)
        return receipts

    def _process_node_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        quest_id: str,
        node: sqlite3.Row,
        *,
        context: dict[str, Any],
        event: dict[str, Any] | None,
        when: datetime,
        revision: int,
        evaluation_key: str,
        emit: Callable[..., Any] | None,
    ) -> list[dict[str, Any]]:
        source_event_id = int(event["id"]) if event else None
        receipts: list[dict[str, Any]] = []
        status = str(node["status"])
        if status == "inactive" and event is not None:
            trigger = self.e._loads(node["trigger_json"])
            if trigger and self.evaluate_condition_db(
                db,
                campaign_id,
                trigger,
                context=context,
                when=when,
                event=event,
            )["passed"]:
                receipt, created = self._transition_db(
                    db,
                    campaign_id,
                    quest_id,
                    str(node["id"]),
                    transition_kind="activated",
                    before_status="inactive",
                    after_status="active",
                    evaluation_key=evaluation_key,
                    source_event_id=source_event_id,
                    detail={"trigger_event_type": event["event_type"]},
                    revision=revision,
                    when=when,
                    emit=emit,
                )
                if created:
                    receipts.append(receipt)
                status = "active"
        if status != "active":
            return receipts
        current = db.execute(
            """SELECT * FROM quest_nodes WHERE campaign_id=? AND quest_id=? AND id=?""",
            (campaign_id, quest_id, node["id"]),
        ).fetchone()
        outcome = self._outcome_db(
            db,
            campaign_id,
            quest_id,
            current,
            context=context,
            event=event,
            when=when,
        )
        selected = outcome["selected"]
        if selected is None:
            return receipts
        after = "completed" if selected == "completed" else "failed"
        transition_kind = selected
        receipt, created = self._transition_db(
            db,
            campaign_id,
            quest_id,
            str(node["id"]),
            transition_kind=transition_kind,
            before_status="active",
            after_status=after,
            evaluation_key=evaluation_key,
            source_event_id=source_event_id,
            detail=outcome,
            revision=revision,
            when=when,
            emit=emit,
        )
        if not created:
            return receipts
        receipts.append(receipt)
        if selected != "completed":
            quest_receipt, quest_created = self._transition_db(
                db,
                campaign_id,
                quest_id,
                str(node["id"]),
                transition_kind="quest_failed",
                before_status="active",
                after_status="failed",
                evaluation_key=evaluation_key,
                source_event_id=source_event_id,
                detail={"reason": selected, "node_id": node["id"]},
                revision=revision,
                when=when,
                emit=emit,
            )
            if quest_created:
                receipts.append(quest_receipt)
            return receipts
        branch_receipts = self._branches_db(
            db,
            campaign_id,
            quest_id,
            current,
            context=context,
            event=event,
            when=when,
            revision=revision,
            evaluation_key=evaluation_key,
            source_event_id=source_event_id,
            emit=emit,
        )
        receipts.extend(branch_receipts)
        state = self.e._loads(current["state_json"])
        outgoing = int(
            db.execute(
                """SELECT COUNT(*) n FROM quest_edges WHERE campaign_id=? AND quest_id=?
                   AND from_node=?""",
                (campaign_id, quest_id, node["id"]),
            ).fetchone()["n"]
        )
        if bool(state.get("terminal")) or outgoing == 0:
            quest_receipt, quest_created = self._transition_db(
                db,
                campaign_id,
                quest_id,
                str(node["id"]),
                transition_kind="quest_completed",
                before_status="active",
                after_status="completed",
                evaluation_key=evaluation_key,
                source_event_id=source_event_id,
                detail={"terminal_node": node["id"]},
                revision=revision,
                when=when,
                emit=emit,
            )
            if quest_created:
                receipts.append(quest_receipt)
        elif not branch_receipts:
            quest_receipt, quest_created = self._transition_db(
                db,
                campaign_id,
                quest_id,
                str(node["id"]),
                transition_kind="quest_failed",
                before_status="active",
                after_status="failed",
                evaluation_key=evaluation_key,
                source_event_id=source_event_id,
                detail={"reason": "no_eligible_branch", "node_id": node["id"]},
                revision=revision,
                when=when,
                emit=emit,
            )
            if quest_created:
                receipts.append(quest_receipt)
        return receipts

    def step_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        revision: int,
        when: datetime | str,
        emit: Callable[..., Any] | None,
    ) -> dict[str, Any]:
        self._require_schema_db(db)
        when_dt = _utc(when)
        campaign = db.execute(
            "SELECT 1 FROM campaigns WHERE id=?", (campaign_id,)
        ).fetchone()
        if not campaign:
            raise KeyError(f"unknown campaign: {campaign_id}")
        cursor = db.execute(
            "SELECT last_event_id FROM quest_event_cursors WHERE campaign_id=?",
            (campaign_id,),
        ).fetchone()
        last_event_id = int(cursor["last_event_id"]) if cursor else 0
        rows = db.execute(
            """SELECT * FROM events WHERE campaign_id=? AND id>?
               ORDER BY id LIMIT ?""",
            (campaign_id, last_event_id, MAX_EVENTS_PER_STEP + 1),
        ).fetchall()
        bounded_rows = rows[:MAX_EVENTS_PER_STEP]
        receipts: list[dict[str, Any]] = []
        processed_events = 0
        quests_validated: set[str] = set()
        for row in bounded_rows:
            event = self._decode_event(self.e, row)
            event_when = _utc(str(event["world_time"]))
            if event_when > when_dt:
                break
            active_quests = db.execute(
                """SELECT q.id,COALESCE(r.start_event_id,0) AS start_event_id
                   FROM quests q LEFT JOIN quest_runtime_instances r
                     ON r.campaign_id=q.campaign_id AND r.quest_id=q.id
                   WHERE q.campaign_id=? AND q.status='active' ORDER BY q.id""",
                (campaign_id,),
            ).fetchall()
            for quest_row in active_quests:
                if int(event["id"]) <= int(quest_row["start_event_id"]):
                    continue
                quest_id = str(quest_row["id"])
                if quest_id not in quests_validated:
                    self.validate_graph_db(db, campaign_id, quest_id)
                    quests_validated.add(quest_id)
                context = self._bindings_context_db(db, campaign_id, quest_id)
                context.update({"event": event, "world_time": event_when.isoformat()})
                nodes = db.execute(
                    """SELECT * FROM quest_nodes WHERE campaign_id=? AND quest_id=?
                       AND status IN ('inactive','active') ORDER BY id""",
                    (campaign_id, quest_id),
                ).fetchall()
                for node in nodes:
                    made = self._process_node_db(
                        db,
                        campaign_id,
                        quest_id,
                        node,
                        context=context,
                        event=event,
                        when=event_when,
                        revision=revision,
                        evaluation_key=f"event:{event['id']}",
                        emit=emit,
                    )
                    receipts.extend(made)
                    if len(receipts) > MAX_TRANSITIONS_PER_STEP:
                        raise RuntimeError("quest transition limit exceeded")
                    quest_status = db.execute(
                        "SELECT status FROM quests WHERE campaign_id=? AND id=?",
                        (campaign_id, quest_id),
                    ).fetchone()["status"]
                    if quest_status != "active":
                        break
            last_event_id = int(event["id"])
            processed_events += 1

        active_quests = db.execute(
            "SELECT id FROM quests WHERE campaign_id=? AND status='active' ORDER BY id",
            (campaign_id,),
        ).fetchall()
        for quest_row in active_quests:
            quest_id = str(quest_row["id"])
            if quest_id not in quests_validated:
                self.validate_graph_db(db, campaign_id, quest_id)
            context = self._bindings_context_db(db, campaign_id, quest_id)
            context["world_time"] = when_dt.isoformat()
            boundary_nodes = db.execute(
                """SELECT * FROM quest_nodes WHERE campaign_id=? AND quest_id=?
                   AND status IN ('inactive','active')
                   ORDER BY CASE WHEN deadline_world_time IS NULL THEN 1 ELSE 0 END,
                            deadline_world_time,id""",
                (campaign_id, quest_id),
            ).fetchall()
            for node in boundary_nodes:
                made = self._process_node_db(
                    db,
                    campaign_id,
                    quest_id,
                    node,
                    context=context,
                    event=None,
                    when=when_dt,
                    revision=revision,
                    evaluation_key=f"boundary:{when_dt.isoformat()}",
                    emit=emit,
                )
                receipts.extend(made)
                if len(receipts) > MAX_TRANSITIONS_PER_STEP:
                    raise RuntimeError("quest transition limit exceeded")
                if db.execute(
                    "SELECT status FROM quests WHERE campaign_id=? AND id=?",
                    (campaign_id, quest_id),
                ).fetchone()["status"] != "active":
                    break

        db.execute(
            """INSERT INTO quest_event_cursors(campaign_id,last_event_id,updated_at)
               VALUES(?,?,?) ON CONFLICT(campaign_id) DO UPDATE SET
               last_event_id=excluded.last_event_id,updated_at=excluded.updated_at""",
            (campaign_id, last_event_id, self.e._now()),
        )
        return {
            "campaign_id": campaign_id,
            "world_time": when_dt.isoformat(),
            "processed_events": processed_events,
            "last_event_id": last_event_id,
            "transitions": len(receipts),
            "receipts": receipts,
            "more_events": len(rows) > MAX_EVENTS_PER_STEP,
            "bounded": True,
        }

    def has_activity_db(self, db: sqlite3.Connection, campaign_id: str) -> bool:
        return db.execute(
            """SELECT 1 FROM quests q JOIN quest_nodes n
                 ON n.campaign_id=q.campaign_id AND n.quest_id=q.id
               WHERE q.campaign_id=? AND q.status='active'
                 AND n.status IN ('inactive','active') LIMIT 1""",
            (campaign_id,),
        ).fetchone() is not None

    def step(self, campaign_id: str, *, when: datetime | str | None = None) -> dict[str, Any]:
        with self.e._write_db() as db:
            campaign = db.execute(
                "SELECT world_time FROM campaigns WHERE id=?", (campaign_id,)
            ).fetchone()
            if not campaign:
                raise KeyError(f"unknown campaign: {campaign_id}")
            when_dt = _utc(when or str(campaign["world_time"]))
            revision = self.e._next_revision(db, campaign_id)

            def emit(
                event_type: str,
                summary: str,
                payload: dict[str, Any],
                region: str | None,
                event_when: datetime,
                **event_options: Any,
            ) -> int:
                return self.e._insert_event(
                    db,
                    campaign_id,
                    revision,
                    event_type,
                    summary,
                    region=region,
                    payload=payload,
                    world_time_override=event_when.isoformat(),
                    **event_options,
                )

            return self.step_db(db, campaign_id, revision, when_dt, emit)

    def step_if_active(
        self,
        campaign_id: str,
        *,
        when: datetime | str | None = None,
        max_batches: int = 4,
    ) -> dict[str, Any]:
        """Consume a bounded event backlog without creating idle revisions."""

        with self.e._write_db() as db:
            campaign = db.execute(
                "SELECT world_time,revision FROM campaigns WHERE id=?", (campaign_id,)
            ).fetchone()
            if not campaign:
                raise KeyError(f"unknown campaign: {campaign_id}")
            when_dt = _utc(when or str(campaign["world_time"]))
            if not self.has_activity_db(db, campaign_id):
                return {
                    "campaign_id": campaign_id,
                    "world_time": when_dt.isoformat(),
                    "processed_events": 0,
                    "transitions": 0,
                    "receipts": [],
                    "more_events": False,
                    "bounded": True,
                    "skipped": "no_active_quests",
                }
            cursor = db.execute(
                "SELECT last_event_id FROM quest_event_cursors WHERE campaign_id=?",
                (campaign_id,),
            ).fetchone()
            last_event_id = int(cursor["last_event_id"]) if cursor else 0
            next_event = db.execute(
                "SELECT id,world_time FROM events WHERE campaign_id=? AND id>? ORDER BY id LIMIT 1",
                (campaign_id, last_event_id),
            ).fetchone()
            if next_event is None or _utc(str(next_event["world_time"])) > when_dt:
                return {
                    "campaign_id": campaign_id,
                    "world_time": when_dt.isoformat(),
                    "processed_events": 0,
                    "last_event_id": last_event_id,
                    "transitions": 0,
                    "receipts": [],
                    "more_events": False,
                    "bounded": True,
                    "skipped": "no_pending_events",
                }
            # This is derived catch-up for already-versioned turn events, so
            # quest transitions and the cursor share the current authoritative
            # revision instead of manufacturing a second revision for one turn.
            revision = int(campaign["revision"])

            def emit(
                event_type: str,
                summary: str,
                payload: dict[str, Any],
                region: str | None,
                event_when: datetime,
                **event_options: Any,
            ) -> int:
                return self.e._insert_event(
                    db,
                    campaign_id,
                    revision,
                    event_type,
                    summary,
                    region=region,
                    payload=payload,
                    world_time_override=event_when.isoformat(),
                    **event_options,
                )

            batch_limit = max(1, min(int(max_batches), 16))
            aggregate = {
                "campaign_id": campaign_id,
                "world_time": when_dt.isoformat(),
                "processed_events": 0,
                "last_event_id": last_event_id,
                "transitions": 0,
                "receipts": [],
                "more_events": False,
                "bounded": True,
                "batches": 0,
            }
            for _ in range(batch_limit):
                tally = self.step_db(db, campaign_id, revision, when_dt, emit)
                aggregate["processed_events"] += int(tally["processed_events"])
                aggregate["last_event_id"] = int(tally["last_event_id"])
                aggregate["transitions"] += int(tally["transitions"])
                aggregate["receipts"].extend(tally["receipts"])
                aggregate["more_events"] = bool(tally["more_events"])
                aggregate["batches"] += 1
                if not tally["more_events"]:
                    break
            return aggregate

    def _resolve_template_bindings_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        specs: dict[str, Any],
        supplied: dict[str, Any],
    ) -> dict[str, dict[str, str]]:
        if len(specs) > MAX_TEMPLATE_BINDINGS:
            raise ValueError(f"template exceeds {MAX_TEMPLATE_BINDINGS} bindings")
        unknown = set(supplied) - set(specs)
        if unknown:
            raise ValueError(f"unknown template bindings: {sorted(unknown)}")
        resolved: dict[str, dict[str, str]] = {}
        for role in sorted(specs):
            self._id(role, f"binding role {role}")
            spec = specs[role]
            if not isinstance(spec, dict):
                raise TypeError(f"binding spec {role} must be an object")
            allowed = set(spec.get("kinds") or ([spec["kind"]] if spec.get("kind") else []))
            allowed = {str(kind).lower() for kind in allowed}
            if not allowed:
                raise ValueError(f"binding spec {role} requires at least one kind")
            value = supplied.get(role, spec.get("default"))
            if value is None:
                if spec.get("required", True):
                    raise ValueError(f"missing required binding: {role}")
                continue
            if isinstance(value, str) and ":" in value:
                kind, entity_id = value.split(":", 1)
            elif isinstance(value, dict):
                kind = str(value.get("kind") or value.get("type") or "")
                entity_id = str(value.get("id") or value.get("entity_id") or "")
            else:
                if len(allowed) != 1:
                    raise ValueError(f"binding {role} must include an entity kind")
                kind, entity_id = next(iter(allowed)), str(value)
            kind = kind.lower()
            if kind not in allowed:
                raise ValueError(f"binding {role} kind {kind!r} is not allowed")
            self._id(entity_id, f"binding {role} entity id")
            entity = self.mechanisms._entity_db(db, campaign_id, kind, entity_id)
            if not entity:
                raise KeyError(f"unknown authoritative binding: {kind}:{entity_id}")
            resolved[role] = {"kind": kind, "id": entity_id, "key": str(entity["key"])}
        return resolved

    def _substitute(self, value: Any, bindings: dict[str, dict[str, str]]) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            found = self._deep_get(bindings, value[1:])
            return found if found is not None else value
        if isinstance(value, dict):
            return {key: self._substitute(item, bindings) for key, item in value.items()}
        if isinstance(value, list):
            return [self._substitute(item, bindings) for item in value]
        return value

    def bind_template(
        self,
        campaign_id: str,
        template: dict[str, Any],
        bindings: dict[str, Any],
        *,
        quest_id: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(template, dict):
            raise TypeError("quest template must be an object")
        allowed = {"template_id", "bindings", "quest", "nodes", "edges", "visibility"}
        unknown = set(template) - allowed
        if unknown:
            raise ValueError(f"quest template has unsupported fields: {sorted(unknown)}")
        template_id = self._id(template.get("template_id"), "template_id")
        quest_spec = dict(template.get("quest") or {})
        node_specs = [dict(node) for node in template.get("nodes") or []]
        edge_specs = [dict(edge) for edge in template.get("edges") or []]
        visibility = str(template.get("visibility", "private")).lower()
        if visibility not in {"public", "private", "secret"}:
            raise ValueError("quest template visibility is invalid")
        manager = self.e._db() if dry_run else self.e._write_db()
        with manager as db:
            self._require_schema_db(db)
            resolved = self._resolve_template_bindings_db(
                db,
                campaign_id,
                dict(template.get("bindings") or {}),
                dict(bindings or {}),
            )
            bound_quest = self._substitute(quest_spec, resolved)
            bound_nodes = self._substitute(node_specs, resolved)
            bound_edges = self._substitute(edge_specs, resolved)
            effective_quest_id = self._id(
                quest_id or bound_quest.get("id"), "quest_id"
            )
            status = str(bound_quest.get("status", "active"))
            if status not in _QUEST_STATUSES:
                raise ValueError("quest template status is invalid")
            title = str(bound_quest.get("title") or "").strip()
            if not title:
                raise ValueError("quest template title is required")
            graph = self._validate_graph_data(bound_nodes, bound_edges)
            result = {
                "template_id": template_id,
                "quest_id": effective_quest_id,
                "quest": {
                    "id": effective_quest_id,
                    "title": title[:300],
                    "status": status,
                    "owner_id": bound_quest.get("owner_id"),
                    "region": bound_quest.get("region"),
                    "objectives": list(bound_quest.get("objectives") or []),
                    "state": dict(bound_quest.get("state") or {}),
                },
                "nodes": bound_nodes,
                "edges": bound_edges,
                "bindings": resolved,
                "visibility": visibility,
                "graph": graph,
                "dry_run": bool(dry_run),
            }
            _json_guard(result, "bound quest template")
            if dry_run:
                return result
            if db.execute(
                "SELECT 1 FROM quests WHERE campaign_id=? AND id=?",
                (campaign_id, effective_quest_id),
            ).fetchone():
                raise ValueError(f"quest already exists: {effective_quest_id}")
            now = self.e._now()
            world_time = str(
                db.execute(
                    "SELECT world_time FROM campaigns WHERE id=?", (campaign_id,)
                ).fetchone()["world_time"]
            )
            start_event_id = int(
                db.execute(
                    "SELECT COALESCE(MAX(id),0) FROM events WHERE campaign_id=?",
                    (campaign_id,),
                ).fetchone()[0]
            )
            db.execute(
                """INSERT INTO quests(
                       campaign_id,id,title,status,owner_id,region,objectives_json,state_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    campaign_id,
                    effective_quest_id,
                    result["quest"]["title"],
                    status,
                    result["quest"]["owner_id"],
                    result["quest"]["region"],
                    self.e._dumps(result["quest"]["objectives"]),
                    self.e._dumps(result["quest"]["state"]),
                    now,
                ),
            )
            for node in bound_nodes:
                db.execute(
                    """INSERT INTO quest_nodes(
                           campaign_id,quest_id,id,node_type,status,trigger_json,success_json,
                           failure_json,deadline_world_time,state_json,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        campaign_id,
                        effective_quest_id,
                        node["id"],
                        str(node.get("node_type", "objective")),
                        str(node.get("status", "inactive")),
                        self.e._dumps(node.get("trigger") or {}),
                        self.e._dumps(node.get("success") or {}),
                        self.e._dumps(node.get("failure") or {}),
                        node.get("deadline_world_time"),
                        self.e._dumps(node.get("state") or {}),
                        now,
                    ),
                )
            for edge in bound_edges:
                db.execute(
                    """INSERT INTO quest_edges(
                           campaign_id,quest_id,from_node,to_node,condition_json,priority,updated_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (
                        campaign_id,
                        effective_quest_id,
                        edge["from_node"],
                        edge["to_node"],
                        self.e._dumps(edge.get("condition") or {}),
                        int(edge.get("priority", 100)),
                        now,
                    ),
                )
            db.execute(
                """INSERT INTO quest_runtime_instances(
                       campaign_id,quest_id,template_id,bindings_json,visibility,
                       start_event_id,created_world_time,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    campaign_id,
                    effective_quest_id,
                    template_id,
                    self.e._dumps(resolved),
                    visibility,
                    start_event_id,
                    world_time,
                    now,
                ),
            )
            revision = self.e._next_revision(db, campaign_id)
            self.e._insert_event(
                db,
                campaign_id,
                revision,
                "quest_instantiated",
                f"Quest instantiated: {effective_quest_id}",
                region=result["quest"]["region"],
                actor_id=result["quest"]["owner_id"],
                payload={
                    "quest_id": effective_quest_id,
                    "template_id": template_id,
                    "node_count": graph["node_count"],
                },
                sensitivity={"public": "PUBLIC", "private": "PRIVATE", "secret": "SECRET"}[visibility],
                scope_type="WORLD" if visibility == "public" else "GM",
            )
            result["dry_run"] = False
            result["revision"] = revision
            return result

    def public_projection_db(
        self, db: sqlite3.Connection, campaign_id: str, quest_id: str
    ) -> dict[str, Any]:
        self._require_schema_db(db)
        quest = db.execute(
                "SELECT * FROM quests WHERE campaign_id=? AND id=?",
                (campaign_id, quest_id),
            ).fetchone()
        if not quest:
            raise KeyError(f"unknown quest: {quest_id}")
        visibility = self._visibility_db(db, campaign_id, quest_id)
        if visibility != "public":
            return {
                "id": quest_id,
                "status": str(quest["status"]),
                "visibility": visibility,
                "redacted": True,
            }
        nodes = [
            {
                "id": str(row["id"]),
                "node_type": str(row["node_type"]),
                "status": str(row["status"]),
                "deadline_world_time": row["deadline_world_time"],
            }
            for row in db.execute(
                """SELECT id,node_type,status,deadline_world_time FROM quest_nodes
                   WHERE campaign_id=? AND quest_id=? ORDER BY id""",
                (campaign_id, quest_id),
            ).fetchall()
        ]
        edges = [
            {
                "from_node": str(row["from_node"]),
                "to_node": str(row["to_node"]),
                "priority": int(row["priority"]),
            }
            for row in db.execute(
                """SELECT from_node,to_node,priority FROM quest_edges
                   WHERE campaign_id=? AND quest_id=? ORDER BY priority,from_node,to_node""",
                (campaign_id, quest_id),
            ).fetchall()
        ]
        return {
            "id": quest_id,
            "title": str(quest["title"]),
            "status": str(quest["status"]),
            "owner_id": quest["owner_id"],
            "region": quest["region"],
            "objectives": self.e._loads(quest["objectives_json"]),
            "visibility": visibility,
            "nodes": nodes,
            "edges": edges,
            "redacted": False,
        }

    def public_projection(self, campaign_id: str, quest_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            return self.public_projection_db(db, campaign_id, quest_id)

    def list_receipts(
        self, campaign_id: str, *, quest_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        sql = "SELECT * FROM quest_transition_receipts WHERE campaign_id=?"
        params: list[Any] = [campaign_id]
        if quest_id:
            sql += " AND quest_id=?"
            params.append(quest_id)
        sql += " ORDER BY world_time,receipt_id LIMIT ?"
        params.append(limit)
        with self.e._db() as db:
            self._require_schema_db(db)
            return [self._decode_receipt(row) for row in db.execute(sql, params).fetchall()]

    def dispatch(
        self, operation: str, campaign_id: str, payload: dict[str, Any] | None = None
    ) -> Any:
        data = dict(payload or {})
        if operation == "install_schema":
            self.install_schema()
            return {"installed": True}
        if operation == "validate_graph":
            return self.validate_graph(campaign_id, data["quest_id"])
        if operation == "bind_template":
            return self.bind_template(
                campaign_id,
                data["template"],
                data.get("bindings") or {},
                quest_id=data.get("quest_id"),
                dry_run=bool(data.get("dry_run", True)),
            )
        if operation == "step":
            return self.step(campaign_id, when=data.get("when"))
        if operation == "step_if_active":
            return self.step_if_active(
                campaign_id,
                when=data.get("when"),
                max_batches=int(data.get("max_batches", 4)),
            )
        if operation == "public_projection":
            return self.public_projection(campaign_id, data["quest_id"])
        if operation == "list_receipts":
            return self.list_receipts(
                campaign_id,
                quest_id=data.get("quest_id"),
                limit=int(data.get("limit", 100)),
            )
        raise ValueError(f"unknown quest runtime operation: {operation}")


__all__ = ["QUEST_SCHEMA", "QuestRuntimeKernel"]
