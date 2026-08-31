from __future__ import annotations

import os
import secrets
import time
from pathlib import Path
from world_engine_connection_guard import persistent_data_dir, load_json
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from world_engine import WorldEngine
from world_engine.openapi_compat import ensure_object_properties, mark_actions_non_consequential
from world_engine.public_projection import attach_turn_directives as _attach_turn_directives

ROOT = Path(__file__).resolve().parent
PERSISTENT_DATA_DIR = persistent_data_dir()
PERSISTENT_DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.environ.get("WORLD_ENGINE_DB", PERSISTENT_DATA_DIR / "world_engine.sqlite3"))
PERSISTENT_CONFIG_PATH = PERSISTENT_DATA_DIR / "launcher_config.json"
engine = WorldEngine(DB_PATH)
engine.ensure_campaign("default")

app = FastAPI(
    title="World Engine GPT Actions API",
    version="4.3.0",
    description=(
        "Persistent authoritative world/game-state API for the World Engine GPT, including the World Engine Turn Protocol (WETP-1.0), capability routing, a universal entity/relationship graph, knowledge provenance, a bounded context compiler, deterministic tabletop-RPG rules, WORLD/SCENE spatial layers, off-screen simulation, automatic image cues, persistent visual continuity, and a shadow-safe narrative director/dialogue/prose-quality compiler. "
        "ChatGPT normalizes intent and renders prose; this service owns facts, routing, context selection, mutations, narrative contracts, and audit records."
    ),
)

_original_openapi = app.openapi

def _openai_compatible_openapi():
    schema = _original_openapi()
    ensure_object_properties(schema)
    mark_actions_non_consequential(schema)
    return schema

app.openapi = _openai_compatible_openapi

bearer = HTTPBearer(auto_error=False)

ENGINE_VERSION = "4.3.0"

_ENFORCE_PUBLIC_TURN_FIELDS = (
    "protocol_version",
    "campaign_id",
    "turn_id",
    "mode",
    "status",
    "actor_key",
    "completed_intents",
    "failed_intents",
    "authoritative",
    "idempotent_replay",
    "turn_record_status",
    "retry_blocked",
)


def _campaign_probe(campaign_id: str) -> dict[str, Any]:
    try:
        c = engine.get_campaign(campaign_id)
        return {"revision": int(c.get("revision", 0)), "world_time": c.get("world_time")}
    except Exception:
        return {"revision": None, "world_time": None}


def _simulation_signals(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    out: dict[str, Any] = {}
    if isinstance(result.get("simulation"), dict):
        out["simulation"] = result["simulation"]
    for key in ("rules_time_update", "world_cascade_events", "cascade_events", "roll", "damage", "status"):
        if key in result:
            value = result[key]
            if isinstance(value, (str, int, float, bool, type(None), list, dict)):
                out[key] = value
    return out


def _with_receipt(operation: str, campaign_id: str, fn) -> Any:
    before = _campaign_probe(campaign_id)
    started = time.perf_counter()
    result = fn()
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    after = _campaign_probe(campaign_id)
    receipt = {
        "engine_version": ENGINE_VERSION,
        "schema_version": engine.SCHEMA_VERSION,
        "operation": operation,
        "campaign_id": campaign_id,
        "authoritative": True,
        "revision_before": before["revision"],
        "revision_after": after["revision"],
        "revision_delta": (after["revision"] - before["revision"]) if before["revision"] is not None and after["revision"] is not None else None,
        "world_time_before": before["world_time"],
        "world_time_after": after["world_time"],
        "elapsed_ms": elapsed_ms,
        "signals": _simulation_signals(result),
    }
    if isinstance(result, dict):
        out = dict(result)
        out["_engine_receipt"] = receipt
        return out
    return result


def _safe_image_cue(campaign_id: str, **kwargs: Any) -> dict[str, Any] | None:
    try:
        return engine.build_image_cue(campaign_id, **kwargs)
    except (KeyError, ValueError):
        return None


def _reasoning_context(world_context: dict[str, Any] | None) -> dict[str, Any]:
    ctx = world_context or {}
    directors = ctx.get("directors") or {}
    stack = directors.get("stack") if isinstance(directors, dict) else []
    return {
        "director_count": len(stack or []),
        "active_combats": ctx.get("active_combats") or [],
        "quest_counts": {"active": len(ctx.get("active_quests") or [])},
    }


def _turn_task(capability_ids: list[str], mode: str, major_consequence: bool) -> str:
    if mode == "capabilities":
        return "campaign_setup"
    caps = set(capability_ids)
    if "author.content" in caps:
        return "world_generation"
    if caps & {"faction.adjust", "quest.update", "knowledge.transfer"} or major_consequence:
        return "major_plot"
    if caps & {"combat.start", "combat.next", "combat.end", "rules.attack"}:
        return "combat"
    if "npc.dialogue.context" in caps:
        return "dialogue"
    if "actor.move" in caps:
        return "movement"
    if len(caps) >= 4:
        return "multi_system"
    return "routine"


def _infer_turn_image_cue(req: "ResolveTurnRequest", result: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    trigger = req.image_trigger_type
    location = req.location_id
    combat_id = None
    if not trigger:
        for intent in req.intents:
            cap = (intent.capability or "").strip().lower()
            kind = (intent.type or "").strip().lower()
            p = intent.parameters
            if cap == "actor.move" or kind in {"move", "travel"}:
                trigger = "new_location"
                location = p.get("location") or p.get("destination") or location
            elif cap == "combat.start" or kind == "combat_start":
                trigger = "battle_start"
                location = p.get("location") or location
                combat_id = p.get("combat_id")
    if not trigger:
        return None, None
    scene_key = f"turn:{result.get('turn_id', 'unknown')}:{trigger}:{req.decision_phase}"
    cue = _safe_image_cue(
        req.campaign_id,
        trigger_type=trigger,
        location_id=location,
        combat_id=combat_id,
        scene_key=scene_key,
        summary=req.image_summary or req.player_text or None,
        choice_options=req.choice_options,
        decision_phase=req.decision_phase,
    )
    return cue, trigger


def require_key(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    configured = os.environ.get("WORLD_ENGINE_API_KEY", "").strip()
    if not configured:
        configured = str(load_json(PERSISTENT_CONFIG_PATH).get("api_key") or "").strip()
    insecure = {"change-me", "changeme", "change-me-before-public-use", "replace-with-a-long-random-secret"}
    if len(configured) < 24 or configured.lower() in insecure:
        raise HTTPException(status_code=503, detail="WORLD_ENGINE_API_KEY is missing or insecure; protected API is disabled")
    if credentials is None or credentials.scheme.lower() != "bearer" or not secrets.compare_digest(credentials.credentials, configured):
        raise HTTPException(status_code=401, detail="Invalid World Engine API key")


class CampaignRequest(BaseModel):
    campaign_id: str = "default"
    name: str = "World Engine Campaign"
    world_time: str | None = None


class CharacterRequest(BaseModel):
    campaign_id: str = "default"
    character_id: str
    name: str
    level: int = Field(default=1, ge=1, le=20)
    hp: int = Field(default=1, ge=0)
    max_hp: int = Field(default=1, ge=1)
    ac: int = Field(default=10, ge=1, le=40)
    location: str = "unknown"
    abilities: dict[str, int] = Field(default_factory=dict, description="Ability modifiers, e.g. {str:3,dex:2}")
    proficiency_bonus: int = 2
    conditions: list[str] = Field(default_factory=list)
    resources: dict[str, Any] = Field(default_factory=dict)
    inventory: list[Any] = Field(default_factory=list)
    notes: dict[str, Any] = Field(default_factory=dict)


class NpcRequest(BaseModel):
    campaign_id: str = "default"
    npc_id: str
    name: str
    hp: int = Field(default=1, ge=0)
    max_hp: int = Field(default=1, ge=1)
    ac: int = Field(default=10, ge=1, le=40)
    location: str = "unknown"
    faction_id: str | None = None
    attitude: int = Field(default=0, ge=-10, le=10)
    stats: dict[str, Any] = Field(default_factory=dict)
    conditions: list[str] = Field(default_factory=list)
    beliefs: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    routine: dict[str, Any] = Field(default_factory=dict)
    memory: list[Any] = Field(default_factory=list)
    importance: Literal["minor", "supporting", "major"] = "minor"


class FactionRequest(BaseModel):
    campaign_id: str = "default"
    faction_id: str
    name: str
    region: str = "unknown"
    reputation: int = Field(default=0, ge=-10, le=10)
    reserve_score: int = 0
    goals: list[str] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)
    leader_id: str | None = None


class LocationRequest(BaseModel):
    campaign_id: str = "default"
    location_id: str
    name: str
    region: str = "unknown"
    description: str = ""
    x: float | None = None
    y: float | None = None
    realm_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)


class HpRequest(BaseModel):
    campaign_id: str = "default"
    kind: Literal["character", "npc"]
    actor_id: str
    delta: int
    reason: str


class MoveRequest(BaseModel):
    campaign_id: str = "default"
    kind: Literal["character", "npc"]
    actor_id: str
    location: str
    reason: str = "moved"


class NpcStateRequest(BaseModel):
    campaign_id: str = "default"
    npc_id: str
    attitude_delta: int = 0
    add_beliefs: list[str] = Field(default_factory=list)
    remove_beliefs: list[str] = Field(default_factory=list)
    add_goals: list[str] = Field(default_factory=list)
    remove_goals: list[str] = Field(default_factory=list)
    add_memory: list[Any] = Field(default_factory=list)
    reason: str = "NPC state changed"


class FactionAdjustRequest(BaseModel):
    campaign_id: str = "default"
    faction_id: str
    reputation_delta: int = 0
    reserve_delta: int = 0
    state_patch: dict[str, Any] = Field(default_factory=dict)
    add_goals: list[str] = Field(default_factory=list)
    remove_goals: list[str] = Field(default_factory=list)
    reason: str = "faction state changed"


class WorldStateRequest(BaseModel):
    campaign_id: str = "default"
    scope_type: str
    scope_id: str
    state_key: str
    value: Any
    reason: str = "world state changed"


class CheckRequest(BaseModel):
    campaign_id: str = "default"
    modifier: int = Field(ge=-30, le=30)
    dc: int = Field(ge=1, le=100)
    mode: Literal["normal", "advantage", "disadvantage"] = "normal"


class AttackRequest(BaseModel):
    campaign_id: str = "default"
    attacker_kind: Literal["character", "npc"]
    attacker_id: str
    target_kind: Literal["character", "npc"]
    target_id: str
    attack_bonus: int = Field(ge=-30, le=30)
    damage_expression: str = Field(description="Dice expression such as 1d8+3")
    mode: Literal["normal", "advantage", "disadvantage"] = "normal"
    attack_name: str = "attack"
    combat_id: str | None = None
    range_cells: float | None = Field(default=None, ge=0)
    ignore_cover: bool = False
    damage_type: str = Field(default="untyped", description="Structured damage type for resistance, immunity, vulnerability, and ledger output.")


class RulesKernelRequest(BaseModel):
    campaign_id: str = "default"
    operation: Literal[
        "configure", "set_actor_profile", "define_object", "define_activity", "grant_object",
        "set_resource", "define_reaction", "resolve_activity", "rest", "death_save",
        "list_effects", "end_effect", "get_actor_rules", "define_advancement", "apply_advancement"
    ]
    payload: dict[str, Any] = Field(default_factory=dict, description="Operation-specific deterministic rules-kernel payload.")


class ConditionRequest(BaseModel):
    campaign_id: str = "default"
    kind: Literal["character", "npc"]
    actor_id: str
    condition: str
    active: bool
    reason: str = "condition changed"


class ResourceRequest(BaseModel):
    campaign_id: str = "default"
    character_id: str
    resource_delta: dict[str, int] = Field(default_factory=dict)
    add_inventory: list[Any] = Field(default_factory=list)
    remove_inventory_indexes: list[int] = Field(default_factory=list)
    reason: str = "resources updated"


class RelationshipRequest(BaseModel):
    campaign_id: str = "default"
    source_id: str
    target_id: str
    trust_delta: int = 0
    fear_delta: int = 0
    respect_delta: int = 0
    affection_delta: int = 0
    reason: str = "relationship changed"


class QuestRequest(BaseModel):
    campaign_id: str = "default"
    quest_id: str
    title: str
    status: Literal["inactive", "active", "completed", "failed", "abandoned"] = "active"
    owner_id: str | None = None
    region: str | None = None
    objectives: list[Any] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)
    reason: str = "quest updated"


class CombatStartRequest(BaseModel):
    campaign_id: str = "default"
    combat_id: str
    location: str = "unknown"
    participants: list[dict[str, str]] = Field(description="Each participant: {kind:'character'|'npc', id:'...'}")
    grid_width: int = Field(default=20, ge=5, le=100)
    grid_height: int = Field(default=20, ge=5, le=100)
    positions: list[dict[str, Any]] = Field(default_factory=list)
    terrain: list[dict[str, Any]] = Field(default_factory=list)
    scene_id: str | None = None


class CombatTurnRequest(BaseModel):
    campaign_id: str = "default"
    combat_id: str


class CombatEndRequest(CombatTurnRequest):
    reason: str = "combat ended"


class AdvanceWorldRequest(BaseModel):
    campaign_id: str = "default"
    minutes: int = Field(ge=0, le=525600)
    reason: str = "elapsed time"
    weather: str | None = None
    simulate: bool = Field(default=True, description="Execute configured off-screen world simulation while time advances.")
    season: str | None = Field(default=None, description="Optional season override for resource growth; defaults to campaign setting or summer.")


class SimulationConfigRequest(BaseModel):
    campaign_id: str = "default"
    kind: Literal["seed", "rule", "resource", "need", "action", "reaction", "link", "combat_position", "combat_terrain", "item", "inventory", "lifecycle", "drama", "scene", "scene_entity", "scene_feature", "director", "ownership", "status", "npc_life", "world_system", "spatial3d"]
    object_id: str | None = Field(default=None, description="Rule/resource/action/reaction identifier, depending on kind.")
    npc_id: str | None = None
    archetype: Literal["drift", "schedule", "stock", "chance", "spread", "decide"] | None = None
    cadence: Literal["hour", "day", "week"] = "day"
    target: str = ""
    location_id: str | None = None
    item_id: str | None = None
    need: str | None = None
    value: float | None = None
    baseline: float = 50
    drift_per_day: float = 0
    qty: float = 0
    qty_max: float = 10
    regen_per_day: float = 0.5
    seed: int | None = None
    trigger_event_type: str | None = None
    priority: int = 100
    enabled: bool = True
    base_utility: float = 0
    params: dict[str, Any] = Field(default_factory=dict)
    season_mult: dict[str, float] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    considerations: list[dict[str, Any]] = Field(default_factory=list)
    effects: list[dict[str, Any]] = Field(default_factory=list)
    curve: Literal["linear", "quadratic", "urgent", "threshold"] = "quadratic"
    requirements: dict[str, Any] = Field(default_factory=dict)
    cost_hours: float = Field(default=8, ge=0)
    tags: list[str] = Field(default_factory=list)
    selector: dict[str, Any] = Field(default_factory=dict)
    probability: float = Field(default=1.0, ge=0, le=1)
    repeat_policy: Literal["once_per_cascade", "count_limited"] = "once_per_cascade"
    repeat_limit: int = Field(default=1, ge=1)
    from_id: str | None = None
    to_id: str | None = None
    travel_hours: float | None = Field(default=None, ge=0)
    road_quality: str = "road"
    bidirectional: bool = True
    combat_id: str | None = None
    actor_kind: Literal["character", "npc"] | None = None
    actor_id: str | None = None
    x: int | None = None
    y: int | None = None
    cover: Literal["none", "half", "three_quarters", "total"] = "none"
    terrain_kind: str = "open"
    blocks_los: bool = False
    difficult: bool = False
    hazard: dict[str, Any] = Field(default_factory=dict)
    name: str | None = None
    base_price: float = Field(default=0, ge=0)
    effect_dice: str | None = None
    owner_kind: Literal["character", "npc", "faction", "location"] | None = None
    owner_id: str | None = None
    birth_year: int | None = None
    parents: list[str] = Field(default_factory=list)
    spouse_id: str | None = None
    fertility: dict[str, Any] = Field(default_factory=dict)
    heir_id: str | None = None
    mortality: dict[str, Any] = Field(default_factory=dict)
    alive: bool = True
    scene_id: str | None = None
    scene_type: Literal["social", "exploration", "travel", "ritual", "combat", "other"] = "exploration"
    radius_m: float = Field(default=30, ge=1, le=5000)
    feature_id: str | None = None
    z: float = 0
    zone: str = "center"
    stance: str = "neutral"
    persistent: bool = False
    director_kind: Literal["civic", "realm", "faction", "divine", "power"] = "civic"
    scope_type: Literal["location", "region", "realm", "scene", "global"] = "location"
    scope_id: str | None = None
    source_kind: Literal["npc", "faction", "faction_leader", "deity", "power"] | None = None
    source_id: str | None = None
    authority: float = Field(default=1.0, ge=0, le=1)
    weights: dict[str, float] = Field(default_factory=dict)
    policies: dict[str, Any] = Field(default_factory=dict)
    asset_kind: str | None = None
    asset_id: str | None = None
    status: Literal["alive", "dead", "missing"] = "alive"
    low_hp_threshold: float = Field(default=0.35, ge=0, le=1)
    hardship_window_hours: float = Field(default=72, ge=0)
    calm_boost: float = Field(default=1.5, ge=0)
    hardship_suppression: float = Field(default=0.45, ge=0)
    relief_boost: float = Field(default=1.5, ge=0)


class AuthoringRequest(BaseModel):
    campaign_id: str = "default"
    action: Literal["stage", "validate", "dry_run", "promote", "materialization_brief", "digest", "lock", "list_gaps", "log_gap", "resolve_gap"]
    batch_id: str | None = None
    mode: Literal["bootstrap", "lazy", "reactive", "revision"] = "bootstrap"
    payload: dict[str, Any] = Field(default_factory=dict)
    days: int = Field(default=365, ge=1, le=18250)
    location_id: str | None = None
    object_kind: str | None = None
    object_id: str | None = None
    reason: str = "player touched"
    gap_key: str | None = None
    gap_kind: str | None = None
    summary: str | None = None
    scope_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    status: Literal["resolved", "suppressed"] = "resolved"
    limit: int = Field(default=20, ge=1, le=100)

class TurnIntentRequest(BaseModel):
    intent_id: str | None = Field(default=None, description="Stable identifier within this turn; defaults to intent_1, intent_2, etc.")
    type: str | None = Field(default=None, description="Friendly intent alias such as move, check, attack, interact, relation, fact, or advance_time.")
    capability: str | None = Field(default=None, description="Explicit capability ID; takes precedence over type when supplied.")
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    optional: bool = False


class ResolveTurnRequest(BaseModel):
    campaign_id: str = "default"
    actor_kind: Literal["character"] | None = None
    actor_id: str | None = None
    player_text: str = Field(default="", max_length=20000)
    intents: list[TurnIntentRequest] = Field(default_factory=list, max_length=20)
    expected_revision: int | None = Field(default=None, ge=0)
    idempotency_key: str | None = None
    mode: Literal["execute", "plan", "context_only", "capabilities"] = "execute"
    max_context_chars: int = Field(default=14000, ge=2000, le=60000)
    include_archive: bool = False
    continue_on_error: bool = False
    retry_failed: bool = False
    location_id: str | None = None
    image_trigger_type: Literal["scene_start", "battle_start", "new_location", "event_choice", "character_reference", "npc_reference"] | None = None
    image_summary: str | None = None
    choice_options: list[str] = Field(default_factory=list)
    decision_phase: Literal["before", "after"] = "before"
    major_consequence: bool = False
    narrative_mode_override: Literal["off", "shadow", "compare", "enforce"] | None = None
    narrative_hint: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional presentation-only inputs such as scene_function, speaker_id, speech_act, facts_to_reveal, information_to_withhold, subtext, and mechanically_supported_player_effects. These cannot overwrite authoritative state.",
    )


class EventRequest(BaseModel):
    campaign_id: str = "default"
    event_type: str
    summary: str
    region: str | None = None
    actor_id: str | None = None
    target_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

class VisualPreferencesRequest(BaseModel):
    campaign_id: str = "default"
    auto_images: bool = True
    scene_start: bool = True
    battle_start: bool = True
    new_location: bool = True
    event_choice: bool = True
    character_reference: bool = True
    major_npc_reference: bool = True
    art_style: str = "cinematic setting-authentic illustration"
    additional_instructions: str = "Show the current scene clearly, with readable composition, setting-authentic environmental detail, and strong environmental storytelling."
    negative_instructions: str = "UI overlays, text blocks, watermarks, or anachronistic elements not established by the World Bible."


class ImageCueRequest(BaseModel):
    campaign_id: str = "default"
    trigger_type: Literal["scene_start", "battle_start", "new_location", "event_choice", "character_reference", "npc_reference"]
    location_id: str | None = None
    combat_id: str | None = None
    scene_key: str | None = None
    summary: str | None = None
    choice_options: list[str] = Field(default_factory=list)
    aspect_ratio: str | None = None
    force: bool = False
    decision_phase: Literal["before", "after"] = "before"
    entity_kind: Literal["character", "npc"] | None = None
    entity_id: str | None = None


class VisualProfileRequest(BaseModel):
    campaign_id: str = "default"
    entity_kind: Literal["character", "npc"]
    entity_id: str
    profile: dict[str, Any] = Field(default_factory=dict, description="Persistent visual descriptors used only as scene-image continuity inputs; this does not trigger a portrait.")
    merge: bool = True


class VisualStateRequest(BaseModel):
    campaign_id: str = "default"
    scope_type: Literal["location", "scene", "combat"]
    scope_id: str
    state: dict[str, Any] = Field(default_factory=dict, description="Persistent visual continuity descriptors for a location, scene, or combat.")
    merge: bool = True


class ImageRecordRequest(BaseModel):
    campaign_id: str = "default"
    trigger_type: Literal["scene_start", "battle_start", "new_location", "event_choice", "character_reference", "npc_reference"]
    scene_key: str
    title: str
    prompt: str
    aspect_ratio: str = "4:3"
    location_id: str | None = None
    combat_id: str | None = None
    image_ref: str | None = None
    status: str = "generated"
    visual_context: dict[str, Any] = Field(default_factory=dict)
    entity_kind: Literal["character", "npc"] | None = None
    entity_id: str | None = None
    set_as_primary_reference: bool = False


class PublishPresentationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: str = Field(default="default", min_length=1, max_length=100)
    presentation_id: str = Field(min_length=1, max_length=128)
    packet_id: str = Field(min_length=1, max_length=160)
    turn_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)
    narration: str = Field(min_length=1, max_length=24000)
    choices: list[
        Annotated[str, StringConstraints(min_length=1, max_length=500)]
    ] = Field(default_factory=list, max_length=12)


def _public_error_code(_: Exception, fallback: str) -> str:
    """Return an endpoint-owned code; exception values are never public data."""
    return fallback


@app.exception_handler(KeyError)
async def key_error_handler(_, exc: KeyError):
    return JSONResponse(
        status_code=404, content={"detail": _public_error_code(exc, "RESOURCE_NOT_FOUND")}
    )


@app.exception_handler(ValueError)
async def value_error_handler(_, exc: ValueError):
    return JSONResponse(
        status_code=422, content={"detail": _public_error_code(exc, "REQUEST_REJECTED")}
    )


@app.get("/health", include_in_schema=False)
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "world-engine"}


@app.post("/api/campaign", operation_id="ensureCampaign", dependencies=[Depends(require_key)])
def ensure_campaign(req: CampaignRequest) -> dict[str, Any]:
    """Create a campaign if absent and return its authoritative clock, weather and revision."""
    return _with_receipt("ensureCampaign", req.campaign_id, lambda: engine.ensure_campaign(req.campaign_id, req.name, req.world_time))


@app.get("/api/context", include_in_schema=False, dependencies=[Depends(require_key)])
def get_context(
    campaign_id: str = Query(default="default"),
    location: str | None = Query(default=None),
    event_limit: int = Query(default=12, ge=1, le=50),
    destination: str | None = Query(default=None, description="Optional destination; returns authoritative shortest route/travel time when the world graph is configured."),
    entity_limit: int = Query(default=40, ge=1, le=40, description="Maximum living characters+NPCs returned; truncation is reported."),
) -> dict[str, Any]:
    """Read authoritative scene/world context, including graph neighbors/LOD and optional route."""
    result = _with_receipt("getWorldContext", campaign_id, lambda: engine.get_world_context(campaign_id, location, event_limit, destination, entity_limit))
    active_scene = result.get("active_scene") if isinstance(result, dict) else None
    cue = None
    trigger_type = None
    scene_location = (active_scene or {}).get("location_id") if isinstance(active_scene, dict) else None
    location_id = location or scene_location
    if isinstance(active_scene, dict) and active_scene.get("id") and active_scene.get("location_id"):
        trigger_type = "scene_start"
        cue = _safe_image_cue(campaign_id, trigger_type="scene_start", location_id=active_scene["location_id"], scene_key=f"scene:{active_scene['id']}")
    elif location_id:
        trigger_type = "new_location"
        cue = _safe_image_cue(campaign_id, trigger_type="new_location", location_id=location_id)
    if isinstance(result, dict):
        return _attach_turn_directives(result, cue=cue, task="routine", trigger_type=trigger_type, context=_reasoning_context(result))
    return result


@app.get("/api/entity/{kind}/{entity_id}", include_in_schema=False, dependencies=[Depends(require_key)])
def get_entity(kind: Literal["character", "npc", "faction", "location", "quest", "combat", "scene", "director"], entity_id: str, campaign_id: str = "default") -> dict[str, Any]:
    """Read one authoritative character, NPC, faction, quest or combat record."""
    if kind == "character": return engine.get_character_sheet(campaign_id, entity_id)
    if kind == "npc": return engine.get_npc_sheet(campaign_id, entity_id)
    if kind == "faction": return engine.get_faction(campaign_id, entity_id)
    if kind == "location": return engine.get_location(campaign_id, entity_id)
    if kind == "quest": return engine.get_quest(campaign_id, entity_id)
    if kind == "scene":
        scene = engine.get_scene(campaign_id, entity_id)
        if not scene: raise KeyError(entity_id)
        return scene
    if kind == "director": return engine.get_director(campaign_id, entity_id)
    return engine.get_combat(campaign_id, entity_id)


@app.post("/api/setup/character", operation_id="saveCharacter", dependencies=[Depends(require_key)])
def save_character(req: CharacterRequest) -> dict[str, Any]:
    """Create/update a character. Intended for campaign setup or explicit authoritative edits."""
    return engine.upsert_character(
        req.campaign_id, req.character_id, req.name, level=req.level, hp=req.hp, max_hp=req.max_hp,
        ac=req.ac, location=req.location, abilities=req.abilities, proficiency_bonus=req.proficiency_bonus,
        conditions=req.conditions, resources=req.resources, inventory=req.inventory, notes=req.notes,
    )


@app.post("/api/setup/npc", operation_id="saveNpc", dependencies=[Depends(require_key)], include_in_schema=False)
def save_npc(req: NpcRequest) -> dict[str, Any]:
    """Create/update persistent NPC state including beliefs, goals, routine and memory."""
    return engine.upsert_npc(
        req.campaign_id, req.npc_id, req.name, hp=req.hp, max_hp=req.max_hp, ac=req.ac,
        location=req.location, faction_id=req.faction_id, attitude=req.attitude, stats=req.stats,
        conditions=req.conditions, beliefs=req.beliefs, goals=req.goals, routine=req.routine, memory=req.memory, importance=req.importance,
    )


@app.post("/api/setup/faction", operation_id="saveFaction", dependencies=[Depends(require_key)], include_in_schema=False)
def save_faction(req: FactionRequest) -> dict[str, Any]:
    """Create/update a faction's persistent goals, reputation, reserves and custom state."""
    return engine.upsert_faction(req.campaign_id, req.faction_id, req.name, region=req.region, reputation=req.reputation, reserve_score=req.reserve_score, goals=req.goals, state=req.state, leader_id=req.leader_id)


@app.post("/api/setup/location", operation_id="saveLocation", dependencies=[Depends(require_key)])
def save_location(req: LocationRequest) -> dict[str, Any]:
    """Create/update persistent location description, tags and structured state."""
    return engine.upsert_location(**req.model_dump())


@app.post("/api/gameplay/hp", operation_id="applyHpDelta", dependencies=[Depends(require_key)])
def apply_hp(req: HpRequest) -> dict[str, Any]:
    """Apply persistent healing or damage from any source and ledger the reason."""
    return engine.apply_hp_delta(**req.model_dump())


@app.post("/api/gameplay/move", operation_id="moveActor", dependencies=[Depends(require_key)])
def move_actor(req: MoveRequest) -> dict[str, Any]:
    """Move a character/NPC to a new persistent location and ledger movement."""
    result = _with_receipt("moveActor", req.campaign_id, lambda: engine.move_actor(**req.model_dump()))
    cue = _safe_image_cue(req.campaign_id, trigger_type="new_location", location_id=req.location) if req.kind == "character" else None
    return _attach_turn_directives(result, cue=cue, task="movement", trigger_type="new_location" if cue else None)


@app.post("/api/npc/state", operation_id="updateNpcState", dependencies=[Depends(require_key)], include_in_schema=False)
def update_npc_state(req: NpcStateRequest) -> dict[str, Any]:
    """Apply bounded NPC attitude/belief/goal/memory deltas without overwriting unrelated memory."""
    return engine.update_npc_state(**req.model_dump())


@app.post("/api/faction/adjust", operation_id="adjustFaction", dependencies=[Depends(require_key)], include_in_schema=False)
def adjust_faction(req: FactionAdjustRequest) -> dict[str, Any]:
    """Apply faction reputation/reserve/state/goal deltas without replacing unrelated faction state."""
    return engine.adjust_faction(**req.model_dump())


@app.post("/api/world/state", operation_id="setWorldState", dependencies=[Depends(require_key)], include_in_schema=False)
def set_world_state(req: WorldStateRequest) -> dict[str, Any]:
    """Persist one module/world variable under a bounded scope/key and ledger the mutation."""
    return engine.set_world_state(**req.model_dump())


@app.post("/api/gameplay/check", operation_id="resolveCheck", dependencies=[Depends(require_key)])
def resolve_check(req: CheckRequest) -> dict[str, Any]:
    """Resolve a bounded 5e-style d20 check. This does not mutate game state by itself."""
    result = _with_receipt("resolveCheck", req.campaign_id, lambda: engine.resolve_check(req.modifier, req.dc, req.mode, campaign_id=req.campaign_id))
    return _attach_turn_directives(result, task="routine_check")


@app.post("/api/gameplay/attack", operation_id="resolveAttack", dependencies=[Depends(require_key)])
def resolve_attack(req: AttackRequest) -> dict[str, Any]:
    """Resolve a baseline 5e-style attack, apply persistent HP damage, and ledger the outcome."""
    result = _with_receipt("resolveAttack", req.campaign_id, lambda: engine.resolve_attack(**req.model_dump()))
    return _attach_turn_directives(result, task="combat")


@app.post("/api/rules", operation_id="runRulesKernel", dependencies=[Depends(require_key)])
def run_rules_kernel(req: RulesKernelRequest) -> Any:
    """Define or execute one deterministic generalized rules-kernel operation."""
    result = _with_receipt("runRulesKernel", req.campaign_id, lambda: engine.rules_dispatch(req.operation, req.campaign_id, req.payload))
    return _attach_turn_directives(result, task="rules") if isinstance(result, dict) else result


@app.post("/api/gameplay/condition", operation_id="setCondition", dependencies=[Depends(require_key)])
def set_condition(req: ConditionRequest) -> dict[str, Any]:
    """Add/remove a persistent condition on a character or NPC and log the change."""
    return engine.set_condition(**req.model_dump())


@app.post("/api/gameplay/resources", operation_id="updateCharacterResources", dependencies=[Depends(require_key)])
def update_resources(req: ResourceRequest) -> dict[str, Any]:
    """Mutate persistent character resources/inventory and log the change."""
    return engine.update_character_resources(**req.model_dump())


@app.post("/api/social/relationship", operation_id="adjustRelationship", dependencies=[Depends(require_key)])
def adjust_relationship(req: RelationshipRequest) -> dict[str, Any]:
    """Adjust persistent directed trust/fear/respect/affection between two entities."""
    return engine.adjust_relationship(**req.model_dump())


@app.post("/api/quest", operation_id="saveQuest", dependencies=[Depends(require_key)])
def save_quest(req: QuestRequest) -> dict[str, Any]:
    """Create/update a persistent quest, objectives and status."""
    return engine.upsert_quest(**req.model_dump())


@app.post("/api/combat/start", operation_id="startCombat", dependencies=[Depends(require_key)])
def start_combat(req: CombatStartRequest) -> dict[str, Any]:
    """Start/restart persistent combat and roll/store initiative."""
    result = _with_receipt("startCombat", req.campaign_id, lambda: engine.start_combat(**req.model_dump()))
    cue = _safe_image_cue(req.campaign_id, trigger_type="battle_start", location_id=req.location, combat_id=req.combat_id)
    return _attach_turn_directives(result, cue=cue, task="combat", trigger_type="battle_start")


@app.post("/api/combat/next", operation_id="nextCombatTurn", dependencies=[Depends(require_key)])
def next_combat(req: CombatTurnRequest) -> dict[str, Any]:
    """Advance to the next persistent initiative turn, incrementing round when required."""
    result = _with_receipt("nextCombatTurn", req.campaign_id, lambda: engine.next_turn(req.campaign_id, req.combat_id))
    return _attach_turn_directives(result, task="combat")


@app.post("/api/combat/end", operation_id="endCombat", dependencies=[Depends(require_key)])
def end_combat(req: CombatEndRequest) -> dict[str, Any]:
    """End persistent combat and record the reason."""
    result = _with_receipt("endCombat", req.campaign_id, lambda: engine.end_combat(req.campaign_id, req.combat_id, req.reason))
    return _attach_turn_directives(result, task="routine")


@app.post("/api/world/advance", operation_id="advanceWorld", dependencies=[Depends(require_key)])
def advance_world(req: AdvanceWorldRequest) -> dict[str, Any]:
    """Advance authoritative world time and run configured off-screen simulation by default."""
    result = _with_receipt("advanceWorld", req.campaign_id, lambda: engine.advance_world(**req.model_dump()))
    task = "multi_system" if req.simulate and req.minutes >= 240 else "routine"
    return _attach_turn_directives(result, task=task)


@app.post("/api/sim/configure", operation_id="configureSimulation", dependencies=[Depends(require_key)], include_in_schema=False)
def configure_simulation(req: SimulationConfigRequest) -> dict[str, Any]:
    """Configure one deterministic simulation object: seed, rule, resource stock, NPC need/action, or cascade reaction."""
    d = req.model_dump()
    kind = d.pop("kind")
    campaign_id = d.pop("campaign_id")
    if kind == "npc_life":
        operation = str(req.params.get("operation", ""))
        payload = dict(req.params.get("payload") or {})
        if not operation:
            raise ValueError("params.operation is required for kind=npc_life")
        return engine.npc_life_dispatch(operation, campaign_id, payload)
    if kind in {"world_system", "spatial3d"}:
        operation = str(req.params.get("operation", ""))
        payload = dict(req.params.get("payload") or {})
        if not operation:
            raise ValueError("params.operation is required for world/spatial configuration")
        return engine.world_systems_dispatch(operation, campaign_id, payload)
    if kind == "seed":
        if req.seed is None:
            raise ValueError("seed is required for kind=seed")
        return engine.set_simulation_seed(campaign_id, req.seed)
    if kind == "rule":
        if not req.object_id or not req.archetype:
            raise ValueError("object_id and archetype are required for kind=rule")
        return engine.save_simulation_rule(campaign_id, req.object_id, req.archetype, cadence=req.cadence, target=req.target, params=req.params, priority=req.priority, enabled=req.enabled)
    if kind == "resource":
        if not req.object_id or not req.location_id or not req.item_id:
            raise ValueError("object_id, location_id and item_id are required for kind=resource")
        return engine.save_resource_node(campaign_id, req.object_id, req.location_id, req.item_id, qty=req.qty, qty_max=req.qty_max, regen_per_day=req.regen_per_day, season_mult=req.season_mult, metadata=req.metadata)
    if kind == "need":
        if not req.npc_id or not req.need or req.value is None:
            raise ValueError("npc_id, need and value are required for kind=need")
        return engine.save_npc_need(campaign_id, req.npc_id, req.need, req.value, baseline=req.baseline, drift_per_day=req.drift_per_day, curve=req.curve)
    if kind == "action":
        if not req.npc_id or not req.object_id:
            raise ValueError("npc_id and object_id are required for kind=action")
        return engine.save_npc_action(campaign_id, req.npc_id, req.object_id, location=req.location_id, base_utility=req.base_utility, considerations=req.considerations, effects=req.effects, requirements=req.requirements, cost_hours=req.cost_hours, tags=req.tags, enabled=req.enabled)
    if kind == "reaction":
        if not req.object_id or not req.trigger_event_type:
            raise ValueError("object_id and trigger_event_type are required for kind=reaction")
        return engine.save_simulation_reaction(campaign_id, req.object_id, req.trigger_event_type, req.effects, selector=req.selector, probability=req.probability, repeat_policy=req.repeat_policy, repeat_limit=req.repeat_limit, priority=req.priority, enabled=req.enabled)
    if kind == "link":
        if not req.from_id or not req.to_id or req.travel_hours is None:
            raise ValueError("from_id, to_id and travel_hours are required for kind=link")
        return engine.save_location_link(campaign_id, req.from_id, req.to_id, req.travel_hours, road_quality=req.road_quality, bidirectional=req.bidirectional, metadata=req.metadata)
    if kind == "combat_position":
        if not req.combat_id or not req.actor_kind or not req.actor_id or req.x is None or req.y is None:
            raise ValueError("combat_id, actor_kind, actor_id, x and y are required for kind=combat_position")
        return engine.set_combat_position(campaign_id, req.combat_id, req.actor_kind, req.actor_id, req.x, req.y, cover=req.cover)
    if kind == "combat_terrain":
        if not req.combat_id or req.x is None or req.y is None:
            raise ValueError("combat_id, x and y are required for kind=combat_terrain")
        return engine.set_combat_terrain(campaign_id, req.combat_id, req.x, req.y, kind=req.terrain_kind, blocks_los=req.blocks_los, difficult=req.difficult, hazard=req.hazard)
    if kind == "item":
        if not req.object_id or not req.name:
            raise ValueError("object_id and name are required for kind=item")
        return engine.save_item_def(campaign_id, req.object_id, req.name, base_price=req.base_price, effect_dice=req.effect_dice, tags=req.tags, metadata=req.metadata)
    if kind == "inventory":
        if not req.owner_kind or not req.owner_id or not req.item_id:
            raise ValueError("owner_kind, owner_id and item_id are required for kind=inventory")
        return engine.set_inventory_item(campaign_id, req.owner_kind, req.owner_id, req.item_id, req.qty, metadata=req.metadata)
    if kind == "lifecycle":
        if not req.npc_id or req.birth_year is None:
            raise ValueError("npc_id and birth_year are required for kind=lifecycle")
        return engine.save_npc_lifecycle(campaign_id, req.npc_id, birth_year=req.birth_year, parents=req.parents, spouse_id=req.spouse_id, fertility=req.fertility, heir_id=req.heir_id, mortality=req.mortality, alive=req.alive)
    if kind == "drama":
        return engine.set_drama_config(campaign_id, enabled=req.enabled, low_hp_threshold=req.low_hp_threshold, hardship_window_hours=req.hardship_window_hours, calm_boost=req.calm_boost, hardship_suppression=req.hardship_suppression, relief_boost=req.relief_boost)
    if kind == "scene":
        if not req.scene_id:
            raise ValueError("scene_id is required for kind=scene")
        action = str(req.params.get("action", "start"))
        if action == "end":
            result = _with_receipt("configureSimulation", campaign_id, lambda: engine.end_scene(campaign_id, req.scene_id, foldback_state=req.params.get("foldback_state") or {}, reason=str(req.params.get("reason", "scene ended"))))
            return _attach_turn_directives(result, task="routine")
        if not req.location_id:
            raise ValueError("location_id is required to start a scene")
        result = _with_receipt("configureSimulation", campaign_id, lambda: engine.start_scene(campaign_id, req.scene_id, req.location_id, scene_type=req.scene_type, radius_m=req.radius_m, entities=req.params.get("entities") or [], features=req.params.get("features") or [], state=req.params.get("state") or {}))
        cue = _safe_image_cue(campaign_id, trigger_type="scene_start", location_id=req.location_id, scene_key=f"scene:{req.scene_id}")
        return _attach_turn_directives(result, cue=cue, task="routine", trigger_type="scene_start")
    if kind == "scene_entity":
        if not req.scene_id or not req.actor_kind or not req.actor_id or req.x is None or req.y is None:
            raise ValueError("scene_id, actor_kind, actor_id, x and y are required for kind=scene_entity")
        return engine.set_scene_entity(campaign_id, req.scene_id, req.actor_kind, req.actor_id, x=req.x, y=req.y, z=req.z, zone=req.zone, stance=req.stance, state=req.params.get("state") or {})
    if kind == "scene_feature":
        if not req.scene_id or not req.feature_id:
            raise ValueError("scene_id and feature_id are required for kind=scene_feature")
        return engine.set_scene_feature(campaign_id, req.scene_id, req.feature_id, kind=req.terrain_kind, x=req.x or 0, y=req.y or 0, z=req.z, blocks_los=req.blocks_los, difficult=req.difficult, persistent=req.persistent, state=req.params.get("state") or {})
    if kind == "director":
        if not req.object_id or not req.name or not req.source_kind or not req.source_id:
            raise ValueError("object_id, name, source_kind and source_id are required for kind=director")
        return engine.save_director(campaign_id, req.object_id, req.name, director_kind=req.director_kind, scope_type=req.scope_type, scope_id=req.scope_id, source_kind=req.source_kind, source_id=req.source_id, authority=req.authority, priority=req.priority, weights=req.weights, policies=req.policies, enabled=req.enabled)
    if kind == "ownership":
        if not req.asset_kind or not req.asset_id or not req.owner_kind or not req.owner_id:
            raise ValueError("asset_kind, asset_id, owner_kind and owner_id are required for kind=ownership")
        return engine.save_ownership(campaign_id, req.asset_kind, req.asset_id, req.owner_kind, req.owner_id, metadata=req.metadata)
    if kind == "status":
        if not req.actor_kind or not req.actor_id:
            raise ValueError("actor_kind and actor_id are required for kind=status")
        return engine.set_actor_status(campaign_id, req.actor_kind, req.actor_id, req.status, reason=str(req.params.get("reason", "status changed")))
    raise ValueError("unknown simulation config kind")


@app.post("/api/authoring", operation_id="authorWorldContent", dependencies=[Depends(require_key)], include_in_schema=False)
def author_world_content(req: AuthoringRequest) -> dict[str, Any] | list[dict[str, Any]]:
    """Authoring-time content pipeline: stage → validate → dry-run → promote, plus lazy materialisation briefs and reactive content gaps. The model proposes rows; runtime decisions remain deterministic."""
    if req.action == "stage":
        if not req.batch_id:
            raise ValueError("batch_id is required for action=stage")
        return engine.author_stage(req.campaign_id, req.batch_id, req.payload, mode=req.mode)
    if req.action == "validate":
        if not req.batch_id:
            raise ValueError("batch_id is required for action=validate")
        return engine.author_validate(req.campaign_id, req.batch_id)
    if req.action == "dry_run":
        if not req.batch_id:
            raise ValueError("batch_id is required for action=dry_run")
        return engine.author_dry_run(req.campaign_id, req.batch_id, days=req.days)
    if req.action == "promote":
        if not req.batch_id:
            raise ValueError("batch_id is required for action=promote")
        return engine.author_promote(req.campaign_id, req.batch_id)
    if req.action == "materialization_brief":
        if not req.location_id:
            raise ValueError("location_id is required for action=materialization_brief")
        return engine.author_materialization_brief(req.campaign_id, req.location_id)
    if req.action == "digest":
        return engine.author_world_digest(req.campaign_id)
    if req.action == "lock":
        if not req.object_kind or not req.object_id:
            raise ValueError("object_kind and object_id are required for action=lock")
        return engine.author_lock(req.campaign_id, req.object_kind, req.object_id, reason=req.reason)
    if req.action == "list_gaps":
        return engine.author_list_gaps(req.campaign_id, req.limit)
    if req.action == "log_gap":
        if not req.gap_key or not req.gap_kind or not req.summary:
            raise ValueError("gap_key, gap_kind and summary are required for action=log_gap")
        return engine.author_log_gap(req.campaign_id, req.gap_key, req.gap_kind, req.summary, scope_id=req.scope_id, context=req.context)
    if req.action == "resolve_gap":
        if not req.gap_key:
            raise ValueError("gap_key is required for action=resolve_gap")
        return engine.author_resolve_gap(req.campaign_id, req.gap_key, status=req.status)
    raise ValueError("unknown authoring action")

_PUBLIC_TURN_ALLOWED_CAPABILITIES = frozenset({
    "actor.move",
    "space.route",
    "rules.check",
    "rules.attack",
    "rules.generic",
    "actor.condition",
    "actor.resources",
    "social.relationship.adjust",
    "npc.dialogue.context",
    "quest.update",
    "world.advance",
    "combat.start",
    "combat.next",
    "combat.end",
    "progression.manage",
    "visual.cue",
})

_PUBLIC_TURN_ALIAS_TO_CAPABILITY = {
    "interact": "npc.dialogue.context",
    "talk": "npc.dialogue.context",
    "dialogue": "npc.dialogue.context",
    "move": "actor.move",
    "travel": "actor.move",
    "route": "space.route",
    "check": "rules.check",
    "attack": "rules.attack",
    "rules": "rules.generic",
    "condition": "actor.condition",
    "resources": "actor.resources",
    "relationship": "social.relationship.adjust",
    "quest": "quest.update",
    "advance_time": "world.advance",
    "combat_start": "combat.start",
    "combat_next": "combat.next",
    "combat_end": "combat.end",
    "progression": "progression.manage",
    "image": "visual.cue",
}


def _validate_public_turn_request(req: ResolveTurnRequest) -> None:
    for intent in req.intents:
        explicit_capability = str(intent.capability or "").strip().lower()
        alias = str(intent.type or "").strip().lower()
        capability = explicit_capability or _PUBLIC_TURN_ALIAS_TO_CAPABILITY.get(alias, "")
        if capability not in _PUBLIC_TURN_ALLOWED_CAPABILITIES:
            raise HTTPException(status_code=403, detail="PUBLIC_TURN_CAPABILITY_NOT_ALLOWED")
    if req.actor_id and req.actor_kind != "character":
        raise HTTPException(status_code=422, detail="PUBLIC_TURN_CHARACTER_REQUIRED")
    configured_mode = str(engine.get_narrative_config(req.campaign_id).get("mode") or "off")
    if configured_mode == "enforce":
        if req.narrative_mode_override not in {None, "enforce"}:
            raise HTTPException(status_code=409, detail="NARRATIVE_ENFORCE_DOWNGRADE_REJECTED")
        if req.mode != "execute":
            raise HTTPException(status_code=409, detail="NARRATIVE_ENFORCE_EXECUTE_REQUIRED")



@app.post("/api/turn", operation_id="resolveTurn", dependencies=[Depends(require_key)])
def resolve_turn(req: ResolveTurnRequest) -> dict[str, Any]:
    """Route one normalized player turn through the World Engine Turn Protocol (WETP-1.0).

    ChatGPT supplies bounded intent objects; the backend selects capabilities,
    compiles relevant context, executes deterministic providers, records
    idempotency/revisions, and returns an auditable result packet.
    """
    _validate_public_turn_request(req)
    intents = [item.model_dump(exclude_none=True) for item in req.intents]
    result = _with_receipt(
        "resolveTurn",
        req.campaign_id,
        lambda: engine.resolve_turn(
            req.campaign_id,
            actor_kind=req.actor_kind,
            actor_id=req.actor_id,
            raw_player_text=req.player_text,
            intents=intents,
            expected_revision=req.expected_revision,
            idempotency_key=req.idempotency_key,
            mode=req.mode,
            max_context_chars=req.max_context_chars,
            include_archive=req.include_archive,
            continue_on_error=req.continue_on_error,
            retry_failed=req.retry_failed,
            location_id=req.location_id,
        ),
    )
    capability_ids = [x.get("capability_id", "") for x in result.get("capability_plan", [])] if isinstance(result, dict) else []
    cue, trigger = _infer_turn_image_cue(req, result) if isinstance(result, dict) and req.mode == "execute" else (None, None)
    task = _turn_task(capability_ids, req.mode, req.major_consequence)
    narrative_packet: dict[str, Any] | None = None
    narrative_error: dict[str, Any] | None = None
    if isinstance(result, dict) and req.mode == "execute":
        try:
            narrative_packet = engine.build_narrative_packet(
                req.campaign_id,
                turn_result=result,
                task=task,
                trigger_type=trigger,
                actor_kind=req.actor_kind,
                actor_id=req.actor_id,
                intents=intents,
                raw_player_text=req.player_text,
                choice_options=req.choice_options,
                major_consequence=req.major_consequence,
                location_id=req.location_id,
                narrative_hint=req.narrative_hint,
                mode_override=req.narrative_mode_override,
            )
        except Exception as exc:
            configured_mode = str(req.narrative_mode_override or "")
            if not configured_mode:
                try:
                    configured_mode = str(engine.get_narrative_config(req.campaign_id).get("mode") or "off")
                except Exception:
                    configured_mode = "off"
            if configured_mode == "enforce":
                # Do not reflect exception text: build failures can contain
                # private packet inputs or database details.
                raise HTTPException(status_code=500, detail="Narrative enforce mode failed closed.") from exc
            narrative_error = {
                "mode": configured_mode,
                "code": "NARRATIVE_RUNTIME_FAILED",
                "baseline_preserved": True,
            }
    return _attach_turn_directives(
        result,
        cue=cue,
        task=task,
        trigger_type=trigger,
        context={"capability_count": len(capability_ids)},
        choice_options=req.choice_options,
        major_consequence=req.major_consequence,
        narrative_packet=narrative_packet,
        narrative_error=narrative_error,
    )


@app.post("/api/world/event", operation_id="commitWorldEvent", dependencies=[Depends(require_key)])
def commit_event(req: EventRequest) -> dict[str, Any]:
    """Commit a bounded narrative/world event to the persistent chronological ledger."""
    return engine.commit_event(req.campaign_id, req.event_type, req.summary, region=req.region, actor_id=req.actor_id, target_id=req.target_id, payload=req.payload)


@app.post("/api/visual/profile", operation_id="saveVisualProfile", dependencies=[Depends(require_key)])
def save_visual_profile(req: VisualProfileRequest) -> dict[str, Any]:
    """Persist authoritative appearance/gear descriptors and request a canonical identity reference when due."""
    profile = _with_receipt("saveVisualProfile", req.campaign_id, lambda: engine.set_visual_profile(**req.model_dump()))
    cue = None
    if req.entity_kind == "character":
        cue = _safe_image_cue(req.campaign_id, trigger_type="character_reference", entity_kind="character", entity_id=req.entity_id, scene_key=f"reference:character:{req.entity_id}")
    else:
        try:
            npc = engine.get_npc(req.campaign_id, req.entity_id)
            if npc.get("importance") == "major":
                cue = _safe_image_cue(req.campaign_id, trigger_type="npc_reference", entity_kind="npc", entity_id=req.entity_id, scene_key=f"reference:npc:{req.entity_id}")
        except KeyError:
            cue = None
    return _attach_turn_directives(profile, cue=cue, task="character_creation" if req.entity_kind == "character" else "npc_introduction", trigger_type=cue.get("trigger_type") if cue else None)


@app.get("/api/visual/profile/{entity_kind}/{entity_id}", operation_id="getVisualProfile", dependencies=[Depends(require_key)])
def get_visual_profile(entity_kind: Literal["character", "npc"], entity_id: str, campaign_id: str = "default") -> dict[str, Any]:
    """Read persistent appearance descriptors used by scene image generation."""
    return engine.get_visual_profile(campaign_id, entity_kind, entity_id)


@app.post("/api/visual/state", operation_id="saveVisualState", dependencies=[Depends(require_key)])
def save_visual_state(req: VisualStateRequest) -> dict[str, Any]:
    """Persist visual continuity for a location, scene, or combat without mutating gameplay facts."""
    return engine.set_visual_state(**req.model_dump())


@app.get("/api/visual/state/{scope_type}/{scope_id}", operation_id="getVisualState", dependencies=[Depends(require_key)])
def get_visual_state(scope_type: Literal["location", "scene", "combat"], scope_id: str, campaign_id: str = "default") -> dict[str, Any]:
    """Read persisted visual continuity state."""
    return engine.get_visual_state(campaign_id, scope_type, scope_id)


@app.get("/api/visual/recent", operation_id="getRecentImageContext", dependencies=[Depends(require_key)])
def get_recent_image_context(campaign_id: str = "default", location_id: str | None = None, limit: int = Query(default=5, ge=1, le=20)) -> dict[str, Any]:
    """Read recent image/continuity context for visual consistency."""
    return engine.get_recent_image_context(campaign_id, location_id, limit)


@app.get("/api/visual/preferences", operation_id="getVisualPreferences", dependencies=[Depends(require_key)])
def get_visual_preferences(campaign_id: str = "default") -> dict[str, Any]:
    """Read campaign-level automatic image-generation preferences."""
    return engine.get_visual_preferences(campaign_id)


@app.post("/api/visual/preferences", operation_id="setVisualPreferences", dependencies=[Depends(require_key)])
def set_visual_preferences(req: VisualPreferencesRequest) -> dict[str, Any]:
    """Configure automatic image generation triggers and style guidance."""
    return engine.set_visual_preferences(**req.model_dump())


@app.post("/api/visual/cue", operation_id="buildImageCue", dependencies=[Depends(require_key)])
def build_image_cue(req: ImageCueRequest) -> dict[str, Any]:
    """Build a prompt/package for ChatGPT native image generation at scene starts, battle starts, new locations, or event choices."""
    result = _with_receipt("buildImageCue", req.campaign_id, lambda: engine.build_image_cue(**req.model_dump()))
    task = "quest_branch" if req.trigger_type == "event_choice" else ("combat" if req.trigger_type == "battle_start" else ("character_creation" if req.trigger_type in {"character_reference","npc_reference"} else "routine"))
    cue_core = {k: v for k, v in result.items() if not str(k).startswith("_")}
    return _attach_turn_directives(result, cue=cue_core, task=task, trigger_type=req.trigger_type, choice_options=req.choice_options, major_consequence=req.trigger_type == "event_choice")


@app.post("/api/visual/record", operation_id="recordImageGeneration", dependencies=[Depends(require_key)])
def record_image_generation(req: ImageRecordRequest) -> dict[str, Any]:
    """Record that an image cue has already been served so repeated scenes do not spam duplicate images."""
    return _with_receipt("recordImageGeneration", req.campaign_id, lambda: engine.record_image_generation(**req.model_dump()))


@app.post("/api/presentation", operation_id="publishPresentation", dependencies=[Depends(require_key)])
def publish_presentation(req: PublishPresentationRequest) -> dict[str, Any]:
    """Validate and durably publish exact accepted narration without mutating world state."""
    return _with_receipt(
        "publishPresentation",
        req.campaign_id,
        lambda: engine.publish_presentation(
            req.campaign_id,
            req.presentation_id,
            req.packet_id,
            req.narration,
            expected_revision=req.expected_revision,
            turn_id=req.turn_id,
            choices=req.choices,
        ),
    )


@app.get(
    "/api/presentation/latest",
    include_in_schema=False,
    dependencies=[Depends(require_key)],
)
def latest_accepted_presentation(
    campaign_id: str = Query(default="default", min_length=1, max_length=100),
) -> dict[str, Any]:
    """Trusted-backend read of the latest accepted public presentation only."""
    return engine.latest_accepted_presentation(campaign_id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=os.environ.get("WORLD_ENGINE_HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")), reload=False)
