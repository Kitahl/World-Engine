"""Trusted local review tool for exact v4.3 narrative publication attempts.

This script is intentionally not an HTTP or GPT Action surface. Approval and
rejection remain bound to the stored canonical candidate and its server digest.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from world_engine import WorldEngine  # noqa: E402
from world_engine_connection_guard import persistent_data_dir  # noqa: E402


def _database_path(value: str | None) -> Path:
    configured = value or os.environ.get("WORLD_ENGINE_DB")
    if configured:
        return Path(configured).expanduser().resolve()
    return persistent_data_dir() / "world_engine.sqlite3"


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or attest an exact stored World Engine publication candidate."
    )
    parser.add_argument("--db", help="World Engine SQLite path (or WORLD_ENGINE_DB).")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_cmd = sub.add_parser("inspect", help="Print the exact candidate under review.")
    inspect_cmd.add_argument("--campaign", default="default")
    inspect_cmd.add_argument("--attempt-id", required=True)

    decide_cmd = sub.add_parser("decide", help="Approve or reject the exact candidate.")
    decide_cmd.add_argument("--campaign", default="default")
    decide_cmd.add_argument("--attempt-id", required=True)
    decide_cmd.add_argument("--candidate-digest", required=True)
    decide_cmd.add_argument("--reviewer-id", required=True)
    decide_cmd.add_argument(
        "--authority-kind", choices=("human", "trusted_server"), default="human"
    )
    decide_cmd.add_argument("--decision", choices=("approve", "reject"), required=True)

    latest_cmd = sub.add_parser(
        "latest", help="Print the latest durably accepted public presentation."
    )
    latest_cmd.add_argument("--campaign", default="default")
    return parser


def main() -> int:
    args = _parser().parse_args()
    engine = WorldEngine(_database_path(args.db))
    if args.command == "inspect":
        _print(engine.publication_attempt_for_review(args.campaign, args.attempt_id))
        return 0
    if args.command == "latest":
        _print(engine.latest_accepted_presentation(args.campaign))
        return 0

    review = engine.publication_attempt_for_review(args.campaign, args.attempt_id)
    actual_digest = str(review["candidate_digest"])
    if not hmac.compare_digest(actual_digest, args.candidate_digest):
        raise SystemExit("candidate digest mismatch; inspect the attempt again")
    result = engine.attest_publication_attempt(
        args.campaign,
        args.attempt_id,
        authority_kind=args.authority_kind,
        reviewer_id=args.reviewer_id,
        decision=args.decision,
    )
    _print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
