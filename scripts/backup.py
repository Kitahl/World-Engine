from __future__ import annotations
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
src = Path(os.environ.get("WORLD_ENGINE_DB", root / "data" / "world_engine.sqlite3"))
if not src.exists():
    raise SystemExit(f"Database does not exist: {src}")
out_dir = root / "backups"
out_dir.mkdir(parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
dst = out_dir / f"world_engine_{stamp}.sqlite3"
with sqlite3.connect(src) as source, sqlite3.connect(dst) as target:
    source.backup(target)
print(dst)
