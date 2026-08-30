from __future__ import annotations

import hashlib
import math
import sqlite3
from collections import deque
from datetime import date, datetime, timedelta
from typing import Any, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import WorldEngine


RULES_SCHEMA = r'''
CREATE TABLE IF NOT EXISTS rules_config (
    campaign_id TEXT PRIMARY KEY,
    rules_version TEXT NOT NULL DEFAULT '2024' CHECK(rules_version IN ('2014','2024')),
    grid_feet INTEGER NOT NULL DEFAULT 5 CHECK(grid_feet BETWEEN 1 AND 20),
    last_dawn_date TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rule_actor_profiles (
    campaign_id TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('character','npc')),
    actor_id TEXT NOT NULL,
    rules_version TEXT NOT NULL DEFAULT '2024' CHECK(rules_version IN ('2014','2024')),
    spellcasting_ability TEXT,
    save_proficiencies_json TEXT NOT NULL DEFAULT '[]',
    skill_proficiencies_json TEXT NOT NULL DEFAULT '[]',
    temp_hp INTEGER NOT NULL DEFAULT 0 CHECK(temp_hp >= 0),
    death_successes INTEGER NOT NULL DEFAULT 0 CHECK(death_successes BETWEEN 0 AND 3),
    death_failures INTEGER NOT NULL DEFAULT 0 CHECK(death_failures BETWEEN 0 AND 3),
    stable INTEGER NOT NULL DEFAULT 0,
    resistances_json TEXT NOT NULL DEFAULT '[]',
    immunities_json TEXT NOT NULL DEFAULT '[]',
    vulnerabilities_json TEXT NOT NULL DEFAULT '[]',
    movement_cells INTEGER NOT NULL DEFAULT 6 CHECK(movement_cells >= 0),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,actor_kind,actor_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rule_objects (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    object_kind TEXT NOT NULL CHECK(object_kind IN ('spell','feat','class_feature','subclass_feature','species_feature','monster_feature','magic_item','ritual','condition','other')),
    rules_version TEXT NOT NULL DEFAULT '2024' CHECK(rules_version IN ('2014','2024','both')),
    level INTEGER,
    source TEXT,
    source_ref TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rule_activities (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    object_id TEXT,
    name TEXT NOT NULL,
    activity_type TEXT NOT NULL CHECK(activity_type IN ('attack','save','damage','heal','utility','summon','transform','teleport')),
    activation TEXT NOT NULL DEFAULT 'action' CHECK(activation IN ('action','bonus_action','reaction','free','none','minute','hour','ritual')),
    rules_version TEXT NOT NULL DEFAULT '2024' CHECK(rules_version IN ('2014','2024','both')),
    attack_json TEXT NOT NULL DEFAULT '{}',
    save_json TEXT NOT NULL DEFAULT '{}',
    damage_json TEXT NOT NULL DEFAULT '[]',
    healing_json TEXT NOT NULL DEFAULT '[]',
    targeting_json TEXT NOT NULL DEFAULT '{}',
    consumption_json TEXT NOT NULL DEFAULT '[]',
    effects_json TEXT NOT NULL DEFAULT '[]',
    scaling_json TEXT NOT NULL DEFAULT '{}',
    special_json TEXT NOT NULL DEFAULT '{}',
    tags_json TEXT NOT NULL DEFAULT '[]',
    world_event_type TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,object_id) REFERENCES rule_objects(campaign_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rule_actor_objects (
    campaign_id TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('character','npc')),
    actor_id TEXT NOT NULL,
    object_id TEXT NOT NULL,
    source TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    granted_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,actor_kind,actor_id,object_id),
    FOREIGN KEY(campaign_id,object_id) REFERENCES rule_objects(campaign_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rule_resources (
    campaign_id TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('character','npc')),
    actor_id TEXT NOT NULL,
    resource_key TEXT NOT NULL,
    current_value INTEGER NOT NULL DEFAULT 0,
    max_value INTEGER NOT NULL DEFAULT 0 CHECK(max_value >= 0),
    recovery TEXT NOT NULL DEFAULT 'long_rest' CHECK(recovery IN ('turn_start','short_rest','long_rest','dawn','never')),
    recovery_amount INTEGER,
    last_recovery_marker TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,actor_kind,actor_id,resource_key),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rule_effects (
    campaign_id TEXT NOT NULL,
    effect_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    source_activity_id TEXT,
    source_actor_kind TEXT,
    source_actor_id TEXT,
    target_kind TEXT NOT NULL CHECK(target_kind IN ('character','npc')),
    target_id TEXT NOT NULL,
    name TEXT NOT NULL,
    condition_name TEXT,
    condition_owned INTEGER NOT NULL DEFAULT 0,
    modifiers_json TEXT NOT NULL DEFAULT '{}',
    stacking TEXT NOT NULL DEFAULT 'replace' CHECK(stacking IN ('replace','stack','highest','ignore')),
    concentration INTEGER NOT NULL DEFAULT 0,
    concentration_owner_kind TEXT,
    concentration_owner_id TEXT,
    expires_on TEXT NOT NULL DEFAULT 'manual' CHECK(expires_on IN ('manual','world_time','turn_start','turn_end','short_rest','long_rest','combat_end')),
    expires_world_time TEXT,
    expires_combat_id TEXT,
    expires_round INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    ended_at TEXT,
    end_reason TEXT,
    PRIMARY KEY(campaign_id,effect_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_rule_effects_target ON rule_effects(campaign_id,target_kind,target_id,active);
CREATE INDEX IF NOT EXISTS idx_rule_effects_concentration ON rule_effects(campaign_id,concentration_owner_kind,concentration_owner_id,active);

CREATE TABLE IF NOT EXISTS rule_reactions (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    owner_kind TEXT NOT NULL CHECK(owner_kind IN ('character','npc')),
    owner_id TEXT NOT NULL,
    trigger TEXT NOT NULL,
    name TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    conditions_json TEXT NOT NULL DEFAULT '{}',
    consumption_json TEXT NOT NULL DEFAULT '[]',
    effect_json TEXT NOT NULL DEFAULT '{}',
    consumes_reaction INTEGER NOT NULL DEFAULT 1,
    selection_mode TEXT NOT NULL DEFAULT 'automatic' CHECK(selection_mode IN ('automatic','prompt')),
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_rule_reactions_trigger ON rule_reactions(campaign_id,trigger,owner_kind,owner_id,enabled,priority,id);

CREATE TABLE IF NOT EXISTS rule_turn_state (
    campaign_id TEXT NOT NULL,
    combat_id TEXT NOT NULL,
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('character','npc')),
    actor_id TEXT NOT NULL,
    round INTEGER NOT NULL,
    action_available INTEGER NOT NULL DEFAULT 1,
    bonus_available INTEGER NOT NULL DEFAULT 1,
    reaction_available INTEGER NOT NULL DEFAULT 1,
    movement_remaining INTEGER NOT NULL DEFAULT 6,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,combat_id,actor_kind,actor_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rule_advancements (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    class_id TEXT NOT NULL,
    level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 20),
    rules_version TEXT NOT NULL DEFAULT '2024' CHECK(rules_version IN ('2014','2024','both')),
    grant_objects_json TEXT NOT NULL DEFAULT '[]',
    resources_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rule_summons (
    campaign_id TEXT NOT NULL,
    summon_id TEXT NOT NULL,
    source_activity_id TEXT NOT NULL,
    owner_kind TEXT NOT NULL CHECK(owner_kind IN ('character','npc')),
    owner_id TEXT NOT NULL,
    npc_id TEXT NOT NULL,
    scene_id TEXT,
    combat_id TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    ended_at TEXT,
    PRIMARY KEY(campaign_id,summon_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rule_transform_snapshots (
    campaign_id TEXT NOT NULL,
    effect_id TEXT NOT NULL,
    target_kind TEXT NOT NULL CHECK(target_kind IN ('character','npc')),
    target_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    PRIMARY KEY(campaign_id,effect_id),
    FOREIGN KEY(campaign_id,effect_id) REFERENCES rule_effects(campaign_id,effect_id) ON DELETE CASCADE
);
'''


class RulesKernel:
    OBJECT_KINDS = {"spell","feat","class_feature","subclass_feature","species_feature","monster_feature","magic_item","ritual","condition","other"}
    ACTIVITY_TYPES = {"attack","save","damage","heal","utility","summon","transform","teleport"}
    ACTIVATIONS = {"action","bonus_action","reaction","free","none","minute","hour","ritual"}
    RULE_VERSIONS = {"2014","2024","both"}
    TRIGGERS = {"before_activity","on_cast","after_attack_roll","after_hit","before_damage","after_damage","before_save","after_save","turn_start","turn_end","on_rest","on_death"}
    RECOVERY = {"turn_start","short_rest","long_rest","dawn","never"}
    MAX_TARGETS = 50
    MAX_REACTIONS = 20
    MAX_EFFECTS_PER_ACTIVITY = 50
    MAX_SUMMONS_PER_ACTIVITY = 12

    def __init__(self, engine: "WorldEngine"):
        self.e = engine

    @staticmethod
    def _latest_processed_dawn_marker(world_time: str) -> str:
        """Return the most recent dawn that has already occurred at world_time."""
        current=datetime.fromisoformat(world_time)
        marker=current.date() if current.hour>=6 else current.date()-timedelta(days=1)
        return marker.isoformat()

    @staticmethod
    def _kind(kind: str) -> str:
        if kind not in {"character","npc"}:
            raise ValueError("actor kind must be character or npc")
        return kind

    def _config_db(self, db: sqlite3.Connection, campaign_id: str, *, create: bool=True) -> dict[str, Any]:
        row = db.execute("SELECT * FROM rules_config WHERE campaign_id=?", (campaign_id,)).fetchone()
        if row:
            return dict(row)
        campaign=db.execute("SELECT world_time FROM campaigns WHERE id=?",(campaign_id,)).fetchone()
        if not campaign:
            raise KeyError(f"unknown campaign: {campaign_id}")
        last_dawn=self._latest_processed_dawn_marker(campaign["world_time"])
        default={"campaign_id":campaign_id,"rules_version":"2024","grid_feet":5,"last_dawn_date":last_dawn,"updated_at":None}
        if not create:
            return default
        now=self.e._now()
        db.execute("INSERT INTO rules_config(campaign_id,rules_version,grid_feet,last_dawn_date,updated_at) VALUES(?,'2024',5,?,?)", (campaign_id,last_dawn,now))
        return dict(db.execute("SELECT * FROM rules_config WHERE campaign_id=?", (campaign_id,)).fetchone())

    def configure(self, campaign_id: str, *, rules_version: str = "2024", grid_feet: int = 5) -> dict[str, Any]:
        if rules_version not in {"2014","2024"}:
            raise ValueError("rules_version must be 2014 or 2024")
        if not 1 <= int(grid_feet) <= 20:
            raise ValueError("grid_feet must be 1..20")
        self.e._ensure_campaign_exists(campaign_id)
        with self.e._write_db() as db:
            campaign=db.execute("SELECT world_time FROM campaigns WHERE id=?",(campaign_id,)).fetchone()
            if not campaign:
                raise KeyError(f"unknown campaign: {campaign_id}")
            last_dawn=self._latest_processed_dawn_marker(campaign["world_time"])
            db.execute("""INSERT INTO rules_config(campaign_id,rules_version,grid_feet,last_dawn_date,updated_at) VALUES(?,?,?,?,?)
                          ON CONFLICT(campaign_id) DO UPDATE SET rules_version=excluded.rules_version,grid_feet=excluded.grid_feet,updated_at=excluded.updated_at""",
                       (campaign_id, rules_version, int(grid_feet), last_dawn, self.e._now()))
        return self.get_config(campaign_id)

    def get_config(self, campaign_id: str) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        with self.e._db() as db:
            return self._config_db(db, campaign_id, create=False)

    def _profile_db(self, db: sqlite3.Connection, campaign_id: str, actor_kind: str, actor_id: str) -> dict[str, Any]:
        row = db.execute("SELECT * FROM rule_actor_profiles WHERE campaign_id=? AND actor_kind=? AND actor_id=?", (campaign_id,actor_kind,actor_id)).fetchone()
        if not row:
            cfg = self._config_db(db, campaign_id, create=False)
            return {"campaign_id":campaign_id,"actor_kind":actor_kind,"actor_id":actor_id,"rules_version":cfg["rules_version"],"spellcasting_ability":None,"save_proficiencies":[],"skill_proficiencies":[],"temp_hp":0,"death_successes":0,"death_failures":0,"stable":False,"resistances":[],"immunities":[],"vulnerabilities":[],"movement_cells":6,"metadata":{}}
        out = dict(row)
        for key in ("save_proficiencies","skill_proficiencies","resistances","immunities","vulnerabilities"):
            out[key] = self.e._loads(out.pop(key + "_json"))
        out["metadata"] = self.e._loads(out.pop("metadata_json"))
        out["stable"] = bool(out["stable"])
        return out

    def _upsert_profile_db(self, db: sqlite3.Connection, campaign_id: str, actor_kind: str, actor_id: str, **changes: Any) -> dict[str, Any]:
        actor_kind = self._kind(actor_kind)
        self.e._get_actor_db(db, campaign_id, actor_kind, actor_id)
        old = self._profile_db(db, campaign_id, actor_kind, actor_id)
        data = dict(old); data.update(changes)
        if data["rules_version"] not in {"2014","2024"}:
            raise ValueError("actor rules_version must be 2014 or 2024")
        if int(data["temp_hp"]) < 0:
            raise ValueError("temp_hp must be non-negative")
        if not 0 <= int(data["death_successes"]) <= 3 or not 0 <= int(data["death_failures"]) <= 3:
            raise ValueError("death-save counts must be 0..3")
        db.execute("""INSERT INTO rule_actor_profiles(campaign_id,actor_kind,actor_id,rules_version,spellcasting_ability,save_proficiencies_json,skill_proficiencies_json,temp_hp,death_successes,death_failures,stable,resistances_json,immunities_json,vulnerabilities_json,movement_cells,metadata_json,updated_at)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(campaign_id,actor_kind,actor_id) DO UPDATE SET rules_version=excluded.rules_version,spellcasting_ability=excluded.spellcasting_ability,save_proficiencies_json=excluded.save_proficiencies_json,skill_proficiencies_json=excluded.skill_proficiencies_json,temp_hp=excluded.temp_hp,death_successes=excluded.death_successes,death_failures=excluded.death_failures,stable=excluded.stable,resistances_json=excluded.resistances_json,immunities_json=excluded.immunities_json,vulnerabilities_json=excluded.vulnerabilities_json,movement_cells=excluded.movement_cells,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                   (campaign_id,actor_kind,actor_id,data["rules_version"],data.get("spellcasting_ability"),self.e._dumps(sorted(set(data.get("save_proficiencies",[])))),self.e._dumps(sorted(set(data.get("skill_proficiencies",[])))),int(data["temp_hp"]),int(data["death_successes"]),int(data["death_failures"]),int(bool(data.get("stable",False))),self.e._dumps(sorted(set(data.get("resistances",[])))),self.e._dumps(sorted(set(data.get("immunities",[])))),self.e._dumps(sorted(set(data.get("vulnerabilities",[])))),int(data.get("movement_cells",6)),self.e._dumps(data.get("metadata",{})),self.e._now()))
        return self._profile_db(db, campaign_id, actor_kind, actor_id)

    def set_actor_profile(self, campaign_id: str, actor_kind: str, actor_id: str, **changes: Any) -> dict[str, Any]:
        with self.e._write_db() as db:
            return self._upsert_profile_db(db, campaign_id, actor_kind, actor_id, **changes)

    def define_object(self, campaign_id: str, object_id: str, name: str, object_kind: str, **data: Any) -> dict[str, Any]:
        if object_kind not in self.OBJECT_KINDS:
            raise ValueError("invalid rule object kind")
        version = str(data.get("rules_version","2024"))
        if version not in self.RULE_VERSIONS:
            raise ValueError("rules_version must be 2014, 2024, or both")
        object_id = self.e._clean_id(object_id)
        self.e._ensure_campaign_exists(campaign_id)
        with self.e._write_db() as db:
            db.execute("""INSERT INTO rule_objects(campaign_id,id,name,object_kind,rules_version,level,source,source_ref,tags_json,metadata_json,updated_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(campaign_id,id) DO UPDATE SET name=excluded.name,object_kind=excluded.object_kind,rules_version=excluded.rules_version,level=excluded.level,source=excluded.source,source_ref=excluded.source_ref,tags_json=excluded.tags_json,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                       (campaign_id,object_id,name[:200],object_kind,version,data.get("level"),data.get("source"),data.get("source_ref"),self.e._dumps(data.get("tags",[])),self.e._dumps(data.get("metadata",{})),self.e._now()))
        return self.get_object(campaign_id, object_id)

    def get_object(self, campaign_id: str, object_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            row = db.execute("SELECT * FROM rule_objects WHERE campaign_id=? AND id=?",(campaign_id,object_id)).fetchone()
        if not row:
            raise KeyError(f"unknown rule object: {object_id}")
        out=dict(row); out["tags"]=self.e._loads(out.pop("tags_json")); out["metadata"]=self.e._loads(out.pop("metadata_json")); return out

    def define_activity(self, campaign_id: str, activity_id: str, name: str, activity_type: str, **data: Any) -> dict[str, Any]:
        if activity_type not in self.ACTIVITY_TYPES:
            raise ValueError("invalid activity_type")
        activation = str(data.get("activation","action"))
        if activation not in self.ACTIVATIONS:
            raise ValueError("invalid activation")
        version = str(data.get("rules_version","2024"))
        if version not in self.RULE_VERSIONS:
            raise ValueError("invalid rules_version")
        activity_id = self.e._clean_id(activity_id)
        object_id = data.get("object_id")
        target = dict(data.get("targeting",{}))
        max_targets = int(target.get("max_targets", 1 if target.get("mode","single") == "single" else self.MAX_TARGETS))
        if not 1 <= max_targets <= self.MAX_TARGETS:
            raise ValueError(f"targeting.max_targets must be 1..{self.MAX_TARGETS}")
        target["max_targets"] = max_targets
        effects=list(data.get("effects",[])); consumption=list(data.get("consumption",[]))
        if len(effects) > self.MAX_EFFECTS_PER_ACTIVITY:
            raise ValueError(f"effects cap is {self.MAX_EFFECTS_PER_ACTIVITY}")
        with self.e._write_db() as db:
            if object_id and not db.execute("SELECT 1 FROM rule_objects WHERE campaign_id=? AND id=?",(campaign_id,object_id)).fetchone():
                raise KeyError(f"unknown rule object: {object_id}")
            db.execute("""INSERT INTO rule_activities(campaign_id,id,object_id,name,activity_type,activation,rules_version,attack_json,save_json,damage_json,healing_json,targeting_json,consumption_json,effects_json,scaling_json,special_json,tags_json,world_event_type,enabled,updated_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                          ON CONFLICT(campaign_id,id) DO UPDATE SET object_id=excluded.object_id,name=excluded.name,activity_type=excluded.activity_type,activation=excluded.activation,rules_version=excluded.rules_version,attack_json=excluded.attack_json,save_json=excluded.save_json,damage_json=excluded.damage_json,healing_json=excluded.healing_json,targeting_json=excluded.targeting_json,consumption_json=excluded.consumption_json,effects_json=excluded.effects_json,scaling_json=excluded.scaling_json,special_json=excluded.special_json,tags_json=excluded.tags_json,world_event_type=excluded.world_event_type,enabled=excluded.enabled,updated_at=excluded.updated_at""",
                       (campaign_id,activity_id,object_id,name[:200],activity_type,activation,version,self.e._dumps(data.get("attack",{})),self.e._dumps(data.get("save",{})),self.e._dumps(data.get("damage",[])),self.e._dumps(data.get("healing",[])),self.e._dumps(target),self.e._dumps(consumption),self.e._dumps(effects),self.e._dumps(data.get("scaling",{})),self.e._dumps(data.get("special",{})),self.e._dumps(data.get("tags",[])),data.get("world_event_type"),int(bool(data.get("enabled",True))),self.e._now()))
        return self.get_activity(campaign_id, activity_id)

    def _decode_activity(self, row: sqlite3.Row | dict[str,Any]) -> dict[str,Any]:
        out=dict(row)
        for key in ("attack","save","targeting","scaling","special"):
            out[key]=self.e._loads(out.pop(key+"_json"))
        for key in ("damage","healing","consumption","effects","tags"):
            out[key]=self.e._loads(out.pop(key+"_json"))
        out["enabled"]=bool(out["enabled"])
        return out

    def get_activity(self, campaign_id: str, activity_id: str) -> dict[str,Any]:
        with self.e._db() as db:
            row=db.execute("SELECT * FROM rule_activities WHERE campaign_id=? AND id=?",(campaign_id,activity_id)).fetchone()
        if not row:
            raise KeyError(f"unknown activity: {activity_id}")
        return self._decode_activity(row)

    def grant_object(self, campaign_id: str, actor_kind: str, actor_id: str, object_id: str, *, source: str|None=None, metadata: dict[str,Any]|None=None) -> dict[str,Any]:
        actor_kind=self._kind(actor_kind)
        with self.e._write_db() as db:
            self.e._get_actor_db(db,campaign_id,actor_kind,actor_id)
            if not db.execute("SELECT 1 FROM rule_objects WHERE campaign_id=? AND id=?",(campaign_id,object_id)).fetchone():
                raise KeyError(f"unknown rule object: {object_id}")
            db.execute("""INSERT INTO rule_actor_objects(campaign_id,actor_kind,actor_id,object_id,source,metadata_json,granted_at) VALUES(?,?,?,?,?,?,?)
                          ON CONFLICT(campaign_id,actor_kind,actor_id,object_id) DO UPDATE SET source=excluded.source,metadata_json=excluded.metadata_json""",
                       (campaign_id,actor_kind,actor_id,object_id,source,self.e._dumps(metadata or {}),self.e._now()))
        return {"campaign_id":campaign_id,"actor_kind":actor_kind,"actor_id":actor_id,"object_id":object_id,"source":source}

    def set_resource(self, campaign_id: str, actor_kind: str, actor_id: str, resource_key: str, current: int, maximum: int, *, recovery: str="long_rest", recovery_amount: int|None=None) -> dict[str,Any]:
        actor_kind=self._kind(actor_kind); current=int(current); maximum=int(maximum)
        if recovery not in self.RECOVERY:
            raise ValueError("invalid recovery policy")
        if maximum < 0 or current < 0 or current > maximum:
            raise ValueError("resource current/max invalid")
        if recovery_amount is not None and int(recovery_amount) < 0:
            raise ValueError("recovery_amount must be non-negative")
        with self.e._write_db() as db:
            self.e._get_actor_db(db,campaign_id,actor_kind,actor_id)
            db.execute("""INSERT INTO rule_resources(campaign_id,actor_kind,actor_id,resource_key,current_value,max_value,recovery,recovery_amount,last_recovery_marker,updated_at)
                          VALUES(?,?,?,?,?,?,?,?,NULL,?)
                          ON CONFLICT(campaign_id,actor_kind,actor_id,resource_key) DO UPDATE SET current_value=excluded.current_value,max_value=excluded.max_value,recovery=excluded.recovery,recovery_amount=excluded.recovery_amount,updated_at=excluded.updated_at""",
                       (campaign_id,actor_kind,actor_id,resource_key[:100],current,maximum,recovery,recovery_amount,self.e._now()))
        return self.get_resource(campaign_id,actor_kind,actor_id,resource_key)

    def get_resource(self, campaign_id: str, actor_kind: str, actor_id: str, resource_key: str) -> dict[str,Any]:
        with self.e._db() as db:
            row=db.execute("SELECT * FROM rule_resources WHERE campaign_id=? AND actor_kind=? AND actor_id=? AND resource_key=?",(campaign_id,actor_kind,actor_id,resource_key)).fetchone()
        if not row:
            raise KeyError(f"unknown resource: {resource_key}")
        return dict(row)

    def define_reaction(self, campaign_id: str, reaction_id: str, owner_kind: str, owner_id: str, trigger: str, name: str, *, conditions: dict[str,Any]|None=None, consumption: Sequence[dict[str,Any]]=(), effect: dict[str,Any]|None=None, priority: int=100, consumes_reaction: bool=True, selection_mode: str="automatic", enabled: bool=True) -> dict[str,Any]:
        owner_kind=self._kind(owner_kind)
        if trigger not in self.TRIGGERS:
            raise ValueError("invalid reaction trigger")
        if selection_mode not in {"automatic","prompt"}:
            raise ValueError("selection_mode must be automatic or prompt")
        with self.e._write_db() as db:
            self.e._get_actor_db(db,campaign_id,owner_kind,owner_id)
            db.execute("""INSERT INTO rule_reactions(campaign_id,id,owner_kind,owner_id,trigger,name,priority,conditions_json,consumption_json,effect_json,consumes_reaction,selection_mode,enabled,updated_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                          ON CONFLICT(campaign_id,id) DO UPDATE SET owner_kind=excluded.owner_kind,owner_id=excluded.owner_id,trigger=excluded.trigger,name=excluded.name,priority=excluded.priority,conditions_json=excluded.conditions_json,consumption_json=excluded.consumption_json,effect_json=excluded.effect_json,consumes_reaction=excluded.consumes_reaction,selection_mode=excluded.selection_mode,enabled=excluded.enabled,updated_at=excluded.updated_at""",
                       (campaign_id,self.e._clean_id(reaction_id),owner_kind,owner_id,trigger,name[:200],int(priority),self.e._dumps(conditions or {}),self.e._dumps(list(consumption)),self.e._dumps(effect or {}),int(bool(consumes_reaction)),selection_mode,int(bool(enabled)),self.e._now()))
        return {"campaign_id":campaign_id,"id":reaction_id,"owner_kind":owner_kind,"owner_id":owner_id,"trigger":trigger,"name":name,"selection_mode":selection_mode}

    def define_advancement(self, campaign_id: str, advancement_id: str, class_id: str, level: int, *, rules_version: str="2024", grant_objects: Sequence[str]=(), resources: dict[str,Any]|None=None, metadata: dict[str,Any]|None=None) -> dict[str,Any]:
        if not 1 <= int(level) <= 20:
            raise ValueError("level must be 1..20")
        if rules_version not in self.RULE_VERSIONS:
            raise ValueError("invalid rules_version")
        with self.e._write_db() as db:
            for object_id in grant_objects:
                if not db.execute("SELECT 1 FROM rule_objects WHERE campaign_id=? AND id=?",(campaign_id,object_id)).fetchone():
                    raise KeyError(f"unknown rule object: {object_id}")
            db.execute("""INSERT INTO rule_advancements(campaign_id,id,class_id,level,rules_version,grant_objects_json,resources_json,metadata_json,updated_at)
                          VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(campaign_id,id) DO UPDATE SET class_id=excluded.class_id,level=excluded.level,rules_version=excluded.rules_version,grant_objects_json=excluded.grant_objects_json,resources_json=excluded.resources_json,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                       (campaign_id,self.e._clean_id(advancement_id),class_id[:100],int(level),rules_version,self.e._dumps(list(grant_objects)),self.e._dumps(resources or {}),self.e._dumps(metadata or {}),self.e._now()))
        return {"campaign_id":campaign_id,"id":advancement_id,"class_id":class_id,"level":int(level),"rules_version":rules_version}

    # ---------- actor rules / modifiers ----------

    def _actor_mod(self, actor: dict[str,Any], ability: str|None) -> int:
        if not ability or ability == "none":
            return 0
        ability=ability.lower(); source=actor.get("abilities") if "abilities" in actor else actor.get("stats",{})
        if not isinstance(source,dict):
            return 0
        for key in (ability,ability+"_mod",ability.upper()):
            value=source.get(key)
            if isinstance(value,(int,float)):
                return int(value)
        return 0

    def _prof_bonus(self, actor: dict[str,Any]) -> int:
        if isinstance(actor.get("proficiency_bonus"),(int,float)):
            return int(actor["proficiency_bonus"])
        stats=actor.get("stats",{})
        if isinstance(stats,dict) and isinstance(stats.get("proficiency_bonus"),(int,float)):
            return int(stats["proficiency_bonus"])
        return 2

    def _active_modifiers_db(self, db: sqlite3.Connection, campaign_id: str, actor_kind: str, actor_id: str) -> dict[str,Any]:
        out={"ac_bonus":0,"attack_bonus":0,"damage_bonus":0,"save_bonus":{},"resistances":set(),"immunities":set(),"vulnerabilities":set(),"advantage":set(),"disadvantage":set()}
        rows=db.execute("SELECT modifiers_json FROM rule_effects WHERE campaign_id=? AND target_kind=? AND target_id=? AND active=1 ORDER BY created_at,effect_id",(campaign_id,actor_kind,actor_id)).fetchall()
        for row in rows:
            m=self.e._loads(row["modifiers_json"] or "{}")
            for key in ("ac_bonus","attack_bonus","damage_bonus"):
                out[key]+=int(m.get(key,0) or 0)
            save=m.get("save_bonus",{})
            if isinstance(save,(int,float)):
                out["save_bonus"]["all"]=out["save_bonus"].get("all",0)+int(save)
            elif isinstance(save,dict):
                for key,value in save.items():
                    out["save_bonus"][key]=out["save_bonus"].get(key,0)+int(value)
            for key in ("resistances","immunities","vulnerabilities","advantage","disadvantage"):
                out[key].update(str(x).lower() for x in (m.get(key,[]) or []))
        return out

    def _list_effects_db(self, db: sqlite3.Connection, campaign_id: str, actor_kind: str|None=None, actor_id: str|None=None, *, active_only: bool=True) -> list[dict[str,Any]]:
        sql="SELECT * FROM rule_effects WHERE campaign_id=?"; params:list[Any]=[campaign_id]
        if actor_kind is not None:
            sql+=" AND target_kind=?"; params.append(actor_kind)
        if actor_id is not None:
            sql+=" AND target_id=?"; params.append(actor_id)
        if active_only:
            sql+=" AND active=1"
        sql+=" ORDER BY created_at,effect_id"
        out=[]
        for row in db.execute(sql,params).fetchall():
            data=dict(row); data["modifiers"]=self.e._loads(data.pop("modifiers_json")); data["active"]=bool(data["active"]); data["concentration"]=bool(data["concentration"]); data["condition_owned"]=bool(data["condition_owned"]); out.append(data)
        return out

    def list_effects(self, campaign_id: str, actor_kind: str|None=None, actor_id: str|None=None, *, active_only: bool=True) -> list[dict[str,Any]]:
        with self.e._db() as db:
            return self._list_effects_db(db,campaign_id,actor_kind,actor_id,active_only=active_only)

    def get_actor_rules(self, campaign_id: str, actor_kind: str, actor_id: str) -> dict[str,Any]:
        actor_kind=self._kind(actor_kind)
        with self.e._db() as db:
            actor=self.e._get_actor_db(db,campaign_id,actor_kind,actor_id); profile=self._profile_db(db,campaign_id,actor_kind,actor_id)
            resources=[dict(r) for r in db.execute("SELECT * FROM rule_resources WHERE campaign_id=? AND actor_kind=? AND actor_id=? ORDER BY resource_key",(campaign_id,actor_kind,actor_id)).fetchall()]
            objects=[]
            rows=db.execute("""SELECT o.*,ao.source AS grant_source,ao.metadata_json AS grant_metadata_json FROM rule_actor_objects ao JOIN rule_objects o ON o.campaign_id=ao.campaign_id AND o.id=ao.object_id WHERE ao.campaign_id=? AND ao.actor_kind=? AND ao.actor_id=? ORDER BY o.name,o.id""",(campaign_id,actor_kind,actor_id)).fetchall()
            for row in rows:
                item=dict(row); item["tags"]=self.e._loads(item.pop("tags_json")); item["metadata"]=self.e._loads(item.pop("metadata_json")); item["grant_metadata"]=self.e._loads(item.pop("grant_metadata_json")); objects.append(item)
            effects=self._list_effects_db(db,campaign_id,actor_kind,actor_id)
        return {"actor":{"kind":actor_kind,"id":actor_id,"name":actor["name"]},"profile":profile,"resources":resources,"objects":objects,"effects":effects}

    def snapshot(self, campaign_id: str) -> dict[str,Any]:
        with self.e._db() as db:
            config=self._config_db(db,campaign_id,create=False)
            objects=[]
            for row in db.execute("SELECT * FROM rule_objects WHERE campaign_id=? ORDER BY id",(campaign_id,)).fetchall():
                item=dict(row); item["tags"]=self.e._loads(item.pop("tags_json")); item["metadata"]=self.e._loads(item.pop("metadata_json")); objects.append(item)
            activities=[self._decode_activity(row) for row in db.execute("SELECT * FROM rule_activities WHERE campaign_id=? ORDER BY id",(campaign_id,)).fetchall()]
            profiles=[]
            for row in db.execute("SELECT actor_kind,actor_id FROM rule_actor_profiles WHERE campaign_id=? ORDER BY actor_kind,actor_id",(campaign_id,)).fetchall(): profiles.append(self._profile_db(db,campaign_id,row["actor_kind"],row["actor_id"]))
            grants=[]
            for row in db.execute("SELECT * FROM rule_actor_objects WHERE campaign_id=? ORDER BY actor_kind,actor_id,object_id",(campaign_id,)).fetchall():
                item=dict(row); item["metadata"]=self.e._loads(item.pop("metadata_json")); grants.append(item)
            resources=[dict(row) for row in db.execute("SELECT * FROM rule_resources WHERE campaign_id=? ORDER BY actor_kind,actor_id,resource_key",(campaign_id,)).fetchall()]
            effects=self._list_effects_db(db,campaign_id,active_only=False)
            reactions=[]
            for row in db.execute("SELECT * FROM rule_reactions WHERE campaign_id=? ORDER BY priority,id",(campaign_id,)).fetchall():
                item=dict(row); item["conditions"]=self.e._loads(item.pop("conditions_json")); item["consumption"]=self.e._loads(item.pop("consumption_json")); item["effect"]=self.e._loads(item.pop("effect_json")); item["consumes_reaction"]=bool(item["consumes_reaction"]); item["enabled"]=bool(item["enabled"]); reactions.append(item)
            turn_state=[dict(row) for row in db.execute("SELECT * FROM rule_turn_state WHERE campaign_id=? ORDER BY combat_id,actor_kind,actor_id",(campaign_id,)).fetchall()]
            advancements=[]
            for row in db.execute("SELECT * FROM rule_advancements WHERE campaign_id=? ORDER BY class_id,level,id",(campaign_id,)).fetchall():
                item=dict(row); item["grant_objects"]=self.e._loads(item.pop("grant_objects_json")); item["resources"]=self.e._loads(item.pop("resources_json")); item["metadata"]=self.e._loads(item.pop("metadata_json")); advancements.append(item)
            summons=[dict(row) for row in db.execute("SELECT * FROM rule_summons WHERE campaign_id=? ORDER BY created_at,summon_id",(campaign_id,)).fetchall()]
        return {"config":config,"objects":objects,"activities":activities,"actor_profiles":profiles,"grants":grants,"resources":resources,"effects":effects,"reactions":reactions,"turn_state":turn_state,"advancements":advancements,"summons":summons}

    # ---------- action economy ----------

    def initialize_combat_db(self, db: sqlite3.Connection, campaign_id: str, combat_id: str, initiative: Sequence[dict[str,Any]], round_num: int = 1) -> None:
        db.execute("DELETE FROM rule_turn_state WHERE campaign_id=? AND combat_id=?",(campaign_id,combat_id))
        for entry in initiative:
            kind,actor_id=entry["kind"],entry["id"]; profile=self._profile_db(db,campaign_id,kind,actor_id)
            db.execute("""INSERT INTO rule_turn_state(campaign_id,combat_id,actor_kind,actor_id,round,action_available,bonus_available,reaction_available,movement_remaining,updated_at) VALUES(?,?,?,?,?,1,1,1,?,?)""",(campaign_id,combat_id,kind,actor_id,int(round_num),int(profile.get("movement_cells",6)),self.e._now()))
        if initiative:
            first=initiative[0]; self.reset_turn_state_db(db,campaign_id,combat_id,first["kind"],first["id"],round_num)

    def reset_turn_state_db(self, db: sqlite3.Connection, campaign_id: str, combat_id: str, actor_kind: str, actor_id: str, round_num: int) -> dict[str,Any]:
        profile=self._profile_db(db,campaign_id,actor_kind,actor_id)
        db.execute("""INSERT INTO rule_turn_state(campaign_id,combat_id,actor_kind,actor_id,round,action_available,bonus_available,reaction_available,movement_remaining,updated_at)
                      VALUES(?,?,?,?,?,1,1,1,?,?) ON CONFLICT(campaign_id,combat_id,actor_kind,actor_id) DO UPDATE SET round=excluded.round,action_available=1,bonus_available=1,reaction_available=1,movement_remaining=excluded.movement_remaining,updated_at=excluded.updated_at""",
                   (campaign_id,combat_id,actor_kind,actor_id,int(round_num),int(profile.get("movement_cells",6)),self.e._now()))
        self._recover_resources_db(db,campaign_id,actor_kind,actor_id,{"turn_start"},marker=f"{combat_id}:{round_num}:{actor_kind}:{actor_id}")
        self._expire_effects_db(db,campaign_id,actor_kind,actor_id,reason="turn_start",combat_id=combat_id,round_num=round_num)
        self._fire_reactions_db(db,campaign_id,"turn_start",{"actor_kind":actor_kind,"actor_id":actor_id,"combat_id":combat_id,"round":round_num},combat_id=combat_id,group_id=f"turn:{combat_id}:{round_num}:{actor_id}")
        row=db.execute("SELECT * FROM rule_turn_state WHERE campaign_id=? AND combat_id=? AND actor_kind=? AND actor_id=?",(campaign_id,combat_id,actor_kind,actor_id)).fetchone()
        return dict(row)

    def end_turn_db(self, db: sqlite3.Connection, campaign_id: str, combat_id: str, actor_kind: str, actor_id: str, round_num: int) -> None:
        self._fire_reactions_db(db,campaign_id,"turn_end",{"actor_kind":actor_kind,"actor_id":actor_id,"combat_id":combat_id,"round":round_num},combat_id=combat_id,group_id=f"turn-end:{combat_id}:{round_num}:{actor_id}")
        self._expire_effects_db(db,campaign_id,actor_kind,actor_id,reason="turn_end",combat_id=combat_id,round_num=round_num)

    def _consume_action_economy_db(self, db: sqlite3.Connection, campaign_id: str, combat_id: str|None, actor_kind: str, actor_id: str, activation: str) -> dict[str,Any]|None:
        if not combat_id or activation in {"none","free","minute","hour","ritual"}:
            return None
        combat=self.e._get_combat_db(db,campaign_id,combat_id)
        current=combat.get("current_turn") or {}
        if activation in {"action","bonus_action"} and (current.get("kind"),current.get("id")) != (actor_kind,actor_id):
            raise ValueError(f"{activation} may only be used on the actor's current turn")
        row=db.execute("SELECT * FROM rule_turn_state WHERE campaign_id=? AND combat_id=? AND actor_kind=? AND actor_id=?",(campaign_id,combat_id,actor_kind,actor_id)).fetchone()
        if not row:
            self.reset_turn_state_db(db,campaign_id,combat_id,actor_kind,actor_id,int(combat["round"])); row=db.execute("SELECT * FROM rule_turn_state WHERE campaign_id=? AND combat_id=? AND actor_kind=? AND actor_id=?",(campaign_id,combat_id,actor_kind,actor_id)).fetchone()
        col={"action":"action_available","bonus_action":"bonus_available","reaction":"reaction_available"}.get(activation)
        if col:
            if not bool(row[col]):
                raise ValueError(f"{activation} already spent")
            db.execute(f"UPDATE rule_turn_state SET {col}=0,updated_at=? WHERE campaign_id=? AND combat_id=? AND actor_kind=? AND actor_id=?",(self.e._now(),campaign_id,combat_id,actor_kind,actor_id))
        return {"activation":activation,"spent":bool(col),"combat_id":combat_id}

    def _recover_resources_db(self, db: sqlite3.Connection, campaign_id: str, actor_kind: str, actor_id: str, policies: set[str], *, marker: str|None=None) -> list[dict[str,Any]]:
        rows=db.execute("SELECT * FROM rule_resources WHERE campaign_id=? AND actor_kind=? AND actor_id=? ORDER BY resource_key",(campaign_id,actor_kind,actor_id)).fetchall(); out=[]
        for row in rows:
            if row["recovery"] not in policies:
                continue
            if marker and row["last_recovery_marker"] == marker:
                continue
            amount=row["recovery_amount"]
            new=int(row["max_value"]) if amount is None else min(int(row["max_value"]),int(row["current_value"])+int(amount))
            db.execute("UPDATE rule_resources SET current_value=?,last_recovery_marker=?,updated_at=? WHERE campaign_id=? AND actor_kind=? AND actor_id=? AND resource_key=?",(new,marker,self.e._now(),campaign_id,actor_kind,actor_id,row["resource_key"]))
            out.append({"resource":row["resource_key"],"old":int(row["current_value"]),"new":new,"policy":row["recovery"]})
        return out

    # ---------- effect lifecycle ----------

    def _duration_fields(self, db: sqlite3.Connection, campaign_id: str, duration: dict[str,Any], combat_id: str|None) -> tuple[str,str|None,str|None,int|None]:
        unit=str(duration.get("unit","manual")); value=float(duration.get("value",1) or 1)
        if unit in {"minute","minutes","hour","hours"}:
            row=db.execute("SELECT world_time FROM campaigns WHERE id=?",(campaign_id,)).fetchone(); start=datetime.fromisoformat(row["world_time"]); seconds=value*(3600 if unit.startswith("hour") else 60); return "world_time",(start+timedelta(seconds=seconds)).isoformat(),None,None
        if unit in {"round","rounds"}:
            if not combat_id:
                raise ValueError("round duration requires combat_id")
            combat=self.e._get_combat_db(db,campaign_id,combat_id); return "manual",None,combat_id,int(combat["round"])+max(1,int(value))
        if unit in {"turn_start","turn_end","short_rest","long_rest","combat_end"}:
            return unit,None,combat_id if unit=="combat_end" else None,None
        if unit not in {"manual","permanent"}:
            raise ValueError(f"unsupported effect duration unit: {unit}")
        return "manual",None,None,None

    def _stop_concentration_db(self, db: sqlite3.Connection, campaign_id: str, owner_kind: str, owner_id: str, reason: str, *, except_group: str|None=None) -> int:
        sql="SELECT effect_id,group_id FROM rule_effects WHERE campaign_id=? AND concentration=1 AND concentration_owner_kind=? AND concentration_owner_id=? AND active=1"; params:list[Any]=[campaign_id,owner_kind,owner_id]
        if except_group is not None:
            sql+=" AND group_id<>?"; params.append(except_group)
        rows=db.execute(sql,params).fetchall(); count=0
        for row in rows:
            count += int(self._end_effect_record_db(db,campaign_id,row["effect_id"],reason))
        return count

    def _restore_transform_db(self, db: sqlite3.Connection, campaign_id: str, effect_id: str) -> None:
        snap=db.execute("SELECT * FROM rule_transform_snapshots WHERE campaign_id=? AND effect_id=?",(campaign_id,effect_id)).fetchone()
        if not snap:
            return
        state=self.e._loads(snap["snapshot_json"]); table=self.e._actor_table(snap["target_kind"])
        allowed={"hp","max_hp","ac"}; sets=[]; values=[]
        for key in allowed:
            if key in state:
                sets.append(f"{key}=?"); values.append(state[key])
        if sets:
            values.extend([self.e._now(),campaign_id,snap["target_id"]]); db.execute(f"UPDATE {table} SET {','.join(sets)},updated_at=? WHERE campaign_id=? AND id=?",values)
        db.execute("DELETE FROM rule_transform_snapshots WHERE campaign_id=? AND effect_id=?",(campaign_id,effect_id))

    def _remove_summon_for_effect_db(self, db: sqlite3.Connection, campaign_id: str, effect_id: str) -> None:
        row=db.execute("SELECT * FROM rule_summons WHERE campaign_id=? AND summon_id=? AND active=1",(campaign_id,effect_id)).fetchone()
        if not row:
            return
        npc_id=row["npc_id"]
        if row["combat_id"]:
            combat_row=db.execute("SELECT initiative_json,participants_json,turn_index FROM combats WHERE campaign_id=? AND id=?",(campaign_id,row["combat_id"])).fetchone()
            if combat_row:
                initiative=self.e._loads(combat_row["initiative_json"]); participants=self.e._loads(combat_row["participants_json"]); old_index=int(combat_row["turn_index"])
                current=initiative[old_index] if initiative and 0<=old_index<len(initiative) else None
                initiative=[item for item in initiative if not (item.get("kind")=="npc" and item.get("id")==npc_id)]
                participants=[item for item in participants if not (item.get("kind")=="npc" and item.get("id")==npc_id)]
                if initiative:
                    if current and not (current.get("kind")=="npc" and current.get("id")==npc_id):
                        turn_index=next((idx for idx,item in enumerate(initiative) if item.get("kind")==current.get("kind") and item.get("id")==current.get("id")),min(old_index,len(initiative)-1))
                    else:
                        turn_index=min(old_index,len(initiative)-1)
                else:
                    turn_index=0
                db.execute("UPDATE combats SET initiative_json=?,participants_json=?,turn_index=?,updated_at=? WHERE campaign_id=? AND id=?",(self.e._dumps(initiative),self.e._dumps(participants),turn_index,self.e._now(),campaign_id,row["combat_id"]))
        db.execute("UPDATE rule_summons SET active=0,ended_at=? WHERE campaign_id=? AND summon_id=?",(self.e._now(),campaign_id,effect_id))
        db.execute("DELETE FROM scene_entities WHERE campaign_id=? AND actor_kind='npc' AND actor_id=?",(campaign_id,npc_id))
        db.execute("DELETE FROM combat_positions WHERE campaign_id=? AND actor_kind='npc' AND actor_id=?",(campaign_id,npc_id))
        db.execute("DELETE FROM rule_turn_state WHERE campaign_id=? AND actor_kind='npc' AND actor_id=?",(campaign_id,npc_id))
        db.execute("DELETE FROM rule_transform_snapshots WHERE campaign_id=? AND effect_id IN (SELECT effect_id FROM rule_effects WHERE campaign_id=? AND target_kind='npc' AND target_id=? AND effect_id<>?)",(campaign_id,campaign_id,npc_id,effect_id))
        db.execute("DELETE FROM rule_effects WHERE campaign_id=? AND target_kind='npc' AND target_id=? AND effect_id<>?",(campaign_id,npc_id,effect_id))
        db.execute("DELETE FROM rule_reactions WHERE campaign_id=? AND owner_kind='npc' AND owner_id=?",(campaign_id,npc_id))
        db.execute("DELETE FROM rule_resources WHERE campaign_id=? AND actor_kind='npc' AND actor_id=?",(campaign_id,npc_id))
        db.execute("DELETE FROM rule_actor_objects WHERE campaign_id=? AND actor_kind='npc' AND actor_id=?",(campaign_id,npc_id))
        db.execute("DELETE FROM rule_actor_profiles WHERE campaign_id=? AND actor_kind='npc' AND actor_id=?",(campaign_id,npc_id))
        db.execute("DELETE FROM visual_profiles WHERE campaign_id=? AND entity_kind='npc' AND entity_id=?",(campaign_id,npc_id))
        db.execute("DELETE FROM relationships WHERE campaign_id=? AND (source_id=? OR target_id=?)",(campaign_id,npc_id,npc_id))
        db.execute("DELETE FROM world_state WHERE campaign_id=? AND scope_type='npc' AND scope_id=?",(campaign_id,npc_id))
        db.execute("DELETE FROM inventories WHERE campaign_id=? AND owner_kind='npc' AND owner_id=?",(campaign_id,npc_id))
        db.execute("DELETE FROM ownership WHERE campaign_id=? AND owner_kind='npc' AND owner_id=?",(campaign_id,npc_id))
        db.execute("DELETE FROM npcs WHERE campaign_id=? AND id=?",(campaign_id,npc_id))

    def _end_effect_record_db(self, db: sqlite3.Connection, campaign_id: str, effect_id: str, reason: str) -> bool:
        row=db.execute("SELECT * FROM rule_effects WHERE campaign_id=? AND effect_id=? AND active=1",(campaign_id,effect_id)).fetchone()
        if not row:
            return False
        db.execute("UPDATE rule_effects SET active=0,ended_at=?,end_reason=? WHERE campaign_id=? AND effect_id=?",(self.e._now(),reason[:200],campaign_id,effect_id))
        self._restore_transform_db(db,campaign_id,effect_id); self._remove_summon_for_effect_db(db,campaign_id,effect_id)
        condition=row["condition_name"]
        if condition and bool(row["condition_owned"]):
            other=db.execute("SELECT 1 FROM rule_effects WHERE campaign_id=? AND target_kind=? AND target_id=? AND condition_name=? AND active=1 LIMIT 1",(campaign_id,row["target_kind"],row["target_id"],condition)).fetchone()
            if not other:
                actor=self.e._get_actor_db(db,campaign_id,row["target_kind"],row["target_id"]); conditions=set(actor.get("conditions",[])); conditions.discard(condition); table=self.e._actor_table(row["target_kind"]); db.execute(f"UPDATE {table} SET conditions_json=?,updated_at=? WHERE campaign_id=? AND id=?",(self.e._dumps(sorted(conditions)),self.e._now(),campaign_id,row["target_id"]))
        return True

    def end_effect(self, campaign_id: str, effect_id: str, *, reason: str="manual") -> dict[str,Any]:
        with self.e._write_db() as db:
            ended=self._end_effect_record_db(db,campaign_id,effect_id,reason)
            rev=None
            if ended:
                rev=self.e._next_revision(db,campaign_id); self.e._insert_event(db,campaign_id,rev,"rule_effect_ended",f"Effect ended: {effect_id}",payload={"effect_id":effect_id,"reason":reason})
        return {"campaign_id":campaign_id,"effect_id":effect_id,"ended":ended,"revision":rev}

    def _expire_effects_db(self, db: sqlite3.Connection, campaign_id: str, actor_kind: str|None=None, actor_id: str|None=None, *, reason: str, combat_id: str|None=None, round_num: int|None=None) -> int:
        rows=db.execute("SELECT * FROM rule_effects WHERE campaign_id=? AND active=1 ORDER BY created_at,effect_id",(campaign_id,)).fetchall(); count=0
        now_row=db.execute("SELECT world_time FROM campaigns WHERE id=?",(campaign_id,)).fetchone(); world_now=datetime.fromisoformat(now_row["world_time"]) if now_row else None
        for row in rows:
            if actor_kind and (row["target_kind"]!=actor_kind or row["target_id"]!=actor_id):
                continue
            should=False
            if row["expires_on"] == reason and reason in {"turn_start","turn_end","short_rest","long_rest","combat_end"}:
                should=True
            if reason=="world_time" and row["expires_on"]=="world_time" and row["expires_world_time"] and world_now and datetime.fromisoformat(row["expires_world_time"]) <= world_now:
                should=True
            if combat_id and row["expires_combat_id"]==combat_id and row["expires_round"] is not None and round_num is not None and int(row["expires_round"]) <= int(round_num):
                should=True
            if should:
                count += int(self._end_effect_record_db(db,campaign_id,row["effect_id"],f"expired:{reason}"))
        return count

    def _expire_world_time_db(self, db: sqlite3.Connection, campaign_id: str) -> dict[str,Any]:
        ended=self._expire_effects_db(db,campaign_id,reason="world_time")
        cfg=self._config_db(db,campaign_id)
        row=db.execute("SELECT world_time FROM campaigns WHERE id=?",(campaign_id,)).fetchone()
        if not row:
            raise KeyError(f"unknown campaign: {campaign_id}")
        now=datetime.fromisoformat(row["world_time"]); dawn=[]
        latest=date.fromisoformat(self._latest_processed_dawn_marker(row["world_time"]))
        last_raw=cfg.get("last_dawn_date")
        last=date.fromisoformat(last_raw) if last_raw else latest
        actors=db.execute(
            """SELECT actor_kind,actor_id FROM rule_actor_profiles WHERE campaign_id=?
               UNION
               SELECT actor_kind,actor_id FROM rule_resources WHERE campaign_id=?
               ORDER BY actor_kind,actor_id""",
            (campaign_id,campaign_id),
        ).fetchall()
        cursor=last+timedelta(days=1)
        while cursor<=latest:
            marker=cursor.isoformat()
            for actor in actors:
                dawn.extend(self._recover_resources_db(db,campaign_id,actor["actor_kind"],actor["actor_id"],{"dawn"},marker=marker))
            cursor+=timedelta(days=1)
        if latest!=last:
            db.execute("UPDATE rules_config SET last_dawn_date=?,updated_at=? WHERE campaign_id=?",(latest.isoformat(),self.e._now(),campaign_id))
        return {"campaign_id":campaign_id,"effects_expired":ended,"dawn_recoveries":dawn}

    def expire_world_time(self, campaign_id: str) -> dict[str,Any]:
        with self.e._write_db() as db:
            return self._expire_world_time_db(db,campaign_id)

    def _insert_effect_db(self, db: sqlite3.Connection, campaign_id: str, source_activity_id: str|None, source_kind: str|None, source_id: str|None, target_kind: str, target_id: str, template: dict[str,Any], *, combat_id: str|None, group_id: str) -> dict[str,Any]|None:
        target_kind=self._kind(target_kind); self.e._get_actor_db(db,campaign_id,target_kind,target_id)
        name=str(template.get("name") or template.get("condition") or "effect")[:200]
        stacking=str(template.get("stacking","replace"))
        if stacking not in {"replace","stack","highest","ignore"}:
            raise ValueError("effect stacking must be replace, stack, highest, or ignore")
        existing=db.execute("SELECT * FROM rule_effects WHERE campaign_id=? AND target_kind=? AND target_id=? AND name=? AND active=1 ORDER BY created_at,effect_id",(campaign_id,target_kind,target_id,name)).fetchall()
        if stacking=="ignore" and existing:
            return None
        if stacking=="replace":
            for row in existing:
                self._end_effect_record_db(db,campaign_id,row["effect_id"],"replaced by same effect")
        if stacking=="highest" and existing:
            old=max(abs(int(self.e._loads(r["modifiers_json"] or "{}").get("ac_bonus",0))) for r in existing)
            new=abs(int((template.get("modifiers") or {}).get("ac_bonus",0)))
            if old >= new:
                return None
            for row in existing:
                self._end_effect_record_db(db,campaign_id,row["effect_id"],"replaced by stronger effect")
        condition=(str(template.get("condition") or "").strip().lower() or None); condition_owned=0
        if condition:
            actor=self.e._get_actor_db(db,campaign_id,target_kind,target_id); conditions=set(actor.get("conditions",[]))
            inherited_owner=db.execute("SELECT 1 FROM rule_effects WHERE campaign_id=? AND target_kind=? AND target_id=? AND condition_name=? AND condition_owned=1 AND active=1 LIMIT 1",(campaign_id,target_kind,target_id,condition)).fetchone()
            if condition not in conditions:
                conditions.add(condition); condition_owned=1; table=self.e._actor_table(target_kind); db.execute(f"UPDATE {table} SET conditions_json=?,updated_at=? WHERE campaign_id=? AND id=?",(self.e._dumps(sorted(conditions)),self.e._now(),campaign_id,target_id))
            elif inherited_owner:
                # Every effect in an engine-owned condition chain carries ownership,
                # so the final active effect can remove it. A pre-existing/manual
                # condition has no owner row and is therefore preserved.
                condition_owned=1
        expires_on,expires_world,expires_combat,expires_round=self._duration_fields(db,campaign_id,dict(template.get("duration") or {}),combat_id)
        concentration=bool(template.get("concentration",False))
        base=f"{campaign_id}:{source_activity_id}:{target_kind}:{target_id}:{group_id}:{name}:{len(existing)}"; effect_id="eff_"+hashlib.sha256(base.encode()).hexdigest()[:24]
        while db.execute("SELECT 1 FROM rule_effects WHERE campaign_id=? AND effect_id=?",(campaign_id,effect_id)).fetchone():
            base += ":x"; effect_id="eff_"+hashlib.sha256(base.encode()).hexdigest()[:24]
        db.execute("""INSERT INTO rule_effects(campaign_id,effect_id,group_id,source_activity_id,source_actor_kind,source_actor_id,target_kind,target_id,name,condition_name,condition_owned,modifiers_json,stacking,concentration,concentration_owner_kind,concentration_owner_id,expires_on,expires_world_time,expires_combat_id,expires_round,active,created_at)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                   (campaign_id,effect_id,group_id,source_activity_id,source_kind,source_id,target_kind,target_id,name,condition,condition_owned,self.e._dumps(template.get("modifiers",{})),stacking,int(concentration),source_kind if concentration else None,source_id if concentration else None,expires_on,expires_world,expires_combat,expires_round,self.e._now()))
        return {"effect_id":effect_id,"group_id":group_id,"name":name,"condition":condition,"concentration":concentration,"expires_on":expires_on,"expires_world_time":expires_world,"expires_round":expires_round}

    def _apply_effects_db(self, db: sqlite3.Connection, campaign_id: str, activity_id: str|None, source_kind: str|None, source_id: str|None, target_kind: str, target_id: str, templates: Sequence[dict[str,Any]], *, combat_id: str|None, group_id: str) -> tuple[list[dict[str,Any]],dict[str,Any]]:
        templates=list(templates)
        if len(templates)>self.MAX_EFFECTS_PER_ACTIVITY:
            raise ValueError(f"effects cap is {self.MAX_EFFECTS_PER_ACTIVITY}")
        concentration=any(bool(t.get("concentration",False)) for t in templates)
        replaced=0
        if concentration and source_kind and source_id:
            replaced=self._stop_concentration_db(db,campaign_id,source_kind,source_id,"replaced concentration",except_group=group_id)
        out=[]
        for template in templates:
            result=self._insert_effect_db(db,campaign_id,activity_id,source_kind,source_id,target_kind,target_id,dict(template),combat_id=combat_id,group_id=group_id)
            if result:
                out.append(result)
        return out,{"previous_concentration_effects_ended":replaced}

    # ---------- reactions ----------

    @staticmethod
    def _reaction_conditions_match(conditions: dict[str,Any], context: dict[str,Any]) -> bool:
        for key,value in conditions.items():
            if key in {"attack_would_hit","target_is_self","save_failed","hit","critical"} and bool(context.get(key)) != bool(value):
                return False
            if key=="damage_type" and str(context.get("damage_type")) != str(value):
                return False
            if key=="rest_type" and str(context.get("rest_type")) != str(value):
                return False
            if key=="activity_tags_any" and not set(str(x) for x in value).intersection(set(context.get("activity_tags",[]))):
                return False
            if key=="hp_below_fraction" and float(context.get("hp_fraction",1.0)) >= float(value):
                return False
        return True

    def _initiative_rank(self, db: sqlite3.Connection, campaign_id: str, combat_id: str|None, kind: str, actor_id: str) -> int:
        if not combat_id:
            return 10_000
        row=db.execute("SELECT initiative_json FROM combats WHERE campaign_id=? AND id=?",(campaign_id,combat_id)).fetchone()
        if not row:
            return 10_000
        for i,item in enumerate(self.e._loads(row["initiative_json"])):
            if item.get("kind")==kind and item.get("id")==actor_id:
                return i
        return 10_000

    def _eligible_reactions_db(self, db: sqlite3.Connection, campaign_id: str, trigger: str, context: dict[str,Any], *, combat_id: str|None, owners: Sequence[tuple[str,str]]|None=None) -> tuple[list[sqlite3.Row],bool]:
        rows=db.execute("SELECT * FROM rule_reactions WHERE campaign_id=? AND trigger=? AND enabled=1",(campaign_id,trigger)).fetchall()
        owner_set=set(owners or []) if owners is not None else None; eligible=[]
        for row in rows:
            pair=(row["owner_kind"],row["owner_id"])
            if owner_set is not None and pair not in owner_set:
                continue
            actor=self.e._get_actor_db(db,campaign_id,row["owner_kind"],row["owner_id"])
            if str(actor.get("status","alive"))!="alive" or int(actor.get("hp",0))<=0:
                continue
            if not self._reaction_conditions_match(self.e._loads(row["conditions_json"]),context):
                continue
            if row["selection_mode"]=="prompt":
                raise ValueError(f"reaction {row['id']} requires player-choice continuation, which is not implemented in v3.7")
            if bool(row["consumes_reaction"]) and combat_id:
                state=db.execute("SELECT reaction_available FROM rule_turn_state WHERE campaign_id=? AND combat_id=? AND actor_kind=? AND actor_id=?",(campaign_id,combat_id,row["owner_kind"],row["owner_id"])).fetchone()
                if state is None:
                    combat=self.e._get_combat_db(db,campaign_id,combat_id); profile=self._profile_db(db,campaign_id,row["owner_kind"],row["owner_id"]); db.execute("INSERT INTO rule_turn_state(campaign_id,combat_id,actor_kind,actor_id,round,action_available,bonus_available,reaction_available,movement_remaining,updated_at) VALUES(?,?,?,?,?,0,0,1,?,?)",(campaign_id,combat_id,row["owner_kind"],row["owner_id"],int(combat["round"]),int(profile.get("movement_cells",6)),self.e._now())); state=db.execute("SELECT reaction_available FROM rule_turn_state WHERE campaign_id=? AND combat_id=? AND actor_kind=? AND actor_id=?",(campaign_id,combat_id,row["owner_kind"],row["owner_id"])).fetchone()
                if not bool(state["reaction_available"]):
                    continue
            eligible.append(row)
        eligible.sort(key=lambda r:(int(r["priority"]),self._initiative_rank(db,campaign_id,combat_id,r["owner_kind"],r["owner_id"]),r["owner_kind"],r["owner_id"],r["id"]))
        truncated=len(eligible)>self.MAX_REACTIONS
        return eligible[:self.MAX_REACTIONS],truncated

    def _fire_reactions_db(self, db: sqlite3.Connection, campaign_id: str, trigger: str, context: dict[str,Any], *, combat_id: str|None, group_id: str, owners: Sequence[tuple[str,str]]|None=None) -> dict[str,Any]:
        eligible,truncated=self._eligible_reactions_db(db,campaign_id,trigger,context,combat_id=combat_id,owners=owners)
        applied=[]
        for row in eligible:
            owner_kind,owner_id=row["owner_kind"],row["owner_id"]
            if bool(row["consumes_reaction"]) and combat_id:
                db.execute("UPDATE rule_turn_state SET reaction_available=0,updated_at=? WHERE campaign_id=? AND combat_id=? AND actor_kind=? AND actor_id=?",(self.e._now(),campaign_id,combat_id,owner_kind,owner_id))
            consumption=self._consume_resources_db(db,campaign_id,owner_kind,owner_id,self.e._loads(row["consumption_json"]),slot_level=None)
            effect=self.e._loads(row["effect_json"]); result={"reaction_id":row["id"],"name":row["name"],"owner":{"kind":owner_kind,"id":owner_id},"consumption":consumption}
            if effect.get("cancel_activity"):
                result["cancel_activity"]=True
            if effect.get("cancel_damage"):
                result["cancel_damage"]=True
            if effect.get("effect"):
                target_kind=str(effect.get("target_kind",owner_kind)); target_id=str(effect.get("target_id",owner_id))
                effect_rows,concentration=self._apply_effects_db(db,campaign_id,None,owner_kind,owner_id,target_kind,target_id,[dict(effect["effect"])],combat_id=combat_id,group_id=group_id+":"+row["id"]); result["effects"]=effect_rows; result["concentration"]=concentration
            if effect.get("temp_hp") is not None:
                profile=self._profile_db(db,campaign_id,owner_kind,owner_id); value=max(int(profile.get("temp_hp",0)),int(effect["temp_hp"])); self._upsert_profile_db(db,campaign_id,owner_kind,owner_id,temp_hp=value); result["temp_hp"]=value
            applied.append(result)
            # v3.7 automatic policy uses the first eligible deterministic reaction.
            break
        return {"trigger":trigger,"eligible_count":len(eligible),"applied":applied,"truncated":truncated,"cap":self.MAX_REACTIONS}

    # ---------- checks, resources, damage ----------

    def _consume_resources_db(self, db: sqlite3.Connection, campaign_id: str, actor_kind: str, actor_id: str, specs: Sequence[dict[str,Any]], *, slot_level: int|None) -> list[dict[str,Any]]:
        # Validate the full consumption set before mutating any resource.
        plan=[]
        for spec in specs:
            key=str(spec.get("resource") or "")
            if not key:
                continue
            if key=="spell_slot":
                minimum=int(spec.get("minimum_level",1)); chosen=int(slot_level or minimum)
                if not minimum <= chosen <= 9:
                    raise ValueError("invalid spell slot level")
                key=f"spell_slot:{chosen}"
            amount=int(spec.get("amount",1))
            if amount < 0:
                raise ValueError("resource amount must be non-negative")
            row=db.execute("SELECT * FROM rule_resources WHERE campaign_id=? AND actor_kind=? AND actor_id=? AND resource_key=?",(campaign_id,actor_kind,actor_id,key)).fetchone()
            if not row or int(row["current_value"]) < amount:
                raise ValueError(f"insufficient resource: {key}")
            plan.append((row,amount))
        out=[]
        for row,amount in plan:
            new=int(row["current_value"])-amount; db.execute("UPDATE rule_resources SET current_value=?,updated_at=? WHERE campaign_id=? AND actor_kind=? AND actor_id=? AND resource_key=?",(new,self.e._now(),campaign_id,actor_kind,actor_id,row["resource_key"])); out.append({"resource":row["resource_key"],"amount":amount,"remaining":new})
        return out

    def _save_modifier_db(self, db: sqlite3.Connection, campaign_id: str, actor_kind: str, actor_id: str, ability: str) -> tuple[int,str]:
        actor=self.e._get_actor_db(db,campaign_id,actor_kind,actor_id); profile=self._profile_db(db,campaign_id,actor_kind,actor_id); modifiers=self._active_modifiers_db(db,campaign_id,actor_kind,actor_id); ability=ability.lower()
        total=self._actor_mod(actor,ability)
        if ability in set(str(x).lower() for x in profile.get("save_proficiencies",[])):
            total+=self._prof_bonus(actor)
        total+=int(modifiers["save_bonus"].get("all",0))+int(modifiers["save_bonus"].get(ability,0))
        key="save:"+ability; advantage=key in modifiers["advantage"] or "save:all" in modifiers["advantage"]; disadvantage=key in modifiers["disadvantage"] or "save:all" in modifiers["disadvantage"]
        mode="advantage" if advantage and not disadvantage else ("disadvantage" if disadvantage and not advantage else "normal")
        return total,mode

    def _spell_dc(self, actor: dict[str,Any], profile: dict[str,Any], save: dict[str,Any]) -> int:
        if save.get("dc") is not None:
            dc=int(save["dc"])
        else:
            ability=str(save.get("caster_ability") or profile.get("spellcasting_ability") or "int"); dc=8+self._prof_bonus(actor)+self._actor_mod(actor,ability)+int(save.get("dc_bonus",0) or 0)
        if not 1 <= dc <= 100:
            raise ValueError("save DC must be 1..100")
        return dc

    def _scaled_parts(self, activity: dict[str,Any], parts: Sequence[dict[str,Any]], *, slot_level: int|None, actor_level: int) -> list[dict[str,Any]]:
        out=[dict(x) for x in parts]; scaling=activity.get("scaling") or {}; typ=scaling.get("type")
        if typ=="slot" and slot_level is not None:
            base=int(scaling.get("base_level",slot_level)); delta=max(0,int(slot_level)-base)
            for _ in range(delta):
                out.extend(dict(x) for x in scaling.get("damage_per_level",[]))
        elif typ=="level":
            chosen=[]
            for key,value in dict(scaling.get("level_thresholds",{})).items():
                if actor_level>=int(key):
                    chosen=list(value or [])
            out.extend(dict(x) for x in chosen)
        return out

    def _roll_parts_db(self, db: sqlite3.Connection, campaign_id: str, parts: Sequence[dict[str,Any]], *, critical: bool, namespace: str, bonus: int=0) -> list[dict[str,Any]]:
        out=[]
        for i,part in enumerate(parts):
            formula=str(part.get("formula","0")); roll=self.e._roll_damage_db(db,campaign_id,formula,critical,namespace=f"{namespace}:{i}"); raw=int(roll["total"])+(int(bonus) if i==0 else 0); out.append({"type":str(part.get("type","untyped")).lower(),"raw":max(0,raw),"roll":roll})
        return out

    def _concentration_check_db(self, db: sqlite3.Connection, campaign_id: str, actor_kind: str, actor_id: str, damage: int, *, revision: int) -> dict[str,Any]|None:
        row=db.execute("SELECT 1 FROM rule_effects WHERE campaign_id=? AND concentration=1 AND concentration_owner_kind=? AND concentration_owner_id=? AND active=1 LIMIT 1",(campaign_id,actor_kind,actor_id)).fetchone()
        if not row:
            return None
        dc=max(10,int(damage)//2); modifier,mode=self._save_modifier_db(db,campaign_id,actor_kind,actor_id,"con"); check=self.e._resolve_check_db(db,campaign_id,modifier,dc,mode,namespace=f"concentration:{actor_kind}:{actor_id}"); ended=0
        if not check["success"]:
            ended=self._stop_concentration_db(db,campaign_id,actor_kind,actor_id,"failed concentration save")
        self.e._insert_event(db,campaign_id,revision,"concentration_check",f"Concentration {'held' if check['success'] else 'broken'}",actor_id=actor_id,payload={"damage":damage,"check":check,"effects_ended":ended})
        return {"dc":dc,"check":check,"effects_ended":ended}

    def _mitigate_parts_db(self, db: sqlite3.Connection, campaign_id: str, target_kind: str, target_id: str, parts: Sequence[dict[str,Any]]) -> tuple[list[dict[str,Any]],int]:
        profile=self._profile_db(db,campaign_id,target_kind,target_id); modifiers=self._active_modifiers_db(db,campaign_id,target_kind,target_id)
        immunities=set(str(x).lower() for x in profile.get("immunities",[]))|modifiers["immunities"]; resistances=set(str(x).lower() for x in profile.get("resistances",[]))|modifiers["resistances"]; vulnerabilities=set(str(x).lower() for x in profile.get("vulnerabilities",[]))|modifiers["vulnerabilities"]
        applied=[]; total=0
        for part in parts:
            damage_type=str(part.get("type","untyped")).lower(); raw=max(0,int(part.get("raw",0))); amount=raw; steps=[]
            if damage_type in immunities:
                amount=0; steps=["immune"]
            else:
                if damage_type in resistances:
                    amount=amount//2; steps.append("resistant")
                if damage_type in vulnerabilities:
                    amount=amount*2; steps.append("vulnerable")
            applied.append({**part,"type":damage_type,"applied":amount,"mitigation":"+".join(steps) if steps else "normal"}); total+=amount
        return applied,total

    def _apply_damage_db(self, db: sqlite3.Connection, campaign_id: str, target_kind: str, target_id: str, parts: Sequence[dict[str,Any]], *, revision: int, source_name: str, combat_id: str|None=None, allow_concentration: bool=True) -> dict[str,Any]:
        actor=self.e._get_actor_db(db,campaign_id,target_kind,target_id)
        provisional,provisional_total=self._mitigate_parts_db(db,campaign_id,target_kind,target_id,parts)
        reaction=self._fire_reactions_db(db,campaign_id,"before_damage",{"target_kind":target_kind,"target_id":target_id,"damage_type":provisional[0]["type"] if len(provisional)==1 else "mixed","damage":provisional_total,"hp_fraction":int(actor["hp"])/max(1,int(actor["max_hp"]))},combat_id=combat_id,group_id=f"damage:{revision}:{target_id}",owners=[(target_kind,target_id)])
        cancelled=any(x.get("cancel_damage") for x in reaction["applied"])
        if cancelled:
            applied=[{**part,"type":str(part.get("type","untyped")).lower(),"applied":0,"mitigation":"cancelled_by_reaction"} for part in parts]; total=0
        else:
            # Re-read effect/profile state so a before-damage reaction can validly
            # add resistance, immunity, vulnerability, or temporary HP.
            applied,total=self._mitigate_parts_db(db,campaign_id,target_kind,target_id,parts)
        profile=self._profile_db(db,campaign_id,target_kind,target_id); temp=int(profile.get("temp_hp",0)); absorbed=min(temp,total); remaining=total-absorbed; new_temp=temp-absorbed
        self._upsert_profile_db(db,campaign_id,target_kind,target_id,temp_hp=new_temp)
        old_hp=int(actor["hp"]); new_hp=max(0,old_hp-remaining); table=self.e._actor_table(target_kind); db.execute(f"UPDATE {table} SET hp=?,updated_at=? WHERE campaign_id=? AND id=?",(new_hp,self.e._now(),campaign_id,target_id))
        concentration=self._concentration_check_db(db,campaign_id,target_kind,target_id,total,revision=revision) if allow_concentration and total>0 else None
        died=False; unconscious=False
        if old_hp>0 and new_hp==0:
            if target_kind=="npc" and str(actor.get("status","alive"))=="alive":
                world_time=db.execute("SELECT world_time FROM campaigns WHERE id=?",(campaign_id,)).fetchone()["world_time"]; self.e._mark_npc_dead_db(db,campaign_id,target_id,revision=revision,world_time=world_time,cause=source_name); died=True
            elif target_kind=="character":
                unconscious=True; conditions=set(actor.get("conditions",[])); conditions.add("unconscious"); db.execute("UPDATE characters SET conditions_json=?,updated_at=? WHERE campaign_id=? AND id=?",(self.e._dumps(sorted(conditions)),self.e._now(),campaign_id,target_id)); self._upsert_profile_db(db,campaign_id,target_kind,target_id,death_successes=0,death_failures=0,stable=False)
        after=self._fire_reactions_db(db,campaign_id,"after_damage",{"target_kind":target_kind,"target_id":target_id,"damage":total,"damage_type":applied[0]["type"] if len(applied)==1 else "mixed","hp_fraction":new_hp/max(1,int(actor["max_hp"])),"died":died},combat_id=combat_id,group_id=f"after-damage:{revision}:{target_id}",owners=[(target_kind,target_id)])
        if died:
            self._fire_reactions_db(db,campaign_id,"on_death",{"target_kind":target_kind,"target_id":target_id},combat_id=combat_id,group_id=f"death:{revision}:{target_id}")
        return {"parts":applied,"raw_total":sum(int(x.get("raw",0)) for x in parts),"applied_total":total,"temp_hp_absorbed":absorbed,"temp_hp_remaining":new_temp,"old_hp":old_hp,"new_hp":new_hp,"unconscious":unconscious,"died":died,"concentration":concentration,"reactions":{"before_damage":reaction,"after_damage":after}}

    def _heal_db(self, db: sqlite3.Connection, campaign_id: str, target_kind: str, target_id: str, rolled: Sequence[dict[str,Any]]) -> dict[str,Any]:
        actor=self.e._get_actor_db(db,campaign_id,target_kind,target_id)
        if str(actor.get("status","alive"))=="dead":
            raise ValueError("ordinary healing cannot restore a dead actor")
        total=sum(int(x.get("raw",0)) for x in rolled); old=int(actor["hp"]); new=min(int(actor["max_hp"]),old+max(0,total)); table=self.e._actor_table(target_kind); conditions=set(actor.get("conditions",[]))
        if new>0:
            conditions.discard("unconscious")
        db.execute(f"UPDATE {table} SET hp=?,conditions_json=?,updated_at=? WHERE campaign_id=? AND id=?",(new,self.e._dumps(sorted(conditions)),self.e._now(),campaign_id,target_id)); self._upsert_profile_db(db,campaign_id,target_kind,target_id,death_successes=0,death_failures=0,stable=False)
        return {"rolls":list(rolled),"total":total,"old_hp":old,"new_hp":new,"actual_healing":new-old}

    # ---------- targeting / spatial mechanics ----------

    def _spatial_attack_db(self, db: sqlite3.Connection, campaign_id: str, combat_id: str|None, source_kind: str, source_id: str, target_kind: str, target_id: str, targeting: dict[str,Any]) -> dict[str,Any]:
        result={"distance_cells":None,"cover":"none","cover_bonus":0,"long_range":False}
        if not combat_id:
            return result
        source=db.execute("SELECT x,y FROM combat_positions WHERE campaign_id=? AND combat_id=? AND actor_kind=? AND actor_id=?",(campaign_id,combat_id,source_kind,source_id)).fetchone(); target=db.execute("SELECT x,y,cover FROM combat_positions WHERE campaign_id=? AND combat_id=? AND actor_kind=? AND actor_id=?",(campaign_id,combat_id,target_kind,target_id)).fetchone()
        if not source or not target:
            raise ValueError("combat activity requires positions for actor and target")
        distance=max(abs(int(source["x"])-int(target["x"])),abs(int(source["y"])-int(target["y"])))
        normal=targeting.get("range_cells"); long_range=targeting.get("long_range_cells")
        if long_range is not None and distance>float(long_range):
            raise ValueError(f"target out of range: {distance} cells > {long_range}")
        if long_range is None and normal is not None and distance>float(normal):
            raise ValueError(f"target out of range: {distance} cells > {normal}")
        is_long=bool(normal is not None and long_range is not None and distance>float(normal))
        cover=str(target["cover"] or "none")
        line=self.e._grid_line_cells(int(source["x"]),int(source["y"]),int(target["x"]),int(target["y"]))[1:-1]
        for x,y in line:
            terrain=db.execute("SELECT blocks_los FROM combat_terrain WHERE campaign_id=? AND combat_id=? AND x=? AND y=?",(campaign_id,combat_id,x,y)).fetchone()
            if terrain and bool(terrain["blocks_los"]):
                cover="total"; break
        if cover=="total":
            raise ValueError("target has total cover / blocked line of sight")
        result.update({"distance_cells":distance,"cover":cover,"cover_bonus":{"none":0,"half":2,"three_quarters":5}.get(cover,0),"long_range":is_long})
        return result

    def _area_distance(self, shape: str, dx: int, dy: int, targeting: dict[str,Any]) -> bool:
        radius=float(targeting.get("radius_cells",0))
        if shape in {"radius","sphere"}:
            return max(abs(dx),abs(dy)) <= radius
        if shape=="cube":
            return abs(dx)<=radius and abs(dy)<=radius
        if shape=="line":
            length=float(targeting.get("length_cells",radius)); width=float(targeting.get("width_cells",0)); return 0 <= dx <= length and abs(dy)<=width
        if shape=="cone":
            length=float(targeting.get("length_cells",radius)); return 0 <= dx <= length and abs(dy)<=dx
        if shape=="cylinder":
            return max(abs(dx),abs(dy))<=radius
        raise ValueError(f"unsupported area shape: {shape}")

    def _resolve_targets_db(self, db: sqlite3.Connection, campaign_id: str, activity: dict[str,Any], actor_kind: str, actor_id: str, targets: Sequence[dict[str,str]], *, combat_id: str|None, center: dict[str,Any]|None) -> tuple[list[dict[str,str]],dict[str,Any]]:
        cfg=activity.get("targeting") or {}; mode=str(cfg.get("mode","single")); truncated=False
        if mode=="self":
            return [{"kind":actor_kind,"id":actor_id}],{"truncated":False,"cap":1,"mode":mode}
        if mode=="area":
            if not combat_id or not center:
                raise ValueError("area activity requires combat_id and center")
            cx,cy=int(center.get("x",0)),int(center.get("y",0)); shape=str(cfg.get("shape","radius")); direction=str(center.get("direction","east")); out=[]
            combat=self.e._get_combat_db(db,campaign_id,combat_id)
            for pos in combat.get("positions",[]):
                dx=int(pos["x"])-cx; dy=int(pos["y"])-cy
                if direction=="west": dx=-dx
                elif direction=="north": dx,dy=-dy,dx
                elif direction=="south": dx,dy=dy,-dx
                if not self._area_distance(shape,dx,dy,cfg):
                    continue
                actor=self.e._get_actor_db(db,campaign_id,pos["actor_kind"],pos["actor_id"])
                if str(actor.get("status","alive"))=="dead" and not bool(cfg.get("allow_dead",False)):
                    continue
                if not bool(cfg.get("include_source",True)) and (pos["actor_kind"],pos["actor_id"])==(actor_kind,actor_id):
                    continue
                out.append({"kind":pos["actor_kind"],"id":pos["actor_id"]})
            out.sort(key=lambda x:(x["kind"],x["id"])); cap=int(cfg.get("max_targets",self.MAX_TARGETS)); truncated=len(out)>cap
            return out[:cap],{"truncated":truncated,"cap":cap,"mode":mode,"shape":shape,"center":{"x":cx,"y":cy}}
        out=[]
        for target in targets:
            kind=self._kind(str(target["kind"])); target_id=self.e._clean_id(str(target["id"])); actor=self.e._get_actor_db(db,campaign_id,kind,target_id)
            if str(actor.get("status","alive"))=="dead" and not bool(cfg.get("allow_dead",False)):
                raise ValueError(f"target is dead: {kind}/{target_id}")
            out.append({"kind":kind,"id":target_id})
        if not out:
            raise ValueError("activity requires at least one target")
        cap=int(cfg.get("max_targets",1 if mode=="single" else self.MAX_TARGETS)); truncated=len(out)>cap
        if truncated:
            raise ValueError(f"target cap exceeded: supplied {len(out)}, cap {cap}")
        if mode=="single" and len(out)!=1:
            raise ValueError("single-target activity requires exactly one target")
        return out,{"truncated":False,"cap":cap,"mode":mode}

    # ---------- summon / transform / teleport ----------

    def _summon_db(self, db: sqlite3.Connection, campaign_id: str, activity: dict[str,Any], actor_kind: str, actor_id: str, *, combat_id: str|None, group_id: str) -> dict[str,Any]:
        cfg=activity.get("special") or {}; summons=list(cfg.get("summons",[]))
        if not summons:
            raise ValueError("summon activity requires special.summons")
        if len(summons)>self.MAX_SUMMONS_PER_ACTIVITY:
            raise ValueError(f"summon cap exceeded: {len(summons)} > {self.MAX_SUMMONS_PER_ACTIVITY}")
        owner=self.e._get_actor_db(db,campaign_id,actor_kind,actor_id); scene=db.execute("SELECT id FROM scenes WHERE campaign_id=? AND location_id=? AND status='active' ORDER BY updated_at DESC LIMIT 1",(campaign_id,owner.get("location"))).fetchone(); created=[]
        for i,spec in enumerate(summons):
            digest=hashlib.sha256(f"{campaign_id}:{activity['id']}:{group_id}:{i}".encode()).hexdigest()[:12]; npc_id=self.e._clean_id(str(spec.get("id") or f"summon_{digest}")); name=str(spec.get("name") or "Summoned Creature")[:200]; hp=max(1,int(spec.get("hp",1))); ac=max(1,min(40,int(spec.get("ac",10)))); location=str(spec.get("location") or owner.get("location") or "unknown")
            if db.execute("SELECT 1 FROM npcs WHERE campaign_id=? AND id=?",(campaign_id,npc_id)).fetchone():
                raise ValueError(f"summon id already exists: {npc_id}")
            db.execute("""INSERT INTO npcs(campaign_id,id,name,hp,max_hp,ac,location,faction_id,attitude,stats_json,conditions_json,beliefs_json,goals_json,routine_json,memory_json,status,died_on,archetype_id,materialized,updated_at)
                          VALUES(?,?,?,?,?,?,?,?,0,?,'[]','[]','[]','{}','[]','alive',NULL,NULL,1,?)""",
                       (campaign_id,npc_id,name,hp,hp,ac,location,spec.get("faction_id"),self.e._dumps(spec.get("stats",{})),self.e._now()))
            if scene:
                count=db.execute("SELECT COUNT(*) n FROM scene_entities WHERE campaign_id=? AND scene_id=?",(campaign_id,scene["id"])).fetchone()["n"]
                if int(count)>=12:
                    raise ValueError("scene entity cap prevents summon")
                db.execute("INSERT INTO scene_entities(campaign_id,scene_id,actor_kind,actor_id,x,y,z,zone,stance,state_json,updated_at) VALUES(?,?,'npc',?,?,?,0,'center','summoned','{}',?)",(campaign_id,scene["id"],npc_id,float(spec.get("x",0)),float(spec.get("y",0)),self.e._now()))
            if combat_id:
                x=int(spec.get("x",0)); y=int(spec.get("y",0))
                combat=self.e._get_combat_db(db,campaign_id,combat_id)
                if not (0<=x<int(combat["grid_width"]) and 0<=y<int(combat["grid_height"])):
                    raise ValueError("summon position outside combat grid")
                db.execute("INSERT INTO combat_positions(campaign_id,combat_id,actor_kind,actor_id,x,y,cover) VALUES(?,?,'npc',?,?,?,'none')",(campaign_id,combat_id,npc_id,x,y))

                initiative=list(combat["initiative"]); participants=list(combat["participants"])
                current_ref=initiative[int(combat["turn_index"])] if initiative else None
                initiative_mode=str(cfg.get("initiative_mode","after_owner"))
                if initiative_mode not in {"after_owner","roll","end"}:
                    raise ValueError("summon initiative_mode must be after_owner, roll, or end")
                if initiative_mode=="roll":
                    natural=self.e._roll_dice_db(db,campaign_id,"1d20",f"summon-initiative:{activity['id']}:{npc_id}").total
                    modifier=int((spec.get("stats") or {}).get("dex_mod",0)); entry={"kind":"npc","id":npc_id,"name":name,"natural":natural,"modifier":modifier,"total":natural+modifier,"summoned_by":f"{actor_kind}:{actor_id}","summon_group":group_id}
                    initiative.append(entry); initiative.sort(key=lambda item:(-int(item.get("total",0)),-int(item.get("modifier",0)),str(item.get("id",""))))
                elif initiative_mode=="after_owner":
                    owner_index=next((idx for idx,item in enumerate(initiative) if item.get("kind")==actor_kind and item.get("id")==actor_id),len(initiative)-1)
                    insertion=owner_index+1
                    while insertion<len(initiative) and initiative[insertion].get("summon_group")==group_id:
                        insertion+=1
                    owner_entry=initiative[owner_index] if 0<=owner_index<len(initiative) else {"natural":0,"modifier":0,"total":0}
                    entry={"kind":"npc","id":npc_id,"name":name,"natural":int(owner_entry.get("natural",0)),"modifier":0,"total":int(owner_entry.get("total",0)),"summoned_by":f"{actor_kind}:{actor_id}","summon_group":group_id}
                    initiative.insert(insertion,entry)
                else:
                    entry={"kind":"npc","id":npc_id,"name":name,"natural":0,"modifier":0,"total":0,"summoned_by":f"{actor_kind}:{actor_id}","summon_group":group_id}; initiative.append(entry)
                participants.append({"kind":"npc","id":npc_id,"name":name,"summoned":True})
                new_turn_index=0
                if current_ref:
                    new_turn_index=next((idx for idx,item in enumerate(initiative) if item.get("kind")==current_ref.get("kind") and item.get("id")==current_ref.get("id")),0)
                db.execute("UPDATE combats SET participants_json=?,initiative_json=?,turn_index=?,updated_at=? WHERE campaign_id=? AND id=?",(self.e._dumps(participants),self.e._dumps(initiative),new_turn_index,self.e._now(),campaign_id,combat_id))
                profile=self._profile_db(db,campaign_id,"npc",npc_id)
                db.execute("INSERT INTO rule_turn_state(campaign_id,combat_id,actor_kind,actor_id,round,action_available,bonus_available,reaction_available,movement_remaining,updated_at) VALUES(?,?,'npc',?,?,0,0,1,?,?)",(campaign_id,combat_id,npc_id,int(combat["round"]),int(profile.get("movement_cells",6)),self.e._now()))
            duration=dict(cfg.get("duration") or {"unit":"combat_end"}); effect_template={"name":"Summoned: "+name,"duration":duration,"stacking":"stack"}; effect=self._insert_effect_db(db,campaign_id,activity["id"],actor_kind,actor_id,"npc",npc_id,effect_template,combat_id=combat_id,group_id=group_id)
            db.execute("INSERT INTO rule_summons(campaign_id,summon_id,source_activity_id,owner_kind,owner_id,npc_id,scene_id,combat_id,active,created_at) VALUES(?,?,?,?,?,?,?,?,1,?)",(campaign_id,effect["effect_id"],activity["id"],actor_kind,actor_id,npc_id,scene["id"] if scene else None,combat_id,self.e._now())); created.append({"npc_id":npc_id,"name":name,"effect_id":effect["effect_id"],"scene_id":scene["id"] if scene else None,"combat_id":combat_id})
        return {"created":created,"truncated":False,"cap":self.MAX_SUMMONS_PER_ACTIVITY}

    def _transform_db(self, db: sqlite3.Connection, campaign_id: str, activity: dict[str,Any], actor_kind: str, actor_id: str, target_kind: str, target_id: str, *, combat_id: str|None, group_id: str) -> dict[str,Any]:
        cfg=activity.get("special") or {}; overrides=dict(cfg.get("transform",{})); duration=dict(cfg.get("duration") or {"unit":"manual"})
        if not overrides:
            raise ValueError("transform activity requires special.transform")
        # End an earlier transform before taking the new base snapshot.
        old=db.execute("""SELECT e.effect_id FROM rule_effects e JOIN rule_transform_snapshots s ON s.campaign_id=e.campaign_id AND s.effect_id=e.effect_id WHERE e.campaign_id=? AND e.target_kind=? AND e.target_id=? AND e.active=1 ORDER BY e.created_at""",(campaign_id,target_kind,target_id)).fetchall()
        for row in old:
            self._end_effect_record_db(db,campaign_id,row["effect_id"],"replaced transformation")
        actor=self.e._get_actor_db(db,campaign_id,target_kind,target_id); snapshot={k:actor[k] for k in ("hp","max_hp","ac","location") if k in actor}; allowed={"hp","max_hp","ac","location"}
        invalid=set(overrides)-allowed
        if invalid:
            raise ValueError(f"unsupported transform fields: {sorted(invalid)}")
        table=self.e._actor_table(target_kind); sets=[]; values=[]
        for key,value in overrides.items():
            sets.append(f"{key}=?"); values.append(value)
        if sets:
            values.extend([self.e._now(),campaign_id,target_id]); db.execute(f"UPDATE {table} SET {','.join(sets)},updated_at=? WHERE campaign_id=? AND id=?",values)
        effect=self._insert_effect_db(db,campaign_id,activity["id"],actor_kind,actor_id,target_kind,target_id,{"name":str(cfg.get("name","Transformation")),"duration":duration,"stacking":"replace"},combat_id=combat_id,group_id=group_id)
        db.execute("INSERT INTO rule_transform_snapshots(campaign_id,effect_id,target_kind,target_id,snapshot_json) VALUES(?,?,?,?,?)",(campaign_id,effect["effect_id"],target_kind,target_id,self.e._dumps(snapshot)))
        return {"effect_id":effect["effect_id"],"overrides":overrides,"snapshot_fields":sorted(snapshot)}

    def _teleport_db(self, db: sqlite3.Connection, campaign_id: str, activity: dict[str,Any], target_kind: str, target_id: str, *, combat_id: str|None, center: dict[str,Any]|None) -> dict[str,Any]:
        cfg=activity.get("special") or {}; actor=self.e._get_actor_db(db,campaign_id,target_kind,target_id)
        if combat_id and center and "x" in center and "y" in center:
            combat=self.e._get_combat_db(db,campaign_id,combat_id); x,y=int(center["x"]),int(center["y"])
            if not (0<=x<int(combat["grid_width"]) and 0<=y<int(combat["grid_height"])):
                raise ValueError("teleport destination outside combat grid")
            blocked=db.execute("SELECT blocks_los FROM combat_terrain WHERE campaign_id=? AND combat_id=? AND x=? AND y=?",(campaign_id,combat_id,x,y)).fetchone()
            if blocked and bool(blocked["blocks_los"]):
                raise ValueError("teleport destination is blocked")
            db.execute("UPDATE combat_positions SET x=?,y=? WHERE campaign_id=? AND combat_id=? AND actor_kind=? AND actor_id=?",(x,y,campaign_id,combat_id,target_kind,target_id)); return {"scope":"combat","old_location":actor.get("location"),"x":x,"y":y}
        location=str(cfg.get("location_id") or (center or {}).get("location_id") or "")
        if not location:
            raise ValueError("teleport requires combat coordinates or location_id")
        if not db.execute("SELECT 1 FROM locations WHERE campaign_id=? AND id=?",(campaign_id,location)).fetchone():
            raise KeyError(f"unknown teleport location: {location}")
        table=self.e._actor_table(target_kind); old=actor.get("location")
        scene=db.execute("SELECT id,location_id FROM scenes WHERE campaign_id=? AND status='active' ORDER BY updated_at DESC LIMIT 1",(campaign_id,)).fetchone()
        scene_update="none"
        if scene:
            existing=db.execute("SELECT 1 FROM scene_entities WHERE campaign_id=? AND scene_id=? AND actor_kind=? AND actor_id=?",(campaign_id,scene["id"],target_kind,target_id)).fetchone()
            if scene["location_id"]==location:
                if not existing:
                    count=db.execute("SELECT COUNT(*) n FROM scene_entities WHERE campaign_id=? AND scene_id=?",(campaign_id,scene["id"])).fetchone()["n"]
                    if int(count)>=12:
                        raise ValueError("destination scene entity cap prevents teleport")
                    db.execute("INSERT INTO scene_entities(campaign_id,scene_id,actor_kind,actor_id,x,y,z,zone,stance,state_json,updated_at) VALUES(?,?,?,?,?,?,0,'center','arriving','{}',?)",(campaign_id,scene["id"],target_kind,target_id,float((center or {}).get("x",0)),float((center or {}).get("y",0)),self.e._now()))
                    scene_update="inserted"
                else:
                    db.execute("UPDATE scene_entities SET x=?,y=?,stance='arriving',updated_at=? WHERE campaign_id=? AND scene_id=? AND actor_kind=? AND actor_id=?",(float((center or {}).get("x",0)),float((center or {}).get("y",0)),self.e._now(),campaign_id,scene["id"],target_kind,target_id)); scene_update="updated"
            elif existing:
                db.execute("DELETE FROM scene_entities WHERE campaign_id=? AND scene_id=? AND actor_kind=? AND actor_id=?",(campaign_id,scene["id"],target_kind,target_id)); scene_update="removed"
        db.execute(f"UPDATE {table} SET location=?,updated_at=? WHERE campaign_id=? AND id=?",(location,self.e._now(),campaign_id,target_id))
        return {"scope":"world","old_location":old,"new_location":location,"scene_update":scene_update,"scene_id":scene["id"] if scene else None}

    def move_in_combat(self, campaign_id: str, actor_kind: str, actor_id: str, combat_id: str, path: Sequence[dict[str,Any]]) -> dict[str,Any]:
        """Move along an explicit contiguous grid path and consume movement atomically."""
        actor_kind=self._kind(actor_kind); actor_id=self.e._clean_id(actor_id); combat_id=self.e._clean_id(combat_id); steps=list(path)
        if not steps:
            raise ValueError("movement path must contain at least one destination cell")
        if len(steps)>100:
            raise ValueError("movement path cap is 100 cells")
        with self.e._write_db() as db:
            combat=self.e._get_combat_db(db,campaign_id,combat_id)
            if combat["status"]!="active":
                raise ValueError("combat is not active")
            current=combat.get("current_turn") or {}
            if (current.get("kind"),current.get("id"))!=(actor_kind,actor_id):
                raise ValueError("ordinary movement may only occur on the actor's current turn")
            pos=db.execute("SELECT x,y FROM combat_positions WHERE campaign_id=? AND combat_id=? AND actor_kind=? AND actor_id=?",(campaign_id,combat_id,actor_kind,actor_id)).fetchone()
            if not pos:
                raise ValueError("actor has no combat position")
            state=db.execute("SELECT * FROM rule_turn_state WHERE campaign_id=? AND combat_id=? AND actor_kind=? AND actor_id=?",(campaign_id,combat_id,actor_kind,actor_id)).fetchone()
            if not state:
                state=self.reset_turn_state_db(db,campaign_id,combat_id,actor_kind,actor_id,int(combat["round"]))
            x,y=int(pos["x"]),int(pos["y"]); cost=0; traversed=[]
            for index,cell in enumerate(steps):
                nx,ny=int(cell.get("x")) if cell.get("x") is not None else -1,int(cell.get("y")) if cell.get("y") is not None else -1
                if not (0<=nx<int(combat["grid_width"]) and 0<=ny<int(combat["grid_height"])):
                    raise ValueError(f"movement cell {index} is outside combat grid")
                if max(abs(nx-x),abs(ny-y))!=1:
                    raise ValueError(f"movement path is not contiguous at cell {index}")
                terrain=db.execute("SELECT blocks_los,difficult FROM combat_terrain WHERE campaign_id=? AND combat_id=? AND x=? AND y=?",(campaign_id,combat_id,nx,ny)).fetchone()
                if terrain and bool(terrain["blocks_los"]):
                    raise ValueError(f"movement cell {index} is blocked")
                occupied=db.execute("SELECT 1 FROM combat_positions WHERE campaign_id=? AND combat_id=? AND x=? AND y=? AND NOT (actor_kind=? AND actor_id=?) LIMIT 1",(campaign_id,combat_id,nx,ny,actor_kind,actor_id)).fetchone()
                if occupied:
                    raise ValueError(f"movement cell {index} is occupied")
                step_cost=2 if terrain and bool(terrain["difficult"]) else 1; cost+=step_cost; traversed.append({"x":nx,"y":ny,"cost":step_cost}); x,y=nx,ny
            remaining=int(state["movement_remaining"])
            if cost>remaining:
                raise ValueError(f"insufficient movement: need {cost}, have {remaining}")
            db.execute("UPDATE combat_positions SET x=?,y=? WHERE campaign_id=? AND combat_id=? AND actor_kind=? AND actor_id=?",(x,y,campaign_id,combat_id,actor_kind,actor_id))
            db.execute("UPDATE rule_turn_state SET movement_remaining=?,updated_at=? WHERE campaign_id=? AND combat_id=? AND actor_kind=? AND actor_id=?",(remaining-cost,self.e._now(),campaign_id,combat_id,actor_kind,actor_id))
            revision=self.e._next_revision(db,campaign_id)
            self.e._insert_event(db,campaign_id,revision,"combat_move",f"{actor_id} moved {len(steps)} cells",actor_id=actor_id,payload={"combat_id":combat_id,"path":traversed,"movement_cost":cost,"movement_remaining":remaining-cost})
            return {"campaign_id":campaign_id,"revision":revision,"combat_id":combat_id,"actor":{"kind":actor_kind,"id":actor_id},"position":{"x":x,"y":y},"path":traversed,"movement_cost":cost,"movement_remaining":remaining-cost,"truncated":False,"cap":100}

    # ---------- generalized activity resolution ----------

    def resolve_activity(self, campaign_id: str, activity_id: str, actor_kind: str, actor_id: str, *, targets: Sequence[dict[str,str]]=(), combat_id: str|None=None, slot_level: int|None=None, center: dict[str,Any]|None=None, mode: str|None=None) -> dict[str,Any]:
        campaign_id=self.e._clean_id(campaign_id); activity_id=self.e._clean_id(activity_id); actor_kind=self._kind(actor_kind)
        with self.e._write_db() as db:
            row=db.execute("SELECT * FROM rule_activities WHERE campaign_id=? AND id=? AND enabled=1",(campaign_id,activity_id)).fetchone()
            if not row:
                raise KeyError(f"unknown or disabled activity: {activity_id}")
            activity=self._decode_activity(row); actor=self.e._get_actor_db(db,campaign_id,actor_kind,actor_id)
            if str(actor.get("status","alive"))!="alive" or int(actor.get("hp",0))<=0:
                raise ValueError("actor cannot use activity while dead or unconscious")
            cfg=self._config_db(db,campaign_id); active_version=cfg["rules_version"]
            if activity["rules_version"] not in {"both",active_version}:
                raise ValueError(f"activity {activity_id} is {activity['rules_version']} rules but campaign uses {active_version}")
            if activity.get("object_id") and not db.execute("SELECT 1 FROM rule_actor_objects WHERE campaign_id=? AND actor_kind=? AND actor_id=? AND object_id=?",(campaign_id,actor_kind,actor_id,activity["object_id"])).fetchone():
                raise ValueError(f"actor does not possess rule object: {activity['object_id']}")
            resolved_targets,target_report=self._resolve_targets_db(db,campaign_id,activity,actor_kind,actor_id,targets,combat_id=combat_id,center=center)
            # Validate action economy and all consumable resources before the first mutation.
            action_use=self._consume_action_economy_db(db,campaign_id,combat_id,actor_kind,actor_id,activity["activation"])
            consumption=self._consume_resources_db(db,campaign_id,actor_kind,actor_id,activity["consumption"],slot_level=slot_level)
            revision=self.e._next_revision(db,campaign_id); group_id=f"activity:{activity_id}:revision:{revision}"
            before=self._fire_reactions_db(db,campaign_id,"before_activity",{"actor_kind":actor_kind,"actor_id":actor_id,"activity_tags":activity["tags"]},combat_id=combat_id,group_id=group_id,owners=[(actor_kind,actor_id)])
            cast_reactions={"trigger":"on_cast","eligible_count":0,"applied":[],"truncated":False,"cap":self.MAX_REACTIONS}
            if activity.get("object_id"):
                obj=db.execute("SELECT object_kind FROM rule_objects WHERE campaign_id=? AND id=?",(campaign_id,activity["object_id"])).fetchone()
                if obj and obj["object_kind"]=="spell":
                    cast_reactions=self._fire_reactions_db(db,campaign_id,"on_cast",{"actor_kind":actor_kind,"actor_id":actor_id,"activity_tags":activity["tags"]},combat_id=combat_id,group_id=group_id)
            if any(x.get("cancel_activity") for x in before["applied"]+cast_reactions["applied"]):
                payload={"activity_id":activity_id,"cancelled":True,"action_economy":action_use,"consumption":consumption,"reactions":{"before_activity":before,"on_cast":cast_reactions}}
                self.e._insert_event(db,campaign_id,revision,"rule_activity_cancelled",f"{actor['name']}'s {activity['name']} was cancelled",region=actor.get("location"),actor_id=actor_id,payload=payload)
                return {"campaign_id":campaign_id,"revision":revision,"activity":activity,"actor":{"kind":actor_kind,"id":actor_id,"name":actor["name"]},"targets":resolved_targets,"target_report":target_report,"cancelled":True,**payload}

            actor_profile=self._profile_db(db,campaign_id,actor_kind,actor_id); actor_level=int(actor.get("level",1) or 1); source_modifiers=self._active_modifiers_db(db,campaign_id,actor_kind,actor_id)
            damage_parts=self._scaled_parts(activity,activity["damage"],slot_level=slot_level,actor_level=actor_level); healing_parts=self._scaled_parts(activity,activity["healing"],slot_level=slot_level,actor_level=actor_level)
            shared_damage=None
            if activity["activity_type"] in {"save","damage"} and damage_parts:
                shared_damage=self._roll_parts_db(db,campaign_id,damage_parts,critical=False,namespace=f"activity:{activity_id}:shared",bonus=int(source_modifiers["damage_bonus"]))
            results=[]
            for target_ref in resolved_targets:
                target_kind,target_id=target_ref["kind"],target_ref["id"]; target=self.e._get_actor_db(db,campaign_id,target_kind,target_id); result={"target":{"kind":target_kind,"id":target_id,"name":target["name"]}}
                activity_type=activity["activity_type"]
                if activity_type=="attack":
                    attack=activity["attack"] or {}; ability=str(attack.get("ability") or (actor_profile.get("spellcasting_ability") if attack.get("classification")=="spell" else "str") or "str"); bonus=self._actor_mod(actor,ability)+int(attack.get("bonus",0))+int(source_modifiers["attack_bonus"])+(self._prof_bonus(actor) if bool(attack.get("proficient",True)) else 0)
                    spatial=self._spatial_attack_db(db,campaign_id,combat_id,actor_kind,actor_id,target_kind,target_id,activity["targeting"]); target_modifiers=self._active_modifiers_db(db,campaign_id,target_kind,target_id); target_ac=int(target["ac"])+int(target_modifiers["ac_bonus"])+int(spatial["cover_bonus"])
                    advantage="attack" in source_modifiers["advantage"]; disadvantage="attack" in source_modifiers["disadvantage"] or bool(spatial["long_range"]); attack_mode=mode or ("advantage" if advantage and not disadvantage else ("disadvantage" if disadvantage and not advantage else "normal")); check=self.e._resolve_check_db(db,campaign_id,bonus,target_ac,attack_mode,namespace=f"activity:{activity_id}:attack:{target_id}"); natural=int(check["natural"]); critical=natural>=int(attack.get("critical_threshold",20)); would_hit=False if natural==1 else (critical or check["success"])
                    attack_reactions=self._fire_reactions_db(db,campaign_id,"after_attack_roll",{"attack_would_hit":would_hit,"target_is_self":True,"activity_tags":activity["tags"],"critical":critical},combat_id=combat_id,group_id=group_id,owners=[(target_kind,target_id)]); target_modifiers=self._active_modifiers_db(db,campaign_id,target_kind,target_id); final_ac=int(target["ac"])+int(target_modifiers["ac_bonus"])+int(spatial["cover_bonus"]); hit=False if natural==1 else (critical or int(check["total"])>=final_ac)
                    result.update({"attack":check,"target_ac":final_ac,"hit":hit,"critical":critical,"spatial":spatial,"reactions":{"after_attack_roll":attack_reactions}})
                    if hit:
                        hit_reactions=self._fire_reactions_db(db,campaign_id,"after_hit",{"hit":True,"critical":critical,"activity_tags":activity["tags"],"target_kind":target_kind,"target_id":target_id},combat_id=combat_id,group_id=group_id)
                        result["reactions"]["after_hit"]=hit_reactions
                        if damage_parts:
                            rolled=self._roll_parts_db(db,campaign_id,damage_parts,critical=critical,namespace=f"activity:{activity_id}:damage:{target_id}",bonus=int(source_modifiers["damage_bonus"])); result["damage"]=self._apply_damage_db(db,campaign_id,target_kind,target_id,rolled,revision=revision,source_name=activity["name"],combat_id=combat_id)
                elif activity_type=="save":
                    save=activity["save"] or {}; ability=str(save.get("ability") or "dex"); dc=self._spell_dc(actor,actor_profile,save); self._fire_reactions_db(db,campaign_id,"before_save",{"target_kind":target_kind,"target_id":target_id,"activity_tags":activity["tags"]},combat_id=combat_id,group_id=group_id,owners=[(target_kind,target_id)]); modifier,save_mode=self._save_modifier_db(db,campaign_id,target_kind,target_id,ability); check=self.e._resolve_check_db(db,campaign_id,modifier,dc,mode or save_mode,namespace=f"activity:{activity_id}:save:{target_id}"); save_reactions=self._fire_reactions_db(db,campaign_id,"after_save",{"save_failed":not check["success"],"target_kind":target_kind,"target_id":target_id,"activity_tags":activity["tags"]},combat_id=combat_id,group_id=group_id,owners=[(target_kind,target_id)]); result["save"]={"ability":ability,**check}; result["reactions"]={"after_save":save_reactions}
                    if shared_damage:
                        outcome=str(save.get("on_success","half" if save.get("half_on_success") else "none")); adjusted=[]
                        for part in shared_damage:
                            raw=int(part["raw"]); amount=raw
                            if check["success"]:
                                amount=raw//2 if outcome=="half" else (0 if outcome=="none" else raw)
                            adjusted.append({**part,"raw":amount})
                        result["damage"]=self._apply_damage_db(db,campaign_id,target_kind,target_id,adjusted,revision=revision,source_name=activity["name"],combat_id=combat_id)
                elif activity_type=="damage":
                    result["damage"]=self._apply_damage_db(db,campaign_id,target_kind,target_id,shared_damage or [],revision=revision,source_name=activity["name"],combat_id=combat_id)
                elif activity_type=="heal":
                    rolled=self._roll_parts_db(db,campaign_id,healing_parts,critical=False,namespace=f"activity:{activity_id}:heal:{target_id}"); result["healing"]=self._heal_db(db,campaign_id,target_kind,target_id,rolled)
                elif activity_type=="transform":
                    result["transformation"]=self._transform_db(db,campaign_id,activity,actor_kind,actor_id,target_kind,target_id,combat_id=combat_id,group_id=group_id)
                elif activity_type=="teleport":
                    result["teleport"]=self._teleport_db(db,campaign_id,activity,target_kind,target_id,combat_id=combat_id,center=center)
                apply_effects = activity_type in {"utility","heal","damage","summon","transform","teleport"} or (activity_type=="attack" and result.get("hit")) or (activity_type=="save" and not result.get("save",{}).get("success",False))
                if apply_effects and activity["effects"]:
                    effects,concentration=self._apply_effects_db(db,campaign_id,activity_id,actor_kind,actor_id,target_kind,target_id,activity["effects"],combat_id=combat_id,group_id=group_id); result["effects"]=effects; result["concentration_start"]=concentration
                results.append(result)
            summon=None
            if activity["activity_type"]=="summon":
                summon=self._summon_db(db,campaign_id,activity,actor_kind,actor_id,combat_id=combat_id,group_id=group_id)
            payload={"activity_id":activity_id,"object_id":activity.get("object_id"),"activity_type":activity["activity_type"],"activation":activity["activation"],"actor_kind":actor_kind,"targets":resolved_targets,"target_report":target_report,"slot_level":slot_level,"action_economy":action_use,"consumption":consumption,"reactions":{"before_activity":before,"on_cast":cast_reactions},"results":results,"summon":summon}
            self.e._insert_event(db,campaign_id,revision,"rule_activity",f"{actor['name']} used {activity['name']}",region=actor.get("location"),actor_id=actor_id,payload=payload)
            cascade_count=0
            if activity.get("world_event_type"):
                event_type=str(activity["world_event_type"]); world_payload={"activity_id":activity_id,"object_id":activity.get("object_id"),"results":results,"summon":summon}; self.e._insert_event(db,campaign_id,revision,event_type,f"World consequence from {activity['name']}",region=actor.get("location"),actor_id=actor_id,target_id=resolved_targets[0]["id"] if resolved_targets else None,payload=world_payload)
                from .simulation import SimulationKernel
                queue=deque([{"event_type":event_type,"summary":f"World consequence from {activity['name']}","payload":world_payload,"region":actor.get("location"),"actor_id":actor_id,"target_id":resolved_targets[0]["id"] if resolved_targets else None,"world_time":db.execute("SELECT world_time FROM campaigns WHERE id=?",(campaign_id,)).fetchone()["world_time"],"depth":0}]); cascade_count=SimulationKernel(self.e)._drain_reactions(db,campaign_id,revision,queue)
            return {"campaign_id":campaign_id,"revision":revision,"activity":activity,"actor":{"kind":actor_kind,"id":actor_id,"name":actor["name"]},"targets":resolved_targets,"target_report":target_report,"action_economy":action_use,"consumption":consumption,"results":results,"summon":summon,"world_cascade_events":cascade_count,"cancelled":False}

    # ---------- rests, death saves, advancement ----------

    def rest(self, campaign_id: str, actor_kind: str, actor_id: str, *, rest_type: str, hit_dice_count: int=0, hit_die_formula: str|None=None, simulate_world: bool=True) -> dict[str,Any]:
        """Resolve recovery and elapsed WORLD time as one SQLite transaction."""
        from .simulation import SimulationKernel

        actor_kind=self._kind(actor_kind)
        if rest_type not in {"short","long"}:
            raise ValueError("rest_type must be short or long")
        hit_dice_count=int(hit_dice_count)
        if hit_dice_count < 0 or hit_dice_count > 20:
            raise ValueError("hit_dice_count must be 0..20")
        if rest_type=="long" and hit_dice_count:
            raise ValueError("hit_dice_count applies only to a short rest")
        minutes=60 if rest_type=="short" else 480
        self.e._ensure_campaign_exists(campaign_id)

        with self.e._write_db() as db:
            before_actor=self.e._get_actor_db(db,campaign_id,actor_kind,actor_id)
            if str(before_actor.get("status","alive"))=="dead":
                raise ValueError("a dead actor cannot complete an ordinary rest")
            if rest_type=="short" and hit_dice_count:
                if not hit_die_formula:
                    raise ValueError("short-rest hit dice require hit_die_formula")
                resource=db.execute("SELECT current_value FROM rule_resources WHERE campaign_id=? AND actor_kind=? AND actor_id=? AND resource_key='hit_dice'",(campaign_id,actor_kind,actor_id)).fetchone()
                if not resource or int(resource["current_value"])<hit_dice_count:
                    raise ValueError("insufficient hit dice")
            for row in db.execute("SELECT id,conditions_json FROM rule_reactions WHERE campaign_id=? AND owner_kind=? AND owner_id=? AND trigger='on_rest' AND enabled=1 AND selection_mode='prompt'",(campaign_id,actor_kind,actor_id)).fetchall():
                if self._reaction_conditions_match(self.e._loads(row["conditions_json"]),{"rest_type":rest_type,"actor_kind":actor_kind,"actor_id":actor_id}):
                    raise ValueError(f"reaction {row['id']} requires player-choice continuation, which is not implemented in v3.7")

            if simulate_world:
                world=SimulationKernel(self.e).advance_db(db,campaign_id,minutes,reason=f"{rest_type} rest")
            else:
                campaign=db.execute("SELECT * FROM campaigns WHERE id=?",(campaign_id,)).fetchone()
                current=datetime.fromisoformat(campaign["world_time"]); updated=current+timedelta(minutes=minutes)
                db.execute("UPDATE campaigns SET world_time=?,updated_at=? WHERE id=?",(updated.isoformat(),self.e._now(),campaign_id))
                world_revision=self.e._next_revision(db,campaign_id)
                self.e._insert_event(db,campaign_id,world_revision,"world_advance",f"{rest_type} rest",payload={"minutes":minutes,"old_time":campaign["world_time"],"new_time":updated.isoformat(),"weather":campaign["weather"]},world_time_override=updated.isoformat())
                current_row=db.execute("SELECT * FROM campaigns WHERE id=?",(campaign_id,)).fetchone()
                world=dict(current_row); world["settings"]=self.e._loads(world.pop("settings_json")); world["simulation"]={"disabled":True}
            world["rules_time_update"]=self._expire_world_time_db(db,campaign_id)

            actor=self.e._get_actor_db(db,campaign_id,actor_kind,actor_id)
            revision=self.e._next_revision(db,campaign_id); healed=0; hit_dice_spent=0
            completed=str(actor.get("status","alive"))!="dead"
            recovered=[]; expired=0; reactions={"trigger":"on_rest","eligible_count":0,"applied":[],"truncated":False,"cap":self.MAX_REACTIONS}
            if completed:
                policies={"short_rest"} if rest_type=="short" else {"short_rest","long_rest"}
                recovered=self._recover_resources_db(db,campaign_id,actor_kind,actor_id,policies,marker=f"rest:{rest_type}:{world['world_time']}")
                if rest_type=="long":
                    old=int(actor["hp"]); new=int(actor["max_hp"]); table=self.e._actor_table(actor_kind); conditions=set(actor.get("conditions",[])); conditions.discard("unconscious")
                    db.execute(f"UPDATE {table} SET hp=?,conditions_json=?,updated_at=? WHERE campaign_id=? AND id=?",(new,self.e._dumps(sorted(conditions)),self.e._now(),campaign_id,actor_id)); healed=new-old
                    self._upsert_profile_db(db,campaign_id,actor_kind,actor_id,death_successes=0,death_failures=0,stable=False)
                elif hit_dice_count:
                    total=sum(self.e._roll_dice_db(db,campaign_id,hit_die_formula,f"rest:{actor_id}:{i}").total for i in range(hit_dice_count))
                    old=int(actor["hp"]); new=min(int(actor["max_hp"]),old+max(0,total)); table=self.e._actor_table(actor_kind)
                    db.execute(f"UPDATE {table} SET hp=?,updated_at=? WHERE campaign_id=? AND id=?",(new,self.e._now(),campaign_id,actor_id))
                    db.execute("UPDATE rule_resources SET current_value=current_value-?,updated_at=? WHERE campaign_id=? AND actor_kind=? AND actor_id=? AND resource_key='hit_dice'",(hit_dice_count,self.e._now(),campaign_id,actor_kind,actor_id)); healed=new-old; hit_dice_spent=hit_dice_count
                expired=self._expire_effects_db(db,campaign_id,actor_kind,actor_id,reason=rest_type+"_rest")
                reactions=self._fire_reactions_db(db,campaign_id,"on_rest",{"rest_type":rest_type,"actor_kind":actor_kind,"actor_id":actor_id},combat_id=None,group_id=f"rest:{revision}",owners=[(actor_kind,actor_id)])
            payload={"rest_type":rest_type,"minutes":minutes,"completed":completed,"healed":healed,"hit_dice_spent":hit_dice_spent,"resources_recovered":recovered,"effects_expired":expired,"reactions":reactions}
            summary=f"{actor['name']} completed a {rest_type} rest" if completed else f"{actor['name']}'s {rest_type} rest ended without recovery"
            self.e._insert_event(db,campaign_id,revision,"rule_rest",summary,actor_id=actor_id,payload=payload)
            return {"campaign_id":campaign_id,"revision":revision,"world":world,**payload}

    def _death_save_db(self, db, campaign_id: str, actor_kind: str, actor_id: str) -> dict[str,Any]:
        """Resolve one death save inside an existing authoritative transaction."""
        actor_kind=self._kind(actor_kind)
        actor=self.e._get_actor_db(db,campaign_id,actor_kind,actor_id)
        if int(actor["hp"])>0:
            raise ValueError("death save only applies at 0 HP")
        if str(actor.get("status","alive"))=="dead":
            raise ValueError("dead actor cannot roll death saves")
        profile=self._profile_db(db,campaign_id,actor_kind,actor_id)
        if profile.get("stable"):
            raise ValueError("stable actor does not roll death saves")
        roll=self.e._roll_dice_db(db,campaign_id,"1d20",f"death-save:{actor_kind}:{actor_id}").total
        successes=int(profile.get("death_successes",0)); failures=int(profile.get("death_failures",0)); outcome=""
        if roll==20:
            table=self.e._actor_table(actor_kind); conditions=set(actor.get("conditions",[])); conditions.discard("unconscious")
            db.execute(f"UPDATE {table} SET hp=1,conditions_json=?,updated_at=? WHERE campaign_id=? AND id=?",(self.e._dumps(sorted(conditions)),self.e._now(),campaign_id,actor_id))
            successes=failures=0; outcome="critical_success_revived"
        elif roll==1:
            failures=min(3,failures+2); outcome="critical_failure"
        elif roll>=10:
            successes=min(3,successes+1); outcome="success"
        else:
            failures=min(3,failures+1); outcome="failure"
        stable=successes>=3 and failures<3
        if stable:
            outcome="stable"
        revision=self.e._next_revision(db,campaign_id)
        if failures>=3:
            outcome="dead"; world_time=db.execute("SELECT world_time FROM campaigns WHERE id=?",(campaign_id,)).fetchone()["world_time"]
            if actor_kind=="npc":
                self.e._mark_npc_dead_db(db,campaign_id,actor_id,revision=revision,world_time=world_time,cause="failed death saves")
            else:
                db.execute("UPDATE characters SET status='dead',died_on=?,updated_at=? WHERE campaign_id=? AND id=?",(world_time,self.e._now(),campaign_id,actor_id))
        self._upsert_profile_db(db,campaign_id,actor_kind,actor_id,death_successes=successes,death_failures=failures,stable=stable)
        self.e._insert_event(db,campaign_id,revision,"death_save",f"{actor['name']} death save: {outcome}",actor_id=actor_id,payload={"roll":roll,"successes":successes,"failures":failures,"stable":stable,"outcome":outcome})
        return {"campaign_id":campaign_id,"revision":revision,"roll":roll,"successes":successes,"failures":failures,"stable":stable,"outcome":outcome}

    def death_save(self, campaign_id: str, actor_kind: str, actor_id: str) -> dict[str,Any]:
        with self.e._write_db() as db:
            return self._death_save_db(db,campaign_id,actor_kind,actor_id)

    def apply_advancement(self, campaign_id: str, actor_kind: str, actor_id: str, class_id: str, level: int) -> dict[str,Any]:
        actor_kind=self._kind(actor_kind); level=int(level)
        if not 1<=level<=20:
            raise ValueError("level must be 1..20")
        with self.e._write_db() as db:
            actor=self.e._get_actor_db(db,campaign_id,actor_kind,actor_id); cfg=self._config_db(db,campaign_id)
            if actor_kind=="character":
                current=int(actor["level"])
                if level != current + 1:
                    raise ValueError(f"character advancement must apply exactly the next level ({current+1})")
                prow=db.execute("SELECT pending_level,class_id FROM character_progression WHERE campaign_id=? AND character_id=?",(campaign_id,actor_id)).fetchone()
                if not prow or prow["pending_level"] is None or int(prow["pending_level"]) < level:
                    raise ValueError("character is not eligible for this level; award XP or a milestone first")
                if prow["class_id"] and str(prow["class_id"]) != str(class_id):
                    raise ValueError("advancement class does not match authoritative progression class")
            rows=db.execute("SELECT * FROM rule_advancements WHERE campaign_id=? AND class_id=? AND level=? AND rules_version IN (?, 'both') ORDER BY id",(campaign_id,class_id,level,cfg["rules_version"])).fetchall(); granted=[]; resources=[]
            for row in rows:
                for object_id in self.e._loads(row["grant_objects_json"]):
                    db.execute("INSERT OR IGNORE INTO rule_actor_objects(campaign_id,actor_kind,actor_id,object_id,source,metadata_json,granted_at) VALUES(?,?,?,?,?,'{}',?)",(campaign_id,actor_kind,actor_id,object_id,f"advancement:{class_id}:{level}",self.e._now())); granted.append(object_id)
                for key,spec in self.e._loads(row["resources_json"]).items():
                    if isinstance(spec,int):
                        spec={"max":spec,"current":spec,"recovery":"long_rest"}
                    maximum=int(spec.get("max",0)); current=int(spec.get("current",maximum)); recovery=str(spec.get("recovery","long_rest"))
                    if recovery not in self.RECOVERY:
                        raise ValueError(f"invalid advancement recovery: {recovery}")
                    db.execute("""INSERT INTO rule_resources(campaign_id,actor_kind,actor_id,resource_key,current_value,max_value,recovery,recovery_amount,last_recovery_marker,updated_at) VALUES(?,?,?,?,?,?,?,?,NULL,?)
                                  ON CONFLICT(campaign_id,actor_kind,actor_id,resource_key) DO UPDATE SET current_value=MAX(rule_resources.current_value,excluded.current_value),max_value=excluded.max_value,recovery=excluded.recovery,recovery_amount=excluded.recovery_amount,updated_at=excluded.updated_at""",
                               (campaign_id,actor_kind,actor_id,key,current,maximum,recovery,spec.get("recovery_amount"),self.e._now())); resources.append(key)
            if actor_kind=="character":
                now=self.e._now()
                db.execute("UPDATE characters SET level=?,updated_at=? WHERE campaign_id=? AND id=?",(level,now,campaign_id,actor_id))
                # Advancement consumes a pending level-up while preserving cumulative XP.
                db.execute("""INSERT INTO character_progression(campaign_id,character_id,mode,xp,pending_level,milestone_count,class_id,last_level_up_at,updated_at)
                              VALUES(?,?,'xp',?,NULL,0,?,?,?)
                              ON CONFLICT(campaign_id,character_id) DO UPDATE SET pending_level=CASE WHEN character_progression.pending_level IS NULL OR character_progression.pending_level<=? THEN NULL ELSE character_progression.pending_level END,class_id=excluded.class_id,last_level_up_at=excluded.last_level_up_at,updated_at=excluded.updated_at""",
                           (campaign_id,actor_id,0,class_id,now,now,level))
            revision=self.e._next_revision(db,campaign_id); self.e._insert_event(db,campaign_id,revision,"advancement",f"{actor['name']} advanced in {class_id} to level {level}",actor_id=actor_id,payload={"class_id":class_id,"level":level,"granted_objects":granted,"resources":resources})
        return {"campaign_id":campaign_id,"revision":revision,"actor_id":actor_id,"class_id":class_id,"level":level,"granted_objects":granted,"resources":resources}

    # ---------- dispatch ----------

    def dispatch(self, operation: str, campaign_id: str="default", payload: dict[str,Any]|None=None) -> Any:
        data=dict(payload or {})
        if operation=="configure": return self.configure(campaign_id,**data)
        if operation=="set_actor_profile": return self.set_actor_profile(campaign_id,**data)
        if operation=="define_object": return self.define_object(campaign_id,**data)
        if operation=="define_activity": return self.define_activity(campaign_id,**data)
        if operation=="grant_object": return self.grant_object(campaign_id,**data)
        if operation=="set_resource": return self.set_resource(campaign_id,**data)
        if operation=="define_reaction": return self.define_reaction(campaign_id,**data)
        if operation=="resolve_activity": return self.resolve_activity(campaign_id,**data)
        if operation=="move": return self.move_in_combat(campaign_id,**data)
        if operation=="rest": return self.rest(campaign_id,**data)
        if operation=="death_save": return self.death_save(campaign_id,**data)
        if operation=="list_effects": return self.list_effects(campaign_id,**data)
        if operation=="end_effect": return self.end_effect(campaign_id,**data)
        if operation=="get_actor_rules": return self.get_actor_rules(campaign_id,**data)
        if operation=="define_advancement": return self.define_advancement(campaign_id,**data)
        if operation=="apply_advancement": return self.apply_advancement(campaign_id,**data)
        raise ValueError(f"unknown rules operation: {operation}")
