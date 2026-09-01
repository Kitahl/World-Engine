from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence, TYPE_CHECKING

from .world_layers import WorldLayerKernel, apply_succession
from .environment import EnvironmentKernel
from .economy import EconomyKernel
from .population import PopulationKernel

if TYPE_CHECKING:
    from .engine import WorldEngine

SIM_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS sim_config (
    campaign_id TEXT PRIMARY KEY,
    seed INTEGER NOT NULL,
    rng_counter INTEGER NOT NULL DEFAULT 0,
    max_cascade_depth INTEGER NOT NULL DEFAULT 8,
    max_cascade_events INTEGER NOT NULL DEFAULT 512,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sim_rules (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    archetype TEXT NOT NULL CHECK(archetype IN ('drift','schedule','stock','chance','spread','decide')),
    enabled INTEGER NOT NULL DEFAULT 1,
    cadence TEXT NOT NULL DEFAULT 'day' CHECK(cadence IN ('hour','day','week')),
    target TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 100,
    params_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sim_accumulators (
    campaign_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    value REAL NOT NULL,
    last_written REAL NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,rule_id,entity_key),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS resource_nodes (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    location_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    qty REAL NOT NULL DEFAULT 0,
    qty_max REAL NOT NULL DEFAULT 10,
    regen_per_day REAL NOT NULL DEFAULT 0.5,
    season_mult_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS npc_needs (
    campaign_id TEXT NOT NULL,
    npc_id TEXT NOT NULL,
    need TEXT NOT NULL,
    value REAL NOT NULL DEFAULT 0 CHECK(value BETWEEN 0 AND 100),
    baseline REAL NOT NULL DEFAULT 50 CHECK(baseline BETWEEN 0 AND 100),
    drift_per_day REAL NOT NULL DEFAULT 0 CHECK(drift_per_day BETWEEN 0 AND 1),
    curve TEXT NOT NULL DEFAULT 'quadratic' CHECK(curve IN ('linear','quadratic','urgent','threshold')),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,npc_id,need),
    FOREIGN KEY(campaign_id,npc_id) REFERENCES npcs(campaign_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS npc_actions (
    campaign_id TEXT NOT NULL,
    npc_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    location TEXT,
    base_utility REAL NOT NULL DEFAULT 0,
    considerations_json TEXT NOT NULL DEFAULT '[]',
    effects_json TEXT NOT NULL DEFAULT '[]',
    requirements_json TEXT NOT NULL DEFAULT '{}',
    cost_hours REAL NOT NULL DEFAULT 8 CHECK(cost_hours >= 0),
    tags_json TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,npc_id,action_id),
    FOREIGN KEY(campaign_id,npc_id) REFERENCES npcs(campaign_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sim_agent_state (
    campaign_id TEXT NOT NULL,
    npc_id TEXT NOT NULL,
    last_action TEXT,
    last_score REAL,
    last_decision_time TEXT,
    committed_until TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,npc_id),
    FOREIGN KEY(campaign_id,npc_id) REFERENCES npcs(campaign_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sim_reactions (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    trigger_event_type TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    selector_json TEXT NOT NULL DEFAULT '{}',
    effects_json TEXT NOT NULL DEFAULT '[]',
    probability REAL NOT NULL DEFAULT 1.0 CHECK(probability BETWEEN 0 AND 1),
    repeat_policy TEXT NOT NULL DEFAULT 'once_per_cascade' CHECK(repeat_policy IN ('once_per_cascade','count_limited')),
    repeat_limit INTEGER NOT NULL DEFAULT 1 CHECK(repeat_limit >= 1),
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS item_defs (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    base_price REAL NOT NULL DEFAULT 0,
    effect_dice TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS inventories (
    campaign_id TEXT NOT NULL,
    owner_kind TEXT NOT NULL CHECK(owner_kind IN ('character','npc','faction','location')),
    owner_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    qty REAL NOT NULL DEFAULT 0 CHECK(qty >= 0),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,owner_kind,owner_id,item_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS npc_lifecycle (
    campaign_id TEXT NOT NULL,
    npc_id TEXT NOT NULL,
    birth_year INTEGER NOT NULL,
    parents_json TEXT NOT NULL DEFAULT '[]',
    spouse_id TEXT,
    mortality_json TEXT NOT NULL DEFAULT '{}',
    fertility_json TEXT NOT NULL DEFAULT '{}',
    heir_id TEXT,
    last_birth_on TEXT,
    alive INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,npc_id),
    FOREIGN KEY(campaign_id,npc_id) REFERENCES npcs(campaign_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS drama_config (
    campaign_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    low_hp_threshold REAL NOT NULL DEFAULT 0.35,
    hardship_window_hours REAL NOT NULL DEFAULT 72,
    calm_boost REAL NOT NULL DEFAULT 1.5,
    hardship_suppression REAL NOT NULL DEFAULT 0.45,
    relief_boost REAL NOT NULL DEFAULT 1.5,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS relationship_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    cause TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'direct',
    trust_delta INTEGER NOT NULL DEFAULT 0,
    fear_delta INTEGER NOT NULL DEFAULT 0,
    respect_delta INTEGER NOT NULL DEFAULT 0,
    affection_delta INTEGER NOT NULL DEFAULT 0,
    world_time TEXT NOT NULL,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sim_rules_campaign ON sim_rules(campaign_id,enabled,priority,id);
CREATE INDEX IF NOT EXISTS idx_resource_nodes_campaign_item ON resource_nodes(campaign_id,item_id,location_id);
CREATE INDEX IF NOT EXISTS idx_npc_actions_campaign_npc ON npc_actions(campaign_id,npc_id,enabled,action_id);
CREATE INDEX IF NOT EXISTS idx_sim_reactions_trigger ON sim_reactions(campaign_id,trigger_event_type,enabled,priority,id);
CREATE INDEX IF NOT EXISTS idx_relationship_events_pair ON relationship_events(campaign_id,source_id,target_id,id DESC);
CREATE INDEX IF NOT EXISTS idx_inventories_owner ON inventories(campaign_id,owner_kind,owner_id,item_id);
CREATE INDEX IF NOT EXISTS idx_lifecycle_alive ON npc_lifecycle(campaign_id,alive,npc_id);
"""

CADENCE_SECONDS = {"hour": 3600, "day": 86400, "week": 604800}
_EPOCH_UTC = datetime(1, 1, 1, tzinfo=timezone.utc)

# Only explicit numeric columns may be targeted by data-driven rules. This is the
# SQL-injection boundary for simulation rules.
TARGETS: dict[str, tuple[str, str, tuple[str, ...], float | None, float | None]] = {
    "relationships.trust": ("relationships", "trust", ("source_id", "target_id"), -100, 100),
    "relationships.fear": ("relationships", "fear", ("source_id", "target_id"), -100, 100),
    "relationships.respect": ("relationships", "respect", ("source_id", "target_id"), -100, 100),
    "relationships.affection": ("relationships", "affection", ("source_id", "target_id"), -100, 100),
    "factions.reputation": ("factions", "reputation", ("id",), -10, 10),
    "factions.reserve_score": ("factions", "reserve_score", ("id",), None, None),
    "npcs.attitude": ("npcs", "attitude", ("id",), -10, 10),
}


def record_relationship_event(
    engine: "WorldEngine",
    db: sqlite3.Connection,
    campaign_id: str,
    source_id: str,
    target_id: str,
    deltas: dict[str, int],
    cause: str,
    revision: int,
    *,
    event_type: str = "direct",
    world_time: str | None = None,
) -> int:
    if world_time is None:
        row = db.execute("SELECT world_time FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        world_time = row["world_time"]
    cur = db.execute(
        """INSERT INTO relationship_events(
               campaign_id,source_id,target_id,cause,event_type,
               trust_delta,fear_delta,respect_delta,affection_delta,world_time,revision,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            campaign_id,
            source_id,
            target_id,
            cause[:1000],
            event_type[:80],
            int(deltas.get("trust", 0)),
            int(deltas.get("fear", 0)),
            int(deltas.get("respect", 0)),
            int(deltas.get("affection", 0)),
            world_time,
            int(revision),
            engine._now(),
        ),
    )
    return int(cur.lastrowid)


class SimulationKernel:
    """Data-driven off-screen world activity simulator.

    Fast-path archetypes (DRIFT, STOCK, CHANCE) use closed-form/event-skipping
    catch-up. State-dependent archetypes (SPREAD, DECIDE) iterate only at their
    declared cadence. SCHEDULE jumps directly to the end-time posting for NPCs
    that do not have utility actions.
    """

    def __init__(self, engine: "WorldEngine"):
        self.e = engine

    # ---------- setup / inspection ----------

    def _default_seed(self, campaign_id: str) -> int:
        raw = hashlib.sha256(("world-engine-sim:" + campaign_id).encode("utf-8")).digest()[:8]
        return int.from_bytes(raw, "big") & ((1 << 63) - 1)

    def _ensure_config(self, db: sqlite3.Connection, campaign_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM sim_config WHERE campaign_id=?", (campaign_id,)).fetchone()
        if row:
            return row
        db.execute(
            "INSERT INTO sim_config(campaign_id,seed,rng_counter,max_cascade_depth,max_cascade_events,updated_at) VALUES(?,?,0,8,512,?)",
            (campaign_id, self._default_seed(campaign_id), self.e._now()),
        )
        return db.execute("SELECT * FROM sim_config WHERE campaign_id=?", (campaign_id,)).fetchone()

    def get_config(self, campaign_id: str) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        with self.e._write_db() as db:
            row = self._ensure_config(db, campaign_id)
            return dict(row)

    def set_seed(self, campaign_id: str, seed: int, *, reset_counter: bool = True) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        seed = int(seed) & ((1 << 63) - 1)
        with self.e._write_db() as db:
            self._ensure_config(db, campaign_id)
            if reset_counter:
                db.execute("UPDATE sim_config SET seed=?,rng_counter=0,updated_at=? WHERE campaign_id=?", (seed, self.e._now(), campaign_id))
            else:
                db.execute("UPDATE sim_config SET seed=?,updated_at=? WHERE campaign_id=?", (seed, self.e._now(), campaign_id))
        return self.get_config(campaign_id)

    def save_rule(
        self,
        campaign_id: str,
        rule_id: str,
        archetype: str,
        *,
        cadence: str = "day",
        target: str = "",
        params: dict[str, Any] | None = None,
        priority: int = 100,
        enabled: bool = True,
    ) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        rule_id = self.e._clean_id(rule_id)
        archetype = archetype.strip().lower()
        if archetype not in {"drift", "schedule", "stock", "chance", "spread", "decide"}:
            raise ValueError("invalid simulation archetype")
        if cadence not in CADENCE_SECONDS:
            raise ValueError("cadence must be hour, day, or week")
        if archetype == "drift" and target not in TARGETS:
            raise ValueError(f"unsupported drift target: {target}")
        if archetype == "stock" and target not in {"resource_nodes.qty"}:
            raise ValueError("stock currently supports target resource_nodes.qty")
        with self.e._write_db() as db:
            db.execute(
                """INSERT INTO sim_rules(campaign_id,id,archetype,enabled,cadence,target,priority,params_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET archetype=excluded.archetype,enabled=excluded.enabled,
                   cadence=excluded.cadence,target=excluded.target,priority=excluded.priority,params_json=excluded.params_json,updated_at=excluded.updated_at""",
                (campaign_id, rule_id, archetype, int(bool(enabled)), cadence, target, int(priority), self.e._dumps(params or {}), self.e._now()),
            )
        return self.get_rule(campaign_id, rule_id)

    def get_rule(self, campaign_id: str, rule_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            row = db.execute("SELECT * FROM sim_rules WHERE campaign_id=? AND id=?", (campaign_id, rule_id)).fetchone()
        if not row:
            raise KeyError(f"unknown simulation rule: {rule_id}")
        data = dict(row)
        data["enabled"] = bool(data["enabled"])
        data["params"] = self.e._loads(data.pop("params_json"))
        return data

    def list_rules(self, campaign_id: str) -> list[dict[str, Any]]:
        with self.e._db() as db:
            rows = db.execute("SELECT id FROM sim_rules WHERE campaign_id=? ORDER BY priority,id", (campaign_id,)).fetchall()
        return [self.get_rule(campaign_id, r["id"]) for r in rows]

    def save_resource_node(
        self,
        campaign_id: str,
        node_id: str,
        location_id: str,
        item_id: str,
        *,
        qty: float = 0,
        qty_max: float = 10,
        regen_per_day: float = 0.5,
        season_mult: dict[str, float] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        node_id = self.e._clean_id(node_id)
        if qty_max <= 0 or regen_per_day < 0:
            raise ValueError("qty_max must be >0 and regen_per_day >=0")
        qty = max(0.0, min(float(qty), float(qty_max)))
        with self.e._write_db() as db:
            db.execute(
                """INSERT INTO resource_nodes(campaign_id,id,location_id,item_id,qty,qty_max,regen_per_day,season_mult_json,metadata_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET location_id=excluded.location_id,item_id=excluded.item_id,
                   qty=excluded.qty,qty_max=excluded.qty_max,regen_per_day=excluded.regen_per_day,
                   season_mult_json=excluded.season_mult_json,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                (campaign_id, node_id, location_id[:200], item_id[:200], qty, float(qty_max), float(regen_per_day), self.e._dumps(season_mult or {}), self.e._dumps(metadata or {}), self.e._now()),
            )
        return self.get_resource_node(campaign_id, node_id)

    def get_resource_node(self, campaign_id: str, node_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            row = db.execute("SELECT * FROM resource_nodes WHERE campaign_id=? AND id=?", (campaign_id, node_id)).fetchone()
        if not row:
            raise KeyError(f"unknown resource node: {node_id}")
        data = dict(row)
        data["season_mult"] = self.e._loads(data.pop("season_mult_json"))
        data["metadata"] = self.e._loads(data.pop("metadata_json"))
        return data

    def save_need(self, campaign_id: str, npc_id: str, need: str, value: float, *, baseline: float = 50, drift_per_day: float = 0, curve: str = "quadratic") -> dict[str, Any]:
        self.e.get_npc(campaign_id, npc_id)
        need = self.e._clean_id(need)
        value, baseline, drift_per_day = float(value), float(baseline), float(drift_per_day)
        curve = str(curve).lower().strip()
        if curve not in {"linear", "quadratic", "urgent", "threshold"}:
            raise ValueError("curve must be linear, quadratic, urgent, or threshold")
        if not 0 <= value <= 100 or not 0 <= baseline <= 100 or not 0 <= drift_per_day <= 1:
            raise ValueError("need value/baseline must be 0..100 and drift_per_day 0..1")
        with self.e._write_db() as db:
            db.execute(
                """INSERT INTO npc_needs(campaign_id,npc_id,need,value,baseline,drift_per_day,curve,updated_at) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,npc_id,need) DO UPDATE SET value=excluded.value,baseline=excluded.baseline,drift_per_day=excluded.drift_per_day,curve=excluded.curve,updated_at=excluded.updated_at""",
                (campaign_id, npc_id, need, value, baseline, drift_per_day, curve, self.e._now()),
            )
        return self.get_need(campaign_id, npc_id, need)

    def get_need(self, campaign_id: str, npc_id: str, need: str) -> dict[str, Any]:
        with self.e._db() as db:
            row = db.execute("SELECT * FROM npc_needs WHERE campaign_id=? AND npc_id=? AND need=?", (campaign_id, npc_id, need)).fetchone()
        if not row:
            raise KeyError(f"unknown need: {npc_id}/{need}")
        return dict(row)

    def save_action(
        self,
        campaign_id: str,
        npc_id: str,
        action_id: str,
        *,
        location: str | None = None,
        base_utility: float = 0,
        considerations: Sequence[dict[str, Any]] = (),
        effects: Sequence[dict[str, Any]] = (),
        requirements: dict[str, Any] | None = None,
        cost_hours: float = 8,
        tags: Sequence[str] = (),
        enabled: bool = True,
    ) -> dict[str, Any]:
        self.e.get_npc(campaign_id, npc_id)
        action_id = self.e._clean_id(action_id)
        if float(cost_hours) < 0:
            raise ValueError("cost_hours must be >= 0")
        with self.e._write_db() as db:
            db.execute(
                """INSERT INTO npc_actions(campaign_id,npc_id,action_id,location,base_utility,considerations_json,effects_json,requirements_json,cost_hours,tags_json,enabled,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,npc_id,action_id) DO UPDATE SET location=excluded.location,base_utility=excluded.base_utility,
                   considerations_json=excluded.considerations_json,effects_json=excluded.effects_json,requirements_json=excluded.requirements_json,
                   cost_hours=excluded.cost_hours,tags_json=excluded.tags_json,enabled=excluded.enabled,updated_at=excluded.updated_at""",
                (campaign_id, npc_id, action_id, location, float(base_utility), self.e._dumps(list(considerations)), self.e._dumps(list(effects)),
                 self.e._dumps(requirements or {}), float(cost_hours), self.e._dumps(sorted(set(tags))), int(bool(enabled)), self.e._now()),
            )
        return self.get_action(campaign_id, npc_id, action_id)

    def get_action(self, campaign_id: str, npc_id: str, action_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            row = db.execute("SELECT * FROM npc_actions WHERE campaign_id=? AND npc_id=? AND action_id=?", (campaign_id, npc_id, action_id)).fetchone()
        if not row:
            raise KeyError(f"unknown NPC action: {npc_id}/{action_id}")
        data = dict(row)
        data["enabled"] = bool(data["enabled"])
        data["considerations"] = self.e._loads(data.pop("considerations_json"))
        data["effects"] = self.e._loads(data.pop("effects_json"))
        data["requirements"] = self.e._loads(data.pop("requirements_json"))
        data["tags"] = self.e._loads(data.pop("tags_json"))
        return data

    def save_reaction(
        self,
        campaign_id: str,
        reaction_id: str,
        trigger_event_type: str,
        effects: Sequence[dict[str, Any]],
        *,
        selector: dict[str, Any] | None = None,
        probability: float = 1.0,
        repeat_policy: str = "once_per_cascade",
        repeat_limit: int = 1,
        priority: int = 100,
        enabled: bool = True,
    ) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        reaction_id = self.e._clean_id(reaction_id)
        probability = float(probability)
        repeat_policy = str(repeat_policy)
        if not 0 <= probability <= 1:
            raise ValueError("reaction probability must be 0..1")
        if repeat_policy not in {"once_per_cascade", "count_limited"}:
            raise ValueError("repeat_policy must be once_per_cascade or count_limited")
        if int(repeat_limit) < 1:
            raise ValueError("repeat_limit must be >=1")
        with self.e._write_db() as db:
            db.execute(
                """INSERT INTO sim_reactions(campaign_id,id,trigger_event_type,priority,selector_json,effects_json,probability,repeat_policy,repeat_limit,enabled,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET trigger_event_type=excluded.trigger_event_type,priority=excluded.priority,
                   selector_json=excluded.selector_json,effects_json=excluded.effects_json,probability=excluded.probability,
                   repeat_policy=excluded.repeat_policy,repeat_limit=excluded.repeat_limit,enabled=excluded.enabled,updated_at=excluded.updated_at""",
                (campaign_id, reaction_id, trigger_event_type[:80], int(priority), self.e._dumps(selector or {}), self.e._dumps(list(effects)),
                 probability, repeat_policy, int(repeat_limit), int(bool(enabled)), self.e._now()),
            )
        return {"campaign_id": campaign_id, "id": reaction_id, "trigger_event_type": trigger_event_type, "priority": priority,
                "selector": selector or {}, "effects": list(effects), "probability": probability,
                "repeat_policy": repeat_policy, "repeat_limit": int(repeat_limit), "enabled": bool(enabled)}

    # ---------- thin item / lifecycle / drama configuration ----------

    def save_item_def(self, campaign_id: str, item_id: str, name: str, *, base_price: float = 0, effect_dice: str | None = None, tags: Sequence[str] = (), metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        item_id = self.e._clean_id(item_id)
        if float(base_price) < 0:
            raise ValueError("base_price must be >=0")
        with self.e._write_db() as db:
            db.execute(
                """INSERT INTO item_defs(campaign_id,id,name,base_price,effect_dice,tags_json,metadata_json,updated_at) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET name=excluded.name,base_price=excluded.base_price,effect_dice=excluded.effect_dice,tags_json=excluded.tags_json,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                (campaign_id, item_id, name[:200], float(base_price), effect_dice, self.e._dumps(sorted(set(tags))), self.e._dumps(metadata or {}), self.e._now()),
            )
        with self.e._db() as db:
            row = db.execute("SELECT * FROM item_defs WHERE campaign_id=? AND id=?", (campaign_id, item_id)).fetchone()
        data = dict(row)
        data["tags"] = self.e._loads(data.pop("tags_json"))
        data["metadata"] = self.e._loads(data.pop("metadata_json"))
        return data

    def set_inventory(self, campaign_id: str, owner_kind: str, owner_id: str, item_id: str, qty: float, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        if owner_kind not in {"character", "npc", "faction", "location"}:
            raise ValueError("invalid inventory owner_kind")
        qty = max(0.0, float(qty))
        with self.e._write_db() as db:
            db.execute(
                """INSERT INTO inventories(campaign_id,owner_kind,owner_id,item_id,qty,metadata_json,updated_at) VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,owner_kind,owner_id,item_id) DO UPDATE SET qty=excluded.qty,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                (campaign_id, owner_kind, owner_id, item_id, qty, self.e._dumps(metadata or {}), self.e._now()),
            )
        return {"campaign_id": campaign_id, "owner_kind": owner_kind, "owner_id": owner_id, "item_id": item_id, "qty": qty, "metadata": metadata or {}}

    def get_inventory(self, campaign_id: str, owner_kind: str, owner_id: str) -> list[dict[str, Any]]:
        with self.e._db() as db:
            rows = db.execute(
                """SELECT i.campaign_id,i.owner_kind,i.owner_id,i.item_id,i.qty,i.updated_at,
                          i.metadata_json AS inventory_metadata_json,
                          d.name,d.base_price,d.effect_dice,d.tags_json,d.metadata_json
                   FROM inventories i LEFT JOIN item_defs d ON d.campaign_id=i.campaign_id AND d.id=i.item_id
                   WHERE i.campaign_id=? AND i.owner_kind=? AND i.owner_id=? ORDER BY i.item_id""",
                (campaign_id, owner_kind, owner_id),
            ).fetchall()
        out=[]
        for row in rows:
            data=dict(row)
            data["tags"] = self.e._loads(data.pop("tags_json")) if data.get("tags_json") else []
            item_meta = self.e._loads(data.pop("metadata_json")) if data.get("metadata_json") else {}
            inv_meta_raw = data.pop("inventory_metadata_json", None)
            data["metadata"] = item_meta
            data["inventory_metadata"] = self.e._loads(inv_meta_raw) if inv_meta_raw else {}
            out.append(data)
        return out

    def market_prices(self, campaign_id: str, location_id: str) -> list[dict[str, Any]]:
        """Thin scarcity pricing over STOCK, not a full economy.

        scarcity = 1 - local_qty/local_capacity, clamped to 0..1
        price = base_price * (1 + scarcity)
        Multiple stock nodes for the same item at a location are aggregated.
        """
        with self.e._db() as db:
            rows = db.execute(
                """SELECT d.id,d.name,d.base_price,
                          COALESCE(SUM(r.qty),0) AS qty,
                          COALESCE(SUM(r.qty_max),0) AS qty_max
                   FROM item_defs d
                   LEFT JOIN resource_nodes r
                     ON r.campaign_id=d.campaign_id AND r.item_id=d.id AND r.location_id=?
                   WHERE d.campaign_id=?
                   GROUP BY d.id,d.name,d.base_price
                   ORDER BY d.id""",
                (location_id, campaign_id),
            ).fetchall()
        out=[]
        for row in rows:
            cap=float(row["qty_max"] or 0)
            qty=float(row["qty"] or 0)
            # No local STOCK definition means neutral price rather than invented scarcity.
            scarcity=0.0 if cap <= 0 else max(0.0, min(1.0, 1.0 - qty/cap))
            base=float(row["base_price"] or 0)
            out.append({
                "item_id": row["id"], "name": row["name"], "base_price": base,
                "local_qty": qty, "local_capacity": cap,
                "scarcity": round(scarcity, 6), "price": round(base * (1.0 + scarcity), 6),
            })
        return out

    def save_lifecycle(self, campaign_id: str, npc_id: str, *, birth_year: int, parents: Sequence[str] = (), spouse_id: str | None = None, mortality: dict[str, Any] | None = None, fertility: dict[str, Any] | None = None, heir_id: str | None = None, alive: bool = True) -> dict[str, Any]:
        with self.e._write_db() as db:
            self.e._get_npc_db(db, campaign_id, npc_id)
            db.execute(
                """INSERT INTO npc_lifecycle(campaign_id,npc_id,birth_year,parents_json,spouse_id,mortality_json,fertility_json,heir_id,last_birth_on,alive,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,NULL,?,?)
                   ON CONFLICT(campaign_id,npc_id) DO UPDATE SET birth_year=excluded.birth_year,parents_json=excluded.parents_json,spouse_id=excluded.spouse_id,mortality_json=excluded.mortality_json,fertility_json=excluded.fertility_json,heir_id=excluded.heir_id,alive=excluded.alive,updated_at=excluded.updated_at""",
                (campaign_id, npc_id, int(birth_year), self.e._dumps(list(parents)[:2]), spouse_id, self.e._dumps(mortality or {"enabled": False}), self.e._dumps(fertility or {"enabled": False}), heir_id, int(bool(alive)), self.e._now()),
            )
            if alive:
                db.execute("UPDATE npcs SET status='alive',died_on=NULL,updated_at=? WHERE campaign_id=? AND id=?", (self.e._now(), campaign_id, npc_id))
        return self.get_lifecycle(campaign_id, npc_id)

    def get_lifecycle(self, campaign_id: str, npc_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            row = db.execute("SELECT * FROM npc_lifecycle WHERE campaign_id=? AND npc_id=?", (campaign_id, npc_id)).fetchone()
            campaign = db.execute("SELECT world_time FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if not row:
            raise KeyError(f"unknown lifecycle: {npc_id}")
        data=dict(row)
        data["parents"] = self.e._loads(data.pop("parents_json"))
        data["mortality"] = self.e._loads(data.pop("mortality_json"))
        data["fertility"] = self.e._loads(data.pop("fertility_json"))
        data["alive"] = bool(data["alive"])
        data["age"] = max(0, datetime.fromisoformat(campaign["world_time"]).year - int(data["birth_year"]))
        return data

    def set_drama_config(self, campaign_id: str, *, enabled: bool = True, low_hp_threshold: float = 0.35, hardship_window_hours: float = 72, calm_boost: float = 1.5, hardship_suppression: float = 0.45, relief_boost: float = 1.5) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        if not 0 <= float(low_hp_threshold) <= 1:
            raise ValueError("low_hp_threshold must be 0..1")
        if float(hardship_window_hours) < 0 or min(float(calm_boost), float(hardship_suppression), float(relief_boost)) < 0:
            raise ValueError("drama values must be non-negative")
        with self.e._write_db() as db:
            db.execute(
                """INSERT INTO drama_config(campaign_id,enabled,low_hp_threshold,hardship_window_hours,calm_boost,hardship_suppression,relief_boost,updated_at) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id) DO UPDATE SET enabled=excluded.enabled,low_hp_threshold=excluded.low_hp_threshold,hardship_window_hours=excluded.hardship_window_hours,calm_boost=excluded.calm_boost,hardship_suppression=excluded.hardship_suppression,relief_boost=excluded.relief_boost,updated_at=excluded.updated_at""",
                (campaign_id, int(bool(enabled)), float(low_hp_threshold), float(hardship_window_hours), float(calm_boost), float(hardship_suppression), float(relief_boost), self.e._now()),
            )
        with self.e._db() as db:
            row=db.execute("SELECT * FROM drama_config WHERE campaign_id=?", (campaign_id,)).fetchone()
        data=dict(row); data["enabled"]=bool(data["enabled"]); return data

    # ---------- deterministic RNG ----------

    def _rand(self, db: sqlite3.Connection, campaign_id: str, namespace: str) -> float:
        cfg = self._ensure_config(db, campaign_id)
        seed, counter = int(cfg["seed"]), int(cfg["rng_counter"])
        digest = hashlib.sha256(f"{seed}:{counter}:{namespace}".encode("utf-8")).digest()
        value = int.from_bytes(digest[:8], "big") / float(1 << 64)
        db.execute("UPDATE sim_config SET rng_counter=?,updated_at=? WHERE campaign_id=?", (counter + 1, self.e._now(), campaign_id))
        return value

    def _rand_keyed(self, db: sqlite3.Connection, campaign_id: str, namespace: str, key: str) -> float:
        """Stateless deterministic random value for a world fact.

        Keying randomness to the semantic event (rule + boundary + edge) makes
        catch-up independent of how elapsed time is chunked and prevents an
        unrelated random rule from shifting another rule's future outcomes.
        """
        cfg = self._ensure_config(db, campaign_id)
        seed = int(cfg["seed"])
        digest = hashlib.sha256(f"{seed}:{namespace}:{key}".encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") / float(1 << 64)

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def _seconds_from_epoch(cls, value: datetime) -> float:
        # Pure datetime arithmetic deliberately avoids platform C timestamp limits;
        # D&D campaign years such as 1492 must work on Windows as well as Linux.
        return (cls._utc(value) - _EPOCH_UTC).total_seconds()

    @classmethod
    def _boundaries(cls, start: datetime, end: datetime, cadence: str) -> int:
        seconds = CADENCE_SECONDS[cadence]
        return max(0, int(math.floor(cls._seconds_from_epoch(end) / seconds) - math.floor(cls._seconds_from_epoch(start) / seconds)))

    @classmethod
    def _first_boundary(cls, start: datetime, cadence: str) -> datetime:
        seconds = CADENCE_SECONDS[cadence]
        index = math.floor(cls._seconds_from_epoch(start) / seconds) + 1
        return _EPOCH_UTC + timedelta(seconds=index * seconds)

    @classmethod
    def _iter_boundaries(cls, start: datetime, end: datetime, cadence: str) -> Iterable[datetime]:
        count = cls._boundaries(start, end, cadence)
        first = cls._first_boundary(start, cadence)
        step = timedelta(seconds=CADENCE_SECONDS[cadence])
        for i in range(count):
            yield first + i * step

    @classmethod
    def _is_boundary(cls, value: datetime, cadence: str) -> bool:
        seconds = CADENCE_SECONDS[cadence]
        raw = cls._seconds_from_epoch(value)
        # Campaign times are normally whole seconds; the tolerance only protects
        # against floating representation at historical dates such as 1492.
        remainder = raw % seconds
        return math.isclose(remainder, 0.0, abs_tol=1e-6) or math.isclose(remainder, float(seconds), abs_tol=1e-6)

    @classmethod
    def _boundaries_between(cls, start: datetime, end: datetime, cadence: str, *, include_end: bool) -> int:
        count = cls._boundaries(start, end, cadence)
        if not include_end and end > start and cls._is_boundary(end, cadence):
            count -= 1
        return max(0, count)

    def _success_indices(self, db: sqlite3.Connection, campaign_id: str, n: int, p: float, namespace: str) -> list[int]:
        if n <= 0 or p <= 0:
            return []
        if p >= 1:
            return list(range(n))
        out: list[int] = []
        idx = -1
        log_q = math.log1p(-p)
        while True:
            u = min(max(self._rand(db, campaign_id, namespace), 2.0 ** -53), 1.0 - 2.0 ** -53)
            failures = int(math.floor(math.log1p(-u) / log_q))
            idx += failures + 1
            if idx >= n:
                break
            out.append(idx)
        return out

    # ---------- event / cascade helpers ----------

    def _emit(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        revision: int,
        queue: deque[dict[str, Any]],
        *,
        event_type: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        region: str | None = None,
        actor_id: str | None = None,
        target_id: str | None = None,
        world_time: str | None = None,
        depth: int = 0,
        persist: bool = True,
    ) -> None:
        event = {
            "event_type": event_type,
            "summary": summary,
            "payload": payload or {},
            "region": region,
            "actor_id": actor_id,
            "target_id": target_id,
            "world_time": world_time,
            "depth": depth,
        }
        if persist:
            self.e._insert_event(db, campaign_id, revision, event_type, summary, region=region, actor_id=actor_id, target_id=target_id, payload=payload or {}, world_time_override=world_time)
        queue.append(event)

    @staticmethod
    def _event_ref(value: Any, event: dict[str, Any]) -> Any:
        if value == "$actor":
            return event.get("actor_id")
        if value == "$target":
            return event.get("target_id")
        if value == "$region":
            return event.get("region")
        return value

    def _relationship_delta_in_txn(self, db: sqlite3.Connection, campaign_id: str, source_id: str, target_id: str, deltas: dict[str, int], cause: str, revision: int, world_time: str | None, event_type: str) -> None:
        row = db.execute("SELECT * FROM relationships WHERE campaign_id=? AND source_id=? AND target_id=?", (campaign_id, source_id, target_id)).fetchone()
        base = dict(row) if row else {"trust": 0, "fear": 0, "respect": 0, "affection": 0}
        vals = {}
        for key in ("trust", "fear", "respect", "affection"):
            vals[key] = max(-100, min(100, int(base[key]) + int(deltas.get(key, 0))))
        db.execute(
            """INSERT INTO relationships(campaign_id,source_id,target_id,trust,fear,respect,affection,notes_json,updated_at)
               VALUES(?,?,?,?,?,?,?,'{}',?)
               ON CONFLICT(campaign_id,source_id,target_id) DO UPDATE SET trust=excluded.trust,fear=excluded.fear,respect=excluded.respect,affection=excluded.affection,updated_at=excluded.updated_at""",
            (campaign_id, source_id, target_id, vals["trust"], vals["fear"], vals["respect"], vals["affection"], self.e._now()),
        )
        record_relationship_event(self.e, db, campaign_id, source_id, target_id, deltas, cause, revision, event_type=event_type, world_time=world_time)

    def _apply_effect(self, db: sqlite3.Connection, campaign_id: str, revision: int, queue: deque[dict[str, Any]], event: dict[str, Any], effect: dict[str, Any]) -> None:
        kind = str(effect.get("type", "")).lower()
        if kind == "relationship":
            source = self._event_ref(effect.get("source_id"), event)
            target = self._event_ref(effect.get("target_id"), event)
            if not source or not target:
                return
            deltas = {k: int(effect.get(f"{k}_delta", 0)) for k in ("trust", "fear", "respect", "affection")}
            cause = str(effect.get("cause") or event["summary"])
            self._relationship_delta_in_txn(db, campaign_id, str(source), str(target), deltas, cause, revision, event.get("world_time"), "cascade")
        elif kind == "need":
            npc_id = self._event_ref(effect.get("npc_id"), event)
            need = effect.get("need")
            if not npc_id or not need:
                return
            row = db.execute("SELECT value FROM npc_needs WHERE campaign_id=? AND npc_id=? AND need=?", (campaign_id, npc_id, need)).fetchone()
            if row:
                value = max(0.0, min(100.0, float(row["value"]) + float(effect.get("delta", 0))))
                db.execute("UPDATE npc_needs SET value=?,updated_at=? WHERE campaign_id=? AND npc_id=? AND need=?", (value, self.e._now(), campaign_id, npc_id, need))
        elif kind == "world_state":
            scope_type = str(effect.get("scope_type", "world"))
            scope_id = str(self._event_ref(effect.get("scope_id", "global"), event))
            key = str(effect.get("key", "reaction"))
            value = effect.get("value", True)
            db.execute(
                """INSERT INTO world_state(campaign_id,scope_type,scope_id,state_key,value_json,updated_at) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,scope_type,scope_id,state_key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
                (campaign_id, scope_type, scope_id, key, self.e._dumps(value), self.e._now()),
            )
        elif kind == "resource":
            node_id = effect.get("node_id")
            if node_id:
                row = db.execute("SELECT qty,qty_max FROM resource_nodes WHERE campaign_id=? AND id=?", (campaign_id, node_id)).fetchone()
                if row:
                    qty = max(0.0, min(float(row["qty_max"]), float(row["qty"]) + float(effect.get("delta", 0))))
                    db.execute("UPDATE resource_nodes SET qty=?,updated_at=? WHERE campaign_id=? AND id=?", (qty, self.e._now(), campaign_id, node_id))
        elif kind == "environment_effect":
            target_spec=dict(effect.get("target") or {})
            if not target_spec and event.get("region"):
                target_spec={"type":"location","id":event.get("region")}
            if target_spec:
                target=EnvironmentKernel(self.e)._bind_target_db(db,campaign_id,target_spec)
                EnvironmentKernel(self.e)._apply_effect_db(
                    db,campaign_id,str(effect.get("effect_type","smoke")),target,
                    intensity=float(effect.get("intensity",0.3)),amount=float(effect.get("amount",0)),
                    source_key=str(effect.get("source_key") or f"event:{event.get('event_type','reaction')}"),
                    state=dict(effect.get("state") or {}),world_time=event.get("world_time"),
                )
        elif kind == "emit":
            self._emit(
                db,
                campaign_id,
                revision,
                queue,
                event_type=str(effect.get("event_type", "sim_reaction")),
                summary=str(effect.get("summary", "A consequence followed.")),
                payload=dict(effect.get("payload") or {}),
                region=self._event_ref(effect.get("region", event.get("region")), event),
                actor_id=self._event_ref(effect.get("actor_id", event.get("actor_id")), event),
                target_id=self._event_ref(effect.get("target_id", event.get("target_id")), event),
                world_time=event.get("world_time"),
                depth=int(event.get("depth", 0)) + 1,
                persist=True,
            )

    def _select_reaction_actors(self, db: sqlite3.Connection, campaign_id: str, selector: dict[str, Any], event: dict[str, Any]) -> list[str | None]:
        if not selector:
            return [event.get("actor_id")]
        target = event.get("target_id")
        if "who" in selector:
            who = self._event_ref(selector.get("who"), event)
            return [str(who)] if who else []
        if "related_to_target" in selector and target:
            trust_gte = int((selector.get("related_to_target") or {}).get("trust_gte", 0))
            rows = db.execute(
                """SELECT source_id AS who FROM relationships WHERE campaign_id=? AND target_id=? AND trust>=?
                   UNION
                   SELECT target_id AS who FROM relationships WHERE campaign_id=? AND source_id=? AND trust>=?
                   ORDER BY who""",
                (campaign_id, target, trust_gte, campaign_id, target, trust_gte),
            ).fetchall()
            return [str(r["who"]) for r in rows if str(r["who"]) != str(target)]
        if "same_location" in selector:
            location = selector.get("same_location")
            if location in (True, None, "$region"):
                location = event.get("region")
            location = self._event_ref(location, event)
            if not location and target:
                row = db.execute("SELECT location FROM npcs WHERE campaign_id=? AND id=?", (campaign_id, target)).fetchone()
                location = row["location"] if row else None
            if not location:
                return []
            rows = db.execute("SELECT id FROM npcs WHERE campaign_id=? AND location=? AND hp>0 ORDER BY id", (campaign_id, location)).fetchall()
            # Never select the event target itself, and hp>0 prevents dead NPCs from reacting.
            return [str(r["id"]) for r in rows if str(r["id"]) != str(target)]
        return []

    def _drain_reactions(self, db: sqlite3.Connection, campaign_id: str, revision: int, queue: deque[dict[str, Any]]) -> int:
        cfg = self._ensure_config(db, campaign_id)
        max_depth = int(cfg["max_cascade_depth"])
        max_events = int(cfg["max_cascade_events"])
        processed = 0
        capped = False
        reaction_counts: dict[tuple[str, str | None, str | None], int] = {}
        while queue and processed < max_events:
            event = queue.popleft()  # FIFO => breadth-first cascade
            depth = int(event.get("depth", 0))
            if depth >= max_depth:
                continue
            reactions = db.execute(
                "SELECT * FROM sim_reactions WHERE campaign_id=? AND enabled=1 AND trigger_event_type=? ORDER BY priority,id",
                (campaign_id, event["event_type"]),
            ).fetchall()
            for row in reactions:
                selector = self.e._loads(row["selector_json"] or "{}")
                actors = self._select_reaction_actors(db, campaign_id, selector, event)
                for who in actors:
                    key = (str(row["id"]), who, event.get("target_id"))
                    count = reaction_counts.get(key, 0)
                    limit = 1 if row["repeat_policy"] == "once_per_cascade" else int(row["repeat_limit"])
                    if count >= limit:
                        continue
                    chance_key = f"{event.get('world_time')}:{event.get('event_type')}:{event.get('actor_id')}:{event.get('target_id')}:{who}:{count}"
                    if self._rand_keyed(db, campaign_id, f"cascade:{row['id']}", chance_key) > float(row["probability"]):
                        continue
                    reaction_counts[key] = count + 1
                    reaction_event = dict(event)
                    if who is not None:
                        reaction_event["actor_id"] = who
                    effects = self.e._loads(row["effects_json"])
                    for effect in effects:
                        self._apply_effect(db, campaign_id, revision, queue, reaction_event, effect)
                        processed += 1
                        if processed >= max_events:
                            capped = True
                            break
                    if capped:
                        break
                if capped:
                    break
        if queue or capped:
            self.e._insert_event(
                db, campaign_id, revision, "sim_cascade_capped", "Simulation cascade hit its safety cap",
                payload={"processed": processed, "remaining": len(queue), "max_depth": max_depth, "max_events": max_events},
            )
            queue.clear()
        return processed

    # ---------- archetypes ----------

    def _drift(self, db: sqlite3.Connection, campaign_id: str, revision: int, rule: sqlite3.Row, steps: int, *, log_summary: bool = True) -> int:
        if steps <= 0:
            return 0
        target = rule["target"]
        table, col, key_cols, hard_min, hard_max = TARGETS[target]
        p = self.e._loads(rule["params_json"])
        k = float(p.get("k", 0.1))
        baseline = float(p.get("baseline", 0))
        if not 0 <= k <= 1:
            raise ValueError(f"drift rule {rule['id']} k must be 0..1")
        lo = hard_min if p.get("min") is None else max(hard_min if hard_min is not None else -math.inf, float(p["min"]))
        hi = hard_max if p.get("max") is None else min(hard_max if hard_max is not None else math.inf, float(p["max"]))
        rows = db.execute(f"SELECT rowid,{','.join(key_cols)},{col} FROM {table} WHERE campaign_id=?", (campaign_id,)).fetchall()
        changed = 0
        factor = (1.0 - k) ** steps
        now = self.e._now()
        for row in rows:
            entity_key = "|".join(str(row[c]) for c in key_cols)
            acc = db.execute("SELECT value,last_written FROM sim_accumulators WHERE campaign_id=? AND rule_id=? AND entity_key=?", (campaign_id, rule["id"], entity_key)).fetchone()
            current = float(row[col])
            x = float(acc["value"]) if acc and float(acc["last_written"]) == current else current
            new_float = baseline + (x - baseline) * factor
            if math.isfinite(lo):
                new_float = max(lo, new_float)
            if math.isfinite(hi):
                new_float = min(hi, new_float)
            new_value = int(round(new_float))
            if hard_min is not None:
                new_value = max(int(hard_min), new_value)
            if hard_max is not None:
                new_value = min(int(hard_max), new_value)
            db.execute(
                """INSERT INTO sim_accumulators(campaign_id,rule_id,entity_key,value,last_written,updated_at) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,rule_id,entity_key) DO UPDATE SET value=excluded.value,last_written=excluded.last_written,updated_at=excluded.updated_at""",
                (campaign_id, rule["id"], entity_key, new_float, new_value, now),
            )
            if new_value != int(current):
                db.execute(f"UPDATE {table} SET {col}=?,updated_at=? WHERE rowid=?", (new_value, now, row["rowid"]))
                if table == "relationships":
                    source_id, target_id = str(row[key_cols[0]]), str(row[key_cols[1]])
                    deltas = {"trust": 0, "fear": 0, "respect": 0, "affection": 0}
                    deltas[col] = int(new_value - int(current))
                    cause = str(p.get("cause") or f"{rule['id']} drift toward {baseline}")
                    record_relationship_event(self.e, db, campaign_id, source_id, target_id, deltas, cause, revision, event_type="drift")
                changed += 1
        if changed and log_summary:
            self.e._insert_event(db, campaign_id, revision, "sim_drift", f"{changed} {target} values drifted toward {baseline}", payload={"rule_id": rule["id"], "target": target, "steps": steps, "affected": changed})
        return changed

    def _stock(self, db: sqlite3.Connection, campaign_id: str, revision: int, rule: sqlite3.Row, elapsed_days: float, season: str | None, at_time: datetime, *, log_summary: bool = True) -> int:
        if elapsed_days <= 0:
            return 0
        p = self.e._loads(rule["params_json"])
        rows = db.execute("SELECT * FROM resource_nodes WHERE campaign_id=? ORDER BY id", (campaign_id,)).fetchall()
        changed = 0
        now = self.e._now()
        environment = EnvironmentKernel(self.e)
        for row in rows:
            if p.get("item_id") and row["item_id"] != p["item_id"]:
                continue
            if p.get("location_id") and row["location_id"] != p["location_id"]:
                continue
            row_season = season or environment.season_for_time_db(
                db, campaign_id, at_time,
                scope_type="location", scope_id=str(row["location_id"]), fallback="summer",
            )
            mult = self.e._loads(row["season_mult_json"] or "{}").get(row_season, 1.0)
            new = min(float(row["qty_max"]), max(0.0, float(row["qty"]) + float(row["regen_per_day"]) * float(mult) * elapsed_days))
            if abs(new - float(row["qty"])) > 1e-12:
                db.execute("UPDATE resource_nodes SET qty=?,updated_at=? WHERE campaign_id=? AND id=?", (new, now, campaign_id, row["id"]))
                changed += 1
        if changed and log_summary:
            self.e._insert_event(db, campaign_id, revision, "sim_growth", f"{changed} resource nodes changed ({season or 'regional'})", payload={"rule_id": rule["id"], "elapsed_days": elapsed_days, "season": season or "regional", "affected": changed})
        return changed

    def _drama_multiplier(self, db: sqlite3.Connection, campaign_id: str, role: str, boundary: datetime) -> float:
        row = db.execute("SELECT * FROM drama_config WHERE campaign_id=?", (campaign_id,)).fetchone()
        if not row or not bool(row["enabled"]) or role in {"", "neutral", "none"}:
            return 1.0
        hp = db.execute("SELECT AVG(CASE WHEN max_hp>0 THEN CAST(hp AS REAL)/max_hp ELSE 1 END) ratio FROM characters WHERE campaign_id=?", (campaign_id,)).fetchone()
        hp_ratio = float(hp["ratio"]) if hp and hp["ratio"] is not None else 1.0
        cutoff = (boundary - timedelta(hours=float(row["hardship_window_hours"]))).isoformat()
        hardship_types = ("death", "disaster", "combat_start", "attack", "hp_delta", "war")
        placeholders = ",".join("?" for _ in hardship_types)
        count = db.execute(
            f"SELECT COUNT(*) n FROM events WHERE campaign_id=? AND world_time>=? AND world_time<=? AND event_type IN ({placeholders})",
            (campaign_id, cutoff, boundary.isoformat(), *hardship_types),
        ).fetchone()["n"]
        stressed = hp_ratio <= float(row["low_hp_threshold"]) or int(count) > 0
        role = role.lower()
        if role == "threat":
            return float(row["hardship_suppression"]) if stressed else float(row["calm_boost"])
        if role in {"relief", "reward"}:
            return float(row["relief_boost"]) if stressed else 0.75
        return 1.0

    def _chance_trials(self, db: sqlite3.Connection, campaign_id: str, rule: sqlite3.Row, start: datetime, end: datetime) -> list[dict[str, Any]]:
        cadence = rule["cadence"]
        p = self.e._loads(rule["params_json"])
        out=[]
        seconds=CADENCE_SECONDS[cadence]
        for boundary in self._iter_boundaries(start, end, cadence):
            boundary_index=int(math.floor(self._seconds_from_epoch(boundary)/seconds))
            out.append({"time":boundary,"priority":int(rule["priority"]),"rule_id":rule["id"],"trial_index":boundary_index,"params":p})
        return out

    def _chance_occurrences(self, db: sqlite3.Connection, campaign_id: str, rule: sqlite3.Row, start: datetime, end: datetime) -> list[dict[str, Any]]:
        cadence = rule["cadence"]
        p = self.e._loads(rule["params_json"])
        p_trial = float(p.get("p", p.get("p_day", 0.02)))
        if not 0 <= p_trial <= 1:
            raise ValueError(f"chance rule {rule['id']} probability must be 0..1")
        out: list[dict[str, Any]] = []
        seconds = CADENCE_SECONDS[cadence]
        for boundary in self._iter_boundaries(start, end, cadence):
            boundary_index = int(math.floor(self._seconds_from_epoch(boundary) / seconds))
            u = self._rand_keyed(db, campaign_id, f"chance:{rule['id']}", str(boundary_index))
            if u < p_trial:
                out.append({
                    "time": boundary,
                    "priority": int(rule["priority"]),
                    "rule_id": rule["id"],
                    # Absolute cadence-boundary index, stable across one-shot vs
                    # chunked catch-up and therefore useful in audit/replay logs.
                    "trial_index": boundary_index,
                    "params": p,
                })
        return out

    @staticmethod
    def _schedule_slot(routine: dict[str, Any], hour: int) -> str | None:
        candidates: list[tuple[int, str]] = []
        for key, dest in routine.items():
            if not isinstance(dest, str) or not isinstance(key, str) or ":" not in key:
                continue
            try:
                h = int(key.split(":", 1)[0])
            except ValueError:
                continue
            if 0 <= h <= hour:
                candidates.append((h, dest))
        if candidates:
            return max(candidates, key=lambda x: x[0])[1]
        # Before the first posting of the day, the NPC remains at the previous
        # day's last posting rather than falling into an undefined location.
        all_slots: list[tuple[int, str]] = []
        for key, dest in routine.items():
            if not isinstance(dest, str) or not isinstance(key, str) or ":" not in key:
                continue
            try:
                h = int(key.split(":", 1)[0])
            except ValueError:
                continue
            if 0 <= h <= 23:
                all_slots.append((h, dest))
        return max(all_slots, default=(None, None), key=lambda x: -1 if x[0] is None else x[0])[1]

    def _schedule(self, db: sqlite3.Connection, campaign_id: str, revision: int, end: datetime, steps: int) -> int:
        if steps <= 0:
            return 0
        # Utility-driven NPCs own their location; fixed schedules are fallback only.
        rows = db.execute(
            """SELECT n.id,n.name,n.location,n.routine_json FROM npcs n
               WHERE n.campaign_id=? AND n.hp>0 AND NOT EXISTS (
                 SELECT 1 FROM npc_actions a WHERE a.campaign_id=n.campaign_id AND a.npc_id=n.id AND a.enabled=1
               ) ORDER BY n.id""",
            (campaign_id,),
        ).fetchall()
        moved = 0
        now = self.e._now()
        for row in rows:
            dest = self._schedule_slot(self.e._loads(row["routine_json"] or "{}"), end.hour)
            if dest and dest != row["location"]:
                db.execute("UPDATE npcs SET location=?,updated_at=? WHERE campaign_id=? AND id=?", (dest, now, campaign_id, row["id"]))
                moved += 1
        if moved:
            self.e._insert_event(db, campaign_id, revision, "sim_routine", f"{moved} NPCs changed posting by {end.hour:02d}:00", payload={"affected": moved, "final_hour": end.hour})
        return moved

    def _spread_step(self, db: sqlite3.Connection, campaign_id: str, revision: int, rule: sqlite3.Row, queue: deque[dict[str, Any]], step_time: datetime) -> int:
        p = self.e._loads(rule["params_json"])
        key = str(p.get("state_key", ""))
        if not key:
            return 0
        seeded = {r["scope_id"] for r in db.execute("SELECT scope_id FROM world_state WHERE campaign_id=? AND scope_type='npc' AND state_key=? AND value_json NOT IN ('false','null','0')", (campaign_id, key)).fetchall()}
        if not seeded:
            return 0
        mode = str(p.get("mode", "relationship")).lower()
        p_hop = float(p.get("p_hop", 0.5))
        newly: set[str] = set()

        if mode in {"relationship", "both"}:
            edges = db.execute("SELECT source_id,target_id,trust FROM relationships WHERE campaign_id=? ORDER BY source_id,target_id", (campaign_id,)).fetchall()
            for edge in edges:
                for a, b in ((edge["source_id"], edge["target_id"]), (edge["target_id"], edge["source_id"])):
                    if a in seeded and b not in seeded and b not in newly:
                        trust_gate = p.get("trust_min")
                        if trust_gate is not None and int(edge["trust"]) < int(trust_gate):
                            continue
                        step_key = f"{step_time.isoformat()}:rel:{a}:{b}"
                        if self._rand_keyed(db, campaign_id, f"spread:{rule['id']}", step_key) < p_hop:
                            newly.add(str(b))

        if mode in {"road", "both"}:
            p_road = float(p.get("p_road", p_hop))
            decay_hours = max(1e-9, float(p.get("road_decay_hours", 24.0)))
            source_rows = db.execute(
                f"SELECT id,location FROM npcs WHERE campaign_id=? AND id IN ({','.join('?' for _ in seeded)}) ORDER BY id",
                (campaign_id, *sorted(seeded)),
            ).fetchall() if seeded else []
            for src in source_rows:
                links = db.execute("SELECT to_id,travel_hours FROM location_links WHERE campaign_id=? AND from_id=? ORDER BY to_id", (campaign_id, src["location"])).fetchall()
                for link in links:
                    recipients = db.execute("SELECT id FROM npcs WHERE campaign_id=? AND location=? AND hp>0 ORDER BY id", (campaign_id, link["to_id"])).fetchall()
                    prob = max(0.0, min(1.0, p_road * math.exp(-float(link["travel_hours"]) / decay_hours)))
                    for rec in recipients:
                        b=str(rec["id"])
                        if b in seeded or b in newly:
                            continue
                        step_key=f"{step_time.isoformat()}:road:{src['id']}:{src['location']}:{link['to_id']}:{b}"
                        if self._rand_keyed(db,campaign_id,f"spread:{rule['id']}",step_key) < prob:
                            newly.add(b)
        now = self.e._now()
        for npc_id in sorted(newly):
            db.execute(
                """INSERT INTO world_state(campaign_id,scope_type,scope_id,state_key,value_json,updated_at) VALUES(?,'npc',?,?,?,?)
                   ON CONFLICT(campaign_id,scope_type,scope_id,state_key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
                (campaign_id, npc_id, key, self.e._dumps(True), now),
            )
        if newly:
            self._emit(db, campaign_id, revision, queue, event_type="sim_spread", summary=f"'{key}' spread to {len(newly)} more people", payload={"rule_id": rule["id"], "state_key": key, "affected": len(newly), "reached": sorted(newly), "mode": mode}, world_time=step_time.isoformat(), persist=True)
        return len(newly)

    def _need_drift_steps(self, db: sqlite3.Connection, campaign_id: str, steps: int) -> int:
        if steps <= 0:
            return 0
        rows = db.execute("SELECT * FROM npc_needs WHERE campaign_id=? ORDER BY npc_id,need", (campaign_id,)).fetchall()
        now = self.e._now()
        changed = 0
        for row in rows:
            k = float(row["drift_per_day"])
            value = float(row["value"])
            baseline = float(row["baseline"])
            new = max(0.0, min(100.0, baseline + (value - baseline) * ((1.0 - k) ** steps)))
            if abs(new - value) > 1e-12:
                db.execute("UPDATE npc_needs SET value=?,updated_at=? WHERE campaign_id=? AND npc_id=? AND need=?", (new, now, campaign_id, row["npc_id"], row["need"]))
                changed += 1
        return changed

    def _lifecycle_step(self, db: sqlite3.Connection, campaign_id: str, revision: int, queue: deque[dict[str, Any]], step_time: datetime) -> int:
        rows = db.execute(
            """SELECT l.*,n.name,n.location,n.hp,n.status,n.faction_id FROM npc_lifecycle l
               JOIN npcs n ON n.campaign_id=l.campaign_id AND n.id=l.npc_id
               WHERE l.campaign_id=? AND l.alive=1 AND n.status='alive' AND n.hp>0 ORDER BY l.npc_id""",
            (campaign_id,),
        ).fetchall()
        changed=0
        dead_ids: set[str] = set()
        for row in rows:
            mortality=self.e._loads(row["mortality_json"] or "{}")
            if not bool(mortality.get("enabled", False)):
                continue
            age=max(0.0, step_time.year-float(row["birth_year"]))
            A=max(0.0,float(mortality.get("makeham", mortality.get("A", 0.0))))
            B=max(0.0,float(mortality.get("gompertz_b", mortality.get("B", 0.0))))
            C=float(mortality.get("gompertz_c", mortality.get("C", 0.0)))
            hazard_annual=max(0.0, A + B * math.exp(C * age))
            p_day=1.0-math.exp(-hazard_annual/365.2425)
            key=f"{step_time.date().isoformat()}:{row['npc_id']}"
            if self._rand_keyed(db,campaign_id,"lifecycle:death",key) < p_day:
                now=self.e._now()
                db.execute("UPDATE npc_lifecycle SET alive=0,updated_at=? WHERE campaign_id=? AND npc_id=?", (now,campaign_id,row["npc_id"]))
                db.execute("UPDATE npcs SET hp=0,status='dead',died_on=?,updated_at=? WHERE campaign_id=? AND id=?", (step_time.isoformat(),now,campaign_id,row["npc_id"]))
                self._emit(
                    db,campaign_id,revision,queue,event_type="death",summary=f"{row['name']} died of natural causes.",
                    payload={"npc_id":row["npc_id"],"age":age,"p_day":p_day},region=row["location"],actor_id=row["npc_id"],target_id=row["npc_id"],world_time=step_time.isoformat(),persist=True,
                )
                apply_succession(self.e, db, campaign_id, str(row["npc_id"]), world_time=step_time.isoformat(), revision=revision)
                dead_ids.add(str(row["npc_id"]))
                changed += 1

        # Thin configurable fertility.  Disabled by default.  We model a couple,
        # not a demographic population pyramid: one configured fertility profile
        # can produce at most one child per couple/day and observes a cooldown.
        living = db.execute(
            """SELECT l.*,n.name,n.location,n.faction_id,n.status,n.hp FROM npc_lifecycle l
               JOIN npcs n ON n.campaign_id=l.campaign_id AND n.id=l.npc_id
               WHERE l.campaign_id=? AND l.alive=1 AND n.status='alive' AND n.hp>0 ORDER BY l.npc_id""",
            (campaign_id,),
        ).fetchall()
        by_id={str(r["npc_id"]): r for r in living}
        seen_pairs:set[tuple[str,str]]=set()
        for row in living:
            spouse=str(row["spouse_id"]) if row["spouse_id"] else None
            if not spouse or spouse not in by_id:
                continue
            pair=tuple(sorted((str(row["npc_id"]),spouse)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            other=by_id[spouse]
            configs=[]
            for candidate in (row,other):
                cfg=self.e._loads(candidate["fertility_json"] or "{}")
                if bool(cfg.get("enabled",False)):
                    configs.append((str(candidate["npc_id"]),candidate,cfg))
            if not configs:
                continue
            configs.sort(key=lambda x:x[0])
            _source_id, source, fertility=configs[0]
            source_age=max(0.0,step_time.year-float(source["birth_year"]))
            partner=other if source["npc_id"]==row["npc_id"] else row
            partner_age=max(0.0,step_time.year-float(partner["birth_year"]))
            min_age=float(fertility.get("min_age",18)); max_age=float(fertility.get("max_age",45))
            partner_min=float(fertility.get("partner_min_age",18)); partner_max=float(fertility.get("partner_max_age",80))
            if not (min_age <= source_age <= max_age and partner_min <= partner_age <= partner_max):
                continue
            cooldown=max(0,int(fertility.get("cooldown_days",300)))
            last_dates=[d for d in (source["last_birth_on"],partner["last_birth_on"]) if d]
            if last_dates:
                last=max(datetime.fromisoformat(str(d)).date() for d in last_dates)
                if (step_time.date()-last).days < cooldown:
                    continue
            annual_rate=max(0.0,min(3.0,float(fertility.get("annual_birth_rate",0.20))))
            p_day=1.0-math.exp(-annual_rate/365.2425)
            key=f"{step_time.date().isoformat()}:{pair[0]}:{pair[1]}"
            if self._rand_keyed(db,campaign_id,"lifecycle:birth",key) >= p_day:
                continue
            digest=hashlib.sha256(f"{campaign_id}:{key}".encode("utf-8")).hexdigest()[:10]
            child_id=f"child_{digest}"
            if db.execute("SELECT 1 FROM npcs WHERE campaign_id=? AND id=?",(campaign_id,child_id)).fetchone():
                continue
            name=str(fertility.get("child_name") or f"Child-{digest[:6]}")[:200]
            location=str(source["location"])
            faction_id=source["faction_id"] or partner["faction_id"]
            now=self.e._now()
            db.execute(
                """INSERT INTO npcs(campaign_id,id,name,hp,max_hp,ac,location,faction_id,attitude,stats_json,conditions_json,beliefs_json,goals_json,routine_json,memory_json,status,died_on,updated_at)
                   VALUES(?,?,?,1,1,10,?,?,0,'{}','[]','[]','[]','{}','[]','alive',NULL,?)""",
                (campaign_id,child_id,name,location,faction_id,now),
            )
            child_mortality=fertility.get("child_mortality") or {"enabled":False}
            db.execute(
                """INSERT INTO npc_lifecycle(campaign_id,npc_id,birth_year,parents_json,spouse_id,mortality_json,fertility_json,heir_id,last_birth_on,alive,updated_at)
                   VALUES(?,?,?,?,NULL,?,'{}',NULL,NULL,1,?)""",
                (campaign_id,child_id,int(step_time.year),self.e._dumps(list(pair)),self.e._dumps(child_mortality),now),
            )
            db.execute("UPDATE npc_lifecycle SET last_birth_on=?,updated_at=? WHERE campaign_id=? AND npc_id IN (?,?)",(step_time.isoformat(),now,campaign_id,pair[0],pair[1]))
            self._emit(db,campaign_id,revision,queue,event_type="birth",summary=f"{name} was born.",payload={"npc_id":child_id,"parents":list(pair)},region=location,actor_id=pair[0],target_id=child_id,world_time=step_time.isoformat(),persist=True)
            changed += 1
        return changed

    @staticmethod
    def _curve_value(value: float, curve: str) -> float:
        value = max(0.0, min(1.0, float(value)))
        if curve == "linear":
            return value
        if curve == "urgent":
            return math.sqrt(value)
        if curve == "threshold":
            return 0.0 if value < 0.60 else (value - 0.60) / 0.40
        return value * value  # quadratic

    def _consideration_value(self, db: sqlite3.Connection, campaign_id: str, npc: sqlite3.Row, c: dict[str, Any]) -> float:
        ctype = str(c.get("type", "need"))
        value = 0.0
        if ctype == "need":
            row = db.execute("SELECT value,curve FROM npc_needs WHERE campaign_id=? AND npc_id=? AND need=?", (campaign_id, npc["id"], c.get("key"))).fetchone()
            if row:
                curve = str(row["curve"] or "quadratic")
                value = self._curve_value(float(row["value"]) / 100.0, curve)
        elif ctype == "resource":
            item_id = c.get("item_id")
            location = c.get("location_id") or npc["location"]
            row = db.execute("SELECT COALESCE(SUM(qty),0) qty,COALESCE(SUM(qty_max),0) qty_max FROM resource_nodes WHERE campaign_id=? AND item_id=? AND location_id=?", (campaign_id, item_id, location)).fetchone()
            value = float(row["qty"]) / float(row["qty_max"]) if row and float(row["qty_max"]) > 0 else 0.0
        elif ctype == "inventory":
            row = db.execute("SELECT qty FROM inventories WHERE campaign_id=? AND owner_kind='npc' AND owner_id=? AND item_id=?", (campaign_id, npc["id"], c.get("item_id"))).fetchone()
            qty = float(row["qty"]) if row else 0.0
            scale = max(float(c.get("scale", 1.0)), 1e-9)
            value = min(1.0, qty / scale)
        elif ctype == "relationship":
            field = str(c.get("field", "trust"))
            if field not in {"trust", "fear", "respect", "affection"}:
                raise ValueError("invalid relationship consideration field")
            target_id = str(c.get("target_id", "player"))
            row = db.execute(f"SELECT {field} FROM relationships WHERE campaign_id=? AND source_id=? AND target_id=?", (campaign_id, npc["id"], target_id)).fetchone()
            raw = float(row[field]) if row else 0.0
            value = (raw + 100.0) / 200.0
        elif ctype == "world_state":
            row = db.execute("SELECT value_json FROM world_state WHERE campaign_id=? AND scope_type=? AND scope_id=? AND state_key=?", (campaign_id, c.get("scope_type", "world"), c.get("scope_id", "global"), c.get("key"))).fetchone()
            if row:
                raw = self.e._loads(row["value_json"])
                if isinstance(raw, bool):
                    value = 1.0 if raw else 0.0
                elif isinstance(raw, (int, float)):
                    lo, hi = float(c.get("min", 0)), float(c.get("max", 100))
                    value = 0.0 if hi == lo else (float(raw) - lo) / (hi - lo)
        elif ctype == "environment":
            value = EnvironmentKernel(self.e).consideration_value_db(db,campaign_id,str(c.get("location_id") or npc["location"]),str(c.get("effect_type") or c.get("key") or "hazard"))
            if str(c.get("effect_type") or c.get("key") or "") == "hazard":
                value=max(EnvironmentKernel(self.e).consideration_value_db(db,campaign_id,str(c.get("location_id") or npc["location"]),x) for x in ("fire","smoke","water","gas","blight","corruption","heat","cold"))
        elif ctype in {"belief", "goal"}:
            field = "beliefs_json" if ctype == "belief" else "goals_json"
            values = self.e._loads(npc[field] or "[]")
            key = str(c.get("key", "")).strip().casefold()
            match = str(c.get("match", "exact")).lower()
            normalized = [str(x).strip().casefold() for x in values]
            if match == "contains":
                value = 1.0 if key and any(key in x for x in normalized) else 0.0
            elif match == "prefix":
                value = 1.0 if key and any(x.startswith(key) for x in normalized) else 0.0
            else:
                value = 1.0 if key and key in normalized else 0.0
        elif ctype == "mood":
            try:
                row = db.execute("SELECT COALESCE(SUM(mood_delta),0) mood FROM npc_thoughts WHERE campaign_id=? AND npc_id=? AND active=1 AND (expires_world_time IS NULL OR expires_world_time>(SELECT world_time FROM campaigns WHERE id=?))", (campaign_id, npc["id"], campaign_id)).fetchone()
                raw = float(row["mood"] if row else 0.0)
                value = (max(-100.0, min(100.0, raw)) + 100.0) / 200.0
            except sqlite3.OperationalError:
                value = 0.5
        elif ctype == "constant":
            value = float(c.get("value", 0))
        value = max(0.0, min(1.0, value))
        if c.get("invert"):
            value = 1.0 - value
        return value

    def _action_feasible(self, db: sqlite3.Connection, campaign_id: str, npc: sqlite3.Row, action: sqlite3.Row) -> bool:
        req = self.e._loads(action["requirements_json"] or "{}")
        for item_id, qty in (req.get("item") or {}).items():
            row = db.execute("SELECT qty FROM inventories WHERE campaign_id=? AND owner_kind='npc' AND owner_id=? AND item_id=?", (campaign_id, npc["id"], item_id)).fetchone()
            if not row or float(row["qty"]) < float(qty):
                return False
        for key, want in (req.get("world_state") or {}).items():
            row = db.execute("SELECT value_json FROM world_state WHERE campaign_id=? AND state_key=? ORDER BY scope_type,scope_id LIMIT 1", (campaign_id, key)).fetchone()
            if not row or self.e._loads(row["value_json"]) != want:
                return False
        for effect_type, minimum in (req.get("environment") or {}).items():
            if EnvironmentKernel(self.e).consideration_value_db(db,campaign_id,str(action["location"] or npc["location"]),str(effect_type)) < float(minimum):
                return False
        beliefs={str(x).strip().casefold() for x in self.e._loads(npc["beliefs_json"] or "[]")}
        goals={str(x).strip().casefold() for x in self.e._loads(npc["goals_json"] or "[]")}
        if any(str(x).strip().casefold() not in beliefs for x in (req.get("beliefs") or [])):
            return False
        if any(str(x).strip().casefold() not in goals for x in (req.get("goals") or [])):
            return False
        for item_id, qty in (req.get("resource") or {}).items():
            location = action["location"] or npc["location"]
            row = db.execute("SELECT COALESCE(SUM(qty),0) qty FROM resource_nodes WHERE campaign_id=? AND item_id=? AND location_id=?", (campaign_id, item_id, location)).fetchone()
            if not row or float(row["qty"]) < float(qty):
                return False
        return True

    def _shortest_travel_hours(self, db: sqlite3.Connection, campaign_id: str, start: str | None, goal: str | None) -> float | None:
        if not start or not goal:
            return None
        if start == goal:
            return 0.0
        try:
            rows = db.execute("SELECT from_id,to_id,travel_hours FROM location_links WHERE campaign_id=? ORDER BY from_id,to_id", (campaign_id,)).fetchall()
        except sqlite3.OperationalError:
            return None
        if not rows:
            return None
        import heapq
        adj: dict[str, list[tuple[str, float]]] = {}
        for r in rows:
            adj.setdefault(str(r["from_id"]), []).append((str(r["to_id"]), float(r["travel_hours"])))
        q=[(0.0, str(start))]
        best={str(start):0.0}
        while q:
            dist,node=heapq.heappop(q)
            if node == str(goal):
                return dist
            if dist != best.get(node):
                continue
            for nxt,w in adj.get(node, []):
                nd=dist+w
                if nd < best.get(nxt, math.inf):
                    best[nxt]=nd
                    heapq.heappush(q,(nd,nxt))
        return math.inf

    def _action_proximity(self, db: sqlite3.Connection, campaign_id: str, here: str | None, there: str | None) -> float:
        if there is None or here == there:
            return 1.0
        hours = self._shortest_travel_hours(db, campaign_id, here, there)
        if hours is None:
            return 0.6  # compatibility only until a campaign defines a world graph
        if math.isinf(hours):
            return 0.0
        return 1.0 / (1.0 + max(0.0, hours))

    def _score_actions(self, db: sqlite3.Connection, campaign_id: str, npc: sqlite3.Row, last_action: str | None, commitment_bonus: float) -> list[tuple[float, str, sqlite3.Row, list[dict[str, Any]]]]:
        actions = db.execute("SELECT * FROM npc_actions WHERE campaign_id=? AND npc_id=? AND enabled=1 ORDER BY action_id", (campaign_id, npc["id"])).fetchall()
        scored: list[tuple[float, str, sqlite3.Row, list[dict[str, Any]]]] = []
        for action in actions:
            if not self._action_feasible(db, campaign_id, npc, action):
                continue
            considerations = self.e._loads(action["considerations_json"])
            components: list[dict[str, Any]] = []
            score = float(action["base_utility"])
            for c in considerations:
                value = self._consideration_value(db, campaign_id, npc, c)
                weight = float(c.get("weight", 1.0))
                contrib = value * weight
                score += contrib
                components.append({"type": c.get("type", "need"), "key": c.get("key") or c.get("item_id") or c.get("field"), "value": round(value, 6), "weight": weight, "contribution": round(contrib, 6)})
            proximity = self._action_proximity(db, campaign_id, npc["location"], action["location"])
            score *= proximity
            if action["action_id"] == last_action:
                score += float(commitment_bonus)
            components.append({"type": "proximity", "value": round(proximity, 6)})
            scored.append((score, action["action_id"], action, components))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored

    def _select_action(self, db: sqlite3.Connection, campaign_id: str, rule_id: str, npc_id: str, step_time: datetime, ranked: list[tuple[float, str, sqlite3.Row, list[dict[str, Any]]]], *, top_k: int, temperature: float):
        if not ranked:
            return None
        pool = ranked[:max(1, int(top_k))]
        if temperature <= 1e-9 or len(pool) == 1:
            return pool[0]
        max_score = max(x[0] for x in pool)
        weights = [math.exp((x[0] - max_score) / max(float(temperature), 1e-9)) for x in pool]
        total = sum(weights)
        r = self._rand_keyed(db, campaign_id, f"decide:{rule_id}:{npc_id}", step_time.isoformat()) * total
        acc = 0.0
        for item, weight in zip(pool, weights):
            acc += weight
            if r <= acc:
                return item
        return pool[-1]

    def _apply_action_effects(self, db: sqlite3.Connection, campaign_id: str, npc: sqlite3.Row, action: sqlite3.Row, effects: list[dict[str, Any]]) -> None:
        now = self.e._now()
        if action["location"] and action["location"] != npc["location"]:
            db.execute("UPDATE npcs SET location=?,updated_at=? WHERE campaign_id=? AND id=?", (action["location"], now, campaign_id, npc["id"]))
        for effect in effects:
            kind = str(effect.get("type", "")).lower()
            if kind == "need":
                need = effect.get("need")
                row = db.execute("SELECT value FROM npc_needs WHERE campaign_id=? AND npc_id=? AND need=?", (campaign_id, npc["id"], need)).fetchone()
                if row:
                    value = max(0.0, min(100.0, float(row["value"]) + float(effect.get("delta", 0))))
                    db.execute("UPDATE npc_needs SET value=?,updated_at=? WHERE campaign_id=? AND npc_id=? AND need=?", (value, now, campaign_id, npc["id"], need))
            elif kind == "resource":
                node_id = effect.get("node_id")
                if node_id:
                    row = db.execute("SELECT qty,qty_max FROM resource_nodes WHERE campaign_id=? AND id=?", (campaign_id, node_id)).fetchone()
                    if row:
                        value = max(0.0, min(float(row["qty_max"]), float(row["qty"]) + float(effect.get("delta", 0))))
                        db.execute("UPDATE resource_nodes SET qty=?,updated_at=? WHERE campaign_id=? AND id=?", (value, now, campaign_id, node_id))
            elif kind == "inventory":
                item_id = str(effect.get("item_id", ""))
                if item_id:
                    row = db.execute("SELECT qty FROM inventories WHERE campaign_id=? AND owner_kind='npc' AND owner_id=? AND item_id=?", (campaign_id, npc["id"], item_id)).fetchone()
                    qty = max(0.0, (float(row["qty"]) if row else 0.0) + float(effect.get("delta", 0)))
                    db.execute("""INSERT INTO inventories(campaign_id,owner_kind,owner_id,item_id,qty,metadata_json,updated_at) VALUES(?,'npc',?,?,?,'{}',?)
                                  ON CONFLICT(campaign_id,owner_kind,owner_id,item_id) DO UPDATE SET qty=excluded.qty,updated_at=excluded.updated_at""",
                               (campaign_id, npc["id"], item_id, qty, now))
            elif kind == "environment":
                target_spec=dict(effect.get("target") or {"type":"location","id":action["location"] or npc["location"]})
                target=EnvironmentKernel(self.e)._bind_target_db(db,campaign_id,target_spec)
                EnvironmentKernel(self.e)._apply_effect_db(db,campaign_id,str(effect.get("effect_type","smoke")),target,intensity=float(effect.get("intensity",0.3)),amount=float(effect.get("amount",0)),source_key=f"npc:{npc['id']}",world_time=db.execute("SELECT world_time FROM campaigns WHERE id=?",(campaign_id,)).fetchone()["world_time"])

    def _decide_step(self, db: sqlite3.Connection, campaign_id: str, revision: int, rule: sqlite3.Row, queue: deque[dict[str, Any]], step_time: datetime) -> int:
        p = self.e._loads(rule["params_json"])
        npc_filter = p.get("npc_id")
        if npc_filter:
            npcs = db.execute("SELECT * FROM npcs WHERE campaign_id=? AND id=? AND hp>0", (campaign_id, npc_filter)).fetchall()
        else:
            npcs = db.execute("SELECT * FROM npcs WHERE campaign_id=? AND hp>0 ORDER BY id", (campaign_id,)).fetchall()
        changed = 0
        top_k = int(p.get("top_k", 3))
        temperature = float(p.get("temperature", 0.25))
        commitment_bonus = float(p.get("commitment_bonus", 0.05))
        for npc in npcs:
            state = db.execute("SELECT * FROM sim_agent_state WHERE campaign_id=? AND npc_id=?", (campaign_id, npc["id"])).fetchone()
            last_action = state["last_action"] if state else None
            committed_until = None
            if state and state["committed_until"]:
                committed_until = self._utc(datetime.fromisoformat(state["committed_until"]))
            if last_action and committed_until and committed_until > step_time:
                action = db.execute("SELECT * FROM npc_actions WHERE campaign_id=? AND npc_id=? AND action_id=? AND enabled=1", (campaign_id, npc["id"], last_action)).fetchone()
                if action and action["location"] and action["location"] != npc["location"]:
                    db.execute("UPDATE npcs SET location=?,updated_at=? WHERE campaign_id=? AND id=?", (action["location"], self.e._now(), campaign_id, npc["id"]))
                continue

            ranked = self._score_actions(db, campaign_id, npc, last_action, commitment_bonus)
            pick = self._select_action(db, campaign_id, str(rule["id"]), str(npc["id"]), step_time, ranked, top_k=top_k, temperature=temperature)
            if not pick:
                continue
            score, action_id, action, components = pick
            effects = self.e._loads(action["effects_json"])
            self._apply_action_effects(db, campaign_id, npc, action, effects)
            cost_hours = max(0.0, float(action["cost_hours"]))
            until = (step_time + timedelta(hours=cost_hours)).isoformat() if cost_hours > 0 else step_time.isoformat()
            db.execute(
                """INSERT INTO sim_agent_state(campaign_id,npc_id,last_action,last_score,last_decision_time,committed_until,updated_at) VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,npc_id) DO UPDATE SET last_action=excluded.last_action,last_score=excluded.last_score,last_decision_time=excluded.last_decision_time,committed_until=excluded.committed_until,updated_at=excluded.updated_at""",
                (campaign_id, npc["id"], action_id, score, step_time.isoformat(), until, self.e._now()),
            )
            if action_id != last_action:
                changed += 1
                # Persist a bounded causal decision-thought so dialogue can reflect why the NPC acted.
                ranked_reasons=sorted([c for c in components if c.get("type") != "proximity"],key=lambda c:-abs(float(c.get("contribution",0))))[:3]
                reason_bits=[f"{c.get('type')}:{c.get('key')} ({float(c.get('contribution',0)):+.2f})" for c in ranked_reasons if c.get("key")]
                cause=f"Chose {action_id}" + (" because " + ", ".join(reason_bits) if reason_bits else "")
                thought_id=f"decision:{npc['id']}:{str(rule['id'])}:{step_time.isoformat()}"
                db.execute("""INSERT INTO npc_thoughts(campaign_id,id,npc_id,cause,mood_delta,source_event_id,tags_json,created_world_time,expires_world_time,active,metadata_json,updated_at)
                              VALUES(?,?,?,?,0,NULL,?,?,NULL,1,?,?) ON CONFLICT(campaign_id,id) DO UPDATE SET cause=excluded.cause,tags_json=excluded.tags_json,active=1,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                           (campaign_id,thought_id,npc["id"],cause,self.e._dumps(["decision",str(action_id)]),step_time.isoformat(),self.e._dumps({"action_id":action_id,"score":round(score,6),"considerations":components}),self.e._now()))
                stale=db.execute("SELECT id FROM npc_thoughts WHERE campaign_id=? AND npc_id=? AND id LIKE 'decision:%' ORDER BY created_world_time DESC,id DESC LIMIT -1 OFFSET 8",(campaign_id,npc["id"])).fetchall()
                if stale:
                    db.executemany("UPDATE npc_thoughts SET active=0,updated_at=? WHERE campaign_id=? AND id=?",[(self.e._now(),campaign_id,r["id"]) for r in stale])
                self._emit(
                    db, campaign_id, revision, queue,
                    event_type="sim_decision", summary=f"{npc['name']} chose {action_id}",
                    payload={"rule_id": rule["id"], "npc_id": npc["id"], "action_id": action_id, "score": round(score, 6), "considerations": components, "committed_until": until},
                    actor_id=npc["id"], world_time=step_time.isoformat(), persist=not bool(p.get("_dry_run_silent", False)),
                )
            elif bool(p.get("emit_on_continue", False)):
                self._emit(
                    db, campaign_id, revision, queue,
                    event_type="sim_decision", summary=f"{npc['name']} continued {action_id}",
                    payload={"rule_id": rule["id"], "npc_id": npc["id"], "action_id": action_id, "score": round(score, 6), "continued": True, "committed_until": until},
                    actor_id=npc["id"], world_time=step_time.isoformat(), persist=False,
                )
        return changed

    # ---------- public advancement ----------

    def _integrate_between(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        revision: int,
        rules: Sequence[sqlite3.Row],
        start: datetime,
        end: datetime,
        season: str,
        tally: dict[str, int],
        *,
        include_end: bool,
    ) -> None:
        if end <= start:
            return
        # STOCK is continuous in elapsed time and is therefore integrated all the
        # way to the discontinuity. DRIFT and need decay are cadence-boundary
        # processes; when an event lands exactly on that boundary we exclude it
        # here and process it in priority order at the boundary itself.
        elapsed_days = (end - start).total_seconds() / 86400.0
        for rule in rules:
            if rule["archetype"] == "drift":
                steps = self._boundaries_between(start, end, rule["cadence"], include_end=include_end)
                if steps:
                    tally["drift"] += self._drift(db, campaign_id, revision, rule, steps, log_summary=False)
            elif rule["archetype"] == "stock":
                tally["stock"] += self._stock(db, campaign_id, revision, rule, elapsed_days, season, start + (end - start) / 2, log_summary=False)
        need_steps = self._boundaries_between(start, end, "day", include_end=include_end)
        self._need_drift_steps(db, campaign_id, need_steps)

    def advance_db(self, db: sqlite3.Connection, campaign_id: str, minutes: int, *, reason: str = "elapsed time", weather: str | None = None, season: str | None = None) -> dict[str, Any]:
        """Advance simulation using an existing serialized write transaction.

        This is the authoritative primitive used by composite operations such as
        rests, so world time, simulation consequences, effect expiry, and recovery
        can commit or roll back together.
        """
        campaign_id = self.e._clean_id(campaign_id)
        if not 0 <= int(minutes) <= 60 * 24 * 365:
            raise ValueError("minutes must be 0..525600")
        campaign = db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if not campaign:
            raise KeyError(f"unknown campaign: {campaign_id}")
        minutes = int(minutes)
        start = self._utc(datetime.fromisoformat(campaign["world_time"]))
        end = start + timedelta(minutes=minutes)
        self._ensure_config(db, campaign_id)
        rev = self.e._next_revision(db, campaign_id)
        rules = db.execute("SELECT * FROM sim_rules WHERE campaign_id=? AND enabled=1 ORDER BY priority,id", (campaign_id,)).fetchall()
        settings = self.e._loads(campaign["settings_json"] or "{}")
        environment = EnvironmentKernel(self.e)
        season_override = (season or settings.get("season"))
        season_override = str(season_override).lower() if season_override else None
        season_name = season_override or environment.season_for_time_db(db,campaign_id,end,fallback="summer")
        tally = {"drift": 0, "schedule": 0, "stock": 0, "chance": 0, "spread": 0, "decide": 0, "cascade": 0, "lifecycle": 0,
                 "environment_effects": 0, "environment_spread": 0, "environment_damage": 0, "environment_weather": 0, "environment_weather_targets": 0, "environment_disasters": 0, "environment_societal": 0,
                 "economy_extraction": 0, "economy_production": 0, "economy_consumption": 0, "economy_shipments_created": 0, "economy_shipments_delivered": 0, "economy_shipments_lost": 0,
                 "population_births": 0.0, "population_deaths": 0.0, "population_transitions": 0.0, "population_migration": 0.0,
                 "population_settlements": 0.0, "population_labor": 0.0, "population_service_updates": 0.0,
                 "population_household_updates": 0.0, "population_rank_changes": 0.0, "population_bootstrapped": 0.0}
        queue: deque[dict[str, Any]] = deque()
        environment_active = environment.has_activity_db(db,campaign_id)
        economy = EconomyKernel(self.e)
        economy_active = economy.has_activity_db(db,campaign_id)
        population = PopulationKernel(self.e)
        population_active = population.has_activity_db(db,campaign_id)

        # Build only the discontinuity timeline. Continuous/simple state is
        # integrated in closed form between these points. This is exact for
        # DRIFT/STOCK even when decisions or cascades modify their targets.
        timeline: dict[datetime, list[tuple[int, str, str, Any]]] = {}
        for rule in rules:
            archetype = rule["archetype"]
            if archetype == "chance":
                # Schedule deterministic trials, not just successes, so a drama
                # multiplier can observe state changes that occurred earlier in
                # the same catch-up interval without breaking chunk invariance.
                for occurrence in self._chance_trials(db, campaign_id, rule, start, end):
                    timeline.setdefault(occurrence["time"], []).append((int(rule["priority"]), "chance", str(rule["id"]), occurrence))
            elif archetype in {"spread", "decide"}:
                for boundary in self._iter_boundaries(start, end, rule["cadence"]):
                    timeline.setdefault(boundary, []).append((int(rule["priority"]), archetype, str(rule["id"]), rule))

        has_lifecycle = db.execute("SELECT 1 FROM npc_lifecycle WHERE campaign_id=? AND alive=1 LIMIT 1", (campaign_id,)).fetchone() is not None
        if has_lifecycle:
            for boundary in self._iter_boundaries(start, end, "day"):
                timeline.setdefault(boundary, []).append((-100, "lifecycle", "__lifecycle__", None))
        if environment_active and end > start:
            # Only canonical absolute-hour boundaries are physical integration
            # points. Arbitrary request tails would make 60 differ from 30+30.
            for boundary in self._iter_boundaries(start,end,"hour"):
                timeline.setdefault(boundary, []).append((-80,"environment","__environment__",None))
        if economy_active and end > start:
            # Economy follows environment at canonical absolute-hour boundaries.
            # Never add an arbitrary request-tail step: it would make 60 differ
            # from 30+30 and destroy deterministic chunk invariance.
            for boundary in self._iter_boundaries(start,end,"hour"):
                timeline.setdefault(boundary, []).append((-70,"economy","__economy__",None))
        if population_active and end > start:
            # Demography is daily and observes the economy state settled earlier
            # at the same boundary.
            for boundary in self._iter_boundaries(start,end,"day"):
                timeline.setdefault(boundary, []).append((-60,"population","__population__",None))

        cursor = start
        for event_time in sorted(timeline):
            self._integrate_between(db, campaign_id, rev, rules, cursor, event_time, season_override, tally, include_end=False)

            # Needs mature before the day's utility decision. Rule-driven DRIFT
            # is instead ordered by the rule's explicit priority alongside
            # CHANCE/SPREAD/DECIDE at this exact timestamp.
            if self._is_boundary(event_time, "day"):
                self._need_drift_steps(db, campaign_id, 1)

            entries = list(timeline[event_time])
            for rule in rules:
                if rule["archetype"] == "drift" and self._is_boundary(event_time, rule["cadence"]):
                    entries.append((int(rule["priority"]), "drift", str(rule["id"]), rule))
            entries.sort(key=lambda x: (x[0], x[2], x[1]))

            for _priority, kind, _rule_id, obj in entries:
                if kind == "environment":
                    def _env_emit(event_type, summary, payload, region, when):
                        self._emit(db,campaign_id,rev,queue,event_type=event_type,summary=summary,payload=payload,region=region,world_time=when.isoformat(),persist=True)
                    env_tally=environment.step_db(db,campaign_id,rev,event_time,emit=_env_emit)
                    tally["environment_effects"] += env_tally["effects"]
                    tally["environment_spread"] += env_tally["spread"]
                    tally["environment_damage"] += env_tally["damage"]
                    tally["environment_weather"] += env_tally["weather"]
                    tally["environment_weather_targets"] += env_tally["weather_targets"]
                    tally["environment_disasters"] += env_tally["disasters"]
                    tally["environment_societal"] += env_tally.get("societal",0)
                elif kind == "economy":
                    def _economy_emit(event_type, summary, payload, region, when):
                        self._emit(db,campaign_id,rev,queue,event_type=event_type,summary=summary,payload=payload,region=region,world_time=when.isoformat(),persist=True)
                    econ_tally=economy.step_db(db,campaign_id,rev,event_time,emit=_economy_emit)
                    tally["economy_extraction"] += econ_tally.get("extraction",0)
                    tally["economy_production"] += econ_tally.get("production",0)
                    tally["economy_consumption"] += econ_tally.get("consumption",0)
                    tally["economy_shipments_created"] += econ_tally.get("shipments_created",0)
                    tally["economy_shipments_delivered"] += econ_tally.get("shipments_delivered",0)
                    tally["economy_shipments_lost"] += econ_tally.get("shipments_lost",0)
                elif kind == "population":
                    def _population_emit(event_type, summary, payload, region, when):
                        self._emit(db,campaign_id,rev,queue,event_type=event_type,summary=summary,payload=payload,region=region,world_time=when.isoformat(),persist=True)
                    pop_tally=population.step_db(db,campaign_id,rev,event_time,emit=_population_emit)
                    tally["population_births"] += pop_tally.get("births",0.0)
                    tally["population_deaths"] += pop_tally.get("deaths",0.0)
                    tally["population_transitions"] += pop_tally.get("transitions",0.0)
                    tally["population_migration"] += pop_tally.get("migration",0.0)
                    tally["population_settlements"] += pop_tally.get("settlements",0.0)
                    tally["population_labor"] += pop_tally.get("labor",0.0)
                    tally["population_service_updates"] += pop_tally.get("service_updates",0.0)
                    tally["population_household_updates"] += pop_tally.get("household_updates",0.0)
                    tally["population_rank_changes"] += pop_tally.get("rank_changes",0.0)
                    tally["population_bootstrapped"] += pop_tally.get("bootstrapped",0.0)
                elif kind == "chance":
                    occurrence = obj
                    p = occurrence["params"]
                    base_p = float(p.get("p", p.get("p_day", 0.02)))
                    if not 0 <= base_p <= 1:
                        raise ValueError(f"chance rule {occurrence['rule_id']} probability must be 0..1")
                    role = str(p.get("drama_role", p.get("director_role", "neutral")))
                    drama_multiplier = self._drama_multiplier(db, campaign_id, role, event_time)
                    location_id = p.get("location_id")
                    if not location_id and p.get("actor_id"):
                        actor_loc = db.execute("SELECT location FROM npcs WHERE campaign_id=? AND id=?", (campaign_id, p.get("actor_id"))).fetchone()
                        if not actor_loc:
                            actor_loc = db.execute("SELECT location FROM characters WHERE campaign_id=? AND id=?", (campaign_id, p.get("actor_id"))).fetchone()
                        location_id = actor_loc["location"] if actor_loc else None
                    director_multiplier = WorldLayerKernel(self.e).event_multiplier(db, campaign_id, role, location_id, p.get("scene_id"))
                    effective_p = max(0.0, min(1.0, base_p * drama_multiplier * director_multiplier))
                    u = self._rand_keyed(db, campaign_id, f"chance:{occurrence['rule_id']}", str(occurrence["trial_index"]))
                    if u < effective_p:
                        required = dict(p.get("requires_content") or {})
                        missing_required = False
                        if required:
                            req_kind = str(required.get("kind", "")).lower()
                            req_id = str(required.get("id", ""))
                            table_map = {"faction": "factions", "npc": "npcs", "location": "locations", "item": "item_defs", "archetype": "npc_archetypes"}
                            table = table_map.get(req_kind)
                            exists = False
                            if table and req_id:
                                try:
                                    exists = db.execute(f"SELECT 1 FROM {table} WHERE campaign_id=? AND id=? LIMIT 1", (campaign_id, req_id)).fetchone() is not None
                                except sqlite3.OperationalError:
                                    exists = False
                            if not exists:
                                missing_required = True
                                gap_key = str(required.get("gap_key") or f"{req_kind}:{req_id}")[:100]
                                try:
                                    db.execute(
                                        """INSERT INTO content_gaps(campaign_id,gap_key,kind,scope_id,summary,context_json,status,created_at,resolved_at)
                                           VALUES(?,?,?,?,?,?,'open',?,NULL)
                                           ON CONFLICT(campaign_id,gap_key) DO UPDATE SET kind=excluded.kind,scope_id=excluded.scope_id,summary=excluded.summary,context_json=excluded.context_json,status='open',resolved_at=NULL""",
                                        (campaign_id, gap_key, req_kind or "unknown", location_id, str(required.get("summary") or f"Simulation requires missing {req_kind}:{req_id}")[:1000], self.e._dumps({"rule_id": occurrence["rule_id"], "required_id": req_id, "event_type": p.get("event_type"), "location_id": location_id}), self.e._now()),
                                    )
                                    self.e._insert_event(db, campaign_id, rev, "content_gap", f"Content gap: {gap_key}", region=location_id, payload={"gap_key": gap_key, "kind": req_kind, "required_id": req_id, "rule_id": occurrence["rule_id"]}, world_time_override=event_time.isoformat())
                                except sqlite3.OperationalError:
                                    pass
                        if not missing_required:
                            self._emit(
                                db, campaign_id, rev, queue,
                                event_type=str(p.get("event_type", "sim_event")),
                                summary=str(p.get("summary", "Something happened off-screen.")),
                                payload={"rule_id": occurrence["rule_id"], "trial_index": occurrence["trial_index"], "base_p": base_p, "drama_multiplier": drama_multiplier, "director_multiplier": director_multiplier, "effective_p": effective_p, **dict(p.get("payload") or {})},
                                region=p.get("region") or location_id, actor_id=p.get("actor_id"), target_id=p.get("target_id"), world_time=event_time.isoformat(), persist=True,
                            )
                            tally["chance"] += 1
                elif kind == "lifecycle":
                    tally["lifecycle"] += self._lifecycle_step(db, campaign_id, rev, queue, event_time)
                elif kind == "spread":
                    tally["spread"] += self._spread_step(db, campaign_id, rev, obj, queue, event_time)
                elif kind == "decide":
                    tally["decide"] += self._decide_step(db, campaign_id, rev, obj, queue, event_time)
                else:  # drift at this exact boundary
                    tally["drift"] += self._drift(db, campaign_id, rev, obj, 1, log_summary=False)
                if queue:
                    tally["cascade"] += self._drain_reactions(db, campaign_id, rev, queue)
            cursor = event_time

        self._integrate_between(db, campaign_id, rev, rules, cursor, end, season_override, tally, include_end=True)

        # Fixed schedules are a display/posting rule; utility-driven NPCs are
        # excluded, so only the final posting is required for long catch-up.
        schedule_steps = max((self._boundaries(start, end, r["cadence"]) for r in rules if r["archetype"] == "schedule"), default=0)
        if schedule_steps:
            tally["schedule"] += self._schedule(db, campaign_id, rev, end, schedule_steps)

        environment_weather = environment.world_weather_db(db,campaign_id) if environment_active else None
        next_weather = (weather or environment_weather or campaign["weather"]).strip()[:120]
        db.execute("UPDATE campaigns SET world_time=?,weather=?,updated_at=? WHERE id=?", (end.isoformat(), next_weather, self.e._now(), campaign_id))
        if tally["drift"]:
            self.e._insert_event(db, campaign_id, rev, "sim_drift", f"{tally['drift']} drift updates occurred during catch-up", payload={"affected_updates": tally["drift"]}, world_time_override=end.isoformat())
        if tally["stock"]:
            self.e._insert_event(db, campaign_id, rev, "sim_growth", f"{tally['stock']} stock updates occurred during catch-up", payload={"affected_updates": tally["stock"], "season": season_name}, world_time_override=end.isoformat())
        self.e._insert_event(
            db, campaign_id, rev, "world_advance", reason,
            payload={"minutes": minutes, "old_time": campaign["world_time"], "new_time": end.isoformat(), "weather": next_weather, "season": season_name, "simulation": tally},
            world_time_override=end.isoformat(),
        )
        row = db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        out = dict(row)
        out["settings"] = self.e._loads(out.pop("settings_json"))
        out["simulation"] = tally
        return out

    def advance(self, campaign_id: str, minutes: int, *, reason: str = "elapsed time", weather: str | None = None, season: str | None = None) -> dict[str, Any]:
        campaign_id = self.e._clean_id(campaign_id)
        if not 0 <= int(minutes) <= 60 * 24 * 365:
            raise ValueError("minutes must be 0..525600")
        self.e._ensure_campaign_exists(campaign_id)
        with self.e._write_db() as db:
            return self.advance_db(db, campaign_id, minutes, reason=reason, weather=weather, season=season)
