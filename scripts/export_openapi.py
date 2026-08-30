from __future__ import annotations
import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from app import app
from world_engine.openapi_compat import (
    PUBLIC_ACTION_OPERATION_IDS,
    ensure_object_properties,
    mark_actions_non_consequential,
    object_schema_paths_missing_properties,
)

public_url = os.environ.get("WORLD_ENGINE_PUBLIC_URL", "https://YOUR-HOST").rstrip("/")
schema = app.openapi()
schema["servers"] = [{"url": public_url}]
ensure_object_properties(schema)
paths = schema.get("paths", {})
# ChatGPT GPT Actions builder currently enforces a 30-operation maximum.
# Keep these helper reads available on the backend/MCP, but do not expose them
# as GPT Actions because buildImageCue already consumes the same data internally.
for hidden_path in (
    "/api/snapshot",
    "/api/visual/profile/{entity_kind}/{entity_id}",
    "/api/visual/state/{scope_type}/{scope_id}",
    "/api/visual/recent",
    "/api/context",
    "/api/entity/{kind}/{entity_id}",
    "/api/setup/npc",
    "/api/setup/faction",
    "/api/npc/state",
    "/api/faction/adjust",
    "/api/world/state",
    "/api/sim/configure",
    "/api/authoring",
):
    paths.pop(hidden_path, None)

# Visual preferences remain configurable through the backend/launcher. Hide
# both operations from GPT Actions to make room for publishPresentation.
paths.pop("/api/visual/preferences", None)
# v3.9.5 preserves saveVisualProfile so the GPT can establish canonical character/NPC identity references.
paths.pop("/api/internal/state", None)
# saveVisualState remains backend/MCP-only; scene mutations and image cue generation preserve visual continuity.
paths.get("/api/visual/state", {}).pop("post", None)
# World Engine 4.0 routes narrative/world-event commits through resolveTurn; keep the low-level endpoint backend/MCP-only.
paths.pop("/api/world/event", None)
http_methods = {"get", "post", "put", "patch", "delete", "options", "head"}
for path, methods in list(paths.items()):
    if not isinstance(methods, dict):
        continue
    for method, operation in list(methods.items()):
        if (
            method in http_methods
            and isinstance(operation, dict)
            and operation.get("operationId") not in PUBLIC_ACTION_OPERATION_IDS
        ):
            methods.pop(method, None)
    if not any(method in http_methods for method in methods):
        paths.pop(path, None)

operation_count = sum(
    1
    for methods in paths.values()
    for operation in methods.values()
    if isinstance(operation, dict) and operation.get("operationId")
)
if operation_count > 30:
    raise RuntimeError(f"GPT Actions schema has {operation_count} operations; maximum is 30")
mark_actions_non_consequential(schema)
missing_object_properties = object_schema_paths_missing_properties(schema)
if missing_object_properties:
    raise RuntimeError(f"OpenAI-incompatible object schemas remain: {missing_object_properties[:10]}")
(root / "openapi_actions.json").write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(root / "openapi_actions.json")
