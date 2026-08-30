from __future__ import annotations
import json
from pathlib import Path
from world_engine_permanent_endpoint import ensure_permanent_runtime, permanent_status, persistent_data_dir

repair=ensure_permanent_runtime(Path(__file__).resolve().parent,data=persistent_data_dir())
print("Automatic runtime repair:")
print(json.dumps(repair,indent=2))
result=permanent_status()
print("\nEndpoint verification:")
print(json.dumps(result,indent=2))
if not result.get("configured"):
    raise SystemExit(2)
if not result.get("health_ok") or not result.get("protected_auth_ok"):
    raise SystemExit(1)
print("\nPERMANENT ENDPOINT: PASS")
