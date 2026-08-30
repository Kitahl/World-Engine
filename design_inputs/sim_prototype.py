"""
Generic tick engine for World Engine v3.1.

Thesis: almost everything a world simulates is one of FIVE update archetypes.
You do not need 20 bespoke handlers -- you need 5 generic ones driven by a rules table.

  1. DRIFT      x -> x + k*(baseline - x)      relationships cool, reputation fades, heat dies down
  2. SCHEDULE   entity.field = table[hour]     NPC routines, shop hours, guard patrols
  3. STOCK      x -> clamp(x + rate*dt)        herb regrowth, food stores, faction reserves, coin
  4. CHANCE     if rand() < p: fire(event)     disasters, crime, births, deaths, caravans
  5. SPREAD     graph BFS one hop per tick     rumours, disease, faction tension, panic

Every handler reads/writes ONLY the tables v3.1 already has, and writes to the
existing `events` ledger, so the model reads "what happened while you were away"
through recent_events -- a mechanism that already exists and already works.
"""
from __future__ import annotations
import json, random, sqlite3
from datetime import datetime, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS sim_rules (
  campaign_id TEXT NOT NULL,
  id          TEXT NOT NULL,
  archetype   TEXT NOT NULL CHECK(archetype IN ('drift','schedule','stock','chance','spread')),
  enabled     INTEGER NOT NULL DEFAULT 1,
  cadence     TEXT NOT NULL DEFAULT 'day' CHECK(cadence IN ('hour','day','week')),
  target      TEXT NOT NULL,            -- table.column, or a scope key
  params_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(campaign_id, id));

CREATE TABLE IF NOT EXISTS resource_nodes (
  campaign_id TEXT NOT NULL, id TEXT NOT NULL, location_id TEXT NOT NULL,
  item_id TEXT NOT NULL, qty REAL NOT NULL DEFAULT 0, qty_max REAL NOT NULL DEFAULT 10,
  regen_per_day REAL NOT NULL DEFAULT 0.5, season_mult_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(campaign_id, id));
"""

class Ticker:
    def __init__(self, engine, seed: int = 1337):
        self.e = engine
        self.rng = random.Random(seed)          # SEEDED -> the whole sim is replayable
        with self.e._db() as db:
            db.executescript(SCHEMA)

    # ---------- helpers ----------
    def _rules(self, db, cid, cadence):
        return db.execute(
            "SELECT * FROM sim_rules WHERE campaign_id=? AND enabled=1 AND cadence=? ORDER BY id",
            (cid, cadence)).fetchall()

    def _log(self, db, cid, rev, etype, summary, **payload):
        self.e._insert_event(db, cid, rev, etype, summary, payload=payload)

    # ---------- the five archetypes ----------
    def _drift(self, db, cid, rev, p):
        """x -> x + k*(baseline - x). Works on ANY numeric column."""
        tbl, col = p["target"].split(".")
        k, base = float(p.get("k", 0.1)), float(p.get("baseline", 0))
        rows = db.execute(f"SELECT rowid,{col} FROM {tbl} WHERE campaign_id=?", (cid,)).fetchall()
        moved = 0
        for r in rows:
            old = float(r[col]); new = round(old + k * (base - old))
            if new != old:
                db.execute(f"UPDATE {tbl} SET {col}=? WHERE rowid=?", (new, r["rowid"])); moved += 1
        if moved:
            self._log(db, cid, rev, "sim_drift", f"{moved} {tbl}.{col} values drifted toward {base}",
                      rule=p["target"], affected=moved)
        return moved

    def _schedule(self, db, cid, rev, p, hour):
        """entity.location = routine[hour]. Finally READS npcs.routine_json."""
        rows = db.execute("SELECT id,name,location,routine_json FROM npcs WHERE campaign_id=?", (cid,)).fetchall()
        moved = 0
        for r in rows:
            routine = json.loads(r["routine_json"] or "{}")
            slot = max([h for h in routine if int(h.split(":")[0]) <= hour] or [None],
                       key=lambda h: int(h.split(":")[0]) if h else -1)
            if not slot: continue
            dest = routine[slot]
            if dest != r["location"]:
                db.execute("UPDATE npcs SET location=? WHERE campaign_id=? AND id=?", (dest, cid, r["id"]))
                moved += 1
        if moved:
            self._log(db, cid, rev, "sim_routine", f"{moved} NPCs moved to their {hour:02d}:00 posting",
                      hour=hour, affected=moved)
        return moved

    def _stock(self, db, cid, rev, p, days):
        """x -> clamp(x + rate*dt). Herbs, food, coin, faction reserves."""
        season = p.get("season", "summer")
        rows = db.execute("SELECT * FROM resource_nodes WHERE campaign_id=?", (cid,)).fetchall()
        grown = 0
        for r in rows:
            mult = json.loads(r["season_mult_json"] or "{}").get(season, 1.0)
            new = min(r["qty_max"], r["qty"] + r["regen_per_day"] * mult * days)
            if new != r["qty"]:
                db.execute("UPDATE resource_nodes SET qty=? WHERE campaign_id=? AND id=?", (new, cid, r["id"]))
                grown += 1
        if grown:
            self._log(db, cid, rev, "sim_growth", f"{grown} resource nodes regrew ({season})",
                      season=season, affected=grown)
        return grown

    def _chance(self, db, cid, rev, p, days):
        """Flat per-day probability. Disasters, crime, caravans, births."""
        p_day, fired = float(p.get("p_day", 0.02)), 0
        for _ in range(days):
            if self.rng.random() < p_day:
                self._log(db, cid, rev, p.get("event_type", "sim_event"),
                          p.get("summary", "something happened"), rule=p.get("id"))
                fired += 1
        return fired

    def _spread(self, db, cid, rev, p):
        """One BFS hop per tick across the relationship graph. Rumours, panic, disease."""
        key = p["state_key"]
        seeded = {r["scope_id"] for r in db.execute(
            "SELECT scope_id FROM world_state WHERE campaign_id=? AND scope_type='npc' AND state_key=?",
            (cid, key)).fetchall()}
        if not seeded: return 0
        edges = db.execute(
            "SELECT source_id,target_id,trust FROM relationships WHERE campaign_id=?", (cid,)).fetchall()
        new = set()
        for ed in edges:
            for a, b in ((ed["source_id"], ed["target_id"]), (ed["target_id"], ed["source_id"])):
                if a in seeded and b not in seeded and self.rng.random() < float(p.get("p_hop", 0.5)):
                    new.add(b)
        now = self.e._now()
        for n in new:
            db.execute("""INSERT INTO world_state(campaign_id,scope_type,scope_id,state_key,value_json,updated_at)
                          VALUES(?,'npc',?,?,?,?) ON CONFLICT DO UPDATE SET value_json=excluded.value_json""",
                       (cid, n, key, json.dumps(True), now))
        if new:
            self._log(db, cid, rev, "sim_spread", f"'{key}' spread to {len(new)} more people",
                      state_key=key, affected=len(new), reached=sorted(new))
        return len(new)

    # ---------- the loop ----------
    def advance(self, cid: str, days: int, season: str = "summer") -> dict:
        """Replaces advance_world for spans > 0. Budget-capped."""
        days = min(days, 365)
        tally = {"drift": 0, "schedule": 0, "stock": 0, "chance": 0, "spread": 0}
        camp = self.e.get_campaign(cid)
        t = datetime.fromisoformat(camp["world_time"])
        with self.e._db() as db:
            db.execute("BEGIN IMMEDIATE")
            rev = self.e._next_revision(db, cid)
            for _ in range(days):
                t += timedelta(days=1)
                for r in self._rules(db, cid, "day"):
                    p = json.loads(r["params_json"]); p["id"] = r["id"]; p["target"] = r["target"]
                    p["season"] = season
                    a = r["archetype"]
                    if   a == "drift":    tally["drift"]    += self._drift(db, cid, rev, p)
                    elif a == "schedule": tally["schedule"] += self._schedule(db, cid, rev, p, t.hour)
                    elif a == "stock":    tally["stock"]    += self._stock(db, cid, rev, p, 1)
                    elif a == "chance":   tally["chance"]   += self._chance(db, cid, rev, p, 1)
                    elif a == "spread":   tally["spread"]   += self._spread(db, cid, rev, p)
            db.execute("UPDATE campaigns SET world_time=?,updated_at=? WHERE id=?",
                       (t.isoformat(), self.e._now(), cid))
            self._log(db, cid, rev, "world_advance", f"{days} days passed", days=days, tally=tally)
        return tally
