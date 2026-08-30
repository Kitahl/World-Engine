"""
DECIDE — utility-AI action selection (6th archetype).
Turns "the baker is at the bakery at 08:00" into "the baker has no flour, so today
he is at the market, and he is angry about it."

MODEL
  needs      : each agent holds need_key -> 0..100 (100 = desperate). Needs DRIFT upward.
  actions    : each advertises {need_key: satisfaction} and declares requirements.
  score(A)   = SUM over needs [ urgency(need) * satisfies * personality_weight ]
               * feasibility(A)            <- HARD GATE, 0 or 1
               * proximity(A)
  selection  = seeded weighted-random over the top K, NOT argmax.

WHY NOT ARGMAX: argmax makes every baker in the city identical and makes the whole
world snap between states. Softmax-over-top-K keeps variety while staying replayable
because the RNG is the ticker's seeded one.
"""
from __future__ import annotations
import json, math
from collections import OrderedDict

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_needs (
  campaign_id TEXT NOT NULL, agent_kind TEXT NOT NULL, agent_id TEXT NOT NULL,
  need_key TEXT NOT NULL,
  value REAL NOT NULL DEFAULT 0 CHECK(value >= 0 AND value <= 100),
  rise_per_day REAL NOT NULL DEFAULT 5,
  PRIMARY KEY(campaign_id, agent_kind, agent_id, need_key));

CREATE TABLE IF NOT EXISTS actions (
  campaign_id TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL,
  location_id  TEXT,                          -- NULL = performable anywhere
  requires_json  TEXT NOT NULL DEFAULT '{}',  -- {"item":{"flour":1}, "world_state":{"market_open":true}}
  satisfies_json TEXT NOT NULL DEFAULT '{}',  -- {"coin":30,"pride":20,"hunger":-0}  (amount of need REMOVED)
  cost_hours REAL NOT NULL DEFAULT 8,
  tags_json TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY(campaign_id, id));

CREATE TABLE IF NOT EXISTS agent_weights (    -- personality: how much this agent cares
  campaign_id TEXT NOT NULL, agent_kind TEXT NOT NULL, agent_id TEXT NOT NULL,
  need_key TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY(campaign_id, agent_kind, agent_id, need_key));
"""

# --- response curves: how urgent is a need at value v (0..100)? -> 0..1 -----------
CURVES = {
    "linear":      lambda v: v / 100.0,
    "quadratic":   lambda v: (v / 100.0) ** 2,          # ignorable until it isn't
    "urgent":      lambda v: (v / 100.0) ** 0.5,        # matters immediately
    "threshold":   lambda v: 0.0 if v < 60 else (v - 60) / 40.0,   # ignored, then panic
}
DEFAULT_CURVE = "quadratic"


class Decider:
    def __init__(self, engine, rng, curve: str = DEFAULT_CURVE, top_k: int = 3, temperature: float = 1.0):
        self.e, self.rng = engine, rng
        self.curve = CURVES[curve]
        self.top_k, self.temperature = top_k, temperature
        with self.e._db() as db:
            db.executescript(SCHEMA)

    # ---------- feasibility: the hard gate that makes "no flour" mean something ----------
    def _feasible(self, db, cid, agent_kind, agent_id, req: dict) -> bool:
        for item, qty in (req.get("item") or {}).items():
            row = db.execute(
                "SELECT qty FROM inventories WHERE campaign_id=? AND owner_kind=? AND owner_id=? AND item_id=?",
                (cid, agent_kind, agent_id, item)).fetchone()
            if not row or row["qty"] < qty:
                return False
        for key, want in (req.get("world_state") or {}).items():
            row = db.execute(
                "SELECT value_json FROM world_state WHERE campaign_id=? AND state_key=?",
                (cid, key)).fetchone()
            if not row or json.loads(row["value_json"]) != want:
                return False
        return True

    def _proximity(self, here: str | None, there: str | None) -> float:
        if there is None or here == there:
            return 1.0
        return 0.6                       # off-site actions are penalised, not forbidden

    # ---------- scoring ----------
    def score_actions(self, db, cid, agent_kind, agent_id, here) -> list[tuple[float, str, dict]]:
        needs = {r["need_key"]: r["value"] for r in db.execute(
            "SELECT need_key,value FROM agent_needs WHERE campaign_id=? AND agent_kind=? AND agent_id=?"
            " ORDER BY need_key", (cid, agent_kind, agent_id))}
        weights = {r["need_key"]: r["weight"] for r in db.execute(
            "SELECT need_key,weight FROM agent_weights WHERE campaign_id=? AND agent_kind=? AND agent_id=?",
            (cid, agent_kind, agent_id))}
        out = []
        for a in db.execute("SELECT * FROM actions WHERE campaign_id=? ORDER BY id", (cid,)):
            req = json.loads(a["requires_json"])
            if not self._feasible(db, cid, agent_kind, agent_id, req):
                continue                                        # HARD GATE
            sat = json.loads(a["satisfies_json"])
            raw = 0.0
            for need_key, amount in sorted(sat.items()):
                urgency = self.curve(needs.get(need_key, 0.0))
                raw += urgency * (amount / 100.0) * weights.get(need_key, 1.0)
            score = raw * self._proximity(here, a["location_id"])
            if score > 0:
                out.append((score, a["id"], dict(a)))
        out.sort(key=lambda t: (-t[0], t[1]))                   # deterministic tiebreak on id
        return out

    def choose(self, db, cid, agent_kind, agent_id, here):
        ranked = self.score_actions(db, cid, agent_kind, agent_id, here)
        if not ranked:
            return None
        pool = ranked[: self.top_k]
        wts = [math.exp(s / max(self.temperature, 1e-6)) for s, _, _ in pool]
        total = sum(wts)
        r, acc = self.rng.random() * total, 0.0
        for (s, aid, row), w in zip(pool, wts):
            acc += w
            if r <= acc:
                return (s, aid, row)
        return pool[-1]

    # ---------- the archetype handler, called once per agent per tick ----------
    def tick(self, db, cid, rev, log) -> int:
        acted = 0
        agents = db.execute("SELECT id,name,location FROM npcs WHERE campaign_id=? ORDER BY id", (cid,)).fetchall()
        for ag in agents:
            # needs rise
            db.execute("""UPDATE agent_needs SET value=MIN(100, value+rise_per_day)
                          WHERE campaign_id=? AND agent_kind='npc' AND agent_id=?""", (cid, ag["id"]))
            pick = self.choose(db, cid, "npc", ag["id"], ag["location"])
            if not pick:
                continue
            score, aid, row = pick
            # apply: move there, satisfy needs, consume requirements
            if row["location_id"] and row["location_id"] != ag["location"]:
                db.execute("UPDATE npcs SET location=? WHERE campaign_id=? AND id=?",
                           (row["location_id"], cid, ag["id"]))
            for need_key, amount in sorted(json.loads(row["satisfies_json"]).items()):
                db.execute("""UPDATE agent_needs SET value=MAX(0, value-?)
                              WHERE campaign_id=? AND agent_kind='npc' AND agent_id=? AND need_key=?""",
                           (amount, cid, ag["id"], need_key))
            for item, qty in (json.loads(row["requires_json"]).get("item") or {}).items():
                db.execute("""UPDATE inventories SET qty=qty-? WHERE campaign_id=? AND owner_kind='npc'
                              AND owner_id=? AND item_id=?""", (qty, cid, ag["id"], item))
            log(db, cid, rev, "sim_decide", f"{ag['name']} chose to {row['name']}",
                agent=ag["id"], action=aid, score=round(score, 4), at=row["location_id"])
            acted += 1
        return acted
