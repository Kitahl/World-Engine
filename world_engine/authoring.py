from __future__ import annotations

import json
import math
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .simulation import CADENCE_SECONDS, TARGETS

if TYPE_CHECKING:
    from .engine import WorldEngine

AUTHORING_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS world_bible (
    campaign_id TEXT PRIMARY KEY,
    bible_json TEXT NOT NULL DEFAULT '{}',
    canon_version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS npc_archetypes (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    needs_json TEXT NOT NULL DEFAULT '{}',
    actions_json TEXT NOT NULL DEFAULT '[]',
    weights_json TEXT NOT NULL DEFAULT '{}',
    routine_json TEXT NOT NULL DEFAULT '{}',
    visual_json TEXT NOT NULL DEFAULT '{}',
    tags_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sim_rule_templates (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    archetype TEXT NOT NULL,
    cadence TEXT NOT NULL DEFAULT 'day',
    target TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 100,
    params_json TEXT NOT NULL DEFAULT '{}',
    tags_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS recipes (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    kind TEXT NOT NULL,
    inputs_json TEXT NOT NULL DEFAULT '{}',
    output_item_id TEXT,
    output_qty REAL NOT NULL DEFAULT 1,
    skill TEXT,
    dc INTEGER NOT NULL DEFAULT 10,
    hours REAL NOT NULL DEFAULT 1,
    station_tag TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS authoring_batches (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'bootstrap',
    status TEXT NOT NULL DEFAULT 'staged',
    payload_json TEXT NOT NULL,
    validation_json TEXT NOT NULL DEFAULT '{}',
    dry_run_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    promoted_at TEXT,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS canon_locks (
    campaign_id TEXT NOT NULL,
    object_kind TEXT NOT NULL,
    object_id TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT 'player touched',
    locked_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,object_kind,object_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS content_gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL,
    gap_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    scope_id TEXT,
    summary TEXT NOT NULL,
    context_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved','suppressed')),
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE(campaign_id,gap_key),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_authoring_batches_status ON authoring_batches(campaign_id,status,created_at);
CREATE INDEX IF NOT EXISTS idx_content_gaps_open ON content_gaps(campaign_id,status,id);
CREATE INDEX IF NOT EXISTS idx_npc_archetypes_campaign ON npc_archetypes(campaign_id,id);
"""

_ALLOWED_ARCHETYPES = {"drift", "schedule", "stock", "chance", "spread", "decide"}
_ALLOWED_CURVES = {"linear", "quadratic", "urgent", "threshold"}
_ALLOWED_REPEAT = {"once_per_cascade", "count_limited"}
_ALLOWED_RECIPE_KINDS = {"craft", "alchemy", "smith", "cook", "trap", "harvest", "ritual", "loot"}


class AuthoringKernel:
    """Safe authoring-time content pipeline.

    The model may propose structured rows, but never owns runtime decisions.
    Rows are staged, validated, dry-run in a scratch DB, then atomically promoted.
    """

    def __init__(self, engine: "WorldEngine"):
        self.e = engine

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def set_world_bible(self, campaign_id: str, bible: dict[str, Any], *, canon_version: int | None = None) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        with self.e._write_db() as db:
            self._assert_unlocked(db, campaign_id, "world_bible", "global")
            old = db.execute("SELECT canon_version FROM world_bible WHERE campaign_id=?", (campaign_id,)).fetchone()
            version = int(canon_version if canon_version is not None else ((old["canon_version"] + 1) if old else 1))
            db.execute(
                """INSERT INTO world_bible(campaign_id,bible_json,canon_version,updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(campaign_id) DO UPDATE SET bible_json=excluded.bible_json,canon_version=excluded.canon_version,updated_at=excluded.updated_at""",
                (campaign_id, self.e._dumps(bible), version, self.e._now()),
            )
        return self.get_world_bible(campaign_id)

    def get_world_bible(self, campaign_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            row = db.execute("SELECT * FROM world_bible WHERE campaign_id=?", (campaign_id,)).fetchone()
        if not row:
            return {"campaign_id": campaign_id, "canon_version": 0, "bible": {}}
        d = dict(row)
        d["bible"] = self.e._loads(d.pop("bible_json"))
        return d

    def lock(self, campaign_id: str, object_kind: str, object_id: str, reason: str = "player touched") -> dict[str, Any]:
        object_kind = str(object_kind).strip().lower()[:80]
        object_id = str(object_id).strip()[:120]
        if not object_kind or not object_id:
            raise ValueError("object_kind and object_id are required")
        with self.e._write_db() as db:
            db.execute(
                """INSERT INTO canon_locks(campaign_id,object_kind,object_id,reason,locked_at) VALUES(?,?,?,?,?)
                   ON CONFLICT(campaign_id,object_kind,object_id) DO UPDATE SET reason=excluded.reason,locked_at=excluded.locked_at""",
                (campaign_id, object_kind, object_id, reason[:500], self.e._now()),
            )
        return {"campaign_id": campaign_id, "object_kind": object_kind, "object_id": object_id, "locked": True, "reason": reason}

    def _assert_unlocked(self, db: sqlite3.Connection, campaign_id: str, object_kind: str, object_id: str) -> None:
        row = db.execute(
            "SELECT reason FROM canon_locks WHERE campaign_id=? AND object_kind=? AND object_id=?",
            (campaign_id, object_kind, object_id),
        ).fetchone()
        if row:
            raise ValueError(f"canon-locked {object_kind}:{object_id}; explicit revision required ({row['reason']})")

    def stage(self, campaign_id: str, batch_id: str, payload: dict[str, Any], *, mode: str = "bootstrap") -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        batch_id = self.e._clean_id(batch_id)
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        with self.e._write_db() as db:
            db.execute(
                """INSERT INTO authoring_batches(campaign_id,id,mode,status,payload_json,validation_json,dry_run_json,created_at,updated_at,promoted_at)
                   VALUES(?,?,?,'staged',?,'{}','{}',?,?,NULL)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET mode=excluded.mode,status='staged',payload_json=excluded.payload_json,
                   validation_json='{}',dry_run_json='{}',updated_at=excluded.updated_at,promoted_at=NULL""",
                (campaign_id, batch_id, mode[:40], self.e._dumps(payload), self.e._now(), self.e._now()),
            )
        return self.get_batch(campaign_id, batch_id)

    def get_batch(self, campaign_id: str, batch_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            row = db.execute("SELECT * FROM authoring_batches WHERE campaign_id=? AND id=?", (campaign_id, batch_id)).fetchone()
        if not row:
            raise KeyError(f"unknown authoring batch: {batch_id}")
        d = dict(row)
        d["payload"] = self.e._loads(d.pop("payload_json"))
        d["validation"] = self.e._loads(d.pop("validation_json"))
        d["dry_run"] = self.e._loads(d.pop("dry_run_json"))
        return d

    def validate(self, campaign_id: str, batch_id: str) -> dict[str, Any]:
        batch = self.get_batch(campaign_id, batch_id)
        result = self.validate_payload(campaign_id, batch["payload"])
        status = "validated" if result["valid"] else "rejected"
        with self.e._write_db() as db:
            db.execute("UPDATE authoring_batches SET status=?,validation_json=?,updated_at=? WHERE campaign_id=? AND id=?", (status, self.e._dumps(result), self.e._now(), campaign_id, batch_id))
        return result

    def validate_payload(self, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        def err(path: str, msg: str) -> None:
            errors.append({"path": path, "message": msg})

        def warn(path: str, msg: str) -> None:
            warnings.append({"path": path, "message": msg})

        # Existing and staged references.
        with self.e._db() as db:
            existing_locations = {r["id"] for r in db.execute("SELECT id FROM locations WHERE campaign_id=?", (campaign_id,))}
            existing_items = {r["id"] for r in db.execute("SELECT id FROM item_defs WHERE campaign_id=?", (campaign_id,))}
            locked = {(r["object_kind"], r["object_id"]) for r in db.execute("SELECT object_kind,object_id FROM canon_locks WHERE campaign_id=?", (campaign_id,))}
        staged_items = {str(x.get("id")) for x in payload.get("items", []) if x.get("id")}
        staged_locations = {str(x.get("id")) for x in payload.get("locations", []) if x.get("id")}
        locations = existing_locations | staged_locations
        items = existing_items | staged_items

        if "world_bible" in payload and not isinstance(payload["world_bible"], dict):
            err("world_bible", "must be an object")
        if "world_bible" in payload and ("world_bible", "global") in locked:
            err("world_bible", "canon-locked world_bible cannot be overwritten")

        seen_arch: set[str] = set()
        for i, a in enumerate(payload.get("archetypes", [])):
            aid = str(a.get("id", ""))
            p = f"archetypes[{i}]"
            if not aid:
                err(p + ".id", "required")
                continue
            if aid in seen_arch:
                err(p + ".id", "duplicate archetype id")
            seen_arch.add(aid)
            if ("archetype", aid) in locked:
                err(p, "canon-locked archetype cannot be overwritten")
            for need, cfg in (a.get("needs") or {}).items():
                if not isinstance(cfg, dict):
                    err(f"{p}.needs.{need}", "must be an object")
                    continue
                for key in ("value", "baseline"):
                    if key in cfg and not 0 <= float(cfg[key]) <= 100:
                        err(f"{p}.needs.{need}.{key}", "must be 0..100")
                if not 0 <= float(cfg.get("drift_per_day", 0)) <= 1:
                    err(f"{p}.needs.{need}.drift_per_day", "must be 0..1")
                if str(cfg.get("curve", "quadratic")) not in _ALLOWED_CURVES:
                    err(f"{p}.needs.{need}.curve", f"must be one of {sorted(_ALLOWED_CURVES)}")
            for j, action in enumerate(a.get("actions") or []):
                self._validate_action(action, f"{p}.actions[{j}]", locations, err)

        templates = {str(t.get("id")): t for t in payload.get("rule_templates", []) if t.get("id")}
        for i, t in enumerate(payload.get("rule_templates", [])):
            self._validate_rule(t, f"rule_templates[{i}]", err, warn)
        for i, r in enumerate(payload.get("rules", [])):
            merged = dict(templates.get(str(r.get("template_id", "")), {}))
            merged.update(r)
            self._validate_rule(merged, f"rules[{i}]", err, warn)
            rid = str(r.get("id", ""))
            if rid and ("rule", rid) in locked:
                err(f"rules[{i}]", "canon-locked rule cannot be overwritten")

        for i, r in enumerate(payload.get("reactions", [])):
            p = f"reactions[{i}]"
            rid = str(r.get("id", ""))
            if not rid:
                err(p + ".id", "required")
            if rid and ("reaction", rid) in locked:
                err(p, "canon-locked reaction cannot be overwritten")
            if not r.get("trigger_event_type"):
                err(p + ".trigger_event_type", "required")
            if "repeat_policy" not in r:
                err(p + ".repeat_policy", "must be explicit")
            elif r.get("repeat_policy") not in _ALLOWED_REPEAT:
                err(p + ".repeat_policy", f"must be one of {sorted(_ALLOWED_REPEAT)}")
            if r.get("repeat_policy") == "count_limited" and not 1 <= int(r.get("repeat_limit", 0)) <= 8:
                err(p + ".repeat_limit", "must be 1..8")
            prob = float(r.get("probability", 1.0))
            if not 0 <= prob <= 1:
                err(p + ".probability", "must be 0..1")
            effects = r.get("effects") or []
            if not effects:
                err(p + ".effects", "must contain at least one effect")
            self_emit = any(str(e.get("type", "")).lower() == "emit" and str(e.get("event_type", "")) == str(r.get("trigger_event_type", "")) for e in effects if isinstance(e, dict))
            if self_emit and r.get("repeat_policy") != "once_per_cascade" and int(r.get("repeat_limit", 99)) > 3:
                err(p, "self-triggering reaction must be once_per_cascade or count_limited<=3")

        for i, recipe in enumerate(payload.get("recipes", [])):
            p = f"recipes[{i}]"
            kind = str(recipe.get("kind", ""))
            if kind not in _ALLOWED_RECIPE_KINDS:
                err(p + ".kind", f"must be one of {sorted(_ALLOWED_RECIPE_KINDS)}")
            if int(recipe.get("dc", 10)) < 1 or int(recipe.get("dc", 10)) > 40:
                err(p + ".dc", "must be 1..40")
            if float(recipe.get("hours", 1)) < 0:
                err(p + ".hours", "must be >=0")
            for item_id, qty in (recipe.get("inputs") or {}).items():
                if str(item_id) not in items:
                    err(p + ".inputs", f"unknown item reference: {item_id}")
                if float(qty) <= 0:
                    err(p + ".inputs", f"quantity for {item_id} must be >0")
            out = recipe.get("output_item_id")
            if out and str(out) not in items:
                err(p + ".output_item_id", f"unknown item reference: {out}")

        arch_ids = seen_arch | {r["id"] for r in self._existing_archetypes(campaign_id)}
        for i, npc in enumerate(payload.get("npcs", [])):
            p = f"npcs[{i}]"
            nid = str(npc.get("id", ""))
            if not nid or not npc.get("name"):
                err(p, "id and name are required")
            if nid and ("npc", nid) in locked:
                err(p, "canon-locked NPC cannot be overwritten")
            aid = npc.get("archetype_id")
            if aid and str(aid) not in arch_ids:
                err(p + ".archetype_id", f"unknown archetype: {aid}")
            loc = npc.get("location")
            if loc and str(loc) not in locations:
                err(p + ".location", f"unknown location: {loc}")

        return {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "counts": {k: len(payload.get(k, [])) if isinstance(payload.get(k), list) else (1 if k in payload else 0) for k in ("archetypes", "rule_templates", "rules", "reactions", "recipes", "items", "locations", "npcs", "world_bible")},
        }

    def _validate_action(self, action: dict[str, Any], path: str, locations: set[str], err) -> None:
        if not action.get("id") and not action.get("action_id"):
            err(path + ".id", "required")
        if float(action.get("cost_hours", 8)) < 0:
            err(path + ".cost_hours", "must be >=0")
        if action.get("location") and str(action["location"]) not in locations:
            err(path + ".location", f"unknown location: {action['location']}")
        satisfies = action.get("satisfies") or {}
        for key, value in satisfies.items():
            if not -100 <= float(value) <= 100:
                err(path + ".satisfies", f"{key} must be -100..100")
        for i, c in enumerate(action.get("considerations") or []):
            if abs(float(c.get("weight", 1))) > 20:
                err(f"{path}.considerations[{i}].weight", "absolute weight must be <=20")

    def _validate_rule(self, r: dict[str, Any], path: str, err, warn) -> None:
        archetype = str(r.get("archetype", "")).lower()
        if archetype not in _ALLOWED_ARCHETYPES:
            err(path + ".archetype", f"must be one of {sorted(_ALLOWED_ARCHETYPES)}")
            return
        if str(r.get("cadence", "day")) not in CADENCE_SECONDS:
            err(path + ".cadence", "must be hour, day, or week")
        target = str(r.get("target", ""))
        params = dict(r.get("params") or {})
        if archetype == "drift":
            if target not in TARGETS:
                err(path + ".target", f"unsupported drift target: {target}")
            k = float(params.get("k", 0.1))
            if not 0 < k <= 0.5:
                err(path + ".params.k", "authoring gate requires 0 < k <= 0.5")
            elif k < 0.005:
                warn(path + ".params.k", "very slow drift; allowed because v3.4+ carries fractional residuals, but verify intent in dry-run")
        elif archetype == "stock":
            if target != "resource_nodes.qty":
                err(path + ".target", "stock target must be resource_nodes.qty")
        elif archetype == "chance":
            p = float(params.get("p", params.get("p_day", 0.02)))
            if not 0 <= p <= 0.2:
                err(path + ".params.p", "generated chance probability must be 0..0.2; higher values require explicit manual configuration")
        elif archetype == "spread":
            for key in ("rate", "p", "probability"):
                if key in params and not 0 <= float(params[key]) <= 1:
                    err(path + f".params.{key}", "must be 0..1")
        elif archetype == "decide":
            top_k = int(params.get("top_k", 3))
            temp = float(params.get("temperature", 0.35))
            if not 1 <= top_k <= 8:
                err(path + ".params.top_k", "must be 1..8")
            if not 0 <= temp <= 5:
                err(path + ".params.temperature", "must be 0..5")

    def _existing_archetypes(self, campaign_id: str) -> list[dict[str, Any]]:
        with self.e._db() as db:
            rows = db.execute("SELECT id,name FROM npc_archetypes WHERE campaign_id=? ORDER BY id", (campaign_id,)).fetchall()
        return [dict(r) for r in rows]

    def dry_run(self, campaign_id: str, batch_id: str, *, days: int = 365) -> dict[str, Any]:
        days = max(1, min(int(days), 18250))
        validation = self.validate(campaign_id, batch_id)
        if not validation["valid"]:
            return {"passed": False, "reason": "static_validation_failed", "validation": validation}
        batch = self.get_batch(campaign_id, batch_id)
        fd, temp_name = tempfile.mkstemp(prefix="world_engine_authoring_", suffix=".sqlite3")
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            src = sqlite3.connect(self.e.db_path)
            dst = sqlite3.connect(temp_path)
            src.backup(dst)
            dst.close(); src.close()
            from .engine import WorldEngine
            scratch = WorldEngine(temp_path)
            ak = AuthoringKernel(scratch)
            with scratch._write_db() as db:
                ak._promote_payload_db(db, campaign_id, batch["payload"], allow_locked=True)
                # Validation needs the causal queue and state transitions, not tens of
                # thousands of ordinary NPC decision log rows. Keep sim_decision in
                # the cascade queue but suppress its scratch-ledger persistence.
                for rr in db.execute("SELECT id,params_json FROM sim_rules WHERE campaign_id=? AND archetype='decide'", (campaign_id,)).fetchall():
                    pp = scratch._loads(rr["params_json"] or "{}")
                    pp["_dry_run_silent"] = True
                    db.execute("UPDATE sim_rules SET params_json=? WHERE campaign_id=? AND id=?", (scratch._dumps(pp), campaign_id, rr["id"]))
            before = ak.world_digest(campaign_id)
            remaining = days
            while remaining:
                chunk = min(365, remaining)
                scratch.advance_world(campaign_id, chunk * 1440, reason="authoring dry run", simulate=True)
                remaining -= chunk
            after = ak.world_digest(campaign_id)
            checks = self._dry_run_checks(before, after, days)
            warnings = self._dry_run_warnings(before, after, days)
            result = {"passed": all(c["passed"] for c in checks), "days": days, "before": before, "after": after, "checks": checks, "warnings": warnings}
        finally:
            try:
                temp_path.unlink(missing_ok=True)
                Path(str(temp_path) + "-wal").unlink(missing_ok=True)
                Path(str(temp_path) + "-shm").unlink(missing_ok=True)
            except Exception:
                pass
        with self.e._write_db() as db:
            db.execute("UPDATE authoring_batches SET status=?,dry_run_json=?,updated_at=? WHERE campaign_id=? AND id=?", ("dry_run_passed" if result["passed"] else "dry_run_failed", self.e._dumps(result), self.e._now(), campaign_id, batch_id))
        return result

    @staticmethod
    def _dry_run_checks(before: dict[str, Any], after: dict[str, Any], days: int) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        p0, p1 = int(before["population_alive"]), int(after["population_alive"])
        if p0:
            lo = max(1, int(math.floor(p0 * 0.2)))
            hi = max(p0 + 25, int(math.ceil(p0 * 5.0)))
            checks.append({"name": "population_band", "passed": lo <= p1 <= hi, "before": p0, "after": p1, "allowed": [lo, hi]})
        else:
            checks.append({"name": "population_band", "passed": True, "before": 0, "after": p1, "note": "no initial cohort"})
        checks.append({"name": "cascade_termination", "passed": int(after.get("cascade_capped_events", 0)) == int(before.get("cascade_capped_events", 0)), "capped_events": int(after.get("cascade_capped_events", 0)) - int(before.get("cascade_capped_events", 0))})
        saturation = after.get("saturation", {})
        total_rel = max(1, int(after.get("relationships", 0)))
        sat_rel = int(saturation.get("relationships_at_bound", 0))
        checks.append({"name": "relationship_saturation", "passed": sat_rel / total_rel <= 0.5, "saturated": sat_rel, "total": int(after.get("relationships", 0))})
        event_delta = int(after.get("event_count", 0)) - int(before.get("event_count", 0))
        checks.append({"name": "event_budget", "passed": event_delta <= max(10000, days * 200), "events": event_delta})
        return checks

    @staticmethod
    def _dry_run_warnings(before: dict[str, Any], after: dict[str, Any], days: int) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        if int(before.get("relationships", 0)) > 0:
            b = float(before.get("mean_abs_trust", 0)); a = float(after.get("mean_abs_trust", 0))
            if b > 0 and a < b * 0.25:
                warnings.append({"name": "social_entropy", "message": "mean absolute trust fell by more than 75%; review generative social rules", "before": b, "after": a})
        actions = after.get("action_distribution", {}) or {}
        total = sum(int(v) for v in actions.values())
        if total >= 10 and actions:
            top_action, top_n = max(actions.items(), key=lambda kv: int(kv[1]))
            if int(top_n) / total >= 0.95:
                warnings.append({"name": "action_monoculture", "message": "95%+ of agents ended on one action; verify utility diversity", "action": top_action, "share": round(int(top_n)/total, 3)})
        sat = after.get("saturation", {}) or {}
        need_count = int(sat.get("need_count", 0)); at_bound = int(sat.get("needs_at_bound", 0))
        if need_count and at_bound / need_count > 0.5:
            warnings.append({"name": "need_saturation", "message": "more than half of needs saturated at 0/100", "at_bound": at_bound, "total": need_count})
        depleted = [r["item_id"] for r in after.get("resources", []) if float(r.get("capacity", 0)) > 0 and float(r.get("qty", 0)) <= 0]
        if depleted:
            warnings.append({"name": "resource_depletion", "message": "one or more resource stocks ended at zero", "items": depleted[:20]})
        return warnings

    def promote(self, campaign_id: str, batch_id: str) -> dict[str, Any]:
        batch = self.get_batch(campaign_id, batch_id)
        if batch["status"] != "dry_run_passed" or not batch.get("dry_run", {}).get("passed"):
            raise ValueError("batch must pass static validation and dry-run before promotion")
        with self.e._write_db() as db:
            self._promote_payload_db(db, campaign_id, batch["payload"], allow_locked=False)
            rev = self.e._next_revision(db, campaign_id)
            self.e._insert_event(db, campaign_id, rev, "authoring_promoted", f"Authoring batch {batch_id} promoted", payload={"batch_id": batch_id, "mode": batch["mode"]})
            db.execute("UPDATE authoring_batches SET status='promoted',promoted_at=?,updated_at=? WHERE campaign_id=? AND id=?", (self.e._now(), self.e._now(), campaign_id, batch_id))
        return self.get_batch(campaign_id, batch_id)

    def _promote_payload_db(self, db: sqlite3.Connection, campaign_id: str, payload: dict[str, Any], *, allow_locked: bool) -> None:
        now = self.e._now()
        def unlocked(kind: str, oid: str) -> None:
            if not allow_locked:
                self._assert_unlocked(db, campaign_id, kind, oid)

        if "world_bible" in payload:
            unlocked("world_bible", "global")
            old = db.execute("SELECT canon_version FROM world_bible WHERE campaign_id=?", (campaign_id,)).fetchone()
            version = int(old["canon_version"] + 1 if old else 1)
            db.execute("""INSERT INTO world_bible(campaign_id,bible_json,canon_version,updated_at) VALUES(?,?,?,?)
                        ON CONFLICT(campaign_id) DO UPDATE SET bible_json=excluded.bible_json,canon_version=excluded.canon_version,updated_at=excluded.updated_at""",
                       (campaign_id, self.e._dumps(payload["world_bible"]), version, now))

        for item in payload.get("items", []):
            iid = self.e._clean_id(str(item["id"])); unlocked("item", iid)
            db.execute("""INSERT INTO item_defs(campaign_id,id,name,base_price,effect_dice,tags_json,metadata_json,updated_at) VALUES(?,?,?,?,?,?,?,?)
                        ON CONFLICT(campaign_id,id) DO UPDATE SET name=excluded.name,base_price=excluded.base_price,effect_dice=excluded.effect_dice,tags_json=excluded.tags_json,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                       (campaign_id, iid, str(item.get("name", iid))[:200], float(item.get("base_price", 0)), item.get("effect_dice"), self.e._dumps(item.get("tags") or []), self.e._dumps(item.get("metadata") or {}), now))

        for loc in payload.get("locations", []):
            lid = self.e._clean_id(str(loc["id"])); unlocked("location", lid)
            db.execute("""INSERT INTO locations(campaign_id,id,name,region,description,x,y,realm_id,tags_json,state_json,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(campaign_id,id) DO UPDATE SET name=excluded.name,region=excluded.region,description=excluded.description,x=excluded.x,y=excluded.y,realm_id=excluded.realm_id,tags_json=excluded.tags_json,state_json=excluded.state_json,updated_at=excluded.updated_at""",
                       (campaign_id, lid, str(loc.get("name", lid))[:200], str(loc.get("region", "unknown"))[:200], str(loc.get("description", ""))[:5000], loc.get("x"), loc.get("y"), loc.get("realm_id"), self.e._dumps(loc.get("tags") or []), self.e._dumps(loc.get("state") or {}), now))

        archetypes = {str(a["id"]): a for a in payload.get("archetypes", [])}
        for aid, a in archetypes.items():
            aid = self.e._clean_id(aid); unlocked("archetype", aid)
            db.execute("""INSERT INTO npc_archetypes(campaign_id,id,name,needs_json,actions_json,weights_json,routine_json,visual_json,tags_json,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(campaign_id,id) DO UPDATE SET name=excluded.name,needs_json=excluded.needs_json,actions_json=excluded.actions_json,weights_json=excluded.weights_json,routine_json=excluded.routine_json,visual_json=excluded.visual_json,tags_json=excluded.tags_json,updated_at=excluded.updated_at""",
                       (campaign_id, aid, str(a.get("name", aid))[:200], self.e._dumps(a.get("needs") or {}), self.e._dumps(a.get("actions") or []), self.e._dumps(a.get("weights") or {}), self.e._dumps(a.get("routine") or {}), self.e._dumps(a.get("visual") or {}), self.e._dumps(a.get("tags") or []), now))

        templates = {str(t["id"]): t for t in payload.get("rule_templates", [])}
        for tid, t in templates.items():
            tid = self.e._clean_id(tid); unlocked("rule_template", tid)
            db.execute("""INSERT INTO sim_rule_templates(campaign_id,id,archetype,cadence,target,priority,params_json,tags_json,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(campaign_id,id) DO UPDATE SET archetype=excluded.archetype,cadence=excluded.cadence,target=excluded.target,priority=excluded.priority,params_json=excluded.params_json,tags_json=excluded.tags_json,updated_at=excluded.updated_at""",
                       (campaign_id, tid, t["archetype"], t.get("cadence", "day"), t.get("target", ""), int(t.get("priority", 100)), self.e._dumps(t.get("params") or {}), self.e._dumps(t.get("tags") or []), now))

        for r in payload.get("rules", []):
            rid = self.e._clean_id(str(r["id"])); unlocked("rule", rid)
            merged = dict(templates.get(str(r.get("template_id", "")), {})); merged.update(r)
            db.execute("""INSERT INTO sim_rules(campaign_id,id,archetype,enabled,cadence,target,priority,params_json,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(campaign_id,id) DO UPDATE SET archetype=excluded.archetype,enabled=excluded.enabled,cadence=excluded.cadence,target=excluded.target,priority=excluded.priority,params_json=excluded.params_json,updated_at=excluded.updated_at""",
                       (campaign_id, rid, merged["archetype"], int(bool(merged.get("enabled", True))), merged.get("cadence", "day"), merged.get("target", ""), int(merged.get("priority", 100)), self.e._dumps(merged.get("params") or {}), now))

        for r in payload.get("reactions", []):
            rid = self.e._clean_id(str(r["id"])); unlocked("reaction", rid)
            db.execute("""INSERT INTO sim_reactions(campaign_id,id,trigger_event_type,priority,selector_json,effects_json,probability,repeat_policy,repeat_limit,enabled,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(campaign_id,id) DO UPDATE SET trigger_event_type=excluded.trigger_event_type,priority=excluded.priority,selector_json=excluded.selector_json,effects_json=excluded.effects_json,probability=excluded.probability,repeat_policy=excluded.repeat_policy,repeat_limit=excluded.repeat_limit,enabled=excluded.enabled,updated_at=excluded.updated_at""",
                       (campaign_id, rid, str(r["trigger_event_type"])[:80], int(r.get("priority", 100)), self.e._dumps(r.get("selector") or {}), self.e._dumps(r.get("effects") or []), float(r.get("probability", 1)), r.get("repeat_policy", "once_per_cascade"), int(r.get("repeat_limit", 1)), int(bool(r.get("enabled", True))), now))

        for recipe in payload.get("recipes", []):
            rid = self.e._clean_id(str(recipe["id"])); unlocked("recipe", rid)
            db.execute("""INSERT INTO recipes(campaign_id,id,kind,inputs_json,output_item_id,output_qty,skill,dc,hours,station_tag,metadata_json,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(campaign_id,id) DO UPDATE SET kind=excluded.kind,inputs_json=excluded.inputs_json,output_item_id=excluded.output_item_id,output_qty=excluded.output_qty,skill=excluded.skill,dc=excluded.dc,hours=excluded.hours,station_tag=excluded.station_tag,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                       (campaign_id, rid, recipe["kind"], self.e._dumps(recipe.get("inputs") or {}), recipe.get("output_item_id"), float(recipe.get("output_qty", 1)), recipe.get("skill"), int(recipe.get("dc", 10)), float(recipe.get("hours", 1)), recipe.get("station_tag"), self.e._dumps(recipe.get("metadata") or {}), now))

        # Materialise NPC instances from archetypes. The archetype supplies defaults;
        # the instance may override only explicit thin fields.
        for npc in payload.get("npcs", []):
            nid = self.e._clean_id(str(npc["id"])); unlocked("npc", nid)
            aid = npc.get("archetype_id")
            arow = db.execute("SELECT * FROM npc_archetypes WHERE campaign_id=? AND id=?", (campaign_id, aid)).fetchone() if aid else None
            routine = self.e._loads(arow["routine_json"]) if arow else {}
            visual = self.e._loads(arow["visual_json"]) if arow else {}
            deviations = dict(npc.get("deviations") or {})
            routine.update(deviations.get("routine") or {})
            hp = int(npc.get("hp", deviations.get("hp", 8))); max_hp = int(npc.get("max_hp", deviations.get("max_hp", max(1, hp))))
            hp = max(0, min(hp, max_hp))
            db.execute("""INSERT INTO npcs(campaign_id,id,name,hp,max_hp,ac,location,faction_id,attitude,stats_json,conditions_json,beliefs_json,goals_json,routine_json,memory_json,status,died_on,archetype_id,materialized,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)
                        ON CONFLICT(campaign_id,id) DO UPDATE SET name=excluded.name,hp=excluded.hp,max_hp=excluded.max_hp,ac=excluded.ac,location=excluded.location,faction_id=excluded.faction_id,attitude=excluded.attitude,stats_json=excluded.stats_json,conditions_json=excluded.conditions_json,beliefs_json=excluded.beliefs_json,goals_json=excluded.goals_json,routine_json=excluded.routine_json,memory_json=excluded.memory_json,status=excluded.status,archetype_id=excluded.archetype_id,materialized=1,updated_at=excluded.updated_at""",
                       (campaign_id, nid, str(npc["name"])[:200], hp, max_hp, int(npc.get("ac", 10)), str(npc.get("location", "unknown"))[:200], npc.get("faction_id"), int(npc.get("attitude", 0)), self.e._dumps(npc.get("stats") or {}), self.e._dumps(npc.get("conditions") or []), self.e._dumps(npc.get("beliefs") or []), self.e._dumps(npc.get("goals") or []), self.e._dumps(routine), self.e._dumps(npc.get("memory") or []), str(npc.get("status", "alive")), npc.get("died_on"), aid, now))
            if arow:
                needs = self.e._loads(arow["needs_json"] or "{}")
                for need, cfg in needs.items():
                    cfg = dict(cfg or {}); cfg.update((deviations.get("needs") or {}).get(need, {}))
                    db.execute("""INSERT INTO npc_needs(campaign_id,npc_id,need,value,baseline,drift_per_day,curve,updated_at) VALUES(?,?,?,?,?,?,?,?)
                                ON CONFLICT(campaign_id,npc_id,need) DO UPDATE SET value=excluded.value,baseline=excluded.baseline,drift_per_day=excluded.drift_per_day,curve=excluded.curve,updated_at=excluded.updated_at""",
                               (campaign_id, nid, need, float(cfg.get("value", cfg.get("baseline", 50))), float(cfg.get("baseline", 50)), float(cfg.get("drift_per_day", 0)), cfg.get("curve", "quadratic"), now))
                actions = self.e._loads(arow["actions_json"] or "[]")
                for action in actions:
                    action_id = self.e._clean_id(str(action.get("id") or action.get("action_id")))
                    db.execute("""INSERT INTO npc_actions(campaign_id,npc_id,action_id,location,base_utility,considerations_json,effects_json,requirements_json,cost_hours,tags_json,enabled,updated_at)
                                VALUES(?,?,?,?,?,?,?,?,?,?,1,?)
                                ON CONFLICT(campaign_id,npc_id,action_id) DO UPDATE SET location=excluded.location,base_utility=excluded.base_utility,considerations_json=excluded.considerations_json,effects_json=excluded.effects_json,requirements_json=excluded.requirements_json,cost_hours=excluded.cost_hours,tags_json=excluded.tags_json,enabled=1,updated_at=excluded.updated_at""",
                               (campaign_id, nid, action_id, action.get("location"), float(action.get("base_utility", 0)), self.e._dumps(action.get("considerations") or []), self.e._dumps(action.get("effects") or []), self.e._dumps(action.get("requirements") or {}), float(action.get("cost_hours", 8)), self.e._dumps(action.get("tags") or []), now))
                visual.update(deviations.get("visual") or {})
                if visual:
                    db.execute("""INSERT INTO visual_profiles(campaign_id,entity_kind,entity_id,profile_json,updated_at) VALUES(?,'npc',?,?,?)
                                ON CONFLICT(campaign_id,entity_kind,entity_id) DO UPDATE SET profile_json=excluded.profile_json,updated_at=excluded.updated_at""",
                               (campaign_id, nid, self.e._dumps(visual), now))

    def world_digest(self, campaign_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            campaign = db.execute("SELECT world_time,revision FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
            population_alive = db.execute("SELECT COUNT(*) n FROM npcs WHERE campaign_id=? AND status='alive'", (campaign_id,)).fetchone()["n"]
            relationships = db.execute("SELECT COUNT(*) n,AVG(ABS(trust)) mean_abs FROM relationships WHERE campaign_id=?", (campaign_id,)).fetchone()
            factions = db.execute("SELECT COUNT(*) n FROM factions WHERE campaign_id=?", (campaign_id,)).fetchone()["n"]
            locations = db.execute("SELECT COUNT(*) n FROM locations WHERE campaign_id=?", (campaign_id,)).fetchone()["n"]
            events = db.execute("SELECT COUNT(*) n FROM events WHERE campaign_id=?", (campaign_id,)).fetchone()["n"]
            capped = db.execute("SELECT COUNT(*) n FROM events WHERE campaign_id=? AND event_type='sim_cascade_capped'", (campaign_id,)).fetchone()["n"]
            rel_bound = db.execute("SELECT COUNT(*) n FROM relationships WHERE campaign_id=? AND (ABS(trust)=100 OR ABS(fear)=100 OR ABS(respect)=100 OR ABS(affection)=100)", (campaign_id,)).fetchone()["n"]
            need_bound = db.execute("SELECT COUNT(*) n FROM npc_needs WHERE campaign_id=? AND (value<=0.000001 OR value>=99.999999)", (campaign_id,)).fetchone()["n"]
            need_count = db.execute("SELECT COUNT(*) n FROM npc_needs WHERE campaign_id=?", (campaign_id,)).fetchone()["n"]
            gaps = db.execute("SELECT COUNT(*) n FROM content_gaps WHERE campaign_id=? AND status='open'", (campaign_id,)).fetchone()["n"]
            event_types = {r["event_type"]: r["n"] for r in db.execute("SELECT event_type,COUNT(*) n FROM events WHERE campaign_id=? GROUP BY event_type ORDER BY event_type", (campaign_id,))}
            action_distribution = {str(r["last_action"] or "none"): int(r["n"]) for r in db.execute("SELECT last_action,COUNT(*) n FROM sim_agent_state WHERE campaign_id=? GROUP BY last_action ORDER BY n DESC,last_action LIMIT 20", (campaign_id,)).fetchall()}
            need_means = {str(r["need"]): round(float(r["avg_value"] or 0),3) for r in db.execute("SELECT need,AVG(value) avg_value FROM npc_needs WHERE campaign_id=? GROUP BY need ORDER BY need LIMIT 20", (campaign_id,)).fetchall()}
            resource_summary = [{"item_id":r["item_id"],"qty":round(float(r["qty"] or 0),3),"capacity":round(float(r["capacity"] or 0),3)} for r in db.execute("SELECT item_id,SUM(qty) qty,SUM(qty_max) capacity FROM resource_nodes WHERE campaign_id=? GROUP BY item_id ORDER BY item_id LIMIT 20", (campaign_id,)).fetchall()]
            faction_summary = [{"id":r["id"],"reputation":int(r["reputation"]),"reserve_score":int(r["reserve_score"]),"leader_id":r["leader_id"]} for r in db.execute("SELECT id,reputation,reserve_score,leader_id FROM factions WHERE campaign_id=? ORDER BY id LIMIT 20", (campaign_id,)).fetchall()]
        return {
            "world_time": campaign["world_time"] if campaign else None,
            "revision": int(campaign["revision"]) if campaign else 0,
            "population_alive": int(population_alive),
            "relationships": int(relationships["n"]),
            "mean_abs_trust": round(float(relationships["mean_abs"] or 0), 4),
            "factions": int(factions),
            "locations": int(locations),
            "event_count": int(events),
            "event_types": event_types,
            "action_distribution": action_distribution,
            "need_means": need_means,
            "resources": resource_summary,
            "faction_summary": faction_summary,
            "cascade_capped_events": int(capped),
            "open_content_gaps": int(gaps),
            "saturation": {"relationships_at_bound": int(rel_bound), "needs_at_bound": int(need_bound), "need_count": int(need_count)},
        }

    def materialization_brief(self, campaign_id: str, location_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            loc = db.execute("SELECT * FROM locations WHERE campaign_id=? AND id=?", (campaign_id, location_id)).fetchone()
            if not loc:
                raise KeyError(f"unknown location: {location_id}")
            state = self.e._loads(loc["state_json"] or "{}")
            pop = state.get("population", state.get("pop"))
            named = db.execute("SELECT COUNT(*) n FROM npcs WHERE campaign_id=? AND location=? AND status='alive'", (campaign_id, location_id)).fetchone()["n"]
            stocks = [dict(r) for r in db.execute("SELECT item_id,qty,qty_max FROM resource_nodes WHERE campaign_id=? AND location_id=? ORDER BY item_id", (campaign_id, location_id))]
            directors = []
            try:
                from .world_layers import WorldLayerKernel
                directors = WorldLayerKernel(self.e).active_directors_db(db, campaign_id, location_id).get("directors", [])
            except Exception:
                directors = []
            bible = db.execute("SELECT bible_json,canon_version FROM world_bible WHERE campaign_id=?", (campaign_id,)).fetchone()
        return {
            "campaign_id": campaign_id,
            "location": {"id": loc["id"], "name": loc["name"], "region": loc["region"], "realm_id": loc["realm_id"], "description": loc["description"], "state": state},
            "aggregates": {"population": pop, "named_npcs": int(named), "stocks": stocks, "directors": directors},
            "world_bible": self.e._loads(bible["bible_json"]) if bible else {},
            "canon_version": int(bible["canon_version"]) if bible else 0,
            "generation_instruction": "Generate only missing named detail consistent with these aggregates. Do not alter aggregates. Prefer archetype references plus deviations over bespoke behavior rows.",
        }

    def log_gap(self, campaign_id: str, gap_key: str, kind: str, summary: str, *, scope_id: str | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
        gap_key = self.e._clean_id(gap_key)
        with self.e._write_db() as db:
            db.execute("""INSERT INTO content_gaps(campaign_id,gap_key,kind,scope_id,summary,context_json,status,created_at,resolved_at)
                        VALUES(?,?,?,?,?,?,'open',?,NULL)
                        ON CONFLICT(campaign_id,gap_key) DO UPDATE SET kind=excluded.kind,scope_id=excluded.scope_id,summary=excluded.summary,context_json=excluded.context_json,status='open',resolved_at=NULL""",
                       (campaign_id, gap_key, kind[:80], scope_id, summary[:1000], self.e._dumps(context or {}), self.e._now()))
        return self.get_gap(campaign_id, gap_key)

    def get_gap(self, campaign_id: str, gap_key: str) -> dict[str, Any]:
        with self.e._db() as db:
            row = db.execute("SELECT * FROM content_gaps WHERE campaign_id=? AND gap_key=?", (campaign_id, gap_key)).fetchone()
        if not row:
            raise KeyError(gap_key)
        d = dict(row); d["context"] = self.e._loads(d.pop("context_json")); return d

    def list_gaps(self, campaign_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.e._db() as db:
            rows = db.execute("SELECT gap_key FROM content_gaps WHERE campaign_id=? AND status='open' ORDER BY id LIMIT ?", (campaign_id, max(1, min(int(limit), 100)))).fetchall()
        return [self.get_gap(campaign_id, r["gap_key"]) for r in rows]

    def resolve_gap(self, campaign_id: str, gap_key: str, status: str = "resolved") -> dict[str, Any]:
        if status not in {"resolved", "suppressed"}:
            raise ValueError("status must be resolved or suppressed")
        with self.e._write_db() as db:
            db.execute("UPDATE content_gaps SET status=?,resolved_at=? WHERE campaign_id=? AND gap_key=?", (status, self.e._now(), campaign_id, gap_key))
        return self.get_gap(campaign_id, gap_key)
