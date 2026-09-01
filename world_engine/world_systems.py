from __future__ import annotations

import heapq
import math
from typing import Any


ALLOWED_WEATHER_CONDITIONS = frozenset({
    "clear", "cloudy", "rain", "storm", "snow", "fog", "wind",
    "heatwave", "cold_snap",
})
WEATHER_WEIGHT_LIMIT = 1_000_000.0


def weather_weight_validation_errors(weights: Any) -> list[tuple[str | None, str]]:
    """Return stable validation errors shared by authoring and direct writes."""
    if not isinstance(weights, dict):
        return [(None, "must be an object")]
    errors: list[tuple[str | None, str]] = []
    positive = False
    total = 0.0
    for weather, weight in weights.items():
        key = str(weather)
        if key not in ALLOWED_WEATHER_CONDITIONS:
            errors.append((key, "unknown weather condition"))
            continue
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(float(weight))
            or float(weight) < 0
            or float(weight) > WEATHER_WEIGHT_LIMIT
        ):
            errors.append((key, "must be a finite non-negative number <= 1000000"))
            continue
        value = float(weight)
        if value > 0:
            positive = True
            total += value
    if weights and not positive:
        errors.append((None, "must contain at least one positive weight"))
    if not math.isfinite(total) or total > WEATHER_WEIGHT_LIMIT:
        errors.append((None, "total weight must be finite and <= 1000000"))
    return errors


def validate_weather_weights(weights: Any) -> dict[str, float]:
    errors = weather_weight_validation_errors(weights)
    if errors:
        detail = "; ".join(
            f"{key}: {message}" if key is not None else message
            for key, message in errors
        )
        raise ValueError(f"invalid weather_weights: {detail}")
    return {str(key): float(value) for key, value in weights.items()}

WORLD_SYSTEMS_SCHEMA = r'''
CREATE TABLE IF NOT EXISTS spatial_maps (
    campaign_id TEXT NOT NULL,id TEXT NOT NULL,name TEXT NOT NULL,scope_type TEXT NOT NULL DEFAULT 'location',scope_id TEXT,
    min_x INTEGER NOT NULL DEFAULT 0,max_x INTEGER NOT NULL DEFAULT 0,min_y INTEGER NOT NULL DEFAULT 0,max_y INTEGER NOT NULL DEFAULT 0,min_z INTEGER NOT NULL DEFAULT 0,max_z INTEGER NOT NULL DEFAULT 0,
    default_terrain TEXT NOT NULL DEFAULT 'open',default_walkable INTEGER NOT NULL DEFAULT 1,metadata_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS spatial_tiles (
    campaign_id TEXT NOT NULL,map_id TEXT NOT NULL,x INTEGER NOT NULL,y INTEGER NOT NULL,z INTEGER NOT NULL,terrain TEXT NOT NULL DEFAULT 'open',walkable INTEGER NOT NULL DEFAULT 1,
    move_cost REAL NOT NULL DEFAULT 1 CHECK(move_cost>0),blocks_los INTEGER NOT NULL DEFAULT 0,terrain_hp REAL,state_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,map_id,x,y,z),FOREIGN KEY(campaign_id,map_id) REFERENCES spatial_maps(campaign_id,id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_spatial_tiles_map ON spatial_tiles(campaign_id,map_id,z,y,x);
CREATE TABLE IF NOT EXISTS spatial_zones (
    campaign_id TEXT NOT NULL,map_id TEXT NOT NULL,id TEXT NOT NULL,name TEXT NOT NULL,min_x INTEGER NOT NULL,max_x INTEGER NOT NULL,min_y INTEGER NOT NULL,max_y INTEGER NOT NULL,min_z INTEGER NOT NULL,max_z INTEGER NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',state_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL,PRIMARY KEY(campaign_id,map_id,id),FOREIGN KEY(campaign_id,map_id) REFERENCES spatial_maps(campaign_id,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS spatial_portals (
    campaign_id TEXT NOT NULL,id TEXT NOT NULL,from_map_id TEXT NOT NULL,from_x INTEGER NOT NULL,from_y INTEGER NOT NULL,from_z INTEGER NOT NULL,to_map_id TEXT NOT NULL,to_x INTEGER NOT NULL,to_y INTEGER NOT NULL,to_z INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'portal',cost REAL NOT NULL DEFAULT 1,enabled INTEGER NOT NULL DEFAULT 1,bidirectional INTEGER NOT NULL DEFAULT 1,metadata_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),FOREIGN KEY(campaign_id,from_map_id) REFERENCES spatial_maps(campaign_id,id) ON DELETE CASCADE,FOREIGN KEY(campaign_id,to_map_id) REFERENCES spatial_maps(campaign_id,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS discoverables (
    campaign_id TEXT NOT NULL,id TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('secret','trap','clue','cache','other')),map_id TEXT,x INTEGER,y INTEGER,z INTEGER,dc INTEGER NOT NULL DEFAULT 10,
    revealed INTEGER NOT NULL DEFAULT 0,triggered INTEGER NOT NULL DEFAULT 0,payload_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL,PRIMARY KEY(campaign_id,id),FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS reward_packages (
    campaign_id TEXT NOT NULL,id TEXT NOT NULL,xp INTEGER NOT NULL DEFAULT 0,currency_json TEXT NOT NULL DEFAULT '{}',items_json TEXT NOT NULL DEFAULT '[]',reputation_json TEXT NOT NULL DEFAULT '{}',metadata_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS character_progression (
    campaign_id TEXT NOT NULL,character_id TEXT NOT NULL,mode TEXT NOT NULL DEFAULT 'xp' CHECK(mode IN ('xp','milestone')),xp INTEGER NOT NULL DEFAULT 0 CHECK(xp>=0),pending_level INTEGER,milestone_count INTEGER NOT NULL DEFAULT 0,class_id TEXT,last_level_up_at TEXT,updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,character_id),FOREIGN KEY(campaign_id,character_id) REFERENCES characters(campaign_id,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS owner_balances (
    campaign_id TEXT NOT NULL,owner_kind TEXT NOT NULL,owner_id TEXT NOT NULL,currency_key TEXT NOT NULL,amount REAL NOT NULL DEFAULT 0,updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,owner_kind,owner_id,currency_key),FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS quest_nodes (
    campaign_id TEXT NOT NULL,quest_id TEXT NOT NULL,id TEXT NOT NULL,node_type TEXT NOT NULL DEFAULT 'objective',status TEXT NOT NULL DEFAULT 'inactive',trigger_json TEXT NOT NULL DEFAULT '{}',success_json TEXT NOT NULL DEFAULT '{}',failure_json TEXT NOT NULL DEFAULT '{}',deadline_world_time TEXT,state_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,quest_id,id),FOREIGN KEY(campaign_id,quest_id) REFERENCES quests(campaign_id,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS quest_edges (
    campaign_id TEXT NOT NULL,quest_id TEXT NOT NULL,from_node TEXT NOT NULL,to_node TEXT NOT NULL,condition_json TEXT NOT NULL DEFAULT '{}',priority INTEGER NOT NULL DEFAULT 100,updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,quest_id,from_node,to_node),FOREIGN KEY(campaign_id,quest_id) REFERENCES quests(campaign_id,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS faction_relations (
    campaign_id TEXT NOT NULL,faction_a TEXT NOT NULL,faction_b TEXT NOT NULL,stance TEXT NOT NULL DEFAULT 'neutral',tension REAL NOT NULL DEFAULT 0,trust REAL NOT NULL DEFAULT 0,state_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,faction_a,faction_b),FOREIGN KEY(campaign_id,faction_a) REFERENCES factions(campaign_id,id) ON DELETE CASCADE,FOREIGN KEY(campaign_id,faction_b) REFERENCES factions(campaign_id,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS crimes (
    campaign_id TEXT NOT NULL,id TEXT NOT NULL,offender_kind TEXT NOT NULL,offender_id TEXT NOT NULL,jurisdiction TEXT NOT NULL,offense TEXT NOT NULL,severity REAL NOT NULL DEFAULT 1,evidence REAL NOT NULL DEFAULT 0,witnesses_json TEXT NOT NULL DEFAULT '[]',bounty REAL NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'open',metadata_json TEXT NOT NULL DEFAULT '{}',world_time TEXT NOT NULL,updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS rumors (
    campaign_id TEXT NOT NULL,id TEXT NOT NULL,claim TEXT NOT NULL,origin_event_id INTEGER,origin_location TEXT,truth_confidence REAL NOT NULL DEFAULT 0.5,distortion REAL NOT NULL DEFAULT 0,heard_by_json TEXT NOT NULL DEFAULT '[]',state_json TEXT NOT NULL DEFAULT '{}',created_world_time TEXT NOT NULL,updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS population_state (
    campaign_id TEXT NOT NULL,location_id TEXT NOT NULL,population REAL NOT NULL DEFAULT 0,food_capacity REAL NOT NULL DEFAULT 0,safety REAL NOT NULL DEFAULT 0.5,employment REAL NOT NULL DEFAULT 0.5,migration_pressure REAL NOT NULL DEFAULT 0,state_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,location_id),FOREIGN KEY(campaign_id,location_id) REFERENCES locations(campaign_id,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS divine_state (
    campaign_id TEXT NOT NULL,actor_kind TEXT NOT NULL,actor_id TEXT NOT NULL,power_id TEXT NOT NULL,favor REAL NOT NULL DEFAULT 0,corruption REAL NOT NULL DEFAULT 0,exposure REAL NOT NULL DEFAULT 0,state_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,actor_kind,actor_id,power_id),FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS visions (
    campaign_id TEXT NOT NULL,id TEXT NOT NULL,actor_kind TEXT NOT NULL,actor_id TEXT NOT NULL,power_id TEXT,kind TEXT NOT NULL DEFAULT 'vision',reason TEXT NOT NULL,payload_json TEXT NOT NULL DEFAULT '{}',delivered INTEGER NOT NULL DEFAULT 0,created_world_time TEXT NOT NULL,updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS afflictions (
    campaign_id TEXT NOT NULL,actor_kind TEXT NOT NULL,actor_id TEXT NOT NULL,id TEXT NOT NULL,kind TEXT NOT NULL,stage INTEGER NOT NULL DEFAULT 0,max_stage INTEGER NOT NULL DEFAULT 1,state_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,actor_kind,actor_id,id),FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS homesteads (
    campaign_id TEXT NOT NULL,id TEXT NOT NULL,owner_kind TEXT NOT NULL,owner_id TEXT NOT NULL,location_id TEXT NOT NULL,facilities_json TEXT NOT NULL DEFAULT '{}',storage_json TEXT NOT NULL DEFAULT '{}',state_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),FOREIGN KEY(campaign_id,location_id) REFERENCES locations(campaign_id,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS town_services (
    campaign_id TEXT NOT NULL,id TEXT NOT NULL,location_id TEXT NOT NULL,kind TEXT NOT NULL,name TEXT NOT NULL,operator_id TEXT,inventory_json TEXT NOT NULL DEFAULT '[]',schedule_json TEXT NOT NULL DEFAULT '{}',state_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),FOREIGN KEY(campaign_id,location_id) REFERENCES locations(campaign_id,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS regional_climate (
    campaign_id TEXT NOT NULL,scope_type TEXT NOT NULL,scope_id TEXT NOT NULL,climate TEXT NOT NULL DEFAULT 'temperate',season TEXT NOT NULL DEFAULT 'summer',weather_weights_json TEXT NOT NULL DEFAULT '{}',magic_theme TEXT,state_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,scope_type,scope_id),FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS encounter_templates (
    campaign_id TEXT NOT NULL,id TEXT NOT NULL,name TEXT NOT NULL,difficulty REAL NOT NULL DEFAULT 1,participants_json TEXT NOT NULL DEFAULT '[]',terrain_json TEXT NOT NULL DEFAULT '{}',objectives_json TEXT NOT NULL DEFAULT '[]',reinforcements_json TEXT NOT NULL DEFAULT '[]',rewards_json TEXT NOT NULL DEFAULT '{}',failure_json TEXT NOT NULL DEFAULT '{}',world_events_json TEXT NOT NULL DEFAULT '[]',updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
'''

class WorldSystemsKernel:
    XP_THRESHOLDS = {1:0,2:300,3:900,4:2700,5:6500,6:14000,7:23000,8:34000,9:48000,10:64000,11:85000,12:100000,13:120000,14:140000,15:165000,16:195000,17:225000,18:265000,19:305000,20:355000}
    def __init__(self, engine: Any): self.e=engine
    @classmethod
    def xp_threshold_for_level(cls, level: int) -> int:
        level=max(1,min(20,int(level))); return int(cls.XP_THRESHOLDS[level])
    @classmethod
    def level_for_xp(cls, xp: int) -> int:
        xp=max(0,int(xp)); level=1
        for candidate,threshold in sorted(cls.XP_THRESHOLDS.items()):
            if xp>=threshold: level=candidate
            else: break
        return level
    def _ensure_owner_db(self,db,campaign_id,kind,owner_id):
        if kind in {"character","npc"}: self.e._get_actor_db(db,campaign_id,kind,owner_id); return
        table={"faction":"factions","location":"locations"}.get(kind)
        if not table: raise ValueError("owner kind must be character, npc, faction, or location")
        if not db.execute(f"SELECT 1 FROM {table} WHERE campaign_id=? AND id=?",(campaign_id,owner_id)).fetchone(): raise KeyError(f"unknown {kind}: {owner_id}")

    def _wt(self,db,c):
        r=db.execute("SELECT world_time FROM campaigns WHERE id=?",(c,)).fetchone()
        if not r: raise KeyError(f"unknown campaign: {c}")
        return str(r["world_time"])
    def save_map(self,campaign_id,map_id,name,*,scope_type="location",scope_id=None,bounds=None,default_terrain="open",default_walkable=True,metadata=None):
        map_id=self.e._clean_id(map_id); b=bounds or {}; vals=[int(b.get(k,0)) for k in ("min_x","max_x","min_y","max_y","min_z","max_z")]
        if vals[0]>vals[1] or vals[2]>vals[3] or vals[4]>vals[5]: raise ValueError("invalid map bounds")
        with self.e._write_db() as db:
            db.execute("""INSERT INTO spatial_maps(campaign_id,id,name,scope_type,scope_id,min_x,max_x,min_y,max_y,min_z,max_z,default_terrain,default_walkable,metadata_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                          ON CONFLICT(campaign_id,id) DO UPDATE SET name=excluded.name,scope_type=excluded.scope_type,scope_id=excluded.scope_id,min_x=excluded.min_x,max_x=excluded.max_x,min_y=excluded.min_y,max_y=excluded.max_y,min_z=excluded.min_z,max_z=excluded.max_z,default_terrain=excluded.default_terrain,default_walkable=excluded.default_walkable,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                       (campaign_id,map_id,name,scope_type,scope_id,*vals,default_terrain,int(bool(default_walkable)),self.e._dumps(metadata or {}),self.e._now()))
        return self.get_map(campaign_id,map_id)
    def get_map(self,campaign_id,map_id):
        with self.e._db() as db:r=db.execute("SELECT * FROM spatial_maps WHERE campaign_id=? AND id=?",(campaign_id,map_id)).fetchone()
        if not r: raise KeyError(f"unknown spatial map: {map_id}")
        d=dict(r);d["default_walkable"]=bool(d["default_walkable"]);d["metadata"]=self.e._loads(d.pop("metadata_json"));return d
    def save_tile(self,campaign_id,map_id,x,y,z,*,terrain="open",walkable=True,move_cost=1,blocks_los=False,terrain_hp=None,state=None):
        self.get_map(campaign_id,map_id);x,y,z=int(x),int(y),int(z);move_cost=float(move_cost)
        if move_cost<=0: raise ValueError("move_cost must be >0")
        state=dict(state or {})
        state["_base_move_cost"]=move_cost
        if terrain_hp is not None: state.setdefault("_max_terrain_hp",float(terrain_hp))
        with self.e._write_db() as db:
            m=db.execute("SELECT * FROM spatial_maps WHERE campaign_id=? AND id=?",(campaign_id,map_id)).fetchone()
            if not (m["min_x"]<=x<=m["max_x"] and m["min_y"]<=y<=m["max_y"] and m["min_z"]<=z<=m["max_z"]): raise ValueError("tile outside map bounds")
            db.execute("""INSERT INTO spatial_tiles(campaign_id,map_id,x,y,z,terrain,walkable,move_cost,blocks_los,terrain_hp,state_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                          ON CONFLICT(campaign_id,map_id,x,y,z) DO UPDATE SET terrain=excluded.terrain,walkable=excluded.walkable,move_cost=excluded.move_cost,blocks_los=excluded.blocks_los,terrain_hp=excluded.terrain_hp,state_json=excluded.state_json,updated_at=excluded.updated_at""",
                       (campaign_id,map_id,x,y,z,terrain,int(bool(walkable)),move_cost,int(bool(blocks_los)),terrain_hp,self.e._dumps(state),self.e._now()))
        return {"campaign_id":campaign_id,"map_id":map_id,"x":x,"y":y,"z":z,"terrain":terrain,"walkable":bool(walkable),"move_cost":move_cost,"blocks_los":bool(blocks_los),"terrain_hp":terrain_hp,"state":state}
    def save_zone(self,campaign_id,map_id,zone_id,name,*,bounds,tags=None,state=None):
        b={k:int(bounds[k]) for k in ("min_x","max_x","min_y","max_y","min_z","max_z")};self.get_map(campaign_id,map_id)
        with self.e._write_db() as db: db.execute("""INSERT INTO spatial_zones(campaign_id,map_id,id,name,min_x,max_x,min_y,max_y,min_z,max_z,tags_json,state_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(campaign_id,map_id,id) DO UPDATE SET name=excluded.name,min_x=excluded.min_x,max_x=excluded.max_x,min_y=excluded.min_y,max_y=excluded.max_y,min_z=excluded.min_z,max_z=excluded.max_z,tags_json=excluded.tags_json,state_json=excluded.state_json,updated_at=excluded.updated_at""",
            (campaign_id,map_id,zone_id,name,b["min_x"],b["max_x"],b["min_y"],b["max_y"],b["min_z"],b["max_z"],self.e._dumps(tags or []),self.e._dumps(state or {}),self.e._now()))
        return {"campaign_id":campaign_id,"map_id":map_id,"zone_id":zone_id,"name":name,"bounds":b}
    def save_portal(self,campaign_id,portal_id,from_map_id,from_pos,to_map_id,to_pos,*,kind="portal",cost=1,enabled=True,bidirectional=True,metadata=None):
        self.get_map(campaign_id,from_map_id);self.get_map(campaign_id,to_map_id);f=tuple(map(int,from_pos));t=tuple(map(int,to_pos))
        with self.e._write_db() as db: db.execute("""INSERT INTO spatial_portals(campaign_id,id,from_map_id,from_x,from_y,from_z,to_map_id,to_x,to_y,to_z,kind,cost,enabled,bidirectional,metadata_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(campaign_id,id) DO UPDATE SET from_map_id=excluded.from_map_id,from_x=excluded.from_x,from_y=excluded.from_y,from_z=excluded.from_z,to_map_id=excluded.to_map_id,to_x=excluded.to_x,to_y=excluded.to_y,to_z=excluded.to_z,kind=excluded.kind,cost=excluded.cost,enabled=excluded.enabled,bidirectional=excluded.bidirectional,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
            (campaign_id,portal_id,from_map_id,*f,to_map_id,*t,kind,float(cost),int(bool(enabled)),int(bool(bidirectional)),self.e._dumps(metadata or {}),self.e._now()))
        return {"campaign_id":campaign_id,"id":portal_id,"from":{"map_id":from_map_id,"pos":list(f)},"to":{"map_id":to_map_id,"pos":list(t)},"kind":kind,"cost":float(cost),"enabled":bool(enabled),"bidirectional":bool(bidirectional)}
    def _map_rows(self,db,campaign_id,map_id):
        m=db.execute("SELECT * FROM spatial_maps WHERE campaign_id=? AND id=?",(campaign_id,map_id)).fetchone();
        if not m: raise KeyError(f"unknown map: {map_id}")
        tiles={(int(r["x"]),int(r["y"]),int(r["z"])):r for r in db.execute("SELECT * FROM spatial_tiles WHERE campaign_id=? AND map_id=?",(campaign_id,map_id))}
        return m,tiles
    def find_path(self,campaign_id,start,goal,*,max_expanded=50000,allow_diagonal=False):
        smap=str(start["map_id"]);gmap=str(goal["map_id"]);s=(int(start["x"]),int(start["y"]),int(start.get("z",0)));g=(int(goal["x"]),int(goal["y"]),int(goal.get("z",0)))
        with self.e._db() as db:
            maps={r["id"]:r for r in db.execute("SELECT * FROM spatial_maps WHERE campaign_id=?",(campaign_id,))};
            if smap not in maps or gmap not in maps: raise KeyError("unknown spatial map")
            tile_rows=db.execute("SELECT * FROM spatial_tiles WHERE campaign_id=?",(campaign_id,)).fetchall();tiles={(r["map_id"],int(r["x"]),int(r["y"]),int(r["z"])):r for r in tile_rows}
            portals=list(db.execute("SELECT * FROM spatial_portals WHERE campaign_id=? AND enabled=1 ORDER BY id",(campaign_id,)))
        def props(mid,p):
            m=maps[mid];r=tiles.get((mid,*p));walk=bool(r["walkable"]) if r else bool(m["default_walkable"]);cost=float(r["move_cost"]) if r else 1.0;return walk,cost
        def inbounds(mid,p):
            m=maps[mid];return m["min_x"]<=p[0]<=m["max_x"] and m["min_y"]<=p[1]<=m["max_y"] and m["min_z"]<=p[2]<=m["max_z"]
        pmap={}
        for r in portals:
            a=(r["from_map_id"],int(r["from_x"]),int(r["from_y"]),int(r["from_z"]));b=(r["to_map_id"],int(r["to_x"]),int(r["to_y"]),int(r["to_z"]));pmap.setdefault(a,[]).append((b,float(r["cost"]),r["id"]));
            if r["bidirectional"]: pmap.setdefault(b,[]).append((a,float(r["cost"]),r["id"]))
        startn=(smap,*s);goaln=(gmap,*g);q=[(0.0,0,startn)];dist={startn:0.0};parent={};expanded=0;seq=0
        dirs=[(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)]
        if allow_diagonal: dirs += [(dx,dy,0) for dx in (-1,1) for dy in (-1,1)]
        while q and expanded<int(max_expanded):
            d,_,node=heapq.heappop(q)
            if d!=dist.get(node):continue
            expanded+=1
            if node==goaln:break
            mid,x,y,z=node
            for dx,dy,dz in dirs:
                np=(x+dx,y+dy,z+dz)
                if not inbounds(mid,np):continue
                walk,c=props(mid,np)
                if not walk:continue
                nn=(mid,*np);nd=d+c*(math.sqrt(2) if dx and dy else 1)
                if nd<dist.get(nn,float('inf')):dist[nn]=nd;parent[nn]=(node,None);seq+=1;heapq.heappush(q,(nd,seq,nn))
            for nn,c,pid in pmap.get(node,[]):
                np=(nn[1],nn[2],nn[3]);walk,_=props(nn[0],np)
                if not walk:continue
                nd=d+c
                if nd<dist.get(nn,float('inf')):dist[nn]=nd;parent[nn]=(node,pid);seq+=1;heapq.heappush(q,(nd,seq,nn))
        if goaln not in dist:return {"found":False,"path":[],"cost":None,"expanded":expanded}
        rev=[];cur=goaln
        while cur!=startn:
            prev,pid=parent[cur];rev.append({"map_id":cur[0],"x":cur[1],"y":cur[2],"z":cur[3],"via_portal":pid});cur=prev
        rev.append({"map_id":startn[0],"x":startn[1],"y":startn[2],"z":startn[3],"via_portal":None});rev.reverse()
        return {"found":True,"path":rev,"cost":dist[goaln],"expanded":expanded}
    def _damage_tile_db(self,db,campaign_id,map_id,x,y,z,damage,*,reason="terrain damaged",revision=None,emit_event=True):
        damage=float(damage);x,y,z=int(x),int(y),int(z)
        r=db.execute("SELECT * FROM spatial_tiles WHERE campaign_id=? AND map_id=? AND x=? AND y=? AND z=?",(campaign_id,map_id,x,y,z)).fetchone()
        if not r or r["terrain_hp"] is None: raise ValueError("tile has no persistent terrain_hp")
        old_hp=float(r["terrain_hp"])
        hp=max(0.0,old_hp-max(0.0,damage));walk=bool(r["walkable"]);blocks=bool(r["blocks_los"]);state=self.e._loads(r["state_json"])
        destroyed=hp<=0
        newly_destroyed=old_hp>0 and destroyed
        if destroyed: state["destroyed"]=True;blocks=False;walk=True
        db.execute("UPDATE spatial_tiles SET terrain_hp=?,walkable=?,blocks_los=?,state_json=?,updated_at=? WHERE campaign_id=? AND map_id=? AND x=? AND y=? AND z=?",(hp,int(walk),int(blocks),self.e._dumps(state),self.e._now(),campaign_id,map_id,x,y,z))
        rev=int(revision) if revision is not None else self.e._next_revision(db,campaign_id)
        if emit_event:
            self.e._insert_event(db,campaign_id,rev,"terrain_damage",reason,payload={"map_id":map_id,"x":x,"y":y,"z":z,"damage":damage,"remaining_hp":hp,"destroyed":destroyed})
        if newly_destroyed and not state.get("_collapse_processed"):
            state["_collapse_processed"]=True
            db.execute("UPDATE spatial_tiles SET state_json=?,updated_at=? WHERE campaign_id=? AND map_id=? AND x=? AND y=? AND z=?",(self.e._dumps(state),self.e._now(),campaign_id,map_id,x,y,z))
            above=db.execute("SELECT * FROM spatial_tiles WHERE campaign_id=? AND map_id=? AND x=? AND y=? AND z=? AND terrain_hp IS NOT NULL",(campaign_id,map_id,x,y,z+1)).fetchone()
            if above and not self.e._loads(above["state_json"] or "{}").get("destroyed"):
                shock=max(1.0,float(above["terrain_hp"])*0.5)
                self._damage_tile_db(db,campaign_id,map_id,x,y,z+1,shock,reason="support collapse",revision=rev,emit_event=emit_event)
        return {"campaign_id":campaign_id,"map_id":map_id,"x":x,"y":y,"z":z,"remaining_hp":hp,"destroyed":destroyed,"newly_destroyed":newly_destroyed}
    def damage_tile(self,campaign_id,map_id,x,y,z,damage,*,reason="terrain damaged"):
        with self.e._write_db() as db:
            return self._damage_tile_db(db,campaign_id,map_id,x,y,z,damage,reason=reason,revision=None,emit_event=True)
    def save_discoverable(self,campaign_id,object_id,kind,*,map_id=None,x=None,y=None,z=None,dc=10,payload=None):
        if kind not in {"secret","trap","clue","cache","other"}:raise ValueError("invalid discoverable kind")
        with self.e._write_db() as db:db.execute("""INSERT INTO discoverables(campaign_id,id,kind,map_id,x,y,z,dc,revealed,triggered,payload_json,updated_at) VALUES(?,?,?,?,?,?,?,?,0,0,?,?)
            ON CONFLICT(campaign_id,id) DO UPDATE SET kind=excluded.kind,map_id=excluded.map_id,x=excluded.x,y=excluded.y,z=excluded.z,dc=excluded.dc,payload_json=excluded.payload_json,updated_at=excluded.updated_at""",(campaign_id,object_id,kind,map_id,x,y,z,int(dc),self.e._dumps(payload or {}),self.e._now()))
        return {"campaign_id":campaign_id,"id":object_id,"kind":kind,"dc":int(dc),"revealed":False,"triggered":False}
    def passive_scan(self,campaign_id,perception,*,map_id=None,position=None,radius=20):
        perception=int(perception);out=[]
        with self.e._write_db() as db:
            rows=db.execute("SELECT * FROM discoverables WHERE campaign_id=? AND revealed=0 ORDER BY id",(campaign_id,)).fetchall()
            for r in rows:
                if map_id and r["map_id"]!=map_id:continue
                if position and r["x"] is not None:
                    if math.dist((float(r["x"]),float(r["y"]),float(r["z"] or 0)),tuple(map(float,position)))>float(radius):continue
                if perception>=int(r["dc"]):db.execute("UPDATE discoverables SET revealed=1,updated_at=? WHERE campaign_id=? AND id=?",(self.e._now(),campaign_id,r["id"]));out.append(r["id"])
        return {"campaign_id":campaign_id,"revealed":out,"count":len(out)}
    def _progression_db(self,db,campaign_id,character_id):
        char=self.e._get_character_db(db,campaign_id,character_id)
        row=db.execute("SELECT * FROM character_progression WHERE campaign_id=? AND character_id=?",(campaign_id,character_id)).fetchone()
        if not row:
            xp=self.xp_threshold_for_level(int(char["level"])); now=self.e._now()
            db.execute("INSERT INTO character_progression(campaign_id,character_id,mode,xp,pending_level,milestone_count,class_id,last_level_up_at,updated_at) VALUES(?,?,'xp',?,NULL,0,NULL,NULL,?)",(campaign_id,character_id,xp,now))
            row=db.execute("SELECT * FROM character_progression WHERE campaign_id=? AND character_id=?",(campaign_id,character_id)).fetchone()
        return char,row
    def _progression_report(self,char,row):
        current=int(char["level"]); xp=int(row["xp"]); eligible=self.level_for_xp(xp) if row["mode"]=="xp" else max(current,int(row["pending_level"] or current))
        pending=max(current+1,eligible) if eligible>current else (int(row["pending_level"]) if row["pending_level"] and int(row["pending_level"])>current else None)
        next_threshold=self.XP_THRESHOLDS.get(current+1)
        return {"mode":row["mode"],"xp":xp,"current_level":current,"eligible_level":eligible,"pending_level":pending,"level_up_available":bool(pending and pending>current),"xp_to_next_level":max(0,int(next_threshold)-xp) if next_threshold is not None else 0,"class_id":row["class_id"],"milestone_count":int(row["milestone_count"])}
    def get_progression(self,campaign_id,character_id):
        with self.e._write_db() as db:
            char,row=self._progression_db(db,campaign_id,character_id); report=self._progression_report(char,row)
        return {"campaign_id":campaign_id,"character_id":character_id,**report}
    def set_progression(self,campaign_id,character_id,*,mode="xp",xp=None,class_id=None):
        mode=str(mode).lower()
        if mode not in {"xp","milestone"}: raise ValueError("mode must be xp or milestone")
        with self.e._write_db() as db:
            char,row=self._progression_db(db,campaign_id,character_id); value=int(row["xp"] if xp is None else xp)
            if value<0: raise ValueError("xp must be nonnegative")
            db.execute("UPDATE character_progression SET mode=?,xp=?,class_id=?,pending_level=NULL,updated_at=? WHERE campaign_id=? AND character_id=?",(mode,value,class_id,self.e._now(),campaign_id,character_id)); row=db.execute("SELECT * FROM character_progression WHERE campaign_id=? AND character_id=?",(campaign_id,character_id)).fetchone(); report=self._progression_report(char,row)
        return {"campaign_id":campaign_id,"character_id":character_id,**report}
    def award_xp(self,campaign_id,character_id,amount,*,reason="experience awarded"):
        amount=int(amount)
        if amount<0: raise ValueError("XP award must be nonnegative")
        with self.e._write_db() as db:
            char,row=self._progression_db(db,campaign_id,character_id)
            if row["mode"]!="xp": raise ValueError("character progression mode is milestone")
            old=int(row["xp"]); new=old+amount; eligible=self.level_for_xp(new); pending=eligible if eligible>int(char["level"]) else None
            db.execute("UPDATE character_progression SET xp=?,pending_level=?,updated_at=? WHERE campaign_id=? AND character_id=?",(new,pending,self.e._now(),campaign_id,character_id)); rev=self.e._next_revision(db,campaign_id); self.e._insert_event(db,campaign_id,rev,"xp_award",reason,actor_id=character_id,payload={"amount":amount,"xp_before":old,"xp_after":new,"eligible_level":eligible,"pending_level":pending}); row=db.execute("SELECT * FROM character_progression WHERE campaign_id=? AND character_id=?",(campaign_id,character_id)).fetchone(); report=self._progression_report(char,row)
        return {"campaign_id":campaign_id,"character_id":character_id,"revision":rev,"xp_awarded":amount,**report}
    def award_milestone(self,campaign_id,character_id,*,target_level=None,reason="milestone reached"):
        with self.e._write_db() as db:
            char,row=self._progression_db(db,campaign_id,character_id)
            if row["mode"]!="milestone": raise ValueError("character progression mode is xp")
            current=int(char["level"]); target=int(target_level or min(20,current+1))
            if not current<target<=20: raise ValueError("milestone target must be above current level and <=20")
            count=int(row["milestone_count"])+1; db.execute("UPDATE character_progression SET pending_level=?,milestone_count=?,updated_at=? WHERE campaign_id=? AND character_id=?",(target,count,self.e._now(),campaign_id,character_id)); rev=self.e._next_revision(db,campaign_id); self.e._insert_event(db,campaign_id,rev,"milestone",reason,actor_id=character_id,payload={"target_level":target,"milestone_count":count}); row=db.execute("SELECT * FROM character_progression WHERE campaign_id=? AND character_id=?",(campaign_id,character_id)).fetchone(); report=self._progression_report(char,row)
        return {"campaign_id":campaign_id,"character_id":character_id,"revision":rev,**report}

    def save_reward(self,campaign_id,reward_id,*,xp=0,currency=None,items=None,reputation=None,metadata=None):
        with self.e._write_db() as db:db.execute("""INSERT INTO reward_packages(campaign_id,id,xp,currency_json,items_json,reputation_json,metadata_json,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(campaign_id,id) DO UPDATE SET xp=excluded.xp,currency_json=excluded.currency_json,items_json=excluded.items_json,reputation_json=excluded.reputation_json,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",(campaign_id,reward_id,int(xp),self.e._dumps(currency or {}),self.e._dumps(items or []),self.e._dumps(reputation or {}),self.e._dumps(metadata or {}),self.e._now()))
        return {"campaign_id":campaign_id,"id":reward_id,"xp":int(xp),"currency":currency or {},"items":items or [],"reputation":reputation or {}}
    def grant_reward(self,campaign_id,reward_id,actor_kind,actor_id,*,reason="reward granted"):
        with self.e._write_db() as db:
            r=db.execute("SELECT * FROM reward_packages WHERE campaign_id=? AND id=?",(campaign_id,reward_id)).fetchone()
            if not r: raise KeyError(f"unknown reward: {reward_id}")
            self._ensure_owner_db(db,campaign_id,actor_kind,actor_id)
            items=self.e._loads(r["items_json"]); currency=self.e._loads(r["currency_json"]); reputation=self.e._loads(r["reputation_json"])
            for item in items:
                iid=str(item["item_id"]); qty=float(item.get("qty",1))
                if qty<0: raise ValueError("reward item quantity must be nonnegative")
                old=db.execute("SELECT qty FROM inventories WHERE campaign_id=? AND owner_kind=? AND owner_id=? AND item_id=?",(campaign_id,actor_kind,actor_id,iid)).fetchone(); new=(float(old["qty"]) if old else 0)+qty
                db.execute("""INSERT INTO inventories(campaign_id,owner_kind,owner_id,item_id,qty,metadata_json,updated_at) VALUES(?,?,?,?,?,'{}',?) ON CONFLICT(campaign_id,owner_kind,owner_id,item_id) DO UPDATE SET qty=excluded.qty,updated_at=excluded.updated_at""",(campaign_id,actor_kind,actor_id,iid,new,self.e._now()))
            for key,amount in currency.items():
                value=float(amount)
                if value<0: raise ValueError("reward currency amount must be nonnegative")
                db.execute("""INSERT INTO owner_balances(campaign_id,owner_kind,owner_id,currency_key,amount,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(campaign_id,owner_kind,owner_id,currency_key) DO UPDATE SET amount=owner_balances.amount+excluded.amount,updated_at=excluded.updated_at""",(campaign_id,actor_kind,actor_id,str(key),value,self.e._now()))
            applied_rep={}
            for faction_id,delta in reputation.items():
                f=db.execute("SELECT reputation FROM factions WHERE campaign_id=? AND id=?",(campaign_id,str(faction_id))).fetchone()
                if not f: raise KeyError(f"unknown faction in reward reputation: {faction_id}")
                value=max(-10,min(10,int(f["reputation"])+int(delta))); db.execute("UPDATE factions SET reputation=?,updated_at=? WHERE campaign_id=? AND id=?",(value,self.e._now(),campaign_id,str(faction_id))); applied_rep[str(faction_id)]=value
            progression=None
            xp=int(r["xp"])
            if xp and actor_kind=="character":
                char,prow=self._progression_db(db,campaign_id,actor_id)
                if prow["mode"]=="xp":
                    oldxp=int(prow["xp"]); newxp=oldxp+xp; eligible=self.level_for_xp(newxp); pending=eligible if eligible>int(char["level"]) else None
                    db.execute("UPDATE character_progression SET xp=?,pending_level=?,updated_at=? WHERE campaign_id=? AND character_id=?",(newxp,pending,self.e._now(),campaign_id,actor_id)); prow=db.execute("SELECT * FROM character_progression WHERE campaign_id=? AND character_id=?",(campaign_id,actor_id)).fetchone(); progression=self._progression_report(char,prow); progression.update({"xp_awarded":xp,"xp_before":oldxp,"xp_after":newxp})
                else:
                    progression=self._progression_report(char,prow); progression.update({"xp_awarded":0,"xp_ignored_reason":"milestone_mode"})
            rev=self.e._next_revision(db,campaign_id); self.e._insert_event(db,campaign_id,rev,"reward",reason,actor_id=actor_id,payload={"reward_id":reward_id,"xp":xp,"items":items,"currency":currency,"reputation":reputation,"progression":progression})
        return {"campaign_id":campaign_id,"reward_id":reward_id,"actor_kind":actor_kind,"actor_id":actor_id,"revision":rev,"xp":xp,"items":items,"currency":currency,"reputation":applied_rep,"progression":progression}
    def save_quest_node(self,campaign_id,quest_id,node_id,*,node_type="objective",status="inactive",trigger=None,success=None,failure=None,deadline_world_time=None,state=None):
        with self.e._write_db() as db:
            if not db.execute("SELECT 1 FROM quests WHERE campaign_id=? AND id=?",(campaign_id,quest_id)).fetchone():raise KeyError(f"unknown quest: {quest_id}")
            db.execute("""INSERT INTO quest_nodes(campaign_id,quest_id,id,node_type,status,trigger_json,success_json,failure_json,deadline_world_time,state_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(campaign_id,quest_id,id) DO UPDATE SET node_type=excluded.node_type,status=excluded.status,trigger_json=excluded.trigger_json,success_json=excluded.success_json,failure_json=excluded.failure_json,deadline_world_time=excluded.deadline_world_time,state_json=excluded.state_json,updated_at=excluded.updated_at""",(campaign_id,quest_id,node_id,node_type,status,self.e._dumps(trigger or {}),self.e._dumps(success or {}),self.e._dumps(failure or {}),deadline_world_time,self.e._dumps(state or {}),self.e._now()))
        return {"campaign_id":campaign_id,"quest_id":quest_id,"id":node_id,"status":status}
    def save_quest_edge(self,campaign_id,quest_id,from_node,to_node,*,condition=None,priority=100):
        with self.e._write_db() as db:db.execute("""INSERT INTO quest_edges(campaign_id,quest_id,from_node,to_node,condition_json,priority,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(campaign_id,quest_id,from_node,to_node) DO UPDATE SET condition_json=excluded.condition_json,priority=excluded.priority,updated_at=excluded.updated_at""",(campaign_id,quest_id,from_node,to_node,self.e._dumps(condition or {}),int(priority),self.e._now()))
        return {"campaign_id":campaign_id,"quest_id":quest_id,"from_node":from_node,"to_node":to_node}
    def save_faction_relation(self,campaign_id,faction_a,faction_b,*,stance="neutral",tension=0,trust=0,state=None):
        if faction_a==faction_b:raise ValueError("factions must differ")
        a,b=sorted((faction_a,faction_b))
        with self.e._write_db() as db:db.execute("""INSERT INTO faction_relations(campaign_id,faction_a,faction_b,stance,tension,trust,state_json,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(campaign_id,faction_a,faction_b) DO UPDATE SET stance=excluded.stance,tension=excluded.tension,trust=excluded.trust,state_json=excluded.state_json,updated_at=excluded.updated_at""",(campaign_id,a,b,stance,float(tension),float(trust),self.e._dumps(state or {}),self.e._now()))
        return {"campaign_id":campaign_id,"faction_a":a,"faction_b":b,"stance":stance,"tension":float(tension),"trust":float(trust)}
    def record_crime(self,campaign_id,crime_id,offender_kind,offender_id,jurisdiction,offense,*,severity=1,evidence=0,witnesses=None,bounty=None,metadata=None):
        with self.e._write_db() as db:
            wt=self._wt(db,campaign_id);b=float(bounty if bounty is not None else max(0,float(severity)*max(.1,float(evidence))*10))
            db.execute("""INSERT INTO crimes(campaign_id,id,offender_kind,offender_id,jurisdiction,offense,severity,evidence,witnesses_json,bounty,status,metadata_json,world_time,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,'open',?,?,?) ON CONFLICT(campaign_id,id) DO UPDATE SET evidence=excluded.evidence,witnesses_json=excluded.witnesses_json,bounty=excluded.bounty,status='open',metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",(campaign_id,crime_id,offender_kind,offender_id,jurisdiction,offense,float(severity),float(evidence),self.e._dumps(witnesses or []),b,self.e._dumps(metadata or {}),wt,self.e._now()))
        return {"campaign_id":campaign_id,"id":crime_id,"bounty":b,"status":"open"}
    def save_rumor(self,campaign_id,rumor_id,claim,*,origin_event_id=None,origin_location=None,truth_confidence=.5,distortion=0,heard_by=None,state=None):
        with self.e._write_db() as db:
            wt=self._wt(db,campaign_id);db.execute("""INSERT INTO rumors(campaign_id,id,claim,origin_event_id,origin_location,truth_confidence,distortion,heard_by_json,state_json,created_world_time,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(campaign_id,id) DO UPDATE SET claim=excluded.claim,truth_confidence=excluded.truth_confidence,distortion=excluded.distortion,heard_by_json=excluded.heard_by_json,state_json=excluded.state_json,updated_at=excluded.updated_at""",(campaign_id,rumor_id,claim,origin_event_id,origin_location,float(truth_confidence),float(distortion),self.e._dumps(heard_by or []),self.e._dumps(state or {}),wt,self.e._now()))
        return {"campaign_id":campaign_id,"id":rumor_id,"claim":claim,"truth_confidence":float(truth_confidence),"distortion":float(distortion)}
    def propagate_rumor(self,campaign_id,rumor_id,npc_id,*,distortion_delta=.02,confidence_decay=.01):
        with self.e._write_db() as db:
            r=db.execute("SELECT * FROM rumors WHERE campaign_id=? AND id=?",(campaign_id,rumor_id)).fetchone();
            if not r:raise KeyError(f"unknown rumor: {rumor_id}")
            heard=self.e._loads(r["heard_by_json"]);heard=sorted(set([*heard,npc_id]));dist=min(1,float(r["distortion"])+max(0,float(distortion_delta)));conf=max(0,float(r["truth_confidence"])-max(0,float(confidence_decay)))
            db.execute("UPDATE rumors SET heard_by_json=?,distortion=?,truth_confidence=?,updated_at=? WHERE campaign_id=? AND id=?",(self.e._dumps(heard),dist,conf,self.e._now(),campaign_id,rumor_id))
        return {"campaign_id":campaign_id,"id":rumor_id,"heard_by":heard,"distortion":dist,"truth_confidence":conf}
    def set_population(
        self, campaign_id, location_id, population, *, food_capacity=0,
        safety=.5, employment=.5, migration_pressure=0, state=None
    ):
        """Set the aggregate total while keeping authoritative cohorts in sync."""
        values = {
            "population": population,
            "food_capacity": food_capacity,
            "safety": safety,
            "employment": employment,
            "migration_pressure": migration_pressure,
        }
        for label, raw in values.items():
            if isinstance(raw, bool) or not math.isfinite(float(raw)):
                raise ValueError(f"{label} must be finite")
        population = max(0.0, float(population))
        with self.e._write_db() as db:
            db.execute(
                """INSERT INTO population_state(
                       campaign_id,location_id,population,food_capacity,safety,employment,
                       migration_pressure,state_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,location_id) DO UPDATE SET
                       population=excluded.population,food_capacity=excluded.food_capacity,
                       safety=excluded.safety,employment=excluded.employment,
                       migration_pressure=excluded.migration_pressure,state_json=excluded.state_json,
                       updated_at=excluded.updated_at""",
                (campaign_id, location_id, population, float(food_capacity), float(safety),
                 float(employment), float(migration_pressure), self.e._dumps(state or {}),
                 self.e._now()),
            )
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='population_cohorts'"
            ).fetchone():
                from .population import PopulationKernel

                kernel = PopulationKernel(self.e)
                rows = db.execute(
                    "SELECT id,count FROM population_cohorts WHERE campaign_id=? AND location_id=? ORDER BY id",
                    (campaign_id, location_id),
                ).fetchall()
                total = sum(max(0.0, float(row["count"])) for row in rows)
                if rows and total > 0:
                    factor = population / total
                    for row in rows:
                        db.execute(
                            "UPDATE population_cohorts SET count=?,updated_at=? WHERE campaign_id=? AND id=?",
                            (max(0.0, float(row["count"]) * factor), self.e._now(),
                             campaign_id, row["id"]),
                        )
                elif rows:
                    db.execute(
                        "UPDATE population_cohorts SET count=0,updated_at=? WHERE campaign_id=? AND location_id=?",
                        (self.e._now(), campaign_id, location_id),
                    )
                    if population > 0:
                        kernel._upsert_cohort_db(
                            db, campaign_id, kernel._legacy_cohort_id(location_id), location_id,
                            count=population, state={"legacy_aggregate": True},
                            preserve_cursor=False,
                        )
                elif population > 0:
                    kernel._bootstrap_location_db(db, campaign_id, location_id)
                else:
                    kernel._ensure_profile_db(db, campaign_id, location_id, population_hint=0)
                kernel._sync_population_summary_db(db, campaign_id, location_id)
        return {"campaign_id": campaign_id, "location_id": location_id, "population": population}

    def migrate(self,campaign_id,origin,destination,count,*,reason="migration"):
        if isinstance(count, bool) or not math.isfinite(float(count)):
            raise ValueError("count must be finite")
        count = max(0.0, float(count))
        if origin == destination:
            raise ValueError("origin and destination must differ")
        with self.e._write_db() as db:
            from .population import PopulationKernel

            kernel = PopulationKernel(self.e)
            kernel._bootstrap_location_db(db, campaign_id, origin)
            kernel._bootstrap_location_db(db, campaign_id, destination)
            a = db.execute(
                "SELECT population FROM population_state WHERE campaign_id=? AND location_id=?",
                (campaign_id, origin),
            ).fetchone()
            b = db.execute(
                "SELECT population FROM population_state WHERE campaign_id=? AND location_id=?",
                (campaign_id, destination),
            ).fetchone()
            if not a or not b:
                raise ValueError("both population records must exist")
            moved = min(count, max(0.0, float(a["population"])))
            when = kernel._campaign_time_db(db, campaign_id)
            rows = db.execute(
                "SELECT * FROM population_cohorts WHERE campaign_id=? AND location_id=? AND count>0 ORDER BY id",
                (campaign_id, origin),
            ).fetchall()
            cohort_total = sum(float(row["count"]) for row in rows)
            if moved > 0 and cohort_total > 0:
                remaining = moved
                for index, row in enumerate(rows):
                    available = max(0.0, float(row["count"]))
                    proportional = moved * available / cohort_total
                    share = min(
                        remaining,
                        available,
                        remaining if index == len(rows) - 1 else proportional,
                    )
                    if share <= 0:
                        continue
                    dest_id = kernel._destination_cohort_db(
                        db, campaign_id, row, destination, when=when,
                    )
                    db.execute(
                        "UPDATE population_cohorts SET count=MAX(0,count-?),updated_at=? WHERE campaign_id=? AND id=?",
                        (share, self.e._now(), campaign_id, row["id"]),
                    )
                    db.execute(
                        "UPDATE population_cohorts SET count=count+?,last_processed_world_time=?,updated_at=? WHERE campaign_id=? AND id=?",
                        (share, when.isoformat(), self.e._now(), campaign_id, dest_id),
                    )
                    remaining = max(0.0, remaining - share)
                moved = max(0.0, moved - remaining)
                kernel._sync_population_summary_db(db, campaign_id, origin)
                kernel._sync_population_summary_db(db, campaign_id, destination)
            else:
                db.execute(
                    "UPDATE population_state SET population=population-?,updated_at=? WHERE campaign_id=? AND location_id=?",
                    (moved, self.e._now(), campaign_id, origin),
                )
                db.execute(
                    "UPDATE population_state SET population=population+?,updated_at=? WHERE campaign_id=? AND location_id=?",
                    (moved, self.e._now(), campaign_id, destination),
                )
            rev = self.e._next_revision(db, campaign_id)
            kernel._record_flow_db(
                db, campaign_id, rev, flow_key=f"explicit:{rev}:{origin}:{destination}",
                kind="migration", count=moved, reason=reason, when=when,
                origin=origin, destination=destination, state={"explicit": True},
            )
            self.e._insert_event(
                db, campaign_id, rev, "migration", reason, region=destination,
                payload={"origin": origin, "destination": destination, "count": moved},
            )
        return {"campaign_id":campaign_id,"origin":origin,"destination":destination,"moved":moved}
    def set_divine_state(self,campaign_id,actor_kind,actor_id,power_id,*,favor=0,corruption=0,exposure=0,state=None):
        with self.e._write_db() as db:db.execute("""INSERT INTO divine_state(campaign_id,actor_kind,actor_id,power_id,favor,corruption,exposure,state_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(campaign_id,actor_kind,actor_id,power_id) DO UPDATE SET favor=excluded.favor,corruption=excluded.corruption,exposure=excluded.exposure,state_json=excluded.state_json,updated_at=excluded.updated_at""",(campaign_id,actor_kind,actor_id,power_id,float(favor),float(corruption),float(exposure),self.e._dumps(state or {}),self.e._now()))
        return {"campaign_id":campaign_id,"actor_kind":actor_kind,"actor_id":actor_id,"power_id":power_id,"favor":float(favor),"corruption":float(corruption),"exposure":float(exposure)}
    def add_vision(self,campaign_id,vision_id,actor_kind,actor_id,reason,*,power_id=None,kind="vision",payload=None):
        with self.e._write_db() as db:
            wt=self._wt(db,campaign_id);db.execute("""INSERT INTO visions(campaign_id,id,actor_kind,actor_id,power_id,kind,reason,payload_json,delivered,created_world_time,updated_at) VALUES(?,?,?,?,?,?,?,?,0,?,?) ON CONFLICT(campaign_id,id) DO UPDATE SET reason=excluded.reason,payload_json=excluded.payload_json,delivered=0,updated_at=excluded.updated_at""",(campaign_id,vision_id,actor_kind,actor_id,power_id,kind,reason,self.e._dumps(payload or {}),wt,self.e._now()))
        return {"campaign_id":campaign_id,"id":vision_id,"actor_id":actor_id,"reason":reason,"delivered":False}
    def set_affliction(self,campaign_id,actor_kind,actor_id,affliction_id,kind,*,stage=0,max_stage=1,state=None):
        stage=max(0,min(int(stage),int(max_stage)))
        with self.e._write_db() as db:db.execute("""INSERT INTO afflictions(campaign_id,actor_kind,actor_id,id,kind,stage,max_stage,state_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(campaign_id,actor_kind,actor_id,id) DO UPDATE SET kind=excluded.kind,stage=excluded.stage,max_stage=excluded.max_stage,state_json=excluded.state_json,updated_at=excluded.updated_at""",(campaign_id,actor_kind,actor_id,affliction_id,kind,stage,int(max_stage),self.e._dumps(state or {}),self.e._now()))
        return {"campaign_id":campaign_id,"actor_kind":actor_kind,"actor_id":actor_id,"id":affliction_id,"kind":kind,"stage":stage,"max_stage":int(max_stage)}
    def save_homestead(self,campaign_id,homestead_id,owner_kind,owner_id,location_id,*,facilities=None,storage=None,state=None):
        with self.e._write_db() as db:db.execute("""INSERT INTO homesteads(campaign_id,id,owner_kind,owner_id,location_id,facilities_json,storage_json,state_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(campaign_id,id) DO UPDATE SET owner_kind=excluded.owner_kind,owner_id=excluded.owner_id,location_id=excluded.location_id,facilities_json=excluded.facilities_json,storage_json=excluded.storage_json,state_json=excluded.state_json,updated_at=excluded.updated_at""",(campaign_id,homestead_id,owner_kind,owner_id,location_id,self.e._dumps(facilities or {}),self.e._dumps(storage or {}),self.e._dumps(state or {}),self.e._now()))
        return {"campaign_id":campaign_id,"id":homestead_id,"location_id":location_id}
    def save_service(self,campaign_id,service_id,location_id,kind,name,*,operator_id=None,inventory=None,schedule=None,state=None):
        with self.e._write_db() as db:db.execute("""INSERT INTO town_services(campaign_id,id,location_id,kind,name,operator_id,inventory_json,schedule_json,state_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(campaign_id,id) DO UPDATE SET location_id=excluded.location_id,kind=excluded.kind,name=excluded.name,operator_id=excluded.operator_id,inventory_json=excluded.inventory_json,schedule_json=excluded.schedule_json,state_json=excluded.state_json,updated_at=excluded.updated_at""",(campaign_id,service_id,location_id,kind,name,operator_id,self.e._dumps(inventory or []),self.e._dumps(schedule or {}),self.e._dumps(state or {}),self.e._now()))
        return {"campaign_id":campaign_id,"id":service_id,"location_id":location_id,"kind":kind,"name":name}
    def set_climate(self,campaign_id,scope_type,scope_id,*,climate="temperate",season="summer",weather_weights=None,magic_theme=None,state=None):
        weather_weights = validate_weather_weights({} if weather_weights is None else weather_weights)
        with self.e._write_db() as db:db.execute("""INSERT INTO regional_climate(campaign_id,scope_type,scope_id,climate,season,weather_weights_json,magic_theme,state_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(campaign_id,scope_type,scope_id) DO UPDATE SET climate=excluded.climate,season=excluded.season,weather_weights_json=excluded.weather_weights_json,magic_theme=excluded.magic_theme,state_json=excluded.state_json,updated_at=excluded.updated_at""",(campaign_id,scope_type,scope_id,climate,season,self.e._dumps(weather_weights),magic_theme,self.e._dumps(state or {}),self.e._now()))
        return {"campaign_id":campaign_id,"scope_type":scope_type,"scope_id":scope_id,"climate":climate,"season":season,"magic_theme":magic_theme}
    def save_encounter_template(self,campaign_id,template_id,name,*,difficulty=1,participants=None,terrain=None,objectives=None,reinforcements=None,rewards=None,failure=None,world_events=None):
        with self.e._write_db() as db:db.execute("""INSERT INTO encounter_templates(campaign_id,id,name,difficulty,participants_json,terrain_json,objectives_json,reinforcements_json,rewards_json,failure_json,world_events_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(campaign_id,id) DO UPDATE SET name=excluded.name,difficulty=excluded.difficulty,participants_json=excluded.participants_json,terrain_json=excluded.terrain_json,objectives_json=excluded.objectives_json,reinforcements_json=excluded.reinforcements_json,rewards_json=excluded.rewards_json,failure_json=excluded.failure_json,world_events_json=excluded.world_events_json,updated_at=excluded.updated_at""",(campaign_id,template_id,name,float(difficulty),self.e._dumps(participants or []),self.e._dumps(terrain or {}),self.e._dumps(objectives or []),self.e._dumps(reinforcements or []),self.e._dumps(rewards or {}),self.e._dumps(failure or {}),self.e._dumps(world_events or []),self.e._now()))
        return {"campaign_id":campaign_id,"id":template_id,"name":name,"difficulty":float(difficulty)}
    def execute_recipe(self,campaign_id,recipe_id,owner_kind,owner_id,*,success=True):
        with self.e._write_db() as db:
            r=db.execute("SELECT * FROM recipes WHERE campaign_id=? AND id=?",(campaign_id,recipe_id)).fetchone();
            if not r:raise KeyError(f"unknown recipe: {recipe_id}")
            inputs=self.e._loads(r["inputs_json"])
            for iid,qty in inputs.items():
                have=db.execute("SELECT qty FROM inventories WHERE campaign_id=? AND owner_kind=? AND owner_id=? AND item_id=?",(campaign_id,owner_kind,owner_id,iid)).fetchone();
                if not have or float(have["qty"])<float(qty):raise ValueError(f"missing recipe input: {iid}")
            for iid,qty in inputs.items():db.execute("UPDATE inventories SET qty=qty-?,updated_at=? WHERE campaign_id=? AND owner_kind=? AND owner_id=? AND item_id=?",(float(qty),self.e._now(),campaign_id,owner_kind,owner_id,iid))
            if success and r["output_item_id"]:
                iid=r["output_item_id"];qty=float(r["output_qty"]);old=db.execute("SELECT qty FROM inventories WHERE campaign_id=? AND owner_kind=? AND owner_id=? AND item_id=?",(campaign_id,owner_kind,owner_id,iid)).fetchone();new=(float(old["qty"]) if old else 0)+qty
                db.execute("""INSERT INTO inventories(campaign_id,owner_kind,owner_id,item_id,qty,metadata_json,updated_at) VALUES(?,?,?,?,?,'{}',?) ON CONFLICT(campaign_id,owner_kind,owner_id,item_id) DO UPDATE SET qty=excluded.qty,updated_at=excluded.updated_at""",(campaign_id,owner_kind,owner_id,iid,new,self.e._now()))
            rev=self.e._next_revision(db,campaign_id);self.e._insert_event(db,campaign_id,rev,"production",f"Recipe {recipe_id} resolved",actor_id=owner_id,payload={"recipe_id":recipe_id,"success":bool(success),"inputs":inputs,"output_item_id":r["output_item_id"],"output_qty":float(r["output_qty"]) if success else 0})
        return {"campaign_id":campaign_id,"recipe_id":recipe_id,"success":bool(success),"revision":rev}
    def snapshot(self,campaign_id,*,map_id=None):
        with self.e._db() as db:
            maps=[dict(r) for r in db.execute("SELECT * FROM spatial_maps WHERE campaign_id=?"+(" AND id=?" if map_id else "")+" ORDER BY id",(campaign_id,map_id) if map_id else (campaign_id,))]
            tiles=[dict(r) for r in db.execute("SELECT * FROM spatial_tiles WHERE campaign_id=?"+(" AND map_id=?" if map_id else "")+" ORDER BY map_id,z,y,x",(campaign_id,map_id) if map_id else (campaign_id,))]
            zones=[dict(r) for r in db.execute("SELECT * FROM spatial_zones WHERE campaign_id=?"+(" AND map_id=?" if map_id else "")+" ORDER BY map_id,id",(campaign_id,map_id) if map_id else (campaign_id,))]
            portals=[dict(r) for r in db.execute("SELECT * FROM spatial_portals WHERE campaign_id=? ORDER BY id",(campaign_id,))]
        return {"campaign_id":campaign_id,"maps":maps,"tiles":tiles,"zones":zones,"portals":portals}
    def dispatch(self,operation,campaign_id,payload=None):
        d=dict(payload or {})
        table={"save_map":self.save_map,"get_map":self.get_map,"save_tile":self.save_tile,"save_zone":self.save_zone,"save_portal":self.save_portal,"find_path":self.find_path,"damage_tile":self.damage_tile,
               "save_discoverable":self.save_discoverable,"passive_scan":self.passive_scan,"set_progression":self.set_progression,"get_progression":self.get_progression,"award_xp":self.award_xp,"award_milestone":self.award_milestone,"save_reward":self.save_reward,"grant_reward":self.grant_reward,"save_quest_node":self.save_quest_node,"save_quest_edge":self.save_quest_edge,
               "save_faction_relation":self.save_faction_relation,"record_crime":self.record_crime,"save_rumor":self.save_rumor,"propagate_rumor":self.propagate_rumor,"set_population":self.set_population,"migrate":self.migrate,
               "set_divine_state":self.set_divine_state,"add_vision":self.add_vision,"set_affliction":self.set_affliction,"save_homestead":self.save_homestead,"save_service":self.save_service,"set_climate":self.set_climate,
               "save_encounter_template":self.save_encounter_template,"execute_recipe":self.execute_recipe,"snapshot":self.snapshot}
        if operation not in table:raise ValueError(f"unknown world systems operation: {operation}")
        return table[operation](campaign_id,**d)
