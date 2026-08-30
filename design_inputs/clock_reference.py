"""
Time policy + lazy catch-up + event rollup for World Engine v3.1.

THE CORE IDEA: the world has an ANCHOR, not a heartbeat.

  anchor = (real_timestamp, game_time)   stored once, on the campaign row
  ratio  = game minutes per real minute  (0 = paused, 1440 = 1 real hr -> 1 game day)

Nothing runs in the background. On ANY read, you compute how much game time
should have elapsed since the anchor and simulate the gap before answering.
That is "offline progress", and it is the same shape Dwarf Fortress uses when
it advances world activity in fixed chunks rather than continuously.

Why not a background daemon: a laptop sleeps, closes, crashes. Any real-time
loop needs this catch-up logic ANYWAY to survive the gap. So build this first;
a daemon is then an optional 15-line wrapper that just calls advance() on a timer.
"""
from __future__ import annotations
import json
from collections import Counter
from datetime import datetime, timezone, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS time_policy (
  campaign_id     TEXT PRIMARY KEY,
  mode            TEXT NOT NULL DEFAULT 'paused'
                  CHECK(mode IN ('paused','skip_only','anchored')),
  ratio           REAL NOT NULL DEFAULT 0,     -- game minutes per real minute
  anchor_real     TEXT,                        -- real UTC timestamp of last sync
  anchor_game     TEXT,                        -- game time at that moment
  max_catchup_days INTEGER NOT NULL DEFAULT 90);

CREATE TABLE IF NOT EXISTS lod_tiers (
  campaign_id TEXT NOT NULL, region_id TEXT NOT NULL,
  tier TEXT NOT NULL CHECK(tier IN ('near','mid','far')),
  cadence_hours INTEGER NOT NULL,
  PRIMARY KEY(campaign_id, region_id));
"""

class Clock:
    MODES = {
        "paused":    0.0,      # time moves only inside a scene
        "skip_only": 0.0,      # time moves only when explicitly skipped
        "anchored":  None,     # time tracks wall-clock at `ratio`
    }

    def __init__(self, engine, ticker):
        self.e, self.tk = engine, ticker
        with self.e._db() as db:
            db.executescript(SCHEMA)

    def set_policy(self, cid, mode, ratio=0.0, max_catchup_days=90):
        camp = self.e.get_campaign(cid)
        with self.e._db() as db:
            db.execute("""INSERT INTO time_policy VALUES(?,?,?,?,?,?)
                          ON CONFLICT(campaign_id) DO UPDATE SET
                          mode=excluded.mode, ratio=excluded.ratio,
                          anchor_real=excluded.anchor_real, anchor_game=excluded.anchor_game,
                          max_catchup_days=excluded.max_catchup_days""",
                       (cid, mode, ratio, datetime.now(timezone.utc).isoformat(),
                        camp["world_time"], max_catchup_days))

    def pending_days(self, cid, now_real=None) -> float:
        with self.e._db() as db:
            p = db.execute("SELECT * FROM time_policy WHERE campaign_id=?", (cid,)).fetchone()
        if not p or p["mode"] != "anchored" or p["ratio"] <= 0:
            return 0.0
        now = now_real or datetime.now(timezone.utc)
        real_elapsed_min = (now - datetime.fromisoformat(p["anchor_real"])).total_seconds() / 60
        game_min = real_elapsed_min * p["ratio"]
        return min(game_min / 1440.0, float(p["max_catchup_days"]))

    def ensure_current(self, cid, now_real=None) -> dict:
        """Call at the top of every read. Simulates the gap, then returns a digest of it."""
        days = int(self.pending_days(cid, now_real))
        if days < 1:
            return {"caught_up_days": 0, "digest": None}
        before_rev = self.e.get_campaign(cid)["revision"]
        tally = self.tk.advance(cid, days=days)
        with self.e._db() as db:
            db.execute("UPDATE time_policy SET anchor_real=?, anchor_game=? WHERE campaign_id=?",
                       ((now_real or datetime.now(timezone.utc)).isoformat(),
                        self.e.get_campaign(cid)["world_time"], cid))
        return {"caught_up_days": days, "tally": tally,
                "digest": self.digest(cid, since_revision=before_rev)}

    # ---------- event rollup: the thing that keeps a time skip readable ----------
    def digest(self, cid, since_revision=0, verbatim_limit=12) -> dict:
        """Raw lines for notable events; counts for everything else.
        8,760 hourly events become ~15 lines the model can actually read."""
        with self.e._db() as db:
            rows = db.execute(
                "SELECT event_type,summary,payload_json FROM events "
                "WHERE campaign_id=? AND revision>? ORDER BY id", (cid, since_revision)).fetchall()
        NOTABLE = {"crime", "disaster", "death", "birth", "war", "sim_spread", "faction_shift"}
        notable = [r for r in rows if r["event_type"] in NOTABLE]
        counts  = Counter(r["event_type"] for r in rows)
        return {
            "total_events": len(rows),
            "headlines": [f"{r['event_type']}: {r['summary']}" for r in notable[:verbatim_limit]],
            "rolled_up": {k: v for k, v in counts.items() if k not in NOTABLE},
            "truncated": max(0, len(notable) - verbatim_limit),
        }
