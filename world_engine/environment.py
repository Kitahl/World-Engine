from __future__ import annotations

import hashlib
import math
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

from .rules import RulesKernel
from .world_systems import WorldSystemsKernel

if TYPE_CHECKING:
    from .engine import WorldEngine


ENVIRONMENT_SCHEMA = r'''
CREATE TABLE IF NOT EXISTS environment_materials (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    flammability REAL NOT NULL DEFAULT 0 CHECK(flammability BETWEEN 0 AND 1),
    fuel_capacity REAL NOT NULL DEFAULT 0 CHECK(fuel_capacity >= 0),
    absorbency REAL NOT NULL DEFAULT 0 CHECK(absorbency BETWEEN 0 AND 1),
    permeability REAL NOT NULL DEFAULT 0 CHECK(permeability BETWEEN 0 AND 1),
    hardness REAL NOT NULL DEFAULT 0.5 CHECK(hardness BETWEEN 0 AND 1),
    conductivity REAL NOT NULL DEFAULT 0 CHECK(conductivity BETWEEN 0 AND 1),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS environment_targets (
    campaign_id TEXT NOT NULL,
    target_key TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    location_id TEXT,
    map_id TEXT,
    x INTEGER,
    y INTEGER,
    z INTEGER,
    material_id TEXT,
    properties_json TEXT NOT NULL DEFAULT '{}',
    state_json TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,target_key),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_environment_targets_location
    ON environment_targets(campaign_id,location_id,active,target_key);
CREATE INDEX IF NOT EXISTS idx_environment_targets_map
    ON environment_targets(campaign_id,map_id,z,y,x,active);

CREATE TABLE IF NOT EXISTS environment_effects (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    effect_type TEXT NOT NULL,
    target_key TEXT NOT NULL,
    source_key TEXT,
    intensity REAL NOT NULL DEFAULT 0 CHECK(intensity BETWEEN 0 AND 1),
    amount REAL NOT NULL DEFAULT 0 CHECK(amount >= 0),
    state_json TEXT NOT NULL DEFAULT '{}',
    created_world_time TEXT NOT NULL,
    last_world_time TEXT NOT NULL,
    expires_world_time TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,target_key) REFERENCES environment_targets(campaign_id,target_key) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_environment_effects_active
    ON environment_effects(campaign_id,active,effect_type,target_key);

CREATE TABLE IF NOT EXISTS environment_weather (
    campaign_id TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    condition TEXT NOT NULL DEFAULT 'clear',
    precipitation TEXT NOT NULL DEFAULT 'none',
    precipitation_intensity REAL NOT NULL DEFAULT 0 CHECK(precipitation_intensity BETWEEN 0 AND 1),
    temperature_c REAL NOT NULL DEFAULT 15,
    wind_speed REAL NOT NULL DEFAULT 0 CHECK(wind_speed >= 0),
    wind_direction TEXT NOT NULL DEFAULT 'N',
    humidity REAL NOT NULL DEFAULT 0.5 CHECK(humidity BETWEEN 0 AND 1),
    visibility REAL NOT NULL DEFAULT 1 CHECK(visibility BETWEEN 0 AND 1),
    severity REAL NOT NULL DEFAULT 0 CHECK(severity BETWEEN 0 AND 1),
    generated_world_time TEXT NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,scope_type,scope_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS environment_disaster_config (
    campaign_id TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    profile_json TEXT NOT NULL DEFAULT '{}',
    activated_ordinal INTEGER NOT NULL DEFAULT 0,
    last_checked_ordinal INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,scope_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS environment_disaster_counters (
    campaign_id TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    tier INTEGER NOT NULL CHECK(tier BETWEEN 1 AND 5),
    last_event_ordinal INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,scope_id,tier),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
'''


EFFECT_TYPES = {
    "fire", "smoke", "water", "heat", "cold", "gas", "blight",
    "corrosion", "ice", "snow", "mud", "darkness", "corruption", "disease", "electricity", "explosion", "drought",
}

DEFAULT_MATERIALS: dict[str, dict[str, Any]] = {
    "wood": {"name": "Wood", "flammability": 0.88, "fuel_capacity": 100, "absorbency": 0.55, "permeability": 0.25, "hardness": 0.35, "conductivity": 0.12},
    "cloth": {"name": "Cloth", "flammability": 0.96, "fuel_capacity": 35, "absorbency": 0.82, "permeability": 0.65, "hardness": 0.05, "conductivity": 0.05},
    "oil": {"name": "Oil", "flammability": 1.0, "fuel_capacity": 180, "absorbency": 0.0, "permeability": 0.95, "hardness": 0.0, "conductivity": 0.02},
    "vegetation": {"name": "Vegetation", "flammability": 0.76, "fuel_capacity": 65, "absorbency": 0.72, "permeability": 0.72, "hardness": 0.10, "conductivity": 0.08},
    "stone": {"name": "Stone", "flammability": 0.0, "fuel_capacity": 0, "absorbency": 0.12, "permeability": 0.08, "hardness": 0.92, "conductivity": 0.45},
    "earth": {"name": "Earth", "flammability": 0.03, "fuel_capacity": 3, "absorbency": 0.78, "permeability": 0.72, "hardness": 0.38, "conductivity": 0.20},
    "metal": {"name": "Metal", "flammability": 0.0, "fuel_capacity": 0, "absorbency": 0.0, "permeability": 0.0, "hardness": 0.95, "conductivity": 0.95},
    "glass": {"name": "Glass", "flammability": 0.0, "fuel_capacity": 0, "absorbency": 0.0, "permeability": 0.0, "hardness": 0.55, "conductivity": 0.35},
    "ice": {"name": "Ice", "flammability": 0.0, "fuel_capacity": 0, "absorbency": 0.0, "permeability": 0.02, "hardness": 0.25, "conductivity": 0.50},
    "water": {"name": "Water", "flammability": 0.0, "fuel_capacity": 0, "absorbency": 0.0, "permeability": 1.0, "hardness": 0.0, "conductivity": 0.70},
    "flesh": {"name": "Living tissue", "flammability": 0.08, "fuel_capacity": 18, "absorbency": 0.60, "permeability": 0.30, "hardness": 0.10, "conductivity": 0.55},
}

CLIMATE_WEIGHTS: dict[str, dict[str, float]] = {
    "temperate": {"clear": 4, "cloudy": 3, "rain": 2.2, "storm": 0.45, "fog": 0.65, "wind": 0.8, "snow": 0.25},
    "coastal": {"clear": 2.8, "cloudy": 3.6, "rain": 3.0, "storm": 0.7, "fog": 1.3, "wind": 1.3, "snow": 0.12},
    "arid": {"clear": 8.5, "cloudy": 0.8, "rain": 0.25, "storm": 0.12, "fog": 0.08, "wind": 1.5, "heatwave": 0.8},
    "tropical": {"clear": 3.0, "cloudy": 2.6, "rain": 3.7, "storm": 1.2, "fog": 0.55, "wind": 0.65, "heatwave": 0.35},
    "arctic": {"clear": 3.0, "cloudy": 2.2, "snow": 4.2, "storm": 0.55, "fog": 0.45, "wind": 1.6, "cold_snap": 0.9},
    "alpine": {"clear": 2.8, "cloudy": 2.5, "rain": 1.2, "snow": 2.6, "storm": 0.65, "fog": 0.7, "wind": 1.4, "cold_snap": 0.45},
}

BASE_TEMPERATURE = {"arctic": -8.0, "alpine": 3.0, "temperate": 12.0, "coastal": 14.0, "tropical": 26.0, "arid": 24.0, "continental": 10.0, "mediterranean": 18.0}
SEASON_TEMP = {"spring": 2.0, "summer": 8.0, "autumn": 0.0, "fall": 0.0, "winter": -8.0}
WEATHER_TEMP = {"clear": 2.0, "cloudy": 0.0, "rain": -2.0, "storm": -3.0, "snow": -5.0, "fog": -1.0, "wind": -1.0, "heatwave": 9.0, "cold_snap": -10.0}
WEATHER_META: dict[str, tuple[str, float, float, float, float]] = {
    # precipitation, precip_intensity, wind_speed, humidity, visibility
    "clear": ("none", 0.0, 6.0, 0.42, 1.0),
    "cloudy": ("none", 0.0, 8.0, 0.58, 0.92),
    "rain": ("rain", 0.55, 12.0, 0.86, 0.72),
    "storm": ("rain", 0.92, 34.0, 0.94, 0.42),
    "snow": ("snow", 0.65, 15.0, 0.78, 0.58),
    "fog": ("none", 0.0, 3.0, 0.96, 0.24),
    "wind": ("none", 0.0, 36.0, 0.38, 0.82),
    "heatwave": ("none", 0.0, 8.0, 0.22, 0.92),
    "cold_snap": ("none", 0.0, 14.0, 0.45, 0.88),
}
DISASTER_MAX_DAYS = {1: 7, 2: 30, 3: 365, 4: 3650, 5: 36500}
DISASTER_DEFAULTS = {1: "minor_storm", 2: "flood", 3: "wildfire", 4: "earthquake", 5: "cataclysm"}
WIND_DIRS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


class EnvironmentKernel:
    """Sparse, event-driven environmental consequence layer for World Engine 4.5.

    WORLD state stays aggregate. Detailed propagation is limited to registered
    targets (normally active scene/map objects), so the engine gets persistent
    physical consequences without simulating every tile everywhere.
    """

    def __init__(self, engine: "WorldEngine"):
        self.e = engine

    # ------------------------------------------------------------------
    # setup / material + target registry
    # ------------------------------------------------------------------

    def seed_defaults_db(self, db: sqlite3.Connection, campaign_id: str) -> None:
        now = self.e._now()
        for material_id, spec in DEFAULT_MATERIALS.items():
            db.execute(
                """INSERT INTO environment_materials(
                       campaign_id,id,name,flammability,fuel_capacity,absorbency,permeability,hardness,conductivity,metadata_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,'{}',?)
                   ON CONFLICT(campaign_id,id) DO NOTHING""",
                (campaign_id, material_id, spec["name"], spec["flammability"], spec["fuel_capacity"], spec["absorbency"], spec["permeability"], spec["hardness"], spec["conductivity"], now),
            )

    def save_material(self, campaign_id: str, material_id: str, name: str, **spec: Any) -> dict[str, Any]:
        campaign_id, material_id = self.e._clean_id(campaign_id), self.e._clean_id(material_id)
        self.e._ensure_campaign_exists(campaign_id)
        values = {
            "flammability": self._unit(spec.get("flammability", 0)),
            "fuel_capacity": max(0.0, float(spec.get("fuel_capacity", 0))),
            "absorbency": self._unit(spec.get("absorbency", 0)),
            "permeability": self._unit(spec.get("permeability", 0)),
            "hardness": self._unit(spec.get("hardness", 0.5)),
            "conductivity": self._unit(spec.get("conductivity", 0)),
        }
        with self.e._write_db() as db:
            db.execute(
                """INSERT INTO environment_materials(campaign_id,id,name,flammability,fuel_capacity,absorbency,permeability,hardness,conductivity,metadata_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET name=excluded.name,flammability=excluded.flammability,
                   fuel_capacity=excluded.fuel_capacity,absorbency=excluded.absorbency,permeability=excluded.permeability,
                   hardness=excluded.hardness,conductivity=excluded.conductivity,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                (campaign_id, material_id, name[:160], values["flammability"], values["fuel_capacity"], values["absorbency"], values["permeability"], values["hardness"], values["conductivity"], self.e._dumps(spec.get("metadata") or {}), self.e._now()),
            )
        return self.get_material(campaign_id, material_id)

    def get_material(self, campaign_id: str, material_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            row = db.execute("SELECT * FROM environment_materials WHERE campaign_id=? AND id=?", (campaign_id, material_id)).fetchone()
        if not row:
            raise KeyError(f"unknown environment material: {material_id}")
        out = dict(row); out["metadata"] = self.e._loads(out.pop("metadata_json")); return out

    @staticmethod
    def _unit(value: Any) -> float:
        return max(0.0, min(1.0, float(value)))

    @staticmethod
    def _target_key(target_type: str, target_id: str, map_id: str | None = None, x: int | None = None, y: int | None = None, z: int | None = None) -> str:
        if target_type == "tile":
            if map_id is None or x is None or y is None:
                raise ValueError("tile target requires map_id, x and y")
            return f"tile:{map_id}:{int(x)}:{int(y)}:{int(z or 0)}"
        return f"{target_type}:{target_id}"

    def _infer_material(self, text: str) -> str:
        value = str(text or "").lower()
        if any(x in value for x in ("wood", "timber", "beam", "door", "tree", "log", "plank")): return "wood"
        if any(x in value for x in ("cloth", "curtain", "rug", "canvas", "hay", "straw", "paper", "parchment")): return "cloth" if "hay" not in value and "straw" not in value else "vegetation"
        if any(x in value for x in ("grass", "brush", "bush", "vine", "forest", "plant")): return "vegetation"
        if any(x in value for x in ("stone", "rock", "brick", "masonry", "wall")): return "stone"
        if any(x in value for x in ("iron", "steel", "metal", "copper", "bronze")): return "metal"
        if "glass" in value: return "glass"
        if "ice" in value: return "ice"
        if any(x in value for x in ("water", "river", "pond", "lake")): return "water"
        return "earth"

    def _bind_target_db(self, db: sqlite3.Connection, campaign_id: str, target: dict[str, Any]) -> sqlite3.Row:
        ttype = str(target.get("type") or target.get("target_type") or "").strip().lower()
        if ttype not in {"tile", "location", "scene_feature", "actor", "zone"}:
            raise ValueError("environment target type must be tile, location, scene_feature, actor, or zone")
        map_id = target.get("map_id")
        x = int(target["x"]) if target.get("x") is not None else None
        y = int(target["y"]) if target.get("y") is not None else None
        z = int(target.get("z", 0)) if ttype == "tile" or target.get("z") is not None else None
        location_id = target.get("location_id")
        material_id = target.get("material_id")
        properties = dict(target.get("properties") or {})
        state = dict(target.get("state") or {})

        if ttype == "tile":
            if not map_id:
                raise ValueError("tile target requires map_id")
            row = db.execute("SELECT * FROM spatial_tiles WHERE campaign_id=? AND map_id=? AND x=? AND y=? AND z=?", (campaign_id, map_id, x, y, z)).fetchone()
            if not row:
                raise KeyError(f"unknown spatial tile: {map_id}/{x}/{y}/{z}")
            map_row = db.execute("SELECT scope_type,scope_id FROM spatial_maps WHERE campaign_id=? AND id=?", (campaign_id, map_id)).fetchone()
            if not location_id and map_row and map_row["scope_type"] == "location":
                location_id = map_row["scope_id"]
            tile_state = self.e._loads(row["state_json"] or "{}")
            material_id = material_id or tile_state.get("material_id") or self._infer_material(str(row["terrain"]))
            properties.setdefault("base_move_cost", float(row["move_cost"]))
            if row["terrain_hp"] is not None:
                properties.setdefault("structural_hp_max", float(tile_state.get("_max_terrain_hp", row["terrain_hp"])))
            target_id = f"{map_id}:{x}:{y}:{z}"
        elif ttype == "location":
            target_id = str(target.get("id") or target.get("target_id") or location_id or "")
            if not target_id:
                raise ValueError("location target requires id")
            if not db.execute("SELECT 1 FROM locations WHERE campaign_id=? AND id=?", (campaign_id, target_id)).fetchone():
                raise KeyError(f"unknown location: {target_id}")
            location_id = target_id
            material_id = material_id or "earth"
        elif ttype == "scene_feature":
            scene_id = str(target.get("scene_id") or "")
            feature_id = str(target.get("id") or target.get("target_id") or "")
            row = db.execute("SELECT sf.*,s.location_id FROM scene_features sf JOIN scenes s ON s.campaign_id=sf.campaign_id AND s.id=sf.scene_id WHERE sf.campaign_id=? AND sf.scene_id=? AND sf.id=?", (campaign_id, scene_id, feature_id)).fetchone()
            if not row:
                raise KeyError(f"unknown scene feature: {scene_id}/{feature_id}")
            target_id = f"{scene_id}:{feature_id}"; location_id = location_id or row["location_id"]
            material_id = material_id or self._infer_material(str(row["kind"]))
            properties.setdefault("scene_id", scene_id)
            properties.setdefault("feature_id", feature_id)
        elif ttype == "actor":
            actor_kind = str(target.get("actor_kind") or "character")
            actor_id = str(target.get("actor_id") or target.get("id") or target.get("target_id") or "")
            actor = self.e._get_actor_db(db, campaign_id, actor_kind, actor_id)
            target_id = f"{actor_kind}:{actor_id}"; location_id = location_id or actor["location"]
            material_id = material_id or "flesh"; properties.setdefault("actor_kind", actor_kind); properties.setdefault("actor_id", actor_id)
        else:  # zone
            target_id = str(target.get("id") or target.get("target_id") or "")
            if not target_id:
                raise ValueError("zone target requires id")
            material_id = material_id or "earth"

        key = self._target_key(ttype, target_id, str(map_id) if map_id else None, x, y, z)
        existing = db.execute("SELECT * FROM environment_targets WHERE campaign_id=? AND target_key=?", (campaign_id, key)).fetchone()
        if existing:
            old_props = self.e._loads(existing["properties_json"] or "{}"); old_props.update(properties); properties = old_props
            old_state = self.e._loads(existing["state_json"] or "{}"); old_state.update(state); state = old_state
        material_id = str(material_id or "earth")
        self.seed_defaults_db(db, campaign_id)
        if not db.execute("SELECT 1 FROM environment_materials WHERE campaign_id=? AND id=?", (campaign_id, material_id)).fetchone():
            raise KeyError(f"unknown environment material: {material_id}")
        db.execute(
            """INSERT INTO environment_targets(campaign_id,target_key,target_type,target_id,location_id,map_id,x,y,z,material_id,properties_json,state_json,active,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?)
               ON CONFLICT(campaign_id,target_key) DO UPDATE SET target_type=excluded.target_type,target_id=excluded.target_id,
               location_id=COALESCE(excluded.location_id,environment_targets.location_id),map_id=COALESCE(excluded.map_id,environment_targets.map_id),
               x=COALESCE(excluded.x,environment_targets.x),y=COALESCE(excluded.y,environment_targets.y),z=COALESCE(excluded.z,environment_targets.z),
               material_id=excluded.material_id,properties_json=excluded.properties_json,state_json=excluded.state_json,active=1,updated_at=excluded.updated_at""",
            (campaign_id, key, ttype, target_id, location_id, map_id, x, y, z, material_id, self.e._dumps(properties), self.e._dumps(state), self.e._now()),
        )
        return db.execute("SELECT * FROM environment_targets WHERE campaign_id=? AND target_key=?", (campaign_id, key)).fetchone()

    def bind_target(self, campaign_id: str, target: dict[str, Any]) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        with self.e._write_db() as db:
            row = self._bind_target_db(db, campaign_id, target)
        return self._decode_target(row)

    def _decode_target(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        out = dict(row); out["properties"] = self.e._loads(out.pop("properties_json")); out["state"] = self.e._loads(out.pop("state_json")); out["active"] = bool(out["active"]); return out

    def _decode_effect(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        out = dict(row); out["state"] = self.e._loads(out.pop("state_json")); out["active"] = bool(out["active"]); return out

    def set_properties(self, campaign_id: str, target: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
        with self.e._write_db() as db:
            row = self._bind_target_db(db, campaign_id, target); props = self.e._loads(row["properties_json"] or "{}"); props.update(properties)
            self._normalize_properties(props)
            db.execute("UPDATE environment_targets SET properties_json=?,updated_at=? WHERE campaign_id=? AND target_key=?", (self.e._dumps(props), self.e._now(), campaign_id, row["target_key"]))
            row = db.execute("SELECT * FROM environment_targets WHERE campaign_id=? AND target_key=?", (campaign_id, row["target_key"])).fetchone()
        return self._decode_target(row)

    @staticmethod
    def _normalize_properties(props: dict[str, Any]) -> None:
        for key in ("wetness", "humidity", "contamination"):
            if key in props: props[key] = max(0.0, min(1.0, float(props[key])))
        for key in ("fuel", "snow_depth", "water_level"):
            if key in props: props[key] = max(0.0, float(props[key]))
        if "temperature_c" in props: props["temperature_c"] = max(-100.0, min(500.0, float(props["temperature_c"])))

    # ------------------------------------------------------------------
    # effects + player interaction
    # ------------------------------------------------------------------

    def _effect_id(self, effect_type: str, target_key: str) -> str:
        digest = hashlib.sha1(target_key.encode("utf-8")).hexdigest()[:16]
        return f"{effect_type}:{digest}"

    def _apply_effect_db(self, db: sqlite3.Connection, campaign_id: str, effect_type: str, target_row: sqlite3.Row, *, intensity: float = 0.5, amount: float = 0.0, source_key: str | None = None, state: dict[str, Any] | None = None, world_time: str | None = None) -> sqlite3.Row:
        effect_type = str(effect_type).lower()
        if effect_type not in EFFECT_TYPES: raise ValueError(f"unsupported environmental effect: {effect_type}")
        intensity = self._unit(intensity); amount = max(0.0, float(amount)); world_time = world_time or db.execute("SELECT world_time FROM campaigns WHERE id=?", (campaign_id,)).fetchone()["world_time"]
        effect_id = self._effect_id(effect_type, target_row["target_key"])
        deferred=getattr(self,"_deferred_effects",None)
        if deferred is not None:
            deferred.append({
                "campaign_id":campaign_id,"effect_type":effect_type,"target_key":target_row["target_key"],
                "intensity":intensity,"amount":amount,"source_key":source_key,"state":dict(state or {}),"world_time":world_time,
            })
            return db.execute("SELECT * FROM environment_effects WHERE campaign_id=? AND id=?",(campaign_id,effect_id)).fetchone() or target_row
        old = db.execute("SELECT * FROM environment_effects WHERE campaign_id=? AND id=?", (campaign_id, effect_id)).fetchone()
        merged_state = self.e._loads(old["state_json"] or "{}") if old else {}; merged_state.update(state or {})
        new_intensity = max(intensity, float(old["intensity"]) if old and old["active"] else 0.0)
        new_amount = amount + (float(old["amount"]) if old and old["active"] else 0.0)
        db.execute(
            """INSERT INTO environment_effects(campaign_id,id,effect_type,target_key,source_key,intensity,amount,state_json,created_world_time,last_world_time,expires_world_time,active,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,NULL,1,?)
               ON CONFLICT(campaign_id,id) DO UPDATE SET source_key=COALESCE(excluded.source_key,environment_effects.source_key),
               intensity=excluded.intensity,amount=excluded.amount,state_json=excluded.state_json,last_world_time=excluded.last_world_time,active=1,updated_at=excluded.updated_at""",
            (campaign_id, effect_id, effect_type, target_row["target_key"], source_key, new_intensity, new_amount, self.e._dumps(merged_state), world_time, world_time, self.e._now()),
        )
        return db.execute("SELECT * FROM environment_effects WHERE campaign_id=? AND id=?", (campaign_id, effect_id)).fetchone()

    def apply_effect(self, campaign_id: str, effect_type: str, target: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        with self.e._write_db() as db:
            target_row = self._bind_target_db(db, campaign_id, target); row = self._apply_effect_db(db, campaign_id, effect_type, target_row, **kwargs)
            rev = self.e._next_revision(db, campaign_id)
            self.e._insert_event(db, campaign_id, rev, "environment_effect_applied", f"Environmental effect applied: {effect_type}", region=target_row["location_id"], payload={"effect_type": effect_type, "target_key": target_row["target_key"], "intensity": float(row["intensity"])})
        return self._decode_effect(row)

    def clear_effect(self, campaign_id: str, effect_type: str, target: dict[str, Any], *, amount: float = 1.0, reason: str = "environmental effect cleared") -> dict[str, Any]:
        with self.e._write_db() as db:
            target_row = self._bind_target_db(db, campaign_id, target); eid = self._effect_id(str(effect_type).lower(), target_row["target_key"])
            row = db.execute("SELECT * FROM environment_effects WHERE campaign_id=? AND id=?", (campaign_id, eid)).fetchone()
            if not row: return {"campaign_id": campaign_id, "effect_type": effect_type, "target_key": target_row["target_key"], "active": False}
            intensity = max(0.0, float(row["intensity"]) - max(0.0, float(amount))); active = intensity > 0.01
            db.execute("UPDATE environment_effects SET intensity=?,active=?,updated_at=? WHERE campaign_id=? AND id=?", (intensity, int(active), self.e._now(), campaign_id, eid))
            rev = self.e._next_revision(db, campaign_id); self.e._insert_event(db, campaign_id, rev, "environment_effect_cleared", reason, region=target_row["location_id"], payload={"effect_type": effect_type, "target_key": target_row["target_key"], "remaining_intensity": intensity})
        return {"campaign_id": campaign_id, "effect_type": effect_type, "target_key": target_row["target_key"], "intensity": intensity, "active": active}

    def _source_strength_db(self, db: sqlite3.Connection, campaign_id: str, actor_kind: str | None, actor_id: str | None, source: dict[str, Any] | None) -> tuple[float, str]:
        source = dict(source or {})
        kind = str(source.get("kind") or source.get("type") or "").lower()
        if kind == "environment":
            key = str(source.get("target_key") or "")
            row = db.execute("SELECT intensity FROM environment_effects WHERE campaign_id=? AND target_key=? AND effect_type='fire' AND active=1", (campaign_id, key)).fetchone()
            if not row: raise ValueError("environment ignition source is not actively burning")
            return float(row["intensity"]), key
        if kind == "event":
            event_id = int(source.get("event_id", 0)); row = db.execute("SELECT * FROM events WHERE campaign_id=? AND id=?", (campaign_id, event_id)).fetchone()
            if not row: raise ValueError("source event does not exist")
            payload = self.e._loads(row["payload_json"] or "{}"); text = f"{row['event_type']} {row['summary']} {payload}".lower()
            if not any(token in text for token in ("fire", "flame", "heat", "lightning")): raise ValueError("source event does not establish an ignition source")
            return 0.8, f"event:{event_id}"
        if kind == "item":
            if not actor_kind or not actor_id: raise ValueError("item source requires a turn actor")
            item_id = str(source.get("item_id") or source.get("id") or "")
            if not item_id: raise ValueError("item source requires item_id")
            owned = db.execute("SELECT qty FROM inventories WHERE campaign_id=? AND owner_kind=? AND owner_id=? AND item_id=? AND qty>0", (campaign_id, actor_kind, actor_id, item_id)).fetchone()
            label = item_id
            idef = db.execute("SELECT name,tags_json,metadata_json FROM item_defs WHERE campaign_id=? AND id=?", (campaign_id, item_id)).fetchone()
            if not owned and actor_kind == "character":
                actor = self.e._get_actor_db(db, campaign_id, actor_kind, actor_id); inv = actor.get("inventory") or []
                owned = any((str(x).lower() == item_id.lower()) or (isinstance(x, dict) and str(x.get("id", "")).lower() == item_id.lower()) for x in inv)
            if not owned: raise ValueError("actor does not possess the ignition item")
            tags: list[str] = []
            if idef:
                label = str(idef["name"] or item_id); tags = [str(x).lower() for x in self.e._loads(idef["tags_json"] or "[]")]
            text = f"{item_id} {label} {' '.join(tags)}".lower()
            if not any(token in text for token in ("torch", "lantern", "tinder", "match", "flame", "fire", "ignition", "burning")):
                raise ValueError("item is not established as an ignition source")
            return 0.72, f"item:{actor_kind}:{actor_id}:{item_id}"
        raise ValueError("ignite requires a validated item, environment, or event source")

    @staticmethod
    def _public_target_spec(target: dict[str, Any]) -> dict[str, Any]:
        """Return a closed locator-only target for player interactions."""
        raw = dict(target or {})
        ttype = str(raw.get("type") or raw.get("target_type") or "").strip().lower()
        allowed: dict[str, frozenset[str]] = {
            "tile": frozenset({"type", "target_type", "map_id", "x", "y", "z"}),
            "location": frozenset({"type", "target_type", "id", "target_id"}),
            "scene_feature": frozenset({"type", "target_type", "scene_id", "id", "target_id"}),
            "actor": frozenset({"type", "target_type", "actor_kind", "actor_id", "id", "target_id"}),
        }
        if ttype not in allowed:
            raise PermissionError("PUBLIC_ENVIRONMENT_TARGET_TYPE_NOT_ALLOWED")
        if set(raw) - set(allowed[ttype]):
            raise PermissionError("PUBLIC_ENVIRONMENT_TARGET_FIELDS_NOT_ALLOWED")
        clean = {key: value for key, value in raw.items() if key in allowed[ttype] and value is not None}
        clean["type"] = ttype
        clean.pop("target_type", None)
        return clean

    def _assert_public_locality_db(self, db: sqlite3.Connection, campaign_id: str, actor_kind: str | None, actor_id: str | None, target_row: sqlite3.Row) -> dict[str, Any]:
        if actor_kind != "character" or not actor_id:
            raise PermissionError("PUBLIC_ENVIRONMENT_CHARACTER_REQUIRED")
        actor = self.e._get_actor_db(db, campaign_id, "character", actor_id)
        actor_location = str(actor.get("location") or "")
        if not actor_location or str(target_row["location_id"] or "") != actor_location:
            raise PermissionError("PUBLIC_ENVIRONMENT_TARGET_NOT_LOCAL")
        return actor

    def _validate_public_source_db(self, db: sqlite3.Connection, campaign_id: str, actor_id: str, actor_location: str, source: dict[str, Any] | None, *, purpose: str, method: str | None = None) -> tuple[float, str]:
        spec = dict(source or {})
        kind = str(spec.get("kind") or spec.get("type") or "").strip().lower()
        if purpose == "ignite":
            if kind == "environment":
                key = str(spec.get("target_key") or "")
                row = db.execute(
                    """SELECT e.intensity,t.location_id FROM environment_effects e
                       JOIN environment_targets t ON t.campaign_id=e.campaign_id AND t.target_key=e.target_key
                       WHERE e.campaign_id=? AND e.target_key=? AND e.effect_type='fire' AND e.active=1""",
                    (campaign_id, key),
                ).fetchone()
                if not row or str(row["location_id"] or "") != actor_location:
                    raise PermissionError("PUBLIC_ENVIRONMENT_SOURCE_NOT_LOCAL")
            elif kind == "event":
                event_id = int(spec.get("event_id", 0))
                row = db.execute("SELECT region FROM events WHERE campaign_id=? AND id=?", (campaign_id, event_id)).fetchone()
                if not row or str(row["region"] or "") != actor_location:
                    raise PermissionError("PUBLIC_ENVIRONMENT_SOURCE_NOT_LOCAL")
            strength, source_key = self._source_strength_db(db, campaign_id, "character", actor_id, spec)
            return self._unit(strength), source_key

        method_token = str(method or "").strip().lower()
        if kind == "environment":
            key = str(spec.get("target_key") or "")
            row = db.execute(
                """SELECT e.intensity,t.location_id FROM environment_effects e
                   JOIN environment_targets t ON t.campaign_id=e.campaign_id AND t.target_key=e.target_key
                   WHERE e.campaign_id=? AND e.target_key=? AND e.effect_type='water' AND e.active=1""",
                (campaign_id, key),
            ).fetchone()
            if not row or str(row["location_id"] or "") != actor_location:
                raise PermissionError("PUBLIC_ENVIRONMENT_SOURCE_NOT_LOCAL")
            return min(0.65, max(0.15, float(row["intensity"]))), key
        if kind != "item":
            raise PermissionError("PUBLIC_ENVIRONMENT_EXTINGUISHING_SOURCE_REQUIRED")
        item_id = str(spec.get("item_id") or spec.get("id") or "")
        if not item_id:
            raise PermissionError("PUBLIC_ENVIRONMENT_EXTINGUISHING_SOURCE_REQUIRED")
        owned = db.execute(
            "SELECT qty FROM inventories WHERE campaign_id=? AND owner_kind='character' AND owner_id=? AND item_id=? AND qty>0",
            (campaign_id, actor_id, item_id),
        ).fetchone()
        actor = self.e._get_actor_db(db, campaign_id, "character", actor_id)
        if not owned:
            owned = any(
                str(item).lower() == item_id.lower()
                or (isinstance(item, dict) and str(item.get("id", "")).lower() == item_id.lower())
                for item in (actor.get("inventory") or [])
            )
        if not owned:
            raise PermissionError("PUBLIC_ENVIRONMENT_SOURCE_NOT_OWNED")
        idef = db.execute("SELECT name,tags_json FROM item_defs WHERE campaign_id=? AND id=?", (campaign_id, item_id)).fetchone()
        label = str(idef["name"] if idef else item_id)
        tags = [str(value).lower() for value in self.e._loads(idef["tags_json"] or "[]")] if idef else []
        text = f"{item_id} {label} {' '.join(tags)}".lower()
        water_tokens = ("water", "bucket", "waterskin", "flask", "extinguish")
        smother_tokens = ("blanket", "cloak", "sand", "smother", "extinguish")
        if method_token in {"water", "douse"}:
            if not any(token in text for token in water_tokens):
                raise PermissionError("PUBLIC_ENVIRONMENT_SOURCE_NOT_EXTINGUISHING")
            return 0.45, f"item:character:{actor_id}:{item_id}"
        if not any(token in text for token in water_tokens + smother_tokens):
            raise PermissionError("PUBLIC_ENVIRONMENT_SOURCE_NOT_EXTINGUISHING")
        return 0.35, f"item:character:{actor_id}:{item_id}"

    def interact(self, campaign_id: str, *, action: str, target: dict[str, Any], actor_kind: str | None = None, actor_id: str | None = None, source: dict[str, Any] | None = None, intensity: float = 0.5, amount: float = 1.0, method: str | None = None, reason: str = "environment interaction") -> dict[str, Any]:
        action = str(action or "").lower().strip()
        if action not in {"ignite", "extinguish", "douse", "inspect"}: raise ValueError("public environment interaction must be ignite, extinguish, douse, or inspect")
        public_target = self._public_target_spec(target)
        with self.e._write_db() as db:
            if action == "inspect":
                db.execute("SAVEPOINT public_environment_inspect")
                try:
                    target_row = self._bind_target_db(db, campaign_id, public_target)
                    self._assert_public_locality_db(db, campaign_id, actor_kind, actor_id, target_row)
                    result = self._snapshot_target_db(db, campaign_id, target_row["target_key"])
                finally:
                    db.execute("ROLLBACK TO public_environment_inspect")
                    db.execute("RELEASE public_environment_inspect")
                return result
            target_row = self._bind_target_db(db, campaign_id, public_target)
            actor = self._assert_public_locality_db(db, campaign_id, actor_kind, actor_id, target_row)
            actor_location = str(actor.get("location") or "")
            rev = self.e._next_revision(db, campaign_id)
            world_time = db.execute("SELECT world_time FROM campaigns WHERE id=?", (campaign_id,)).fetchone()["world_time"]
            if action == "ignite":
                source_strength, source_key = self._validate_public_source_db(db, campaign_id, str(actor_id), actor_location, source, purpose="ignite")
                mat = db.execute("SELECT * FROM environment_materials WHERE campaign_id=? AND id=?", (campaign_id, target_row["material_id"])).fetchone()
                props = self.e._loads(target_row["properties_json"] or "{}"); wetness = self._unit(props.get("wetness", 0)); flammability = float(mat["flammability"])
                score = source_strength * flammability * (1.0 - 0.88 * wetness)
                success = score >= 0.12 and flammability > 0
                if success:
                    if "fuel" not in props: props["fuel"] = float(mat["fuel_capacity"])
                    db.execute("UPDATE environment_targets SET properties_json=?,updated_at=? WHERE campaign_id=? AND target_key=?", (self.e._dumps(props), self.e._now(), campaign_id, target_row["target_key"]))
                    row = self._apply_effect_db(db, campaign_id, "fire", target_row, intensity=max(0.12, min(1.0, score)), amount=0, source_key=source_key, state={"ignition_score": round(score, 6)}, world_time=world_time)
                    self.e._insert_event(db, campaign_id, rev, "environment_ignited", reason, region=target_row["location_id"], actor_id=actor_id, payload={"target_key": target_row["target_key"], "source_key": source_key, "ignition_score": round(score, 6), "intensity": float(row["intensity"])})
                else:
                    self.e._insert_event(db, campaign_id, rev, "environment_ignition_failed", reason, region=target_row["location_id"], actor_id=actor_id, payload={"target_key": target_row["target_key"], "ignition_score": round(score, 6), "wetness": wetness, "flammability": flammability})
                return {"campaign_id": campaign_id, "action": action, "success": success, "target_key": target_row["target_key"], "ignition_score": round(score, 6), "revision": rev}
            reduction, source_key = self._validate_public_source_db(
                db, campaign_id, str(actor_id), actor_location, source,
                purpose="extinguish", method="water" if action == "douse" else method,
            )
            props = self.e._loads(target_row["properties_json"] or "{}")
            if action == "douse" or str(method or "").lower() == "water":
                props["wetness"] = min(1.0, float(props.get("wetness", 0)) + reduction * 0.5)
                db.execute("UPDATE environment_targets SET properties_json=?,updated_at=? WHERE campaign_id=? AND target_key=?", (self.e._dumps(props), self.e._now(), campaign_id, target_row["target_key"]))
                reduction *= 0.75
            eid = self._effect_id("fire", target_row["target_key"]); fire = db.execute("SELECT * FROM environment_effects WHERE campaign_id=? AND id=?", (campaign_id, eid)).fetchone()
            remaining = 0.0
            if fire:
                remaining = max(0.0, float(fire["intensity"]) - reduction); db.execute("UPDATE environment_effects SET intensity=?,active=?,updated_at=? WHERE campaign_id=? AND id=?", (remaining, int(remaining > 0.01), self.e._now(), campaign_id, eid))
            self.e._insert_event(db, campaign_id, rev, "environment_extinguished", reason, region=target_row["location_id"], actor_id=actor_id, payload={"target_key": target_row["target_key"], "source_key": source_key, "remaining_fire": remaining, "wetness": props.get("wetness", 0)})
            return {"campaign_id": campaign_id, "action": action, "success": bool(fire), "target_key": target_row["target_key"], "remaining_fire": remaining, "revision": rev}

    # ------------------------------------------------------------------
    # weather / season
    # ------------------------------------------------------------------

    def season_for_time_db(self, db: sqlite3.Connection, campaign_id: str, when: datetime, *, scope_type: str | None = None, scope_id: str | None = None, fallback: str = "summer") -> str:
        row = None
        if scope_type and scope_id:
            row = db.execute("SELECT season,state_json FROM regional_climate WHERE campaign_id=? AND scope_type=? AND scope_id=?", (campaign_id, scope_type, scope_id)).fetchone()
        state = self.e._loads(row["state_json"] or "{}") if row else {}
        if row and state.get("auto_season") is False: return str(row["season"] or fallback).lower()
        mapping = state.get("season_months")
        if isinstance(mapping, dict):
            for season_name, months in mapping.items():
                if when.month in [int(x) for x in months]: return str(season_name).lower()
        # Default northern-hemisphere quarter model. Campaigns can override by
        # regional_climate.state.season_months or disable auto_season.
        if when.month in (3, 4, 5): return "spring"
        if when.month in (6, 7, 8): return "summer"
        if when.month in (9, 10, 11): return "autumn"
        return "winter"

    def _seed(self, db: sqlite3.Connection, campaign_id: str) -> int:
        row = db.execute("SELECT seed FROM sim_config WHERE campaign_id=?", (campaign_id,)).fetchone()
        if row: return int(row["seed"])
        return int.from_bytes(hashlib.sha256(("world-engine-sim:" + campaign_id).encode()).digest()[:8], "big") & ((1 << 63) - 1)

    def _rand_keyed(self, db: sqlite3.Connection, campaign_id: str, namespace: str, key: str) -> float:
        digest = hashlib.sha256(f"{self._seed(db,campaign_id)}:{namespace}:{key}".encode()).digest()
        return int.from_bytes(digest[:8], "big") / float(1 << 64)

    def _weighted_pick(self, db: sqlite3.Connection, campaign_id: str, namespace: str, key: str, weights: dict[str, float]) -> str:
        items = [(str(k), max(0.0, float(v))) for k, v in weights.items() if float(v) > 0]
        if not items: return "clear"
        total = sum(v for _, v in items); r = self._rand_keyed(db, campaign_id, namespace, key) * total; acc = 0.0
        for name, weight in sorted(items):
            acc += weight
            if r <= acc: return name
        return sorted(items)[-1][0]

    def _weather_for_scope_db(self, db: sqlite3.Connection, campaign_id: str, climate_row: sqlite3.Row, when: datetime) -> dict[str, Any]:
        scope_type, scope_id = climate_row["scope_type"], climate_row["scope_id"]; climate = str(climate_row["climate"] or "temperate").lower()
        state = self.e._loads(climate_row["state_json"] or "{}"); season = self.season_for_time_db(db, campaign_id, when, scope_type=scope_type, scope_id=scope_id, fallback=str(climate_row["season"] or "summer"))
        custom = self.e._loads(climate_row["weather_weights_json"] or "{}")
        weights = dict(custom or CLIMATE_WEIGHTS.get(climate, CLIMATE_WEIGHTS["temperate"]))
        # Seasonal plausibility without dense meteorology.
        # A non-empty custom table is an explicit allowlist: scale its entries,
        # but never introduce a condition the author deliberately omitted.
        if season == "winter":
            if not custom and "snow" not in weights: weights["snow"] = 0.2
            if "snow" in weights: weights["snow"] *= 3.0
            if "heatwave" in weights: weights["heatwave"] = 0.0
        elif season == "summer":
            if not custom and "snow" not in weights: weights["snow"] = 0.1
            if not custom and "heatwave" not in weights: weights["heatwave"] = 0.25
            if "snow" in weights: weights["snow"] *= 0.05
            if "heatwave" in weights: weights["heatwave"] *= 1.7
        previous = db.execute("SELECT condition FROM environment_weather WHERE campaign_id=? AND scope_type=? AND scope_id=?", (campaign_id, scope_type, scope_id)).fetchone()
        if previous: weights[str(previous["condition"])] = weights.get(str(previous["condition"]), 0.0) + max(1.0, sum(weights.values()) * 0.18)
        bucket = when.replace(minute=0, second=0, microsecond=0); condition = self._weighted_pick(db, campaign_id, f"weather:{scope_type}:{scope_id}", bucket.isoformat(), weights)
        if condition not in WEATHER_META: condition = "clear"
        precip, precip_intensity, wind_speed, humidity, visibility = WEATHER_META[condition]
        base = BASE_TEMPERATURE.get(climate, 12.0) + SEASON_TEMP.get(season, 0.0) + WEATHER_TEMP.get(condition, 0.0)
        jitter = (self._rand_keyed(db, campaign_id, f"weather-temp:{scope_type}:{scope_id}", bucket.isoformat()) - 0.5) * 5.0
        wind_jitter = 0.7 + self._rand_keyed(db, campaign_id, f"weather-wind:{scope_type}:{scope_id}", bucket.isoformat()) * 0.7
        wind_index = int(self._rand_keyed(db, campaign_id, f"weather-dir:{scope_type}:{scope_id}", bucket.isoformat()) * len(WIND_DIRS)) % len(WIND_DIRS)
        severity = max(precip_intensity, min(1.0, wind_speed * wind_jitter / 60.0), 1.0 - visibility)
        return {"scope_type": scope_type, "scope_id": scope_id, "climate": climate, "season": season, "condition": condition, "precipitation": precip, "precipitation_intensity": precip_intensity, "temperature_c": round(base + jitter, 2), "wind_speed": round(wind_speed * wind_jitter, 2), "wind_direction": WIND_DIRS[wind_index], "humidity": humidity, "visibility": visibility, "severity": round(severity, 4), "state": state}

    def _update_weather_db(self, db: sqlite3.Connection, campaign_id: str, when: datetime, emit: Callable[..., None] | None) -> int:
        # Weather transitions at absolute 6-hour boundaries. Explicit regional
        # climate rows opt locations/regions into automatic local weather.
        if when.minute or when.second or when.hour % 6: return 0
        changed = 0
        rows = db.execute("SELECT * FROM regional_climate WHERE campaign_id=? ORDER BY scope_type,scope_id", (campaign_id,)).fetchall()
        for row in rows:
            state = self.e._loads(row["state_json"] or "{}"); auto_weather = state.get("auto_weather", True)
            season = self.season_for_time_db(db, campaign_id, when, scope_type=row["scope_type"], scope_id=row["scope_id"], fallback=str(row["season"] or "summer"))
            if season != str(row["season"]): db.execute("UPDATE regional_climate SET season=?,updated_at=? WHERE campaign_id=? AND scope_type=? AND scope_id=?", (season, self.e._now(), campaign_id, row["scope_type"], row["scope_id"]))
            if not auto_weather: continue
            wx = self._weather_for_scope_db(db, campaign_id, row, when)
            old = db.execute("SELECT condition FROM environment_weather WHERE campaign_id=? AND scope_type=? AND scope_id=?", (campaign_id, row["scope_type"], row["scope_id"])).fetchone()
            db.execute(
                """INSERT INTO environment_weather(campaign_id,scope_type,scope_id,condition,precipitation,precipitation_intensity,temperature_c,wind_speed,wind_direction,humidity,visibility,severity,generated_world_time,state_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,scope_type,scope_id) DO UPDATE SET condition=excluded.condition,precipitation=excluded.precipitation,
                   precipitation_intensity=excluded.precipitation_intensity,temperature_c=excluded.temperature_c,wind_speed=excluded.wind_speed,
                   wind_direction=excluded.wind_direction,humidity=excluded.humidity,visibility=excluded.visibility,severity=excluded.severity,
                   generated_world_time=excluded.generated_world_time,state_json=excluded.state_json,updated_at=excluded.updated_at""",
                (campaign_id, row["scope_type"], row["scope_id"], wx["condition"], wx["precipitation"], wx["precipitation_intensity"], wx["temperature_c"], wx["wind_speed"], wx["wind_direction"], wx["humidity"], wx["visibility"], wx["severity"], when.isoformat(), self.e._dumps({"climate": wx["climate"], "season": wx["season"]}), self.e._now()),
            )
            if not old or old["condition"] != wx["condition"]:
                changed += 1
                if emit: emit("weather_changed", f"Weather changed to {wx['condition']}", {k: wx[k] for k in ("scope_type","scope_id","condition","precipitation","precipitation_intensity","temperature_c","wind_speed","wind_direction","humidity","visibility","severity","season")}, wx["scope_id"] if wx["scope_type"] == "location" else None, when)
        return changed

    # ------------------------------------------------------------------
    # disaster scheduler (1.63 compatible frequency rule, opt-in)
    # ------------------------------------------------------------------

    def set_disaster_config(self, campaign_id: str, scope_id: str, *, enabled: bool = True, profile: dict[str, Any] | None = None) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id); now_dt = datetime.fromisoformat(self.e.get_campaign(campaign_id)["world_time"]); ordinal = now_dt.date().toordinal()
        with self.e._write_db() as db:
            db.execute("""INSERT INTO environment_disaster_config(campaign_id,scope_id,enabled,profile_json,activated_ordinal,last_checked_ordinal,updated_at) VALUES(?,?,?,?,?,0,?)
                          ON CONFLICT(campaign_id,scope_id) DO UPDATE SET enabled=excluded.enabled,profile_json=excluded.profile_json,updated_at=excluded.updated_at""", (campaign_id, scope_id, int(bool(enabled)), self.e._dumps(profile or {}), ordinal, self.e._now()))
            for tier in range(1, 6): db.execute("INSERT OR IGNORE INTO environment_disaster_counters(campaign_id,scope_id,tier,last_event_ordinal,updated_at) VALUES(?,?,?,?,?)", (campaign_id, scope_id, tier, ordinal, self.e._now()))
        return {"campaign_id": campaign_id, "scope_id": scope_id, "enabled": bool(enabled), "profile": profile or {}}

    def _disaster_step_db(self, db: sqlite3.Connection, campaign_id: str, when: datetime, emit: Callable[..., None] | None) -> int:
        if when.hour != 0 or when.minute or when.second: return 0
        ordinal = when.date().toordinal(); count = 0
        rows = db.execute("SELECT * FROM environment_disaster_config WHERE campaign_id=? AND enabled=1 ORDER BY scope_id", (campaign_id,)).fetchall()
        for cfg in rows:
            if int(cfg["last_checked_ordinal"]) >= ordinal: continue
            profile = self.e._loads(cfg["profile_json"] or "{}"); triggered: list[tuple[int, float]] = []
            for tier in range(1, 6):
                counter = db.execute("SELECT last_event_ordinal FROM environment_disaster_counters WHERE campaign_id=? AND scope_id=? AND tier=?", (campaign_id, cfg["scope_id"], tier)).fetchone()
                last = int(counter["last_event_ordinal"]) if counter else int(cfg["activated_ordinal"]); d = max(1, ordinal - last); tmax = int((profile.get("max_days") or {}).get(str(tier), DISASTER_MAX_DAYS[tier])); p = 1.0 if d >= tmax else min(1.0, 1.0 / max(1, tmax - d))
                u = self._rand_keyed(db, campaign_id, f"disaster:{cfg['scope_id']}:{tier}", str(ordinal))
                if u < p: triggered.append((tier, p))
            db.execute("UPDATE environment_disaster_config SET last_checked_ordinal=?,updated_at=? WHERE campaign_id=? AND scope_id=?", (ordinal, self.e._now(), campaign_id, cfg["scope_id"]))
            if not triggered: continue
            tier, probability = max(triggered, key=lambda x: x[0]); dtype = str((profile.get("types") or {}).get(str(tier), DISASTER_DEFAULTS[tier])); scope_id = str(cfg["scope_id"])
            db.execute("UPDATE environment_disaster_counters SET last_event_ordinal=?,updated_at=? WHERE campaign_id=? AND scope_id=? AND tier=?", (ordinal, self.e._now(), campaign_id, scope_id, tier))
            # Incidents inject pressure into the same environment mechanics rather
            # than directly scripting every downstream consequence.
            location_exists = db.execute("SELECT 1 FROM locations WHERE campaign_id=? AND id=?", (campaign_id, scope_id)).fetchone() is not None
            if location_exists:
                target = self._bind_target_db(db, campaign_id, {"type":"location","id":scope_id})
                if dtype == "wildfire": self._apply_effect_db(db,campaign_id,"fire",target,intensity=min(1.0,0.35+0.12*tier),state={"disaster_tier":tier},world_time=when.isoformat())
                elif dtype == "flood": self._apply_effect_db(db,campaign_id,"water",target,intensity=min(1.0,0.30+0.12*tier),amount=float(tier),state={"disaster_tier":tier},world_time=when.isoformat())
                elif dtype in {"minor_storm","cataclysm"}: self._apply_effect_db(db,campaign_id,"water",target,intensity=min(1.0,0.20+0.10*tier),amount=float(tier)*0.5,state={"disaster_tier":tier},world_time=when.isoformat())
                elif dtype == "earthquake":
                    for tile in db.execute(
                        """SELECT st.* FROM spatial_tiles st
                           JOIN spatial_maps sm ON sm.campaign_id=st.campaign_id AND sm.id=st.map_id
                           WHERE st.campaign_id=? AND st.terrain_hp IS NOT NULL
                             AND sm.scope_type='location' AND sm.scope_id=?
                           ORDER BY st.map_id,st.z,st.y,st.x LIMIT 64""",
                        (campaign_id, scope_id),
                    ).fetchall():
                        if self._rand_keyed(db,campaign_id,f"earthquake:{scope_id}:{tier}",f"{ordinal}:{tile['map_id']}:{tile['x']}:{tile['y']}:{tile['z']}") < min(0.65,0.10*tier):
                            WorldSystemsKernel(self.e)._damage_tile_db(db,campaign_id,tile["map_id"],tile["x"],tile["y"],tile["z"],float(tier)*2.0,reason="earthquake damage",revision=None,emit_event=False)
            if emit: emit("environment_disaster", f"Tier {tier} environmental disaster: {dtype}", {"tier":tier,"disaster_type":dtype,"probability":probability,"scope_id":scope_id,"ordinal":ordinal}, scope_id if location_exists else None, when)
            count += 1
        return count

    # ------------------------------------------------------------------
    # sparse deterministic advancement
    # ------------------------------------------------------------------

    def has_activity_db(self, db: sqlite3.Connection, campaign_id: str) -> bool:
        return any((
            db.execute("SELECT 1 FROM regional_climate WHERE campaign_id=? LIMIT 1", (campaign_id,)).fetchone(),
            db.execute("SELECT 1 FROM environment_effects WHERE campaign_id=? AND active=1 LIMIT 1", (campaign_id,)).fetchone(),
            db.execute("SELECT 1 FROM environment_disaster_config WHERE campaign_id=? AND enabled=1 LIMIT 1", (campaign_id,)).fetchone(),
        ))

    @staticmethod
    def _hours_between(a: str, b: datetime) -> float:
        start = datetime.fromisoformat(a); start = start if start.tzinfo else start.replace(tzinfo=timezone.utc); end = b if b.tzinfo else b.replace(tzinfo=timezone.utc); return max(0.0, (end.astimezone(timezone.utc)-start.astimezone(timezone.utc)).total_seconds()/3600.0)

    def _weather_for_target_db(self, db: sqlite3.Connection, campaign_id: str, target: sqlite3.Row) -> sqlite3.Row | None:
        if target["location_id"]:
            row = db.execute("SELECT * FROM environment_weather WHERE campaign_id=? AND scope_type='location' AND scope_id=?", (campaign_id, target["location_id"])).fetchone()
            if row: return row
        return db.execute("SELECT * FROM environment_weather WHERE campaign_id=? AND scope_type='world' ORDER BY generated_world_time DESC LIMIT 1", (campaign_id,)).fetchone()

    def _neighbors_db(self, db: sqlite3.Connection, campaign_id: str, target: sqlite3.Row) -> list[sqlite3.Row]:
        if target["target_type"] != "tile" or not target["map_id"]: return []
        out=[]
        for dx,dy,dz in ((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)):
            x,y,z=int(target["x"])+dx,int(target["y"])+dy,int(target["z"] or 0)+dz
            row=db.execute("SELECT * FROM environment_targets WHERE campaign_id=? AND map_id=? AND x=? AND y=? AND z=? AND active=1",(campaign_id,target["map_id"],x,y,z)).fetchone()
            if not row:
                tile=db.execute("SELECT * FROM spatial_tiles WHERE campaign_id=? AND map_id=? AND x=? AND y=? AND z=?",(campaign_id,target["map_id"],x,y,z)).fetchone()
                if tile:
                    row=self._bind_target_db(db,campaign_id,{"type":"tile","map_id":target["map_id"],"x":x,"y":y,"z":z})
            if row: out.append(row)
        return out

    def _wind_bias(self, direction: str, source: sqlite3.Row, target: sqlite3.Row) -> float:
        dx=int(target["x"])-int(source["x"]); dy=int(target["y"])-int(source["y"]); vectors={"N":(0,-1),"NE":(1,-1),"E":(1,0),"SE":(1,1),"S":(0,1),"SW":(-1,1),"W":(-1,0),"NW":(-1,-1)}; vx,vy=vectors.get(str(direction).upper(),(0,0)); dot=dx*vx+dy*vy
        return 1.45 if dot>0 else (0.72 if dot<0 else 1.0)

    def _set_target_props_db(self, db: sqlite3.Connection, campaign_id: str, target_key: str, props: dict[str, Any]) -> None:
        self._normalize_properties(props); db.execute("UPDATE environment_targets SET properties_json=?,updated_at=? WHERE campaign_id=? AND target_key=?", (self.e._dumps(props),self.e._now(),campaign_id,target_key))

    def _sync_tile_move_cost_db(self, db: sqlite3.Connection, campaign_id: str, target: sqlite3.Row, props: dict[str, Any], material_id: str) -> None:
        if target["target_type"] != "tile": return
        tile=db.execute("SELECT move_cost,state_json FROM spatial_tiles WHERE campaign_id=? AND map_id=? AND x=? AND y=? AND z=?",(campaign_id,target["map_id"],target["x"],target["y"],target["z"])).fetchone()
        tile_state=self.e._loads(tile["state_json"] or "{}") if tile else {}
        base=max(0.05,float(tile_state.get("_base_move_cost",props.get("base_move_cost",tile["move_cost"] if tile else 1.0)))); wet=self._unit(props.get("wetness",0)); snow=max(0,float(props.get("snow_depth",0))); water=max(0,float(props.get("water_level",0))); factor=1.0
        if material_id=="earth" and wet>0.55: factor += (wet-0.55)*1.6
        factor += min(1.5, snow*0.55) + min(2.5, water*1.4)
        if props.get("icy"): factor += 0.35
        factor += min(0.75,max(0.0,float(props.get("wind_speed",0.0))-35.0)/45.0)
        db.execute("UPDATE spatial_tiles SET move_cost=?,updated_at=? WHERE campaign_id=? AND map_id=? AND x=? AND y=? AND z=?", (base*factor,self.e._now(),campaign_id,target["map_id"],target["x"],target["y"],target["z"]))

    def _actor_exposure_db(self, db: sqlite3.Connection, campaign_id: str, revision: int, target: sqlite3.Row, effect_type: str, intensity: float, dt_hours: float) -> int:
        if target["target_type"] != "actor" or dt_hours <= 0 or intensity <= 0.05: return 0
        props=self.e._loads(target["properties_json"] or "{}"); kind=props.get("actor_kind"); aid=props.get("actor_id")
        if not kind or not aid: return 0
        damage_type={"fire":"fire","smoke":"poison","heat":"fire","cold":"cold","gas":"poison","corrosion":"acid","corruption":"necrotic","disease":"poison","electricity":"lightning","explosion":"force"}.get(effect_type)
        if not damage_type: return 0
        raw=max(0,int(math.floor(intensity*dt_hours*4.0)))
        if raw<=0:return 0
        RulesKernel(self.e)._apply_damage_db(db,campaign_id,str(kind),str(aid),[{"type":damage_type,"raw":raw,"source":"environment"}],revision=revision,source_name=f"environmental {effect_type}",allow_concentration=True)
        return raw

    def _process_effect_db(self, db: sqlite3.Connection, campaign_id: str, revision: int, effect: sqlite3.Row, when: datetime, is_hour_boundary: bool, emit: Callable[..., None] | None) -> dict[str,int]:
        target=db.execute("SELECT * FROM environment_targets WHERE campaign_id=? AND target_key=? AND active=1",(campaign_id,effect["target_key"])).fetchone()
        if not target: db.execute("UPDATE environment_effects SET active=0,updated_at=? WHERE campaign_id=? AND id=?",(self.e._now(),campaign_id,effect["id"])); return {"effects":0,"spread":0,"damage":0}
        if target["target_type"] == "actor":
            target=self._refresh_actor_target_db(db,campaign_id,target)
            if not target:
                db.execute("UPDATE environment_effects SET active=0,updated_at=? WHERE campaign_id=? AND id=?",(self.e._now(),campaign_id,effect["id"]))
                return {"effects":0,"spread":0,"damage":0}
        dt=self._hours_between(effect["last_world_time"],when)
        if dt<=0:return {"effects":0,"spread":0,"damage":0}
        if effect["expires_world_time"]:
            expires=datetime.fromisoformat(str(effect["expires_world_time"])); expires=expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)
            if when.astimezone(timezone.utc) >= expires.astimezone(timezone.utc):
                db.execute("UPDATE environment_effects SET intensity=0,active=0,last_world_time=?,updated_at=? WHERE campaign_id=? AND id=?",(when.isoformat(),self.e._now(),campaign_id,effect["id"]))
                if emit: emit("environment_effect_ended",f"Environmental effect expired: {effect['effect_type']}",{"effect_type":effect["effect_type"],"target_key":target["target_key"],"reason":"expired"},target["location_id"],when)
                return {"effects":1,"spread":0,"damage":0}
        weather=self._weather_for_target_db(db,campaign_id,target)
        mat=db.execute("SELECT * FROM environment_materials WHERE campaign_id=? AND id=?",(campaign_id,target["material_id"])).fetchone(); props=self.e._loads(target["properties_json"] or "{}"); state=self.e._loads(effect["state_json"] or "{}"); intensity=float(effect["intensity"]); amount=float(effect["amount"]); et=str(effect["effect_type"])
        wet=self._unit(props.get("wetness",0)); damage=0; spread=0; active=True

        if et=="fire":
            fuel=float(props.get("fuel",mat["fuel_capacity"])); dryness=max(0.0,1.0-wet); growth=(float(mat["flammability"])*dryness*0.22-0.065)*dt; intensity=max(0.0,min(1.0,intensity+growth)); burn=max(0.0,intensity*(0.035+0.045*float(mat["flammability"]))*dt*max(1.0,float(mat["fuel_capacity"]))); fuel=max(0.0,fuel-burn); props["fuel"]=fuel; props["wetness"]=max(0.0,wet-intensity*0.10*dt); props["temperature_c"]=max(float(props.get("temperature_c",20.0)),80.0+intensity*420.0)
            smoke=self._apply_effect_db(db,campaign_id,"smoke",target,intensity=min(1.0,intensity*0.72),amount=intensity*dt,source_key=effect["id"],world_time=when.isoformat())
            if target["target_type"]=="tile" and target["map_id"]:
                dmg=intensity*(2.0+8.0*(1.0-float(mat["hardness"])))*dt
                if dmg>0.01:
                    result=WorldSystemsKernel(self.e)._damage_tile_db(db,campaign_id,target["map_id"],target["x"],target["y"],target["z"],dmg,reason="environmental fire damage",revision=revision,emit_event=False); damage+=1
                    if result["newly_destroyed"] and emit: emit("environment_structure_destroyed","Fire destroyed terrain",{"target_key":target["target_key"],"map_id":target["map_id"],"x":target["x"],"y":target["y"],"z":target["z"]},target["location_id"],when)
            damage += 1 if self._actor_exposure_db(db,campaign_id,revision,target,"fire",intensity,dt)>0 else 0
            if fuel<=0.001 or intensity<=0.01: active=False; intensity=0.0
            if is_hour_boundary and intensity>0.08:
                for nb in self._neighbors_db(db,campaign_id,target):
                    nbmat=db.execute("SELECT * FROM environment_materials WHERE campaign_id=? AND id=?",(campaign_id,nb["material_id"])).fetchone(); nbp=self.e._loads(nb["properties_json"] or "{}"); chance=intensity*float(nbmat["flammability"])*(1.0-self._unit(nbp.get("wetness",0)))*0.38
                    if weather: chance*=self._wind_bias(weather["wind_direction"],target,nb)*(1.0+min(0.8,float(weather["wind_speed"])/80.0))
                    if self._rand_keyed(db,campaign_id,"environment-fire-spread",f"{when.isoformat()}:{target['target_key']}:{nb['target_key']}")<min(0.95,chance):
                        self._apply_effect_db(db,campaign_id,"fire",nb,intensity=max(0.08,intensity*0.32),source_key=effect["id"],state={"spread_from":target["target_key"]},world_time=when.isoformat()); spread+=1
                        if emit: emit("environment_effect_spread","Fire spread",{"effect_type":"fire","from":target["target_key"],"to":nb["target_key"]},nb["location_id"],when)
        elif et in {"smoke","gas"}:
            wind=float(weather["wind_speed"]) if weather else 0.0; decay=(0.12+min(0.22,wind/180.0))*dt; intensity=max(0.0,intensity-decay); amount=max(0.0,amount*(1.0-min(0.85,0.16*dt))); ambient_vis=float(weather["visibility"]) if weather else float(props.get("ambient_visibility",1.0)); props["visibility"] = min(ambient_vis, max(0.05,1.0-intensity*0.78))
            damage += 1 if self._actor_exposure_db(db,campaign_id,revision,target,et,intensity,dt)>0 else 0
            if target["location_id"] and target["target_type"] in {"location","zone"} and intensity>0.12:
                for table,kind in (("characters","character"),("npcs","npc")):
                    for actor in db.execute(f"SELECT id FROM {table} WHERE campaign_id=? AND location=? AND status='alive' ORDER BY id LIMIT 200",(campaign_id,target["location_id"])).fetchall():
                        at=self._bind_target_db(db,campaign_id,{"type":"actor","actor_kind":kind,"actor_id":actor["id"]}); raw=self._actor_exposure_db(db,campaign_id,revision,at,et,intensity,dt)
                        if raw>0: damage+=1
            if is_hour_boundary and intensity>0.10:
                for nb in self._neighbors_db(db,campaign_id,target):
                    chance=min(0.92,intensity*(0.42 if et=="smoke" else 0.30));
                    if weather: chance*=self._wind_bias(weather["wind_direction"],target,nb)
                    if self._rand_keyed(db,campaign_id,f"environment-{et}-spread",f"{when.isoformat()}:{target['target_key']}:{nb['target_key']}")<chance:
                        self._apply_effect_db(db,campaign_id,et,nb,intensity=intensity*0.45,amount=amount*0.15,source_key=effect["id"],world_time=when.isoformat()); spread+=1
            if intensity<=0.02: active=False; intensity=0.0
        elif et=="water":
            props["wetness"]=min(1.0,wet+intensity*0.32*dt); props["water_level"]=max(0.0,float(props.get("water_level",0))+amount*0.06*dt+intensity*0.02*dt-float(mat["permeability"])*0.035*dt); amount=max(0.0,amount-float(mat["permeability"])*0.08*dt); intensity=max(0.0,min(1.0,max(intensity*max(0.0,1.0-0.08*dt),float(props["water_level"])*0.5)))
            if is_hour_boundary and float(props["water_level"])>0.18:
                for nb in self._neighbors_db(db,campaign_id,target):
                    if int(nb["z"] or 0)>int(target["z"] or 0): continue
                    if self._rand_keyed(db,campaign_id,"environment-water-spread",f"{when.isoformat()}:{target['target_key']}:{nb['target_key']}")<min(0.9,0.30+float(props["water_level"])*0.35):
                        self._apply_effect_db(db,campaign_id,"water",nb,intensity=intensity*0.55,amount=max(0.05,amount*0.20),source_key=effect["id"],world_time=when.isoformat()); spread+=1
            if intensity<=0.02 and amount<=0.02 and float(props.get("water_level",0))<=0.02: active=False; intensity=0.0
        elif et in {"heat","cold"}:
            goal=80.0+intensity*220 if et=="heat" else -5.0-intensity*35; temp=float(props.get("temperature_c",20.0)); props["temperature_c"]=temp+(goal-temp)*min(1.0,0.22*dt); intensity=max(0.0,intensity-0.035*dt); damage += 1 if self._actor_exposure_db(db,campaign_id,revision,target,et,intensity,dt)>0 else 0; active=intensity>0.02
        elif et in {"blight","corruption"}:
            intensity=min(1.0,intensity+0.006*dt); props["contamination"]=max(float(props.get("contamination",0)),intensity)
            if target["location_id"]:
                db.execute("UPDATE resource_nodes SET qty=MAX(0,qty-regen_per_day*?*?),updated_at=? WHERE campaign_id=? AND location_id=?",(intensity,dt/24.0,self.e._now(),campaign_id,target["location_id"]))
            if is_hour_boundary and when.hour==0 and intensity>0.18:
                for nb in self._neighbors_db(db,campaign_id,target):
                    if self._rand_keyed(db,campaign_id,f"environment-{et}-spread",f"{when.date().isoformat()}:{target['target_key']}:{nb['target_key']}")<intensity*0.16:
                        self._apply_effect_db(db,campaign_id,et,nb,intensity=intensity*0.28,source_key=effect["id"],world_time=when.isoformat()); spread+=1
            damage += 1 if self._actor_exposure_db(db,campaign_id,revision,target,et,intensity,dt)>0 else 0
        elif et=="disease":
            # Disease is a persistent exposure/affliction process rather than a
            # one-off condition string. Location disease can seed actor disease;
            # actor disease advances the existing affliction stage record.
            intensity=max(0.0,min(1.0,intensity+0.002*dt))
            if target["target_type"]=="actor":
                ap=self.e._loads(target["properties_json"] or "{}"); ak=str(ap.get("actor_kind")); aid=str(ap.get("actor_id")); stage=max(1,min(4,int(math.ceil(intensity*4))))
                db.execute("""INSERT INTO afflictions(campaign_id,actor_kind,actor_id,id,kind,stage,max_stage,state_json,updated_at) VALUES(?,?,?,?,?, ?,4,?,?)
                              ON CONFLICT(campaign_id,actor_kind,actor_id,id) DO UPDATE SET stage=MAX(afflictions.stage,excluded.stage),state_json=excluded.state_json,updated_at=excluded.updated_at""",
                           (campaign_id,ak,aid,"environment_disease","disease",stage,self.e._dumps({"source_effect":effect["id"],"intensity":round(intensity,6)}),self.e._now()))
                damage += 1 if self._actor_exposure_db(db,campaign_id,revision,target,"disease",intensity,dt)>0 else 0
            elif target["location_id"] and is_hour_boundary and when.hour==0 and intensity>0.08:
                for table,kind in (("characters","character"),("npcs","npc")):
                    for actor in db.execute(f"SELECT id FROM {table} WHERE campaign_id=? AND location=? AND status='alive' ORDER BY id LIMIT 200",(campaign_id,target["location_id"])).fetchall():
                        if self._rand_keyed(db,campaign_id,"environment-disease-exposure",f"{when.date().isoformat()}:{target['target_key']}:{kind}:{actor['id']}") < intensity*0.32:
                            at=self._bind_target_db(db,campaign_id,{"type":"actor","actor_kind":kind,"actor_id":actor["id"]});self._apply_effect_db(db,campaign_id,"disease",at,intensity=max(0.06,intensity*0.45),source_key=effect["id"],world_time=when.isoformat());spread+=1
        elif et=="electricity":
            damage += 1 if self._actor_exposure_db(db,campaign_id,revision,target,"electricity",intensity,dt)>0 else 0
            if target["target_type"]=="tile" and is_hour_boundary and intensity>0.08:
                for nb in self._neighbors_db(db,campaign_id,target):
                    nbmat=db.execute("SELECT conductivity FROM environment_materials WHERE campaign_id=? AND id=?",(campaign_id,nb["material_id"])).fetchone();chance=intensity*float(nbmat["conductivity"])*0.65
                    if self._rand_keyed(db,campaign_id,"environment-electricity-spread",f"{when.isoformat()}:{target['target_key']}:{nb['target_key']}")<chance:
                        self._apply_effect_db(db,campaign_id,"electricity",nb,intensity=intensity*0.45,source_key=effect["id"],world_time=when.isoformat());spread+=1
            if target["target_type"]=="tile":
                if float(mat["flammability"])>0 and self._rand_keyed(db,campaign_id,"environment-electricity-ignite",f"{when.isoformat()}:{target['target_key']}") < intensity*float(mat["flammability"])*(1.0-wet)*0.22:
                    self._apply_effect_db(db,campaign_id,"fire",target,intensity=max(0.10,intensity*0.30),source_key=effect["id"],world_time=when.isoformat());spread+=1
            intensity=max(0.0,intensity-0.75*dt);active=intensity>0.02
        elif et=="explosion":
            # One-shot radial incident. The effect persists only until the next
            # environment step, where it damages nearby structure and may ignite
            # flammable material.
            radius=max(1,min(3,int(state.get("radius",1)))); targets=[target]
            if target["target_type"]=="tile" and target["map_id"]:
                frontier=[target]; seen={target["target_key"]}
                for _ in range(radius):
                    nxt=[]
                    for cur in frontier:
                        for nb in self._neighbors_db(db,campaign_id,cur):
                            if nb["target_key"] not in seen: seen.add(nb["target_key"]);targets.append(nb);nxt.append(nb)
                    frontier=nxt
            for victim in targets:
                distance=0 if victim["target_key"]==target["target_key"] else 1
                power=max(0.05,intensity*(1.0-0.28*distance))
                if victim["target_type"]=="tile" and victim["map_id"]:
                    tr=db.execute("SELECT terrain_hp FROM spatial_tiles WHERE campaign_id=? AND map_id=? AND x=? AND y=? AND z=?",(campaign_id,victim["map_id"],victim["x"],victim["y"],victim["z"])).fetchone()
                    if tr and tr["terrain_hp"] is not None:
                        WorldSystemsKernel(self.e)._damage_tile_db(db,campaign_id,victim["map_id"],victim["x"],victim["y"],victim["z"],power*14.0,reason="environmental explosion",revision=revision,emit_event=False);damage+=1
                    vm=db.execute("SELECT flammability FROM environment_materials WHERE campaign_id=? AND id=?",(campaign_id,victim["material_id"])).fetchone(); vp=self.e._loads(victim["properties_json"] or "{}")
                    if vm and float(vm["flammability"])>0 and self._rand_keyed(db,campaign_id,"environment-explosion-ignite",f"{when.isoformat()}:{effect['id']}:{victim['target_key']}") < power*float(vm["flammability"])*(1.0-self._unit(vp.get("wetness",0)))*0.65:
                        self._apply_effect_db(db,campaign_id,"fire",victim,intensity=max(0.12,power*0.55),source_key=effect["id"],world_time=when.isoformat());spread+=1
                damage += 1 if self._actor_exposure_db(db,campaign_id,revision,victim,"explosion",power,max(dt,0.25))>0 else 0
            intensity=0.0; active=False
            if emit: emit("environment_explosion","Environmental explosion resolved",{"target_key":target["target_key"],"radius":radius,"affected":len(targets)},target["location_id"],when)
        elif et=="drought":
            props["wetness"]=max(0.0,wet-intensity*0.08*dt); props["humidity"]=min(float(props.get("humidity",0.5)),max(0.05,0.35-intensity*0.2)); intensity=max(0.0,min(1.0,intensity+0.001*dt))
            if target["location_id"]:
                db.execute("UPDATE resource_nodes SET qty=MAX(0,qty-regen_per_day*?*?),updated_at=? WHERE campaign_id=? AND location_id=?",(intensity*1.5,dt/24.0,self.e._now(),campaign_id,target["location_id"]))
        elif et=="corrosion":
            intensity=max(0.0,intensity-0.01*dt)
            if target["target_type"]=="tile" and target["map_id"] and float(mat["hardness"])<0.98:
                WorldSystemsKernel(self.e)._damage_tile_db(db,campaign_id,target["map_id"],target["x"],target["y"],target["z"],intensity*(1.1-float(mat["hardness"]))*2.0*dt,reason="environmental corrosion",revision=revision,emit_event=False); damage+=1
            active=intensity>0.02
        elif et=="snow": props["snow_depth"]=max(0.0,float(props.get("snow_depth",0))+amount*0.03*dt); intensity=max(0.0,intensity-0.01*dt); active=intensity>0.01
        elif et=="ice": props["icy"]=True; props["temperature_c"]=min(float(props.get("temperature_c",0)),0.0); intensity=max(0.0,intensity-0.015*dt); active=intensity>0.02
        elif et=="mud": props["wetness"]=max(wet,intensity); intensity=max(0.0,intensity-0.025*dt); active=intensity>0.02
        elif et=="darkness": props["visibility"]=min(float(props.get("visibility",1.0)),max(0.0,1.0-intensity)); active=True

        self._set_target_props_db(db,campaign_id,target["target_key"],props); self._sync_tile_move_cost_db(db,campaign_id,target,props,str(target["material_id"]))
        db.execute("UPDATE environment_effects SET intensity=?,amount=?,state_json=?,last_world_time=?,active=?,updated_at=? WHERE campaign_id=? AND id=?",(self._unit(intensity),max(0.0,amount),self.e._dumps(state),when.isoformat(),int(bool(active)),self.e._now(),campaign_id,effect["id"]))
        if not active and emit: emit("environment_effect_ended",f"Environmental effect ended: {et}",{"effect_type":et,"target_key":target["target_key"]},target["location_id"],when)
        return {"effects":1,"spread":spread,"damage":damage}

    def _location_is_sheltered_db(self, db: sqlite3.Connection, campaign_id: str, location_id: str) -> bool:
        row=db.execute("SELECT tags_json,state_json FROM locations WHERE campaign_id=? AND id=?",(campaign_id,location_id)).fetchone()
        if not row: return False
        tags={str(x).lower() for x in self.e._loads(row["tags_json"] or "[]")}
        state=self.e._loads(row["state_json"] or "{}")
        if state.get("sheltered") is True or state.get("indoors") is True: return True
        return bool(tags & {"indoors","interior","building","underground","sheltered"})

    def _refresh_actor_target_db(self, db: sqlite3.Connection, campaign_id: str, target: sqlite3.Row) -> sqlite3.Row | None:
        if target["target_type"] != "actor": return target
        props=self.e._loads(target["properties_json"] or "{}"); kind=str(props.get("actor_kind") or ""); actor_id=str(props.get("actor_id") or "")
        table={"character":"characters","npc":"npcs"}.get(kind)
        actor=db.execute(f"SELECT location,status FROM {table} WHERE campaign_id=? AND id=?",(campaign_id,actor_id)).fetchone() if table and actor_id else None
        if not actor or str(actor["status"])!="alive":
            db.execute("UPDATE environment_targets SET active=0,updated_at=? WHERE campaign_id=? AND target_key=?",(self.e._now(),campaign_id,target["target_key"]))
            return None
        if actor["location"] != target["location_id"]:
            db.execute("UPDATE environment_targets SET location_id=?,updated_at=? WHERE campaign_id=? AND target_key=?",(actor["location"],self.e._now(),campaign_id,target["target_key"]))
            target=db.execute("SELECT * FROM environment_targets WHERE campaign_id=? AND target_key=?",(campaign_id,target["target_key"])).fetchone()
        return target

    def _ensure_weather_actor_targets_db(self, db: sqlite3.Connection, campaign_id: str) -> int:
        """Bind actors only where local climate explicitly permits exposure.

        A location can opt out with regional_climate.state.actor_exposure=false or
        with ordinary indoor/shelter location metadata. This avoids treating every
        town resident as permanently outdoors while still making wilderness weather
        mechanically consequential without authoring actor targets by hand.
        """
        added=0
        for climate in db.execute("SELECT scope_id,state_json FROM regional_climate WHERE campaign_id=? AND scope_type='location' ORDER BY scope_id",(campaign_id,)).fetchall():
            location_id=str(climate["scope_id"]); state=self.e._loads(climate["state_json"] or "{}")
            if state.get("actor_exposure") is False or self._location_is_sheltered_db(db,campaign_id,location_id): continue
            for table,kind in (("characters","character"),("npcs","npc")):
                for actor in db.execute(f"SELECT id FROM {table} WHERE campaign_id=? AND location=? AND status='alive' ORDER BY id LIMIT 200",(campaign_id,location_id)).fetchall():
                    key=self._target_key("actor",f"{kind}:{actor['id']}",None,None,None,None)
                    existed=db.execute("SELECT 1 FROM environment_targets WHERE campaign_id=? AND target_key=?",(campaign_id,key)).fetchone() is not None
                    self._bind_target_db(db,campaign_id,{"type":"actor","actor_kind":kind,"actor_id":actor["id"]})
                    if not existed: added+=1
        return added

    def _weather_targets_db(self, db: sqlite3.Connection, campaign_id: str, revision: int, when: datetime, emit: Callable[..., None] | None) -> int:
        changed=self._ensure_weather_actor_targets_db(db,campaign_id)
        # Ambient weather is a per-target phase, independent of effect count.
        targets=db.execute("SELECT * FROM environment_targets WHERE campaign_id=? AND active=1 ORDER BY target_key",(campaign_id,)).fetchall()
        for target in targets:
            if target["target_type"]=="actor":
                target=self._refresh_actor_target_db(db,campaign_id,target)
                if not target: continue
                location_id=str(target["location_id"] or "")
                climate=db.execute("SELECT state_json FROM regional_climate WHERE campaign_id=? AND scope_type='location' AND scope_id=?",(campaign_id,location_id)).fetchone()
                if not climate or self._location_is_sheltered_db(db,campaign_id,location_id): continue
                if self.e._loads(climate["state_json"] or "{}").get("actor_exposure") is False: continue
            weather=self._weather_for_target_db(db,campaign_id,target)
            if not weather: continue
            props=self.e._loads(target["properties_json"] or "{}"); before=dict(props); dt=1.0
            temp=float(props.get("temperature_c",weather["temperature_c"])); props["temperature_c"]=temp+(float(weather["temperature_c"])-temp)*0.12; props["humidity"]=float(weather["humidity"]); props["wind_speed"]=float(weather["wind_speed"]); props["wind_direction"]=str(weather["wind_direction"]); props["ambient_visibility"]=float(weather["visibility"]); props["visibility"]=float(weather["visibility"]); p=float(weather["precipitation_intensity"])
            if weather["precipitation"]=="rain": props["wetness"]=min(1.0,float(props.get("wetness",0))+p*0.16)
            if weather["precipitation"]=="snow" and float(weather["temperature_c"])<=1.0: props["snow_depth"]=float(props.get("snow_depth",0))+p*0.04
            dry=max(0.0,(float(weather["temperature_c"])-5.0)/35.0)*(1.0-float(weather["humidity"])); props["wetness"]=max(0.0,float(props.get("wetness",0))-dry*(0.035+float(weather["wind_speed"])/1000.0)*dt)
            if float(weather["temperature_c"])>1.0 and props.get("snow_depth",0)>0: props["snow_depth"]=max(0.0,float(props["snow_depth"])-0.04*(float(weather["temperature_c"])-1.0))
            # Sparse survival/exposure: use bands rather than dense heat physics.
            if target["target_type"]=="actor" and not bool(props.get("sheltered",False)):
                wt=float(weather["temperature_c"]); exposure=0.0; kind=None
                if wt < -5.0: kind="cold"; exposure=min(1.0,(-5.0-wt)/25.0)
                elif wt > 35.0: kind="heat"; exposure=min(1.0,(wt-35.0)/20.0)
                if kind and exposure>0.05:
                    raw=self._actor_exposure_db(db,campaign_id,revision,target,kind,exposure,1.0)
                    if raw>0 and emit: emit("environment_exposure",f"Environmental {kind} exposure",{"target_key":target["target_key"],"effect_type":kind,"intensity":round(exposure,6),"raw_damage":raw,"temperature_c":wt},target["location_id"],when)
                if str(weather["condition"])=="storm" and float(weather["severity"])>0.55:
                    u=self._rand_keyed(db,campaign_id,"weather-lightning",f"{when.isoformat()}:{target['target_key']}")
                    if u < min(0.08,float(weather["severity"])*0.035):
                        self._apply_effect_db(db,campaign_id,"electricity",target,intensity=max(0.35,float(weather["severity"])),source_key="weather:lightning",world_time=when.isoformat())
                        if emit: emit("environment_lightning_strike","Lightning struck an exposed actor",{"target_key":target["target_key"],"severity":float(weather["severity"])},target["location_id"],when)
            if props!=before: self._set_target_props_db(db,campaign_id,target["target_key"],props); self._sync_tile_move_cost_db(db,campaign_id,target,props,str(target["material_id"])); changed+=1
        return changed

    def _daily_societal_consequences_db(self, db: sqlite3.Connection, campaign_id: str, when: datetime, emit: Callable[..., None] | None) -> int:
        if when.hour!=0 or when.minute or when.second: return 0
        count=0
        rows=db.execute("""SELECT t.location_id,MAX(e.intensity) hazard
                           FROM environment_effects e JOIN environment_targets t
                           ON t.campaign_id=e.campaign_id AND t.target_key=e.target_key
                           WHERE e.campaign_id=? AND e.active=1 AND t.location_id IS NOT NULL
                             AND e.effect_type IN ('fire','smoke','water','gas','blight','corruption','disease','drought')
                           GROUP BY t.location_id""",(campaign_id,)).fetchall()
        for row in rows:
            loc=str(row["location_id"]); hazard=self._unit(row["hazard"]); pop=db.execute("SELECT * FROM population_state WHERE campaign_id=? AND location_id=?",(campaign_id,loc)).fetchone()
            if pop and hazard>0.05:
                safety=max(0.0,float(pop["safety"])-hazard*0.05); pressure=max(0.0,float(pop["migration_pressure"])+hazard*0.10)
                db.execute("UPDATE population_state SET safety=?,migration_pressure=?,updated_at=? WHERE campaign_id=? AND location_id=?",(safety,pressure,self.e._now(),campaign_id,loc));count+=1
            if hazard>0.35:
                # Damage/depletion remains authoritative state; social systems
                # can subscribe to this typed seam without the environment layer
                # inventing faction policy or rumors.
                if emit: emit("environment_social_pressure","Environmental hazards are creating local social pressure",{"location_id":loc,"hazard":round(hazard,6)},loc,when)
        return count

    def _sync_world_state_db(self, db: sqlite3.Connection, campaign_id: str) -> None:
        aggregate: dict[str, dict[str,float]]={}
        for row in db.execute("""SELECT t.location_id,e.effect_type,MAX(e.intensity) intensity FROM environment_effects e JOIN environment_targets t ON t.campaign_id=e.campaign_id AND t.target_key=e.target_key WHERE e.campaign_id=? AND e.active=1 AND t.location_id IS NOT NULL GROUP BY t.location_id,e.effect_type""",(campaign_id,)).fetchall():
            aggregate.setdefault(str(row["location_id"]),{})[str(row["effect_type"])]=float(row["intensity"])
        existing=db.execute("SELECT scope_id,state_key FROM world_state WHERE campaign_id=? AND scope_type='location' AND state_key LIKE 'environment.%'",(campaign_id,)).fetchall(); keys={(r["scope_id"],r["state_key"]) for r in existing}
        now=self.e._now()
        for loc,effects in aggregate.items():
            hazard=max(effects.get(x,0.0) for x in ("fire","smoke","water","gas","blight","corrosion","corruption","heat","cold","disease","electricity","explosion","drought")); effects={**effects,"hazard":hazard}
            for et,value in effects.items():
                key=f"environment.{et}"; db.execute("""INSERT INTO world_state(campaign_id,scope_type,scope_id,state_key,value_json,updated_at) VALUES(?,'location',?,?,?,?) ON CONFLICT(campaign_id,scope_type,scope_id,state_key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",(campaign_id,loc,key,self.e._dumps(round(value,6)),now)); keys.discard((loc,key))
        for loc,key in keys: db.execute("UPDATE world_state SET value_json='0',updated_at=? WHERE campaign_id=? AND scope_type='location' AND scope_id=? AND state_key=?",(now,campaign_id,loc,key))

    def step_db(self, db: sqlite3.Connection, campaign_id: str, revision: int, when: datetime, *, emit: Callable[..., None] | None = None) -> dict[str,int]:
        when = when if when.tzinfo else when.replace(tzinfo=timezone.utc); is_hour_boundary=(when.minute==0 and when.second==0)
        tally={"weather":0,"effects":0,"spread":0,"damage":0,"weather_targets":0,"disasters":0,"societal":0}
        tally["weather"] += self._update_weather_db(db,campaign_id,when,emit)
        self._deferred_effects=[]
        try:
            if is_hour_boundary: tally["weather_targets"] += self._weather_targets_db(db,campaign_id,revision,when,emit)
            effects=db.execute("SELECT id FROM environment_effects WHERE campaign_id=? AND active=1 ORDER BY effect_type,id",(campaign_id,)).fetchall()
            for effect_ref in effects:
                effect=db.execute("SELECT * FROM environment_effects WHERE campaign_id=? AND id=? AND active=1",(campaign_id,effect_ref["id"])).fetchone()
                if not effect: continue
                result=self._process_effect_db(db,campaign_id,revision,effect,when,is_hour_boundary,emit)
                for key in ("effects","spread","damage"): tally[key]+=int(result[key])
        finally:
            pending=self._deferred_effects
            del self._deferred_effects
        # Consequences produced during this boundary begin at the boundary; they
        # must not replace or skip the just-completed evolution of older material.
        for application in pending:
            target=db.execute("SELECT * FROM environment_targets WHERE campaign_id=? AND target_key=? AND active=1",(campaign_id,application.pop("target_key"))).fetchone()
            if target: self._apply_effect_db(db,target_row=target,**application)
        tally["disasters"] += self._disaster_step_db(db,campaign_id,when,emit)
        tally["societal"] += self._daily_societal_consequences_db(db,campaign_id,when,emit)
        self._sync_world_state_db(db,campaign_id)
        return tally

    # ------------------------------------------------------------------
    # snapshots / NPC utility bridge / dispatch
    # ------------------------------------------------------------------

    def _snapshot_target_db(self, db: sqlite3.Connection, campaign_id: str, target_key: str) -> dict[str, Any]:
        row=db.execute("SELECT * FROM environment_targets WHERE campaign_id=? AND target_key=?",(campaign_id,target_key)).fetchone()
        if not row: raise KeyError(f"unknown environment target: {target_key}")
        effects=[self._decode_effect(r) for r in db.execute("SELECT * FROM environment_effects WHERE campaign_id=? AND target_key=? AND active=1 ORDER BY effect_type,id",(campaign_id,target_key)).fetchall()]
        out=self._decode_target(row); out["effects"]=effects; return out

    def snapshot_db(self, db: sqlite3.Connection, campaign_id: str, *, location_id: str | None = None, limit: int = 100) -> dict[str, Any]:
        params=[campaign_id]; where="campaign_id=? AND active=1"
        if location_id: where+=" AND location_id=?";params.append(location_id)
        targets=[self._decode_target(r) for r in db.execute(f"SELECT * FROM environment_targets WHERE {where} ORDER BY target_key LIMIT ?",(*params,max(1,min(int(limit),200)))).fetchall()]
        target_keys=[x["target_key"] for x in targets]; effects=[]
        if target_keys:
            marks=",".join("?" for _ in target_keys); effects=[self._decode_effect(r) for r in db.execute(f"SELECT * FROM environment_effects WHERE campaign_id=? AND active=1 AND target_key IN ({marks}) ORDER BY effect_type,target_key",(campaign_id,*target_keys)).fetchall()]
        weather=[]
        if location_id:
            weather=[dict(r) for r in db.execute("SELECT * FROM environment_weather WHERE campaign_id=? AND ((scope_type='location' AND scope_id=?) OR scope_type='world') ORDER BY CASE scope_type WHEN 'location' THEN 0 ELSE 1 END",(campaign_id,location_id)).fetchall()]
        else: weather=[dict(r) for r in db.execute("SELECT * FROM environment_weather WHERE campaign_id=? ORDER BY scope_type,scope_id LIMIT 50",(campaign_id,)).fetchall()]
        for w in weather: w["state"]=self.e._loads(w.pop("state_json"))
        return {"campaign_id":campaign_id,"location_id":location_id,"targets":targets,"effects":effects,"weather":weather,"hazard_count":sum(1 for e in effects if e["effect_type"] in {"fire","smoke","water","gas","blight","corrosion","corruption","heat","cold","disease","electricity","explosion","drought"} and float(e["intensity"])>0.05)}

    def snapshot(self, campaign_id: str, *, location_id: str | None = None, limit: int = 100) -> dict[str, Any]:
        with self.e._db() as db: return self.snapshot_db(db,campaign_id,location_id=location_id,limit=limit)

    def public_summary_db(self, db: sqlite3.Connection, campaign_id: str, *, location_id: str | None = None) -> dict[str, Any]:
        """Project only public, location-level environment context.

        Raw targets/properties/state stay on the trusted environment dispatcher.
        Scene features, tiles, and actor targets require explicit perception or a
        validated inspect interaction before they can enter player context.
        """
        weather_rows: list[dict[str, Any]] = []
        if location_id:
            rows = db.execute(
                """SELECT scope_type,scope_id,condition,precipitation,
                          precipitation_intensity,temperature_c,wind_speed,
                          wind_direction,humidity,visibility,severity,generated_world_time
                   FROM environment_weather
                   WHERE campaign_id=? AND ((scope_type='location' AND scope_id=?) OR scope_type='world')
                   ORDER BY CASE scope_type WHEN 'location' THEN 0 ELSE 1 END""",
                (campaign_id, location_id),
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT scope_type,scope_id,condition,precipitation,
                          precipitation_intensity,temperature_c,wind_speed,
                          wind_direction,humidity,visibility,severity,generated_world_time
                   FROM environment_weather
                   WHERE campaign_id=? AND scope_type='world'
                   ORDER BY scope_id LIMIT 10""",
                (campaign_id,),
            ).fetchall()
        weather_rows = [dict(row) for row in rows]
        visible_effects: list[dict[str, Any]] = []
        if location_id:
            visible_effects = [
                dict(row)
                for row in db.execute(
                    """SELECT e.effect_type,MAX(e.intensity) AS intensity,COUNT(*) AS target_count
                       FROM environment_effects e
                       JOIN environment_targets t ON t.campaign_id=e.campaign_id AND t.target_key=e.target_key
                       WHERE e.campaign_id=? AND e.active=1 AND t.active=1
                         AND t.target_type='location' AND t.location_id=?
                       GROUP BY e.effect_type ORDER BY e.effect_type""",
                    (campaign_id, location_id),
                ).fetchall()
            ]
        return {
            "campaign_id": campaign_id,
            "location_id": location_id,
            "weather": weather_rows,
            "location_effects": visible_effects,
            "projection": "WE-ENV-PUBLIC-1.0",
        }

    def consideration_value_db(self, db: sqlite3.Connection, campaign_id: str, location_id: str | None, effect_type: str) -> float:
        if not location_id:return 0.0
        effect_type=str(effect_type).lower()
        if effect_type in {"weather","severe_weather"}:
            wx=db.execute("SELECT severity FROM environment_weather WHERE campaign_id=? AND scope_type='location' AND scope_id=?",(campaign_id,location_id)).fetchone();return self._unit(wx["severity"] if wx else 0.0)
        row=db.execute("""SELECT MAX(e.intensity) v FROM environment_effects e JOIN environment_targets t ON t.campaign_id=e.campaign_id AND t.target_key=e.target_key WHERE e.campaign_id=? AND e.active=1 AND t.location_id=? AND e.effect_type=?""",(campaign_id,location_id,effect_type)).fetchone(); value=self._unit(row["v"] if row and row["v"] is not None else 0.0)
        if effect_type in {"cold","heat"}:
            wx=db.execute("SELECT temperature_c FROM environment_weather WHERE campaign_id=? AND scope_type='location' AND scope_id=?",(campaign_id,location_id)).fetchone()
            if wx:
                temp=float(wx["temperature_c"]); weather_value=max(0.0,min(1.0,(-temp-5.0)/25.0)) if effect_type=="cold" else max(0.0,min(1.0,(temp-30.0)/20.0));value=max(value,weather_value)
        return value

    def world_weather_db(self, db: sqlite3.Connection, campaign_id: str) -> str | None:
        row=db.execute("SELECT condition FROM environment_weather WHERE campaign_id=? AND scope_type='world' AND scope_id='global'",(campaign_id,)).fetchone(); return str(row["condition"]) if row else None

    def dispatch(self, operation: str, campaign_id: str, payload: dict[str, Any] | None = None) -> Any:
        p=dict(payload or {}); operation=str(operation or "").strip().lower()
        if operation=="save_material": return self.save_material(campaign_id,**p)
        if operation=="bind_target": return self.bind_target(campaign_id,p["target"] if "target" in p else p)
        if operation=="set_properties": return self.set_properties(campaign_id,p["target"],p.get("properties") or {})
        if operation=="apply_effect":
            effect_type=p.pop("effect_type");target=p.pop("target");return self.apply_effect(campaign_id,effect_type,target,**p)
        if operation=="clear_effect":
            effect_type=p.pop("effect_type");target=p.pop("target");return self.clear_effect(campaign_id,effect_type,target,**p)
        if operation=="interact": return self.interact(campaign_id,**p)
        if operation=="set_disaster_config": return self.set_disaster_config(campaign_id,**p)
        if operation=="snapshot": return self.snapshot(campaign_id,**p)
        raise ValueError(f"unknown environment operation: {operation}")
