from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence, TYPE_CHECKING

from .context.authorization import authorize_candidate, resolve_principal
from .context.scoring import fixed_point_score

if TYPE_CHECKING:
    import sqlite3
    from .engine import WorldEngine


TURN_ROUTER_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS we4_capability_manifests (
    campaign_id TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('READ','RESOLVED','SIMULATED','NARRATED','AUTHOR')),
    provider TEXT NOT NULL,
    engine_domain TEXT NOT NULL,
    version TEXT NOT NULL,
    requires_json TEXT NOT NULL DEFAULT '[]',
    writes_json TEXT NOT NULL DEFAULT '[]',
    context_tiers_json TEXT NOT NULL DEFAULT '[]',
    input_schema_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 100,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id, capability_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_we4_capability_manifests_enabled
    ON we4_capability_manifests(campaign_id, enabled, priority DESC, capability_id);

CREATE TABLE IF NOT EXISTS we4_entities (
    campaign_id TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    source_table TEXT,
    components_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id, entity_key),
    UNIQUE(campaign_id, entity_type, entity_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_we4_entities_type
    ON we4_entities(campaign_id, entity_type, status, canonical_name, entity_id);
CREATE INDEX IF NOT EXISTS idx_we4_entities_id
    ON we4_entities(campaign_id, entity_id, entity_type);

CREATE TABLE IF NOT EXISTS we4_relations (
    campaign_id TEXT NOT NULL,
    relation_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    target_key TEXT NOT NULL,
    strength REAL NOT NULL DEFAULT 1,
    directed INTEGER NOT NULL DEFAULT 1,
    valid_from TEXT,
    valid_to TEXT,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id, relation_id),
    FOREIGN KEY(campaign_id, source_key) REFERENCES we4_entities(campaign_id, entity_key) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id, target_key) REFERENCES we4_entities(campaign_id, entity_key) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_we4_relations_source
    ON we4_relations(campaign_id, source_key, relation_type, valid_to);
CREATE INDEX IF NOT EXISTS idx_we4_relations_target
    ON we4_relations(campaign_id, target_key, relation_type, valid_to);

CREATE TABLE IF NOT EXISTS we4_facts (
    campaign_id TEXT NOT NULL,
    fact_id TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object_type TEXT NOT NULL DEFAULT 'literal' CHECK(object_type IN ('literal','entity')),
    object_value_json TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1 CHECK(confidence BETWEEN 0 AND 1),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disputed','retracted')),
    source_event_id INTEGER,
    valid_from TEXT,
    valid_to TEXT,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id, fact_id),
    FOREIGN KEY(campaign_id, subject_key) REFERENCES we4_entities(campaign_id, entity_key) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_we4_facts_subject
    ON we4_facts(campaign_id, subject_key, predicate, status);

CREATE TABLE IF NOT EXISTS we4_beliefs (
    campaign_id TEXT NOT NULL,
    believer_key TEXT NOT NULL,
    fact_id TEXT NOT NULL,
    belief_value_json TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5 CHECK(confidence BETWEEN 0 AND 1),
    source_key TEXT,
    acquired_world_time TEXT NOT NULL,
    last_confirmed_world_time TEXT,
    status TEXT NOT NULL DEFAULT 'believes' CHECK(status IN ('believes','doubts','rejects','unknown')),
    provenance_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id, believer_key, fact_id),
    FOREIGN KEY(campaign_id, believer_key) REFERENCES we4_entities(campaign_id, entity_key) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id, fact_id) REFERENCES we4_facts(campaign_id, fact_id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id, source_key) REFERENCES we4_entities(campaign_id, entity_key) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_we4_beliefs_believer
    ON we4_beliefs(campaign_id, believer_key, status, confidence DESC);

CREATE TABLE IF NOT EXISTS we4_information_transfers (
    campaign_id TEXT NOT NULL,
    transfer_id TEXT NOT NULL,
    fact_id TEXT NOT NULL,
    sender_key TEXT,
    receiver_key TEXT NOT NULL,
    world_time TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'speech',
    credibility REAL NOT NULL DEFAULT 1 CHECK(credibility BETWEEN 0 AND 1),
    distortion REAL NOT NULL DEFAULT 0 CHECK(distortion BETWEEN 0 AND 1),
    parent_transfer_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id, transfer_id),
    FOREIGN KEY(campaign_id, fact_id) REFERENCES we4_facts(campaign_id, fact_id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id, sender_key) REFERENCES we4_entities(campaign_id, entity_key) ON DELETE SET NULL,
    FOREIGN KEY(campaign_id, receiver_key) REFERENCES we4_entities(campaign_id, entity_key) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id, parent_transfer_id) REFERENCES we4_information_transfers(campaign_id, transfer_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_we4_information_transfers_receiver
    ON we4_information_transfers(campaign_id, receiver_key, world_time DESC);
CREATE INDEX IF NOT EXISTS idx_we4_information_transfers_fact
    ON we4_information_transfers(campaign_id, fact_id, world_time, transfer_id);

CREATE TABLE IF NOT EXISTS we4_context_compilations (
    campaign_id TEXT NOT NULL,
    compilation_id TEXT NOT NULL,
    turn_id TEXT,
    actor_key TEXT,
    location_id TEXT,
    requested_capabilities_json TEXT NOT NULL DEFAULT '[]',
    budget_chars INTEGER NOT NULL,
    used_chars INTEGER NOT NULL,
    estimated_tokens INTEGER NOT NULL,
    included_json TEXT NOT NULL DEFAULT '[]',
    omitted_json TEXT NOT NULL DEFAULT '[]',
    digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id, compilation_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_we4_context_compilations_turn
    ON we4_context_compilations(campaign_id, turn_id, created_at DESC);

CREATE TABLE IF NOT EXISTS we4_turn_records (
    campaign_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    actor_key TEXT,
    expected_revision INTEGER,
    revision_before INTEGER,
    revision_after INTEGER,
    raw_player_text TEXT NOT NULL DEFAULT '',
    intents_json TEXT NOT NULL DEFAULT '[]',
    capability_plan_json TEXT NOT NULL DEFAULT '[]',
    context_digest TEXT,
    result_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','planned','completed','partial_failed','failed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id, turn_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_we4_turn_records_campaign
    ON we4_turn_records(campaign_id, created_at DESC, turn_id);

-- Schema 14: authorization-first epistemic claim store and compiler receipts.
CREATE TABLE IF NOT EXISTS knowledge_claims (
    campaign_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object_json TEXT NOT NULL,
    authority TEXT NOT NULL CHECK(authority IN ('WORLD_TRUTH','PLAYER_KNOWLEDGE','NPC_BELIEF','NPC_MEMORY','RUMOR','GM_SECRET')),
    principal_scope_type TEXT NOT NULL DEFAULT 'WORLD' CHECK(principal_scope_type IN ('WORLD','PLAYER','ENTITY','GM')),
    principal_kind TEXT,
    principal_id TEXT,
    valid_from TEXT,
    valid_until TEXT,
    learned_revision INTEGER NOT NULL DEFAULT 0,
    superseded_revision INTEGER,
    source_event_id INTEGER,
    confidence REAL NOT NULL DEFAULT 1 CHECK(confidence BETWEEN 0 AND 1),
    sensitivity TEXT NOT NULL DEFAULT 'NORMAL' CHECK(sensitivity IN ('NORMAL','PRIVATE','SECRET')),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disputed','retracted')),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id, claim_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_knowledge_claims_lookup
    ON knowledge_claims(campaign_id, subject_key, predicate, status, superseded_revision);
CREATE INDEX IF NOT EXISTS idx_knowledge_claims_principal
    ON knowledge_claims(campaign_id, principal_scope_type, principal_kind, principal_id, sensitivity);

CREATE TABLE IF NOT EXISTS context_compile_receipts (
    campaign_id TEXT NOT NULL,
    compile_id TEXT NOT NULL,
    compiler_version TEXT NOT NULL,
    snapshot_revision INTEGER NOT NULL,
    index_revision INTEGER NOT NULL,
    plan_hash TEXT NOT NULL,
    principal_json TEXT NOT NULL,
    requested_budget INTEGER NOT NULL,
    usable_budget INTEGER NOT NULL,
    used_chars INTEGER NOT NULL,
    compile_hash TEXT NOT NULL,
    counts_json TEXT NOT NULL,
    timing_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id, compile_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS context_compile_items (
    campaign_id TEXT NOT NULL,
    compile_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    source_revision INTEGER NOT NULL DEFAULT 0,
    authorized INTEGER NOT NULL,
    included INTEGER NOT NULL,
    tier TEXT NOT NULL,
    kind TEXT NOT NULL,
    fixed_point_score INTEGER NOT NULL DEFAULT 0,
    char_count INTEGER NOT NULL DEFAULT 0,
    exclusion_reason TEXT,
    dependencies_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(campaign_id, compile_id, candidate_id),
    FOREIGN KEY(campaign_id, compile_id) REFERENCES context_compile_receipts(campaign_id, compile_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS context_index_state (
    campaign_id TEXT PRIMARY KEY,
    campaign_revision INTEGER NOT NULL DEFAULT 0,
    fts_revision INTEGER NOT NULL DEFAULT 0,
    vector_revision INTEGER NOT NULL DEFAULT 0,
    embedding_model TEXT,
    embedding_version TEXT,
    embedding_checksum TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    campaign_id UNINDEXED, event_id UNINDEXED, revision UNINDEXED, event_type, summary, region, actor_id
);
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    campaign_id UNINDEXED, claim_id UNINDEXED, subject_key, predicate, object_text
);
CREATE VIRTUAL TABLE IF NOT EXISTS world_bible_fts USING fts5(
    campaign_id UNINDEXED, canon_version UNINDEXED, text
);
"""


CAPABILITY_MODES = {"READ", "RESOLVED", "SIMULATED", "NARRATED", "AUTHOR"}
ENTITY_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
PREDICATE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")
RELATION_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")


# These manifests reuse the existing v3.9.x kernels rather than replacing them.
DEFAULT_CAPABILITIES: tuple[dict[str, Any], ...] = (
    {
        "capability_id": "context.compile", "mode": "READ", "provider": "turn_router",
        "engine_domain": "state_event_memory", "version": "4.0.0",
        "requires": ["campaign", "intent", "actor?", "location?"], "writes": ["context_compilation"],
        "context_tiers": ["HOT", "WARM", "COLD", "ARCHIVE"], "priority": 1000,
        "metadata": {"foundation": 8, "description": "Budgeted deterministic context packet with activation inspector."},
    },
    {
        "capability_id": "entity.graph.read", "mode": "READ", "provider": "turn_router",
        "engine_domain": "we4_relationship_graph", "version": "4.0.0",
        "requires": ["entity_ref?"], "writes": [], "context_tiers": ["HOT", "WARM"], "priority": 970,
        "metadata": {"foundation": 1},
    },
    {
        "capability_id": "entity.relation.write", "mode": "RESOLVED", "provider": "turn_router",
        "engine_domain": "we4_relationship_graph", "version": "4.0.0",
        "requires": ["source", "relation_type", "target"], "writes": ["we4_relations"],
        "context_tiers": ["HOT", "WARM"], "priority": 900, "metadata": {"foundation": 1},
    },
    {
        "capability_id": "knowledge.read", "mode": "READ", "provider": "turn_router",
        "engine_domain": "agent_knowledge", "version": "4.0.0",
        "requires": ["believer?", "subject?"], "writes": [], "context_tiers": ["HOT", "WARM", "ARCHIVE"],
        "priority": 960, "metadata": {"foundation": 7},
    },
    {
        "capability_id": "knowledge.fact.assert", "mode": "RESOLVED", "provider": "turn_router",
        "engine_domain": "agent_knowledge", "version": "4.0.0",
        "requires": ["subject", "predicate", "object_value"], "writes": ["we4_facts"],
        "context_tiers": ["HOT", "WARM"], "priority": 900, "metadata": {"foundation": 7},
    },
    {
        "capability_id": "knowledge.belief.set", "mode": "RESOLVED", "provider": "turn_router",
        "engine_domain": "agent_knowledge", "version": "4.0.0",
        "requires": ["believer", "fact_id"], "writes": ["we4_beliefs"],
        "context_tiers": ["HOT", "WARM"], "priority": 900, "metadata": {"foundation": 7},
    },
    {
        "capability_id": "knowledge.transfer", "mode": "RESOLVED", "provider": "turn_router",
        "engine_domain": "agent_knowledge", "version": "4.0.0",
        "requires": ["fact_id", "receiver", "sender?"],
        "writes": ["we4_information_transfers", "we4_beliefs"],
        "context_tiers": ["HOT", "WARM", "ARCHIVE"], "priority": 890,
        "metadata": {"foundation": 7, "description": "Traceable information transfer and rumor genealogy."},
    },
    {
        "capability_id": "actor.move", "mode": "RESOLVED", "provider": "engine.move_actor",
        "engine_domain": "space_geography", "version": "4.0.0",
        "requires": ["actor", "destination"], "writes": ["actor.location", "events"],
        "context_tiers": ["HOT", "WARM"], "priority": 850, "metadata": {"foundation": 2},
    },
    {
        "capability_id": "space.route", "mode": "READ", "provider": "world_systems.find_path",
        "engine_domain": "space_geography", "version": "4.0.0",
        "requires": ["start", "goal"], "writes": [], "context_tiers": ["HOT", "WARM"],
        "priority": 850, "metadata": {"foundation": 2},
    },
    {
        "capability_id": "rules.check", "mode": "RESOLVED", "provider": "engine.resolve_check",
        "engine_domain": "rules", "version": "4.0.0",
        "requires": ["modifier", "dc"], "writes": ["rng_ledger"],
        "context_tiers": ["HOT"], "priority": 860, "metadata": {"foundation": 8},
    },
    {
        "capability_id": "rules.attack", "mode": "RESOLVED", "provider": "engine.resolve_attack",
        "engine_domain": "rules", "version": "4.0.0",
        "requires": ["attacker", "target", "attack_bonus", "damage_expression"],
        "writes": ["hp", "effects", "resources", "events"], "context_tiers": ["HOT"],
        "priority": 870, "metadata": {"foundation": 8},
    },
    {
        "capability_id": "rules.generic", "mode": "RESOLVED", "provider": "engine.rules_dispatch",
        "engine_domain": "rules", "version": "4.0.0",
        "requires": ["operation", "payload"], "writes": ["rules_state", "events"],
        "context_tiers": ["HOT", "WARM"], "priority": 840, "metadata": {"foundation": 8},
    },
    {
        "capability_id": "actor.condition", "mode": "RESOLVED", "provider": "engine.set_condition",
        "engine_domain": "rules", "version": "4.0.0",
        "requires": ["actor", "condition", "active"], "writes": ["conditions", "events"],
        "context_tiers": ["HOT"], "priority": 830, "metadata": {"foundation": 8},
    },
    {
        "capability_id": "actor.resources", "mode": "RESOLVED", "provider": "engine.update_character_resources",
        "engine_domain": "state_event_memory", "version": "4.0.0",
        "requires": ["character_id"], "writes": ["resources", "inventory", "events"],
        "context_tiers": ["HOT"], "priority": 820, "metadata": {"foundation": 8},
    },
    {
        "capability_id": "social.relationship.adjust", "mode": "RESOLVED", "provider": "engine.adjust_relationship",
        "engine_domain": "we4_relationship_graph", "version": "4.0.0",
        "requires": ["source_id", "target_id"], "writes": ["relationships", "relationship_events", "events"],
        "context_tiers": ["HOT", "WARM"], "priority": 820, "metadata": {"foundation": 1},
    },
    {
        "capability_id": "npc.state.update", "mode": "RESOLVED", "provider": "engine.update_npc_state",
        "engine_domain": "agent_planning_knowledge", "version": "4.0.0",
        "requires": ["npc_id"], "writes": ["npc_state", "events"],
        "context_tiers": ["HOT", "WARM"], "priority": 810, "metadata": {"foundation": 7},
    },
    {
        "capability_id": "npc.dialogue.context", "mode": "NARRATED", "provider": "context_compiler",
        "engine_domain": "agent_planning_knowledge", "version": "4.0.0",
        "requires": ["npc", "topic?"], "writes": [], "context_tiers": ["HOT", "WARM", "ARCHIVE"],
        "priority": 900, "metadata": {"foundation": 7, "description": "Facts for model-authored natural dialogue; no hidden chain of thought."},
    },
    {
        "capability_id": "npc.plan", "mode": "RESOLVED", "provider": "npc_life.plan",
        "engine_domain": "agent_planning_knowledge", "version": "4.0.0",
        "requires": ["start", "goal", "actions"], "writes": [], "context_tiers": ["HOT", "WARM"],
        "priority": 800, "metadata": {"foundation": 7},
    },
    {
        "capability_id": "faction.adjust", "mode": "RESOLVED", "provider": "engine.adjust_faction",
        "engine_domain": "politics_law_warfare", "version": "4.0.0",
        "requires": ["faction_id"], "writes": ["faction_state", "events"],
        "context_tiers": ["HOT", "WARM", "COLD"], "priority": 800, "metadata": {"foundation": 5},
    },
    {
        "capability_id": "quest.update", "mode": "RESOLVED", "provider": "engine.upsert_quest",
        "engine_domain": "state_event_memory", "version": "4.0.0",
        "requires": ["quest_id", "title"], "writes": ["quests", "events"],
        "context_tiers": ["HOT", "WARM"], "priority": 800, "metadata": {"foundation": 8},
    },
    {
        "capability_id": "world.state.set", "mode": "RESOLVED", "provider": "engine.set_world_state",
        "engine_domain": "state_event_memory", "version": "4.0.0",
        "requires": ["scope_type", "scope_id", "state_key", "value"],
        "writes": ["world_state", "events"], "context_tiers": ["HOT", "WARM", "COLD"],
        "priority": 790, "metadata": {"foundation": 8},
    },
    {
        "capability_id": "world.event.commit", "mode": "RESOLVED", "provider": "engine.commit_event",
        "engine_domain": "state_event_memory", "version": "4.0.0",
        "requires": ["event_type", "summary"], "writes": ["events"],
        "context_tiers": ["HOT", "WARM", "ARCHIVE"], "priority": 790, "metadata": {"foundation": 8},
    },
    {
        "capability_id": "world.advance", "mode": "SIMULATED", "provider": "engine.advance_world",
        "engine_domain": "population_economy_politics_ecology", "version": "4.0.0",
        "requires": ["minutes"], "writes": ["world_time", "simulation_state", "events"],
        "context_tiers": ["HOT", "WARM", "COLD"], "priority": 880,
        "metadata": {"foundations": [3, 4, 5, 6, 7, 8]},
    },
    {
        "capability_id": "combat.start", "mode": "RESOLVED", "provider": "engine.start_combat",
        "engine_domain": "rules", "version": "4.0.0",
        "requires": ["combat_id", "location", "participants"], "writes": ["combat", "rng_ledger", "events"],
        "context_tiers": ["HOT"], "priority": 880, "metadata": {"foundation": 8},
    },
    {
        "capability_id": "combat.next", "mode": "RESOLVED", "provider": "engine.next_turn",
        "engine_domain": "rules", "version": "4.0.0",
        "requires": ["combat_id"], "writes": ["combat", "effects", "death_saves", "events"],
        "context_tiers": ["HOT"], "priority": 880, "metadata": {"foundation": 8},
    },
    {
        "capability_id": "combat.end", "mode": "RESOLVED", "provider": "engine.end_combat",
        "engine_domain": "rules", "version": "4.0.0",
        "requires": ["combat_id"], "writes": ["combat", "scene", "events"],
        "context_tiers": ["HOT", "WARM"], "priority": 870, "metadata": {"foundation": 8},
    },
    {
        "capability_id": "progression.manage", "mode": "RESOLVED", "provider": "world_systems.progression",
        "engine_domain": "state_event_memory", "version": "4.0.0",
        "requires": ["operation", "payload"], "writes": ["progression", "rewards", "events"],
        "context_tiers": ["HOT", "WARM"], "priority": 830, "metadata": {"foundation": 8},
    },
    {
        "capability_id": "author.content", "mode": "AUTHOR", "provider": "authoring_kernel",
        "engine_domain": "state_event_memory", "version": "4.0.0",
        "requires": ["action"], "writes": ["staging", "validated_content", "canon"],
        "context_tiers": ["HOT", "WARM", "COLD", "ARCHIVE"], "priority": 700,
        "metadata": {"foundation": 8, "pipeline": ["stage", "validate", "dry_run", "promote"]},
    },
    {
        "capability_id": "narrative.manage", "mode": "AUTHOR", "provider": "narrative_kernel",
        "engine_domain": "presentation", "version": "4.3.0",
        "requires": ["operation", "payload?"],
        "writes": ["narrative_config?", "voice_profile?", "storylet?", "motif?", "quality_receipt?", "semantic_dialogue_state?"],
        "context_tiers": ["HOT", "WARM", "ARCHIVE"], "priority": 520,
        "metadata": {
            "description": "Typed narrative director, semantic dialogue plan, voice/motif compiler, render packet and deterministic quality gate.",
            "authority_boundary": "Presentation only; never resolves or overwrites mechanics/world truth.",
            "migration_modes": ["off", "shadow", "compare", "enforce"],
        },
    },
    {
        "capability_id": "visual.cue", "mode": "NARRATED", "provider": "engine.build_image_cue",
        "engine_domain": "presentation", "version": "4.0.0",
        "requires": ["trigger_type"], "writes": ["image_generation_record?"],
        "context_tiers": ["HOT", "WARM"], "priority": 500,
        "metadata": {"description": "Presentation directive only; never mechanical authority."},
    },
)


INTENT_ALIASES: dict[str, str] = {
    "observe": "context.compile",
    "look": "context.compile",
    "inspect": "context.compile",
    "read_context": "context.compile",
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
    "npc_state": "npc.state.update",
    "npc_plan": "npc.plan",
    "faction": "faction.adjust",
    "quest": "quest.update",
    "world_state": "world.state.set",
    "event": "world.event.commit",
    "advance_time": "world.advance",
    "combat_start": "combat.start",
    "combat_next": "combat.next",
    "combat_end": "combat.end",
    "progression": "progression.manage",
    "author": "author.content",
    "relation": "entity.relation.write",
    "fact": "knowledge.fact.assert",
    "belief": "knowledge.belief.set",
    "inform": "knowledge.transfer",
    "knowledge": "knowledge.read",
    "graph": "entity.graph.read",
    "image": "visual.cue",
    "narrative": "narrative.manage",
    "prose": "narrative.manage",
}


TIER_ORDER = {"HOT": 0, "WARM": 1, "COLD": 2, "ARCHIVE": 3}
MUTATING_MODES = {"RESOLVED", "SIMULATED", "AUTHOR"}
def _public_step_error(exc: Exception) -> dict[str, Any]:
    """Map failures to stable public codes without reflecting exception text."""
    if isinstance(exc, KeyError):
        code = "RESOURCE_NOT_FOUND"
    elif isinstance(exc, PermissionError):
        code = "ACTION_NOT_AUTHORIZED"
    elif isinstance(exc, TimeoutError):
        code = "ACTION_TIMEOUT"
    elif isinstance(exc, ValueError):
        code = "ACTION_REJECTED"
    elif isinstance(exc, RuntimeError):
        code = "ACTION_RUNTIME_FAILED"
    else:
        code = "ACTION_FAILED"
    return {"code": code, "retryable": isinstance(exc, (TimeoutError, ConnectionError))}




class TurnRouter:
    """Unified turn router for World Engine 4.0.

    ChatGPT normalizes natural-language intent into bounded capability requests.
    This router validates the plan, compiles only the relevant authoritative
    context, executes existing deterministic kernels, records idempotency, and
    returns a structured result packet for narration.
    """

    def __init__(self, engine: "WorldEngine"):
        self.e = engine

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, float(value)))

    @staticmethod
    def _canonical(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

    @classmethod
    def _digest(cls, value: Any) -> str:
        return hashlib.sha256(cls._canonical(value).encode("utf-8")).hexdigest()

    @classmethod
    def _stable_payload(cls, value: Any) -> Any:
        """Remove infrastructure timestamps that change during idempotent index refreshes.

        World time, event time, validity windows, revisions, and gameplay dates are
        retained. Database maintenance timestamps are not context semantics.
        """
        if isinstance(value, dict):
            return {
                str(k): cls._stable_payload(v)
                for k, v in value.items()
                if str(k) not in {"updated_at", "created_at"}
            }
        if isinstance(value, list):
            return [cls._stable_payload(v) for v in value]
        if isinstance(value, tuple):
            return [cls._stable_payload(v) for v in value]
        return value


    @staticmethod
    def _clean_label(value: str, pattern: re.Pattern[str], label: str) -> str:
        value = str(value or "").strip().lower()
        if not pattern.fullmatch(value):
            raise ValueError(f"invalid {label}: {value!r}")
        return value

    def _world_time_db(self, db: "sqlite3.Connection", campaign_id: str) -> str:
        row = db.execute("SELECT world_time FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if not row:
            raise KeyError(f"unknown campaign: {campaign_id}")
        return str(row["world_time"])

    def _campaign_revision(self, campaign_id: str) -> int:
        return int(self.e.get_campaign(campaign_id)["revision"])

    def _entity_key(self, entity_type: str, entity_id: str) -> str:
        entity_type = self._clean_label(entity_type, ENTITY_TYPE_RE, "entity_type")
        entity_id = self.e._clean_id(str(entity_id))
        return f"{entity_type}:{entity_id}"

    def _decode_entity_row(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        data["components"] = self.e._loads(data.pop("components_json"))
        return data

    def _decode_relation_row(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        data["directed"] = bool(data["directed"])
        data["provenance"] = self.e._loads(data.pop("provenance_json"))
        data["metadata"] = self.e._loads(data.pop("metadata_json"))
        return data

    def _decode_fact_row(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        data["object_value"] = self.e._loads(data.pop("object_value_json"))
        data["provenance"] = self.e._loads(data.pop("provenance_json"))
        return data

    def _decode_belief_row(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        data["belief_value"] = self.e._loads(data.pop("belief_value_json"))
        data["provenance"] = self.e._loads(data.pop("provenance_json"))
        return data

    # ------------------------------------------------------------------
    # Capability registry
    # ------------------------------------------------------------------

    def seed_defaults_db(self, db: "sqlite3.Connection", campaign_id: str) -> int:
        now = self.e._now()
        count = 0
        for manifest in DEFAULT_CAPABILITIES:
            db.execute(
                """INSERT INTO we4_capability_manifests(
                       campaign_id,capability_id,mode,provider,engine_domain,version,
                       requires_json,writes_json,context_tiers_json,input_schema_json,
                       enabled,priority,metadata_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?,?)
                   ON CONFLICT(campaign_id,capability_id) DO UPDATE SET
                       mode=excluded.mode,provider=excluded.provider,engine_domain=excluded.engine_domain,
                       version=excluded.version,requires_json=excluded.requires_json,writes_json=excluded.writes_json,
                       context_tiers_json=excluded.context_tiers_json,input_schema_json=excluded.input_schema_json,
                       priority=excluded.priority,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                (
                    campaign_id,
                    manifest["capability_id"],
                    manifest["mode"],
                    manifest["provider"],
                    manifest["engine_domain"],
                    manifest["version"],
                    self.e._dumps(manifest.get("requires", [])),
                    self.e._dumps(manifest.get("writes", [])),
                    self.e._dumps(manifest.get("context_tiers", [])),
                    self.e._dumps(manifest.get("input_schema", {})),
                    int(manifest.get("priority", 100)),
                    self.e._dumps(manifest.get("metadata", {})),
                    now,
                ),
            )
            count += 1
        return count

    def seed_defaults(self, campaign_id: str) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        with self.e._write_db() as db:
            count = self.seed_defaults_db(db, campaign_id)
        return {"campaign_id": campaign_id, "seeded": count}

    def _manifest_from_row(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        data["requires"] = self.e._loads(data.pop("requires_json"))
        data["writes"] = self.e._loads(data.pop("writes_json"))
        data["context_tiers"] = self.e._loads(data.pop("context_tiers_json"))
        data["input_schema"] = self.e._loads(data.pop("input_schema_json"))
        data["metadata"] = self.e._loads(data.pop("metadata_json"))
        data["enabled"] = bool(data["enabled"])
        return data

    def list_capabilities(self, campaign_id: str, *, enabled_only: bool = True) -> list[dict[str, Any]]:
        self.seed_defaults(campaign_id)
        sql = "SELECT * FROM we4_capability_manifests WHERE campaign_id=?"
        params: list[Any] = [campaign_id]
        if enabled_only:
            sql += " AND enabled=1"
        sql += " ORDER BY priority DESC,capability_id"
        with self.e._db() as db:
            return [self._manifest_from_row(r) for r in db.execute(sql, params).fetchall()]

    def get_capability(self, campaign_id: str, capability_id: str) -> dict[str, Any]:
        self.seed_defaults(campaign_id)
        capability_id = str(capability_id or "").strip().lower()
        with self.e._db() as db:
            row = db.execute(
                "SELECT * FROM we4_capability_manifests WHERE campaign_id=? AND capability_id=?",
                (campaign_id, capability_id),
            ).fetchone()
        if not row:
            raise KeyError(f"unknown capability: {capability_id}")
        return self._manifest_from_row(row)

    def set_capability_enabled(self, campaign_id: str, capability_id: str, enabled: bool) -> dict[str, Any]:
        capability_id = str(capability_id or "").strip().lower()
        self.seed_defaults(campaign_id)
        with self.e._write_db() as db:
            cur = db.execute(
                "UPDATE we4_capability_manifests SET enabled=?,updated_at=? WHERE campaign_id=? AND capability_id=?",
                (int(bool(enabled)), self.e._now(), campaign_id, capability_id),
            )
            if cur.rowcount != 1:
                raise KeyError(f"unknown capability: {capability_id}")
        return self.get_capability(campaign_id, capability_id)

    # ------------------------------------------------------------------
    # Universal entity + relation graph
    # ------------------------------------------------------------------

    def _upsert_entity_db(
        self,
        db: "sqlite3.Connection",
        campaign_id: str,
        entity_type: str,
        entity_id: str,
        canonical_name: str,
        *,
        status: str = "active",
        source_table: str | None = None,
        components: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = self._entity_key(entity_type, entity_id)
        now = self.e._now()
        db.execute(
            """INSERT INTO we4_entities(
                   campaign_id,entity_key,entity_type,entity_id,canonical_name,status,source_table,components_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(campaign_id,entity_key) DO UPDATE SET
                   canonical_name=excluded.canonical_name,status=excluded.status,source_table=excluded.source_table,
                   components_json=excluded.components_json,updated_at=excluded.updated_at""",
            (
                campaign_id, key, entity_type, entity_id, str(canonical_name or entity_id)[:300],
                str(status or "active")[:80], source_table,
                self.e._dumps(components or {}), now, now,
            ),
        )
        row = db.execute(
            "SELECT * FROM we4_entities WHERE campaign_id=? AND entity_key=?",
            (campaign_id, key),
        ).fetchone()
        return self._decode_entity_row(row)

    def register_entity(
        self,
        campaign_id: str,
        entity_type: str,
        entity_id: str,
        canonical_name: str,
        *,
        status: str = "active",
        source_table: str | None = None,
        components: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        with self.e._write_db() as db:
            entity = self._upsert_entity_db(
                db, campaign_id, entity_type, entity_id, canonical_name,
                status=status, source_table=source_table, components=components,
            )
            revision = self.e._next_revision(db, campaign_id)
            self.e._insert_event(
                db, campaign_id, revision, "entity_registered",
                f"Entity registered: {entity['entity_key']}",
                actor_id=entity['entity_key'], payload={"entity": entity},
            )
        entity["revision"] = revision
        return entity

    def _ensure_entity_key_db(
        self,
        db: "sqlite3.Connection",
        campaign_id: str,
        ref: str | dict[str, Any],
        *,
        create_generic: bool = False,
    ) -> str:
        if isinstance(ref, dict):
            entity_type = str(ref.get("type") or ref.get("entity_type") or "").strip().lower()
            entity_id = str(ref.get("id") or ref.get("entity_id") or "").strip()
            if not entity_type or not entity_id:
                raise ValueError("entity reference object requires type and id")
            key = self._entity_key(entity_type, entity_id)
        else:
            value = str(ref or "").strip()
            if not value:
                raise ValueError("entity reference is required")
            direct = db.execute(
                "SELECT entity_key FROM we4_entities WHERE campaign_id=? AND entity_key=?",
                (campaign_id, value),
            ).fetchone()
            if direct:
                return str(direct["entity_key"])
            matches = db.execute(
                "SELECT entity_key FROM we4_entities WHERE campaign_id=? AND entity_id=? ORDER BY entity_type",
                (campaign_id, value),
            ).fetchall()
            if len(matches) == 1:
                return str(matches[0]["entity_key"])
            if len(matches) > 1:
                raise ValueError(f"ambiguous entity id {value!r}; use type:id")
            if ":" in value:
                entity_type, entity_id = value.split(":", 1)
                key = self._entity_key(entity_type, entity_id)
            else:
                if not create_generic:
                    raise KeyError(f"unknown entity reference: {value}")
                key = self._entity_key("external", value)
        row = db.execute(
            "SELECT entity_key FROM we4_entities WHERE campaign_id=? AND entity_key=?",
            (campaign_id, key),
        ).fetchone()
        if row:
            return key
        if not create_generic:
            raise KeyError(f"unknown entity reference: {key}")
        entity_type, entity_id = key.split(":", 1)
        self._upsert_entity_db(
            db, campaign_id, entity_type, entity_id, entity_id,
            status="external", source_table=None, components={"auto_registered": True},
        )
        return key

    @staticmethod
    def _source_status(row: Any, *, default: str = "active") -> str:
        keys = set(row.keys()) if hasattr(row, "keys") else set()
        if "status" in keys:
            return str(row["status"] or default)
        if "enabled" in keys:
            return "active" if bool(row["enabled"]) else "disabled"
        return default

    def sync_existing_entities_db(self, db: "sqlite3.Connection", campaign_id: str) -> dict[str, int]:
        """Project existing typed tables into the universal entity graph.

        The original typed tables remain authoritative for their domains. This
        graph is a normalized cross-domain index and relation layer.
        """
        counts: dict[str, int] = defaultdict(int)

        source_specs: tuple[tuple[str, str, str, str, Sequence[str]], ...] = (
            ("characters", "character", "id", "name", ("level", "hp", "max_hp", "ac", "location", "status")),
            ("npcs", "npc", "id", "name", ("hp", "max_hp", "ac", "location", "faction_id", "importance", "status", "archetype_id")),
            ("factions", "faction", "id", "name", ("region", "reputation", "reserve_score", "leader_id")),
            ("locations", "location", "id", "name", ("region", "realm_id", "x", "y")),
            ("quests", "quest", "id", "title", ("status", "owner_id", "region")),
            ("item_defs", "item", "id", "name", ("base_price", "effect_dice")),
            ("scenes", "scene", "id", "id", ("location_id", "scene_type", "status", "radius_m")),
            ("combats", "combat", "id", "id", ("location", "status", "round", "turn_index")),
            ("directors", "director", "id", "name", ("director_kind", "scope_type", "scope_id", "source_kind", "source_id", "authority", "enabled")),
            ("spatial_maps", "spatial_map", "id", "name", ("scope_type", "scope_id", "min_x", "max_x", "min_y", "max_y", "min_z", "max_z")),
            ("homesteads", "homestead", "id", "id", ("owner_kind", "owner_id", "location_id")),
            ("town_services", "service", "id", "name", ("location_id", "kind", "operator_id")),
            ("encounter_templates", "encounter_template", "id", "name", ("difficulty",)),
            ("rumors", "rumor", "id", "claim", ("origin_location", "truth_confidence", "distortion")),
            ("crimes", "crime", "id", "offense", ("offender_kind", "offender_id", "jurisdiction", "severity", "evidence", "bounty", "status")),
        )

        existing_tables = {
            str(r["name"])
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        for table, entity_type, id_col, name_col, component_cols in source_specs:
            if table not in existing_tables:
                continue
            columns = {str(r["name"]) for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
            selected = [id_col, name_col, *[c for c in component_cols if c in columns]]
            # De-duplicate columns while preserving order.
            selected = list(dict.fromkeys(selected))
            rows = db.execute(
                f"SELECT {','.join(selected)} FROM {table} WHERE campaign_id=? ORDER BY {id_col}",
                (campaign_id,),
            ).fetchall()
            for row in rows:
                components = {c: row[c] for c in selected if c not in {id_col, name_col}}
                self._upsert_entity_db(
                    db, campaign_id, entity_type, str(row[id_col]), str(row[name_col]),
                    status=self._source_status(row), source_table=table, components=components,
                )
                counts[entity_type] += 1

        # String-valued realms are promoted to entities when referenced by locations.
        if "locations" in existing_tables:
            for row in db.execute(
                "SELECT DISTINCT realm_id FROM locations WHERE campaign_id=? AND realm_id IS NOT NULL AND TRIM(realm_id)<>'' ORDER BY realm_id",
                (campaign_id,),
            ).fetchall():
                realm_id = str(row["realm_id"])
                self._upsert_entity_db(db, campaign_id, "realm", realm_id, realm_id, source_table="locations", components={"inferred": True})
                counts["realm"] += 1

        self._sync_builtin_relations_db(db, campaign_id)
        return dict(counts)

    def sync_existing_entities(self, campaign_id: str) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        with self.e._write_db() as db:
            counts = self.sync_existing_entities_db(db, campaign_id)
        return {"campaign_id": campaign_id, "entity_counts": counts, "total": sum(counts.values())}

    def _relation_id(self, source_key: str, relation_type: str, target_key: str, namespace: str = "relation") -> str:
        digest = hashlib.sha256(f"{namespace}|{source_key}|{relation_type}|{target_key}".encode("utf-8")).hexdigest()[:24]
        return f"rel_{digest}"

    def _upsert_relation_db(
        self,
        db: "sqlite3.Connection",
        campaign_id: str,
        source_key: str,
        relation_type: str,
        target_key: str,
        *,
        relation_id: str | None = None,
        strength: float = 1.0,
        directed: bool = True,
        valid_from: str | None = None,
        valid_to: str | None = None,
        provenance: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        relation_type = self._clean_label(relation_type, RELATION_RE, "relation_type")
        relation_id = self.e._clean_id(relation_id) if relation_id else self._relation_id(source_key, relation_type, target_key)
        if valid_from:
            datetime.fromisoformat(valid_from)
        if valid_to:
            datetime.fromisoformat(valid_to)
        now = self.e._now()
        db.execute(
            """INSERT INTO we4_relations(
                   campaign_id,relation_id,source_key,relation_type,target_key,strength,directed,
                   valid_from,valid_to,provenance_json,metadata_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(campaign_id,relation_id) DO UPDATE SET
                   source_key=excluded.source_key,relation_type=excluded.relation_type,target_key=excluded.target_key,
                   strength=excluded.strength,directed=excluded.directed,valid_from=excluded.valid_from,
                   valid_to=excluded.valid_to,provenance_json=excluded.provenance_json,
                   metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
            (
                campaign_id, relation_id, source_key, relation_type, target_key,
                float(strength), int(bool(directed)), valid_from, valid_to,
                self.e._dumps(provenance or {}), self.e._dumps(metadata or {}), now, now,
            ),
        )
        row = db.execute(
            "SELECT * FROM we4_relations WHERE campaign_id=? AND relation_id=?",
            (campaign_id, relation_id),
        ).fetchone()
        return self._decode_relation_row(row)

    def upsert_relation(
        self,
        campaign_id: str,
        source: str | dict[str, Any],
        relation_type: str,
        target: str | dict[str, Any],
        *,
        relation_id: str | None = None,
        strength: float = 1.0,
        directed: bool = True,
        valid_from: str | None = None,
        valid_to: str | None = None,
        provenance: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        create_missing: bool = False,
    ) -> dict[str, Any]:
        self.sync_existing_entities(campaign_id)
        with self.e._write_db() as db:
            source_key = self._ensure_entity_key_db(db, campaign_id, source, create_generic=create_missing)
            target_key = self._ensure_entity_key_db(db, campaign_id, target, create_generic=create_missing)
            relation = self._upsert_relation_db(
                db, campaign_id, source_key, relation_type, target_key,
                relation_id=relation_id, strength=strength, directed=directed,
                valid_from=valid_from, valid_to=valid_to,
                provenance=provenance, metadata=metadata,
            )
            revision = self.e._next_revision(db, campaign_id)
            self.e._insert_event(
                db, campaign_id, revision, "entity_relation",
                f"{source_key} {relation['relation_type']} {target_key}",
                actor_id=source_key, target_id=target_key, payload={"relation": relation},
            )
        relation["revision"] = revision
        return relation

    def _sync_relation_if_entities_db(
        self,
        db: "sqlite3.Connection",
        campaign_id: str,
        source_ref: str | dict[str, Any],
        relation_type: str,
        target_ref: str | dict[str, Any],
        *,
        strength: float = 1.0,
        directed: bool = True,
        namespace: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        try:
            source_key = self._ensure_entity_key_db(db, campaign_id, source_ref)
            target_key = self._ensure_entity_key_db(db, campaign_id, target_ref)
        except (KeyError, ValueError):
            return False
        relation_id = self._relation_id(source_key, relation_type, target_key, namespace)
        self._upsert_relation_db(
            db, campaign_id, source_key, relation_type, target_key,
            relation_id=relation_id, strength=strength, directed=directed,
            provenance={"source": "typed_table_sync", "namespace": namespace}, metadata=metadata or {},
        )
        return True

    def _unique_actor_ref_db(self, db: "sqlite3.Connection", campaign_id: str, actor_id: str) -> dict[str, str] | None:
        rows = db.execute(
            "SELECT entity_type,entity_id FROM we4_entities WHERE campaign_id=? AND entity_id=? AND entity_type IN ('character','npc') ORDER BY entity_type",
            (campaign_id, actor_id),
        ).fetchall()
        if len(rows) != 1:
            return None
        return {"type": str(rows[0]["entity_type"]), "id": str(rows[0]["entity_id"])}

    def _sync_builtin_relations_db(self, db: "sqlite3.Connection", campaign_id: str) -> int:
        count = 0
        tables = {str(r["name"]) for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

        if "characters" in tables:
            for row in db.execute("SELECT id,location FROM characters WHERE campaign_id=? AND location<>'unknown'", (campaign_id,)).fetchall():
                count += int(self._sync_relation_if_entities_db(
                    db, campaign_id, {"type": "character", "id": row["id"]}, "located_in",
                    {"type": "location", "id": row["location"]}, namespace="character_location",
                ))
        if "npcs" in tables:
            for row in db.execute("SELECT id,location,faction_id FROM npcs WHERE campaign_id=?", (campaign_id,)).fetchall():
                if row["location"] and str(row["location"]) != "unknown":
                    count += int(self._sync_relation_if_entities_db(
                        db, campaign_id, {"type": "npc", "id": row["id"]}, "located_in",
                        {"type": "location", "id": row["location"]}, namespace="npc_location",
                    ))
                if row["faction_id"]:
                    count += int(self._sync_relation_if_entities_db(
                        db, campaign_id, {"type": "npc", "id": row["id"]}, "member_of",
                        {"type": "faction", "id": row["faction_id"]}, namespace="npc_faction",
                    ))
        if "factions" in tables:
            for row in db.execute("SELECT id,leader_id FROM factions WHERE campaign_id=? AND leader_id IS NOT NULL", (campaign_id,)).fetchall():
                actor_ref = self._unique_actor_ref_db(db, campaign_id, str(row["leader_id"]))
                if actor_ref:
                    count += int(self._sync_relation_if_entities_db(
                        db, campaign_id, actor_ref, "leads", {"type": "faction", "id": row["id"]},
                        namespace="faction_leader",
                    ))
        if "quests" in tables:
            for row in db.execute("SELECT id,owner_id FROM quests WHERE campaign_id=? AND owner_id IS NOT NULL", (campaign_id,)).fetchall():
                actor_ref = self._unique_actor_ref_db(db, campaign_id, str(row["owner_id"]))
                if actor_ref:
                    count += int(self._sync_relation_if_entities_db(
                        db, campaign_id, actor_ref, "assigned_to", {"type": "quest", "id": row["id"]},
                        namespace="quest_owner",
                    ))
        if "locations" in tables:
            for row in db.execute("SELECT id,realm_id FROM locations WHERE campaign_id=? AND realm_id IS NOT NULL", (campaign_id,)).fetchall():
                count += int(self._sync_relation_if_entities_db(
                    db, campaign_id, {"type": "location", "id": row["id"]}, "located_in",
                    {"type": "realm", "id": row["realm_id"]}, namespace="location_realm",
                ))
        if "location_links" in tables:
            for row in db.execute("SELECT from_id,to_id,travel_hours,road_quality FROM location_links WHERE campaign_id=?", (campaign_id,)).fetchall():
                count += int(self._sync_relation_if_entities_db(
                    db, campaign_id, {"type": "location", "id": row["from_id"]}, "connected_to",
                    {"type": "location", "id": row["to_id"]},
                    strength=1.0 / max(1.0, float(row["travel_hours"] or 1.0)), directed=True,
                    namespace="location_link", metadata={"travel_hours": row["travel_hours"], "road_quality": row["road_quality"]},
                ))
        if "ownership" in tables:
            for row in db.execute("SELECT asset_kind,asset_id,owner_kind,owner_id,since,metadata_json FROM ownership WHERE campaign_id=?", (campaign_id,)).fetchall():
                owner_ref = {"type": str(row["owner_kind"]), "id": str(row["owner_id"])}
                asset_type = str(row["asset_kind"] or "asset").strip().lower().replace("-", "_")
                if not ENTITY_TYPE_RE.fullmatch(asset_type):
                    asset_type = "asset"
                asset_key = self._entity_key(asset_type, str(row["asset_id"]))
                if not db.execute("SELECT 1 FROM we4_entities WHERE campaign_id=? AND entity_key=?", (campaign_id, asset_key)).fetchone():
                    self._upsert_entity_db(db, campaign_id, asset_type, str(row["asset_id"]), str(row["asset_id"]), source_table="ownership", components={"inferred": True})
                count += int(self._sync_relation_if_entities_db(
                    db, campaign_id, owner_ref, "owns", asset_key,
                    namespace="ownership", metadata={"since": row["since"], **self.e._loads(row["metadata_json"])},
                ))
        if "relationships" in tables:
            for row in db.execute("SELECT * FROM relationships WHERE campaign_id=?", (campaign_id,)).fetchall():
                source_ref = self._unique_actor_ref_db(db, campaign_id, str(row["source_id"]))
                target_ref = self._unique_actor_ref_db(db, campaign_id, str(row["target_id"]))
                if not source_ref or not target_ref:
                    continue
                strength = max(
                    abs(float(row["trust"])), abs(float(row["fear"])),
                    abs(float(row["respect"])), abs(float(row["affection"])),
                ) / 100.0
                count += int(self._sync_relation_if_entities_db(
                    db, campaign_id, source_ref, "social_state", target_ref,
                    strength=strength, directed=True, namespace="relationship",
                    metadata={
                        "trust": row["trust"], "fear": row["fear"],
                        "respect": row["respect"], "affection": row["affection"],
                    },
                ))
        if "faction_relations" in tables:
            for row in db.execute("SELECT * FROM faction_relations WHERE campaign_id=?", (campaign_id,)).fetchall():
                count += int(self._sync_relation_if_entities_db(
                    db, campaign_id, {"type": "faction", "id": row["faction_a"]}, "faction_stance",
                    {"type": "faction", "id": row["faction_b"]},
                    strength=max(abs(float(row["tension"])), abs(float(row["trust"]))) / 100.0,
                    directed=False, namespace="faction_relation",
                    metadata={"stance": row["stance"], "tension": row["tension"], "trust": row["trust"]},
                ))
        return count

    def list_entities(
        self,
        campaign_id: str,
        *,
        entity_type: str | None = None,
        status: str | None = None,
        search: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.sync_existing_entities(campaign_id)
        limit = max(1, min(int(limit), 500))
        sql = "SELECT * FROM we4_entities WHERE campaign_id=?"
        params: list[Any] = [campaign_id]
        if entity_type:
            sql += " AND entity_type=?"
            params.append(self._clean_label(entity_type, ENTITY_TYPE_RE, "entity_type"))
        if status:
            sql += " AND status=?"
            params.append(str(status))
        if search:
            sql += " AND (canonical_name LIKE ? OR entity_id LIKE ? OR entity_key LIKE ?)"
            needle = f"%{str(search).strip()}%"
            params.extend([needle, needle, needle])
        sql += " ORDER BY entity_type,canonical_name,entity_id LIMIT ?"
        params.append(limit)
        with self.e._db() as db:
            return [self._decode_entity_row(r) for r in db.execute(sql, params).fetchall()]

    def get_entity(self, campaign_id: str, ref: str | dict[str, Any]) -> dict[str, Any]:
        self.sync_existing_entities(campaign_id)
        with self.e._db() as db:
            key = self._ensure_entity_key_db(db, campaign_id, ref)
            row = db.execute("SELECT * FROM we4_entities WHERE campaign_id=? AND entity_key=?", (campaign_id, key)).fetchone()
            relations = self._relations_db(db, campaign_id, key, direction="both", limit=100)
        return {"entity": self._decode_entity_row(row), "relations": relations}

    def _relations_db(
        self,
        db: "sqlite3.Connection",
        campaign_id: str,
        entity_key: str,
        *,
        direction: str = "both",
        relation_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        direction = str(direction or "both").lower()
        if direction not in {"out", "in", "both"}:
            raise ValueError("direction must be out, in, or both")
        conditions: list[str] = []
        params: list[Any] = [campaign_id]
        if direction == "out":
            conditions.append("source_key=?")
            params.append(entity_key)
        elif direction == "in":
            conditions.append("target_key=?")
            params.append(entity_key)
        else:
            conditions.append("(source_key=? OR target_key=?)")
            params.extend([entity_key, entity_key])
        if relation_type:
            conditions.append("relation_type=?")
            params.append(self._clean_label(relation_type, RELATION_RE, "relation_type"))
        sql = "SELECT * FROM we4_relations WHERE campaign_id=? AND " + " AND ".join(conditions)
        sql += " AND (valid_to IS NULL OR valid_to>(SELECT world_time FROM campaigns WHERE id=?))"
        params.append(campaign_id)
        sql += " ORDER BY strength DESC,relation_type,relation_id LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        return [self._decode_relation_row(r) for r in db.execute(sql, params).fetchall()]

    def relations_for(
        self,
        campaign_id: str,
        ref: str | dict[str, Any],
        *,
        direction: str = "both",
        relation_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.sync_existing_entities(campaign_id)
        with self.e._db() as db:
            key = self._ensure_entity_key_db(db, campaign_id, ref)
            return self._relations_db(db, campaign_id, key, direction=direction, relation_type=relation_type, limit=limit)

    def graph_path(
        self,
        campaign_id: str,
        source: str | dict[str, Any],
        target: str | dict[str, Any],
        *,
        relation_types: Sequence[str] = (),
        max_depth: int = 6,
        max_expanded: int = 5000,
    ) -> dict[str, Any]:
        self.sync_existing_entities(campaign_id)
        max_depth = max(1, min(int(max_depth), 20))
        max_expanded = max(1, min(int(max_expanded), 100_000))
        allowed = {self._clean_label(x, RELATION_RE, "relation_type") for x in relation_types}
        with self.e._db() as db:
            source_key = self._ensure_entity_key_db(db, campaign_id, source)
            target_key = self._ensure_entity_key_db(db, campaign_id, target)
            rows = db.execute(
                "SELECT source_key,target_key,relation_id,relation_type,directed,strength FROM we4_relations WHERE campaign_id=? AND (valid_to IS NULL OR valid_to>(SELECT world_time FROM campaigns WHERE id=?)) ORDER BY relation_id",
                (campaign_id, campaign_id),
            ).fetchall()
        adjacency: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        for row in rows:
            if allowed and row["relation_type"] not in allowed:
                continue
            edge = dict(row)
            adjacency[row["source_key"]].append((row["target_key"], edge))
            if not bool(row["directed"]):
                adjacency[row["target_key"]].append((row["source_key"], edge))
        for key in adjacency:
            adjacency[key].sort(key=lambda x: (x[1]["relation_type"], -float(x[1]["strength"]), x[0], x[1]["relation_id"]))
        queue = deque([(source_key, 0)])
        parent: dict[str, tuple[str, dict[str, Any]]] = {}
        seen = {source_key}
        expanded = 0
        found = source_key == target_key
        while queue and not found and expanded < max_expanded:
            node, depth = queue.popleft()
            expanded += 1
            if depth >= max_depth:
                continue
            for neighbor, edge in adjacency.get(node, []):
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                parent[neighbor] = (node, edge)
                if neighbor == target_key:
                    found = True
                    break
                queue.append((neighbor, depth + 1))
        if not found:
            return {"found": False, "source_key": source_key, "target_key": target_key, "path": [], "expanded": expanded}
        nodes = [target_key]
        edges: list[dict[str, Any]] = []
        cur = target_key
        while cur != source_key:
            prev, edge = parent[cur]
            edges.append(edge)
            nodes.append(prev)
            cur = prev
        nodes.reverse()
        edges.reverse()
        return {
            "found": True, "source_key": source_key, "target_key": target_key,
            "nodes": nodes, "edges": edges, "depth": len(edges), "expanded": expanded,
        }

    # ------------------------------------------------------------------
    # Knowledge, beliefs, and information provenance
    # ------------------------------------------------------------------

    def _upsert_world_claim_db(self, db: "sqlite3.Connection", campaign_id: str, fact: dict[str, Any], revision: int) -> None:
        db.execute(
            """INSERT INTO knowledge_claims(campaign_id,claim_id,subject_key,predicate,object_json,authority,principal_scope_type,principal_kind,principal_id,valid_from,valid_until,learned_revision,superseded_revision,source_event_id,confidence,sensitivity,status,updated_at)
               VALUES(?,?,?,?,?,'WORLD_TRUTH','WORLD',NULL,NULL,?,?,?,?,?,?,'NORMAL',?,?)
               ON CONFLICT(campaign_id,claim_id) DO UPDATE SET object_json=excluded.object_json,valid_from=excluded.valid_from,valid_until=excluded.valid_until,learned_revision=excluded.learned_revision,source_event_id=excluded.source_event_id,confidence=excluded.confidence,status=excluded.status,updated_at=excluded.updated_at""",
            (campaign_id, f"fact:{fact['fact_id']}", fact['subject_key'], fact['predicate'], self.e._dumps(fact['object_value']), fact.get('valid_from'), fact.get('valid_to'), revision, None, fact.get('source_event_id'), float(fact.get('confidence', 1.0)), fact.get('status', 'active'), self.e._now()),
        )

    def _upsert_belief_claim_db(self, db: "sqlite3.Connection", campaign_id: str, belief: dict[str, Any], fact: Any, revision: int) -> None:
        believer_key = str(belief['believer_key'])
        kind, ident = believer_key.split(':', 1)
        authority = 'NPC_BELIEF' if kind == 'npc' else 'PLAYER_KNOWLEDGE'
        status = 'active' if belief.get('status') == 'believes' else 'disputed'
        db.execute(
            """INSERT INTO knowledge_claims(campaign_id,claim_id,subject_key,predicate,object_json,authority,principal_scope_type,principal_kind,principal_id,valid_from,valid_until,learned_revision,superseded_revision,source_event_id,confidence,sensitivity,status,updated_at)
               VALUES(?,?,?,?,?,?,'ENTITY',?,?,?,?,?,NULL,?,?,'PRIVATE',?,?)
               ON CONFLICT(campaign_id,claim_id) DO UPDATE SET object_json=excluded.object_json,authority=excluded.authority,principal_kind=excluded.principal_kind,principal_id=excluded.principal_id,valid_from=excluded.valid_from,learned_revision=excluded.learned_revision,confidence=excluded.confidence,status=excluded.status,updated_at=excluded.updated_at""",
            (campaign_id, f"belief:{believer_key}:{belief['fact_id']}", fact['subject_key'], fact['predicate'], self.e._dumps(belief['belief_value']), authority, kind, ident, belief.get('acquired_world_time'), None, revision, fact['source_event_id'], float(belief.get('confidence', 0.5)), status, self.e._now()),
        )

    def assert_fact(
        self,
        campaign_id: str,
        subject: str | dict[str, Any],
        predicate: str,
        object_value: Any,
        *,
        object_type: str = "literal",
        fact_id: str | None = None,
        confidence: float = 1.0,
        status: str = "active",
        source_event_id: int | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.sync_existing_entities(campaign_id)
        predicate = self._clean_label(predicate, PREDICATE_RE, "predicate")
        object_type = str(object_type or "literal").lower()
        if object_type not in {"literal", "entity"}:
            raise ValueError("object_type must be literal or entity")
        if status not in {"active", "disputed", "retracted"}:
            raise ValueError("fact status must be active, disputed, or retracted")
        if valid_from:
            datetime.fromisoformat(valid_from)
        if valid_to:
            datetime.fromisoformat(valid_to)
        with self.e._write_db() as db:
            subject_key = self._ensure_entity_key_db(db, campaign_id, subject)
            normalized_object = object_value
            if object_type == "entity":
                normalized_object = {"entity_key": self._ensure_entity_key_db(db, campaign_id, object_value)}
            if fact_id:
                fact_id = self.e._clean_id(fact_id)
            else:
                digest = self._digest([subject_key, predicate, object_type, normalized_object])[:24]
                fact_id = f"fact_{digest}"
            now = self.e._now()
            db.execute(
                """INSERT INTO we4_facts(
                       campaign_id,fact_id,subject_key,predicate,object_type,object_value_json,
                       confidence,status,source_event_id,valid_from,valid_to,provenance_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,fact_id) DO UPDATE SET
                       subject_key=excluded.subject_key,predicate=excluded.predicate,object_type=excluded.object_type,
                       object_value_json=excluded.object_value_json,confidence=excluded.confidence,status=excluded.status,
                       source_event_id=excluded.source_event_id,valid_from=excluded.valid_from,valid_to=excluded.valid_to,
                       provenance_json=excluded.provenance_json,updated_at=excluded.updated_at""",
                (
                    campaign_id, fact_id, subject_key, predicate, object_type,
                    self.e._dumps(normalized_object), self._clamp(confidence, 0, 1), status,
                    source_event_id, valid_from, valid_to, self.e._dumps(provenance or {}), now, now,
                ),
            )
            row = db.execute("SELECT * FROM we4_facts WHERE campaign_id=? AND fact_id=?", (campaign_id, fact_id)).fetchone()
            fact = self._decode_fact_row(row)
            revision = self.e._next_revision(db, campaign_id)
            self.e._insert_event(
                db, campaign_id, revision, "canonical_fact",
                f"Fact asserted: {subject_key} {predicate}",
                actor_id=subject_key, payload={"fact": fact},
            )
            self._upsert_world_claim_db(db, campaign_id, fact, revision)
        fact["revision"] = revision
        return fact

    def retract_fact(self, campaign_id: str, fact_id: str, *, provenance: dict[str, Any] | None = None) -> dict[str, Any]:
        fact_id = self.e._clean_id(fact_id)
        with self.e._write_db() as db:
            row = db.execute("SELECT * FROM we4_facts WHERE campaign_id=? AND fact_id=?", (campaign_id, fact_id)).fetchone()
            if not row:
                raise KeyError(f"unknown fact: {fact_id}")
            current_provenance = self.e._loads(row["provenance_json"])
            current_provenance["retraction"] = provenance or {}
            db.execute(
                "UPDATE we4_facts SET status='retracted',provenance_json=?,updated_at=? WHERE campaign_id=? AND fact_id=?",
                (self.e._dumps(current_provenance), self.e._now(), campaign_id, fact_id),
            )
            row = db.execute("SELECT * FROM we4_facts WHERE campaign_id=? AND fact_id=?", (campaign_id, fact_id)).fetchone()
            fact = self._decode_fact_row(row)
            revision = self.e._next_revision(db, campaign_id)
            self.e._insert_event(
                db, campaign_id, revision, "canonical_fact_retracted",
                f"Fact retracted: {fact_id}", actor_id=fact["subject_key"],
                payload={"fact": fact},
            )
            db.execute("UPDATE knowledge_claims SET status='retracted',superseded_revision=?,updated_at=? WHERE campaign_id=? AND claim_id=?", (revision, self.e._now(), campaign_id, f"fact:{fact_id}"))
        fact["revision"] = revision
        return fact

    def set_belief(
        self,
        campaign_id: str,
        believer: str | dict[str, Any],
        fact_id: str,
        *,
        belief_value: Any | None = None,
        confidence: float = 0.5,
        source: str | dict[str, Any] | None = None,
        acquired_world_time: str | None = None,
        last_confirmed_world_time: str | None = None,
        status: str = "believes",
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.sync_existing_entities(campaign_id)
        if status not in {"believes", "doubts", "rejects", "unknown"}:
            raise ValueError("invalid belief status")
        fact_id = self.e._clean_id(fact_id)
        with self.e._write_db() as db:
            believer_key = self._ensure_entity_key_db(db, campaign_id, believer)
            fact = db.execute("SELECT * FROM we4_facts WHERE campaign_id=? AND fact_id=?", (campaign_id, fact_id)).fetchone()
            if not fact:
                raise KeyError(f"unknown fact: {fact_id}")
            source_key = self._ensure_entity_key_db(db, campaign_id, source) if source is not None else None
            world_time = acquired_world_time or self._world_time_db(db, campaign_id)
            datetime.fromisoformat(world_time)
            if last_confirmed_world_time:
                datetime.fromisoformat(last_confirmed_world_time)
            value = self.e._loads(fact["object_value_json"]) if belief_value is None else belief_value
            db.execute(
                """INSERT INTO we4_beliefs(
                       campaign_id,believer_key,fact_id,belief_value_json,confidence,source_key,
                       acquired_world_time,last_confirmed_world_time,status,provenance_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,believer_key,fact_id) DO UPDATE SET
                       belief_value_json=excluded.belief_value_json,confidence=excluded.confidence,
                       source_key=excluded.source_key,acquired_world_time=excluded.acquired_world_time,
                       last_confirmed_world_time=excluded.last_confirmed_world_time,status=excluded.status,
                       provenance_json=excluded.provenance_json,updated_at=excluded.updated_at""",
                (
                    campaign_id, believer_key, fact_id, self.e._dumps(value),
                    self._clamp(confidence, 0, 1), source_key, world_time,
                    last_confirmed_world_time, status, self.e._dumps(provenance or {}), self.e._now(),
                ),
            )
            row = db.execute(
                "SELECT * FROM we4_beliefs WHERE campaign_id=? AND believer_key=? AND fact_id=?",
                (campaign_id, believer_key, fact_id),
            ).fetchone()
            belief = self._decode_belief_row(row)
            revision = self.e._next_revision(db, campaign_id)
            self.e._insert_event(
                db, campaign_id, revision, "belief_updated",
                f"Belief updated: {believer_key} / {fact_id}",
                actor_id=believer_key, payload={"belief": belief},
            )
            self._upsert_belief_claim_db(db, campaign_id, belief, fact, revision)
        belief["revision"] = revision
        return belief

    def transfer_information(
        self,
        campaign_id: str,
        fact_id: str,
        receiver: str | dict[str, Any],
        *,
        sender: str | dict[str, Any] | None = None,
        transfer_id: str | None = None,
        channel: str = "speech",
        credibility: float = 1.0,
        distortion: float = 0.0,
        parent_transfer_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.sync_existing_entities(campaign_id)
        fact_id = self.e._clean_id(fact_id)
        with self.e._write_db() as db:
            fact = db.execute("SELECT * FROM we4_facts WHERE campaign_id=? AND fact_id=?", (campaign_id, fact_id)).fetchone()
            if not fact:
                raise KeyError(f"unknown fact: {fact_id}")
            receiver_key = self._ensure_entity_key_db(db, campaign_id, receiver)
            sender_key = self._ensure_entity_key_db(db, campaign_id, sender) if sender is not None else None
            if parent_transfer_id:
                parent_transfer_id = self.e._clean_id(parent_transfer_id)
                if not db.execute(
                    "SELECT 1 FROM we4_information_transfers WHERE campaign_id=? AND transfer_id=?",
                    (campaign_id, parent_transfer_id),
                ).fetchone():
                    raise KeyError(f"unknown parent transfer: {parent_transfer_id}")
            world_time = self._world_time_db(db, campaign_id)
            if transfer_id:
                transfer_id = self.e._clean_id(transfer_id)
            else:
                digest = self._digest([fact_id, sender_key, receiver_key, world_time, channel, parent_transfer_id, payload or {}])[:24]
                transfer_id = f"info_{digest}"
            base_confidence = float(fact["confidence"])
            transmitted_value = self.e._loads(fact["object_value_json"])
            if sender_key:
                sender_belief = db.execute(
                    "SELECT confidence,belief_value_json FROM we4_beliefs WHERE campaign_id=? AND believer_key=? AND fact_id=?",
                    (campaign_id, sender_key, fact_id),
                ).fetchone()
                if sender_belief:
                    base_confidence = float(sender_belief["confidence"])
                    transmitted_value = self.e._loads(sender_belief["belief_value_json"])
            credibility = self._clamp(credibility, 0, 1)
            distortion = self._clamp(distortion, 0, 1)
            receiver_confidence = self._clamp(base_confidence * credibility * (1.0 - distortion), 0, 1)
            transfer_payload = dict(payload or {})
            belief_value = transfer_payload.get("belief_value", transmitted_value)
            now = self.e._now()
            db.execute(
                """INSERT INTO we4_information_transfers(
                       campaign_id,transfer_id,fact_id,sender_key,receiver_key,world_time,channel,
                       credibility,distortion,parent_transfer_id,payload_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,transfer_id) DO NOTHING""",
                (
                    campaign_id, transfer_id, fact_id, sender_key, receiver_key, world_time,
                    str(channel or "speech")[:80], credibility, distortion, parent_transfer_id,
                    self.e._dumps(transfer_payload), now,
                ),
            )
            db.execute(
                """INSERT INTO we4_beliefs(
                       campaign_id,believer_key,fact_id,belief_value_json,confidence,source_key,
                       acquired_world_time,last_confirmed_world_time,status,provenance_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,NULL,'believes',?,?)
                   ON CONFLICT(campaign_id,believer_key,fact_id) DO UPDATE SET
                       belief_value_json=excluded.belief_value_json,confidence=MAX(we4_beliefs.confidence,excluded.confidence),
                       source_key=excluded.source_key,acquired_world_time=excluded.acquired_world_time,
                       status='believes',provenance_json=excluded.provenance_json,updated_at=excluded.updated_at""",
                (
                    campaign_id, receiver_key, fact_id, self.e._dumps(belief_value), receiver_confidence,
                    sender_key, world_time,
                    self.e._dumps({"transfer_id": transfer_id, "channel": channel, "distortion": distortion}), now,
                ),
            )
            transfer = db.execute(
                "SELECT * FROM we4_information_transfers WHERE campaign_id=? AND transfer_id=?",
                (campaign_id, transfer_id),
            ).fetchone()
            belief = db.execute(
                "SELECT * FROM we4_beliefs WHERE campaign_id=? AND believer_key=? AND fact_id=?",
                (campaign_id, receiver_key, fact_id),
            ).fetchone()
            t = dict(transfer)
            t["payload"] = self.e._loads(t.pop("payload_json"))
            decoded_belief = self._decode_belief_row(belief)
            revision = self.e._next_revision(db, campaign_id)
            self.e._insert_event(
                db, campaign_id, revision, "information_transfer",
                f"Information transferred: {fact_id} -> {receiver_key}",
                actor_id=sender_key, target_id=receiver_key,
                payload={"transfer": t, "receiver_belief": decoded_belief},
            )
        return {"transfer": t, "receiver_belief": decoded_belief, "revision": revision}

    def knowledge_snapshot(
        self,
        campaign_id: str,
        *,
        believer: str | dict[str, Any] | None = None,
        fact_view: str = "canonical",
        subject: str | dict[str, Any] | None = None,
        fact_id: str | None = None,
        include_retracted: bool = False,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return canonical knowledge or an explicitly believer-scoped view.

        ``fact_view="canonical"`` preserves the pre-v4.3 contract: ``facts``
        contains canonical fact rows even when ``believer`` is supplied to
        select beliefs and transfers.  ``fact_view="believer"`` is the safe
        rendering/context view.  Its ``facts`` projection contains only facts
        for which that believer has a belief row, and substitutes the belief
        value/confidence/status/provenance for canonical truth.
        """
        self.sync_existing_entities(campaign_id)
        fact_view = str(fact_view or "canonical").strip().lower()
        if fact_view not in {"canonical", "believer"}:
            raise ValueError("fact_view must be canonical or believer")
        if fact_view == "believer" and believer is None:
            raise ValueError("believer is required when fact_view=believer")
        limit = max(1, min(int(limit), 500))
        with self.e._db() as db:
            believer_key = self._ensure_entity_key_db(db, campaign_id, believer) if believer is not None else None
            subject_key = self._ensure_entity_key_db(db, campaign_id, subject) if subject is not None else None
            fact_conditions = ["campaign_id=?"]
            fact_params: list[Any] = [campaign_id]
            if subject_key:
                fact_conditions.append("subject_key=?")
                fact_params.append(subject_key)
            if fact_id:
                fact_conditions.append("fact_id=?")
                fact_params.append(self.e._clean_id(fact_id))
            if fact_view == "believer" and believer_key:
                fact_conditions.append(
                    "fact_id IN (SELECT fact_id FROM we4_beliefs WHERE campaign_id=? AND believer_key=?)"
                )
                fact_params.extend([campaign_id, believer_key])
            if not include_retracted:
                fact_conditions.append("status<>'retracted'")
            fact_params.append(limit)
            facts = [
                self._decode_fact_row(r)
                for r in db.execute(
                    "SELECT * FROM we4_facts WHERE " + " AND ".join(fact_conditions) +
                    " ORDER BY updated_at DESC,fact_id LIMIT ?",
                    fact_params,
                ).fetchall()
            ]
            beliefs: list[dict[str, Any]] = []
            if believer_key:
                sql = "SELECT * FROM we4_beliefs WHERE campaign_id=? AND believer_key=?"
                params: list[Any] = [campaign_id, believer_key]
                if fact_id:
                    sql += " AND fact_id=?"
                    params.append(self.e._clean_id(fact_id))
                if subject_key:
                    sql += " AND fact_id IN (SELECT fact_id FROM we4_facts WHERE campaign_id=? AND subject_key=?)"
                    params.extend([campaign_id, subject_key])
                sql += " ORDER BY confidence DESC,updated_at DESC,fact_id LIMIT ?"
                params.append(limit)
                beliefs = [self._decode_belief_row(r) for r in db.execute(sql, params).fetchall()]
            if fact_view == "believer":
                canonical_by_id = {item["fact_id"]: item for item in facts}
                epistemic_facts: list[dict[str, Any]] = []
                for belief in beliefs:
                    canonical = canonical_by_id.get(belief["fact_id"])
                    if canonical is None:
                        continue
                    epistemic_facts.append({
                        "campaign_id": campaign_id,
                        "fact_id": belief["fact_id"],
                        "subject_key": canonical["subject_key"],
                        "predicate": canonical["predicate"],
                        "object_type": canonical["object_type"],
                        "object_value": belief["belief_value"],
                        "confidence": belief["confidence"],
                        "status": belief["status"],
                        "provenance": belief["provenance"],
                        "believer_key": believer_key,
                        "epistemic_authority": "BELIEF",
                    })
                facts = epistemic_facts
            transfers: list[dict[str, Any]] = []
            if believer_key or fact_id:
                sql = "SELECT * FROM we4_information_transfers WHERE campaign_id=?"
                params = [campaign_id]
                if believer_key:
                    sql += " AND receiver_key=?"
                    params.append(believer_key)
                if fact_id:
                    sql += " AND fact_id=?"
                    params.append(self.e._clean_id(fact_id))
                sql += " ORDER BY world_time DESC,transfer_id DESC LIMIT ?"
                params.append(limit)
                for row in db.execute(sql, params).fetchall():
                    data = dict(row)
                    data["payload"] = self.e._loads(data.pop("payload_json"))
                    transfers.append(data)
        return {
            "campaign_id": campaign_id,
            "believer_key": believer_key,
            "fact_view": fact_view,
            "subject_key": subject_key,
            "facts": facts,
            "beliefs": beliefs,
            "transfers": transfers,
        }

    # ------------------------------------------------------------------
    # Intent normalization and capability planning
    # ------------------------------------------------------------------

    def _normalize_intents(self, campaign_id: str, intents: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(intents) > 20:
            raise ValueError("a turn may contain at most 20 intents")
        available = {m["capability_id"]: m for m in self.list_capabilities(campaign_id, enabled_only=True)}
        normalized: list[dict[str, Any]] = []
        ids: set[str] = set()
        for index, raw in enumerate(intents):
            if not isinstance(raw, dict):
                raise ValueError(f"intent {index+1} must be an object")
            intent_id = str(raw.get("intent_id") or f"intent_{index+1}").strip()
            intent_id = self.e._clean_id(intent_id)
            if intent_id in ids:
                raise ValueError(f"duplicate intent_id: {intent_id}")
            ids.add(intent_id)
            capability = str(raw.get("capability") or "").strip().lower()
            intent_type = str(raw.get("type") or raw.get("intent_type") or "").strip().lower()
            if not capability:
                capability = INTENT_ALIASES.get(intent_type, intent_type)
            if capability not in available:
                raise ValueError(f"intent {intent_id} routes to unavailable capability: {capability}")
            params = raw.get("parameters", raw.get("params", {}))
            if not isinstance(params, dict):
                raise ValueError(f"intent {intent_id} parameters must be an object")
            depends_on = raw.get("depends_on", [])
            if not isinstance(depends_on, list) or not all(isinstance(x, str) for x in depends_on):
                raise ValueError(f"intent {intent_id} depends_on must be a list of intent IDs")
            normalized.append({
                "intent_id": intent_id,
                "intent_type": intent_type or capability,
                "capability_id": capability,
                "parameters": dict(params),
                "depends_on": [self.e._clean_id(x) for x in depends_on],
                "optional": bool(raw.get("optional", False)),
                "manifest": available[capability],
            })
        known = {x["intent_id"] for x in normalized}
        for intent in normalized:
            missing = [x for x in intent["depends_on"] if x not in known]
            if missing:
                raise ValueError(f"intent {intent['intent_id']} has unknown dependencies: {missing}")
        return self._topological_order(normalized)

    @staticmethod
    def _topological_order(intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id = {x["intent_id"]: x for x in intents}
        indegree = {x["intent_id"]: 0 for x in intents}
        children: dict[str, list[str]] = defaultdict(list)
        for intent in intents:
            for dep in intent["depends_on"]:
                indegree[intent["intent_id"]] += 1
                children[dep].append(intent["intent_id"])
        order_index = {x["intent_id"]: i for i, x in enumerate(intents)}
        ready = sorted([k for k, v in indegree.items() if v == 0], key=lambda x: order_index[x])
        result: list[dict[str, Any]] = []
        while ready:
            current = ready.pop(0)
            result.append(by_id[current])
            for child in sorted(children.get(current, []), key=lambda x: order_index[x]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
                    ready.sort(key=lambda x: order_index[x])
        if len(result) != len(intents):
            raise ValueError("intent dependency graph contains a cycle")
        return result

    def capability_plan(self, campaign_id: str, intents: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = self._normalize_intents(campaign_id, intents)
        plan = []
        for order, intent in enumerate(normalized, start=1):
            manifest = intent["manifest"]
            plan.append({
                "order": order,
                "intent_id": intent["intent_id"],
                "intent_type": intent["intent_type"],
                "capability_id": intent["capability_id"],
                "mode": manifest["mode"],
                "provider": manifest["provider"],
                "engine_domain": manifest["engine_domain"],
                "requires": manifest["requires"],
                "writes": manifest["writes"],
                "context_tiers": manifest["context_tiers"],
                "depends_on": intent["depends_on"],
                "optional": intent["optional"],
                "parameters": intent["parameters"],
            })
        return plan

    # ------------------------------------------------------------------
    # Bounded context compiler + activation inspector
    # ------------------------------------------------------------------

    @staticmethod
    def _shrink_payload(payload: Any, max_chars: int) -> Any:
        if max_chars <= 80:
            return {"truncated": True}
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        if len(text) <= max_chars:
            return payload
        # Keep structural information and a deterministic bounded preview.
        if isinstance(payload, dict):
            result: dict[str, Any] = {"_truncated": True}
            for key in sorted(payload):
                value = payload[key]
                candidate = dict(result)
                candidate[key] = value
                if len(json.dumps(candidate, ensure_ascii=False, default=str)) <= max_chars:
                    result[key] = value
                else:
                    preview = str(value)
                    allowance = max(20, max_chars - len(json.dumps(result, ensure_ascii=False, default=str)) - len(str(key)) - 30)
                    result[key] = preview[:allowance]
                    break
            return result
        if isinstance(payload, list):
            result = []
            for value in payload:
                candidate = [*result, value]
                if len(json.dumps(candidate, ensure_ascii=False, default=str)) <= max_chars - 30:
                    result.append(value)
                else:
                    break
            return [*result, {"_truncated": True, "omitted": max(0, len(payload) - len(result))}]
        return {"value_preview": str(payload)[: max(20, max_chars - 40)], "_truncated": True}

    def _actor_ref_from_request(self, actor_kind: str | None, actor_id: str | None) -> dict[str, str] | None:
        if not actor_kind or not actor_id:
            return None
        return {"type": str(actor_kind), "id": str(actor_id)}

    def _target_refs_from_intents(self, intents: Sequence[dict[str, Any]]) -> list[str | dict[str, Any]]:
        refs: list[str | dict[str, Any]] = []
        for intent in intents:
            p = intent.get("parameters") or {}
            for key in ("entity", "subject", "believer", "receiver", "sender", "source", "target"):
                value = p.get(key)
                if isinstance(value, (str, dict)) and value:
                    refs.append(value)
            if p.get("target_kind") and p.get("target_id"):
                refs.append({"type": p["target_kind"], "id": p["target_id"]})
            if p.get("npc_id"):
                refs.append({"type": "npc", "id": p["npc_id"]})
            if p.get("faction_id"):
                refs.append({"type": "faction", "id": p["faction_id"]})
            if p.get("quest_id"):
                refs.append({"type": "quest", "id": p["quest_id"]})
            if p.get("location_id"):
                refs.append({"type": "location", "id": p["location_id"]})
            if p.get("location"):
                refs.append({"type": "location", "id": p["location"]})
        return refs

    def _resolve_location(
        self,
        campaign_id: str,
        actor_kind: str | None,
        actor_id: str | None,
        explicit_location: str | None,
    ) -> str | None:
        if explicit_location:
            return str(explicit_location)
        if actor_kind in {"character", "npc"} and actor_id:
            try:
                actor = self.e.get_actor(campaign_id, actor_kind, actor_id)
                value = actor.get("location")
                if value and value != "unknown":
                    return str(value)
            except (KeyError, ValueError):
                return None
        return None

    @staticmethod
    def _public_npc_payload(npc: dict[str, Any]) -> dict[str, Any]:
        private = {"beliefs", "goals", "routine", "memory", "cognition"}
        return {k: v for k, v in npc.items() if k not in private}

    @staticmethod
    def _public_faction_payload(faction: dict[str, Any]) -> dict[str, Any]:
        private = {"goals", "state"}
        return {k: v for k, v in faction.items() if k not in private}

    def _sync_safe_knowledge_fts(self, db: "sqlite3.Connection", campaign_id: str, snapshot_revision: int) -> int:
        """Index only public/world, non-secret knowledge claims.

        Private entity beliefs and GM secrets never enter the searchable corpus.
        The operation is deterministic and version-pinned by context_index_state.
        """
        row = db.execute("SELECT fts_revision FROM context_index_state WHERE campaign_id=?", (campaign_id,)).fetchone()
        current = int(row["fts_revision"]) if row else -1
        if current != snapshot_revision:
            db.execute("DELETE FROM knowledge_fts WHERE campaign_id=?", (campaign_id,))
            rows = db.execute(
                """SELECT claim_id,subject_key,predicate,object_json FROM knowledge_claims
                   WHERE campaign_id=? AND status='active' AND superseded_revision IS NULL
                     AND sensitivity='NORMAL' AND principal_scope_type='WORLD'
                   ORDER BY claim_id""",
                (campaign_id,),
            ).fetchall()
            for claim in rows:
                db.execute(
                    "INSERT INTO knowledge_fts(campaign_id,claim_id,subject_key,predicate,object_text) VALUES(?,?,?,?,?)",
                    (campaign_id, claim["claim_id"], claim["subject_key"], claim["predicate"], claim["object_json"]),
                )
            db.execute(
                """INSERT INTO context_index_state(campaign_id,campaign_revision,fts_revision,vector_revision,updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(campaign_id) DO UPDATE SET
                     campaign_revision=excluded.campaign_revision,fts_revision=excluded.fts_revision,updated_at=excluded.updated_at""",
                (campaign_id, snapshot_revision, snapshot_revision, 0, self.e._now()),
            )
        return snapshot_revision

    def _fts_claim_candidates(self, db: "sqlite3.Connection", campaign_id: str, search_text: str, limit: int = 20) -> list[dict[str, Any]]:
        tokens = re.findall(r"[A-Za-z0-9_]{2,}", str(search_text or "").lower())[:8]
        if not tokens:
            return []
        query = " OR ".join(tokens)
        try:
            ids = [r["claim_id"] for r in db.execute(
                "SELECT claim_id FROM knowledge_fts WHERE campaign_id=? AND knowledge_fts MATCH ? ORDER BY bm25(knowledge_fts),claim_id LIMIT ?",
                (campaign_id, query, max(1, min(int(limit), 100))),
            ).fetchall()]
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for claim_id in ids:
            row = db.execute(
                "SELECT * FROM knowledge_claims WHERE campaign_id=? AND claim_id=?",
                (campaign_id, claim_id),
            ).fetchone()
            if row:
                d = dict(row)
                d["object"] = self.e._loads(d.pop("object_json"))
                out.append(d)
        return out

    def compile_context(
        self,
        campaign_id: str,
        *,
        actor_kind: str | None = None,
        actor_id: str | None = None,
        viewer_kind: str | None = None,
        viewer_id: str | None = None,
        location_id: str | None = None,
        intents: Sequence[dict[str, Any]] = (),
        capability_ids: Sequence[str] = (),
        max_chars: int = 14_000,
        include_archive: bool = False,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        """Compile a deterministic, authorization-first context packet.

        HOT mandatory state is exact and never summarized. If that projection
        cannot fit, compilation fails with CONTEXT_BUDGET_UNSAT. Unauthorized
        candidates are rejected before ranking and their payloads are not stored
        in receipts.
        """
        started = time.perf_counter()
        self.e._ensure_campaign_exists(campaign_id)
        max_chars = max(2_000, min(int(max_chars), 60_000))
        principal = resolve_principal(viewer_kind, viewer_id, actor_kind=actor_kind, actor_id=actor_id)
        privileged_view = principal.kind in {"gm", "system"}

        # Project typed entities before taking the revision-pinned read snapshot.
        self.sync_existing_entities(campaign_id)
        normalized_intents = self._normalize_intents(campaign_id, intents) if intents else []
        requested_caps = list(dict.fromkeys([*(capability_ids or []), *[x["capability_id"] for x in normalized_intents]]))
        location_id = self._resolve_location(campaign_id, actor_kind, actor_id, location_id)
        actor_ref = self._actor_ref_from_request(actor_kind, actor_id)
        plan_hash = self._digest({
            "intents": [
                {k: intent[k] for k in ("intent_id", "intent_type", "capability_id", "parameters", "depends_on", "optional")}
                for intent in normalized_intents
            ],
            "capabilities": requested_caps,
            "location_id": location_id,
        })

        candidates: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(
            tier: str, kind: str, item_id: str, priority: int, payload: Any, reason: str, *,
            mandatory: bool = False, authority: str = "CANONICAL_STATE", sensitivity: str = "NORMAL",
            scope_type: str = "PUBLIC", scope_kind: str | None = None, scope_id: str | None = None,
            dependencies: Sequence[str] = (), source: str = "world_engine", source_revision: int | None = None,
        ) -> None:
            tier = tier.upper()
            if tier not in TIER_ORDER or item_id in seen or payload in (None, {}, []):
                return
            if tier == "ARCHIVE" and not include_archive:
                return
            seen.add(item_id)
            candidates.append({
                "tier": tier, "kind": kind, "item_id": item_id, "candidate_id": item_id,
                "priority": int(priority), "payload": self._stable_payload(payload),
                "activation_reason": reason, "mandatory": bool(mandatory), "authority": authority,
                "sensitivity": sensitivity.upper(), "scope_type": scope_type.upper(),
                "scope_kind": scope_kind, "scope_id": scope_id, "dependencies": list(dependencies),
                "source": source, "source_revision": int(source_revision if source_revision is not None else snapshot_revision),
            })

        with self.e._turn_lock:
            with self.e._db() as snapshot_db:
                crow = snapshot_db.execute("SELECT revision FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
                if not crow:
                    raise KeyError(f"unknown campaign: {campaign_id}")
                snapshot_revision = int(crow["revision"])
                # Zero means no shared search index participated in this
                # compilation. Until knowledge search has a principal-scoped
                # index, only privileged inspectors may use the WORLD corpus.
                index_revision = 0

            campaign = self.e.get_campaign(campaign_id)
            add("HOT", "campaign", f"campaign:{campaign_id}", 1000, campaign, "authoritative campaign clock/revision", mandatory=True, scope_type="WORLD")

            if requested_caps:
                manifests = []
                for cap in requested_caps:
                    m = self.get_capability(campaign_id, cap)
                    manifests.append({k: m[k] for k in ("capability_id", "mode", "provider", "engine_domain", "version", "requires", "writes", "context_tiers")})
                add("HOT", "capability_plan", "capabilities:requested", 995, manifests, "activated capability contracts", mandatory=True)
            if {"narrative.manage", "npc.dialogue.context"}.intersection(requested_caps):
                add(
                    "HOT", "narrative_config", "narrative:config", 992,
                    self.e.get_narrative_config(campaign_id),
                    "narrative rendering, agency and quality contract requested",
                    scope_type="WORLD",
                )
                add(
                    "WARM", "narrative_director_state", "narrative:director_state", 808,
                    self.e.narrative_dispatch("get_director_state", campaign_id, {}),
                    "recent accepted beat/tension history",
                    scope_type="WORLD",
                )

            if actor_ref:
                try:
                    actor_sheet = self.e.get_character_sheet(campaign_id, actor_id) if actor_kind == "character" else self.e.get_npc_sheet(campaign_id, actor_id)
                    if actor_kind == "npc" and principal.kind not in {"gm", "system", "npc"}:
                        actor_sheet = self._public_npc_payload(actor_sheet)
                    add("HOT", "actor", f"actor:{actor_kind}:{actor_id}", 990, actor_sheet, "acting entity exact state", mandatory=True,
                        sensitivity="PRIVATE", scope_type="ENTITY", scope_kind=actor_kind, scope_id=actor_id)
                except (KeyError, ValueError):
                    pass

            target_refs = self._target_refs_from_intents(normalized_intents)
            if "npc.dialogue.context" in requested_caps:
                for intent in normalized_intents:
                    params = intent.get("parameters") or {}
                    npc_id = params.get("npc_id") or params.get("target_id")
                    if not npc_id:
                        continue
                    try:
                        voice = self.e.narrative_dispatch("get_voice", campaign_id, {"npc_id": npc_id})
                        add(
                            "HOT", "npc_voice_profile", f"narrative:voice:{npc_id}", 988, voice,
                            "dialogue target voice profile and original anchors",
                            scope_type="WORLD",
                        )
                    except (KeyError, ValueError):
                        pass
            resolved_target_keys: list[str] = []
            with self.e._db() as db:
                actor_key = None
                if actor_ref:
                    try: actor_key = self._ensure_entity_key_db(db, campaign_id, actor_ref)
                    except (KeyError, ValueError): actor_key = None
                for ref in target_refs:
                    try: key = self._ensure_entity_key_db(db, campaign_id, ref)
                    except (KeyError, ValueError): continue
                    if key not in resolved_target_keys:
                        resolved_target_keys.append(key)
                        row = db.execute("SELECT * FROM we4_entities WHERE campaign_id=? AND entity_key=?", (campaign_id, key)).fetchone()
                        if row:
                            add("HOT", "target_entity", f"target:{key}", 980, self._decode_entity_row(row), "explicit target in player intent", mandatory=False)
                for key in [x for x in [actor_key, *resolved_target_keys] if x]:
                    add("WARM", "we4_relations", f"relations:{key}", 790, self._relations_db(db, campaign_id, key, direction="both", limit=60), f"relations connected to {key}")

            world_context = self.e.get_world_context(campaign_id, location_id, event_limit=20, entity_limit=40)
            add("HOT", "location", f"location:{location_id or 'none'}", 970, world_context.get("location_record"), "active/requested location", mandatory=bool(location_id))
            add("HOT", "scene", "scene:active", 965, world_context.get("active_scene"), "active scene exact state", mandatory=bool(world_context.get("active_scene")))
            add("HOT", "combat", "combat:active", 960, world_context.get("active_combats"), "active combat exact state", mandatory=bool(world_context.get("active_combats")))
            add("HOT", "rules_state", "rules:local", 955, world_context.get("rules_state"), "local deterministic rules state", mandatory=any(c.startswith("rules.") or c.startswith("combat.") for c in requested_caps))

            add("WARM", "local_characters", "actors:characters", 820, world_context.get("characters"), "local living characters")
            public_npcs = [self._public_npc_payload(x) for x in (world_context.get("npcs") or [])]
            add("WARM", "local_npcs", "actors:npcs", 815, public_npcs, "local living NPC public state")
            for cognition in world_context.get("npc_cognition") or []:
                npc_id = cognition.get("npc_id") or cognition.get("id")
                if npc_id:
                    add("WARM", "npc_cognition", f"npc:cognition:{npc_id}", 810, cognition, "private NPC cognition", sensitivity="PRIVATE", scope_type="ENTITY", scope_kind="npc", scope_id=str(npc_id))
            add("WARM", "quests", "quests:active", 805, world_context.get("active_quests"), "active quest branches")
            add("WARM", "directors", "directors:active", 800, world_context.get("directors"), "internal regional authority stack", sensitivity="PRIVATE", scope_type="GM")
            add("WARM", "world_graph", "world:graph", 795, world_context.get("world_graph"), "nearby geography and route data")
            add("WARM", "world_state", "world:location_state", 785, world_context.get("location_world_state"), "current location state")
            add("WARM", "social_history", "social:recent", 775, world_context.get("recent_social_history"), "causal relationship history")
            if privileged_view:
                add(
                    "WARM", "events", "events:recent", 770,
                    world_context.get("recent_events"),
                    "recent authoritative events for privileged inspector",
                    sensitivity="PRIVATE", scope_type="GM",
                )
            add("WARM", "content_gaps", "authoring:gaps", 720, world_context.get("open_content_gaps"), "internal lazy-authoring gaps", sensitivity="PRIVATE", scope_type="GM")
            factions = [self._public_faction_payload(x) for x in (world_context.get("factions") or [])]
            add("COLD", "factions", "factions:regional", 620, factions, "regional public faction state")
            add("COLD", "market", "economy:market", 610, world_context.get("market_prices"), "local supply/scarcity prices")
            add("COLD", "lifecycle", "population:lifecycle", 600, world_context.get("npc_lifecycle"), "local lifecycle state", sensitivity="PRIVATE", scope_type="GM")
            add("COLD", "tracking", "world:tracking", 590, world_context.get("world_tracking"), "aggregate world tracking")
            add("COLD", "world_bible", "world:bible", 580, world_context.get("world_bible"), "setting canon and constraints")

            if actor_ref:
                try:
                    knowledge = self.knowledge_snapshot(
                        campaign_id,
                        believer=actor_ref,
                        fact_view="believer",
                        limit=100,
                        include_retracted=include_archive,
                    )
                    add("WARM", "knowledge", "knowledge:actor", 830, knowledge, "controlled actor beliefs and provenance", sensitivity="PRIVATE", scope_type="ENTITY", scope_kind=actor_kind, scope_id=actor_id)
                except (KeyError, ValueError):
                    pass

            # The current FTS corpus contains global WORLD truth and has no
            # principal partition. Fail closed for players/entities until a
            # per-principal index exists; their explicit belief snapshot above
            # remains the only knowledge candidate source.
            if privileged_view:
                search_text = " ".join(str(v) for i in normalized_intents for v in (i.get("parameters") or {}).values() if isinstance(v, (str, int, float)))
                with self.e._write_db() as db:
                    index_revision = self._sync_safe_knowledge_fts(db, campaign_id, snapshot_revision)
                    for claim in self._fts_claim_candidates(db, campaign_id, search_text, limit=20):
                        add("WARM", "knowledge_claim", f"claim:{claim['claim_id']}", 760, claim, "FTS5 privileged WORLD knowledge match", authority=claim["authority"], sensitivity="PRIVATE", scope_type="GM", source_revision=int(claim["learned_revision"] or 0))

            # Events have no visibility model. Do not create even redacted
            # event candidates for non-privileged viewers.
            if include_archive and privileged_view:
                with self.e._db() as db:
                    archive_events = []
                    for row in db.execute("SELECT * FROM events WHERE campaign_id=? ORDER BY revision DESC,id DESC LIMIT 100 OFFSET 20", (campaign_id,)).fetchall():
                        data = dict(row)
                        data["payload"] = self.e._loads(data.pop("payload_json"))
                        archive_events.append(data)
                    add("ARCHIVE", "events", "events:archive", 300, archive_events, "older event history for privileged inspector", scope_type="GM", sensitivity="PRIVATE")

            # Authorization happens before scoring/ranking. Unauthorized payloads
            # are discarded and only redacted exclusion metadata survives.
            authorized: list[dict[str, Any]] = []
            for candidate in candidates:
                ok, reason_code = authorize_candidate(candidate, principal)
                if not ok:
                    if candidate.get("mandatory"):
                        raise ValueError(f"CONTEXT_AUTH_UNSAT: mandatory candidate {candidate['item_id']} is not authorized ({reason_code})")
                    excluded.append({"item_id": candidate["item_id"], "tier": candidate["tier"], "kind": candidate["kind"], "reason": reason_code, "authorized": False})
                    continue
                components = {
                    "intent": 10000 if candidate["tier"] == "HOT" else (8500 if candidate["tier"] == "WARM" else 5000),
                    "importance": max(0, min(10000, candidate["priority"] * 10)),
                    "proximity": 10000 if candidate["tier"] in {"HOT", "WARM"} else 5000,
                    "recency": 10000 if candidate["source_revision"] >= snapshot_revision else 7000,
                    "continuity": 9000 if candidate["kind"] in {"actor", "scene", "combat", "knowledge"} else 6000,
                }
                candidate["score_components"] = components
                candidate["fixed_point_score"] = fixed_point_score(components)
                authorized.append(candidate)

            authorized.sort(key=lambda x: (TIER_ORDER[x["tier"]], 0 if x["mandatory"] else 1, -x["fixed_point_score"], -x["source_revision"], x["item_id"]))

            def envelope(candidate: dict[str, Any]) -> tuple[dict[str, Any], int]:
                out = {k: v for k, v in candidate.items() if k not in {"payload", "priority", "scope_type", "scope_kind", "scope_id", "sensitivity"}}
                out["principal_scope"] = {"type": candidate["scope_type"], "entity_kind": candidate["scope_kind"], "entity_id": candidate["scope_id"]}
                out["sensitivity"] = candidate["sensitivity"]
                out["payload"] = candidate["payload"]
                out["char_count"] = 0
                for _ in range(4):
                    n = len(self._canonical(out))
                    if out["char_count"] == n: break
                    out["char_count"] = n
                n = len(self._canonical(out)); out["char_count"] = n
                return out, len(self._canonical(out))

            prepared = [(c, *envelope(c)) for c in authorized]
            mandatory_cost = sum(size for c, _e, size in prepared if c["mandatory"])
            if mandatory_cost > max_chars:
                raise ValueError(f"CONTEXT_BUDGET_UNSAT: mandatory exact projection requires {mandatory_cost} chars but budget is {max_chars}")

            included: list[dict[str, Any]] = []
            used = 0
            for candidate, env, size in prepared:
                if candidate["mandatory"] or used + size <= max_chars:
                    included.append(env); used += size
                else:
                    excluded.append({"item_id": candidate["item_id"], "tier": candidate["tier"], "kind": candidate["kind"], "reason": "BUDGET", "authorized": True, "char_count": size})

            end_revision = self._campaign_revision(campaign_id)
            if end_revision != snapshot_revision:
                raise ValueError(f"CONTEXT_SNAPSHOT_CHANGED: revision moved from {snapshot_revision} to {end_revision}")

            grouped = {tier: [x for x in included if x["tier"] == tier] for tier in TIER_ORDER}
            compile_basis = {"compiler_version": "WECC-1.0", "campaign_id": campaign_id, "snapshot_revision": snapshot_revision, "plan_hash": plan_hash, "principal": principal.as_dict(), "budget_chars": max_chars, "included": included}
            compile_hash = self._digest(compile_basis)
            compilation_id = f"ctx_{compile_hash[:24]}"
            elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
            counts = {"generated": len(candidates), "authorized": len(authorized), "included": len(included), "excluded": len(excluded)}

            with self.e._write_db() as db:
                db.execute(
                    """INSERT INTO context_compile_receipts(campaign_id,compile_id,compiler_version,snapshot_revision,index_revision,plan_hash,principal_json,requested_budget,usable_budget,used_chars,compile_hash,counts_json,timing_json,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(campaign_id,compile_id) DO UPDATE SET used_chars=excluded.used_chars,counts_json=excluded.counts_json,timing_json=excluded.timing_json""",
                    (campaign_id, compilation_id, "WECC-1.0", snapshot_revision, index_revision, plan_hash, self.e._dumps(principal.as_dict()), max_chars, max_chars, used, compile_hash, self.e._dumps(counts), self.e._dumps({"total": elapsed_ms}), self.e._now()),
                )
                db.execute("DELETE FROM context_compile_items WHERE campaign_id=? AND compile_id=?", (campaign_id, compilation_id))
                included_ids = {x["item_id"] for x in included}
                excluded_map = {x["item_id"]: x for x in excluded}
                for candidate in candidates:
                    ex = excluded_map.get(candidate["item_id"])
                    db.execute(
                        """INSERT INTO context_compile_items(campaign_id,compile_id,candidate_id,source_revision,authorized,included,tier,kind,fixed_point_score,char_count,exclusion_reason,dependencies_json)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (campaign_id, compilation_id, candidate["item_id"], int(candidate.get("source_revision", 0)), 0 if ex and ex.get("authorized") is False else 1, int(candidate["item_id"] in included_ids), candidate["tier"], candidate["kind"], int(candidate.get("fixed_point_score", 0)), int((ex or {}).get("char_count", 0)), (ex or {}).get("reason"), self.e._dumps(candidate.get("dependencies") or [])),
                    )
                db.execute(
                    """INSERT INTO we4_context_compilations(campaign_id,compilation_id,turn_id,actor_key,location_id,requested_capabilities_json,budget_chars,used_chars,estimated_tokens,included_json,omitted_json,digest,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(campaign_id,compilation_id) DO UPDATE SET turn_id=COALESCE(excluded.turn_id,we4_context_compilations.turn_id),used_chars=excluded.used_chars,estimated_tokens=excluded.estimated_tokens,included_json=excluded.included_json,omitted_json=excluded.omitted_json,digest=excluded.digest""",
                    (campaign_id, compilation_id, turn_id, self._entity_key(actor_kind, actor_id) if actor_ref else None, location_id, self.e._dumps(requested_caps), max_chars, used, math.ceil(used / 4), self.e._dumps(included), self.e._dumps(excluded), compile_hash, self.e._now()),
                )

            return {
                "campaign_id": campaign_id, "compilation_id": compilation_id, "turn_id": turn_id,
                "actor_key": self._entity_key(actor_kind, actor_id) if actor_ref else None, "location_id": location_id,
                "requested_capabilities": requested_caps, "snapshot_revision": snapshot_revision, "index_revision": index_revision,
                "principal": principal.as_dict(), "plan_hash": plan_hash,
                "budget": {"max_chars": max_chars, "used_chars": used, "remaining_chars": max_chars - used, "estimated_tokens": math.ceil(used / 4), "mandatory_chars": mandatory_cost},
                "digest": compile_hash, "compile_hash": compile_hash, "context": grouped,
                "activation_inspector": {"candidate_count": len(candidates), "authorized_count": len(authorized), "included_count": len(included), "omitted_count": len(excluded),
                    "included": [{"item_id": x["item_id"], "tier": x["tier"], "kind": x["kind"], "char_count": x.get("char_count"), "activation_reason": x["activation_reason"], "fixed_point_score": x.get("fixed_point_score", 0)} for x in included],
                    "omitted": excluded, "timing_ms": {"total": elapsed_ms}},
            }

    # ------------------------------------------------------------------
    # Unified resolveTurn execution
    # ------------------------------------------------------------------

    def _turn_id(
        self,
        campaign_id: str,
        actor_kind: str | None,
        actor_id: str | None,
        expected_revision: int,
        raw_player_text: str,
        intents: Sequence[dict[str, Any]],
        idempotency_key: str | None,
    ) -> str:
        if idempotency_key:
            cleaned = self.e._clean_id(idempotency_key)
            return cleaned if cleaned.startswith("turn_") else f"turn_{cleaned}"
        digest = self._digest([
            campaign_id, actor_kind, actor_id, expected_revision,
            str(raw_player_text or ""), intents,
        ])[:24]
        return f"turn_{digest}"

    def _author_action(self, campaign_id: str, params: dict[str, Any]) -> Any:
        action = str(params.get("action") or "").strip().lower()
        if action == "stage":
            return self.e.author_stage(campaign_id, params["batch_id"], params.get("payload") or {}, mode=params.get("mode", "bootstrap"))
        if action == "validate":
            return self.e.author_validate(campaign_id, params["batch_id"])
        if action == "dry_run":
            return self.e.author_dry_run(campaign_id, params["batch_id"], days=int(params.get("days", 365)))
        if action == "promote":
            return self.e.author_promote(campaign_id, params["batch_id"])
        if action == "materialization_brief":
            return self.e.author_materialization_brief(campaign_id, params["location_id"])
        if action == "digest":
            return self.e.author_world_digest(campaign_id)
        if action == "lock":
            return self.e.author_lock(campaign_id, params["object_kind"], params["object_id"], reason=params.get("reason", "player touched"))
        if action == "log_gap":
            return self.e.author_log_gap(
                campaign_id, params["gap_key"], params["kind"], params["summary"],
                scope_id=params.get("scope_id"), context=params.get("context") or {},
            )
        if action == "list_gaps":
            return self.e.author_list_gaps(campaign_id, int(params.get("limit", 20)))
        if action == "resolve_gap":
            return self.e.author_resolve_gap(campaign_id, params["gap_key"], status=params.get("status", "resolved"))
        raise ValueError(f"unknown author action: {action}")

    def _execute_capability(
        self,
        campaign_id: str,
        actor_kind: str | None,
        actor_id: str | None,
        capability_id: str,
        params: dict[str, Any],
    ) -> Any:
        p = dict(params)
        if capability_id == "context.compile":
            return {"status": "context_compiled_by_turn_router"}
        if capability_id == "entity.graph.read":
            if p.get("source") is not None and p.get("target") is not None:
                return self.graph_path(
                    campaign_id, p["source"], p["target"],
                    relation_types=p.get("relation_types") or [],
                    max_depth=int(p.get("max_depth", 6)),
                    max_expanded=int(p.get("max_expanded", 5000)),
                )
            if p.get("entity") is not None:
                return self.get_entity(campaign_id, p["entity"])
            return {"entities": self.list_entities(
                campaign_id, entity_type=p.get("entity_type"), status=p.get("status"),
                search=p.get("search"), limit=int(p.get("limit", 100)),
            )}
        if capability_id == "entity.relation.write":
            return self.upsert_relation(
                campaign_id, p["source"], p["relation_type"], p["target"],
                relation_id=p.get("relation_id"), strength=float(p.get("strength", 1.0)),
                directed=bool(p.get("directed", True)), valid_from=p.get("valid_from"),
                valid_to=p.get("valid_to"), provenance=p.get("provenance") or {},
                metadata=p.get("metadata") or {}, create_missing=bool(p.get("create_missing", False)),
            )
        if capability_id == "knowledge.read":
            believer = p.get("believer")
            fact_view = "canonical"
            if actor_kind in {"character", "npc"} and actor_id:
                expected_believer = f"{actor_kind}:{actor_id}"
                if believer is not None and str(believer) != expected_believer:
                    raise PermissionError("KNOWLEDGE_PRINCIPAL_MISMATCH")
                believer = expected_believer
                fact_view = "believer"
            return self.knowledge_snapshot(
                campaign_id, believer=believer, subject=p.get("subject"),
                fact_id=p.get("fact_id"), include_retracted=bool(p.get("include_retracted", False)),
                limit=int(p.get("limit", 100)), fact_view=fact_view,
            )
        if capability_id == "knowledge.fact.assert":
            return self.assert_fact(
                campaign_id, p["subject"], p["predicate"], p.get("object_value"),
                object_type=p.get("object_type", "literal"), fact_id=p.get("fact_id"),
                confidence=float(p.get("confidence", 1.0)), status=p.get("status", "active"),
                source_event_id=p.get("source_event_id"), valid_from=p.get("valid_from"),
                valid_to=p.get("valid_to"), provenance=p.get("provenance") or {},
            )
        if capability_id == "knowledge.belief.set":
            return self.set_belief(
                campaign_id, p["believer"], p["fact_id"], belief_value=p.get("belief_value"),
                confidence=float(p.get("confidence", 0.5)), source=p.get("source"),
                acquired_world_time=p.get("acquired_world_time"),
                last_confirmed_world_time=p.get("last_confirmed_world_time"),
                status=p.get("status", "believes"), provenance=p.get("provenance") or {},
            )
        if capability_id == "knowledge.transfer":
            return self.transfer_information(
                campaign_id, p["fact_id"], p["receiver"], sender=p.get("sender"),
                transfer_id=p.get("transfer_id"), channel=p.get("channel", "speech"),
                credibility=float(p.get("credibility", 1.0)), distortion=float(p.get("distortion", 0.0)),
                parent_transfer_id=p.get("parent_transfer_id"), payload=p.get("payload") or {},
            )
        if capability_id == "actor.move":
            kind = p.get("kind") or actor_kind
            entity_id = p.get("actor_id") or actor_id
            if not kind or not entity_id:
                raise ValueError("actor.move requires kind/actor_id or a turn actor")
            destination = p.get("location") or p.get("destination")
            if not destination:
                raise ValueError("actor.move requires location/destination")
            if str(destination) != "unknown":
                try:
                    self.e.get_location(campaign_id, str(destination))
                except KeyError as exc:
                    raise ValueError(f"actor.move destination is not a registered location: {destination}") from exc
            return self.e.move_actor(campaign_id, kind, entity_id, destination, p.get("reason", "moved"))
        if capability_id == "space.route":
            return self.e.world_systems_dispatch("find_path", campaign_id, p)
        if capability_id == "rules.check":
            return self.e.resolve_check(int(p["modifier"]), int(p["dc"]), p.get("mode", "normal"), campaign_id)
        if capability_id == "rules.attack":
            attacker_kind = p.get("attacker_kind") or actor_kind
            attacker_id = p.get("attacker_id") or actor_id
            if not attacker_kind or not attacker_id:
                raise ValueError("rules.attack requires attacker or a turn actor")
            return self.e.resolve_attack(
                campaign_id, attacker_kind, attacker_id, p["target_kind"], p["target_id"],
                attack_bonus=int(p["attack_bonus"]), damage_expression=p["damage_expression"],
                mode=p.get("mode", "normal"), attack_name=p.get("attack_name", "attack"),
                combat_id=p.get("combat_id"), range_cells=p.get("range_cells"),
                ignore_cover=bool(p.get("ignore_cover", False)), damage_type=p.get("damage_type", "untyped"),
            )
        if capability_id == "rules.generic":
            return self.e.rules_dispatch(p["operation"], campaign_id, p.get("payload") or {})
        if capability_id == "actor.condition":
            kind = p.get("kind") or actor_kind
            entity_id = p.get("actor_id") or actor_id
            if not kind or not entity_id:
                raise ValueError("actor.condition requires actor")
            return self.e.set_condition(
                campaign_id, kind, entity_id, p["condition"], bool(p["active"]),
                p.get("reason", "condition changed"),
            )
        if capability_id == "actor.resources":
            character_id = p.get("character_id") or (actor_id if actor_kind == "character" else None)
            if not character_id:
                raise ValueError("actor.resources requires character_id")
            return self.e.update_character_resources(
                campaign_id, character_id,
                resource_delta=p.get("resource_delta") or {},
                add_inventory=p.get("add_inventory") or [],
                remove_inventory_indexes=p.get("remove_inventory_indexes") or [],
                reason=p.get("reason", "resources updated"),
            )
        if capability_id == "social.relationship.adjust":
            source_id = p.get("source_id") or actor_id
            if not source_id:
                raise ValueError("relationship adjustment requires source_id or turn actor")
            return self.e.adjust_relationship(
                campaign_id, source_id, p["target_id"],
                trust_delta=int(p.get("trust_delta", 0)), fear_delta=int(p.get("fear_delta", 0)),
                respect_delta=int(p.get("respect_delta", 0)), affection_delta=int(p.get("affection_delta", 0)),
                reason=p.get("reason", "relationship changed"),
            )
        if capability_id == "npc.state.update":
            return self.e.update_npc_state(
                campaign_id, p["npc_id"], attitude_delta=int(p.get("attitude_delta", 0)),
                add_beliefs=p.get("add_beliefs") or [], remove_beliefs=p.get("remove_beliefs") or [],
                add_goals=p.get("add_goals") or [], remove_goals=p.get("remove_goals") or [],
                add_memory=p.get("add_memory") or [], reason=p.get("reason", "NPC state changed"),
            )
        if capability_id == "npc.dialogue.context":
            npc_id = p.get("npc_id") or p.get("target_id")
            if not npc_id:
                raise ValueError("npc.dialogue.context requires npc_id")
            topic = p.get("topic") or "general"
            dialogue_hint = dict(p.get("dialogue_hint") or {})
            for key in ("speech_act", "objective", "facts_to_reveal", "facts_to_conceal", "subtext", "desired_effect", "emotion", "dominant_motive", "status_difference", "interruptibility"):
                if key in p and key not in dialogue_hint:
                    dialogue_hint[key] = p[key]
            plan = self.e.narrative_dispatch(
                "plan_dialogue", campaign_id,
                {
                    "speaker_id": npc_id,
                    "listener_kind": actor_kind,
                    "listener_id": actor_id,
                    "topic": topic,
                    "hint": dialogue_hint,
                },
            )
            return {
                "npc": self._public_npc_payload(self.e.get_npc_sheet(campaign_id, npc_id)),
                "topic": topic,
                "dialogue_plan": plan,
                "narration_required": True,
                "authority_note": "facts/cognition remain backend state; the plan supplies only bounded semantic intent and exact wording is model-authored",
            }
        if capability_id == "npc.plan":
            return self.e.npc_life_dispatch("plan", campaign_id, p)
        if capability_id == "faction.adjust":
            return self.e.adjust_faction(
                campaign_id, p["faction_id"], reputation_delta=int(p.get("reputation_delta", 0)),
                reserve_delta=int(p.get("reserve_delta", 0)), state_patch=p.get("state_patch") or {},
                add_goals=p.get("add_goals") or [], remove_goals=p.get("remove_goals") or [],
                reason=p.get("reason", "faction state changed"),
            )
        if capability_id == "quest.update":
            return self.e.upsert_quest(
                campaign_id, p["quest_id"], p["title"], status=p.get("status", "active"),
                owner_id=p.get("owner_id"), region=p.get("region"), objectives=p.get("objectives") or [],
                state=p.get("state") or {}, reason=p.get("reason", "quest updated"),
            )
        if capability_id == "world.state.set":
            return self.e.set_world_state(
                campaign_id, p["scope_type"], p["scope_id"], p["state_key"], p.get("value"),
                p.get("reason", "world state changed"),
            )
        if capability_id == "world.event.commit":
            return self.e.commit_event(
                campaign_id, p["event_type"], p["summary"], region=p.get("region"),
                actor_id=p.get("actor_id") or actor_id, target_id=p.get("target_id"),
                payload=p.get("payload") or {},
            )
        if capability_id == "world.advance":
            return self.e.advance_world(
                campaign_id, int(p["minutes"]), p.get("reason", "elapsed time"),
                weather=p.get("weather"), simulate=bool(p.get("simulate", True)), season=p.get("season"),
            )
        if capability_id == "combat.start":
            return self.e.start_combat(
                campaign_id, p["combat_id"], p.get("location", "unknown"), p["participants"],
                grid_width=int(p.get("grid_width", 20)), grid_height=int(p.get("grid_height", 20)),
                positions=p.get("positions") or [], terrain=p.get("terrain") or [], scene_id=p.get("scene_id"),
            )
        if capability_id == "combat.next":
            return self.e.next_turn(campaign_id, p["combat_id"])
        if capability_id == "combat.end":
            return self.e.end_combat(campaign_id, p["combat_id"], p.get("reason", "combat ended"))
        if capability_id == "progression.manage":
            operation = p.get("operation")
            payload = p.get("payload") or {k: v for k, v in p.items() if k != "operation"}
            if not operation:
                raise ValueError("progression.manage requires operation")
            return self.e.world_systems_dispatch(operation, campaign_id, payload)
        if capability_id == "author.content":
            return self._author_action(campaign_id, p)
        if capability_id == "narrative.manage":
            operation = p.pop("operation", None) or p.pop("action", None)
            payload = p.pop("payload", None)
            if not operation:
                raise ValueError("narrative.manage requires operation")
            if payload is None:
                payload = p
            elif p:
                payload = {**dict(payload), **p}
            return self.e.narrative_dispatch(str(operation), campaign_id, dict(payload or {}))
        if capability_id == "visual.cue":
            return self.e.build_image_cue(campaign_id, **p)
        raise ValueError(f"capability has no executor: {capability_id}")

    def _turn_record(self, campaign_id: str, turn_id: str) -> dict[str, Any] | None:
        with self.e._db() as db:
            row = db.execute("SELECT * FROM we4_turn_records WHERE campaign_id=? AND turn_id=?", (campaign_id, turn_id)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["intents"] = self.e._loads(data.pop("intents_json"))
        data["capability_plan"] = self.e._loads(data.pop("capability_plan_json"))
        data["result"] = self.e._loads(data.pop("result_json"))
        return data

    def resolve_turn(
        self,
        campaign_id: str,
        *,
        actor_kind: str | None = None,
        actor_id: str | None = None,
        raw_player_text: str = "",
        intents: Sequence[dict[str, Any]] = (),
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
        mode: str = "execute",
        max_context_chars: int = 14_000,
        include_archive: bool = False,
        continue_on_error: bool = False,
        retry_failed: bool = False,
        location_id: str | None = None,
    ) -> dict[str, Any]:
        mode = str(mode or "execute").lower()
        if mode not in {"execute", "plan", "context_only", "capabilities"}:
            raise ValueError("mode must be execute, plan, context_only, or capabilities")
        self.e._ensure_campaign_exists(campaign_id)
        self.seed_defaults(campaign_id)
        if mode == "capabilities":
            return {
                "campaign_id": campaign_id,
                "mode": mode,
                "capabilities": self.list_capabilities(campaign_id),
                "intent_aliases": dict(sorted(INTENT_ALIASES.items())),
                "protocol_version": "WETP-1.0",
            }

        with self.e._turn_lock:
            self.sync_existing_entities(campaign_id)
            revision_before = self._campaign_revision(campaign_id)
            effective_expected = revision_before if expected_revision is None else int(expected_revision)
            turn_id = self._turn_id(
                campaign_id, actor_kind, actor_id, effective_expected,
                raw_player_text, intents, idempotency_key,
            )
            existing = self._turn_record(campaign_id, turn_id)
            if existing and existing["status"] in {"completed", "planned"}:
                result = dict(existing["result"])
                result["idempotent_replay"] = True
                result["turn_record_status"] = existing["status"]
                return result
            if existing and existing["status"] in {"failed", "partial_failed"} and not retry_failed:
                result = dict(existing["result"])
                result["idempotent_replay"] = True
                result["retry_blocked"] = True
                result["turn_record_status"] = existing["status"]
                return result
            if effective_expected != revision_before:
                raise ValueError(
                    f"revision conflict: expected {effective_expected}, current {revision_before}; re-read context before acting"
                )

            normalized = self._normalize_intents(campaign_id, intents)
            plan = self.capability_plan(campaign_id, intents)
            context = self.compile_context(
                campaign_id,
                actor_kind=actor_kind,
                actor_id=actor_id,
                location_id=location_id,
                intents=intents,
                capability_ids=[x["capability_id"] for x in normalized],
                max_chars=max_context_chars,
                include_archive=include_archive,
                turn_id=turn_id,
            )
            actor_key = context.get("actor_key")
            base_result = {
                "protocol_version": "WETP-1.0",
                "campaign_id": campaign_id,
                "turn_id": turn_id,
                "mode": mode,
                "actor_key": actor_key,
                "revision_before": revision_before,
                "expected_revision": effective_expected,
                "capability_plan": plan,
                "context_packet": context,
                "commit_model": "atomic_per_command; ordered turn stops on first required failure",
                "idempotent_replay": False,
            }

            if mode in {"plan", "context_only"}:
                status = "planned"
                if mode == "context_only":
                    base_result["capability_plan"] = []
                base_result.update({"status": status, "steps": [], "revision_after": revision_before})
                with self.e._write_db() as db:
                    now = self.e._now()
                    db.execute(
                        """INSERT INTO we4_turn_records(
                               campaign_id,turn_id,actor_key,expected_revision,revision_before,revision_after,
                               raw_player_text,intents_json,capability_plan_json,context_digest,result_json,status,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(campaign_id,turn_id) DO UPDATE SET
                               result_json=excluded.result_json,status=excluded.status,updated_at=excluded.updated_at""",
                        (
                            campaign_id, turn_id, actor_key, effective_expected, revision_before, revision_before,
                            str(raw_player_text or "")[:20_000], self.e._dumps(list(intents)), self.e._dumps(plan),
                            context["digest"], self.e._dumps(base_result), status, now, now,
                        ),
                    )
                return base_result

            with self.e._write_db() as db:
                now = self.e._now()
                db.execute(
                    """INSERT INTO we4_turn_records(
                           campaign_id,turn_id,actor_key,expected_revision,revision_before,revision_after,
                           raw_player_text,intents_json,capability_plan_json,context_digest,result_json,status,created_at,updated_at)
                       VALUES(?,?,?,?,?,NULL,?,?,?,?,?,'pending',?,?)
                       ON CONFLICT(campaign_id,turn_id) DO UPDATE SET
                           expected_revision=excluded.expected_revision,revision_before=excluded.revision_before,
                           raw_player_text=excluded.raw_player_text,intents_json=excluded.intents_json,
                           capability_plan_json=excluded.capability_plan_json,context_digest=excluded.context_digest,
                           result_json='{}',status='pending',updated_at=excluded.updated_at""",
                    (
                        campaign_id, turn_id, actor_key, effective_expected, revision_before,
                        str(raw_player_text or "")[:20_000], self.e._dumps(list(intents)),
                        self.e._dumps(plan), context["digest"], self.e._dumps({}), now, now,
                    ),
                )

            steps: list[dict[str, Any]] = []
            failed_required = False
            completed_ids: set[str] = set()
            failed_ids: set[str] = set()
            for intent in normalized:
                intent_id = intent["intent_id"]
                blocked_by = [x for x in intent["depends_on"] if x in failed_ids]
                if blocked_by:
                    step = {
                        "intent_id": intent_id, "capability_id": intent["capability_id"],
                        "status": "skipped_dependency_failed", "blocked_by": blocked_by,
                        "revision_before": self._campaign_revision(campaign_id),
                        "revision_after": self._campaign_revision(campaign_id),
                    }
                    steps.append(step)
                    failed_ids.add(intent_id)
                    if not intent["optional"]:
                        failed_required = True
                        if not continue_on_error:
                            break
                    continue
                step_revision_before = self._campaign_revision(campaign_id)
                try:
                    result = self._execute_capability(
                        campaign_id, actor_kind, actor_id,
                        intent["capability_id"], intent["parameters"],
                    )
                    step_revision_after = self._campaign_revision(campaign_id)
                    steps.append({
                        "intent_id": intent_id,
                        "intent_type": intent["intent_type"],
                        "capability_id": intent["capability_id"],
                        "mode": intent["manifest"]["mode"],
                        "status": "completed",
                        "revision_before": step_revision_before,
                        "revision_after": step_revision_after,
                        "revision_delta": step_revision_after - step_revision_before,
                        "result": result,
                    })
                    completed_ids.add(intent_id)
                except Exception as exc:
                    step_revision_after = self._campaign_revision(campaign_id)
                    steps.append({
                        "intent_id": intent_id,
                        "intent_type": intent["intent_type"],
                        "capability_id": intent["capability_id"],
                        "mode": intent["manifest"]["mode"],
                        "status": "failed",
                        "optional": intent["optional"],
                        "revision_before": step_revision_before,
                        "revision_after": step_revision_after,
                        "revision_delta": step_revision_after - step_revision_before,
                        "error": _public_step_error(exc),
                    })
                    failed_ids.add(intent_id)
                    if not intent["optional"]:
                        failed_required = True
                        if not continue_on_error:
                            break

            revision_after = self._campaign_revision(campaign_id)
            if failed_required:
                status = "partial_failed" if any(x["status"] == "completed" for x in steps) else "failed"
            else:
                status = "completed"
            result_packet = dict(base_result)
            result_packet.update({
                "status": status,
                "steps": steps,
                "revision_after": revision_after,
                "revision_delta": revision_after - revision_before,
                "completed_intents": sorted(completed_ids),
                "failed_intents": sorted(failed_ids),
                "authoritative": True,
            })
            # Re-sync the graph after typed-table mutations. RESOLVED/SIMULATED/
            # AUTHOR turns return a post-commit context packet so narration is
            # compiled from committed state rather than the pre-action snapshot.
            self.sync_existing_entities(campaign_id)
            mutating_turn = any(intent["manifest"]["mode"] in MUTATING_MODES for intent in normalized)
            if mutating_turn:
                post_context = self.compile_context(
                    campaign_id, actor_kind=actor_kind, actor_id=actor_id, location_id=location_id,
                    intents=intents, capability_ids=[x["capability_id"] for x in normalized],
                    max_chars=max_context_chars, include_archive=include_archive, turn_id=turn_id,
                )
                result_packet["context_packet"] = post_context
                result_packet["context_phase"] = "post_commit"
            else:
                result_packet["context_phase"] = "read_snapshot"
            with self.e._write_db() as db:
                db.execute(
                    "UPDATE we4_turn_records SET revision_after=?,context_digest=?,result_json=?,status=?,updated_at=? WHERE campaign_id=? AND turn_id=?",
                    (
                        revision_after, result_packet["context_packet"]["digest"], self.e._dumps(result_packet), status,
                        self.e._now(), campaign_id, turn_id,
                    ),
                )
            return result_packet

    def get_turn(self, campaign_id: str, turn_id: str) -> dict[str, Any]:
        turn_id = self.e._clean_id(turn_id)
        record = self._turn_record(campaign_id, turn_id)
        if not record:
            raise KeyError(f"unknown turn: {turn_id}")
        return record

    def dispatch(self, operation: str, campaign_id: str, payload: dict[str, Any] | None = None) -> Any:
        p = dict(payload or {})
        operation = str(operation or "").strip().lower()
        if operation == "capabilities":
            return self.list_capabilities(campaign_id, enabled_only=bool(p.get("enabled_only", True)))
        if operation == "sync_entities":
            return self.sync_existing_entities(campaign_id)
        if operation == "list_entities":
            return self.list_entities(campaign_id, **p)
        if operation == "get_entity":
            return self.get_entity(campaign_id, p["entity"])
        if operation == "upsert_relation":
            return self.upsert_relation(campaign_id, **p)
        if operation == "relations_for":
            entity = p.pop("entity")
            return self.relations_for(campaign_id, entity, **p)
        if operation == "graph_path":
            return self.graph_path(campaign_id, **p)
        if operation == "assert_fact":
            return self.assert_fact(campaign_id, **p)
        if operation == "retract_fact":
            return self.retract_fact(campaign_id, **p)
        if operation == "set_belief":
            return self.set_belief(campaign_id, **p)
        if operation == "transfer_information":
            return self.transfer_information(campaign_id, **p)
        if operation == "knowledge":
            return self.knowledge_snapshot(campaign_id, **p)
        if operation == "compile_context":
            return self.compile_context(campaign_id, **p)
        if operation == "resolve_turn":
            return self.resolve_turn(campaign_id, **p)
        if operation == "get_turn":
            return self.get_turn(campaign_id, p["turn_id"])
        raise ValueError(f"unknown turn-router operation: {operation}")
