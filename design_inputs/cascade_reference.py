"""
CASCADE — consequences of consequences (7th archetype).

DF's depth comes from output feeding input WITHIN one tick:
  a friend dies -> grief -> tantrum -> a door is smashed -> the cistern floods -> more deaths.

Handlers stop applying effects directly. They EMIT into a queue; a drain loop
processes it, and processing may emit more.

FOUR CORRECTNESS PROPERTIES, all load-bearing:
  1. BFS, not DFS. Same-generation consequences resolve together, so the ledger reads
     "the death caused three people to grieve" instead of one deep unreadable thread.
  2. Deterministic ordering. Reactions sorted by rule id; queue is strict FIFO.
  3. TWO caps, not one. max_depth stops recursion; max_effects stops breadth explosion.
     A death with 50 friends is already 50 effects at depth 1 — depth alone will not save you.
  4. Loop guard. Track (event_type, target) already fired this cascade and refuse repeats,
     or A grieves B grieves A forever.
"""
from __future__ import annotations
import json
from collections import deque

SCHEMA = """
CREATE TABLE IF NOT EXISTS reactions (
  campaign_id TEXT NOT NULL, id TEXT NOT NULL,
  trigger_event TEXT NOT NULL,                 -- 'death', 'grief', 'theft'...
  selector_json TEXT NOT NULL DEFAULT '{}',    -- who reacts: {"related_to_target":{"trust_gte":40}}
  emit_event    TEXT NOT NULL,
  emit_json     TEXT NOT NULL DEFAULT '{}',    -- {"summary":"{who} grieves for {target}"}
  effect_json   TEXT NOT NULL DEFAULT '{}',    -- {"need":{"grief":+40}, "relationship":{"trust":-10}}
  probability REAL NOT NULL DEFAULT 1.0,
  enabled INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(campaign_id, id));
"""

class Cascade:
    def __init__(self, engine, rng, max_depth: int = 4, max_effects: int = 200):
        self.e, self.rng = engine, rng
        self.max_depth, self.max_effects = max_depth, max_effects
        with self.e._db() as db:
            db.executescript(SCHEMA)

    # ---- who reacts to this effect? deterministic order, always ----
    def _selected(self, db, cid, sel: dict, effect: dict) -> list[str]:
        target = effect.get("target")
        if "related_to_target" in sel and target:
            gte = sel["related_to_target"].get("trust_gte", 0)
            rows = db.execute(
                """SELECT source_id AS who FROM relationships
                   WHERE campaign_id=? AND target_id=? AND trust>=?
                   UNION
                   SELECT target_id AS who FROM relationships
                   WHERE campaign_id=? AND source_id=? AND trust>=?
                   ORDER BY who""", (cid, target, gte, cid, target, gte)).fetchall()
            return [r["who"] for r in rows if r["who"] != target]
        if "same_location" in sel:
            rows = db.execute("SELECT id AS who FROM npcs WHERE campaign_id=? AND location=? ORDER BY id",
                              (cid, effect.get("location"))).fetchall()
            return [r["who"] for r in rows]
        return [sel["who"]] if "who" in sel else []

    def _apply(self, db, cid, rev, effect: dict, log):
        """Mutate state for one effect, then log it. Keep this the ONLY mutation point."""
        eff = effect.get("effect") or {}
        who = effect.get("who")
        for need_key, delta in sorted((eff.get("need") or {}).items()):
            db.execute("""INSERT INTO agent_needs(campaign_id,agent_kind,agent_id,need_key,value)
                          VALUES(?,'npc',?,?,MAX(0,MIN(100,?)))
                          ON CONFLICT DO UPDATE SET value=MAX(0,MIN(100,value+?))""",
                       (cid, who, need_key, max(0, delta), delta))
        for field, delta in sorted((eff.get("relationship") or {}).items()):
            if who and effect.get("target"):
                db.execute(f"""UPDATE relationships SET {field}=MAX(-100,MIN(100,{field}+?))
                               WHERE campaign_id=? AND source_id=? AND target_id=?""",
                           (delta, cid, who, effect["target"]))
        log(db, cid, rev, effect["event"], effect.get("summary", effect["event"]),
            who=who, target=effect.get("target"), depth=effect.get("depth", 0))

    def run(self, db, cid, rev, seeds: list[dict], log) -> dict:
        queue = deque((dict(s, depth=0) for s in seeds))
        seen: set[tuple] = set()
        applied, suppressed_loop, truncated_depth = 0, 0, 0

        while queue and applied < self.max_effects:
            effect = queue.popleft()
            key = (effect["event"], effect.get("who"), effect.get("target"))
            if key in seen:
                suppressed_loop += 1
                continue
            seen.add(key)
            self._apply(db, cid, rev, effect, log)
            applied += 1

            if effect["depth"] >= self.max_depth:
                truncated_depth += 1
                continue
            rules = db.execute(
                "SELECT * FROM reactions WHERE campaign_id=? AND enabled=1 AND trigger_event=? ORDER BY id",
                (cid, effect["event"])).fetchall()
            for r in rules:                                          # sorted -> deterministic
                for who in self._selected(db, cid, json.loads(r["selector_json"]), effect):
                    if self.rng.random() > r["probability"]:
                        continue
                    tmpl = json.loads(r["emit_json"])
                    summary = tmpl.get("summary", r["emit_event"]).format(
                        who=who, target=effect.get("target", "?"))
                    queue.append({"event": r["emit_event"], "who": who,
                                  "target": effect.get("target"), "location": effect.get("location"),
                                  "summary": summary, "effect": json.loads(r["effect_json"]),
                                  "depth": effect["depth"] + 1})

        stats = {"applied": applied, "loops_suppressed": suppressed_loop,
                 "depth_truncated": truncated_depth, "budget_dropped": len(queue)}
        if queue:
            log(db, cid, rev, "cascade_truncated",
                f"cascade hit the {self.max_effects}-effect budget", **stats)
        return stats
