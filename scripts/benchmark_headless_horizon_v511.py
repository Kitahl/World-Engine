"""Measure exact long-horizon simulation on an explicitly disposable DB copy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from world_engine import WorldEngine


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_provenance() -> dict[str, Any]:
    def text(*args: str) -> str | None:
        completed = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 else None

    status = text("status", "--porcelain")
    return {
        "commit": text("rev-parse", "HEAD"),
        "worktree_dirty": bool(status) if status is not None else None,
    }


def run(database: Path, campaign_id: str, years: int) -> dict[str, Any]:
    if years < 1 or years > 100:
        raise ValueError("years must be 1..100")
    database = database.resolve()
    source_database_sha256 = _sha256(database)
    source_database_bytes = database.stat().st_size
    engine = WorldEngine(database)
    before = engine.get_campaign(campaign_id)
    per_year: list[float] = []
    tallies: list[dict[str, Any]] = []
    checkpoints: list[dict[str, Any]] = []
    with engine._db() as db:
        config = db.execute(
            "SELECT seed,rng_counter,max_cascade_depth,max_cascade_events FROM sim_config WHERE campaign_id=?",
            (campaign_id,),
        ).fetchone()
        setup_row = db.execute(
            """SELECT payload_json FROM events WHERE campaign_id=?
               AND event_type='headless_session_setup_receipt' ORDER BY id DESC LIMIT 1""",
            (campaign_id,),
        ).fetchone()
    setup_payload = json.loads(setup_row["payload_json"]) if setup_row else None
    started = time.perf_counter()
    for year_index in range(1, years + 1):
        year_started = time.perf_counter()
        result = engine.advance_world(
            campaign_id,
            365 * 24 * 60,
            "exact long-horizon benchmark",
        )
        year_elapsed = time.perf_counter() - year_started
        per_year.append(year_elapsed)
        tallies.append(result["simulation"])
        with engine._db() as db:
            checkpoint_events = int(
                db.execute(
                    "SELECT COUNT(*) FROM events WHERE campaign_id=?", (campaign_id,)
                ).fetchone()[0]
            )
        checkpoints.append(
            {
                "year": year_index,
                "elapsed_seconds": year_elapsed,
                "revision": int(result["revision"]),
                "world_time": result["world_time"],
                "database_bytes": database.stat().st_size,
                "events_total": checkpoint_events,
            }
        )
    elapsed = time.perf_counter() - started
    after = engine.get_campaign(campaign_id)
    with engine._db() as db:
        event_total = int(
            db.execute(
                "SELECT COUNT(*) FROM events WHERE campaign_id=?", (campaign_id,)
            ).fetchone()[0]
        )
        rollups = {
            row["event_type"]: int(row["n"])
            for row in db.execute(
                """SELECT event_type,COUNT(*) n FROM events
                   WHERE campaign_id=? AND event_type IN (
                     'economy_consumption_rollup',
                     'economy_resource_extracted_rollup'
                   ) GROUP BY event_type""",
                (campaign_id,),
            ).fetchall()
        }
        integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_errors = len(db.execute("PRAGMA foreign_key_check").fetchall())
        population_row = db.execute(
            "SELECT MIN(population) FROM population_state WHERE campaign_id=?",
            (campaign_id,),
        ).fetchone()
        resource_row = db.execute(
            "SELECT MIN(qty) FROM resource_nodes WHERE campaign_id=?",
            (campaign_id,),
        ).fetchone()
        character_row = db.execute(
            "SELECT MIN(hp) FROM characters WHERE campaign_id=?",
            (campaign_id,),
        ).fetchone()
    return {
        "receipt_version": "WE511-HORIZON-BENCHMARK-1.0",
        "input_contract": "database is a disposable copy and is mutated in place",
        "campaign_id": campaign_id,
        "years": years,
        "minutes_per_year": 365 * 24 * 60,
        "elapsed_seconds": elapsed,
        "per_year_seconds": per_year,
        "checkpoints": checkpoints,
        "before_revision": int(before["revision"]),
        "after_revision": int(after["revision"]),
        "before_time": before["world_time"],
        "after_time": after["world_time"],
        "database_bytes": os.path.getsize(database),
        "events_total": event_total,
        "rollups": rollups,
        "integrity": integrity,
        "foreign_key_errors": foreign_key_errors,
        "minimum_population": population_row[0] if population_row else None,
        "minimum_resource_quantity": resource_row[0] if resource_row else None,
        "minimum_character_hp": character_row[0] if character_row else None,
        "source_database_sha256": source_database_sha256,
        "source_database_bytes": source_database_bytes,
        "output_database_sha256": _sha256(database),
        "simulation_config_before": dict(config) if config else None,
        "headless_setup_receipt": setup_payload,
        "runtime": {
            "python": sys.version,
            "sqlite": sqlite3.sqlite_version,
            "platform": platform.platform(),
        },
        "git": _git_provenance(),
        "code_sha256": {
            str(path.relative_to(ROOT)).replace("\\", "/"): _sha256(path)
            for path in (
                ROOT / "world_engine" / "simulation.py",
                ROOT / "scripts" / "headless_player_v511.py",
                Path(__file__).resolve(),
            )
        },
        "year_tallies": tallies,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--years", required=True, type=int)
    parser.add_argument(
        "--confirm-disposable-copy",
        action="store_true",
        help="required acknowledgement that --database may be mutated in place",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.confirm_disposable_copy:
        parser.error("--confirm-disposable-copy is required because the database is mutated")
    encoded = json.dumps(
        run(args.database.resolve(), args.campaign_id, args.years),
        ensure_ascii=False,
        indent=2 if args.output else None,
        sort_keys=True,
    )
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
