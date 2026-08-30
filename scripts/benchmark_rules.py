"""Small deterministic rules-kernel benchmark.

Measurements are environment-specific and are not performance guarantees.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from world_engine import WorldEngine


def run(iterations: int=500) -> dict:
    with tempfile.TemporaryDirectory() as td:
        e=WorldEngine(Path(td)/"rules_benchmark.sqlite3")
        e.ensure_campaign("bench","Rules Benchmark","1492-01-01T08:00:00+00:00")
        e.set_simulation_seed("bench",424242)
        e.upsert_location("bench","yard","Yard")
        e.upsert_character("bench","hero","Hero",level=5,hp=100,max_hp=100,ac=14,location="yard",abilities={"int":4},proficiency_bonus=3)
        e.upsert_npc("bench","target","Target",hp=iterations+100,max_hp=iterations+100,ac=10,location="yard",stats={"dex_mod":1})
        e.rules_dispatch("configure","bench",{"rules_version":"2024"})
        e.rules_dispatch("set_actor_profile","bench",{"actor_kind":"character","actor_id":"hero","spellcasting_ability":"int"})
        e.rules_dispatch("define_activity","bench",{
            "activity_id":"benchmark_save","name":"Benchmark Save","activity_type":"save","activation":"none",
            "save":{"ability":"dex","dc":100,"on_success":"half"},
            "damage":[{"formula":"1","type":"force"}],"targeting":{"mode":"single"},
        })
        started=time.perf_counter()
        for _ in range(iterations):
            e.rules_dispatch("resolve_activity","bench",{
                "activity_id":"benchmark_save","actor_kind":"character","actor_id":"hero",
                "targets":[{"kind":"npc","id":"target"}],
            })
        elapsed=time.perf_counter()-started
        with e._db() as db:
            integrity=db.execute("PRAGMA integrity_check").fetchone()[0]
            events=db.execute("SELECT COUNT(*) n FROM events WHERE campaign_id='bench' AND event_type='rule_activity'").fetchone()["n"]
        return {
            "iterations":iterations,
            "elapsed_seconds":round(elapsed,3),
            "resolutions_per_second":round(iterations/elapsed,2) if elapsed else None,
            "activity_events":int(events),
            "target_hp":e.get_npc("bench","target")["hp"],
            "sqlite_integrity":integrity,
        }


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--iterations",type=int,default=500)
    args=parser.parse_args()
    if args.iterations<1:
        raise SystemExit("--iterations must be >=1")
    print(json.dumps(run(args.iterations),indent=2))
