from __future__ import annotations

import json
from pathlib import Path

from world_engine_startup import automatic_startup


result = automatic_startup(
    Path(__file__).resolve().parent,
    interactive=False,
    allow_download=False,
    launch_ui=False,
)
print(json.dumps({
    "status": result["status"],
    "backend": result["backend"],
    "endpoint": result["endpoint"],
    "final_verification": result["final_verification"],
    "api_key_fingerprint": result["api_key_fingerprint"],
}, indent=2))
if result["status"] != "PASS":
    raise SystemExit(1)
print("\nWORLD ENGINE 4.0 AUTOMATIC CONNECTION: PASS")
