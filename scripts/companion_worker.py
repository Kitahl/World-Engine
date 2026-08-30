"""Run the existing hardened v4.3 companion outbox worker.

Foundry delivery is restricted by the library to a literal loopback IP origin.
The API key is read from an environment variable and is never accepted on the
command line, where it could be exposed in process listings.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from world_engine import WorldEngine  # noqa: E402
from world_engine.companion import (  # noqa: E402
    CompanionService,
    CompanionWorker,
    FoundryBridge,
    FoundryConfig,
)
from world_engine_connection_guard import persistent_data_dir  # noqa: E402


def _database_path(value: str | None) -> Path:
    configured = value or os.environ.get("WORLD_ENGINE_DB")
    if configured:
        return Path(configured).expanduser().resolve()
    return persistent_data_dir() / "world_engine.sqlite3"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deliver accepted presentations to Foundry.")
    parser.add_argument("--db", help="World Engine SQLite path (or WORLD_ENGINE_DB).")
    parser.add_argument(
        "--foundry-url",
        default=os.environ.get("WORLD_ENGINE_FOUNDRY_URL", "http://127.0.0.1:3010"),
        help="Literal loopback relay origin only.",
    )
    parser.add_argument(
        "--max-items", type=int, default=1, help="Maximum claims to process (1-100)."
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not 1 <= args.max_items <= 100:
        raise SystemExit("--max-items must be between 1 and 100")
    engine = WorldEngine(_database_path(args.db))
    service = CompanionService(engine)
    bridge = FoundryBridge(
        FoundryConfig(
            base_url=args.foundry_url,
            api_key=os.environ.get("WORLD_ENGINE_FOUNDRY_API_KEY", ""),
        )
    )
    worker = CompanionWorker(service, bridge)
    delivered = 0
    for _ in range(args.max_items):
        result = worker.run_once()
        delivered += result
        if result == 0:
            break
    print(f"processed={delivered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
