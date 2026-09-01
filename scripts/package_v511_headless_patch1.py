#!/usr/bin/env python3
"""Build the verified World Engine 5.1.1 headless long-horizon patch archive."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import package_v511 as inherited

PACKAGE_ROOT = "world_engine_v5_1_1_HEADLESS_LONG_HORIZON_PATCH1_WINDOWS_FULL"
PACKAGE_NAME = PACKAGE_ROOT + ".zip"
HANDOFF_NAME = "WORLD_ENGINE_V511_HEADLESS_LONG_HORIZON_PATCH1_HANDOFF.json"

PATCH_CRITICAL_FILES = (
    "scripts/package_v511_headless_patch1.py",
    "scripts/benchmark_headless_horizon_v511.py",
    "tests/test_v511_long_horizon.py",
    "HEADLESS_PLAYTEST_AND_LONG_HORIZON_REPORT_V511_PATCH1.md",
)

inherited.PACKAGE_ROOT = PACKAGE_ROOT
inherited.PACKAGE_NAME = PACKAGE_NAME
inherited.HANDOFF_NAME = HANDOFF_NAME
inherited.CRITICAL_FILES = tuple(
    dict.fromkeys((*inherited.CRITICAL_FILES, *PATCH_CRITICAL_FILES))
)


if __name__ == "__main__":
    raise SystemExit(inherited.main())
