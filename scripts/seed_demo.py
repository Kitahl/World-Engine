from pathlib import Path
import os
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from world_engine import WorldEngine

db = Path(os.environ.get("WORLD_ENGINE_DB", root / "data" / "world_engine.sqlite3"))
e = WorldEngine(db)
e.ensure_campaign("default", "World Engine Demo")
e.upsert_location("default", "crossroads", "Old Crossroads", region="Northmarch", description="A muddy crossroads beside a weathered shrine.", tags=["road", "shrine"], state={"danger": 1})
e.upsert_character("default", "player", "Adventurer", level=1, hp=12, max_hp=12, ac=15, location="crossroads", abilities={"dex": 2, "wis": 1}, resources={"hit_dice": 1}, inventory=[{"name": "Traveler's pack"}])
e.upsert_faction("default", "wardens", "Northmarch Wardens", region="Northmarch", reserve_score=20, goals=["Keep the roads open"])
e.upsert_npc("default", "warden_sera", "Warden Sera", hp=18, max_hp=18, ac=16, location="crossroads", faction_id="wardens", attitude=1, stats={"dex_mod": 2}, beliefs=["The eastern road is unsafe"], goals=["Find the missing patrol"], memory=[{"summary": "Met the adventurer at the crossroads"}])
e.upsert_quest("default", "missing_patrol", "Find the Missing Patrol", owner_id="player", region="Northmarch", objectives=[{"text": "Search the eastern road", "complete": False}])
print(e.get_world_context("default", "crossroads"))
