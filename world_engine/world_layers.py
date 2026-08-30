from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime
from typing import Any, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import WorldEngine

LAYER_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS scenes (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    location_id TEXT NOT NULL,
    scene_type TEXT NOT NULL DEFAULT 'exploration',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','ending')),
    radius_m REAL NOT NULL DEFAULT 30 CHECK(radius_m > 0),
    state_json TEXT NOT NULL DEFAULT '{}',
    created_world_time TEXT NOT NULL,
    updated_world_time TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_scene_per_campaign
    ON scenes(campaign_id) WHERE status='active';
CREATE INDEX IF NOT EXISTS idx_scenes_location
    ON scenes(campaign_id,location_id,status);

CREATE TABLE IF NOT EXISTS scene_entities (
    campaign_id TEXT NOT NULL,
    scene_id TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('character','npc')),
    actor_id TEXT NOT NULL,
    x REAL NOT NULL DEFAULT 0,
    y REAL NOT NULL DEFAULT 0,
    z REAL NOT NULL DEFAULT 0,
    zone TEXT NOT NULL DEFAULT 'center',
    stance TEXT NOT NULL DEFAULT 'neutral',
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,scene_id,actor_kind,actor_id),
    FOREIGN KEY(campaign_id,scene_id) REFERENCES scenes(campaign_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scene_features (
    campaign_id TEXT NOT NULL,
    scene_id TEXT NOT NULL,
    id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'feature',
    x REAL NOT NULL DEFAULT 0,
    y REAL NOT NULL DEFAULT 0,
    z REAL NOT NULL DEFAULT 0,
    blocks_los INTEGER NOT NULL DEFAULT 0,
    difficult INTEGER NOT NULL DEFAULT 0,
    persistent INTEGER NOT NULL DEFAULT 0,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,scene_id,id),
    FOREIGN KEY(campaign_id,scene_id) REFERENCES scenes(campaign_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS directors (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    director_kind TEXT NOT NULL DEFAULT 'civic' CHECK(director_kind IN ('civic','realm','faction','divine','power')),
    scope_type TEXT NOT NULL DEFAULT 'location' CHECK(scope_type IN ('location','region','realm','scene','global')),
    scope_id TEXT,
    source_kind TEXT NOT NULL DEFAULT 'npc' CHECK(source_kind IN ('npc','faction','faction_leader','deity','power')),
    source_id TEXT NOT NULL,
    authority REAL NOT NULL DEFAULT 1 CHECK(authority BETWEEN 0 AND 1),
    priority INTEGER NOT NULL DEFAULT 100,
    weights_json TEXT NOT NULL DEFAULT '{}',
    policies_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_directors_scope
    ON directors(campaign_id,enabled,scope_type,scope_id,priority,id);

CREATE TABLE IF NOT EXISTS ownership (
    campaign_id TEXT NOT NULL,
    asset_kind TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    owner_kind TEXT NOT NULL CHECK(owner_kind IN ('character','npc','faction','location')),
    owner_id TEXT NOT NULL,
    since TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,asset_kind,asset_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ownership_owner
    ON ownership(campaign_id,owner_kind,owner_id);
"""


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _is_living_npc(db: sqlite3.Connection, campaign_id: str, npc_id: str | None) -> bool:
    if not npc_id:
        return False
    row = db.execute(
        "SELECT hp,status FROM npcs WHERE campaign_id=? AND id=?",
        (campaign_id, npc_id),
    ).fetchone()
    return bool(row and int(row["hp"]) > 0 and str(row["status"] or "alive") == "alive")


def choose_successor(db: sqlite3.Connection, campaign_id: str, dead_npc_id: str, *, faction_id: str | None = None) -> str | None:
    """Deterministic thin succession rule.

    Order: declared heir -> living spouse -> living child -> highest-trust living
    relation -> first living member of the same faction.  The rule is intentionally
    simple and fully inspectable; campaign content can override it by declaring heir_id.
    """
    lifecycle = db.execute(
        "SELECT heir_id,spouse_id FROM npc_lifecycle WHERE campaign_id=? AND npc_id=?",
        (campaign_id, dead_npc_id),
    ).fetchone()
    if lifecycle:
        for candidate in (lifecycle["heir_id"], lifecycle["spouse_id"]):
            if _is_living_npc(db, campaign_id, candidate):
                return str(candidate)

    child_rows = db.execute(
        "SELECT npc_id,parents_json FROM npc_lifecycle WHERE campaign_id=? AND alive=1 ORDER BY npc_id",
        (campaign_id,),
    ).fetchall()
    for row in child_rows:
        if dead_npc_id in _loads(row["parents_json"], []):
            if _is_living_npc(db, campaign_id, row["npc_id"]):
                return str(row["npc_id"])

    rel_rows = db.execute(
        """SELECT source_id,target_id,trust,respect FROM relationships
           WHERE campaign_id=? AND (source_id=? OR target_id=?)
           ORDER BY (trust+respect) DESC, source_id, target_id""",
        (campaign_id, dead_npc_id, dead_npc_id),
    ).fetchall()
    for row in rel_rows:
        candidate = row["target_id"] if row["source_id"] == dead_npc_id else row["source_id"]
        if candidate != dead_npc_id and _is_living_npc(db, campaign_id, candidate):
            return str(candidate)

    if faction_id:
        row = db.execute(
            """SELECT id FROM npcs WHERE campaign_id=? AND faction_id=? AND status='alive' AND hp>0 AND id<>?
               ORDER BY id LIMIT 1""",
            (campaign_id, faction_id, dead_npc_id),
        ).fetchone()
        if row:
            return str(row["id"])
    return None


def apply_succession(
    engine: "WorldEngine",
    db: sqlite3.Connection,
    campaign_id: str,
    dead_npc_id: str,
    *,
    world_time: str,
    revision: int,
) -> list[dict[str, Any]]:
    npc = db.execute(
        "SELECT id,name,faction_id,location FROM npcs WHERE campaign_id=? AND id=?",
        (campaign_id, dead_npc_id),
    ).fetchone()
    faction_id = str(npc["faction_id"]) if npc and npc["faction_id"] else None
    successor = choose_successor(db, campaign_id, dead_npc_id, faction_id=faction_id)
    changes: list[dict[str, Any]] = []

    owned = db.execute(
        "SELECT * FROM ownership WHERE campaign_id=? AND owner_kind='npc' AND owner_id=? ORDER BY asset_kind,asset_id",
        (campaign_id, dead_npc_id),
    ).fetchall()
    for row in owned:
        if successor:
            db.execute(
                "UPDATE ownership SET owner_id=?,since=?,updated_at=? WHERE campaign_id=? AND asset_kind=? AND asset_id=?",
                (successor, world_time, engine._now(), campaign_id, row["asset_kind"], row["asset_id"]),
            )
            summary = f"{row['asset_kind']} {row['asset_id']} passed from {dead_npc_id} to {successor}."
        else:
            db.execute(
                "DELETE FROM ownership WHERE campaign_id=? AND asset_kind=? AND asset_id=?",
                (campaign_id, row["asset_kind"], row["asset_id"]),
            )
            summary = f"{row['asset_kind']} {row['asset_id']} became unowned after {dead_npc_id} died."
        engine._insert_event(
            db, campaign_id, revision, "succession", summary,
            region=npc["location"] if npc else None,
            actor_id=dead_npc_id, target_id=successor,
            payload={"asset_kind": row["asset_kind"], "asset_id": row["asset_id"], "from": dead_npc_id, "to": successor},
            world_time_override=world_time,
        )
        changes.append({"kind": "ownership", "asset_kind": row["asset_kind"], "asset_id": row["asset_id"], "successor": successor})

    leader_rows = db.execute(
        "SELECT id,name FROM factions WHERE campaign_id=? AND leader_id=? ORDER BY id",
        (campaign_id, dead_npc_id),
    ).fetchall()
    for faction in leader_rows:
        faction_successor = choose_successor(db, campaign_id, dead_npc_id, faction_id=faction["id"])
        db.execute(
            "UPDATE factions SET leader_id=?,updated_at=? WHERE campaign_id=? AND id=?",
            (faction_successor, engine._now(), campaign_id, faction["id"]),
        )
        engine._insert_event(
            db, campaign_id, revision, "succession",
            f"Leadership of {faction['name']} passed from {dead_npc_id} to {faction_successor or 'no successor'}.",
            region=npc["location"] if npc else None,
            actor_id=dead_npc_id, target_id=faction_successor,
            payload={"faction_id": faction["id"], "from": dead_npc_id, "to": faction_successor},
            world_time_override=world_time,
        )
        changes.append({"kind": "faction_leader", "faction_id": faction["id"], "successor": faction_successor})
    return changes


class WorldLayerKernel:
    MAX_SCENE_ENTITIES = 12

    def __init__(self, engine: "WorldEngine"):
        self.e = engine

    def _actor_name_status(self, db: sqlite3.Connection, campaign_id: str, kind: str, actor_id: str) -> tuple[str, str] | None:
        table = "characters" if kind == "character" else "npcs"
        row = db.execute(f"SELECT name,status FROM {table} WHERE campaign_id=? AND id=?", (campaign_id, actor_id)).fetchone()
        if not row:
            return None
        return str(row["name"]), str(row["status"] or "alive")

    def start_scene(
        self,
        campaign_id: str,
        scene_id: str,
        location_id: str,
        *,
        scene_type: str = "exploration",
        radius_m: float = 30,
        entities: Sequence[dict[str, Any]] = (),
        features: Sequence[dict[str, Any]] = (),
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        campaign_id, scene_id = self.e._clean_id(campaign_id), self.e._clean_id(scene_id)
        location_id = self.e._clean_id(location_id)
        if not 1 <= float(radius_m) <= 5000:
            raise ValueError("radius_m must be 1..5000")
        if scene_type not in {"social", "exploration", "travel", "ritual", "combat", "other"}:
            raise ValueError("invalid scene_type")
        with self.e._write_db() as db:
            loc = db.execute("SELECT id FROM locations WHERE campaign_id=? AND id=?", (campaign_id, location_id)).fetchone()
            if not loc:
                raise KeyError(f"unknown location: {location_id}")
            existing = db.execute("SELECT id FROM scenes WHERE campaign_id=? AND status='active'", (campaign_id,)).fetchone()
            if existing and existing["id"] != scene_id:
                raise ValueError(f"active scene already exists: {existing['id']}")
            world_time = db.execute("SELECT world_time FROM campaigns WHERE id=?", (campaign_id,)).fetchone()["world_time"]
            now = self.e._now()
            db.execute(
                """INSERT INTO scenes(campaign_id,id,location_id,scene_type,status,radius_m,state_json,created_world_time,updated_world_time,created_at,updated_at)
                   VALUES(?,?,?,?, 'active', ?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET location_id=excluded.location_id,scene_type=excluded.scene_type,status='active',radius_m=excluded.radius_m,state_json=excluded.state_json,updated_world_time=excluded.updated_world_time,updated_at=excluded.updated_at""",
                (campaign_id, scene_id, location_id, scene_type, float(radius_m), self.e._dumps(state or {}), world_time, world_time, now, now),
            )

            requested = list(entities)
            if not requested:
                chars = db.execute(
                    "SELECT id FROM characters WHERE campaign_id=? AND location=? AND status='alive' ORDER BY name,id",
                    (campaign_id, location_id),
                ).fetchall()
                npcs = db.execute(
                    "SELECT id FROM npcs WHERE campaign_id=? AND location=? AND status='alive' AND hp>0 ORDER BY name,id",
                    (campaign_id, location_id),
                ).fetchall()
                requested = ([{"kind": "character", "id": r["id"]} for r in chars] +
                             [{"kind": "npc", "id": r["id"]} for r in npcs])
            selected = requested[: self.MAX_SCENE_ENTITIES]
            db.execute("DELETE FROM scene_entities WHERE campaign_id=? AND scene_id=?", (campaign_id, scene_id))
            inserted_count = 0
            for idx, entity in enumerate(selected):
                kind = str(entity.get("kind", "npc"))
                actor_id = self.e._clean_id(str(entity.get("id", "")))
                if kind not in {"character", "npc"}:
                    raise ValueError("scene entity kind must be character or npc")
                info = self._actor_name_status(db, campaign_id, kind, actor_id)
                if not info or info[1] != "alive":
                    continue
                # deterministic loose staging: 4 columns, 2 m spacing.
                x = float(entity.get("x", (idx % 4) * 2.0))
                y = float(entity.get("y", (idx // 4) * 2.0))
                z = float(entity.get("z", 0.0))
                zone = str(entity.get("zone", "center"))[:80]
                stance = str(entity.get("stance", "neutral"))[:80]
                db.execute(
                    """INSERT INTO scene_entities(campaign_id,scene_id,actor_kind,actor_id,x,y,z,zone,stance,state_json,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (campaign_id, scene_id, kind, actor_id, x, y, z, zone, stance, self.e._dumps(entity.get("state") or {}), now),
                )
                inserted_count += 1
            db.execute("DELETE FROM scene_features WHERE campaign_id=? AND scene_id=?", (campaign_id, scene_id))
            for idx, feature in enumerate(list(features)[:64]):
                fid = self.e._clean_id(str(feature.get("id") or f"feature_{idx+1}"))
                db.execute(
                    """INSERT INTO scene_features(campaign_id,scene_id,id,kind,x,y,z,blocks_los,difficult,persistent,state_json,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (campaign_id, scene_id, fid, str(feature.get("kind", "feature"))[:80], float(feature.get("x", 0)), float(feature.get("y", 0)), float(feature.get("z", 0)), int(bool(feature.get("blocks_los", False))), int(bool(feature.get("difficult", False))), int(bool(feature.get("persistent", False))), self.e._dumps(feature.get("state") or {}), now),
                )
            rev = self.e._next_revision(db, campaign_id)
            self.e._insert_event(db, campaign_id, rev, "scene_start", f"Scene {scene_id} started at {location_id}.", region=location_id, payload={"scene_id": scene_id, "scene_type": scene_type, "entity_count": inserted_count, "requested_entity_count": len(requested), "truncated": len(requested) > self.MAX_SCENE_ENTITIES})
        return self.get_scene(campaign_id, scene_id)

    def get_scene(self, campaign_id: str, scene_id: str | None = None) -> dict[str, Any] | None:
        with self.e._db() as db:
            if scene_id:
                row = db.execute("SELECT * FROM scenes WHERE campaign_id=? AND id=?", (campaign_id, scene_id)).fetchone()
            else:
                row = db.execute("SELECT * FROM scenes WHERE campaign_id=? AND status='active' ORDER BY updated_at DESC LIMIT 1", (campaign_id,)).fetchone()
            if not row:
                return None
            entities = db.execute("SELECT * FROM scene_entities WHERE campaign_id=? AND scene_id=? ORDER BY actor_kind,actor_id", (campaign_id, row["id"])).fetchall()
            features = db.execute("SELECT * FROM scene_features WHERE campaign_id=? AND scene_id=? ORDER BY id", (campaign_id, row["id"])).fetchall()
        data = dict(row)
        data["state"] = self.e._loads(data.pop("state_json"))
        data["entities"] = []
        for r in entities:
            x = dict(r); x["state"] = self.e._loads(x.pop("state_json")); data["entities"].append(x)
        data["features"] = []
        for r in features:
            x = dict(r); x["blocks_los"] = bool(x["blocks_los"]); x["difficult"] = bool(x["difficult"]); x["persistent"] = bool(x["persistent"]); x["state"] = self.e._loads(x.pop("state_json")); data["features"].append(x)
        data["entity_limit"] = self.MAX_SCENE_ENTITIES
        return data

    def set_scene_entity(self, campaign_id: str, scene_id: str, actor_kind: str, actor_id: str, *, x: float, y: float, z: float = 0, zone: str = "center", stance: str = "neutral", state: dict[str, Any] | None = None) -> dict[str, Any]:
        if actor_kind not in {"character", "npc"}:
            raise ValueError("actor_kind must be character or npc")
        with self.e._write_db() as db:
            scene = db.execute("SELECT id FROM scenes WHERE campaign_id=? AND id=? AND status='active'", (campaign_id, scene_id)).fetchone()
            if not scene:
                raise KeyError(f"unknown active scene: {scene_id}")
            count = db.execute("SELECT COUNT(*) n FROM scene_entities WHERE campaign_id=? AND scene_id=?", (campaign_id, scene_id)).fetchone()["n"]
            existing = db.execute("SELECT 1 FROM scene_entities WHERE campaign_id=? AND scene_id=? AND actor_kind=? AND actor_id=?", (campaign_id, scene_id, actor_kind, actor_id)).fetchone()
            if not existing and int(count) >= self.MAX_SCENE_ENTITIES:
                raise ValueError(f"scene entity cap is {self.MAX_SCENE_ENTITIES}")
            info = self._actor_name_status(db, campaign_id, actor_kind, actor_id)
            if not info or info[1] != "alive":
                raise ValueError("scene actor must exist and be alive")
            db.execute(
                """INSERT INTO scene_entities(campaign_id,scene_id,actor_kind,actor_id,x,y,z,zone,stance,state_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,scene_id,actor_kind,actor_id) DO UPDATE SET x=excluded.x,y=excluded.y,z=excluded.z,zone=excluded.zone,stance=excluded.stance,state_json=excluded.state_json,updated_at=excluded.updated_at""",
                (campaign_id, scene_id, actor_kind, actor_id, float(x), float(y), float(z), zone[:80], stance[:80], self.e._dumps(state or {}), self.e._now()),
            )
        return self.get_scene(campaign_id, scene_id) or {}

    def set_scene_feature(self, campaign_id: str, scene_id: str, feature_id: str, *, kind: str = "feature", x: float = 0, y: float = 0, z: float = 0, blocks_los: bool = False, difficult: bool = False, persistent: bool = False, state: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.e._write_db() as db:
            scene = db.execute("SELECT id FROM scenes WHERE campaign_id=? AND id=? AND status='active'", (campaign_id, scene_id)).fetchone()
            if not scene:
                raise KeyError(f"unknown active scene: {scene_id}")
            db.execute(
                """INSERT INTO scene_features(campaign_id,scene_id,id,kind,x,y,z,blocks_los,difficult,persistent,state_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,scene_id,id) DO UPDATE SET kind=excluded.kind,x=excluded.x,y=excluded.y,z=excluded.z,blocks_los=excluded.blocks_los,difficult=excluded.difficult,persistent=excluded.persistent,state_json=excluded.state_json,updated_at=excluded.updated_at""",
                (campaign_id, scene_id, feature_id, kind[:80], float(x), float(y), float(z), int(bool(blocks_los)), int(bool(difficult)), int(bool(persistent)), self.e._dumps(state or {}), self.e._now()),
            )
        return self.get_scene(campaign_id, scene_id) or {}

    def end_scene(self, campaign_id: str, scene_id: str, *, foldback_state: dict[str, Any] | None = None, reason: str = "scene ended") -> dict[str, Any]:
        with self.e._write_db() as db:
            row = db.execute("SELECT * FROM scenes WHERE campaign_id=? AND id=? AND status='active'", (campaign_id, scene_id)).fetchone()
            if not row:
                raise KeyError(f"unknown active scene: {scene_id}")
            persistent_features = db.execute("SELECT * FROM scene_features WHERE campaign_id=? AND scene_id=? AND persistent=1 ORDER BY id", (campaign_id, scene_id)).fetchall()
            loc = db.execute("SELECT state_json FROM locations WHERE campaign_id=? AND id=?", (campaign_id, row["location_id"])).fetchone()
            loc_state = self.e._loads(loc["state_json"]) if loc else {}
            if foldback_state:
                loc_state.update(foldback_state)
            if persistent_features:
                current = list(loc_state.get("persistent_scene_features", []))
                for f in persistent_features:
                    current.append({"id": f["id"], "kind": f["kind"], "state": self.e._loads(f["state_json"]), "x": f["x"], "y": f["y"], "z": f["z"]})
                loc_state["persistent_scene_features"] = current[-100:]
            db.execute("UPDATE locations SET state_json=?,updated_at=? WHERE campaign_id=? AND id=?", (self.e._dumps(loc_state), self.e._now(), campaign_id, row["location_id"]))

            # Lazy materialisation foldback: generated NPCs that were never canon-locked
            # and never participated in a persistent event/relationship can be safely
            # demoted back into the location aggregate. Named detail that mattered in
            # play or simulation is retained automatically.
            dematerialized: list[str] = []
            try:
                npc_rows = db.execute(
                    """SELECT n.id FROM scene_entities se JOIN npcs n ON n.campaign_id=se.campaign_id AND n.id=se.actor_id
                       WHERE se.campaign_id=? AND se.scene_id=? AND se.actor_kind='npc' AND n.materialized=1 ORDER BY n.id""",
                    (campaign_id, scene_id),
                ).fetchall()
                for nr in npc_rows:
                    npc_id = str(nr["id"])
                    locked = db.execute("SELECT 1 FROM canon_locks WHERE campaign_id=? AND object_kind='npc' AND object_id=?", (campaign_id, npc_id)).fetchone()
                    used_event = db.execute("SELECT 1 FROM events WHERE campaign_id=? AND (actor_id=? OR target_id=?) LIMIT 1", (campaign_id, npc_id, npc_id)).fetchone()
                    used_rel = db.execute("SELECT 1 FROM relationships WHERE campaign_id=? AND (source_id=? OR target_id=?) LIMIT 1", (campaign_id, npc_id, npc_id)).fetchone()
                    if not locked and not used_event and not used_rel:
                        db.execute("DELETE FROM visual_profiles WHERE campaign_id=? AND entity_kind='npc' AND entity_id=?", (campaign_id, npc_id))
                        db.execute("DELETE FROM npcs WHERE campaign_id=? AND id=?", (campaign_id, npc_id))
                        dematerialized.append(npc_id)
            except sqlite3.OperationalError:
                dematerialized = []

            rev = self.e._next_revision(db, campaign_id)
            self.e._insert_event(db, campaign_id, rev, "scene_end", reason, region=row["location_id"], payload={"scene_id": scene_id, "folded_feature_count": len(persistent_features), "dematerialized_npcs": dematerialized})
            db.execute("DELETE FROM scenes WHERE campaign_id=? AND id=?", (campaign_id, scene_id))
        return {"campaign_id": campaign_id, "scene_id": scene_id, "ended": True, "folded_feature_count": len(persistent_features), "dematerialized_npcs": dematerialized}

    def save_director(
        self,
        campaign_id: str,
        director_id: str,
        name: str,
        *,
        director_kind: str = "civic",
        scope_type: str = "location",
        scope_id: str | None = None,
        source_kind: str = "npc",
        source_id: str,
        authority: float = 1.0,
        priority: int = 100,
        weights: dict[str, float] | None = None,
        policies: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        if director_kind not in {"civic", "realm", "faction", "divine", "power"}:
            raise ValueError("invalid director_kind")
        if scope_type not in {"location", "region", "realm", "scene", "global"}:
            raise ValueError("invalid scope_type")
        if source_kind not in {"npc", "faction", "faction_leader", "deity", "power"}:
            raise ValueError("invalid source_kind")
        if not 0 <= float(authority) <= 1:
            raise ValueError("authority must be 0..1")
        clean_weights: dict[str, float] = {}
        for key, value in (weights or {}).items():
            f = float(value)
            if not 0 <= f <= 5:
                raise ValueError("director weights must be 0..5")
            clean_weights[str(key)[:80]] = f
        with self.e._write_db() as db:
            db.execute(
                """INSERT INTO directors(campaign_id,id,name,director_kind,scope_type,scope_id,source_kind,source_id,authority,priority,weights_json,policies_json,enabled,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET name=excluded.name,director_kind=excluded.director_kind,scope_type=excluded.scope_type,scope_id=excluded.scope_id,source_kind=excluded.source_kind,source_id=excluded.source_id,authority=excluded.authority,priority=excluded.priority,weights_json=excluded.weights_json,policies_json=excluded.policies_json,enabled=excluded.enabled,updated_at=excluded.updated_at""",
                (campaign_id, director_id, name[:200], director_kind, scope_type, scope_id, source_kind, source_id, float(authority), int(priority), self.e._dumps(clean_weights), self.e._dumps(policies or {}), int(bool(enabled)), self.e._now()),
            )
        return self.get_director(campaign_id, director_id)

    def get_director(self, campaign_id: str, director_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            row = db.execute("SELECT * FROM directors WHERE campaign_id=? AND id=?", (campaign_id, director_id)).fetchone()
        if not row:
            raise KeyError(f"unknown director: {director_id}")
        data = dict(row); data["weights"] = self.e._loads(data.pop("weights_json")); data["policies"] = self.e._loads(data.pop("policies_json")); data["enabled"] = bool(data["enabled"])
        return data

    @staticmethod
    def _scope_matches(row: sqlite3.Row, location: sqlite3.Row | None, scene_id: str | None) -> bool:
        st, sid = str(row["scope_type"]), row["scope_id"]
        if st == "global":
            return True
        if st == "scene":
            return bool(scene_id and sid == scene_id)
        if not location:
            return False
        if st == "location":
            return sid == location["id"]
        if st == "region":
            return sid == location["region"]
        if st == "realm":
            return sid == location["realm_id"]
        return False

    @staticmethod
    def _source_alive(db: sqlite3.Connection, campaign_id: str, row: sqlite3.Row) -> tuple[bool, str | None, str | None]:
        sk, sid = str(row["source_kind"]), str(row["source_id"])
        if sk == "npc":
            r = db.execute("SELECT name,status,hp FROM npcs WHERE campaign_id=? AND id=?", (campaign_id, sid)).fetchone()
            return (bool(r and r["status"] == "alive" and int(r["hp"]) > 0), sid, str(r["name"]) if r else None)
        if sk == "faction_leader":
            f = db.execute("SELECT leader_id,name FROM factions WHERE campaign_id=? AND id=?", (campaign_id, sid)).fetchone()
            if not f or not f["leader_id"]:
                return False, None, None
            n = db.execute("SELECT name,status,hp FROM npcs WHERE campaign_id=? AND id=?", (campaign_id, f["leader_id"])).fetchone()
            return (bool(n and n["status"] == "alive" and int(n["hp"]) > 0), str(f["leader_id"]), str(n["name"]) if n else None)
        if sk == "faction":
            f = db.execute("SELECT name FROM factions WHERE campaign_id=? AND id=?", (campaign_id, sid)).fetchone()
            return bool(f), sid if f else None, str(f["name"]) if f else None
        # deity/power are canonical named authorities, not mortal rows.
        return True, sid, sid

    def active_directors_db(self, db: sqlite3.Connection, campaign_id: str, location_id: str | None, scene_id: str | None = None) -> dict[str, Any]:
        location = None
        if location_id:
            location = db.execute("SELECT id,region,realm_id FROM locations WHERE campaign_id=? AND id=?", (campaign_id, location_id)).fetchone()
        rows = db.execute("SELECT * FROM directors WHERE campaign_id=? AND enabled=1 ORDER BY priority,authority DESC,id", (campaign_id,)).fetchall()
        stack = []
        multipliers: dict[str, float] = {}
        policies: dict[str, Any] = {}
        for row in rows:
            if not self._scope_matches(row, location, scene_id):
                continue
            alive, resolved_id, resolved_name = self._source_alive(db, campaign_id, row)
            if not alive:
                continue
            weights = self.e._loads(row["weights_json"])
            row_policies = self.e._loads(row["policies_json"])
            authority = float(row["authority"])
            for domain, weight in sorted(weights.items()):
                local = 1.0 + authority * (float(weight) - 1.0)
                multipliers[domain] = max(0.1, min(5.0, multipliers.get(domain, 1.0) * local))
            for key, value in row_policies.items():
                if key not in policies:  # highest priority wins conflicts
                    policies[key] = value
            stack.append({
                "id": row["id"], "name": row["name"], "director_kind": row["director_kind"],
                "scope_type": row["scope_type"], "scope_id": row["scope_id"],
                "source_kind": row["source_kind"], "source_id": row["source_id"],
                "resolved_source_id": resolved_id, "resolved_source_name": resolved_name,
                "authority": authority, "priority": int(row["priority"]), "weights": weights, "policies": row_policies,
            })
        return {"stack": stack, "event_multipliers": multipliers, "policies": policies}

    def active_directors(self, campaign_id: str, location_id: str | None, scene_id: str | None = None) -> dict[str, Any]:
        with self.e._db() as db:
            return self.active_directors_db(db, campaign_id, location_id, scene_id)

    def event_multiplier(self, db: sqlite3.Connection, campaign_id: str, role: str, location_id: str | None, scene_id: str | None = None) -> float:
        data = self.active_directors_db(db, campaign_id, location_id, scene_id)
        return float(data["event_multipliers"].get(role, 1.0))

    def save_ownership(self, campaign_id: str, asset_kind: str, asset_id: str, owner_kind: str, owner_id: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if owner_kind not in {"character", "npc", "faction", "location"}:
            raise ValueError("invalid owner_kind")
        with self.e._write_db() as db:
            world_time = db.execute("SELECT world_time FROM campaigns WHERE id=?", (campaign_id,)).fetchone()["world_time"]
            db.execute(
                """INSERT INTO ownership(campaign_id,asset_kind,asset_id,owner_kind,owner_id,since,metadata_json,updated_at) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,asset_kind,asset_id) DO UPDATE SET owner_kind=excluded.owner_kind,owner_id=excluded.owner_id,since=excluded.since,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                (campaign_id, asset_kind, asset_id, owner_kind, owner_id, world_time, self.e._dumps(metadata or {}), self.e._now()),
            )
        return self.get_ownership(campaign_id, asset_kind, asset_id)

    def get_ownership(self, campaign_id: str, asset_kind: str, asset_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            row = db.execute("SELECT * FROM ownership WHERE campaign_id=? AND asset_kind=? AND asset_id=?", (campaign_id, asset_kind, asset_id)).fetchone()
        if not row:
            raise KeyError(f"unknown ownership: {asset_kind}/{asset_id}")
        data = dict(row); data["metadata"] = self.e._loads(data.pop("metadata_json")); return data
