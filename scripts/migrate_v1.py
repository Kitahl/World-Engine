"""Import the earlier World Engine MVP SQLite database into v2/default campaign.

Usage:
    python scripts/migrate_v1.py path/to/old_world_engine.sqlite3

The old file is read-only; v2 writes to WORLD_ENGINE_DB or data/world_engine.sqlite3.
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from world_engine import WorldEngine

parser = argparse.ArgumentParser()
parser.add_argument("source")
parser.add_argument("--campaign", default="default")
args = parser.parse_args()
source = Path(args.source)
if not source.exists():
    raise SystemExit(f"Missing source database: {source}")

target_path = Path(os.environ.get("WORLD_ENGINE_DB", root / "data" / "world_engine.sqlite3"))
e = WorldEngine(target_path)
e.ensure_campaign(args.campaign, "Migrated World Engine Campaign")

src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
src.row_factory = sqlite3.Row

def has_table(name: str) -> bool:
    return src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

counts = {"characters": 0, "npcs": 0, "factions": 0, "events": 0}

if has_table("characters"):
    for r in src.execute("SELECT * FROM characters"):
        d = dict(r)
        e.upsert_character(
            args.campaign, d["id"], d["name"], level=d["level"], hp=d["hp"], max_hp=d["max_hp"], ac=d["ac"],
            location=d["location"], conditions=json.loads(d.get("conditions_json", "[]")), resources=json.loads(d.get("resources_json", "{}")),
        )
        counts["characters"] += 1

if has_table("npcs"):
    for r in src.execute("SELECT * FROM npcs"):
        d = dict(r)
        e.upsert_npc(
            args.campaign, d["id"], d["name"], location=d["location"], faction_id=d.get("faction_id"), attitude=d["attitude"],
            beliefs=json.loads(d.get("beliefs_json", "[]")), goals=json.loads(d.get("goals_json", "[]")),
            routine=json.loads(d.get("routine_json", "{}")), memory=json.loads(d.get("memory_json", "[]")),
        )
        counts["npcs"] += 1

if has_table("factions"):
    for r in src.execute("SELECT * FROM factions"):
        d = dict(r)
        e.upsert_faction(args.campaign, d["id"], d["name"], region=d["region"], reputation=d["reputation"], reserve_score=d["reserve_score"], goals=json.loads(d.get("goals_json", "[]")))
        counts["factions"] += 1

if has_table("events"):
    for r in src.execute("SELECT * FROM events ORDER BY id"):
        d = dict(r)
        payload = json.loads(d.get("payload_json", "{}"))
        payload["migrated_v1_world_time"] = d.get("world_time")
        e.commit_event(args.campaign, d["event_type"], d["summary"], region=d.get("region"), actor_id=d.get("actor_id"), payload=payload)
        counts["events"] += 1

src.close()
print({"target": str(target_path), "campaign": args.campaign, "imported": counts, "revision": e.get_campaign(args.campaign)["revision"]})
