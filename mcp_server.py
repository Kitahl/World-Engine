"""Trusted-local MCP interface over the authoritative WorldEngine database.

Run after installing requirements-mcp.txt:
    uvicorn mcp_server:app --host 127.0.0.1 --port 8001

This operator surface intentionally includes private reads and direct writes. It
is therefore loopback-only and is never a public gameplay/API alternative.
"""
from __future__ import annotations

import os
from ipaddress import ip_address
from pathlib import Path
from world_engine_connection_guard import persistent_data_dir
from typing import Any

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from world_engine import WorldEngine

ROOT = Path(__file__).resolve().parent
PERSISTENT_DATA_DIR = persistent_data_dir()
PERSISTENT_DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.environ.get("WORLD_ENGINE_DB", PERSISTENT_DATA_DIR / "world_engine.sqlite3"))
engine = WorldEngine(DB_PATH)
engine.ensure_campaign("default")
mcp = MCPServer("World Engine v4.7 trusted-local operator")


@mcp.tool()
def get_world_context(campaign_id: str = "default", location: str | None = None, event_limit: int = 12) -> dict[str, Any]:
    """Read authoritative scene/world context before narration or resolution."""
    return engine.get_world_context(campaign_id, location, event_limit)


@mcp.tool()
def get_entity(campaign_id: str, kind: str, entity_id: str) -> dict[str, Any]:
    """Read a character, NPC, faction, quest or combat by id."""
    if kind == "character": return engine.get_character(campaign_id, entity_id)
    if kind == "npc": return engine.get_npc(campaign_id, entity_id)
    if kind == "faction": return engine.get_faction(campaign_id, entity_id)
    if kind == "quest": return engine.get_quest(campaign_id, entity_id)
    if kind == "combat": return engine.get_combat(campaign_id, entity_id)
    raise ValueError("kind must be character, npc, faction, quest, or combat")


@mcp.tool()
def resolve_check(modifier: int, dc: int, mode: str = "normal") -> dict[str, Any]:
    """Resolve a 5e-style d20 check."""
    return engine.resolve_check(modifier, dc, mode)


@mcp.tool()
def resolve_attack(campaign_id: str, attacker_kind: str, attacker_id: str, target_kind: str, target_id: str, attack_bonus: int, damage_expression: str, mode: str = "normal", attack_name: str = "attack", combat_id: str | None = None) -> dict[str, Any]:
    """Resolve an attack, apply persistent damage and ledger the result."""
    return engine.resolve_attack(campaign_id, attacker_kind, attacker_id, target_kind, target_id, attack_bonus=attack_bonus, damage_expression=damage_expression, mode=mode, attack_name=attack_name, combat_id=combat_id)


@mcp.tool()
def set_condition(campaign_id: str, kind: str, actor_id: str, condition: str, active: bool, reason: str = "condition changed") -> dict[str, Any]:
    """Add/remove a persistent condition."""
    return engine.set_condition(campaign_id, kind, actor_id, condition, active, reason)


@mcp.tool()
def adjust_relationship(campaign_id: str, source_id: str, target_id: str, trust_delta: int = 0, fear_delta: int = 0, respect_delta: int = 0, affection_delta: int = 0, reason: str = "relationship changed") -> dict[str, Any]:
    """Adjust directed persistent relationship scores."""
    return engine.adjust_relationship(campaign_id, source_id, target_id, trust_delta=trust_delta, fear_delta=fear_delta, respect_delta=respect_delta, affection_delta=affection_delta, reason=reason)


@mcp.tool()
def advance_world(campaign_id: str, minutes: int, reason: str = "elapsed time", weather: str | None = None) -> dict[str, Any]:
    """Advance persistent world time and optionally weather."""
    return engine.advance_world(campaign_id, minutes, reason, weather)


@mcp.tool()
def commit_world_event(campaign_id: str, event_type: str, summary: str, region: str | None = None, actor_id: str | None = None, target_id: str | None = None) -> dict[str, Any]:
    """Commit a persistent world/narrative event."""
    return engine.commit_event(campaign_id, event_type, summary, region=region, actor_id=actor_id, target_id=target_id)



@mcp.tool()
def author_world_content(campaign_id: str = "default", action: str = "digest", batch_id: str | None = None, mode: str = "bootstrap", payload: dict[str, Any] | None = None, days: int = 365, location_id: str | None = None, object_kind: str | None = None, object_id: str | None = None, reason: str = "player touched", gap_key: str | None = None, gap_kind: str | None = None, summary: str | None = None, scope_id: str | None = None, context: dict[str, Any] | None = None, status: str = "resolved", limit: int = 20) -> Any:
    """Authoring-time content pipeline. The model proposes stored rules/content; runtime execution remains deterministic."""
    if action == "stage":
        if not batch_id: raise ValueError("batch_id required")
        return engine.author_stage(campaign_id, batch_id, payload or {}, mode=mode)
    if action == "validate":
        if not batch_id: raise ValueError("batch_id required")
        return engine.author_validate(campaign_id, batch_id)
    if action == "dry_run":
        if not batch_id: raise ValueError("batch_id required")
        return engine.author_dry_run(campaign_id, batch_id, days=days)
    if action == "promote":
        if not batch_id: raise ValueError("batch_id required")
        return engine.author_promote(campaign_id, batch_id)
    if action == "materialization_brief":
        if not location_id: raise ValueError("location_id required")
        return engine.author_materialization_brief(campaign_id, location_id)
    if action == "digest":
        return engine.author_world_digest(campaign_id)
    if action == "lock":
        if not object_kind or not object_id: raise ValueError("object_kind and object_id required")
        return engine.author_lock(campaign_id, object_kind, object_id, reason=reason)
    if action == "list_gaps":
        return engine.author_list_gaps(campaign_id, limit)
    if action == "log_gap":
        if not gap_key or not gap_kind or not summary: raise ValueError("gap_key, gap_kind and summary required")
        return engine.author_log_gap(campaign_id, gap_key, gap_kind, summary, scope_id=scope_id, context=context or {})
    if action == "resolve_gap":
        if not gap_key: raise ValueError("gap_key required")
        return engine.author_resolve_gap(campaign_id, gap_key, status=status)
    raise ValueError("unknown authoring action")


@mcp.tool()
def run_rules_kernel(operation: str, campaign_id: str = "default", payload: dict[str, Any] | None = None) -> Any:
    """Define or execute a deterministic generalized tabletop-RPG rules operation."""
    return engine.rules_dispatch(operation, campaign_id, payload or {})


@mcp.tool()
def get_internal_state_block(campaign_id: str = "default", location: str | None = None) -> dict[str, Any]:
    """Read hidden numerical simulation state. Do not display raw values to players."""
    return engine.get_internal_state_block(campaign_id, location)


@mcp.tool()
def save_visual_profile(campaign_id: str, entity_kind: str, entity_id: str, profile: dict[str, Any], merge: bool = True) -> dict[str, Any]:
    """Persist appearance descriptors for scene-image continuity. Does not generate a portrait."""
    return engine.set_visual_profile(campaign_id, entity_kind, entity_id, profile, merge=merge)


@mcp.tool()
def get_visual_profile(campaign_id: str, entity_kind: str, entity_id: str) -> dict[str, Any]:
    """Read persistent appearance descriptors used in image prompts."""
    return engine.get_visual_profile(campaign_id, entity_kind, entity_id)


@mcp.tool()
def save_visual_state(campaign_id: str, scope_type: str, scope_id: str, state: dict[str, Any], merge: bool = True) -> dict[str, Any]:
    """Persist location, scene, or combat visual continuity."""
    return engine.set_visual_state(campaign_id, scope_type, scope_id, state, merge=merge)


@mcp.tool()
def get_visual_state(campaign_id: str, scope_type: str, scope_id: str) -> dict[str, Any]:
    """Read location, scene, or combat visual continuity."""
    return engine.get_visual_state(campaign_id, scope_type, scope_id)


@mcp.tool()
def get_recent_image_context(campaign_id: str = "default", location_id: str | None = None, limit: int = 5) -> dict[str, Any]:
    """Read recent image context to maintain visual consistency."""
    return engine.get_recent_image_context(campaign_id, location_id, limit)


@mcp.tool()
def get_visual_preferences(campaign_id: str = "default") -> dict[str, Any]:
    """Read image-trigger preferences for the campaign."""
    return engine.get_visual_preferences(campaign_id)


@mcp.tool()
def set_visual_preferences(campaign_id: str = "default", auto_images: bool = True, scene_start: bool = True, battle_start: bool = True, new_location: bool = True, event_choice: bool = True, art_style: str = "cinematic fantasy illustration", additional_instructions: str = "", negative_instructions: str = "") -> dict[str, Any]:
    """Configure automatic image cue behavior and style."""
    return engine.set_visual_preferences(campaign_id, auto_images=auto_images, scene_start=scene_start, battle_start=battle_start, new_location=new_location, event_choice=event_choice, art_style=art_style, additional_instructions=additional_instructions, negative_instructions=negative_instructions)


@mcp.tool()
def build_image_cue(campaign_id: str = "default", trigger_type: str = "scene_start", location_id: str | None = None, combat_id: str | None = None, scene_key: str | None = None, summary: str | None = None, choice_options: list[str] | None = None, aspect_ratio: str | None = None, force: bool = False) -> dict[str, Any]:
    """Build a native-image-generation prompt for a scene start, battle start, new location, or event-choice moment."""
    return engine.build_image_cue(campaign_id, trigger_type=trigger_type, location_id=location_id, combat_id=combat_id, scene_key=scene_key, summary=summary, choice_options=choice_options or [], aspect_ratio=aspect_ratio, force=force)


@mcp.tool()
def record_image_generation(campaign_id: str, trigger_type: str, scene_key: str, title: str, prompt: str, aspect_ratio: str = "4:3", location_id: str | None = None, combat_id: str | None = None, image_ref: str | None = None, status: str = "generated", visual_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Record that a visual cue has been rendered to prevent duplicate auto-generation."""
    return engine.record_image_generation(campaign_id, trigger_type, scene_key, title=title, prompt=prompt, aspect_ratio=aspect_ratio, location_id=location_id, combat_id=combat_id, image_ref=image_ref, status=status, visual_context=visual_context)

def _security() -> TransportSecuritySettings:
    port = int(os.environ.get("MCP_PORT", "8001"))
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            f"127.0.0.1:{port}",
            f"localhost:{port}",
            f"[::1]:{port}",
        ],
        allowed_origins=[
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
            f"http://[::1]:{port}",
        ],
    )


class _LoopbackOnly:
    """Reject non-loopback peers even if a caller starts Uvicorn incorrectly."""

    def __init__(self, wrapped: Any) -> None:
        self.wrapped = wrapped

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") in {"http", "websocket"}:
            client = scope.get("client")
            address = str(client[0]) if isinstance(client, (tuple, list)) and client else ""
            try:
                loopback = ip_address(address).is_loopback
            except ValueError:
                loopback = False
            if not loopback:
                if scope.get("type") == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await send({
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8")],
                    })
                    await send({
                        "type": "http.response.body",
                        "body": b"World Engine MCP is trusted-local only.",
                    })
                return
        await self.wrapped(scope, receive, send)


_transport_app = mcp.streamable_http_app(
    json_response=True,
    stateless_http=True,
    transport_security=_security(),
)
app = _LoopbackOnly(_transport_app)

if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    try:
        is_loopback = ip_address(host).is_loopback
    except ValueError:
        is_loopback = host.lower() == "localhost"
    if not is_loopback:
        raise SystemExit("World Engine MCP is trusted-local only; MCP_HOST must be a loopback address.")
    uvicorn.run("mcp_server:app", host=host, port=int(os.environ.get("MCP_PORT", "8001")), reload=False)
