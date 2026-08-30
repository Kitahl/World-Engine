from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
import sys
import argparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from world_engine import WorldEngine


def run_benchmark(agents: int = 200, days: int = 365) -> dict:
    with tempfile.TemporaryDirectory() as td:
        e = WorldEngine(Path(td) / "benchmark.sqlite3")
        e.ensure_campaign("bench", "World Activity Benchmark")
        e.set_simulation_seed("bench", 424242)
        e.save_resource_node("bench", "grain", "town", "grain", qty=50, qty_max=100, regen_per_day=0.1)

        for i in range(agents):
            npc_id = f"npc_{i:04d}"
            e.upsert_npc("bench", npc_id, f"NPC {i}", hp=5, max_hp=5, ac=10, location="home")
            e.save_npc_need("bench", npc_id, "hunger", 50, baseline=80, drift_per_day=0.02)
            e.save_npc_action(
                "bench", npc_id, "eat", location="home", base_utility=0.1,
                considerations=[{"type": "need", "key": "hunger", "weight": 1.0}],
                effects=[{"type": "need", "need": "hunger", "delta": -20}],
            )
            e.save_npc_action(
                "bench", npc_id, "work", location="work", base_utility=0.6,
                considerations=[{"type": "need", "key": "hunger", "weight": -0.2}],
            )

        e.save_simulation_rule("bench", "daily_decide", "decide", cadence="day", priority=20)
        e.save_simulation_rule(
            "bench", "grain_growth", "stock", cadence="day", target="resource_nodes.qty",
            params={"item_id": "grain"}, priority=10,
        )
        e.save_simulation_rule(
            "bench", "rare_event", "chance", cadence="day",
            params={"p": 0.01, "event_type": "news", "summary": "News spreads."}, priority=30,
        )

        started = time.perf_counter()
        result = e.advance_world("bench", days * 1440)
        elapsed = time.perf_counter() - started
        with e._db() as db:
            transitions = db.execute(
                "SELECT COUNT(*) n FROM events WHERE campaign_id='bench' AND event_type='sim_decision'"
            ).fetchone()["n"]
            chance_events = db.execute(
                "SELECT COUNT(*) n FROM events WHERE campaign_id='bench' AND event_type='news'"
            ).fetchone()["n"]

        return {
            "agents": agents,
            "days": days,
            "agent_day_evaluations": agents * days,
            "elapsed_seconds": round(elapsed, 3),
            "decision_transition_events": int(transitions),
            "chance_events": int(chance_events),
            "simulation_tally": result["simulation"],
            "final_time": result["world_time"],
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="World Engine off-screen simulation benchmark")
    parser.add_argument("--agents", type=int, default=200)
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()
    if args.agents < 1 or args.days < 1:
        raise SystemExit("--agents and --days must be >= 1")
    print(json.dumps(run_benchmark(args.agents, args.days), indent=2))
