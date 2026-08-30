from __future__ import annotations

import heapq
import json
from typing import Any

NPC_LIFE_SCHEMA = r'''
CREATE TABLE IF NOT EXISTS npc_thoughts (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    npc_id TEXT NOT NULL,
    cause TEXT NOT NULL,
    mood_delta REAL NOT NULL DEFAULT 0,
    source_event_id INTEGER,
    tags_json TEXT NOT NULL DEFAULT '[]',
    created_world_time TEXT NOT NULL,
    expires_world_time TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,npc_id) REFERENCES npcs(campaign_id,id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_npc_thoughts_active ON npc_thoughts(campaign_id,npc_id,active,expires_world_time);

CREATE TABLE IF NOT EXISTS npc_archetype_profiles (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    needs_json TEXT NOT NULL DEFAULT '{}',
    actions_json TEXT NOT NULL DEFAULT '[]',
    planning_actions_json TEXT NOT NULL DEFAULT '[]',
    routine_json TEXT NOT NULL DEFAULT '{}',
    traits_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS npc_jobs (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    location_id TEXT,
    priority REAL NOT NULL DEFAULT 0,
    capacity INTEGER NOT NULL DEFAULT 1 CHECK(capacity >= 1),
    requirements_json TEXT NOT NULL DEFAULT '{}',
    effects_json TEXT NOT NULL DEFAULT '[]',
    source_event_id INTEGER,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','active','completed','cancelled')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_world_time TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_npc_jobs_open ON npc_jobs(campaign_id,status,priority DESC,id);

CREATE TABLE IF NOT EXISTS npc_job_reservations (
    campaign_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    npc_id TEXT NOT NULL,
    reserved_world_time TEXT NOT NULL,
    expires_world_time TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','released','completed','expired')),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,job_id,npc_id),
    FOREIGN KEY(campaign_id,job_id) REFERENCES npc_jobs(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id,npc_id) REFERENCES npcs(campaign_id,id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_job_reservations_active ON npc_job_reservations(campaign_id,job_id,status);
'''

CANONICAL_NEEDS: dict[str, dict[str, Any]] = {
    "hunger": {"baseline": 20, "drift_per_day": 0.18, "curve": "urgent"},
    "fatigue": {"baseline": 20, "drift_per_day": 0.14, "curve": "urgent"},
    "recreation": {"baseline": 25, "drift_per_day": 0.07, "curve": "quadratic"},
    "social": {"baseline": 25, "drift_per_day": 0.06, "curve": "quadratic"},
    "safety": {"baseline": 15, "drift_per_day": 0.02, "curve": "threshold"},
    "comfort": {"baseline": 20, "drift_per_day": 0.03, "curve": "quadratic"},
    "health": {"baseline": 10, "drift_per_day": 0, "curve": "urgent"},
    "wealth": {"baseline": 30, "drift_per_day": 0.03, "curve": "quadratic"},
    "purpose": {"baseline": 25, "drift_per_day": 0.03, "curve": "quadratic"},
    "belonging": {"baseline": 25, "drift_per_day": 0.04, "curve": "quadratic"},
}


class NpcLifeKernel:
    def __init__(self, engine: Any):
        self.e = engine

    def _world_time_db(self, db, campaign_id: str) -> str:
        row = db.execute("SELECT world_time FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if not row:
            raise KeyError(f"unknown campaign: {campaign_id}")
        return str(row["world_time"])

    def add_thought(self, campaign_id: str, npc_id: str, thought_id: str, cause: str, *, mood_delta: float = 0,
                    tags: list[str] | None = None, expires_world_time: str | None = None,
                    source_event_id: int | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        self.e.get_npc(campaign_id, npc_id)
        thought_id = self.e._clean_id(thought_id)
        with self.e._write_db() as db:
            wt = self._world_time_db(db, campaign_id)
            db.execute(
                """INSERT INTO npc_thoughts(campaign_id,id,npc_id,cause,mood_delta,source_event_id,tags_json,created_world_time,expires_world_time,active,metadata_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,1,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET cause=excluded.cause,mood_delta=excluded.mood_delta,source_event_id=excluded.source_event_id,tags_json=excluded.tags_json,expires_world_time=excluded.expires_world_time,active=1,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                (campaign_id, thought_id, npc_id, str(cause)[:500], float(mood_delta), source_event_id,
                 self.e._dumps(sorted(set(tags or []))), wt, expires_world_time, self.e._dumps(metadata or {}), self.e._now()),
            )
            rev = self.e._next_revision(db, campaign_id)
            self.e._insert_event(db, campaign_id, rev, "npc_thought", str(cause)[:500], actor_id=npc_id,
                                 payload={"thought_id": thought_id, "mood_delta": float(mood_delta), "tags": sorted(set(tags or []))})
        return self.get_thought(campaign_id, thought_id)

    def get_thought(self, campaign_id: str, thought_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            row = db.execute("SELECT * FROM npc_thoughts WHERE campaign_id=? AND id=?", (campaign_id, thought_id)).fetchone()
        if not row:
            raise KeyError(f"unknown thought: {thought_id}")
        d = dict(row); d["active"] = bool(d["active"]); d["tags"] = self.e._loads(d.pop("tags_json")); d["metadata"] = self.e._loads(d.pop("metadata_json")); return d

    def mood(self, campaign_id: str, npc_id: str) -> dict[str, Any]:
        self.e.get_npc(campaign_id, npc_id)
        with self.e._write_db() as db:
            wt = self._world_time_db(db, campaign_id)
            db.execute("UPDATE npc_thoughts SET active=0,updated_at=? WHERE campaign_id=? AND npc_id=? AND active=1 AND expires_world_time IS NOT NULL AND expires_world_time<=?",
                       (self.e._now(), campaign_id, npc_id, wt))
            rows = db.execute("SELECT * FROM npc_thoughts WHERE campaign_id=? AND npc_id=? AND active=1 ORDER BY created_world_time,id", (campaign_id, npc_id)).fetchall()
            total = sum(float(r["mood_delta"]) for r in rows)
        return {"campaign_id": campaign_id, "npc_id": npc_id, "mood": max(-100.0, min(100.0, total)),
                "thoughts": [{"id": r["id"], "cause": r["cause"], "mood_delta": float(r["mood_delta"])} for r in rows]}

    def _cognition_snapshot_db(self, db, campaign_id: str, npc_id: str) -> dict[str, Any]:
        npc_row=db.execute("SELECT * FROM npcs WHERE campaign_id=? AND id=?",(campaign_id,npc_id)).fetchone()
        if not npc_row: raise KeyError(f"unknown npc: {npc_id}")
        wt=self._world_time_db(db,campaign_id)
        db.execute("UPDATE npc_thoughts SET active=0,updated_at=? WHERE campaign_id=? AND npc_id=? AND active=1 AND expires_world_time IS NOT NULL AND expires_world_time<=?",(self.e._now(),campaign_id,npc_id,wt))
        thoughts=db.execute("SELECT * FROM npc_thoughts WHERE campaign_id=? AND npc_id=? AND active=1 ORDER BY created_world_time DESC,id DESC LIMIT 8",(campaign_id,npc_id)).fetchall()
        needs=db.execute("SELECT need,value,baseline,drift_per_day,curve FROM npc_needs WHERE campaign_id=? AND npc_id=? ORDER BY value DESC,need LIMIT 10",(campaign_id,npc_id)).fetchall()
        state=db.execute("SELECT * FROM sim_agent_state WHERE campaign_id=? AND npc_id=?",(campaign_id,npc_id)).fetchone()
        job=db.execute("""SELECT j.id,j.kind,j.title,j.priority,r.status FROM npc_job_reservations r JOIN npc_jobs j ON j.campaign_id=r.campaign_id AND j.id=r.job_id WHERE r.campaign_id=? AND r.npc_id=? AND r.status='active' ORDER BY j.priority DESC,j.id LIMIT 1""",(campaign_id,npc_id)).fetchone()
        beliefs=self.e._loads(npc_row["beliefs_json"]); goals=self.e._loads(npc_row["goals_json"]); memory=self.e._loads(npc_row["memory_json"])
        mood=max(-100.0,min(100.0,sum(float(r["mood_delta"]) for r in thoughts)))
        motives=[]
        for n in needs[:5]:
            urgency=max(0.0,min(100.0,float(n["value"])))
            motives.append({"source":"need","key":n["need"],"weight":round(urgency,3),"reason":f"{n['need']} pressure is {urgency:.0f}/100"})
        for i,g in enumerate(goals[:4]):
            motives.append({"source":"goal","key":str(g),"weight":round(85-i*5,3),"reason":f"active goal: {g}"})
        for i,b in enumerate(beliefs[:3]):
            motives.append({"source":"belief","key":str(b),"weight":round(45-i*3,3),"reason":f"belief influencing interpretation: {b}"})
        if job: motives.append({"source":"job","key":job["id"],"weight":70.0+min(20.0,float(job["priority"])),"reason":f"reserved duty: {job['title']}"})
        for t in thoughts[:3]:
            motives.append({"source":"thought","key":t["id"],"weight":min(80.0,30.0+abs(float(t["mood_delta"]))),"reason":t["cause"]})
        motives.sort(key=lambda x:(-float(x["weight"]),str(x["source"]),str(x["key"])))
        return {"npc_id":npc_id,"name":npc_row["name"],"importance":npc_row["importance"] if "importance" in npc_row.keys() else "minor","beliefs":beliefs[:8],"goals":goals[:8],"recent_memory":memory[-6:],"mood":mood,"thoughts":[{"id":r["id"],"cause":r["cause"],"mood_delta":float(r["mood_delta"]),"tags":self.e._loads(r["tags_json"])} for r in thoughts],"needs":[{"need":r["need"],"value":float(r["value"]),"curve":r["curve"]} for r in needs],"last_decision":{"action_id":state["last_action"],"score":float(state["last_score"]),"committed_until":state["committed_until"]} if state else None,"active_job":dict(job) if job else None,"dominant_motives":motives[:6]}

    def cognition_snapshot(self, campaign_id: str, npc_id: str) -> dict[str, Any]:
        with self.e._write_db() as db:
            data=self._cognition_snapshot_db(db,campaign_id,npc_id)
        return {"campaign_id":campaign_id,**data}

    def seed_needs(self, campaign_id: str, npc_id: str, *, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        self.e.get_npc(campaign_id, npc_id)
        spec = {k: dict(v) for k, v in CANONICAL_NEEDS.items()}
        for k, v in (overrides or {}).items():
            spec.setdefault(k, {}).update(v if isinstance(v, dict) else {"baseline": float(v)})
        for need, cfg in sorted(spec.items()):
            self.e.save_npc_need(campaign_id, npc_id, need, float(cfg.get("value", cfg.get("baseline", 25))),
                                 baseline=float(cfg.get("baseline", 25)), drift_per_day=float(cfg.get("drift_per_day", 0)), curve=str(cfg.get("curve", "quadratic")))
        return {"campaign_id": campaign_id, "npc_id": npc_id, "needs": sorted(spec)}

    def save_archetype(self, campaign_id: str, archetype_id: str, name: str, *, needs: dict[str, Any] | None = None,
                       actions: list[dict[str, Any]] | None = None, planning_actions: list[dict[str, Any]] | None = None,
                       routine: dict[str, Any] | None = None, traits: dict[str, Any] | None = None) -> dict[str, Any]:
        archetype_id = self.e._clean_id(archetype_id)
        with self.e._write_db() as db:
            db.execute("""INSERT INTO npc_archetype_profiles(campaign_id,id,name,needs_json,actions_json,planning_actions_json,routine_json,traits_json,updated_at)
                          VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(campaign_id,id) DO UPDATE SET name=excluded.name,needs_json=excluded.needs_json,actions_json=excluded.actions_json,planning_actions_json=excluded.planning_actions_json,routine_json=excluded.routine_json,traits_json=excluded.traits_json,updated_at=excluded.updated_at""",
                       (campaign_id, archetype_id, name, self.e._dumps(needs or {}), self.e._dumps(actions or []), self.e._dumps(planning_actions or []), self.e._dumps(routine or {}), self.e._dumps(traits or {}), self.e._now()))
        return self.get_archetype(campaign_id, archetype_id)

    def get_archetype(self, campaign_id: str, archetype_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            r = db.execute("SELECT * FROM npc_archetype_profiles WHERE campaign_id=? AND id=?", (campaign_id, archetype_id)).fetchone()
        if not r: raise KeyError(f"unknown archetype: {archetype_id}")
        d = dict(r)
        for k in ("needs", "actions", "planning_actions", "routine", "traits"): d[k] = self.e._loads(d.pop(k+"_json"))
        return d

    def apply_archetype(self, campaign_id: str, npc_id: str, archetype_id: str, *, deviations: dict[str, Any] | None = None) -> dict[str, Any]:
        a = self.get_archetype(campaign_id, archetype_id)
        self.e.get_npc(campaign_id, npc_id)
        self.seed_needs(campaign_id, npc_id, overrides={**a["needs"], **((deviations or {}).get("needs") or {})})
        for action in a["actions"]:
            aid = str(action["id"])
            self.e.save_npc_action(campaign_id, npc_id, aid, location=action.get("location"), base_utility=float(action.get("base_utility", 0)),
                                   considerations=action.get("considerations") or [], effects=action.get("effects") or [], requirements=action.get("requirements") or {},
                                   cost_hours=float(action.get("cost_hours", 8)), tags=action.get("tags") or [], enabled=bool(action.get("enabled", True)))
        with self.e._write_db() as db:
            db.execute("UPDATE npcs SET archetype_id=?,routine_json=?,updated_at=? WHERE campaign_id=? AND id=?",
                       (archetype_id, self.e._dumps({**a["routine"], **((deviations or {}).get("routine") or {})}), self.e._now(), campaign_id, npc_id))
        return {"campaign_id": campaign_id, "npc_id": npc_id, "archetype_id": archetype_id, "actions_installed": len(a["actions"]), "needs_installed": len(a["needs"])}

    def create_job(self, campaign_id: str, job_id: str, kind: str, title: str, *, location_id: str | None = None,
                   priority: float = 0, capacity: int = 1, requirements: dict[str, Any] | None = None,
                   effects: list[dict[str, Any]] | None = None, source_event_id: int | None = None,
                   metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        job_id = self.e._clean_id(job_id); capacity = int(capacity)
        if capacity < 1: raise ValueError("capacity must be >=1")
        with self.e._write_db() as db:
            wt = self._world_time_db(db, campaign_id)
            db.execute("""INSERT INTO npc_jobs(campaign_id,id,kind,title,location_id,priority,capacity,requirements_json,effects_json,source_event_id,status,metadata_json,created_world_time,updated_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?,'open',?,?,?) ON CONFLICT(campaign_id,id) DO UPDATE SET kind=excluded.kind,title=excluded.title,location_id=excluded.location_id,priority=excluded.priority,capacity=excluded.capacity,requirements_json=excluded.requirements_json,effects_json=excluded.effects_json,source_event_id=excluded.source_event_id,status='open',metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                       (campaign_id, job_id, str(kind), str(title), location_id, float(priority), capacity, self.e._dumps(requirements or {}), self.e._dumps(effects or []), source_event_id, self.e._dumps(metadata or {}), wt, self.e._now()))
        return self.get_job(campaign_id, job_id)

    def get_job(self, campaign_id: str, job_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            r = db.execute("SELECT * FROM npc_jobs WHERE campaign_id=? AND id=?", (campaign_id, job_id)).fetchone()
            if not r: raise KeyError(f"unknown job: {job_id}")
            active = db.execute("SELECT COUNT(*) n FROM npc_job_reservations WHERE campaign_id=? AND job_id=? AND status='active'", (campaign_id, job_id)).fetchone()["n"]
        d=dict(r); d["requirements"]=self.e._loads(d.pop("requirements_json")); d["effects"]=self.e._loads(d.pop("effects_json")); d["metadata"]=self.e._loads(d.pop("metadata_json")); d["active_reservations"]=int(active); d["slots_available"]=max(0,int(d["capacity"])-int(active)); return d

    def reserve_job(self, campaign_id: str, job_id: str, npc_id: str, *, expires_world_time: str | None = None) -> dict[str, Any]:
        self.e.get_npc(campaign_id, npc_id)
        with self.e._write_db() as db:
            wt = self._world_time_db(db, campaign_id)
            db.execute("UPDATE npc_job_reservations SET status='expired',updated_at=? WHERE campaign_id=? AND status='active' AND expires_world_time IS NOT NULL AND expires_world_time<=?", (self.e._now(), campaign_id, wt))
            job = db.execute("SELECT * FROM npc_jobs WHERE campaign_id=? AND id=?", (campaign_id, job_id)).fetchone()
            if not job or job["status"] not in ("open","active"): raise ValueError("job is not reservable")
            count = db.execute("SELECT COUNT(*) n FROM npc_job_reservations WHERE campaign_id=? AND job_id=? AND status='active'", (campaign_id, job_id)).fetchone()["n"]
            existing = db.execute("SELECT status FROM npc_job_reservations WHERE campaign_id=? AND job_id=? AND npc_id=?", (campaign_id, job_id, npc_id)).fetchone()
            if not (existing and existing["status"] == "active") and int(count) >= int(job["capacity"]): raise ValueError("job capacity exhausted")
            db.execute("""INSERT INTO npc_job_reservations(campaign_id,job_id,npc_id,reserved_world_time,expires_world_time,status,updated_at) VALUES(?,?,?,?,?,'active',?)
                          ON CONFLICT(campaign_id,job_id,npc_id) DO UPDATE SET reserved_world_time=excluded.reserved_world_time,expires_world_time=excluded.expires_world_time,status='active',updated_at=excluded.updated_at""",
                       (campaign_id, job_id, npc_id, wt, expires_world_time, self.e._now()))
            db.execute("UPDATE npc_jobs SET status='active',updated_at=? WHERE campaign_id=? AND id=?", (self.e._now(), campaign_id, job_id))
        return {"campaign_id":campaign_id,"job_id":job_id,"npc_id":npc_id,"status":"active"}

    def release_job(self, campaign_id: str, job_id: str, npc_id: str, *, completed: bool=False) -> dict[str, Any]:
        status = "completed" if completed else "released"
        with self.e._write_db() as db:
            db.execute("UPDATE npc_job_reservations SET status=?,updated_at=? WHERE campaign_id=? AND job_id=? AND npc_id=?", (status,self.e._now(),campaign_id,job_id,npc_id))
            active = db.execute("SELECT COUNT(*) n FROM npc_job_reservations WHERE campaign_id=? AND job_id=? AND status='active'",(campaign_id,job_id)).fetchone()["n"]
            if completed:
                db.execute("UPDATE npc_jobs SET status='completed',updated_at=? WHERE campaign_id=? AND id=?",(self.e._now(),campaign_id,job_id))
            elif not active:
                db.execute("UPDATE npc_jobs SET status='open',updated_at=? WHERE campaign_id=? AND id=? AND status!='completed'",(self.e._now(),campaign_id,job_id))
        return {"campaign_id":campaign_id,"job_id":job_id,"npc_id":npc_id,"status":status}

    @staticmethod
    def _preconditions_met(state: dict[str, Any], req: dict[str, Any]) -> bool:
        for key, want in req.items():
            have = state.get(key, 0)
            if isinstance(want, dict):
                if "ge" in want and not (float(have) >= float(want["ge"])): return False
                if "gt" in want and not (float(have) > float(want["gt"])): return False
                if "le" in want and not (float(have) <= float(want["le"])): return False
                if "lt" in want and not (float(have) < float(want["lt"])): return False
                if "eq" in want and have != want["eq"]: return False
            elif have != want: return False
        return True

    @staticmethod
    def _apply_effects(state: dict[str, Any], effects: dict[str, Any]) -> dict[str, Any]:
        nxt = dict(state)
        for key, effect in effects.items():
            if isinstance(effect, dict):
                if "set" in effect: nxt[key] = effect["set"]
                elif "delta" in effect: nxt[key] = float(nxt.get(key,0)) + float(effect["delta"])
            else: nxt[key] = effect
        return nxt

    @staticmethod
    def _goal_met(state: dict[str, Any], goal: dict[str, Any]) -> bool:
        return NpcLifeKernel._preconditions_met(state, goal)

    @staticmethod
    def plan(start: dict[str, Any], goal: dict[str, Any], actions: list[dict[str, Any]], *, max_depth: int=5, max_expanded: int=256) -> dict[str, Any]:
        max_depth=max(1,min(int(max_depth),12)); max_expanded=max(1,min(int(max_expanded),4096))
        def freeze(s): return tuple(sorted((str(k), json.dumps(v,sort_keys=True,separators=(",",":"))) for k,v in s.items()))
        q=[(0.0,0,(),freeze(start),dict(start))]; best={freeze(start):0.0}; expanded=0
        ordered=sorted(actions,key=lambda a:str(a.get("id","")))
        while q and expanded<max_expanded:
            cost,depth,path,key,state=heapq.heappop(q); expanded+=1
            if NpcLifeKernel._goal_met(state,goal): return {"found":True,"plan":list(path),"cost":cost,"expanded":expanded,"depth":depth}
            if depth>=max_depth: continue
            for a in ordered:
                if not NpcLifeKernel._preconditions_met(state,a.get("preconditions") or {}): continue
                nxt=NpcLifeKernel._apply_effects(state,a.get("effects") or {}); nk=freeze(nxt); nc=cost+float(a.get("cost",1))
                if nc>=best.get(nk,float("inf")): continue
                best[nk]=nc; heapq.heappush(q,(nc,depth+1,path+(str(a.get("id","")),),nk,nxt))
        return {"found":False,"plan":[],"cost":None,"expanded":expanded,"depth":None,"reason":"budget_exhausted_or_unreachable"}

    def dispatch(self, operation: str, campaign_id: str, payload: dict[str, Any] | None=None) -> Any:
        d=dict(payload or {})
        if operation=="add_thought": return self.add_thought(campaign_id,**d)
        if operation=="mood": return self.mood(campaign_id,**d)
        if operation=="cognition_snapshot": return self.cognition_snapshot(campaign_id,**d)
        if operation=="seed_needs": return self.seed_needs(campaign_id,**d)
        if operation=="save_archetype": return self.save_archetype(campaign_id,**d)
        if operation=="apply_archetype": return self.apply_archetype(campaign_id,**d)
        if operation=="create_job": return self.create_job(campaign_id,**d)
        if operation=="get_job": return self.get_job(campaign_id,**d)
        if operation=="reserve_job": return self.reserve_job(campaign_id,**d)
        if operation=="release_job": return self.release_job(campaign_id,**d)
        if operation=="plan": return self.plan(**d)
        raise ValueError(f"unknown npc life operation: {operation}")
