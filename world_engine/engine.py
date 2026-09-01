from __future__ import annotations

import hashlib
import json
import math
import random
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .authoring import AUTHORING_SCHEMA, AuthoringKernel
from .agency import AgencyKernel, prepare_agency_schema_db
from .companion import (
    CompanionService,
    PresentationEnvelope,
    canonical_json_bytes,
    install_companion_schema_db,
    validate_public_text,
)
from .narrative import NARRATIVE_SCHEMA, NarrativeKernel
from .npc_life import NPC_LIFE_SCHEMA, NpcLifeKernel
from .environment import ENVIRONMENT_SCHEMA, EnvironmentKernel
from .economy import ECONOMY_SCHEMA, EconomyKernel, migrate_economy_schema_db
from .incidents import INCIDENT_SCHEMA, IncidentKernel
from .mechanisms import (
    MECHANISM_SCHEMA,
    MechanismKernel,
    prepare_mechanism_schema_db,
    verify_mechanism_schema_db,
)
from .population import POPULATION_SCHEMA, PopulationKernel
from .politics import PoliticsKernel
from .procedural import ProceduralWorldGenerator
from .quests import QUEST_SCHEMA, QuestRuntimeKernel
from .rules import RULES_SCHEMA, RulesKernel
from .simulation import SIM_SCHEMA, SimulationKernel, record_relationship_event
from .turn_router import TURN_ROUTER_SCHEMA, TurnRouter
from .world_layers import LAYER_SCHEMA, WorldLayerKernel, apply_succession
from .world_systems import WORLD_SYSTEMS_SCHEMA, WorldSystemsKernel

_DICE_RE = re.compile(r"^\s*(?:(\d{1,3})d(\d{1,5})|(-?\d+))\s*([+-]\s*\d+)?\s*$", re.I)
_ENTITY_KINDS = {"character", "npc"}
_PUBLICATION_CANDIDATE_VERSION = "WEPUB-1.0"
_PRESENTATION_VERSION = "WEP-1.0"
_PUBLICATION_CANDIDATE_KEYS = frozenset(
    {
        "candidate_version",
        "campaign_id",
        "packet_id",
        "turn_id",
        "authoritative_revision",
        "narration",
        "choices",
        "presentation",
    }
)
_CLOSED_PRESENTATION_KEYS = frozenset(
    {"presentation_version", "kind", "presentation_id"}
)
_PUBLICATION_ENVELOPE_KEYS = frozenset(
    {
        "campaign_id",
        "presentation_id",
        "revision",
        "narration",
        "turn_id",
        "choices",
        "presentation",
    }
)
_ACCEPTED_PRESENTATION_KEYS = frozenset(
    {"presentation_version", "kind", "presentation_id", "narrative_evidence"}
)
_ACCEPTED_EVIDENCE_KEYS = frozenset(
    {
        "verification_version",
        "campaign_id",
        "turn_id",
        "authoritative_revision",
        "packet_id",
        "packet_digest",
        "packet_version",
        "output_id",
        "output_hash",
        "receipt_id",
        "receipt_version",
        "accepted",
        "hard_pass",
        "evidence_digest",
    }
)


@dataclass(frozen=True)
class DiceResult:
    expression: str
    rolls: tuple[int, ...]
    modifier: int
    total: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "expression": self.expression,
            "rolls": list(self.rolls),
            "modifier": self.modifier,
            "total": self.total,
        }


class WorldEngine:
    """Persistent game/world kernel for a ChatGPT-hosted World Engine.

    The database is authoritative. The language model may interpret intent and
    narrate outcomes, but game facts are read from and mutated through this
    kernel. Mutations are transactional and logged to the event ledger.
    """

    # v4.0.1, v4.0.2, and v4.1.0 all used user_version 14 for
    # different additive schemas. v4.2 records schema 15. v4.3 adds private
    # narrative validation evidence plus the presentation and delivery outbox
    # foundation. v4.5 merges the sparse environmental consequence runtime and
    # records schema 17. v4.7 rebases the independently numbered mechanism,
    # economy, and population donors into ordered schema stages 18..20.
    # Stages 21..24 converge the event/incident spine, politics, agency, and
    # executable quest runtime. Additive schemas are always installed, so a
    # partially upgraded database cannot skip a domain merely due to user_version.
    SCHEMA_VERSION = 24

    def __init__(self, db_path: str | Path, rng: random.Random | None = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # This RNG is only for non-campaign utility parsing/tests. Authoritative
        # gameplay and simulation randomness is campaign-seeded in sim_config.
        self.rng = rng or random.Random(0)
        # Unified turns are serialized within one process. Individual mutations
        # remain protected by BEGIN IMMEDIATE in SQLite.
        self._turn_lock = threading.RLock()
        self._init_db()

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _write_db(self):
        """Open a serialized SQLite write transaction.

        BEGIN IMMEDIATE acquires the write reservation before any read that
        participates in a read-modify-write operation. This prevents concurrent
        GPT/HTTP calls from reading the same old value and silently overwriting
        each other.
        """
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _loads(value: str) -> Any:
        return json.loads(value)

    def _init_db(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS campaigns (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    world_time TEXT NOT NULL,
                    weather TEXT NOT NULL DEFAULT 'clear',
                    revision INTEGER NOT NULL DEFAULT 0,
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS characters (
                    campaign_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    level INTEGER NOT NULL DEFAULT 1 CHECK(level BETWEEN 1 AND 20),
                    hp INTEGER NOT NULL DEFAULT 1 CHECK(hp >= 0),
                    max_hp INTEGER NOT NULL DEFAULT 1 CHECK(max_hp >= 1),
                    ac INTEGER NOT NULL DEFAULT 10 CHECK(ac BETWEEN 1 AND 40),
                    location TEXT NOT NULL DEFAULT 'unknown',
                    abilities_json TEXT NOT NULL DEFAULT '{}',
                    proficiency_bonus INTEGER NOT NULL DEFAULT 2,
                    conditions_json TEXT NOT NULL DEFAULT '[]',
                    resources_json TEXT NOT NULL DEFAULT '{}',
                    inventory_json TEXT NOT NULL DEFAULT '[]',
                    notes_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'alive' CHECK(status IN ('alive','dead','missing')),
                    died_on TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(campaign_id, id),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS npcs (
                    campaign_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    hp INTEGER NOT NULL DEFAULT 1 CHECK(hp >= 0),
                    max_hp INTEGER NOT NULL DEFAULT 1 CHECK(max_hp >= 1),
                    ac INTEGER NOT NULL DEFAULT 10 CHECK(ac BETWEEN 1 AND 40),
                    location TEXT NOT NULL DEFAULT 'unknown',
                    faction_id TEXT,
                    attitude INTEGER NOT NULL DEFAULT 0 CHECK(attitude BETWEEN -10 AND 10),
                    stats_json TEXT NOT NULL DEFAULT '{}',
                    conditions_json TEXT NOT NULL DEFAULT '[]',
                    beliefs_json TEXT NOT NULL DEFAULT '[]',
                    goals_json TEXT NOT NULL DEFAULT '[]',
                    routine_json TEXT NOT NULL DEFAULT '{}',
                    memory_json TEXT NOT NULL DEFAULT '[]',
                    importance TEXT NOT NULL DEFAULT 'minor' CHECK(importance IN ('minor','supporting','major')),
                    status TEXT NOT NULL DEFAULT 'alive' CHECK(status IN ('alive','dead','missing')),
                    died_on TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(campaign_id, id),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS factions (
                    campaign_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    region TEXT NOT NULL DEFAULT 'unknown',
                    reputation INTEGER NOT NULL DEFAULT 0 CHECK(reputation BETWEEN -10 AND 10),
                    reserve_score INTEGER NOT NULL DEFAULT 0,
                    goals_json TEXT NOT NULL DEFAULT '[]',
                    state_json TEXT NOT NULL DEFAULT '{}',
                    leader_id TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(campaign_id, id),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS locations (
                    campaign_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    region TEXT NOT NULL DEFAULT 'unknown',
                    description TEXT NOT NULL DEFAULT '',
                    x REAL,
                    y REAL,
                    realm_id TEXT,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    state_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(campaign_id, id),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS world_state (
                    campaign_id TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    state_key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(campaign_id, scope_type, scope_id, state_key),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS relationships (
                    campaign_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    trust INTEGER NOT NULL DEFAULT 0 CHECK(trust BETWEEN -100 AND 100),
                    fear INTEGER NOT NULL DEFAULT 0 CHECK(fear BETWEEN -100 AND 100),
                    respect INTEGER NOT NULL DEFAULT 0 CHECK(respect BETWEEN -100 AND 100),
                    affection INTEGER NOT NULL DEFAULT 0 CHECK(affection BETWEEN -100 AND 100),
                    notes_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(campaign_id, source_id, target_id),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS quests (
                    campaign_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    owner_id TEXT,
                    region TEXT,
                    objectives_json TEXT NOT NULL DEFAULT '[]',
                    state_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(campaign_id, id),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS combats (
                    campaign_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    location TEXT NOT NULL DEFAULT 'unknown',
                    round INTEGER NOT NULL DEFAULT 1,
                    turn_index INTEGER NOT NULL DEFAULT 0,
                    grid_width INTEGER NOT NULL DEFAULT 20,
                    grid_height INTEGER NOT NULL DEFAULT 20,
                    participants_json TEXT NOT NULL,
                    initiative_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(campaign_id, id),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS location_links (
                    campaign_id TEXT NOT NULL,
                    from_id TEXT NOT NULL,
                    to_id TEXT NOT NULL,
                    travel_hours REAL NOT NULL CHECK(travel_hours >= 0),
                    road_quality TEXT NOT NULL DEFAULT 'road',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(campaign_id,from_id,to_id),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS combat_positions (
                    campaign_id TEXT NOT NULL,
                    combat_id TEXT NOT NULL,
                    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('character','npc')),
                    actor_id TEXT NOT NULL,
                    x INTEGER NOT NULL,
                    y INTEGER NOT NULL,
                    cover TEXT NOT NULL DEFAULT 'none' CHECK(cover IN ('none','half','three_quarters','total')),
                    PRIMARY KEY(campaign_id,combat_id,actor_kind,actor_id),
                    FOREIGN KEY(campaign_id,combat_id) REFERENCES combats(campaign_id,id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS combat_terrain (
                    campaign_id TEXT NOT NULL,
                    combat_id TEXT NOT NULL,
                    x INTEGER NOT NULL,
                    y INTEGER NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'open',
                    blocks_los INTEGER NOT NULL DEFAULT 0,
                    difficult INTEGER NOT NULL DEFAULT 0,
                    hazard_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(campaign_id,combat_id,x,y),
                    FOREIGN KEY(campaign_id,combat_id) REFERENCES combats(campaign_id,id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    world_time TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    region TEXT,
                    actor_id TEXT,
                    target_id TEXT,
                    summary TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    sensitivity TEXT NOT NULL DEFAULT 'PUBLIC'
                        CHECK(sensitivity IN ('PUBLIC','PRIVATE','SECRET')),
                    scope_type TEXT NOT NULL DEFAULT 'WORLD'
                        CHECK(scope_type IN ('WORLD','ENTITY','GM','SYSTEM')),
                    principal_kind TEXT,
                    principal_id TEXT,
                    causal_parent_event_id INTEGER,
                    causal_root_event_id INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE,
                    FOREIGN KEY(causal_parent_event_id) REFERENCES events(id) ON DELETE SET NULL,
                    FOREIGN KEY(causal_root_event_id) REFERENCES events(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_campaign_revision
                    ON events(campaign_id, revision DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_events_campaign_region
                    ON events(campaign_id, region, id DESC);
                CREATE TABLE IF NOT EXISTS visual_preferences (
                    campaign_id TEXT PRIMARY KEY,
                    auto_images INTEGER NOT NULL DEFAULT 1,
                    scene_start INTEGER NOT NULL DEFAULT 1,
                    battle_start INTEGER NOT NULL DEFAULT 1,
                    new_location INTEGER NOT NULL DEFAULT 1,
                    event_choice INTEGER NOT NULL DEFAULT 1,
                    character_reference INTEGER NOT NULL DEFAULT 1,
                    major_npc_reference INTEGER NOT NULL DEFAULT 1,
                    art_style TEXT NOT NULL DEFAULT 'cinematic setting-authentic illustration',
                    additional_instructions TEXT NOT NULL DEFAULT '',
                    negative_instructions TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS image_generations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    scene_key TEXT NOT NULL,
                    location_id TEXT,
                    combat_id TEXT,
                    title TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    aspect_ratio TEXT NOT NULL DEFAULT '4:3',
                    status TEXT NOT NULL DEFAULT 'generated',
                    image_ref TEXT,
                    visual_context_json TEXT NOT NULL DEFAULT '{}',
                    source_revision INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(campaign_id, trigger_type, scene_key),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS visual_profiles (
                    campaign_id TEXT NOT NULL,
                    entity_kind TEXT NOT NULL CHECK(entity_kind IN ('character','npc')),
                    entity_id TEXT NOT NULL,
                    profile_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(campaign_id, entity_kind, entity_id),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS entity_visual_references (
                    campaign_id TEXT NOT NULL,
                    entity_kind TEXT NOT NULL CHECK(entity_kind IN ('character','npc')),
                    entity_id TEXT NOT NULL,
                    image_ref TEXT,
                    reference_prompt TEXT NOT NULL DEFAULT '',
                    visual_fingerprint_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    source_scene_key TEXT,
                    source_revision INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(campaign_id,entity_kind,entity_id),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS visual_states (
                    campaign_id TEXT NOT NULL,
                    scope_type TEXT NOT NULL CHECK(scope_type IN ('location','scene','combat')),
                    scope_id TEXT NOT NULL,
                    state_json TEXT NOT NULL DEFAULT '{}',
                    source_revision INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(campaign_id, scope_type, scope_id),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_characters_location
                    ON characters(campaign_id, location);
                CREATE INDEX IF NOT EXISTS idx_npcs_location
                    ON npcs(campaign_id, location);
                CREATE INDEX IF NOT EXISTS idx_image_generations_campaign
                    ON image_generations(campaign_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_visual_profiles_campaign
                    ON visual_profiles(campaign_id, entity_kind, entity_id);
                CREATE INDEX IF NOT EXISTS idx_visual_states_campaign
                    ON visual_states(campaign_id, scope_type, scope_id);
                CREATE INDEX IF NOT EXISTS idx_location_links_from
                    ON location_links(campaign_id, from_id, to_id);
                CREATE INDEX IF NOT EXISTS idx_combat_positions
                    ON combat_positions(campaign_id, combat_id, actor_kind, actor_id);
                """
            )
            db.executescript(SIM_SCHEMA)
            db.executescript(LAYER_SCHEMA)
            db.executescript(AUTHORING_SCHEMA)
            db.executescript(RULES_SCHEMA)
            db.executescript(NPC_LIFE_SCHEMA)
            db.executescript(WORLD_SYSTEMS_SCHEMA)
            db.executescript(ENVIRONMENT_SCHEMA)
            prepare_mechanism_schema_db(db)
            db.executescript(MECHANISM_SCHEMA)
            verify_mechanism_schema_db(db)
            migrate_economy_schema_db(db)
            db.executescript(ECONOMY_SCHEMA)
            db.executescript(POPULATION_SCHEMA)
            db.executescript(INCIDENT_SCHEMA)
            PoliticsKernel(self).install_schema_db(db)
            prepare_agency_schema_db(db)
            db.executescript(QUEST_SCHEMA)
            db.executescript(TURN_ROUTER_SCHEMA)
            db.executescript(NARRATIVE_SCHEMA)
            install_companion_schema_db(db, int(datetime.now(timezone.utc).timestamp()))
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS we42_schema_features (
                    feature_id TEXT PRIMARY KEY,
                    feature_version TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            # Forward-compatible migration for databases created by v3.0.
            image_columns = {r["name"] for r in db.execute("PRAGMA table_info(image_generations)").fetchall()}
            if "visual_context_json" not in image_columns:
                db.execute("ALTER TABLE image_generations ADD COLUMN visual_context_json TEXT NOT NULL DEFAULT '{}'")
            if "source_revision" not in image_columns:
                db.execute("ALTER TABLE image_generations ADD COLUMN source_revision INTEGER NOT NULL DEFAULT 0")
            location_columns = {r["name"] for r in db.execute("PRAGMA table_info(locations)").fetchall()}
            if "x" not in location_columns:
                db.execute("ALTER TABLE locations ADD COLUMN x REAL")
            if "y" not in location_columns:
                db.execute("ALTER TABLE locations ADD COLUMN y REAL")
            combat_columns = {r["name"] for r in db.execute("PRAGMA table_info(combats)").fetchall()}
            if "grid_width" not in combat_columns:
                db.execute("ALTER TABLE combats ADD COLUMN grid_width INTEGER NOT NULL DEFAULT 20")
            if "grid_height" not in combat_columns:
                db.execute("ALTER TABLE combats ADD COLUMN grid_height INTEGER NOT NULL DEFAULT 20")
            need_columns = {r["name"] for r in db.execute("PRAGMA table_info(npc_needs)").fetchall()}
            if "curve" not in need_columns:
                db.execute("ALTER TABLE npc_needs ADD COLUMN curve TEXT NOT NULL DEFAULT 'quadratic'")
            action_columns = {r["name"] for r in db.execute("PRAGMA table_info(npc_actions)").fetchall()}
            if "requirements_json" not in action_columns:
                db.execute("ALTER TABLE npc_actions ADD COLUMN requirements_json TEXT NOT NULL DEFAULT '{}'")
            if "cost_hours" not in action_columns:
                db.execute("ALTER TABLE npc_actions ADD COLUMN cost_hours REAL NOT NULL DEFAULT 8")
            if "tags_json" not in action_columns:
                db.execute("ALTER TABLE npc_actions ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'")
            agent_columns = {r["name"] for r in db.execute("PRAGMA table_info(sim_agent_state)").fetchall()}
            if "committed_until" not in agent_columns:
                db.execute("ALTER TABLE sim_agent_state ADD COLUMN committed_until TEXT")
            inventory_columns = {r["name"] for r in db.execute("PRAGMA table_info(inventories)").fetchall()}
            if "metadata_json" not in inventory_columns:
                db.execute("ALTER TABLE inventories ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
            reaction_columns = {r["name"] for r in db.execute("PRAGMA table_info(sim_reactions)").fetchall()}
            if "selector_json" not in reaction_columns:
                db.execute("ALTER TABLE sim_reactions ADD COLUMN selector_json TEXT NOT NULL DEFAULT '{}'")
            if "probability" not in reaction_columns:
                db.execute("ALTER TABLE sim_reactions ADD COLUMN probability REAL NOT NULL DEFAULT 1.0")
            if "repeat_policy" not in reaction_columns:
                db.execute("ALTER TABLE sim_reactions ADD COLUMN repeat_policy TEXT NOT NULL DEFAULT 'once_per_cascade'")
            if "repeat_limit" not in reaction_columns:
                db.execute("ALTER TABLE sim_reactions ADD COLUMN repeat_limit INTEGER NOT NULL DEFAULT 1")
            event_columns = {r["name"] for r in db.execute("PRAGMA table_info(events)").fetchall()}
            if "sensitivity" not in event_columns:
                db.execute("ALTER TABLE events ADD COLUMN sensitivity TEXT NOT NULL DEFAULT 'PUBLIC'")
            if "scope_type" not in event_columns:
                db.execute("ALTER TABLE events ADD COLUMN scope_type TEXT NOT NULL DEFAULT 'WORLD'")
            if "principal_kind" not in event_columns:
                db.execute("ALTER TABLE events ADD COLUMN principal_kind TEXT")
            if "principal_id" not in event_columns:
                db.execute("ALTER TABLE events ADD COLUMN principal_id TEXT")
            if "causal_parent_event_id" not in event_columns:
                db.execute("ALTER TABLE events ADD COLUMN causal_parent_event_id INTEGER")
            if "causal_root_event_id" not in event_columns:
                db.execute("ALTER TABLE events ADD COLUMN causal_root_event_id INTEGER")
            incident_columns = {
                r["name"]
                for r in db.execute("PRAGMA table_info(incident_instances)").fetchall()
            }
            if "sensitivity" not in incident_columns:
                # Existing records predate an immutable visibility snapshot. They
                # fail closed until a trusted migration explicitly classifies them.
                db.execute(
                    "ALTER TABLE incident_instances ADD COLUMN sensitivity "
                    "TEXT NOT NULL DEFAULT 'SECRET'"
                )
            if "visibility_scope" not in incident_columns:
                db.execute(
                    "ALTER TABLE incident_instances ADD COLUMN visibility_scope "
                    "TEXT NOT NULL DEFAULT 'GM'"
                )
            quest_runtime_columns = {
                r["name"]
                for r in db.execute(
                    "PRAGMA table_info(quest_runtime_instances)"
                ).fetchall()
            }
            if "start_event_id" not in quest_runtime_columns:
                db.execute(
                    "ALTER TABLE quest_runtime_instances ADD COLUMN start_event_id "
                    "INTEGER NOT NULL DEFAULT 0"
                )
                # Existing runtime quests have no trustworthy creation cursor.
                # Fail closed against retroactive history by starting at the
                # campaign's current event high-water mark.
                db.execute(
                    """UPDATE quest_runtime_instances
                       SET start_event_id=COALESCE((
                           SELECT MAX(e.id) FROM events e
                           WHERE e.campaign_id=quest_runtime_instances.campaign_id
                       ),0)"""
                )
            db.execute(
                """CREATE INDEX IF NOT EXISTS idx_events_visibility
                   ON events(campaign_id,sensitivity,scope_type,principal_kind,principal_id,id DESC)"""
            )
            db.execute(
                """CREATE INDEX IF NOT EXISTS idx_events_causal_parent
                   ON events(campaign_id,causal_parent_event_id,id)"""
            )
            for table in ("characters", "npcs"):
                cols = {r["name"] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
                if "status" not in cols:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN status TEXT NOT NULL DEFAULT 'alive'")
                if "died_on" not in cols:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN died_on TEXT")
            npc_columns = {r["name"] for r in db.execute("PRAGMA table_info(npcs)").fetchall()}
            if "archetype_id" not in npc_columns:
                db.execute("ALTER TABLE npcs ADD COLUMN archetype_id TEXT")
            if "materialized" not in npc_columns:
                db.execute("ALTER TABLE npcs ADD COLUMN materialized INTEGER NOT NULL DEFAULT 0")
            faction_columns = {r["name"] for r in db.execute("PRAGMA table_info(factions)").fetchall()}
            if "leader_id" not in faction_columns:
                db.execute("ALTER TABLE factions ADD COLUMN leader_id TEXT")
            if "realm_id" not in location_columns:
                db.execute("ALTER TABLE locations ADD COLUMN realm_id TEXT")
            lifecycle_columns = {r["name"] for r in db.execute("PRAGMA table_info(npc_lifecycle)").fetchall()}
            if "fertility_json" not in lifecycle_columns:
                db.execute("ALTER TABLE npc_lifecycle ADD COLUMN fertility_json TEXT NOT NULL DEFAULT '{}'")
            if "heir_id" not in lifecycle_columns:
                db.execute("ALTER TABLE npc_lifecycle ADD COLUMN heir_id TEXT")
            if "last_birth_on" not in lifecycle_columns:
                db.execute("ALTER TABLE npc_lifecycle ADD COLUMN last_birth_on TEXT")
            npc_columns = {r["name"] for r in db.execute("PRAGMA table_info(npcs)").fetchall()}
            if "importance" not in npc_columns:
                db.execute("ALTER TABLE npcs ADD COLUMN importance TEXT NOT NULL DEFAULT 'minor'")
            visual_pref_columns = {r["name"] for r in db.execute("PRAGMA table_info(visual_preferences)").fetchall()}
            if "character_reference" not in visual_pref_columns:
                db.execute("ALTER TABLE visual_preferences ADD COLUMN character_reference INTEGER NOT NULL DEFAULT 1")
            if "major_npc_reference" not in visual_pref_columns:
                db.execute("ALTER TABLE visual_preferences ADD COLUMN major_npc_reference INTEGER NOT NULL DEFAULT 1")
            # Restore the v4.0.1 authorization-aware compiler projection that
            # was absent from the narrative ZIPs. Typed fact/belief tables
            # remain authoritative; the claim store is an additive index.
            db.execute(
                """INSERT OR IGNORE INTO knowledge_claims(
                       campaign_id,claim_id,subject_key,predicate,object_json,authority,
                       principal_scope_type,principal_kind,principal_id,valid_from,valid_until,
                       learned_revision,superseded_revision,source_event_id,confidence,sensitivity,status,updated_at)
                   SELECT f.campaign_id,'fact:'||f.fact_id,f.subject_key,f.predicate,f.object_value_json,'WORLD_TRUTH',
                          'WORLD',NULL,NULL,f.valid_from,f.valid_to,
                          COALESCE((SELECT revision FROM events e WHERE e.campaign_id=f.campaign_id AND e.id=f.source_event_id),0),
                          NULL,f.source_event_id,f.confidence,'NORMAL',f.status,f.updated_at
                   FROM we4_facts f"""
            )
            db.execute(
                """INSERT OR IGNORE INTO knowledge_claims(
                       campaign_id,claim_id,subject_key,predicate,object_json,authority,
                       principal_scope_type,principal_kind,principal_id,valid_from,valid_until,
                       learned_revision,superseded_revision,source_event_id,confidence,sensitivity,status,updated_at)
                   SELECT b.campaign_id,'belief:'||b.believer_key||':'||b.fact_id,f.subject_key,f.predicate,b.belief_value_json,
                          CASE WHEN b.believer_key LIKE 'npc:%' THEN 'NPC_BELIEF' ELSE 'PLAYER_KNOWLEDGE' END,
                          'ENTITY',substr(b.believer_key,1,instr(b.believer_key,':')-1),
                          substr(b.believer_key,instr(b.believer_key,':')+1),
                          b.acquired_world_time,NULL,0,NULL,f.source_event_id,b.confidence,'PRIVATE',
                          CASE WHEN b.status='believes' THEN 'active' ELSE 'disputed' END,b.updated_at
                   FROM we4_beliefs b JOIN we4_facts f
                     ON f.campaign_id=b.campaign_id AND f.fact_id=b.fact_id"""
            )
            NarrativeKernel(self).migrate_v41_rows_db(db)
            now = self._now()
            db.execute(
                """INSERT INTO we42_schema_features(feature_id,feature_version,applied_at,details_json)
                   VALUES('context_compiler_hardened','4.0.1',?,'{"authorization_first":true,"fixed_point_scoring":true,"post_commit_recompile":true}')
                   ON CONFLICT(feature_id) DO NOTHING""",
                (now,),
            )
            db.execute(
                """INSERT INTO we42_schema_features(feature_id,feature_version,applied_at,details_json)
                   VALUES('narrative_runtime','4.2.0',?,'{"base":"4.0.2","default_mode":"off","packet":"NRP-1.1","receipt":"NQR-1.1"}')
                   ON CONFLICT(feature_id) DO NOTHING""",
                (now,),
            )
            db.execute(
                """INSERT INTO we42_schema_features(feature_id,feature_version,applied_at,details_json)
                   VALUES('output_companion_hardening','4.3.0',?,'{"packet":"NRP-1.2","receipt":"NQR-1.2","private_validation":true,"public_projection":"WETP-PUBLIC-1.0","companion":"presentation_only"}')
                   ON CONFLICT(feature_id) DO UPDATE SET
                       feature_version=excluded.feature_version,
                       applied_at=excluded.applied_at,
                       details_json=excluded.details_json""",
                (now,),
            )
            db.execute(
                """INSERT INTO we42_schema_features(feature_id,feature_version,applied_at,details_json)
                   VALUES('procedural_desktop_companion','5.0.0',?,'{"generation":"WEGEN-2.0","accepts_staged":["WEGEN-1.0","WEGEN-1.1","WEGEN-1.2","WEGEN-2.0"],"stage_only":true,"dry_run_required":true,"atomic_promotion":true,"desktop_projection":"WE-DESKTOP-5.0.0","local_first_endpoint":true,"runtime_domains":["quests","agency","politics","incidents"]}')
                   ON CONFLICT(feature_id) DO UPDATE SET
                       feature_version=excluded.feature_version,
                       applied_at=excluded.applied_at,
                       details_json=excluded.details_json""",
                (now,),
            )
            db.execute(
                """INSERT INTO we42_schema_features(feature_id,feature_version,applied_at,details_json)
                   VALUES('environment_consequence_runtime','4.5.0',?,'{"sparse":true,"weather":true,"season":true,"materials":true,"effects":true,"propagation":true,"disasters":true,"world_scene_lod":true,"public_projection":"WE-ENV-PUBLIC-1.0"}')
                   ON CONFLICT(feature_id) DO UPDATE SET
                       feature_version=excluded.feature_version,
                       applied_at=excluded.applied_at,
                       details_json=excluded.details_json""",
                (now,),
            )
            db.execute(
                """INSERT INTO we42_schema_features(feature_id,feature_version,applied_at,details_json)
                   VALUES('pbem_public_boundary','4.7.0',?,'{"policy":"PBEM-2.2","public_actor":"character","gameplay_gateway":"resolveTurn","gpt_actions":5,"operator_key_separate":true,"fpc_server_derived":true,"economy_actor_bound":true,"population_local_only":true}')
                   ON CONFLICT(feature_id) DO UPDATE SET
                       feature_version=excluded.feature_version,
                       applied_at=excluded.applied_at,
                       details_json=excluded.details_json""",
                (now,),
            )
            db.execute(
                """INSERT INTO we42_schema_features(feature_id,feature_version,applied_at,details_json)
                   VALUES('canonical_mechanism_contract','5.0.0',?,'{"contract":"MOP-1.0","phase":"transaction_aware_runtime","trusted_internal":true,"binding_refs":true,"tamper_evident_receipts":true,"canonical_effect_callback":true,"scoped_idempotency":true,"scheduler_step_identity":true}')
                   ON CONFLICT(feature_id) DO UPDATE SET
                       feature_version=excluded.feature_version,
                       applied_at=excluded.applied_at,
                       details_json=excluded.details_json""",
                (now,),
            )
            db.execute(
                """INSERT INTO we42_schema_features(feature_id,feature_version,applied_at,details_json)
                   VALUES('economy_production_logistics_runtime','4.7.0',?,'{"finite_ledgers":true,"actor_scoped_idempotency":true,"public_market_visibility":true,"canonical_hour_steps":true,"population_labor_seam":true}')
                   ON CONFLICT(feature_id) DO UPDATE SET
                       feature_version=excluded.feature_version,
                       applied_at=excluded.applied_at,
                       details_json=excluded.details_json""",
                (now,),
            )
            db.execute(
                """INSERT INTO we42_schema_features(feature_id,feature_version,applied_at,details_json)
                   VALUES('population_lifecycle_settlement_runtime','4.7.0',?,'{"aggregate_cohorts":true,"households":true,"labor":true,"services":true,"migration":true,"public_projection":"actor_local"}')
                   ON CONFLICT(feature_id) DO UPDATE SET
                       feature_version=excluded.feature_version,
                       applied_at=excluded.applied_at,
                       details_json=excluded.details_json""",
                (now,),
            )
            db.execute(
                """INSERT INTO we42_schema_features(feature_id,feature_version,applied_at,details_json)
                   VALUES('event_incident_runtime','5.0.0',?,'{"schema_stage":21,"event_visibility":true,"causal_provenance":true,"derived_pressures":true,"deterministic_selection":true,"mop_execution":"in_transaction","history_authority":"events"}')
                   ON CONFLICT(feature_id) DO UPDATE SET
                       feature_version=excluded.feature_version,
                       applied_at=excluded.applied_at,
                       details_json=excluded.details_json""",
                (now,),
            )
            db.execute(
                """INSERT INTO we42_schema_features(feature_id,feature_version,applied_at,details_json)
                   VALUES('politics_commitment_runtime','5.0.0',?,'{"schema_stage":22,"commitment_ledger":true,"belief_scoped_strategy":true,"diplomacy":true,"treaties":true,"territorial_control":true,"military_logistics":true,"legal_hooks":true,"actor_scoped_projection":true}')
                   ON CONFLICT(feature_id) DO UPDATE SET
                       feature_version=excluded.feature_version,
                       applied_at=excluded.applied_at,
                       details_json=excluded.details_json""",
                (now,),
            )
            db.execute(
                """INSERT INTO we42_schema_features(feature_id,feature_version,applied_at,details_json)
                   VALUES('actor_agency_runtime','5.0.0',?,'{"schema_stage":23,"contract":"AGENCY-1.0","mop_planning":true,"belief_scoped":true,"private_cognition":true,"bounded_daily_step":true}')
                   ON CONFLICT(feature_id) DO UPDATE SET
                       feature_version=excluded.feature_version,
                       applied_at=excluded.applied_at,
                       details_json=excluded.details_json""",
                (now,),
            )
            db.execute(
                """INSERT INTO we42_schema_features(feature_id,feature_version,applied_at,details_json)
                   VALUES('quest_graph_runtime','5.0.0',?,'{"schema_stage":24,"event_cursor":true,"typed_conditions":true,"transition_receipts":true,"template_binding":true,"mop_predicates":true}')
                   ON CONFLICT(feature_id) DO UPDATE SET
                       feature_version=excluded.feature_version,
                       applied_at=excluded.applied_at,
                       details_json=excluded.details_json""",
                (now,),
            )
            db.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")

    def _default_visual_preferences(self) -> dict[str, Any]:
        return {
            "auto_images": True,
            "scene_start": True,
            "battle_start": True,
            "new_location": True,
            "event_choice": True,
            "character_reference": True,
            "major_npc_reference": True,
            "art_style": "cinematic setting-authentic illustration",
            "additional_instructions": "Show the current scene clearly, with readable composition, setting-authentic environmental detail, and strong environmental storytelling.",
            "negative_instructions": "UI overlays, text blocks, watermarks, or anachronistic elements not established by the World Bible.",
        }

    def ensure_campaign(
        self,
        campaign_id: str = "default",
        name: str = "World Engine Campaign",
        world_time: str | None = None,
    ) -> dict[str, Any]:
        campaign_id = self._clean_id(campaign_id)
        now = self._now()
        world_time = world_time or datetime(1492, 1, 1, 8, 0, tzinfo=timezone.utc).isoformat()
        datetime.fromisoformat(world_time)
        with self._write_db() as db:
            db.execute(
                """INSERT INTO campaigns(id,name,world_time,weather,revision,settings_json,created_at,updated_at)
                   VALUES(?,?,?,?,0,'{}',?,?)
                   ON CONFLICT(id) DO NOTHING""",
                (campaign_id, name[:200], world_time, "clear", now, now),
            )
            TurnRouter(self).seed_defaults_db(db, campaign_id)
            EnvironmentKernel(self).seed_defaults_db(db, campaign_id)
            EconomyKernel(self).seed_defaults_db(db, campaign_id)
            PopulationKernel(self).seed_defaults_db(db, campaign_id)
            PoliticsKernel(self).seed_defaults_db(db, campaign_id)
        return self.get_campaign(campaign_id)

    def get_campaign(self, campaign_id: str = "default") -> dict[str, Any]:
        campaign_id = self._clean_id(campaign_id)
        with self._db() as db:
            row = db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if not row:
            raise KeyError(f"unknown campaign: {campaign_id}")
        data = dict(row)
        data["settings"] = self._loads(data.pop("settings_json"))
        return data

    def _ensure_campaign_exists(self, campaign_id: str) -> None:
        try:
            self.get_campaign(campaign_id)
        except KeyError:
            self.ensure_campaign(campaign_id)

    @staticmethod
    def _clean_id(value: str) -> str:
        value = value.strip()
        if not value or len(value) > 100 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
            raise ValueError("IDs must be 1-100 characters using letters, digits, _, ., :, or -")
        return value

    def _canonize_materialized_npc_db(self, db: sqlite3.Connection, campaign_id: str, npc_id: str, reason: str) -> bool:
        """Lock generated named content once authoritative gameplay touches it.

        Off-screen simulation writes directly through SimulationKernel and therefore do
        not call this helper; only player/API gameplay mutations canonize materialised NPCs.
        """
        try:
            row = db.execute("SELECT materialized FROM npcs WHERE campaign_id=? AND id=?", (campaign_id, npc_id)).fetchone()
            if not row or not bool(row["materialized"]):
                return False
            db.execute(
                """INSERT INTO canon_locks(campaign_id,object_kind,object_id,reason,locked_at) VALUES(?,'npc',?,?,?)
                   ON CONFLICT(campaign_id,object_kind,object_id) DO NOTHING""",
                (campaign_id, npc_id, reason[:500], self._now()),
            )
            return True
        except sqlite3.OperationalError:
            return False

    def _next_revision(self, db: sqlite3.Connection, campaign_id: str) -> int:
        now = self._now()
        db.execute(
            "UPDATE campaigns SET revision=revision+1,updated_at=? WHERE id=?",
            (now, campaign_id),
        )
        row = db.execute("SELECT revision FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if not row:
            raise KeyError(f"unknown campaign: {campaign_id}")
        return int(row["revision"])

    def _insert_event(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        revision: int,
        event_type: str,
        summary: str,
        *,
        region: str | None = None,
        actor_id: str | None = None,
        target_id: str | None = None,
        payload: dict[str, Any] | None = None,
        world_time_override: str | None = None,
        sensitivity: str = "PUBLIC",
        scope_type: str = "WORLD",
        principal_kind: str | None = None,
        principal_id: str | None = None,
        causal_parent_event_id: int | None = None,
    ) -> int:
        sensitivity = str(sensitivity).upper()
        scope_type = str(scope_type).upper()
        if sensitivity not in {"PUBLIC", "PRIVATE", "SECRET"}:
            raise ValueError("event sensitivity must be PUBLIC, PRIVATE, or SECRET")
        if scope_type not in {"WORLD", "ENTITY", "GM", "SYSTEM"}:
            raise ValueError("event scope_type must be WORLD, ENTITY, GM, or SYSTEM")
        if scope_type == "ENTITY" and (not principal_kind or not principal_id):
            raise ValueError("ENTITY event scope requires principal_kind and principal_id")
        causal_root_event_id = None
        if causal_parent_event_id is not None:
            parent = db.execute(
                """SELECT id,causal_root_event_id FROM events
                   WHERE campaign_id=? AND id=?""",
                (campaign_id, int(causal_parent_event_id)),
            ).fetchone()
            if not parent:
                raise ValueError("causal parent event must exist in the same campaign")
            causal_root_event_id = int(parent["causal_root_event_id"] or parent["id"])
        world_time = world_time_override or db.execute("SELECT world_time FROM campaigns WHERE id=?", (campaign_id,)).fetchone()["world_time"]
        cur = db.execute(
            """INSERT INTO events(
                   campaign_id,revision,world_time,event_type,region,actor_id,target_id,
                   summary,payload_json,sensitivity,scope_type,principal_kind,principal_id,
                   causal_parent_event_id,causal_root_event_id,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                campaign_id,
                revision,
                world_time,
                event_type[:80],
                region,
                actor_id,
                target_id,
                summary[:2000],
                self._dumps(payload or {}),
                sensitivity,
                scope_type,
                principal_kind,
                principal_id,
                causal_parent_event_id,
                causal_root_event_id,
                self._now(),
            ),
        )
        event_id = int(cur.lastrowid)
        if causal_root_event_id is None:
            db.execute(
                "UPDATE events SET causal_root_event_id=? WHERE id=?",
                (event_id, event_id),
            )
        return event_id

    # ---------- dice / core resolution ----------

    def roll_dice(self, expression: str) -> DiceResult:
        """Standalone dice helper.

        Gameplay-facing checks/attacks use the campaign-seeded transaction helpers
        below.  This method remains for local utilities/tests that intentionally do
        not participate in campaign replay.
        """
        m = _DICE_RE.fullmatch(expression)
        if not m:
            raise ValueError("Supported dice format: NdM[+/-K] or integer; example 1d20+5")
        count_s, sides_s, constant_s, mod_s = m.groups()
        modifier = int((mod_s or "0").replace(" ", ""))
        if constant_s is not None:
            total = int(constant_s) + modifier
            return DiceResult(expression=expression, rolls=(), modifier=modifier, total=total)
        count, sides = int(count_s), int(sides_s)
        if not 1 <= count <= 100:
            raise ValueError("Dice count must be 1..100")
        if not 2 <= sides <= 10000:
            raise ValueError("Die sides must be 2..10000")
        rolls = tuple(self.rng.randint(1, sides) for _ in range(count))
        return DiceResult(expression=expression, rolls=rolls, modifier=modifier, total=sum(rolls) + modifier)

    def _roll_dice_db(self, db: sqlite3.Connection, campaign_id: str, expression: str, namespace: str) -> DiceResult:
        m = _DICE_RE.fullmatch(expression)
        if not m:
            raise ValueError("Supported dice format: NdM[+/-K] or integer; example 1d20+5")
        count_s, sides_s, constant_s, mod_s = m.groups()
        modifier = int((mod_s or "0").replace(" ", ""))
        if constant_s is not None:
            total = int(constant_s) + modifier
            return DiceResult(expression=expression, rolls=(), modifier=modifier, total=total)
        count, sides = int(count_s), int(sides_s)
        if not 1 <= count <= 100:
            raise ValueError("Dice count must be 1..100")
        if not 2 <= sides <= 10000:
            raise ValueError("Die sides must be 2..10000")
        kernel = SimulationKernel(self)
        rolls = []
        for idx in range(count):
            r = kernel._rand(db, campaign_id, f"gameplay:{namespace}:{idx}")
            rolls.append(1 + min(sides - 1, int(r * sides)))
        return DiceResult(expression=expression, rolls=tuple(rolls), modifier=modifier, total=sum(rolls) + modifier)

    def _resolve_check_db(self, db: sqlite3.Connection, campaign_id: str, modifier: int, dc: int, mode: str, *, namespace: str) -> dict[str, Any]:
        if not -100 <= modifier <= 100:
            raise ValueError("modifier must be -100..100")
        if not 1 <= dc <= 100:
            raise ValueError("dc must be 1..100")
        if mode not in {"normal", "advantage", "disadvantage"}:
            raise ValueError("mode must be normal, advantage, or disadvantage")
        rolls = [self._roll_dice_db(db, campaign_id, "1d20", namespace + ":d20a").total]
        if mode != "normal":
            rolls.append(self._roll_dice_db(db, campaign_id, "1d20", namespace + ":d20b").total)
        natural = rolls[0] if mode == "normal" else (max(rolls) if mode == "advantage" else min(rolls))
        total = natural + modifier
        return {
            "mode": mode,
            "d20_rolls": rolls,
            "natural": natural,
            "modifier": modifier,
            "total": total,
            "dc": dc,
            "success": total >= dc,
        }

    def resolve_check(self, modifier: int, dc: int, mode: str = "normal", campaign_id: str = "default") -> dict[str, Any]:
        campaign_id = self._clean_id(campaign_id)
        self._ensure_campaign_exists(campaign_id)
        with self._write_db() as db:
            result = self._resolve_check_db(db, campaign_id, modifier, dc, mode, namespace="check")
            rev = self._next_revision(db, campaign_id)
            self._insert_event(db, campaign_id, rev, "check", f"Resolved check vs DC {dc}.", payload={"check": result})
        result["campaign_id"] = campaign_id
        result["revision"] = rev
        return result

    def _roll_damage(self, expression: str, critical: bool) -> dict[str, Any]:
        m = _DICE_RE.fullmatch(expression)
        if not m:
            raise ValueError("damage_expression must use NdM[+/-K], e.g. 1d8+3")
        count_s, sides_s, constant_s, mod_s = m.groups()
        if constant_s is not None:
            result = self.roll_dice(expression)
            return result.as_dict() | {"critical_dice_doubled": False, "critical_dice_clamped": False}
        count, sides = int(count_s), int(sides_s)
        modifier = int((mod_s or "0").replace(" ", ""))
        requested_count = count * 2 if critical else count
        actual_count = min(100, requested_count)
        result = self.roll_dice(f"{actual_count}d{sides}{modifier:+d}")
        return result.as_dict() | {"critical_dice_doubled": critical, "critical_dice_clamped": actual_count != requested_count, "requested_dice_count": requested_count}

    def _roll_damage_db(self, db: sqlite3.Connection, campaign_id: str, expression: str, critical: bool, *, namespace: str) -> dict[str, Any]:
        m = _DICE_RE.fullmatch(expression)
        if not m:
            raise ValueError("damage_expression must use NdM[+/-K], e.g. 1d8+3")
        count_s, sides_s, constant_s, mod_s = m.groups()
        if constant_s is not None:
            result = self._roll_dice_db(db, campaign_id, expression, namespace)
            return result.as_dict() | {"critical_dice_doubled": False, "critical_dice_clamped": False}
        count, sides = int(count_s), int(sides_s)
        modifier = int((mod_s or "0").replace(" ", ""))
        requested_count = count * 2 if critical else count
        actual_count = min(100, requested_count)
        result = self._roll_dice_db(db, campaign_id, f"{actual_count}d{sides}{modifier:+d}", namespace)
        return result.as_dict() | {"critical_dice_doubled": critical, "critical_dice_clamped": actual_count != requested_count, "requested_dice_count": requested_count}

    # ---------- entity state ----------

    def _decode_character_row(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        for field in ("abilities", "conditions", "resources", "inventory", "notes"):
            data[field] = self._loads(data.pop(field + "_json"))
        return data

    def _decode_npc_row(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        for field in ("stats", "conditions", "beliefs", "goals", "routine", "memory"):
            data[field] = self._loads(data.pop(field + "_json"))
        return data

    def _decode_faction_row(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        data["goals"] = self._loads(data.pop("goals_json"))
        data["state"] = self._loads(data.pop("state_json"))
        return data

    def _decode_combat_row_db(self, db: sqlite3.Connection, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        data["participants"] = self._loads(data.pop("participants_json"))
        data["initiative"] = self._loads(data.pop("initiative_json"))
        if data["initiative"]:
            data["current_turn"] = data["initiative"][int(data["turn_index"])]
        else:
            data["current_turn"] = None
        positions = [dict(r) for r in db.execute("SELECT * FROM combat_positions WHERE campaign_id=? AND combat_id=? ORDER BY actor_kind,actor_id", (data["campaign_id"], data["id"])).fetchall()]
        terrain = []
        for r in db.execute("SELECT * FROM combat_terrain WHERE campaign_id=? AND combat_id=? ORDER BY y,x", (data["campaign_id"], data["id"])).fetchall():
            x = dict(r); x["blocks_los"] = bool(x["blocks_los"]); x["difficult"] = bool(x["difficult"]); x["hazard"] = self._loads(x.pop("hazard_json")); terrain.append(x)
        data["positions"] = positions
        data["terrain"] = terrain
        return data

    def _get_character_db(self, db: sqlite3.Connection, campaign_id: str, character_id: str) -> dict[str, Any]:
        row = db.execute("SELECT * FROM characters WHERE campaign_id=? AND id=?", (campaign_id, character_id)).fetchone()
        if not row:
            raise KeyError(f"unknown character: {character_id}")
        return self._decode_character_row(row)

    def _get_npc_db(self, db: sqlite3.Connection, campaign_id: str, npc_id: str) -> dict[str, Any]:
        row = db.execute("SELECT * FROM npcs WHERE campaign_id=? AND id=?", (campaign_id, npc_id)).fetchone()
        if not row:
            raise KeyError(f"unknown npc: {npc_id}")
        return self._decode_npc_row(row)

    def _get_actor_db(self, db: sqlite3.Connection, campaign_id: str, kind: str, actor_id: str) -> dict[str, Any]:
        if kind == "character":
            return self._get_character_db(db, campaign_id, actor_id)
        if kind == "npc":
            return self._get_npc_db(db, campaign_id, actor_id)
        raise ValueError("kind must be character or npc")

    def _get_faction_db(self, db: sqlite3.Connection, campaign_id: str, faction_id: str) -> dict[str, Any]:
        row = db.execute("SELECT * FROM factions WHERE campaign_id=? AND id=?", (campaign_id, faction_id)).fetchone()
        if not row:
            raise KeyError(f"unknown faction: {faction_id}")
        return self._decode_faction_row(row)

    def _get_combat_db(self, db: sqlite3.Connection, campaign_id: str, combat_id: str) -> dict[str, Any]:
        row = db.execute("SELECT * FROM combats WHERE campaign_id=? AND id=?", (campaign_id, combat_id)).fetchone()
        if not row:
            raise KeyError(f"unknown combat: {combat_id}")
        return self._decode_combat_row_db(db, row)

    def upsert_character(
        self,
        campaign_id: str,
        character_id: str,
        name: str,
        *,
        level: int = 1,
        hp: int = 1,
        max_hp: int = 1,
        ac: int = 10,
        location: str = "unknown",
        abilities: dict[str, int] | None = None,
        proficiency_bonus: int = 2,
        conditions: Iterable[str] = (),
        resources: dict[str, Any] | None = None,
        inventory: Iterable[dict[str, Any] | str] = (),
        notes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        campaign_id, character_id = self._clean_id(campaign_id), self._clean_id(character_id)
        self._ensure_campaign_exists(campaign_id)
        if not 1 <= level <= 20:
            raise ValueError("level must be 1..20")
        if max_hp < 1 or hp < 0 or hp > max_hp:
            raise ValueError("require 0 <= hp <= max_hp and max_hp >= 1")
        if not 1 <= ac <= 40:
            raise ValueError("ac must be 1..40")
        abilities = abilities or {}
        for key, value in abilities.items():
            if not -10 <= int(value) <= 20:
                raise ValueError(f"ability modifier {key} outside -10..20")
        now = self._now()
        with self._write_db() as db:
            db.execute(
                """INSERT INTO characters(campaign_id,id,name,level,hp,max_hp,ac,location,abilities_json,proficiency_bonus,conditions_json,resources_json,inventory_json,notes_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET
                   name=excluded.name,level=excluded.level,hp=excluded.hp,max_hp=excluded.max_hp,ac=excluded.ac,
                   location=excluded.location,abilities_json=excluded.abilities_json,proficiency_bonus=excluded.proficiency_bonus,
                   conditions_json=excluded.conditions_json,resources_json=excluded.resources_json,inventory_json=excluded.inventory_json,
                   notes_json=excluded.notes_json,updated_at=excluded.updated_at""",
                (
                    campaign_id, character_id, name[:200], level, hp, max_hp, ac, location[:200],
                    self._dumps(abilities), proficiency_bonus, self._dumps(sorted(set(conditions))),
                    self._dumps(resources or {}), self._dumps(list(inventory)), self._dumps(notes or {}), now,
                ),
            )
            floor_xp = WorldSystemsKernel.xp_threshold_for_level(level)
            db.execute(
                """INSERT INTO character_progression(campaign_id,character_id,mode,xp,pending_level,milestone_count,class_id,last_level_up_at,updated_at)
                   VALUES(?,?,'xp',?,NULL,0,NULL,NULL,?)
                   ON CONFLICT(campaign_id,character_id) DO UPDATE SET xp=MAX(character_progression.xp,excluded.xp),updated_at=excluded.updated_at""",
                (campaign_id, character_id, floor_xp, now),
            )
            rev = self._next_revision(db, campaign_id)
            self._insert_event(db, campaign_id, rev, "character_upsert", f"Character state saved: {name}", region=location, actor_id=character_id)
        return self.get_character(campaign_id, character_id)

    def get_character(self, campaign_id: str, character_id: str) -> dict[str, Any]:
        campaign_id, character_id = self._clean_id(campaign_id), self._clean_id(character_id)
        with self._db() as db:
            return self._get_character_db(db, campaign_id, character_id)

    def _actor_ledger_db(self, db: sqlite3.Connection, campaign_id: str, actor_kind: str, actor_id: str) -> dict[str, Any]:
        inventory=[]
        for row in db.execute("SELECT item_id,qty,metadata_json FROM inventories WHERE campaign_id=? AND owner_kind=? AND owner_id=? AND qty>0 ORDER BY item_id",(campaign_id,actor_kind,actor_id)).fetchall():
            item=dict(row); item["metadata"]=self._loads(item.pop("metadata_json")); inventory.append(item)
        balances={str(r["currency_key"]):float(r["amount"]) for r in db.execute("SELECT currency_key,amount FROM owner_balances WHERE campaign_id=? AND owner_kind=? AND owner_id=? ORDER BY currency_key",(campaign_id,actor_kind,actor_id)).fetchall()}
        return {"inventory_ledger":inventory,"balances":balances}

    def get_character_sheet(self, campaign_id: str, character_id: str) -> dict[str, Any]:
        campaign_id, character_id = self._clean_id(campaign_id), self._clean_id(character_id)
        with self._db() as db:
            char=self._get_character_db(db,campaign_id,character_id)
            prow=db.execute("SELECT * FROM character_progression WHERE campaign_id=? AND character_id=?",(campaign_id,character_id)).fetchone()
            if prow:
                char["progression"]=WorldSystemsKernel(self)._progression_report(char,prow)
            char.update(self._actor_ledger_db(db,campaign_id,"character",character_id))
            vrow=db.execute("SELECT profile_json FROM visual_profiles WHERE campaign_id=? AND entity_kind='character' AND entity_id=?",(campaign_id,character_id)).fetchone()
            char["visual_profile"]=self._loads(vrow["profile_json"]) if vrow else {}
            rrow=db.execute("SELECT image_ref,reference_prompt,visual_fingerprint_json,status,source_scene_key,source_revision FROM entity_visual_references WHERE campaign_id=? AND entity_kind='character' AND entity_id=?",(campaign_id,character_id)).fetchone()
            if rrow:
                ref=dict(rrow); ref["visual_fingerprint"]=self._loads(ref.pop("visual_fingerprint_json")); char["visual_reference"]=ref
            else:
                char["visual_reference"]={"status":"missing","image_ref":None}
            return char

    def get_npc_sheet(self, campaign_id: str, npc_id: str) -> dict[str, Any]:
        campaign_id, npc_id = self._clean_id(campaign_id), self._clean_id(npc_id)
        with self._write_db() as db:
            npc=self._get_npc_db(db,campaign_id,npc_id)
            npc.update(self._actor_ledger_db(db,campaign_id,"npc",npc_id))
            npc["cognition"]=NpcLifeKernel(self)._cognition_snapshot_db(db,campaign_id,npc_id)
            vrow=db.execute("SELECT profile_json FROM visual_profiles WHERE campaign_id=? AND entity_kind='npc' AND entity_id=?",(campaign_id,npc_id)).fetchone()
            npc["visual_profile"]=self._loads(vrow["profile_json"]) if vrow else {}
            rrow=db.execute("SELECT image_ref,reference_prompt,visual_fingerprint_json,status,source_scene_key,source_revision FROM entity_visual_references WHERE campaign_id=? AND entity_kind='npc' AND entity_id=?",(campaign_id,npc_id)).fetchone()
            if rrow:
                ref=dict(rrow); ref["visual_fingerprint"]=self._loads(ref.pop("visual_fingerprint_json")); npc["visual_reference"]=ref
            else:
                npc["visual_reference"]={"status":"missing","image_ref":None}
            return npc

    def upsert_npc(
        self,
        campaign_id: str,
        npc_id: str,
        name: str,
        *,
        hp: int = 1,
        max_hp: int = 1,
        ac: int = 10,
        location: str = "unknown",
        faction_id: str | None = None,
        attitude: int = 0,
        stats: dict[str, Any] | None = None,
        conditions: Iterable[str] = (),
        beliefs: Iterable[str] = (),
        goals: Iterable[str] = (),
        routine: dict[str, Any] | None = None,
        memory: Iterable[dict[str, Any] | str] = (),
        importance: str = "minor",
    ) -> dict[str, Any]:
        campaign_id, npc_id = self._clean_id(campaign_id), self._clean_id(npc_id)
        self._ensure_campaign_exists(campaign_id)
        if max_hp < 1 or hp < 0 or hp > max_hp:
            raise ValueError("require 0 <= hp <= max_hp and max_hp >= 1")
        if not 1 <= ac <= 40:
            raise ValueError("ac must be 1..40")
        if not -10 <= attitude <= 10:
            raise ValueError("attitude must be -10..10")
        importance = str(importance or "minor").strip().lower()
        if importance not in {"minor", "supporting", "major"}:
            raise ValueError("importance must be minor, supporting, or major")
        now = self._now()
        with self._write_db() as db:
            db.execute(
                """INSERT INTO npcs(campaign_id,id,name,hp,max_hp,ac,location,faction_id,attitude,stats_json,conditions_json,beliefs_json,goals_json,routine_json,memory_json,importance,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET
                   name=excluded.name,hp=excluded.hp,max_hp=excluded.max_hp,ac=excluded.ac,location=excluded.location,
                   faction_id=excluded.faction_id,attitude=excluded.attitude,stats_json=excluded.stats_json,
                   conditions_json=excluded.conditions_json,beliefs_json=excluded.beliefs_json,goals_json=excluded.goals_json,
                   routine_json=excluded.routine_json,memory_json=excluded.memory_json,importance=excluded.importance,updated_at=excluded.updated_at""",
                (
                    campaign_id, npc_id, name[:200], hp, max_hp, ac, location[:200], faction_id, attitude,
                    self._dumps(stats or {}), self._dumps(sorted(set(conditions))), self._dumps(list(beliefs)),
                    self._dumps(list(goals)), self._dumps(routine or {}), self._dumps(list(memory)), importance, now,
                ),
            )
            rev = self._next_revision(db, campaign_id)
            self._insert_event(db, campaign_id, rev, "npc_upsert", f"NPC state saved: {name}", region=location, actor_id=npc_id)
        return self.get_npc(campaign_id, npc_id)

    def get_npc(self, campaign_id: str, npc_id: str) -> dict[str, Any]:
        campaign_id, npc_id = self._clean_id(campaign_id), self._clean_id(npc_id)
        with self._db() as db:
            return self._get_npc_db(db, campaign_id, npc_id)

    def get_actor(self, campaign_id: str, kind: str, actor_id: str) -> dict[str, Any]:
        if kind not in _ENTITY_KINDS:
            raise ValueError("kind must be character or npc")
        return self.get_character(campaign_id, actor_id) if kind == "character" else self.get_npc(campaign_id, actor_id)

    def _actor_table(self, kind: str) -> str:
        if kind == "character":
            return "characters"
        if kind == "npc":
            return "npcs"
        raise ValueError("kind must be character or npc")

    def _mark_npc_dead_db(self, db: sqlite3.Connection, campaign_id: str, npc_id: str, *, revision: int, world_time: str, cause: str) -> list[dict[str, Any]]:
        npc = self._get_npc_db(db, campaign_id, npc_id)
        if str(npc.get("status", "alive")) == "dead":
            return []
        now = self._now()
        db.execute("UPDATE npcs SET hp=0,status='dead',died_on=?,updated_at=? WHERE campaign_id=? AND id=?", (world_time, now, campaign_id, npc_id))
        db.execute("UPDATE npc_lifecycle SET alive=0,updated_at=? WHERE campaign_id=? AND npc_id=?", (now, campaign_id, npc_id))
        self._insert_event(db, campaign_id, revision, "death", f"{npc['name']} died: {cause}", region=npc.get("location"), actor_id=npc_id, target_id=npc_id, payload={"npc_id": npc_id, "cause": cause}, world_time_override=world_time)
        return apply_succession(self, db, campaign_id, npc_id, world_time=world_time, revision=revision)

    def apply_hp_delta(self, campaign_id: str, kind: str, actor_id: str, delta: int, reason: str) -> dict[str, Any]:
        campaign_id, actor_id = self._clean_id(campaign_id), self._clean_id(actor_id)
        table = self._actor_table(kind)
        with self._write_db() as db:
            actor = self._get_actor_db(db, campaign_id, kind, actor_id)
            if kind == "npc":
                self._canonize_materialized_npc_db(db, campaign_id, actor_id, f"gameplay HP change: {reason}")
            new_hp = max(0, min(int(actor["max_hp"]), int(actor["hp"]) + int(delta)))
            db.execute(
                f"UPDATE {table} SET hp=?,updated_at=? WHERE campaign_id=? AND id=?",
                (new_hp, self._now(), campaign_id, actor_id),
            )
            rev = self._next_revision(db, campaign_id)
            self._insert_event(
                db, campaign_id, rev, "hp_delta", reason,
                region=actor.get("location"), actor_id=actor_id,
                payload={"kind": kind, "delta": delta, "old_hp": actor["hp"], "new_hp": new_hp},
            )
        return self.get_actor(campaign_id, kind, actor_id)

    def set_condition(self, campaign_id: str, kind: str, actor_id: str, condition: str, active: bool, reason: str = "condition changed") -> dict[str, Any]:
        campaign_id, actor_id = self._clean_id(campaign_id), self._clean_id(actor_id)
        condition = condition.strip().lower()[:80]
        if not condition:
            raise ValueError("condition cannot be empty")
        table = self._actor_table(kind)
        with self._write_db() as db:
            actor = self._get_actor_db(db, campaign_id, kind, actor_id)
            if kind == "npc":
                self._canonize_materialized_npc_db(db, campaign_id, actor_id, f"gameplay condition change: {reason}")
            conditions = set(actor.get("conditions", []))
            if active:
                conditions.add(condition)
            else:
                conditions.discard(condition)
            db.execute(
                f"UPDATE {table} SET conditions_json=?,updated_at=? WHERE campaign_id=? AND id=?",
                (self._dumps(sorted(conditions)), self._now(), campaign_id, actor_id),
            )
            rev = self._next_revision(db, campaign_id)
            self._insert_event(
                db, campaign_id, rev, "condition_change", reason,
                region=actor.get("location"), actor_id=actor_id,
                payload={"kind": kind, "condition": condition, "active": active},
            )
        return self.get_actor(campaign_id, kind, actor_id)

    def update_character_resources(
        self,
        campaign_id: str,
        character_id: str,
        *,
        resource_delta: dict[str, int] | None = None,
        add_inventory: Sequence[dict[str, Any] | str] = (),
        remove_inventory_indexes: Sequence[int] = (),
        reason: str = "resources updated",
    ) -> dict[str, Any]:
        campaign_id, character_id = self._clean_id(campaign_id), self._clean_id(character_id)
        with self._write_db() as db:
            character = self._get_character_db(db, campaign_id, character_id)
            resources = dict(character["resources"])
            for key, delta in (resource_delta or {}).items():
                old = resources.get(key, 0)
                if not isinstance(old, (int, float)):
                    raise ValueError(f"resource {key} is not numeric")
                resources[key] = old + int(delta)
            inventory = list(character["inventory"])
            for idx in sorted({int(i) for i in remove_inventory_indexes}, reverse=True):
                if 0 <= idx < len(inventory):
                    inventory.pop(idx)
            inventory.extend(add_inventory)
            db.execute(
                "UPDATE characters SET resources_json=?,inventory_json=?,updated_at=? WHERE campaign_id=? AND id=?",
                (self._dumps(resources), self._dumps(inventory), self._now(), campaign_id, character_id),
            )
            rev = self._next_revision(db, campaign_id)
            self._insert_event(db, campaign_id, rev, "resource_change", reason, region=character["location"], actor_id=character_id,
                               payload={"resource_delta": resource_delta or {}, "inventory_added": list(add_inventory), "inventory_removed_indexes": list(remove_inventory_indexes)})
        return self.get_character(campaign_id, character_id)

    def upsert_location(self, campaign_id: str, location_id: str, name: str, *, region: str = "unknown", description: str = "", x: float | None = None, y: float | None = None, realm_id: str | None = None, tags: Iterable[str] = (), state: dict[str, Any] | None = None) -> dict[str, Any]:
        campaign_id, location_id = self._clean_id(campaign_id), self._clean_id(location_id)
        self._ensure_campaign_exists(campaign_id)
        with self._write_db() as db:
            db.execute(
                """INSERT INTO locations(campaign_id,id,name,region,description,x,y,realm_id,tags_json,state_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET name=excluded.name,region=excluded.region,description=excluded.description,
                   x=COALESCE(excluded.x,locations.x),y=COALESCE(excluded.y,locations.y),realm_id=COALESCE(excluded.realm_id,locations.realm_id),tags_json=excluded.tags_json,state_json=excluded.state_json,updated_at=excluded.updated_at""",
                (campaign_id, location_id, name[:200], region[:200], description[:5000], x, y, realm_id, self._dumps(sorted(set(tags))), self._dumps(state or {}), self._now()),
            )
            rev = self._next_revision(db, campaign_id)
            self._insert_event(db, campaign_id, rev, "location_upsert", f"Location state saved: {name}", region=region, actor_id=location_id, payload={"x": x, "y": y, "realm_id": realm_id})
        return self.get_location(campaign_id, location_id)

    def get_location(self, campaign_id: str, location_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM locations WHERE campaign_id=? AND id=?", (campaign_id, location_id)).fetchone()
        if not row:
            raise KeyError(f"unknown location: {location_id}")
        data = dict(row)
        data["tags"] = self._loads(data.pop("tags_json"))
        data["state"] = self._loads(data.pop("state_json"))
        return data

    def save_location_link(self, campaign_id: str, from_id: str, to_id: str, travel_hours: float, *, road_quality: str = "road", bidirectional: bool = True, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        campaign_id, from_id, to_id = self._clean_id(campaign_id), self._clean_id(from_id), self._clean_id(to_id)
        self.get_location(campaign_id, from_id)
        self.get_location(campaign_id, to_id)
        travel_hours = float(travel_hours)
        if travel_hours < 0:
            raise ValueError("travel_hours must be >=0")
        with self._write_db() as db:
            rows=[(campaign_id,from_id,to_id,travel_hours,road_quality[:80],self._dumps(metadata or {}),self._now())]
            if bidirectional:
                rows.append((campaign_id,to_id,from_id,travel_hours,road_quality[:80],self._dumps(metadata or {}),self._now()))
            db.executemany(
                """INSERT INTO location_links(campaign_id,from_id,to_id,travel_hours,road_quality,metadata_json,updated_at) VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,from_id,to_id) DO UPDATE SET travel_hours=excluded.travel_hours,road_quality=excluded.road_quality,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                rows,
            )
            rev=self._next_revision(db,campaign_id)
            self._insert_event(db,campaign_id,rev,"location_link",f"Route linked {from_id} to {to_id}",actor_id=from_id,target_id=to_id,payload={"travel_hours":travel_hours,"road_quality":road_quality,"bidirectional":bidirectional})
        return self.route_locations(campaign_id, from_id, to_id)

    def get_location_links(self, campaign_id: str, location_id: str | None = None) -> list[dict[str, Any]]:
        with self._db() as db:
            if location_id:
                rows=db.execute("SELECT * FROM location_links WHERE campaign_id=? AND from_id=? ORDER BY travel_hours,to_id",(campaign_id,location_id)).fetchall()
            else:
                rows=db.execute("SELECT * FROM location_links WHERE campaign_id=? ORDER BY from_id,to_id",(campaign_id,)).fetchall()
        out=[]
        for row in rows:
            d=dict(row); d["metadata"]=self._loads(d.pop("metadata_json")); out.append(d)
        return out

    def _route_locations_db(self, db: sqlite3.Connection, campaign_id: str, from_id: str, to_id: str) -> dict[str, Any]:
        import heapq
        from_id, to_id = self._clean_id(from_id), self._clean_id(to_id)
        if from_id == to_id:
            return {"from":from_id,"to":to_id,"reachable":True,"travel_hours":0.0,"hops":0,"path":[from_id]}
        rows=db.execute("SELECT from_id,to_id,travel_hours FROM location_links WHERE campaign_id=? ORDER BY from_id,to_id",(campaign_id,)).fetchall()
        adj: dict[str,list[tuple[str,float]]]={}
        for r in rows:
            adj.setdefault(str(r["from_id"]),[]).append((str(r["to_id"]),float(r["travel_hours"])))
        q=[(0.0,0,from_id,[from_id])]
        best={from_id:0.0}
        while q:
            hours,hops,node,path=heapq.heappop(q)
            if node == to_id:
                return {"from":from_id,"to":to_id,"reachable":True,"travel_hours":hours,"hops":hops,"path":path}
            if hours != best.get(node):
                continue
            for nxt,w in adj.get(node,[]):
                nh=hours+w
                if nh < best.get(nxt,float("inf")):
                    best[nxt]=nh
                    heapq.heappush(q,(nh,hops+1,nxt,path+[nxt]))
        return {"from":from_id,"to":to_id,"reachable":False,"travel_hours":None,"hops":None,"path":[]}

    def route_locations(self, campaign_id: str, from_id: str, to_id: str) -> dict[str, Any]:
        with self._db() as db:
            return self._route_locations_db(db, campaign_id, from_id, to_id)

    def _lod_tiers_db(self, db: sqlite3.Connection, campaign_id: str, origin: str) -> list[dict[str, Any]]:
        locations=[dict(r) for r in db.execute("SELECT id,name,region FROM locations WHERE campaign_id=? ORDER BY id",(campaign_id,)).fetchall()]
        origin_region=next((x["region"] for x in locations if x["id"]==origin),None)
        out=[]
        for loc in locations:
            route=self._route_locations_db(db,campaign_id,origin,loc["id"])
            hops=route["hops"]
            if loc["id"] == origin or (hops is not None and hops <= 1):
                tier="near"
            elif loc["region"] == origin_region or (hops is not None and hops <= 3):
                tier="mid"
            else:
                tier="far"
            out.append({"location_id":loc["id"],"name":loc["name"],"tier":tier,"travel_hours":route["travel_hours"],"hops":hops})
        return out

    def get_lod_tiers(self, campaign_id: str, origin: str) -> list[dict[str, Any]]:
        with self._db() as db:
            return self._lod_tiers_db(db,campaign_id,origin)

    def set_world_state(self, campaign_id: str, scope_type: str, scope_id: str, state_key: str, value: Any, reason: str = "world state changed") -> dict[str, Any]:
        campaign_id = self._clean_id(campaign_id)
        scope_type = self._clean_id(scope_type)
        scope_id = self._clean_id(scope_id)
        state_key = self._clean_id(state_key)
        self._ensure_campaign_exists(campaign_id)
        with self._write_db() as db:
            db.execute(
                """INSERT INTO world_state(campaign_id,scope_type,scope_id,state_key,value_json,updated_at) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,scope_type,scope_id,state_key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
                (campaign_id, scope_type, scope_id, state_key, self._dumps(value), self._now()),
            )
            rev = self._next_revision(db, campaign_id)
            self._insert_event(db, campaign_id, rev, "world_state_change", reason, actor_id=scope_id, payload={"scope_type": scope_type, "scope_id": scope_id, "key": state_key, "value": value})
        return {"campaign_id": campaign_id, "scope_type": scope_type, "scope_id": scope_id, "key": state_key, "value": value, "revision": self.get_campaign(campaign_id)["revision"]}

    def get_world_state(self, campaign_id: str, scope_type: str | None = None, scope_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM world_state WHERE campaign_id=?"
        params: list[Any] = [campaign_id]
        if scope_type is not None:
            sql += " AND scope_type=?"
            params.append(scope_type)
        if scope_id is not None:
            sql += " AND scope_id=?"
            params.append(scope_id)
        sql += " ORDER BY scope_type,scope_id,state_key"
        with self._db() as db:
            rows = db.execute(sql, params).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["value"] = self._loads(data.pop("value_json"))
            data["key"] = data["state_key"]  # stable public alias while retaining canonical column name
            out.append(data)
        return out

    def move_actor(self, campaign_id: str, kind: str, actor_id: str, location: str, reason: str = "moved") -> dict[str, Any]:
        campaign_id, actor_id = self._clean_id(campaign_id), self._clean_id(actor_id)
        table = self._actor_table(kind)
        with self._write_db() as db:
            actor = self._get_actor_db(db, campaign_id, kind, actor_id)
            if kind == "npc":
                self._canonize_materialized_npc_db(db, campaign_id, actor_id, f"gameplay movement: {reason}")
            old_location = actor["location"]
            db.execute(f"UPDATE {table} SET location=?,updated_at=? WHERE campaign_id=? AND id=?", (location[:200], self._now(), campaign_id, actor_id))
            rev = self._next_revision(db, campaign_id)
            self._insert_event(db, campaign_id, rev, "movement", reason, region=location, actor_id=actor_id, payload={"kind": kind, "from": old_location, "to": location})
        return self.get_actor(campaign_id, kind, actor_id)

    def update_npc_state(self, campaign_id: str, npc_id: str, *, attitude_delta: int = 0, add_beliefs: Sequence[str] = (), remove_beliefs: Sequence[str] = (), add_goals: Sequence[str] = (), remove_goals: Sequence[str] = (), add_memory: Sequence[dict[str, Any] | str] = (), reason: str = "NPC state changed") -> dict[str, Any]:
        campaign_id, npc_id = self._clean_id(campaign_id), self._clean_id(npc_id)
        with self._write_db() as db:
            npc = self._get_npc_db(db, campaign_id, npc_id)
            self._canonize_materialized_npc_db(db, campaign_id, npc_id, f"gameplay NPC state: {reason}")
            attitude = max(-10, min(10, int(npc["attitude"]) + int(attitude_delta)))
            beliefs = list(npc["beliefs"])
            for value in remove_beliefs:
                beliefs = [x for x in beliefs if x != value]
            for value in add_beliefs:
                if value not in beliefs:
                    beliefs.append(value)
            goals = list(npc["goals"])
            for value in remove_goals:
                goals = [x for x in goals if x != value]
            for value in add_goals:
                if value not in goals:
                    goals.append(value)
            memory = list(npc["memory"]) + list(add_memory)
            if len(memory) > 500:
                memory = memory[-500:]
            db.execute(
                "UPDATE npcs SET attitude=?,beliefs_json=?,goals_json=?,memory_json=?,updated_at=? WHERE campaign_id=? AND id=?",
                (attitude, self._dumps(beliefs), self._dumps(goals), self._dumps(memory), self._now(), campaign_id, npc_id),
            )
            rev = self._next_revision(db, campaign_id)
            self._insert_event(db, campaign_id, rev, "npc_state_change", reason, region=npc["location"], actor_id=npc_id, payload={"attitude_delta": attitude_delta, "add_beliefs": list(add_beliefs), "remove_beliefs": list(remove_beliefs), "add_goals": list(add_goals), "remove_goals": list(remove_goals), "memory_added": list(add_memory)})
        return self.get_npc(campaign_id, npc_id)

    def adjust_faction(self, campaign_id: str, faction_id: str, *, reputation_delta: int = 0, reserve_delta: int = 0, state_patch: dict[str, Any] | None = None, add_goals: Sequence[str] = (), remove_goals: Sequence[str] = (), reason: str = "faction state changed") -> dict[str, Any]:
        campaign_id, faction_id = self._clean_id(campaign_id), self._clean_id(faction_id)
        with self._write_db() as db:
            faction = self._get_faction_db(db, campaign_id, faction_id)
            reputation = max(-10, min(10, int(faction["reputation"]) + int(reputation_delta)))
            reserve = int(faction["reserve_score"]) + int(reserve_delta)
            state = dict(faction["state"])
            state.update(state_patch or {})
            goals = list(faction["goals"])
            for value in remove_goals:
                goals = [x for x in goals if x != value]
            for value in add_goals:
                if value not in goals:
                    goals.append(value)
            db.execute("UPDATE factions SET reputation=?,reserve_score=?,state_json=?,goals_json=?,updated_at=? WHERE campaign_id=? AND id=?",
                       (reputation, reserve, self._dumps(state), self._dumps(goals), self._now(), campaign_id, faction_id))
            rev = self._next_revision(db, campaign_id)
            self._insert_event(db, campaign_id, rev, "faction_change", reason, region=faction["region"], actor_id=faction_id, payload={"reputation_delta": reputation_delta, "reserve_delta": reserve_delta, "state_patch": state_patch or {}, "add_goals": list(add_goals), "remove_goals": list(remove_goals)})
        return self.get_faction(campaign_id, faction_id)

    # ---------- factions / relationships / quests ----------

    def upsert_faction(self, campaign_id: str, faction_id: str, name: str, *, region: str = "unknown", reputation: int = 0, reserve_score: int = 0, goals: Iterable[str] = (), state: dict[str, Any] | None = None, leader_id: str | None = None) -> dict[str, Any]:
        campaign_id, faction_id = self._clean_id(campaign_id), self._clean_id(faction_id)
        self._ensure_campaign_exists(campaign_id)
        if not -10 <= reputation <= 10:
            raise ValueError("reputation must be -10..10")
        with self._write_db() as db:
            db.execute(
                """INSERT INTO factions(campaign_id,id,name,region,reputation,reserve_score,goals_json,state_json,leader_id,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET name=excluded.name,region=excluded.region,reputation=excluded.reputation,
                   reserve_score=excluded.reserve_score,goals_json=excluded.goals_json,state_json=excluded.state_json,leader_id=COALESCE(excluded.leader_id,factions.leader_id),updated_at=excluded.updated_at""",
                (campaign_id, faction_id, name[:200], region[:200], reputation, reserve_score, self._dumps(list(goals)), self._dumps(state or {}), leader_id, self._now()),
            )
            rev = self._next_revision(db, campaign_id)
            self._insert_event(db, campaign_id, rev, "faction_upsert", f"Faction state saved: {name}", region=region, actor_id=faction_id)
        return self.get_faction(campaign_id, faction_id)

    def get_faction(self, campaign_id: str, faction_id: str) -> dict[str, Any]:
        with self._db() as db:
            return self._get_faction_db(db, campaign_id, faction_id)

    @staticmethod
    def _clamp100(value: int) -> int:
        return max(-100, min(100, int(value)))

    def adjust_relationship(self, campaign_id: str, source_id: str, target_id: str, *, trust_delta: int = 0, fear_delta: int = 0, respect_delta: int = 0, affection_delta: int = 0, reason: str = "relationship changed") -> dict[str, Any]:
        campaign_id, source_id, target_id = self._clean_id(campaign_id), self._clean_id(source_id), self._clean_id(target_id)
        self._ensure_campaign_exists(campaign_id)
        now = self._now()
        with self._write_db() as db:
            row = db.execute("SELECT * FROM relationships WHERE campaign_id=? AND source_id=? AND target_id=?", (campaign_id, source_id, target_id)).fetchone()
            self._canonize_materialized_npc_db(db, campaign_id, source_id, f"gameplay relationship: {reason}")
            self._canonize_materialized_npc_db(db, campaign_id, target_id, f"gameplay relationship: {reason}")
            base = dict(row) if row else {"trust": 0, "fear": 0, "respect": 0, "affection": 0}
            values = {
                "trust": self._clamp100(base["trust"] + trust_delta),
                "fear": self._clamp100(base["fear"] + fear_delta),
                "respect": self._clamp100(base["respect"] + respect_delta),
                "affection": self._clamp100(base["affection"] + affection_delta),
            }
            db.execute(
                """INSERT INTO relationships(campaign_id,source_id,target_id,trust,fear,respect,affection,notes_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,'{}',?)
                   ON CONFLICT(campaign_id,source_id,target_id) DO UPDATE SET trust=excluded.trust,fear=excluded.fear,respect=excluded.respect,
                   affection=excluded.affection,updated_at=excluded.updated_at""",
                (campaign_id, source_id, target_id, values["trust"], values["fear"], values["respect"], values["affection"], now),
            )
            rev = self._next_revision(db, campaign_id)
            deltas = {"trust": trust_delta, "fear": fear_delta, "respect": respect_delta, "affection": affection_delta}
            self._insert_event(db, campaign_id, rev, "relationship_change", reason, actor_id=source_id, target_id=target_id,
                               payload={"deltas": deltas, "new": values})
            record_relationship_event(self, db, campaign_id, source_id, target_id, deltas, reason, rev, event_type="direct")
        return self.get_relationship(campaign_id, source_id, target_id)

    def get_relationship_events(self, campaign_id: str, source_id: str | None = None, target_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        sql = "SELECT * FROM relationship_events WHERE campaign_id=?"
        params: list[Any] = [campaign_id]
        if source_id is not None:
            sql += " AND source_id=?"
            params.append(source_id)
        if target_id is not None:
            sql += " AND target_id=?"
            params.append(target_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._db() as db:
            return [dict(r) for r in db.execute(sql, params).fetchall()]

    def get_relationship(self, campaign_id: str, source_id: str, target_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM relationships WHERE campaign_id=? AND source_id=? AND target_id=?", (campaign_id, source_id, target_id)).fetchone()
        if not row:
            return {"campaign_id": campaign_id, "source_id": source_id, "target_id": target_id, "trust": 0, "fear": 0, "respect": 0, "affection": 0, "notes": {}}
        data = dict(row)
        data["notes"] = self._loads(data.pop("notes_json"))
        return data

    def upsert_quest(self, campaign_id: str, quest_id: str, title: str, *, status: str = "active", owner_id: str | None = None, region: str | None = None, objectives: Sequence[dict[str, Any] | str] = (), state: dict[str, Any] | None = None, reason: str = "quest updated") -> dict[str, Any]:
        if status not in {"inactive", "active", "completed", "failed", "abandoned"}:
            raise ValueError("invalid quest status")
        campaign_id, quest_id = self._clean_id(campaign_id), self._clean_id(quest_id)
        self._ensure_campaign_exists(campaign_id)
        with self._write_db() as db:
            db.execute(
                """INSERT INTO quests(campaign_id,id,title,status,owner_id,region,objectives_json,state_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET title=excluded.title,status=excluded.status,owner_id=excluded.owner_id,
                   region=excluded.region,objectives_json=excluded.objectives_json,state_json=excluded.state_json,updated_at=excluded.updated_at""",
                (campaign_id, quest_id, title[:300], status, owner_id, region, self._dumps(list(objectives)), self._dumps(state or {}), self._now()),
            )
            rev = self._next_revision(db, campaign_id)
            self._insert_event(db, campaign_id, rev, "quest_update", reason, region=region, actor_id=owner_id, payload={"quest_id": quest_id, "status": status})
        return self.get_quest(campaign_id, quest_id)

    def get_quest(self, campaign_id: str, quest_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM quests WHERE campaign_id=? AND id=?", (campaign_id, quest_id)).fetchone()
        if not row:
            raise KeyError(f"unknown quest: {quest_id}")
        data = dict(row)
        data["objectives"] = self._loads(data.pop("objectives_json"))
        data["state"] = self._loads(data.pop("state_json"))
        return data

    # ---------- gameplay ----------

    @staticmethod
    def _grid_line_cells(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
        cells=[]
        dx=abs(x1-x0); sx=1 if x0<x1 else -1
        dy=-abs(y1-y0); sy=1 if y0<y1 else -1
        err=dx+dy
        x,y=x0,y0
        while True:
            cells.append((x,y))
            if x==x1 and y==y1:
                break
            e2=2*err
            if e2>=dy:
                err+=dy; x+=sx
            if e2<=dx:
                err+=dx; y+=sy
        return cells

    def set_combat_position(self, campaign_id: str, combat_id: str, actor_kind: str, actor_id: str, x: int, y: int, *, cover: str = "none") -> dict[str, Any]:
        if cover not in {"none","half","three_quarters","total"}:
            raise ValueError("invalid cover")
        x,y=int(x),int(y)
        with self._write_db() as db:
            combat=self._get_combat_db(db,campaign_id,combat_id)
            if combat["status"] != "active":
                raise ValueError("combat is not active")
            self._get_actor_db(db,campaign_id,actor_kind,actor_id)
            if not (0 <= x < int(combat["grid_width"]) and 0 <= y < int(combat["grid_height"])):
                raise ValueError("combat position outside grid")
            db.execute("""INSERT INTO combat_positions(campaign_id,combat_id,actor_kind,actor_id,x,y,cover) VALUES(?,?,?,?,?,?,?)
                          ON CONFLICT(campaign_id,combat_id,actor_kind,actor_id) DO UPDATE SET x=excluded.x,y=excluded.y,cover=excluded.cover""",(campaign_id,combat_id,actor_kind,actor_id,x,y,cover))
        return {"campaign_id":campaign_id,"combat_id":combat_id,"actor_kind":actor_kind,"actor_id":actor_id,"x":x,"y":y,"cover":cover}

    def set_combat_terrain(self, campaign_id: str, combat_id: str, x: int, y: int, *, kind: str = "open", blocks_los: bool = False, difficult: bool = False, hazard: dict[str, Any] | None = None) -> dict[str, Any]:
        x,y=int(x),int(y)
        with self._write_db() as db:
            combat=self._get_combat_db(db,campaign_id,combat_id)
            if combat["status"] != "active":
                raise ValueError("combat is not active")
            if not (0 <= x < int(combat["grid_width"]) and 0 <= y < int(combat["grid_height"])):
                raise ValueError("combat terrain outside grid")
            db.execute("""INSERT INTO combat_terrain(campaign_id,combat_id,x,y,kind,blocks_los,difficult,hazard_json) VALUES(?,?,?,?,?,?,?,?)
                          ON CONFLICT(campaign_id,combat_id,x,y) DO UPDATE SET kind=excluded.kind,blocks_los=excluded.blocks_los,difficult=excluded.difficult,hazard_json=excluded.hazard_json""",(campaign_id,combat_id,x,y,kind[:80],int(bool(blocks_los)),int(bool(difficult)),self._dumps(hazard or {})))
        return {"campaign_id":campaign_id,"combat_id":combat_id,"x":x,"y":y,"kind":kind,"blocks_los":bool(blocks_los),"difficult":bool(difficult),"hazard":hazard or {}}

    def resolve_attack(
        self,
        campaign_id: str,
        attacker_kind: str,
        attacker_id: str,
        target_kind: str,
        target_id: str,
        *,
        attack_bonus: int,
        damage_expression: str,
        mode: str = "normal",
        attack_name: str = "attack",
        combat_id: str | None = None,
        range_cells: float | None = None,
        ignore_cover: bool = False,
        damage_type: str = "untyped",
    ) -> dict[str, Any]:
        """Backward-compatible attack endpoint using the shared v3.7 rules primitives."""
        campaign_id = self._clean_id(campaign_id)
        attacker_id, target_id = self._clean_id(attacker_id), self._clean_id(target_id)
        rules=RulesKernel(self)
        with self._write_db() as db:
            attacker = self._get_actor_db(db, campaign_id, attacker_kind, attacker_id)
            target = self._get_actor_db(db, campaign_id, target_kind, target_id)
            if attacker_kind == "npc":
                self._canonize_materialized_npc_db(db, campaign_id, attacker_id, "gameplay combat participant")
            if target_kind == "npc":
                self._canonize_materialized_npc_db(db, campaign_id, target_id, "gameplay combat participant")
            if str(attacker.get("status", "alive")) != "alive" or int(attacker.get("hp",0)) <= 0:
                raise ValueError("dead or unconscious actors cannot attack")
            if str(target.get("status", "alive")) == "dead":
                raise ValueError("cannot attack an already-dead target")

            cover = "none"
            cover_bonus = 0
            distance_cells = None
            if combat_id:
                apos = db.execute("SELECT x,y FROM combat_positions WHERE campaign_id=? AND combat_id=? AND actor_kind=? AND actor_id=?", (campaign_id,combat_id,attacker_kind,attacker_id)).fetchone()
                tpos = db.execute("SELECT x,y,cover FROM combat_positions WHERE campaign_id=? AND combat_id=? AND actor_kind=? AND actor_id=?", (campaign_id,combat_id,target_kind,target_id)).fetchone()
                if not apos or not tpos:
                    raise ValueError("combat attack requires positions for attacker and target")
                distance_cells = max(abs(int(apos["x"])-int(tpos["x"])), abs(int(apos["y"])-int(tpos["y"])))
                if range_cells is not None and distance_cells > float(range_cells):
                    raise ValueError(f"target out of range: {distance_cells} cells > {range_cells}")
                cover = str(tpos["cover"] or "none")
                for cx,cy in self._grid_line_cells(int(apos["x"]),int(apos["y"]),int(tpos["x"]),int(tpos["y"]))[1:-1]:
                    terrain=db.execute("SELECT blocks_los FROM combat_terrain WHERE campaign_id=? AND combat_id=? AND x=? AND y=?",(campaign_id,combat_id,cx,cy)).fetchone()
                    if terrain and bool(terrain["blocks_los"]):
                        cover="total"; break
                if cover=="total" and not ignore_cover:
                    raise ValueError("target has total cover / blocked line of sight")
                if not ignore_cover:
                    cover_bonus={"none":0,"half":2,"three_quarters":5,"total":999}.get(cover,0)

            source_mods=rules._active_modifiers_db(db,campaign_id,attacker_kind,attacker_id)
            target_mods=rules._active_modifiers_db(db,campaign_id,target_kind,target_id)
            effective_attack_bonus=int(attack_bonus)+int(source_mods["attack_bonus"])
            effective_ac=int(target["ac"])+int(target_mods["ac_bonus"])+cover_bonus
            effective_mode=mode
            if mode=="normal":
                adv="attack" in source_mods["advantage"]
                dis="attack" in source_mods["disadvantage"]
                if adv and not dis: effective_mode="advantage"
                elif dis and not adv: effective_mode="disadvantage"
            check=self._resolve_check_db(db,campaign_id,effective_attack_bonus,effective_ac,effective_mode,namespace=f"attack:{attacker_kind}:{attacker_id}:{target_kind}:{target_id}")
            natural=int(check["natural"]); critical=natural==20; would_hit=False if natural==1 else (critical or check["success"])
            revision=self._next_revision(db,campaign_id); group_id=f"legacy-attack:{revision}:{attacker_id}:{target_id}"
            attack_reactions=rules._fire_reactions_db(db,campaign_id,"after_attack_roll",{"attack_would_hit":would_hit,"target_is_self":True,"activity_tags":["legacy_attack"],"critical":critical},combat_id=combat_id,group_id=group_id,owners=[(target_kind,target_id)])
            target_mods=rules._active_modifiers_db(db,campaign_id,target_kind,target_id)
            final_ac=int(target["ac"])+int(target_mods["ac_bonus"])+cover_bonus
            hit=False if natural==1 else (critical or int(check["total"])>=final_ac)
            raw_damage=None; application=None; old_hp=int(target["hp"]); new_hp=old_hp
            if hit:
                raw_damage=self._roll_damage_db(db,campaign_id,damage_expression,critical,namespace=f"damage:{attacker_id}:{target_id}")
                application=rules._apply_damage_db(db,campaign_id,target_kind,target_id,[{"type":damage_type.lower(),"raw":int(raw_damage["total"]),"roll":raw_damage}],revision=revision,source_name=f"{attacker['name']} with {attack_name}",combat_id=combat_id)
                new_hp=int(application["new_hp"])
            summary=f"{attacker['name']} {'hit' if hit else 'missed'} {target['name']} with {attack_name}"
            payload={"combat_id":combat_id,"attacker_kind":attacker_kind,"target_kind":target_kind,"attack_name":attack_name,"attack_bonus":attack_bonus,"effective_attack_bonus":effective_attack_bonus,"attack":check,"target_ac":target["ac"],"effective_target_ac":final_ac,"cover":cover,"cover_bonus":cover_bonus,"distance_cells":distance_cells,"range_cells":range_cells,"hit":hit,"critical":critical,"damage":raw_damage,"damage_type":damage_type,"damage_application":application,"old_hp":old_hp,"new_hp":new_hp,"reactions":{"after_attack_roll":attack_reactions}}
            self._insert_event(db,campaign_id,revision,"attack",summary,region=attacker.get("location"),actor_id=attacker_id,target_id=target_id,payload=payload)
        return {"campaign_id":campaign_id,"revision":revision,"attacker":{"kind":attacker_kind,"id":attacker_id,"name":attacker["name"]},"target":{"kind":target_kind,"id":target_id,"name":target["name"],"ac":target["ac"],"effective_ac":final_ac,"old_hp":old_hp,"new_hp":new_hp},"spatial":{"cover":cover,"cover_bonus":cover_bonus,"cover_ac_bonus":cover_bonus,"distance_cells":distance_cells,"range_cells":range_cells},"attack_name":attack_name,"attack_bonus":attack_bonus,"effective_attack_bonus":effective_attack_bonus,"attack":check,"hit":hit,"critical":critical,"damage":raw_damage,"damage_type":damage_type,"damage_application":application,"reactions":{"after_attack_roll":attack_reactions}}

    @staticmethod
    def _scene_coord_to_grid(value: float, radius_m: float, size: int) -> int:
        if 0 <= value <= size - 1:
            return int(round(value))
        normalized = (float(value) + float(radius_m)) / (2.0 * float(radius_m))
        return max(0, min(size - 1, int(round(normalized * (size - 1)))))

    def start_combat(self, campaign_id: str, combat_id: str, location: str, participants: Sequence[dict[str, str]], *, grid_width: int = 20, grid_height: int = 20, positions: Sequence[dict[str, Any]] = (), terrain: Sequence[dict[str, Any]] = (), scene_id: str | None = None) -> dict[str, Any]:
        campaign_id, combat_id = self._clean_id(campaign_id), self._clean_id(combat_id)
        if len(participants) < 2 or len(participants) > 50:
            raise ValueError("combat requires 2..50 participants")
        grid_width,grid_height=int(grid_width),int(grid_height)
        if not (5 <= grid_width <= 100 and 5 <= grid_height <= 100):
            raise ValueError("combat grid dimensions must be 5..100")
        now=self._now()
        with self._write_db() as db:
            normalized=[]; initiative=[]
            participant_keys=set()
            for p in participants:
                kind=p.get("kind",""); actor_id=self._clean_id(p.get("id","")); actor=self._get_actor_db(db,campaign_id,kind,actor_id)
                if str(actor.get("status", "alive")) != "alive" or int(actor.get("hp", 0)) <= 0:
                    raise ValueError(f"combat participant must be alive: {kind}/{actor_id}")
                dex_mod=int(actor.get("abilities",{}).get("dex",0)) if kind=="character" else int(actor.get("stats",{}).get("dex_mod",0))
                roll=self._roll_dice_db(db,campaign_id,"1d20",f"initiative:{combat_id}:{kind}:{actor_id}"); total=roll.total+dex_mod
                normalized.append({"kind":kind,"id":actor_id,"name":actor["name"]})
                initiative.append({"kind":kind,"id":actor_id,"name":actor["name"],"natural":roll.total,"modifier":dex_mod,"total":total})
                participant_keys.add((kind,actor_id))
            initiative.sort(key=lambda x:(-x["total"],-x["modifier"],x["id"]))

            # If no explicit tactical staging is provided, materialize it from the
            # active disposable SCENE layer.  WORLD never gains a dense grid.
            local_positions=list(positions)
            local_terrain=list(terrain)
            scene_row=None
            if scene_id:
                scene_row=db.execute("SELECT * FROM scenes WHERE campaign_id=? AND id=? AND status='active'",(campaign_id,scene_id)).fetchone()
            else:
                scene_row=db.execute("SELECT * FROM scenes WHERE campaign_id=? AND location_id=? AND status='active' ORDER BY updated_at DESC LIMIT 1",(campaign_id,location)).fetchone()
            if scene_row and not local_positions:
                radius=float(scene_row["radius_m"])
                for r in db.execute("SELECT * FROM scene_entities WHERE campaign_id=? AND scene_id=? ORDER BY actor_kind,actor_id",(campaign_id,scene_row["id"])).fetchall():
                    if (r["actor_kind"],r["actor_id"]) in participant_keys:
                        local_positions.append({"kind":r["actor_kind"],"id":r["actor_id"],"x":self._scene_coord_to_grid(float(r["x"]),radius,grid_width),"y":self._scene_coord_to_grid(float(r["y"]),radius,grid_height),"cover":"none"})
            if scene_row and not local_terrain:
                radius=float(scene_row["radius_m"])
                for r in db.execute("SELECT * FROM scene_features WHERE campaign_id=? AND scene_id=? ORDER BY id",(campaign_id,scene_row["id"])).fetchall():
                    local_terrain.append({"x":self._scene_coord_to_grid(float(r["x"]),radius,grid_width),"y":self._scene_coord_to_grid(float(r["y"]),radius,grid_height),"kind":r["kind"],"blocks_los":bool(r["blocks_los"]),"difficult":bool(r["difficult"]),"hazard":self._loads(r["state_json"])})
                db.execute("UPDATE scenes SET scene_type='combat',updated_at=? WHERE campaign_id=? AND id=?",(now,campaign_id,scene_row["id"]))

            db.execute(
                """INSERT INTO combats(campaign_id,id,status,location,round,turn_index,grid_width,grid_height,participants_json,initiative_json,created_at,updated_at)
                   VALUES(?,?,'active',?,1,0,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET status='active',location=excluded.location,round=1,turn_index=0,grid_width=excluded.grid_width,grid_height=excluded.grid_height,
                   participants_json=excluded.participants_json,initiative_json=excluded.initiative_json,updated_at=excluded.updated_at""",
                (campaign_id,combat_id,location[:200],grid_width,grid_height,self._dumps(normalized),self._dumps(initiative),now,now),
            )
            db.execute("DELETE FROM combat_positions WHERE campaign_id=? AND combat_id=?",(campaign_id,combat_id))
            db.execute("DELETE FROM combat_terrain WHERE campaign_id=? AND combat_id=?",(campaign_id,combat_id))
            for pos in local_positions:
                kind=str(pos.get("kind","")); aid=self._clean_id(str(pos.get("id","")))
                if (kind,aid) not in participant_keys:
                    raise ValueError(f"combat position actor is not a participant: {kind}/{aid}")
                x,y=int(pos.get("x",0)),int(pos.get("y",0)); cover=str(pos.get("cover","none"))
                if cover not in {"none","half","three_quarters","total"} or not (0<=x<grid_width and 0<=y<grid_height):
                    raise ValueError("invalid combat position")
                db.execute("INSERT INTO combat_positions(campaign_id,combat_id,actor_kind,actor_id,x,y,cover) VALUES(?,?,?,?,?,?,?)",(campaign_id,combat_id,kind,aid,x,y,cover))
            for tile in local_terrain:
                x,y=int(tile.get("x",0)),int(tile.get("y",0))
                if not (0<=x<grid_width and 0<=y<grid_height):
                    raise ValueError("invalid combat terrain coordinate")
                db.execute("INSERT OR REPLACE INTO combat_terrain(campaign_id,combat_id,x,y,kind,blocks_los,difficult,hazard_json) VALUES(?,?,?,?,?,?,?,?)",(campaign_id,combat_id,x,y,str(tile.get("kind","open"))[:80],int(bool(tile.get("blocks_los",False))),int(bool(tile.get("difficult",False))),self._dumps(tile.get("hazard") or {})))
            rev=self._next_revision(db,campaign_id)
            self._insert_event(db,campaign_id,rev,"combat_start",f"Combat started: {combat_id}",region=location,payload={"combat_id":combat_id,"initiative":initiative,"grid":{"width":grid_width,"height":grid_height},"positions":len(local_positions),"terrain_tiles":len(local_terrain),"source_scene_id":scene_row["id"] if scene_row else None})
            RulesKernel(self).initialize_combat_db(db,campaign_id,combat_id,initiative,1)
        return self.get_combat(campaign_id,combat_id)

    def get_combat(self, campaign_id: str, combat_id: str) -> dict[str, Any]:
        with self._db() as db:
            return self._get_combat_db(db, campaign_id, combat_id)

    def next_turn(self, campaign_id: str, combat_id: str) -> dict[str, Any]:
        campaign_id, combat_id = self._clean_id(campaign_id), self._clean_id(combat_id)
        automatic_death_save = None
        with self._write_db() as db:
            combat = self._get_combat_db(db, campaign_id, combat_id)
            if combat["status"] != "active":
                raise ValueError("combat is not active")
            count = len(combat["initiative"])
            if count == 0:
                raise ValueError("combat has no initiative")
            old_turn=combat["initiative"][int(combat["turn_index"])]
            rules = RulesKernel(self)
            rules.end_turn_db(db,campaign_id,combat_id,old_turn["kind"],old_turn["id"],int(combat["round"]))
            idx = int(combat["turn_index"]) + 1
            round_num = int(combat["round"])
            if idx >= count:
                idx = 0
                round_num += 1
            db.execute("UPDATE combats SET turn_index=?,round=?,updated_at=? WHERE campaign_id=? AND id=?", (idx, round_num, self._now(), campaign_id, combat_id))
            new_turn=combat["initiative"][idx]
            rules.reset_turn_state_db(db,campaign_id,combat_id,new_turn["kind"],new_turn["id"],round_num)
            rev = self._next_revision(db, campaign_id)
            self._insert_event(db, campaign_id, rev, "combat_turn", f"Combat {combat_id}: round {round_num}, turn {idx+1}", region=combat["location"], payload={"combat_id": combat_id, "round": round_num, "turn_index": idx})

            # Mandatory player-character death saves are resolved by the backend
            # when the dying character's turn becomes active. This cannot depend
            # on the GPT/API caller remembering to invoke a separate operation.
            if str(new_turn.get("kind")) == "character":
                actor = self._get_actor_db(db,campaign_id,"character",str(new_turn["id"]))
                if int(actor.get("hp", 0)) <= 0 and str(actor.get("status", "alive")) != "dead":
                    profile = rules._profile_db(db,campaign_id,"character",str(new_turn["id"]))
                    if not bool(profile.get("stable")):
                        automatic_death_save = rules._death_save_db(
                            db,campaign_id,"character",str(new_turn["id"])
                        )
        result = self.get_combat(campaign_id, combat_id)
        if automatic_death_save is not None:
            result = dict(result)
            result["automatic_death_save"] = automatic_death_save
        return result

    def end_combat(self, campaign_id: str, combat_id: str, reason: str = "combat ended") -> dict[str, Any]:
        campaign_id, combat_id = self._clean_id(campaign_id), self._clean_id(combat_id)
        with self._write_db() as db:
            combat = self._get_combat_db(db, campaign_id, combat_id)
            # Fold tactical end positions back into the active disposable scene, if
            # one exists.  The dense grid itself is then deleted.
            scene = db.execute("SELECT * FROM scenes WHERE campaign_id=? AND location_id=? AND status='active' ORDER BY updated_at DESC LIMIT 1", (campaign_id, combat["location"])).fetchone()
            folded = 0
            if scene:
                radius = float(scene["radius_m"])
                width = max(2, int(combat["grid_width"])); height = max(2, int(combat["grid_height"]))
                for pos in combat.get("positions", []):
                    x_m = (float(pos["x"]) / float(width - 1)) * (2.0 * radius) - radius
                    y_m = (float(pos["y"]) / float(height - 1)) * (2.0 * radius) - radius
                    cur = db.execute("SELECT 1 FROM scene_entities WHERE campaign_id=? AND scene_id=? AND actor_kind=? AND actor_id=?", (campaign_id, scene["id"], pos["actor_kind"], pos["actor_id"])).fetchone()
                    if cur:
                        db.execute("UPDATE scene_entities SET x=?,y=?,updated_at=? WHERE campaign_id=? AND scene_id=? AND actor_kind=? AND actor_id=?", (x_m, y_m, self._now(), campaign_id, scene["id"], pos["actor_kind"], pos["actor_id"]))
                        folded += 1
                db.execute("UPDATE scenes SET scene_type='exploration',updated_at=? WHERE campaign_id=? AND id=?", (self._now(), campaign_id, scene["id"]))
            RulesKernel(self)._expire_effects_db(db,campaign_id,reason="combat_end",combat_id=combat_id,round_num=int(combat["round"]))
            db.execute("DELETE FROM rule_turn_state WHERE campaign_id=? AND combat_id=?",(campaign_id,combat_id))
            db.execute("DELETE FROM combat_positions WHERE campaign_id=? AND combat_id=?", (campaign_id,combat_id))
            db.execute("DELETE FROM combat_terrain WHERE campaign_id=? AND combat_id=?", (campaign_id,combat_id))
            db.execute("UPDATE combats SET status='ended',updated_at=? WHERE campaign_id=? AND id=?", (self._now(), campaign_id, combat_id))
            rev = self._next_revision(db, campaign_id)
            self._insert_event(db, campaign_id, rev, "combat_end", reason, region=combat["location"], payload={"combat_id": combat_id, "scene_positions_folded": folded})
        return self.get_combat(campaign_id, combat_id)

    # ---------- world progression / ledger ----------

    def advance_world(self, campaign_id: str, minutes: int, reason: str = "elapsed time", weather: str | None = None, *, simulate: bool = True, season: str | None = None) -> dict[str, Any]:
        """Advance time, simulation, and absolute-time rules effects atomically."""
        campaign_id = self._clean_id(campaign_id)
        minutes = int(minutes)
        if not 0 <= minutes <= 60 * 24 * 365:
            raise ValueError("minutes must be 0..525600")
        self._ensure_campaign_exists(campaign_id)
        with self._write_db() as db:
            if simulate:
                result = SimulationKernel(self).advance_db(db, campaign_id, minutes, reason=reason, weather=weather, season=season)
            else:
                campaign = db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
                if not campaign:
                    raise KeyError(f"unknown campaign: {campaign_id}")
                current = datetime.fromisoformat(campaign["world_time"])
                updated = current + timedelta(minutes=minutes)
                next_weather = (weather or campaign["weather"]).strip()[:120]
                db.execute("UPDATE campaigns SET world_time=?,weather=?,updated_at=? WHERE id=?", (updated.isoformat(), next_weather, self._now(), campaign_id))
                rev = self._next_revision(db, campaign_id)
                self._insert_event(db, campaign_id, rev, "world_advance", reason, payload={"minutes": minutes, "old_time": campaign["world_time"], "new_time": updated.isoformat(), "weather": next_weather}, world_time_override=updated.isoformat())
                row = db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
                result = dict(row)
                result["settings"] = self._loads(result.pop("settings_json"))
            result["rules_time_update"] = RulesKernel(self)._expire_world_time_db(db, campaign_id)
            return result

    # ---------- simulation configuration (backend/admin; not exposed to GPT schema) ----------

    def simulation_config(self, campaign_id: str = "default") -> dict[str, Any]:
        return SimulationKernel(self).get_config(campaign_id)

    def set_simulation_seed(self, campaign_id: str, seed: int, *, reset_counter: bool = True) -> dict[str, Any]:
        return SimulationKernel(self).set_seed(campaign_id, seed, reset_counter=reset_counter)

    def save_simulation_rule(self, campaign_id: str, rule_id: str, archetype: str, **kwargs: Any) -> dict[str, Any]:
        return SimulationKernel(self).save_rule(campaign_id, rule_id, archetype, **kwargs)

    def list_simulation_rules(self, campaign_id: str = "default") -> list[dict[str, Any]]:
        return SimulationKernel(self).list_rules(campaign_id)

    def save_resource_node(self, campaign_id: str, node_id: str, location_id: str, item_id: str, **kwargs: Any) -> dict[str, Any]:
        return SimulationKernel(self).save_resource_node(campaign_id, node_id, location_id, item_id, **kwargs)

    def save_npc_need(self, campaign_id: str, npc_id: str, need: str, value: float, **kwargs: Any) -> dict[str, Any]:
        return SimulationKernel(self).save_need(campaign_id, npc_id, need, value, **kwargs)

    def save_npc_action(self, campaign_id: str, npc_id: str, action_id: str, **kwargs: Any) -> dict[str, Any]:
        return SimulationKernel(self).save_action(campaign_id, npc_id, action_id, **kwargs)

    def save_simulation_reaction(self, campaign_id: str, reaction_id: str, trigger_event_type: str, effects: Sequence[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        return SimulationKernel(self).save_reaction(campaign_id, reaction_id, trigger_event_type, effects, **kwargs)

    def save_item_def(self, campaign_id: str, item_id: str, name: str, **kwargs: Any) -> dict[str, Any]:
        return SimulationKernel(self).save_item_def(campaign_id, item_id, name, **kwargs)

    def set_inventory_item(self, campaign_id: str, owner_kind: str, owner_id: str, item_id: str, qty: float, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return SimulationKernel(self).set_inventory(campaign_id, owner_kind, owner_id, item_id, qty, metadata=metadata)

    def get_inventory_items(self, campaign_id: str, owner_kind: str, owner_id: str) -> list[dict[str, Any]]:
        return SimulationKernel(self).get_inventory(campaign_id, owner_kind, owner_id)

    def get_market_prices(self, campaign_id: str, location_id: str) -> list[dict[str, Any]]:
        return SimulationKernel(self).market_prices(campaign_id, location_id)

    def save_npc_lifecycle(self, campaign_id: str, npc_id: str, **kwargs: Any) -> dict[str, Any]:
        return SimulationKernel(self).save_lifecycle(campaign_id, npc_id, **kwargs)

    def get_npc_lifecycle(self, campaign_id: str, npc_id: str) -> dict[str, Any]:
        return SimulationKernel(self).get_lifecycle(campaign_id, npc_id)

    def set_drama_config(self, campaign_id: str, **kwargs: Any) -> dict[str, Any]:
        return SimulationKernel(self).set_drama_config(campaign_id, **kwargs)

    # ---------- NPC life / bounded planning (v3.8) ----------

    def npc_life_dispatch(self, operation: str, campaign_id: str = "default", payload: dict[str, Any] | None = None) -> Any:
        return NpcLifeKernel(self).dispatch(operation, campaign_id, payload)

    # ---------- World Engine 4.0 unified turn/context/knowledge router ----------

    def turn_router_dispatch(self, operation: str, campaign_id: str = "default", payload: dict[str, Any] | None = None) -> Any:
        return TurnRouter(self).dispatch(operation, campaign_id, payload)

    def resolve_turn(self, campaign_id: str = "default", **kwargs: Any) -> dict[str, Any]:
        return TurnRouter(self).resolve_turn(campaign_id, **kwargs)

    def compile_turn_context(self, campaign_id: str = "default", **kwargs: Any) -> dict[str, Any]:
        return TurnRouter(self).compile_context(campaign_id, **kwargs)

    def list_capabilities(self, campaign_id: str = "default", *, enabled_only: bool = True) -> list[dict[str, Any]]:
        return TurnRouter(self).list_capabilities(campaign_id, enabled_only=enabled_only)

    # ---------- World Engine 4.2.0 narrative director / dialogue / prose quality ----------

    def narrative_dispatch(self, operation: str, campaign_id: str = "default", payload: dict[str, Any] | None = None) -> Any:
        return NarrativeKernel(self).dispatch(operation, campaign_id, payload)

    def get_narrative_config(self, campaign_id: str = "default") -> dict[str, Any]:
        return NarrativeKernel(self).get_config(campaign_id)

    def configure_narrative(self, campaign_id: str = "default", **kwargs: Any) -> dict[str, Any]:
        return NarrativeKernel(self).configure(campaign_id, **kwargs)

    def build_narrative_packet(self, campaign_id: str = "default", **kwargs: Any) -> dict[str, Any]:
        return NarrativeKernel(self).build_packet(campaign_id, **kwargs)

    def check_narrative_quality(self, campaign_id: str, output_text: str, **kwargs: Any) -> dict[str, Any]:
        return NarrativeKernel(self).quality_check(campaign_id, output_text, **kwargs)

    def record_narrative_output(self, campaign_id: str, packet_id: str, output_text: str, **kwargs: Any) -> dict[str, Any]:
        return NarrativeKernel(self).record_output(campaign_id, packet_id, output_text, **kwargs)

    def verify_narrative_output(
        self,
        campaign_id: str,
        packet_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return NarrativeKernel(self).verify_accepted_output(campaign_id, packet_id, **kwargs)

    @staticmethod
    def _strict_publication_id(value: Any, *, max_chars: int = 128) -> str:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= max_chars
            or re.fullmatch(r"[A-Za-z0-9._:-]+", value) is None
        ):
            raise ValueError("PUBLICATION_ID_INVALID")
        return value

    def _canonical_publication_candidate(
        self,
        campaign_id: str,
        presentation_id: str,
        packet_id: str,
        narration: str,
        *,
        expected_revision: int,
        turn_id: str,
        choices: Sequence[str],
        presentation: dict[str, Any] | None,
    ) -> dict[str, Any]:
        clean_campaign_id = self._strict_publication_id(campaign_id, max_chars=100)
        clean_packet_id = self._strict_publication_id(packet_id, max_chars=160)
        clean_turn_id = self._strict_publication_id(turn_id)
        clean_presentation_id = self._strict_publication_id(presentation_id)
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ValueError("PRESENTATION_REVISION_INVALID")
        validate_public_text(narration, max_chars=24_000)
        if isinstance(choices, (str, bytes)) or not isinstance(choices, Sequence):
            raise ValueError("PRESENTATION_CHOICES_INVALID")
        exact_choices = list(choices)
        if len(exact_choices) > 12:
            raise ValueError("PRESENTATION_CHOICES_INVALID")
        for choice in exact_choices:
            validate_public_text(choice, max_chars=500)

        closed_presentation = {
            "presentation_version": _PRESENTATION_VERSION,
            "kind": "narrative",
            "presentation_id": clean_presentation_id,
        }
        if presentation is not None and not isinstance(presentation, dict):
            raise ValueError("PRESENTATION_SCHEMA_CLOSED")
        supplied_presentation = dict(presentation or {})
        if supplied_presentation and supplied_presentation != closed_presentation:
            raise ValueError("PRESENTATION_SCHEMA_CLOSED")
        return {
            "candidate_version": _PUBLICATION_CANDIDATE_VERSION,
            "campaign_id": clean_campaign_id,
            "packet_id": clean_packet_id,
            "turn_id": clean_turn_id,
            "authoritative_revision": expected_revision,
            "narration": narration,
            "choices": exact_choices,
            "presentation": closed_presentation,
        }

    @staticmethod
    def _publication_candidate_digest(candidate: dict[str, Any]) -> str:
        return hashlib.sha256(canonical_json_bytes(candidate)).hexdigest()

    def _decode_stored_publication_candidate(
        self,
        canonical_candidate_json: str,
        candidate_digest: str,
    ) -> dict[str, Any]:
        try:
            candidate = json.loads(canonical_candidate_json)
        except (TypeError, ValueError) as exc:
            raise ValueError("PUBLICATION_ATTEMPT_INTEGRITY_FAILED") from exc
        if not isinstance(candidate, dict) or set(candidate) != _PUBLICATION_CANDIDATE_KEYS:
            raise ValueError("PUBLICATION_ATTEMPT_INTEGRITY_FAILED")
        presentation = candidate.get("presentation")
        if not isinstance(presentation, dict) or set(presentation) != _CLOSED_PRESENTATION_KEYS:
            raise ValueError("PUBLICATION_ATTEMPT_INTEGRITY_FAILED")
        rebuilt = self._canonical_publication_candidate(
            candidate.get("campaign_id"),
            presentation.get("presentation_id"),
            candidate.get("packet_id"),
            candidate.get("narration"),
            expected_revision=candidate.get("authoritative_revision"),
            turn_id=candidate.get("turn_id"),
            choices=candidate.get("choices"),
            presentation=presentation,
        )
        canonical = canonical_json_bytes(rebuilt).decode("utf-8")
        if (
            candidate.get("candidate_version") != _PUBLICATION_CANDIDATE_VERSION
            or canonical != canonical_candidate_json
            or self._publication_candidate_digest(rebuilt) != candidate_digest
        ):
            raise ValueError("PUBLICATION_ATTEMPT_INTEGRITY_FAILED")
        return rebuilt

    def _publication_packet_binding_db(
        self,
        db: sqlite3.Connection,
        candidate: dict[str, Any],
    ) -> tuple[dict[str, Any], int]:
        campaign_id = candidate["campaign_id"]
        packet_id = candidate["packet_id"]
        campaign = db.execute(
            "SELECT revision FROM campaigns WHERE id=?", (campaign_id,)
        ).fetchone()
        if campaign is None:
            raise KeyError("CAMPAIGN_NOT_FOUND")
        row = db.execute(
            """SELECT turn_id,packet_version,packet_json,digest
               FROM we4_narrative_packets
               WHERE campaign_id=? AND packet_id=?""",
            (campaign_id, packet_id),
        ).fetchone()
        if row is None:
            raise KeyError("NARRATIVE_PACKET_NOT_FOUND")
        try:
            packet = self._loads(row["packet_json"])
        except (TypeError, ValueError) as exc:
            raise ValueError("NARRATIVE_PACKET_INTEGRITY_FAILED") from exc
        if not isinstance(packet, dict):
            raise ValueError("NARRATIVE_PACKET_INTEGRITY_FAILED")
        narrative = NarrativeKernel(self)
        authority = packet.get("authority")
        authoritative_state = (
            authority.get("authoritative_state")
            if isinstance(authority, dict)
            else None
        )
        campaign_state = (
            authoritative_state.get("campaign")
            if isinstance(authoritative_state, dict)
            else None
        )
        scene = packet.get("scene")
        packet_revision = (
            campaign_state.get("revision")
            if isinstance(campaign_state, dict)
            else None
        )
        packet_choices = (
            scene.get("choice_options") if isinstance(scene, dict) else None
        )
        if (
            not isinstance(packet, dict)
            or packet.get("packet_version") != narrative.PACKET_VERSION
            or row["packet_version"] != narrative.PACKET_VERSION
            or packet.get("campaign_id") != campaign_id
            or packet.get("packet_id") != packet_id
            or packet.get("turn_id") != candidate["turn_id"]
            or row["turn_id"] != candidate["turn_id"]
            or isinstance(packet_revision, bool)
            or not isinstance(packet_revision, int)
            or packet_revision != candidate["authoritative_revision"]
            or packet.get("digest") != row["digest"]
            or not narrative._verify_packet_hash(packet)
        ):
            raise ValueError("PRESENTATION_PACKET_BINDING_FAILED")
        if (
            not isinstance(packet_choices, list)
            or any(not isinstance(choice, str) for choice in packet_choices)
            or packet_choices != candidate["choices"]
        ):
            raise ValueError("PRESENTATION_CHOICES_MISMATCH")
        return packet, int(campaign["revision"])

    def _accepted_publication_result_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        packet_id: str,
        candidate_digest: str,
        *,
        replayed: bool,
    ) -> dict[str, Any]:
        row = db.execute(
            """SELECT a.*,p.canonical_bytes,p.content_sha256,
                      p.packet_id AS presentation_packet_id,
                      p.accepted_output_id AS presentation_output_id,
                      o.payload_bytes,o.status AS delivery_status,
                      o.presentation_id AS outbox_presentation_id,
                      o.packet_id AS outbox_packet_id,
                      o.accepted_output_id AS outbox_output_id,
                      n.packet_id AS output_packet_id,
                      n.output_text AS accepted_output_text,
                      n.output_hash AS accepted_output_hash,
                      n.accepted AS output_accepted,
                      q.packet_id AS receipt_packet_id,
                      q.output_hash AS receipt_output_hash,
                      q.hard_pass AS receipt_hard_pass,
                      q.revision_required AS receipt_revision_required
               FROM we43_narrative_packet_acceptances AS a
               JOIN we_companion_presentations AS p
                 ON p.campaign_id=a.campaign_id AND p.presentation_id=a.presentation_id
               JOIN we_companion_outbox AS o
                 ON o.campaign_id=a.campaign_id AND o.outbox_id=a.outbox_id
               JOIN we4_narrative_outputs AS n
                 ON n.campaign_id=a.campaign_id AND n.output_id=a.accepted_output_id
               JOIN we4_narrative_quality_receipts AS q
                 ON q.campaign_id=a.campaign_id AND q.receipt_id=a.receipt_id
               WHERE a.campaign_id=? AND a.packet_id=?""",
            (campaign_id, packet_id),
        ).fetchone()
        if row is None or row["candidate_digest"] != candidate_digest:
            raise ValueError("PUBLICATION_ACCEPTANCE_INTEGRITY_FAILED")
        raw = bytes(row["canonical_bytes"])
        if (
            bytes(row["payload_bytes"]) != raw
            or hashlib.sha256(raw).hexdigest() != row["content_sha256"]
        ):
            raise ValueError("PUBLICATION_ACCEPTANCE_INTEGRITY_FAILED")
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (KeyError, TypeError, UnicodeDecodeError, ValueError) as exc:
            raise ValueError("PUBLICATION_ACCEPTANCE_INTEGRITY_FAILED") from exc
        presentation = envelope.get("presentation") if isinstance(envelope, dict) else None
        evidence = (
            presentation.get("narrative_evidence")
            if isinstance(presentation, dict)
            else None
        )
        if (
            not isinstance(envelope, dict)
            or set(envelope) != _PUBLICATION_ENVELOPE_KEYS
            or not isinstance(presentation, dict)
            or set(presentation) != _ACCEPTED_PRESENTATION_KEYS
            or not isinstance(evidence, dict)
            or set(evidence) != _ACCEPTED_EVIDENCE_KEYS
            or envelope.get("campaign_id") != campaign_id
            or envelope.get("turn_id") != evidence.get("turn_id")
            or envelope.get("revision") != evidence.get("authoritative_revision")
            or evidence.get("packet_id") != packet_id
            or evidence.get("output_id") != row["accepted_output_id"]
            or evidence.get("receipt_id") != row["receipt_id"]
            or envelope.get("presentation_id") != row["presentation_id"]
            or presentation.get("presentation_id") != row["presentation_id"]
            or presentation.get("presentation_version") != _PRESENTATION_VERSION
            or presentation.get("kind") != "narrative"
            or row["presentation_packet_id"] != packet_id
            or row["presentation_output_id"] != row["accepted_output_id"]
            or row["outbox_presentation_id"] != row["presentation_id"]
            or row["outbox_packet_id"] != packet_id
            or row["outbox_output_id"] != row["accepted_output_id"]
            or row["output_packet_id"] != packet_id
            or not bool(row["output_accepted"])
            or row["receipt_packet_id"] != packet_id
            or not bool(row["receipt_hard_pass"])
            or bool(row["receipt_revision_required"])
            or row["accepted_output_hash"] != evidence.get("output_hash")
            or row["receipt_output_hash"] != evidence.get("output_hash")
            or envelope.get("narration") != row["accepted_output_text"]
            or hashlib.sha256(
                str(row["accepted_output_text"]).encode("utf-8")
            ).hexdigest()
            != evidence.get("output_hash")
        ):
            raise ValueError("PUBLICATION_ACCEPTANCE_INTEGRITY_FAILED")
        try:
            captured_envelope = PresentationEnvelope(
                campaign_id=envelope["campaign_id"],
                presentation_id=envelope["presentation_id"],
                revision=envelope["revision"],
                narration=envelope["narration"],
                turn_id=envelope["turn_id"],
                choices=tuple(envelope["choices"]),
                presentation=presentation,
            )
            candidate_presentation = {
                "presentation_version": presentation["presentation_version"],
                "kind": presentation["kind"],
                "presentation_id": presentation["presentation_id"],
            }
            captured_candidate = self._canonical_publication_candidate(
                envelope["campaign_id"],
                envelope["presentation_id"],
                packet_id,
                envelope["narration"],
                expected_revision=envelope["revision"],
                turn_id=envelope["turn_id"],
                choices=envelope["choices"],
                presentation=candidate_presentation,
            )
            bound_packet, _current_revision = self._publication_packet_binding_db(
                db, captured_candidate
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("PUBLICATION_ACCEPTANCE_INTEGRITY_FAILED") from exc
        if (
            captured_envelope.canonical_bytes() != raw
            or self._publication_candidate_digest(captured_candidate) != candidate_digest
            or evidence["packet_digest"] != bound_packet["digest"]
        ):
            raise ValueError("PUBLICATION_ACCEPTANCE_INTEGRITY_FAILED")
        evidence_payload = dict(evidence)
        evidence_digest = evidence_payload.pop("evidence_digest", None)
        if (
            not isinstance(evidence_digest, str)
            or hashlib.sha256(canonical_json_bytes(evidence_payload)).hexdigest()
            != evidence_digest
        ):
            raise ValueError("PUBLICATION_ACCEPTANCE_INTEGRITY_FAILED")
        quality_receipt = {
            "receipt_version": evidence["receipt_version"],
            "receipt_id": evidence["receipt_id"],
            "packet_id": packet_id,
            "output_hash": evidence["output_hash"],
            "hard_pass": True,
            "revision_required": False,
            "semantic_review_attested": row["acceptance_mode"] == "semantic_attested",
        }
        delivery_status = str(row["delivery_status"])
        presentation_result = dict(envelope)
        presentation_result.update(
            {
                "content_sha256": row["content_sha256"],
                "outbox_id": row["outbox_id"],
                "packet_id": packet_id,
                "accepted_output_id": row["accepted_output_id"],
            }
        )
        return {
            "status": "accepted",
            "published": True,
            "accepted": True,
            "replayed": replayed,
            "campaign_id": campaign_id,
            "packet_id": packet_id,
            "attempt_id": row["attempt_id"],
            "candidate_digest": candidate_digest,
            "accepted_output_id": row["accepted_output_id"],
            "presentation_id": row["presentation_id"],
            "outbox_id": row["outbox_id"],
            "campaign_revision": evidence["authoritative_revision"],
            "acceptance_mode": row["acceptance_mode"],
            "delivery": "queued" if delivery_status == "pending" else delivery_status,
            "delivery_status": delivery_status,
            "consumption_scope": "server_authorized_director_only",
            "quality_receipt": quality_receipt,
            "narrative_evidence": evidence,
            "presentation": presentation_result,
        }

    def _existing_publication_result_db(
        self,
        db: sqlite3.Connection,
        candidate: dict[str, Any],
        candidate_digest: str,
    ) -> dict[str, Any] | None:
        row = db.execute(
            """SELECT candidate_digest
               FROM we43_narrative_packet_acceptances
               WHERE campaign_id=? AND packet_id=?""",
            (candidate["campaign_id"], candidate["packet_id"]),
        ).fetchone()
        if row is None:
            return None
        if row["candidate_digest"] != candidate_digest:
            raise ValueError("PRESENTATION_PACKET_ALREADY_ACCEPTED")
        return self._accepted_publication_result_db(
            db,
            candidate["campaign_id"],
            candidate["packet_id"],
            candidate_digest,
            replayed=True,
        )

    @staticmethod
    def _publication_attempt_id(candidate_digest: str) -> str:
        return f"pubatt_{candidate_digest}"

    def _publication_attempt_response(
        self,
        candidate: dict[str, Any],
        candidate_digest: str,
        attempt_id: str,
        *,
        status: str,
        reason_codes: Sequence[str],
    ) -> dict[str, Any]:
        public_status = "rejected" if status in {"rejected", "semantic_rejected"} else status
        response: dict[str, Any] = {
            "status": public_status,
            "published": False,
            "accepted": False,
            "campaign_id": candidate["campaign_id"],
            "packet_id": candidate["packet_id"],
            "presentation_id": candidate["presentation"]["presentation_id"],
            "attempt_id": attempt_id,
            "candidate_digest": candidate_digest,
            "reason_codes": list(reason_codes),
        }
        if status == "semantic_review_required":
            response["next_step"] = "operator_review"
        else:
            response["quality_receipt"] = {
                "hard_pass": False,
                "revision_required": True,
            }
        return response

    @staticmethod
    def _deterministic_rejection_codes(receipt: dict[str, Any]) -> tuple[str, ...]:
        if receipt.get("hard_failures"):
            return ("DETERMINISTIC_QUALITY_REJECTED",)
        return ("DETERMINISTIC_REVISION_REQUIRED",)

    def _record_publication_attempt(
        self,
        candidate: dict[str, Any],
        candidate_digest: str,
        *,
        status: str,
        reason_codes: Sequence[str],
    ) -> dict[str, Any]:
        if status not in {"rejected", "semantic_review_required"}:
            raise ValueError("PUBLICATION_ATTEMPT_STATUS_INVALID")
        if self._publication_candidate_digest(candidate) != candidate_digest:
            raise ValueError("PUBLICATION_CANDIDATE_DIGEST_MISMATCH")
        attempt_id = self._publication_attempt_id(candidate_digest)
        canonical = canonical_json_bytes(candidate).decode("utf-8")
        with self._write_db() as db:
            existing_result = self._existing_publication_result_db(
                db, candidate, candidate_digest
            )
            if existing_result is not None:
                return existing_result
            _packet, current_revision = self._publication_packet_binding_db(db, candidate)
            if current_revision != candidate["authoritative_revision"]:
                raise ValueError("STALE_PRESENTATION_REVISION")
            existing = db.execute(
                """SELECT attempt_id,candidate_version,status
                   FROM we43_narrative_publication_attempts
                   WHERE campaign_id=? AND packet_id=? AND candidate_digest=?""",
                (
                    candidate["campaign_id"],
                    candidate["packet_id"],
                    candidate_digest,
                ),
            ).fetchone()
            if existing is not None:
                if (
                    existing["attempt_id"] != attempt_id
                    or existing["candidate_version"] != _PUBLICATION_CANDIDATE_VERSION
                ):
                    raise ValueError("PUBLICATION_ATTEMPT_INTEGRITY_FAILED")
                if existing["status"] == "semantic_rejected":
                    return self._publication_attempt_response(
                        candidate,
                        candidate_digest,
                        attempt_id,
                        status="semantic_rejected",
                        reason_codes=("SEMANTIC_ATTESTATION_REJECTED",),
                    )
                if existing["status"] == "accepted":
                    raise ValueError("PUBLICATION_ACCEPTANCE_INTEGRITY_FAILED")
                db.execute(
                    """UPDATE we43_narrative_publication_attempts
                       SET canonical_candidate_json=?,status=?,reason_codes_json=?,updated_at=?
                       WHERE campaign_id=? AND attempt_id=?""",
                    (
                        None if status == "rejected" else canonical,
                        status,
                        self._dumps(list(reason_codes)),
                        self._now(),
                        candidate["campaign_id"],
                        attempt_id,
                    ),
                )
            else:
                now = self._now()
                db.execute(
                    """INSERT INTO we43_narrative_publication_attempts(
                           campaign_id,attempt_id,packet_id,candidate_version,candidate_digest,
                           canonical_candidate_json,status,reason_codes_json,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        candidate["campaign_id"],
                        attempt_id,
                        candidate["packet_id"],
                        _PUBLICATION_CANDIDATE_VERSION,
                        candidate_digest,
                        None if status == "rejected" else canonical,
                        status,
                        self._dumps(list(reason_codes)),
                        now,
                        now,
                    ),
                )
        return self._publication_attempt_response(
            candidate,
            candidate_digest,
            attempt_id,
            status=status,
            reason_codes=reason_codes,
        )

    def _accept_publication_candidate(
        self,
        candidate: dict[str, Any],
        candidate_digest: str,
        receipt: dict[str, Any],
        *,
        attempt_id: str,
        acceptance_mode: str,
        attestation: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if (
            acceptance_mode not in {"deterministic", "semantic_attested"}
            or self._publication_candidate_digest(candidate) != candidate_digest
            or attempt_id != self._publication_attempt_id(candidate_digest)
        ):
            raise ValueError("PUBLICATION_CANDIDATE_DIGEST_MISMATCH")
        semantic_review = receipt.get("semantic_review")
        if (
            not isinstance(semantic_review, dict)
            or not isinstance(semantic_review.get("required"), bool)
            or (
                acceptance_mode == "deterministic"
                and semantic_review["required"] is not False
            )
            or (
                acceptance_mode == "semantic_attested"
                and semantic_review["required"] is not True
            )
        ):
            raise ValueError("SEMANTIC_ATTESTATION_BINDING_FAILED")
        canonical = canonical_json_bytes(candidate).decode("utf-8")
        narrative = NarrativeKernel(self)
        try:
            with self._write_db() as db:
                existing_result = self._existing_publication_result_db(
                    db, candidate, candidate_digest
                )
                if existing_result is not None:
                    return existing_result
                packet, current_revision = self._publication_packet_binding_db(db, candidate)
                if current_revision != candidate["authoritative_revision"]:
                    raise ValueError("STALE_PRESENTATION_REVISION")

                attempt = db.execute(
                    """SELECT attempt_id,candidate_version,canonical_candidate_json,status
                       FROM we43_narrative_publication_attempts
                       WHERE campaign_id=? AND packet_id=? AND candidate_digest=?""",
                    (
                        candidate["campaign_id"],
                        candidate["packet_id"],
                        candidate_digest,
                    ),
                ).fetchone()
                if attempt is not None and (
                    attempt["attempt_id"] != attempt_id
                    or attempt["candidate_version"] != _PUBLICATION_CANDIDATE_VERSION
                    or attempt["status"] == "semantic_rejected"
                ):
                    raise ValueError("PUBLICATION_ATTEMPT_CONFLICT")
                if acceptance_mode == "semantic_attested":
                    if (
                        attestation is None
                        or attempt is None
                        or attempt["status"] != "semantic_review_required"
                        or attempt["canonical_candidate_json"] != canonical
                    ):
                        raise ValueError("SEMANTIC_ATTESTATION_BINDING_FAILED")
                    if db.execute(
                        """SELECT 1 FROM we43_narrative_semantic_attestations
                           WHERE campaign_id=? AND packet_id=? AND candidate_digest=?""",
                        (
                            candidate["campaign_id"],
                            candidate["packet_id"],
                            candidate_digest,
                        ),
                    ).fetchone() is not None:
                        raise ValueError("SEMANTIC_ATTESTATION_CONFLICT")
                    db.execute(
                        """INSERT INTO we43_narrative_semantic_attestations(
                               campaign_id,attestation_id,attempt_id,packet_id,candidate_digest,
                               authority_kind,reviewer_id,decision,created_at)
                           VALUES(?,?,?,?,?,?,?,'approve',?)""",
                        (
                            candidate["campaign_id"],
                            attestation["attestation_id"],
                            attempt_id,
                            candidate["packet_id"],
                            candidate_digest,
                            attestation["authority_kind"],
                            attestation["reviewer_id"],
                            self._now(),
                        ),
                    )
                    semantic_attestation_id: str | None = attestation["attestation_id"]
                elif attestation is not None:
                    raise ValueError("SEMANTIC_ATTESTATION_BINDING_FAILED")
                else:
                    semantic_attestation_id = None

                if attempt is None:
                    now = self._now()
                    db.execute(
                        """INSERT INTO we43_narrative_publication_attempts(
                               campaign_id,attempt_id,packet_id,candidate_version,candidate_digest,
                               canonical_candidate_json,status,reason_codes_json,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,'accepted','[]',?,?)""",
                        (
                            candidate["campaign_id"],
                            attempt_id,
                            candidate["packet_id"],
                            _PUBLICATION_CANDIDATE_VERSION,
                            candidate_digest,
                            canonical,
                            now,
                            now,
                        ),
                    )
                else:
                    db.execute(
                        """UPDATE we43_narrative_publication_attempts
                           SET canonical_candidate_json=?,status='accepted',
                               reason_codes_json='[]',updated_at=?
                           WHERE campaign_id=? AND attempt_id=?""",
                        (
                            canonical,
                            self._now(),
                            candidate["campaign_id"],
                            attempt_id,
                        ),
                    )

                recorded = narrative.accept_publication_output_db(
                    db,
                    candidate["campaign_id"],
                    candidate["packet_id"],
                    candidate["narration"],
                    packet=packet,
                    receipt=receipt,
                    metadata={
                        "purpose": "companion_publication",
                        "presentation_id": candidate["presentation"]["presentation_id"],
                        "publication_candidate_digest": candidate_digest,
                    },
                )
                evidence = {
                    "verification_version": "NOV-1.0",
                    "campaign_id": candidate["campaign_id"],
                    "packet_id": candidate["packet_id"],
                    "turn_id": candidate["turn_id"],
                    "authoritative_revision": candidate["authoritative_revision"],
                    "packet_digest": packet["digest"],
                    "packet_version": packet["packet_version"],
                    "output_id": recorded["output_id"],
                    "output_hash": recorded["output_hash"],
                    "receipt_id": receipt["receipt_id"],
                    "receipt_version": narrative.RECEIPT_VERSION,
                    "accepted": True,
                    "hard_pass": True,
                }
                evidence["evidence_digest"] = hashlib.sha256(
                    canonical_json_bytes(evidence)
                ).hexdigest()
                closed_presentation = dict(candidate["presentation"])
                closed_presentation["narrative_evidence"] = evidence
                envelope = PresentationEnvelope(
                    campaign_id=candidate["campaign_id"],
                    presentation_id=candidate["presentation"]["presentation_id"],
                    revision=candidate["authoritative_revision"],
                    narration=candidate["narration"],
                    turn_id=candidate["turn_id"],
                    choices=tuple(candidate["choices"]),
                    presentation=closed_presentation,
                )
                companion_result = CompanionService(self).enqueue_presentation_db(db, envelope)
                outbox_id = str(companion_result["outbox_id"])

                # (campaign_id, packet_id) is the authoritative one-acceptance
                # decision key. This final insert records that decision; the
                # surrounding transaction becomes visible atomically at commit.
                db.execute(
                    """INSERT INTO we43_narrative_packet_acceptances(
                           campaign_id,packet_id,attempt_id,candidate_digest,
                           accepted_output_id,receipt_id,presentation_id,outbox_id,
                           acceptance_mode,semantic_attestation_id,accepted_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        candidate["campaign_id"],
                        candidate["packet_id"],
                        attempt_id,
                        candidate_digest,
                        recorded["output_id"],
                        receipt["receipt_id"],
                        candidate["presentation"]["presentation_id"],
                        outbox_id,
                        acceptance_mode,
                        semantic_attestation_id,
                        self._now(),
                    ),
                )
                return self._accepted_publication_result_db(
                    db,
                    candidate["campaign_id"],
                    candidate["packet_id"],
                    candidate_digest,
                    replayed=False,
                )
        except sqlite3.IntegrityError as exc:
            with self._db() as db:
                result = self._existing_publication_result_db(
                    db, candidate, candidate_digest
                )
            if result is not None:
                return result
            raise ValueError("PUBLICATION_TRANSACTION_CONFLICT") from exc

    def _reject_semantic_publication_attempt(
        self,
        candidate: dict[str, Any],
        candidate_digest: str,
        attempt_id: str,
        attestation: dict[str, str],
    ) -> dict[str, Any]:
        canonical = canonical_json_bytes(candidate).decode("utf-8")
        with self._write_db() as db:
            existing_result = self._existing_publication_result_db(
                db, candidate, candidate_digest
            )
            if existing_result is not None:
                raise ValueError("SEMANTIC_ATTESTATION_CONFLICT")
            _packet, current_revision = self._publication_packet_binding_db(db, candidate)
            if current_revision != candidate["authoritative_revision"]:
                raise ValueError("STALE_PRESENTATION_REVISION")
            attempt = db.execute(
                """SELECT canonical_candidate_json,status
                   FROM we43_narrative_publication_attempts
                   WHERE campaign_id=? AND attempt_id=? AND packet_id=? AND candidate_digest=?""",
                (
                    candidate["campaign_id"],
                    attempt_id,
                    candidate["packet_id"],
                    candidate_digest,
                ),
            ).fetchone()
            if (
                attempt is None
                or attempt["canonical_candidate_json"] != canonical
                or attempt["status"] not in {"semantic_review_required", "semantic_rejected"}
            ):
                raise ValueError("SEMANTIC_ATTESTATION_BINDING_FAILED")
            existing = db.execute(
                """SELECT attestation_id,authority_kind,reviewer_id,decision
                   FROM we43_narrative_semantic_attestations
                   WHERE campaign_id=? AND packet_id=? AND candidate_digest=?""",
                (
                    candidate["campaign_id"],
                    candidate["packet_id"],
                    candidate_digest,
                ),
            ).fetchone()
            if existing is not None:
                if (
                    existing["attestation_id"] != attestation["attestation_id"]
                    or existing["authority_kind"] != attestation["authority_kind"]
                    or existing["reviewer_id"] != attestation["reviewer_id"]
                    or existing["decision"] != "reject"
                ):
                    raise ValueError("SEMANTIC_ATTESTATION_CONFLICT")
            else:
                db.execute(
                    """INSERT INTO we43_narrative_semantic_attestations(
                           campaign_id,attestation_id,attempt_id,packet_id,candidate_digest,
                           authority_kind,reviewer_id,decision,created_at)
                       VALUES(?,?,?,?,?,?,?,'reject',?)""",
                    (
                        candidate["campaign_id"],
                        attestation["attestation_id"],
                        attempt_id,
                        candidate["packet_id"],
                        candidate_digest,
                        attestation["authority_kind"],
                        attestation["reviewer_id"],
                        self._now(),
                    ),
                )
            db.execute(
                """UPDATE we43_narrative_publication_attempts
                   SET status='semantic_rejected',reason_codes_json=?,updated_at=?
                   WHERE campaign_id=? AND attempt_id=?""",
                (
                    self._dumps(["SEMANTIC_ATTESTATION_REJECTED"]),
                    self._now(),
                    candidate["campaign_id"],
                    attempt_id,
                ),
            )
        return self._publication_attempt_response(
            candidate,
            candidate_digest,
            attempt_id,
            status="semantic_rejected",
            reason_codes=("SEMANTIC_ATTESTATION_REJECTED",),
        )

    def _attest_publication_attempt(
        self,
        campaign_id: str,
        attempt_id: str,
        *,
        authority_kind: str,
        reviewer_id: str,
        decision: str,
    ) -> dict[str, Any]:
        """Internal-only exact-candidate semantic review and acceptance entrypoint."""
        clean_campaign_id = self._strict_publication_id(campaign_id, max_chars=100)
        clean_attempt_id = self._strict_publication_id(attempt_id)
        clean_reviewer_id = self._strict_publication_id(reviewer_id)
        if authority_kind not in {"human", "trusted_server"}:
            raise ValueError("SEMANTIC_AUTHORITY_INVALID")
        if decision not in {"approve", "reject"}:
            raise ValueError("SEMANTIC_DECISION_INVALID")
        attestation_id = "natt_" + hashlib.sha256(
            canonical_json_bytes(
                [clean_campaign_id, clean_attempt_id, authority_kind, clean_reviewer_id, decision]
            )
        ).hexdigest()[:32]
        attestation = {
            "attestation_id": attestation_id,
            "authority_kind": authority_kind,
            "reviewer_id": clean_reviewer_id,
        }
        with self._turn_lock:
            with self._db() as db:
                attempt = db.execute(
                    """SELECT packet_id,candidate_digest,canonical_candidate_json,status
                       FROM we43_narrative_publication_attempts
                       WHERE campaign_id=? AND attempt_id=?""",
                    (clean_campaign_id, clean_attempt_id),
                ).fetchone()
                if attempt is None:
                    raise KeyError("PUBLICATION_ATTEMPT_NOT_FOUND")
                accepted = db.execute(
                    """SELECT candidate_digest
                       FROM we43_narrative_packet_acceptances
                       WHERE campaign_id=? AND packet_id=?""",
                    (clean_campaign_id, attempt["packet_id"]),
                ).fetchone()
                if accepted is not None:
                    if (
                        decision != "approve"
                        or accepted["candidate_digest"] != attempt["candidate_digest"]
                    ):
                        raise ValueError("SEMANTIC_ATTESTATION_CONFLICT")
                    return self._accepted_publication_result_db(
                        db,
                        clean_campaign_id,
                        attempt["packet_id"],
                        attempt["candidate_digest"],
                        replayed=True,
                    )
                if (
                    attempt["status"] != "semantic_review_required"
                    or not attempt["canonical_candidate_json"]
                ):
                    raise ValueError("SEMANTIC_ATTESTATION_BINDING_FAILED")
                candidate = self._decode_stored_publication_candidate(
                    attempt["canonical_candidate_json"], attempt["candidate_digest"]
                )
                if candidate["campaign_id"] != clean_campaign_id:
                    raise ValueError("SEMANTIC_ATTESTATION_BINDING_FAILED")
                candidate_digest = attempt["candidate_digest"]

            if decision == "reject":
                return self._reject_semantic_publication_attempt(
                    candidate,
                    candidate_digest,
                    clean_attempt_id,
                    attestation,
                )

            receipt = NarrativeKernel(self).quality_check(
                clean_campaign_id,
                candidate["narration"],
                packet_id=candidate["packet_id"],
                record=False,
                publication_read_only=True,
            )
            if not receipt.get("hard_pass") or receipt.get("revision_required"):
                return self._record_publication_attempt(
                    candidate,
                    candidate_digest,
                    status="rejected",
                    reason_codes=self._deterministic_rejection_codes(receipt),
                )
            return self._accept_publication_candidate(
                candidate,
                candidate_digest,
                receipt,
                attempt_id=clean_attempt_id,
                acceptance_mode="semantic_attested",
                attestation=attestation,
            )

    def publication_attempt_for_review(
        self, campaign_id: str, attempt_id: str
    ) -> dict[str, Any]:
        """Return one exact public candidate for a trusted local reviewer."""
        clean_campaign_id = self._strict_publication_id(campaign_id, max_chars=100)
        clean_attempt_id = self._strict_publication_id(attempt_id)
        with self._db() as db:
            row = db.execute(
                """SELECT packet_id,candidate_digest,canonical_candidate_json,
                          status,reason_codes_json,created_at,updated_at
                   FROM we43_narrative_publication_attempts
                   WHERE campaign_id=? AND attempt_id=?""",
                (clean_campaign_id, clean_attempt_id),
            ).fetchone()
        if row is None:
            raise KeyError("PUBLICATION_ATTEMPT_NOT_FOUND")
        candidate = None
        if row["canonical_candidate_json"]:
            candidate = self._decode_stored_publication_candidate(
                row["canonical_candidate_json"], row["candidate_digest"]
            )
        return {
            "campaign_id": clean_campaign_id,
            "attempt_id": clean_attempt_id,
            "packet_id": row["packet_id"],
            "candidate_digest": row["candidate_digest"],
            "status": row["status"],
            "reason_codes": self._loads(row["reason_codes_json"] or "[]"),
            "candidate": candidate,
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }

    def attest_publication_attempt(
        self,
        campaign_id: str,
        attempt_id: str,
        *,
        authority_kind: str,
        reviewer_id: str,
        decision: str,
    ) -> dict[str, Any]:
        """Trusted local/server semantic decision bound to an exact candidate."""
        return self._attest_publication_attempt(
            campaign_id,
            attempt_id,
            authority_kind=authority_kind,
            reviewer_id=reviewer_id,
            decision=decision,
        )

    def latest_accepted_presentation(self, campaign_id: str) -> dict[str, Any]:
        """Return the latest public envelope after validating its acceptance chain."""
        clean_campaign_id = self._strict_publication_id(campaign_id, max_chars=100)
        with self._db() as db:
            row = db.execute(
                """SELECT packet_id,candidate_digest,accepted_at
                   FROM we43_narrative_packet_acceptances
                   WHERE campaign_id=?
                   ORDER BY accepted_at DESC,packet_id DESC
                   LIMIT 1""",
                (clean_campaign_id,),
            ).fetchone()
            if row is None:
                return {
                    "campaign_id": clean_campaign_id,
                    "presentation": None,
                }
            accepted = self._accepted_publication_result_db(
                db,
                clean_campaign_id,
                str(row["packet_id"]),
                str(row["candidate_digest"]),
                replayed=True,
            )
            stored = accepted["presentation"]
            public_envelope = {
                key: stored[key] for key in _PUBLICATION_ENVELOPE_KEYS
            }
            return {
                "campaign_id": clean_campaign_id,
                "presentation": public_envelope,
                "content_sha256": str(stored["content_sha256"]),
                "accepted_at": str(row["accepted_at"]),
            }

    def publish_presentation(
        self,
        campaign_id: str,
        presentation_id: str,
        packet_id: str,
        narration: str,
        *,
        expected_revision: int,
        turn_id: str,
        choices: Sequence[str] = (),
        presentation: dict[str, Any] | None = None,
        communicated_fact_ids: Sequence[str] = (),
        motifs_used: Sequence[str] = (),
        beat_realizations: Sequence[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        """Prevalidate and atomically accept one exact public packet candidate."""
        # Model declarations are intentionally excluded from both the
        # candidate and acceptance. Only state derivable from the immutable
        # server packet is consumed by the same-connection primitive.
        _ = communicated_fact_ids, motifs_used, beat_realizations
        candidate = self._canonical_publication_candidate(
            campaign_id,
            presentation_id,
            packet_id,
            narration,
            expected_revision=expected_revision,
            turn_id=turn_id,
            choices=choices,
            presentation=presentation,
        )
        candidate_digest = self._publication_candidate_digest(candidate)
        attempt_id = self._publication_attempt_id(candidate_digest)

        with self._turn_lock:
            # Read-only authoritative/bounds checks and the acceptance replay
            # fence precede deterministic quality validation and all mutation.
            with self._db() as db:
                existing_result = self._existing_publication_result_db(
                    db, candidate, candidate_digest
                )
                if existing_result is not None:
                    return existing_result
                _packet, current_revision = self._publication_packet_binding_db(
                    db, candidate
                )
            if current_revision != expected_revision:
                raise ValueError("STALE_PRESENTATION_REVISION")

            receipt = NarrativeKernel(self).quality_check(
                candidate["campaign_id"],
                candidate["narration"],
                packet_id=candidate["packet_id"],
                record=False,
                publication_read_only=True,
            )
            if not receipt.get("hard_pass") or receipt.get("revision_required"):
                return self._record_publication_attempt(
                    candidate,
                    candidate_digest,
                    status="rejected",
                    reason_codes=self._deterministic_rejection_codes(receipt),
                )
            semantic_required = bool(
                (receipt.get("semantic_review") or {}).get("required")
            )
            if semantic_required:
                return self._record_publication_attempt(
                    candidate,
                    candidate_digest,
                    status="semantic_review_required",
                    reason_codes=("SEMANTIC_AUTHORITY_REVIEW_REQUIRED",),
                )
            return self._accept_publication_candidate(
                candidate,
                candidate_digest,
                receipt,
                attempt_id=attempt_id,
                acceptance_mode="deterministic",
            )

    # ---------- generalized world systems / sparse 3D spatial map (v3.9) ----------

    def world_systems_dispatch(self, operation: str, campaign_id: str = "default", payload: dict[str, Any] | None = None) -> Any:
        return WorldSystemsKernel(self).dispatch(operation, campaign_id, payload)

    # ---------- sparse environment / consequence runtime ----------

    def environment_dispatch(self, operation: str, campaign_id: str = "default", payload: dict[str, Any] | None = None) -> Any:
        return EnvironmentKernel(self).dispatch(operation, campaign_id, payload)

    # ---------- canonical mechanism/economy/population providers (v4.7) ----------

    def mechanism_dispatch(self, operation: str, campaign_id: str = "default", payload: dict[str, Any] | None = None) -> Any:
        """Trusted/internal MOP-1.0 dispatch; intentionally not a public GPT Action."""
        return MechanismKernel(self).dispatch(operation, campaign_id, payload)

    def _mechanism_apply_effect_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        effect: dict[str, Any],
        bindings: dict[str, Any],
        *,
        phase: str,
        overlay: dict[str, Any],
        revision: int | None,
        world_time: str,
        operator_id: str,
        execution_id: str | None,
    ) -> dict[str, Any]:
        """Preflight or apply one MOP effect through canonical domain tables.

        The mechanism kernel owns eligibility, idempotency, and receipts. This
        callback owns domain invariants. Preflight updates only the supplied
        overlay so cumulative costs are checked without writing; apply uses the
        revision allocated by the mechanism transaction and never allocates one.
        """
        if phase not in {"preflight", "apply"}:
            raise ValueError("invalid mechanism effect phase")
        applying = phase == "apply"
        if applying and (revision is None or execution_id is None):
            raise ValueError("mechanism apply requires revision and execution_id")

        def failed(code: str, message: str) -> dict[str, Any]:
            if applying:
                raise ValueError(message)
            return {"passed": False, "reason_code": code}

        def finite(value: Any, label: str) -> float:
            if isinstance(value, bool):
                raise ValueError(f"{label} must be finite")
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(f"{label} must be finite")
            return number

        def bound(role: Any) -> dict[str, Any] | None:
            value = bindings.get(str(role)) if role else None
            return value if isinstance(value, dict) else None

        def actor_ref(role_field: str) -> tuple[str, str] | None:
            entity = bound(effect.get(role_field))
            kind = str(effect.get("kind") or (entity or {}).get("kind") or "").lower()
            actor_id = str(effect.get("actor_id") or (entity or {}).get("id") or "")
            if kind not in {"character", "npc"} or not actor_id:
                return None
            return kind, self._clean_id(actor_id)

        def event(
            event_type: str,
            summary: str,
            *,
            region: str | None = None,
            actor_id: str | None = None,
            target_id: str | None = None,
            payload: dict[str, Any] | None = None,
        ) -> int:
            assert revision is not None
            return self._insert_event(
                db,
                campaign_id,
                revision,
                event_type,
                summary,
                region=region,
                actor_id=actor_id,
                target_id=target_id,
                payload={
                    "operator_id": operator_id,
                    "execution_id": execution_id,
                    **dict(payload or {}),
                },
                world_time_override=world_time,
            )

        op = str(effect.get("op") or "")
        reason = str(effect.get("reason") or f"mechanism operator {operator_id}")[:1000]
        try:
            if op == "need.adjust":
                entity = bound(effect.get("binding"))
                npc_id = self._clean_id(str(effect.get("npc_id") or (entity or {}).get("id") or ""))
                if entity and entity.get("kind") != "npc":
                    return failed("invalid_target", "need target must be an npc")
                need = self._clean_id(str(effect.get("need") or ""))
                delta = finite(effect.get("delta"), "need delta")
                key = f"need:{npc_id}:{need}"
                row = db.execute(
                    "SELECT value FROM npc_needs WHERE campaign_id=? AND npc_id=? AND need=?",
                    (campaign_id, npc_id, need),
                ).fetchone()
                if not row:
                    return failed("missing_target", f"unknown npc need: {npc_id}/{need}")
                before = finite(overlay.get(key, row["value"]), "need value")
                after = max(0.0, min(100.0, before + delta))
                overlay[key] = after
                if not applying:
                    return {"passed": True, "reason_code": "ok"}
                db.execute(
                    "UPDATE npc_needs SET value=?,updated_at=? WHERE campaign_id=? AND npc_id=? AND need=?",
                    (after, self._now(), campaign_id, npc_id, need),
                )
                self._canonize_materialized_npc_db(db, campaign_id, npc_id, reason)
                event("mechanism_need_adjusted", reason, actor_id=npc_id, payload={"npc_id": npc_id, "need": need, "before": before, "after": after})
                return {"applied": True, "result": {"npc_id": npc_id, "need": need, "before": before, "after": after}}

            if op == "inventory.adjust":
                entity = bound(effect.get("binding"))
                owner_kind = str(effect.get("owner_kind") or (entity or {}).get("kind") or "").lower()
                owner_id = self._clean_id(str(effect.get("owner_id") or (entity or {}).get("id") or ""))
                if owner_kind not in {"character", "npc", "faction", "location"}:
                    return failed("invalid_target", "invalid inventory owner kind")
                owner_table = {"character": "characters", "npc": "npcs", "faction": "factions", "location": "locations"}[owner_kind]
                if not db.execute(f"SELECT 1 FROM {owner_table} WHERE campaign_id=? AND id=?", (campaign_id, owner_id)).fetchone():
                    return failed("missing_target", f"unknown inventory owner: {owner_kind}:{owner_id}")
                item_id = self._clean_id(str(effect.get("item_id") or ""))
                if not db.execute("SELECT 1 FROM item_defs WHERE campaign_id=? AND id=?", (campaign_id, item_id)).fetchone():
                    return failed("missing_item", f"unknown item: {item_id}")
                delta = finite(effect.get("delta"), "inventory delta")
                key = f"inventory:{owner_kind}:{owner_id}:{item_id}"
                row = db.execute(
                    "SELECT qty FROM inventories WHERE campaign_id=? AND owner_kind=? AND owner_id=? AND item_id=?",
                    (campaign_id, owner_kind, owner_id, item_id),
                ).fetchone()
                before = finite(overlay.get(key, row["qty"] if row else 0.0), "inventory quantity")
                after = before + delta
                if after < -1e-9:
                    return failed("insufficient_inventory", "inventory quantity cannot become negative")
                after = max(0.0, after)
                overlay[key] = after
                if not applying:
                    return {"passed": True, "reason_code": "ok"}
                db.execute(
                    """INSERT INTO inventories(campaign_id,owner_kind,owner_id,item_id,qty,metadata_json,updated_at)
                       VALUES(?,?,?,?,?,'{}',?)
                       ON CONFLICT(campaign_id,owner_kind,owner_id,item_id)
                       DO UPDATE SET qty=excluded.qty,updated_at=excluded.updated_at""",
                    (campaign_id, owner_kind, owner_id, item_id, after, self._now()),
                )
                if owner_kind == "npc":
                    self._canonize_materialized_npc_db(db, campaign_id, owner_id, reason)
                event("mechanism_inventory_adjusted", reason, actor_id=owner_id, payload={"owner_kind": owner_kind, "owner_id": owner_id, "item_id": item_id, "before": before, "after": after})
                return {"applied": True, "result": {"owner_kind": owner_kind, "owner_id": owner_id, "item_id": item_id, "before": before, "after": after}}

            if op == "resource.adjust":
                node_id = self._clean_id(str(effect.get("node_id") or ""))
                row = db.execute(
                    "SELECT location_id,item_id,qty,qty_max FROM resource_nodes WHERE campaign_id=? AND id=?",
                    (campaign_id, node_id),
                ).fetchone()
                if not row:
                    return failed("missing_target", f"unknown resource node: {node_id}")
                delta = finite(effect.get("delta"), "resource delta")
                key = f"resource:{node_id}"
                before = finite(overlay.get(key, row["qty"]), "resource quantity")
                after = before + delta
                if after < -1e-9:
                    return failed("insufficient_resource", "resource quantity cannot become negative")
                after = max(0.0, after)
                if not bool(effect.get("allow_overflow", False)):
                    after = min(after, finite(row["qty_max"], "resource capacity"))
                overlay[key] = after
                if not applying:
                    return {"passed": True, "reason_code": "ok"}
                db.execute(
                    "UPDATE resource_nodes SET qty=?,updated_at=? WHERE campaign_id=? AND id=?",
                    (after, self._now(), campaign_id, node_id),
                )
                event("mechanism_resource_adjusted", reason, region=row["location_id"], payload={"node_id": node_id, "item_id": row["item_id"], "before": before, "after": after})
                return {"applied": True, "result": {"node_id": node_id, "item_id": row["item_id"], "before": before, "after": after}}

            if op == "world_state.set":
                scope = bound(effect.get("scope_binding"))
                scope_type = str(effect.get("scope_type") or (scope or {}).get("kind") or "world").lower()
                scope_id = self._clean_id(str(effect.get("scope_id") or (scope or {}).get("id") or "global"))
                state_key = self._clean_id(str(effect.get("key") or ""))
                overlay[f"world_state:{scope_type}:{scope_id}:{state_key}"] = effect.get("value")
                if not applying:
                    return {"passed": True, "reason_code": "ok"}
                db.execute(
                    """INSERT INTO world_state(campaign_id,scope_type,scope_id,state_key,value_json,updated_at)
                       VALUES(?,?,?,?,?,?)
                       ON CONFLICT(campaign_id,scope_type,scope_id,state_key)
                       DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
                    (campaign_id, scope_type, scope_id, state_key, self._dumps(effect.get("value")), self._now()),
                )
                event("world_state_change", reason, actor_id=(scope or {}).get("id"), payload={"scope_type": scope_type, "scope_id": scope_id, "state_key": state_key})
                return {"applied": True, "result": {"scope_type": scope_type, "scope_id": scope_id, "key": state_key}}

            if op == "relationship.adjust":
                source = bound(effect.get("source_binding"))
                target = bound(effect.get("target_binding"))
                source_id = self._clean_id(str(effect.get("source_id") or (source or {}).get("id") or ""))
                target_id = self._clean_id(str(effect.get("target_id") or (target or {}).get("id") or ""))
                if source_id == target_id:
                    return failed("invalid_target", "relationship endpoints must differ")
                deltas = {name: int(finite(effect.get(f"{name}_delta", 0), f"{name} delta")) for name in ("trust", "fear", "respect", "affection")}
                key = f"relationship:{source_id}:{target_id}"
                row = db.execute(
                    "SELECT trust,fear,respect,affection FROM relationships WHERE campaign_id=? AND source_id=? AND target_id=?",
                    (campaign_id, source_id, target_id),
                ).fetchone()
                base = dict(overlay.get(key) or (dict(row) if row else {name: 0 for name in deltas}))
                after = {name: max(-100, min(100, int(base[name]) + deltas[name])) for name in deltas}
                overlay[key] = after
                if not applying:
                    return {"passed": True, "reason_code": "ok"}
                db.execute(
                    """INSERT INTO relationships(campaign_id,source_id,target_id,trust,fear,respect,affection,notes_json,updated_at)
                       VALUES(?,?,?,?,?,?,?,'{}',?)
                       ON CONFLICT(campaign_id,source_id,target_id) DO UPDATE SET
                       trust=excluded.trust,fear=excluded.fear,respect=excluded.respect,
                       affection=excluded.affection,updated_at=excluded.updated_at""",
                    (campaign_id, source_id, target_id, after["trust"], after["fear"], after["respect"], after["affection"], self._now()),
                )
                for npc_id in (source_id, target_id):
                    self._canonize_materialized_npc_db(db, campaign_id, npc_id, reason)
                assert revision is not None
                record_relationship_event(self, db, campaign_id, source_id, target_id, deltas, reason, revision, event_type="mechanism", world_time=world_time)
                event("relationship_change", reason, actor_id=source_id, target_id=target_id, payload={"source_id": source_id, "target_id": target_id, "deltas": deltas, "after": after})
                return {"applied": True, "result": {"source_id": source_id, "target_id": target_id, "after": after}}

            if op == "actor.move":
                ref = actor_ref("binding")
                location_entity = bound(effect.get("location_binding"))
                location_id = self._clean_id(str(effect.get("location_id") or (location_entity or {}).get("id") or ""))
                if not ref:
                    return failed("invalid_target", "actor move requires a character or npc")
                kind, actor_id = ref
                table = "characters" if kind == "character" else "npcs"
                row = db.execute(f"SELECT location FROM {table} WHERE campaign_id=? AND id=?", (campaign_id, actor_id)).fetchone()
                if not row:
                    return failed("missing_target", f"unknown actor: {kind}:{actor_id}")
                if not db.execute("SELECT 1 FROM locations WHERE campaign_id=? AND id=?", (campaign_id, location_id)).fetchone():
                    return failed("missing_location", f"unknown location: {location_id}")
                before = overlay.get(f"actor_location:{kind}:{actor_id}", row["location"])
                overlay[f"actor_location:{kind}:{actor_id}"] = location_id
                if not applying:
                    return {"passed": True, "reason_code": "ok"}
                db.execute(f"UPDATE {table} SET location=?,updated_at=? WHERE campaign_id=? AND id=?", (location_id, self._now(), campaign_id, actor_id))
                if kind == "npc":
                    self._canonize_materialized_npc_db(db, campaign_id, actor_id, reason)
                event("movement", reason, region=location_id, actor_id=actor_id, payload={"actor_kind": kind, "from": before, "to": location_id})
                return {"applied": True, "result": {"actor_kind": kind, "actor_id": actor_id, "from": before, "to": location_id}}

            if op == "environment.apply":
                target_entity = bound(effect.get("target_binding"))
                target_key = str(effect.get("target_key") or "")
                target_row = db.execute(
                    "SELECT * FROM environment_targets WHERE campaign_id=? AND target_key=?",
                    (campaign_id, target_key),
                ).fetchone() if target_key else None
                target_type = str(effect.get("target_type") or (target_entity or {}).get("kind") or "").lower()
                target_id = str(effect.get("target_id") or (target_entity or {}).get("id") or "")
                if not target_row:
                    if target_type == "location":
                        if not target_id or not db.execute("SELECT 1 FROM locations WHERE campaign_id=? AND id=?", (campaign_id, target_id)).fetchone():
                            return failed("missing_target", "unknown environment location target")
                    elif target_type in {"character", "npc", "actor"}:
                        actor_kind = str((target_entity or {}).get("kind") or ("character" if target_type == "actor" else target_type))
                        actor_id = str((target_entity or {}).get("id") or target_id)
                        table = "characters" if actor_kind == "character" else "npcs" if actor_kind == "npc" else ""
                        if not table or not db.execute(f"SELECT 1 FROM {table} WHERE campaign_id=? AND id=?", (campaign_id, actor_id)).fetchone():
                            return failed("missing_target", "unknown environment actor target")
                        target_type, target_id = "actor", actor_id
                    else:
                        return failed("missing_target", "environment effect requires an existing target_key, location, or actor")
                intensity = finite(effect.get("intensity", 0.5), "environment intensity")
                amount = finite(effect.get("amount", 0.0), "environment amount")
                if not 0.0 <= intensity <= 1.0 or amount < 0.0:
                    return failed("invalid_amount", "environment intensity/amount is out of range")
                if not applying:
                    return {"passed": True, "reason_code": "ok"}
                environment = EnvironmentKernel(self)
                if not target_row:
                    spec = {"type": target_type, "id": target_id}
                    if target_type == "actor":
                        spec.update({"actor_kind": str((target_entity or {}).get("kind") or "character"), "actor_id": target_id})
                    target_row = environment._bind_target_db(db, campaign_id, spec)
                applied_row = environment._apply_effect_db(
                    db,
                    campaign_id,
                    str(effect.get("effect_type")),
                    target_row,
                    intensity=intensity,
                    amount=amount,
                    source_key=str(effect.get("source_key") or f"mechanism:{operator_id}"),
                    state=dict(effect.get("state") or {}),
                    world_time=world_time,
                )
                event("environment_effect_applied", reason, region=target_row["location_id"], payload={"effect_type": effect.get("effect_type"), "target_key": target_row["target_key"], "intensity": float(applied_row["intensity"])})
                return {"applied": True, "result": {"effect_type": str(effect.get("effect_type")), "target_key": target_row["target_key"], "intensity": float(applied_row["intensity"])}}

            if op == "fact.assert":
                router = TurnRouter(self)
                subject_entity = bound(effect.get("subject_binding"))
                subject_ref = effect.get("subject_key") or (subject_entity or {}).get("key")
                if not subject_ref:
                    return failed("invalid_target", "fact requires a subject")
                try:
                    subject_key = router._ensure_entity_key_db(db, campaign_id, str(subject_ref))
                except KeyError:
                    if not subject_entity:
                        raise
                    subject_key = f"{subject_entity['kind']}:{subject_entity['id']}"
                    if applying:
                        router._upsert_entity_db(
                            db, campaign_id, str(subject_entity["kind"]), str(subject_entity["id"]),
                            str(subject_entity.get("name") or subject_entity["id"]),
                            status=str(subject_entity.get("status") or "active"),
                            source_table={"character": "characters", "npc": "npcs", "location": "locations", "faction": "factions"}.get(str(subject_entity["kind"])),
                        )
                fact_id = self._clean_id(str(effect.get("fact_id") or ""))
                predicate = str(effect.get("predicate") or "").lower()
                if not re.fullmatch(r"[a-z][a-z0-9_.:-]{0,79}", predicate):
                    return failed("invalid_predicate", "invalid fact predicate")
                object_type = str(effect.get("object_type", "literal"))
                value = effect.get("value")
                if object_type == "entity":
                    value = {"entity_key": router._ensure_entity_key_db(db, campaign_id, value)}
                confidence = finite(effect.get("confidence", 1.0), "fact confidence")
                if not 0.0 <= confidence <= 1.0:
                    return failed("invalid_confidence", "fact confidence must be 0..1")
                if not applying:
                    overlay[f"fact:{fact_id}"] = True
                    return {"passed": True, "reason_code": "ok"}
                now = self._now()
                source_event_id = effect.get("source_event_id")
                if source_event_id is not None and not db.execute("SELECT 1 FROM events WHERE campaign_id=? AND id=?", (campaign_id, int(source_event_id))).fetchone():
                    raise KeyError(f"unknown source event: {source_event_id}")
                db.execute(
                    """INSERT INTO we4_facts(campaign_id,fact_id,subject_key,predicate,object_type,object_value_json,
                       confidence,status,source_event_id,valid_from,valid_to,provenance_json,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(campaign_id,fact_id) DO UPDATE SET subject_key=excluded.subject_key,
                       predicate=excluded.predicate,object_type=excluded.object_type,object_value_json=excluded.object_value_json,
                       confidence=excluded.confidence,status=excluded.status,source_event_id=excluded.source_event_id,
                       valid_from=excluded.valid_from,valid_to=excluded.valid_to,provenance_json=excluded.provenance_json,
                       updated_at=excluded.updated_at""",
                    (campaign_id, fact_id, subject_key, predicate, object_type, self._dumps(value), confidence,
                     str(effect.get("status", "active")), source_event_id, effect.get("valid_from"), effect.get("valid_to"),
                     self._dumps(dict(effect.get("provenance") or {})), now, now),
                )
                fact_row = db.execute("SELECT * FROM we4_facts WHERE campaign_id=? AND fact_id=?", (campaign_id, fact_id)).fetchone()
                fact = router._decode_fact_row(fact_row)
                assert revision is not None
                router._upsert_world_claim_db(db, campaign_id, fact, revision)
                event("canonical_fact", f"Fact asserted: {subject_key} {predicate}", actor_id=subject_key, payload={"fact_id": fact_id, "subject_key": subject_key, "predicate": predicate, "status": fact["status"]})
                return {"applied": True, "result": {"fact_id": fact_id, "subject_key": subject_key, "predicate": predicate, "status": fact["status"]}}

            if op == "belief.set":
                router = TurnRouter(self)
                believer = bound(effect.get("binding"))
                believer_ref = effect.get("believer_key") or (believer or {}).get("key")
                if not believer_ref:
                    return failed("invalid_target", "belief requires a believer")
                try:
                    believer_key = router._ensure_entity_key_db(db, campaign_id, str(believer_ref))
                except KeyError:
                    if not believer:
                        raise
                    believer_key = f"{believer['kind']}:{believer['id']}"
                    if applying:
                        router._upsert_entity_db(
                            db, campaign_id, str(believer["kind"]), str(believer["id"]),
                            str(believer.get("name") or believer["id"]),
                            status=str(believer.get("status") or "active"),
                            source_table={"character": "characters", "npc": "npcs", "location": "locations", "faction": "factions"}.get(str(believer["kind"])),
                        )
                fact_id = self._clean_id(str(effect.get("fact_id") or ""))
                fact_row = db.execute("SELECT * FROM we4_facts WHERE campaign_id=? AND fact_id=?", (campaign_id, fact_id)).fetchone()
                if not fact_row and not overlay.get(f"fact:{fact_id}"):
                    return failed("missing_fact", f"unknown fact: {fact_id}")
                source = bound(effect.get("source_binding"))
                source_ref = effect.get("source_key") or (source or {}).get("key")
                if source_ref:
                    try:
                        source_key = router._ensure_entity_key_db(db, campaign_id, str(source_ref))
                    except KeyError:
                        if not source:
                            raise
                        source_key = f"{source['kind']}:{source['id']}"
                        if applying:
                            router._upsert_entity_db(
                                db, campaign_id, str(source["kind"]), str(source["id"]),
                                str(source.get("name") or source["id"]),
                                status=str(source.get("status") or "active"),
                                source_table={"character": "characters", "npc": "npcs", "location": "locations", "faction": "factions"}.get(str(source["kind"])),
                            )
                else:
                    source_key = None
                confidence = finite(effect.get("confidence", 0.5), "belief confidence")
                if not 0.0 <= confidence <= 1.0:
                    return failed("invalid_confidence", "belief confidence must be 0..1")
                if not applying:
                    overlay[f"belief:{believer_key}:{fact_id}"] = True
                    return {"passed": True, "reason_code": "ok"}
                if not fact_row:
                    fact_row = db.execute("SELECT * FROM we4_facts WHERE campaign_id=? AND fact_id=?", (campaign_id, fact_id)).fetchone()
                now = self._now()
                db.execute(
                    """INSERT INTO we4_beliefs(campaign_id,believer_key,fact_id,belief_value_json,confidence,source_key,
                       acquired_world_time,last_confirmed_world_time,status,provenance_json,updated_at)
                       VALUES(?,?,?,?,?,?,?,NULL,?,?,?)
                       ON CONFLICT(campaign_id,believer_key,fact_id) DO UPDATE SET
                       belief_value_json=excluded.belief_value_json,confidence=excluded.confidence,
                       source_key=excluded.source_key,acquired_world_time=excluded.acquired_world_time,
                       status=excluded.status,provenance_json=excluded.provenance_json,updated_at=excluded.updated_at""",
                    (campaign_id, believer_key, fact_id, self._dumps(effect.get("value")), confidence, source_key,
                     world_time, str(effect.get("status", "believes")), self._dumps(dict(effect.get("provenance") or {})), now),
                )
                belief_row = db.execute(
                    "SELECT * FROM we4_beliefs WHERE campaign_id=? AND believer_key=? AND fact_id=?",
                    (campaign_id, believer_key, fact_id),
                ).fetchone()
                belief = router._decode_belief_row(belief_row)
                assert revision is not None and fact_row is not None
                router._upsert_belief_claim_db(db, campaign_id, belief, fact_row, revision)
                event("belief_updated", f"Belief updated: {believer_key} / {fact_id}", actor_id=believer_key, payload={"believer_key": believer_key, "fact_id": fact_id, "status": belief["status"], "confidence": confidence})
                return {"applied": True, "result": {"believer_key": believer_key, "fact_id": fact_id, "status": belief["status"], "confidence": confidence}}

            return failed("unsupported_effect", f"unsupported mechanism effect: {op}")
        except (KeyError, TypeError, ValueError, sqlite3.Error):
            if applying:
                raise
            return {"passed": False, "reason_code": "invalid_effect"}

    def economy_dispatch(self, operation: str, campaign_id: str = "default", payload: dict[str, Any] | None = None) -> Any:
        return EconomyKernel(self).dispatch(operation, campaign_id, payload)

    def population_dispatch(self, operation: str, campaign_id: str = "default", payload: dict[str, Any] | None = None) -> Any:
        return PopulationKernel(self).dispatch(operation, campaign_id, payload)

    def incident_dispatch(self, operation: str, campaign_id: str = "default", payload: dict[str, Any] | None = None) -> Any:
        # This Python-only seam is the trusted GM/authoring boundary. Public GPT
        # Actions do not expose it; IncidentKernel.dispatch remains projection-only.
        return IncidentKernel(self).trusted_dispatch(operation, campaign_id, payload)

    def politics_dispatch(self, operation: str, campaign_id: str = "default", payload: dict[str, Any] | None = None) -> Any:
        """Trusted internal politics seam; it is not a public GPT Action."""
        return PoliticsKernel(self).dispatch(operation, campaign_id, payload)

    def agency_dispatch(self, operation: str, campaign_id: str = "default", payload: dict[str, Any] | None = None) -> Any:
        return AgencyKernel(self).dispatch(operation, campaign_id, payload)

    def quest_runtime_dispatch(self, operation: str, campaign_id: str = "default", payload: dict[str, Any] | None = None) -> Any:
        return QuestRuntimeKernel(self).dispatch(operation, campaign_id, payload)

    # ---------- deterministic tabletop-RPG rules kernel ----------

    def rules_dispatch(self, operation: str, campaign_id: str = "default", payload: dict[str, Any] | None = None) -> Any:
        return RulesKernel(self).dispatch(operation, campaign_id, payload)

    def get_actor_rules(self, campaign_id: str, actor_kind: str, actor_id: str) -> dict[str, Any]:
        return RulesKernel(self).get_actor_rules(campaign_id, actor_kind, actor_id)

    # ---------- authoring-time content generation (model proposes, DB validates) ----------

    def generate_world(
        self,
        seed: str | int,
        config: dict[str, Any] | None = None,
        *,
        namespace: str = "bootstrap",
        mode: str = "bootstrap",
        anchor_location_id: str | None = None,
    ) -> dict[str, Any]:
        """Generate a deterministic authoring payload without touching the database."""
        return ProceduralWorldGenerator().generate(
            seed,
            config,
            namespace=namespace,
            mode=mode,
            anchor_location_id=anchor_location_id,
        )

    def stage_generated_world(
        self,
        campaign_id: str,
        batch_id: str,
        seed: str | int,
        config: dict[str, Any] | None = None,
        *,
        namespace: str = "bootstrap",
        mode: str = "bootstrap",
        anchor_location_id: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Generate and stage only; validation, dry-run, and promotion stay explicit."""
        if mode == "expansion" and anchor_location_id is None:
            with self._db() as db:
                row = db.execute(
                    "SELECT id FROM locations WHERE campaign_id=? ORDER BY id LIMIT 1",
                    (campaign_id,),
                ).fetchone()
            if row is None:
                raise ValueError("expansion generation requires an existing location anchor")
            anchor_location_id = str(row["id"])
        generation = self.generate_world(
            seed,
            config,
            namespace=namespace,
            mode=mode,
            anchor_location_id=anchor_location_id,
        )
        batch = AuthoringKernel(self).stage_generated(
            campaign_id,
            batch_id,
            generation,
            expected_revision=expected_revision,
        )
        return {"generation": generation, "batch": batch}

    def author_stage(self, campaign_id: str, batch_id: str, payload: dict[str, Any], *, mode: str = "bootstrap") -> dict[str, Any]:
        return AuthoringKernel(self).stage(campaign_id, batch_id, payload, mode=mode)

    def author_validate(self, campaign_id: str, batch_id: str) -> dict[str, Any]:
        return AuthoringKernel(self).validate(campaign_id, batch_id)

    def author_dry_run(self, campaign_id: str, batch_id: str, *, days: int = 365) -> dict[str, Any]:
        return AuthoringKernel(self).dry_run(campaign_id, batch_id, days=days)

    def author_promote(self, campaign_id: str, batch_id: str) -> dict[str, Any]:
        return AuthoringKernel(self).promote(campaign_id, batch_id)

    def author_materialization_brief(self, campaign_id: str, location_id: str) -> dict[str, Any]:
        return AuthoringKernel(self).materialization_brief(campaign_id, location_id)

    def author_world_digest(self, campaign_id: str) -> dict[str, Any]:
        return AuthoringKernel(self).world_digest(campaign_id)

    def author_lock(self, campaign_id: str, object_kind: str, object_id: str, *, reason: str = "player touched") -> dict[str, Any]:
        return AuthoringKernel(self).lock(campaign_id, object_kind, object_id, reason)

    def author_log_gap(self, campaign_id: str, gap_key: str, kind: str, summary: str, *, scope_id: str | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
        return AuthoringKernel(self).log_gap(campaign_id, gap_key, kind, summary, scope_id=scope_id, context=context)

    def author_list_gaps(self, campaign_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return AuthoringKernel(self).list_gaps(campaign_id, limit)

    def author_resolve_gap(self, campaign_id: str, gap_key: str, *, status: str = "resolved") -> dict[str, Any]:
        return AuthoringKernel(self).resolve_gap(campaign_id, gap_key, status)

    def get_world_bible(self, campaign_id: str) -> dict[str, Any]:
        return AuthoringKernel(self).get_world_bible(campaign_id)

    # ---------- WORLD / SCENE / authority layers ----------

    def start_scene(self, campaign_id: str, scene_id: str, location_id: str, **kwargs: Any) -> dict[str, Any]:
        return WorldLayerKernel(self).start_scene(campaign_id, scene_id, location_id, **kwargs)

    def get_scene(self, campaign_id: str, scene_id: str | None = None) -> dict[str, Any] | None:
        return WorldLayerKernel(self).get_scene(campaign_id, scene_id)

    def set_scene_entity(self, campaign_id: str, scene_id: str, actor_kind: str, actor_id: str, **kwargs: Any) -> dict[str, Any]:
        return WorldLayerKernel(self).set_scene_entity(campaign_id, scene_id, actor_kind, actor_id, **kwargs)

    def set_scene_feature(self, campaign_id: str, scene_id: str, feature_id: str, **kwargs: Any) -> dict[str, Any]:
        return WorldLayerKernel(self).set_scene_feature(campaign_id, scene_id, feature_id, **kwargs)

    def end_scene(self, campaign_id: str, scene_id: str, **kwargs: Any) -> dict[str, Any]:
        return WorldLayerKernel(self).end_scene(campaign_id, scene_id, **kwargs)

    def save_director(self, campaign_id: str, director_id: str, name: str, **kwargs: Any) -> dict[str, Any]:
        return WorldLayerKernel(self).save_director(campaign_id, director_id, name, **kwargs)

    def get_director(self, campaign_id: str, director_id: str) -> dict[str, Any]:
        return WorldLayerKernel(self).get_director(campaign_id, director_id)

    def get_active_directors(self, campaign_id: str, location_id: str | None, scene_id: str | None = None) -> dict[str, Any]:
        return WorldLayerKernel(self).active_directors(campaign_id, location_id, scene_id)

    def save_ownership(self, campaign_id: str, asset_kind: str, asset_id: str, owner_kind: str, owner_id: str, **kwargs: Any) -> dict[str, Any]:
        return WorldLayerKernel(self).save_ownership(campaign_id, asset_kind, asset_id, owner_kind, owner_id, **kwargs)

    def get_ownership(self, campaign_id: str, asset_kind: str, asset_id: str) -> dict[str, Any]:
        return WorldLayerKernel(self).get_ownership(campaign_id, asset_kind, asset_id)

    def set_actor_status(self, campaign_id: str, kind: str, actor_id: str, status: str, *, reason: str = "status changed") -> dict[str, Any]:
        if status not in {"alive", "dead", "missing"}:
            raise ValueError("status must be alive, dead, or missing")
        table = self._actor_table(kind)
        campaign_id, actor_id = self._clean_id(campaign_id), self._clean_id(actor_id)
        with self._write_db() as db:
            actor = self._get_actor_db(db, campaign_id, kind, actor_id)
            world_time = db.execute("SELECT world_time FROM campaigns WHERE id=?", (campaign_id,)).fetchone()["world_time"]
            rev = self._next_revision(db, campaign_id)
            if kind == "npc" and status == "dead":
                self._mark_npc_dead_db(db, campaign_id, actor_id, revision=rev, world_time=world_time, cause=reason)
            else:
                died_on = world_time if status == "dead" else None
                db.execute(f"UPDATE {table} SET status=?,died_on=?,updated_at=? WHERE campaign_id=? AND id=?", (status, died_on, self._now(), campaign_id, actor_id))
                self._insert_event(db, campaign_id, rev, "status_change", f"{actor['name']} status changed to {status}: {reason}", region=actor.get("location"), actor_id=actor_id, payload={"kind": kind, "old_status": actor.get("status", "alive"), "new_status": status, "reason": reason})
        return self.get_actor(campaign_id, kind, actor_id)

    def commit_event(
        self,
        campaign_id: str,
        event_type: str,
        summary: str,
        *,
        region: str | None = None,
        actor_id: str | None = None,
        target_id: str | None = None,
        payload: dict[str, Any] | None = None,
        sensitivity: str = "PUBLIC",
        scope_type: str = "WORLD",
        principal_kind: str | None = None,
        principal_id: str | None = None,
        causal_parent_event_id: int | None = None,
    ) -> dict[str, Any]:
        campaign_id = self._clean_id(campaign_id)
        self._ensure_campaign_exists(campaign_id)
        with self._write_db() as db:
            rev = self._next_revision(db, campaign_id)
            event_id = self._insert_event(
                db,
                campaign_id,
                rev,
                event_type,
                summary,
                region=region,
                actor_id=actor_id,
                target_id=target_id,
                payload=payload,
                sensitivity=sensitivity,
                scope_type=scope_type,
                principal_kind=principal_kind,
                principal_id=principal_id,
                causal_parent_event_id=causal_parent_event_id,
            )
        return self.get_event(campaign_id, event_id)

    def get_event(self, campaign_id: str, event_id: int) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM events WHERE campaign_id=? AND id=?", (campaign_id, int(event_id))).fetchone()
        if not row:
            raise KeyError(event_id)
        data = dict(row)
        data["payload"] = self._loads(data.pop("payload_json"))
        return data

    def recent_events(self, campaign_id: str, limit: int = 20, region: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        with self._db() as db:
            if region:
                rows = db.execute("SELECT * FROM events WHERE campaign_id=? AND region=? ORDER BY revision DESC,id DESC LIMIT ?", (campaign_id, region, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM events WHERE campaign_id=? ORDER BY revision DESC,id DESC LIMIT ?", (campaign_id, limit)).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["payload"] = self._loads(data.pop("payload_json"))
            out.append(data)
        return out

    def get_world_context(self, campaign_id: str = "default", location: str | None = None, event_limit: int = 12, destination: str | None = None, entity_limit: int = 40) -> dict[str, Any]:
        """Return bounded authoritative context in a single SQLite connection.

        WORLD remains broad/abstract; an active SCENE carries at most 12 concrete
        entities.  Dead/missing actors are excluded from ordinary playable context.
        Full records outside the cap remain available through getEntity.
        """
        campaign_id = self._clean_id(campaign_id)
        entity_limit=max(1,min(int(entity_limit),40))
        event_limit=max(1,min(int(event_limit),50))
        with self._db() as db:
            crow=db.execute("SELECT * FROM campaigns WHERE id=?",(campaign_id,)).fetchone()
            if not crow:
                raise KeyError(f"unknown campaign: {campaign_id}")
            campaign=dict(crow); campaign["settings"]=self._loads(campaign.pop("settings_json"))
            bible_row=db.execute("SELECT bible_json,canon_version FROM world_bible WHERE campaign_id=?",(campaign_id,)).fetchone()
            world_bible={"canon_version":int(bible_row["canon_version"]),"bible":self._loads(bible_row["bible_json"])} if bible_row else {"canon_version":0,"bible":{}}
            gap_rows=db.execute("SELECT gap_key,kind,scope_id,summary,context_json FROM content_gaps WHERE campaign_id=? AND status='open' ORDER BY id LIMIT 10",(campaign_id,)).fetchall()
            content_gaps=[]
            for gr in gap_rows:
                gd=dict(gr); gd["context"]=self._loads(gd.pop("context_json")); content_gaps.append(gd)
            location_row=None
            location_record=None
            if location:
                location_row=db.execute("SELECT * FROM locations WHERE campaign_id=? AND id=?",(campaign_id,location)).fetchone()
                if location_row:
                    location_record=dict(location_row); location_record["tags"]=self._loads(location_record.pop("tags_json")); location_record["state"]=self._loads(location_record.pop("state_json"))
            region_filter=location_row["region"] if location_row else location

            if location:
                char_total=int(db.execute("SELECT COUNT(*) n FROM characters WHERE campaign_id=? AND location=? AND status='alive'",(campaign_id,location)).fetchone()["n"])
                npc_total=int(db.execute("SELECT COUNT(*) n FROM npcs WHERE campaign_id=? AND location=? AND status='alive' AND hp>0",(campaign_id,location)).fetchone()["n"])
                char_rows=db.execute("SELECT * FROM characters WHERE campaign_id=? AND location=? AND status='alive' ORDER BY name,id LIMIT ?",(campaign_id,location,entity_limit)).fetchall()
                remaining=max(0,entity_limit-len(char_rows))
                npc_rows=db.execute("SELECT * FROM npcs WHERE campaign_id=? AND location=? AND status='alive' AND hp>0 ORDER BY name,id LIMIT ?",(campaign_id,location,remaining)).fetchall()
                faction_rows=db.execute("SELECT * FROM factions WHERE campaign_id=? AND region=? ORDER BY name,id LIMIT 50",(campaign_id,region_filter)).fetchall()
                quest_rows=db.execute("SELECT * FROM quests WHERE campaign_id=? AND (region=? OR region IS NULL) AND status='active' ORDER BY updated_at DESC LIMIT 50",(campaign_id,region_filter)).fetchall()
            else:
                char_total=int(db.execute("SELECT COUNT(*) n FROM characters WHERE campaign_id=? AND status='alive'",(campaign_id,)).fetchone()["n"])
                npc_total=int(db.execute("SELECT COUNT(*) n FROM npcs WHERE campaign_id=? AND status='alive' AND hp>0",(campaign_id,)).fetchone()["n"])
                char_rows=db.execute("SELECT * FROM characters WHERE campaign_id=? AND status='alive' ORDER BY name,id LIMIT ?",(campaign_id,entity_limit)).fetchall()
                remaining=max(0,entity_limit-len(char_rows))
                npc_rows=db.execute("SELECT * FROM npcs WHERE campaign_id=? AND status='alive' AND hp>0 ORDER BY name,id LIMIT ?",(campaign_id,remaining)).fetchall()
                faction_rows=db.execute("SELECT * FROM factions WHERE campaign_id=? ORDER BY name,id LIMIT 50",(campaign_id,)).fetchall()
                quest_rows=db.execute("SELECT * FROM quests WHERE campaign_id=? AND status='active' ORDER BY updated_at DESC LIMIT 50",(campaign_id,)).fetchall()

            characters=[self._decode_character_row(r) for r in char_rows]
            npcs=[self._decode_npc_row(r) for r in npc_rows]
            progression_kernel=WorldSystemsKernel(self)
            for char in characters:
                prow=db.execute("SELECT * FROM character_progression WHERE campaign_id=? AND character_id=?",(campaign_id,char["id"])).fetchone()
                if prow:
                    char["progression"]=progression_kernel._progression_report(char,prow)
                else:
                    floor=progression_kernel.xp_threshold_for_level(int(char["level"]))
                    char["progression"]={"mode":"xp","xp":floor,"current_level":int(char["level"]),"eligible_level":int(char["level"]),"pending_level":None,"level_up_available":False,"xp_to_next_level":max(0,progression_kernel.XP_THRESHOLDS.get(int(char["level"])+1,floor)-floor),"class_id":None,"milestone_count":0}
                char.update(self._actor_ledger_db(db,campaign_id,"character",char["id"]))
            factions=[self._decode_faction_row(r) for r in faction_rows]
            quests=[]
            for r in quest_rows:
                q=dict(r); q["objectives"]=self._loads(q.pop("objectives_json")); q["state"]=self._loads(q.pop("state_json")); quests.append(q)
            quest_runtime=QuestRuntimeKernel(self)
            quests=[quest_runtime.public_projection_db(db,campaign_id,q["id"]) for q in quests]

            combat_rows=db.execute("SELECT * FROM combats WHERE campaign_id=? AND status='active' ORDER BY updated_at DESC",(campaign_id,)).fetchall()
            combats=[self._decode_combat_row_db(db,r) for r in combat_rows]

            # Local lifecycle only for entities actually returned to the model.
            local_lifecycle=[]
            for npc in npcs:
                lr=db.execute("SELECT * FROM npc_lifecycle WHERE campaign_id=? AND npc_id=?",(campaign_id,npc["id"])).fetchone()
                if lr:
                    d=dict(lr); d["parents"]=self._loads(d.pop("parents_json")); d["mortality"]=self._loads(d.pop("mortality_json")); d["fertility"]=self._loads(d.pop("fertility_json")); d["alive"]=bool(d["alive"]); d["age"]=max(0,datetime.fromisoformat(campaign["world_time"]).year-int(d["birth_year"])); local_lifecycle.append(d)

            social_rows=db.execute("SELECT * FROM relationship_events WHERE campaign_id=? ORDER BY id DESC LIMIT ?",(campaign_id,max(event_limit,20))).fetchall()
            local_ids={x["id"] for x in characters}|{x["id"] for x in npcs}
            social_history=[]
            for r in social_rows:
                d=dict(r)
                if not location or d["source_id"] in local_ids or d["target_id"] in local_ids:
                    social_history.append(d)
                if len(social_history)>=event_limit:
                    break

            world_state=[]
            if location:
                ws_rows=db.execute("SELECT * FROM world_state WHERE campaign_id=? AND scope_type='location' AND scope_id=? ORDER BY state_key",(campaign_id,location)).fetchall()
            else:
                ws_rows=[]
            for r in ws_rows:
                d=dict(r); d["value"]=self._loads(d.pop("value_json")); d["key"]=d["state_key"]; world_state.append(d)

            graph_neighbors=[]
            if location:
                for r in db.execute("SELECT * FROM location_links WHERE campaign_id=? AND from_id=? ORDER BY travel_hours,to_id",(campaign_id,location)).fetchall():
                    d=dict(r); d["metadata"]=self._loads(d.pop("metadata_json")); graph_neighbors.append(d)
            route=self._route_locations_db(db,campaign_id,location,destination) if location and destination else None
            lod=self._lod_tiers_db(db,campaign_id,location) if location and location_row else []

            market_prices=[]
            if location:
                price_rows=db.execute(
                    """SELECT d.id,d.name,d.base_price,COALESCE(SUM(r.qty),0) qty,COALESCE(SUM(r.qty_max),0) qty_max
                       FROM item_defs d LEFT JOIN resource_nodes r ON r.campaign_id=d.campaign_id AND r.item_id=d.id AND r.location_id=?
                       WHERE d.campaign_id=? GROUP BY d.id,d.name,d.base_price ORDER BY d.id""",
                    (location,campaign_id),
                ).fetchall()
                for r in price_rows:
                    cap=float(r["qty_max"] or 0); qty=float(r["qty"] or 0); scarcity=0.0 if cap<=0 else max(0.0,min(1.0,1.0-qty/cap)); base=float(r["base_price"] or 0)
                    market_prices.append({"item_id":r["id"],"name":r["name"],"base_price":base,"local_qty":qty,"local_capacity":cap,"scarcity":round(scarcity,6),"price":round(base*(1.0+scarcity),6)})

            active_scene=None
            scene_row=db.execute("SELECT * FROM scenes WHERE campaign_id=? AND status='active' ORDER BY updated_at DESC LIMIT 1",(campaign_id,)).fetchone()
            if scene_row and (not location or scene_row["location_id"]==location):
                active_scene=dict(scene_row); active_scene["state"]=self._loads(active_scene.pop("state_json"))
                sent=[]
                for r in db.execute("SELECT * FROM scene_entities WHERE campaign_id=? AND scene_id=? ORDER BY actor_kind,actor_id",(campaign_id,scene_row["id"])).fetchall():
                    d=dict(r); d["state"]=self._loads(d.pop("state_json")); sent.append(d)
                sfeat=[]
                for r in db.execute("SELECT * FROM scene_features WHERE campaign_id=? AND scene_id=? ORDER BY id",(campaign_id,scene_row["id"])).fetchall():
                    d=dict(r); d["blocks_los"]=bool(d["blocks_los"]); d["difficult"]=bool(d["difficult"]); d["persistent"]=bool(d["persistent"]); d["state"]=self._loads(d.pop("state_json")); sfeat.append(d)
                active_scene["entities"]=sent; active_scene["features"]=sfeat; active_scene["entity_limit"]=WorldLayerKernel.MAX_SCENE_ENTITIES

            directors=WorldLayerKernel(self).active_directors_db(db,campaign_id,location,active_scene["id"] if active_scene else None)

            # Bounded deterministic cognition projection for dialogue/action rendering.
            cognition_kernel=NpcLifeKernel(self)
            cognition_order=sorted(npcs,key=lambda n:(0 if n.get("importance")=="major" else 1,0 if n.get("importance")=="supporting" else 1,n.get("name",""),n["id"]))[:8]
            npc_cognition=[cognition_kernel._cognition_snapshot_db(db,campaign_id,n["id"]) for n in cognition_order]

            if location:
                event_rows=db.execute("SELECT * FROM events WHERE campaign_id=? AND sensitivity='PUBLIC' AND scope_type='WORLD' AND (region=? OR region IS NULL) ORDER BY revision DESC,id DESC LIMIT ?",(campaign_id,location,event_limit)).fetchall()
            else:
                event_rows=db.execute("SELECT * FROM events WHERE campaign_id=? AND sensitivity='PUBLIC' AND scope_type='WORLD' ORDER BY revision DESC,id DESC LIMIT ?",(campaign_id,event_limit)).fetchall()
            recent=[]
            for r in event_rows:
                d=dict(r); d["payload"]=self._loads(d.pop("payload_json")); recent.append(d)

            rules_cfg_row=db.execute("SELECT rules_version,grid_feet FROM rules_config WHERE campaign_id=?",(campaign_id,)).fetchone()
            rules_config=dict(rules_cfg_row) if rules_cfg_row else {"rules_version":"2024","grid_feet":5}
            local_pairs={("character",x["id"]) for x in characters}|{("npc",x["id"]) for x in npcs}
            rule_actors=[]
            for kind,actor_id in sorted(local_pairs):
                profile_row=db.execute("SELECT * FROM rule_actor_profiles WHERE campaign_id=? AND actor_kind=? AND actor_id=?",(campaign_id,kind,actor_id)).fetchone()
                if profile_row:
                    profile=dict(profile_row)
                    for key in ("save_proficiencies","skill_proficiencies","resistances","immunities","vulnerabilities"):
                        profile[key]=self._loads(profile.pop(key+"_json"))
                    profile["metadata"]=self._loads(profile.pop("metadata_json")); profile["stable"]=bool(profile["stable"])
                else:
                    profile={"rules_version":rules_config["rules_version"],"temp_hp":0,"death_successes":0,"death_failures":0,"stable":False,"resistances":[],"immunities":[],"vulnerabilities":[]}
                resources=[dict(r) for r in db.execute("SELECT resource_key,current_value,max_value,recovery,recovery_amount FROM rule_resources WHERE campaign_id=? AND actor_kind=? AND actor_id=? ORDER BY resource_key",(campaign_id,kind,actor_id)).fetchall()]
                effects=[]
                for er in db.execute("SELECT effect_id,name,condition_name,modifiers_json,concentration,expires_on,expires_world_time,expires_combat_id,expires_round FROM rule_effects WHERE campaign_id=? AND target_kind=? AND target_id=? AND active=1 ORDER BY created_at,effect_id",(campaign_id,kind,actor_id)).fetchall():
                    ed=dict(er); ed["modifiers"]=self._loads(ed.pop("modifiers_json")); ed["concentration"]=bool(ed["concentration"]); effects.append(ed)
                if profile_row or resources or effects:
                    rule_actors.append({"kind":kind,"id":actor_id,"profile":profile,"resources":resources,"effects":effects})
            turn_states=[dict(r) for r in db.execute("SELECT * FROM rule_turn_state WHERE campaign_id=? ORDER BY combat_id,round,actor_kind,actor_id",(campaign_id,)).fetchall()]
            environment_state=EnvironmentKernel(self).public_summary_db(db,campaign_id,location_id=location)
            economy_state=EconomyKernel(self).public_snapshot_db(db,campaign_id,location_id=location) if location else None
            population_state=PopulationKernel(self).public_snapshot_db(db,campaign_id,location_id=location) if location else None
            incident_state=IncidentKernel(self).public_snapshot_db(db,campaign_id,location_id=location)
            politics_state=PoliticsKernel(self).public_snapshot_db(
                db,campaign_id,location_id=location
            )
            agency_state=[
                AgencyKernel(self).public_snapshot_db(db,campaign_id,"character",character["id"])
                for character in characters
            ]

            tracking={
                "locations_total":int(db.execute("SELECT COUNT(*) n FROM locations WHERE campaign_id=?",(campaign_id,)).fetchone()["n"]),
                "living_npcs_total":int(db.execute("SELECT COUNT(*) n FROM npcs WHERE campaign_id=? AND status='alive' AND hp>0",(campaign_id,)).fetchone()["n"]),
                "dead_npcs_total":int(db.execute("SELECT COUNT(*) n FROM npcs WHERE campaign_id=? AND status='dead'",(campaign_id,)).fetchone()["n"]),
                "active_combats":len(combats),
                "active_scene_id":active_scene["id"] if active_scene else None,
                "location_character_total":char_total if location else None,
                "location_npc_total":npc_total if location else None,
            }

        total_entities=char_total+npc_total
        returned_entities=len(characters)+len(npcs)
        return {
            "campaign":campaign,
            "world_bible":world_bible,
            "open_content_gaps":content_gaps,
            "location":location,
            "location_record":location_record,
            "content_materialization": {
                "needs_materialization": bool(location_record and (location_record.get("state",{}).get("pop") is not None or location_record.get("state",{}).get("population") is not None) and npc_total == 0),
                "aggregate_population": (location_record.get("state",{}).get("population", location_record.get("state",{}).get("pop")) if location_record else None),
                "named_npc_count": npc_total if location else None,
            },
            "world_tracking":tracking,
            "entity_window":{"limit":entity_limit,"total_count":total_entities,"returned_count":returned_entities,"truncated":total_entities>returned_entities},
            "location_world_state":world_state,
            "environment":environment_state,
            "economy":economy_state,
            "population":population_state,
            "incidents":incident_state,
            "politics":politics_state,
            "agency":agency_state,
            "world_graph":{"neighbors":graph_neighbors,"route_to_destination":route,"lod_tiers":lod},
            "market_prices":market_prices,
            "characters":characters,
            "npcs":npcs,
            "npc_cognition":npc_cognition,
            "npc_lifecycle":local_lifecycle,
            "rules_state":{"config":rules_config,"actors":rule_actors,"turn_states":turn_states},
            "factions":factions,
            "directors":directors,
            "active_scene":active_scene,
            "active_quests":quests,
            "active_combats":combats,
            "recent_events":recent,
            "recent_social_history":social_history,
        }

    # ---------- hidden numerical simulation projection ----------

    def get_internal_state_block(self, campaign_id: str = "default", location: str | None = None, entity_limit: int = 40) -> dict[str, Any]:
        """Compact numerical projection for internal model/tool use only.

        Canonical state remains in the normal tables. Entity rows are capped and the
        cap is reported so large settlements cannot silently consume the context window.
        """
        campaign_id = self._clean_id(campaign_id)
        entity_limit = max(1, min(int(entity_limit), 40))
        weather_map = {"clear": 0, "rain": 1, "storm": 2, "snow": 3, "fog": 4, "wind": 5}
        with self._db() as db:
            campaign_row = db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
            if not campaign_row:
                raise KeyError(f"unknown campaign: {campaign_id}")
            campaign = dict(campaign_row)
            campaign["settings"] = self._loads(campaign.pop("settings_json"))
            world_dt = datetime.fromisoformat(campaign["world_time"])
            weather_code = weather_map.get(str(campaign.get("weather", "")).strip().lower(), 99)

            where = "campaign_id=? AND status='alive'"
            params: list[Any] = [campaign_id]
            if location:
                where += " AND location=?"
                params.append(location)
            char_total = int(db.execute(f"SELECT COUNT(*) FROM characters WHERE {where}", params).fetchone()[0])
            npc_total = int(db.execute(f"SELECT COUNT(*) FROM npcs WHERE {where}", params).fetchone()[0])
            char_rows = db.execute(f"SELECT * FROM characters WHERE {where} ORDER BY id LIMIT ?", [*params, entity_limit]).fetchall()
            remaining = max(0, entity_limit - len(char_rows))
            npc_rows = db.execute(f"SELECT * FROM npcs WHERE {where} ORDER BY id LIMIT ?", [*params, remaining]).fetchall() if remaining else []

            faction_rows = db.execute("SELECT * FROM factions WHERE campaign_id=? ORDER BY id LIMIT 50", (campaign_id,)).fetchall()
            relationships = [dict(r) for r in db.execute(
                "SELECT source_id,target_id,trust,fear,respect,affection FROM relationships WHERE campaign_id=? ORDER BY source_id,target_id LIMIT 200",
                (campaign_id,),
            ).fetchall()]
            combats = [dict(r) for r in db.execute(
                "SELECT id,round,turn_index,participants_json FROM combats WHERE campaign_id=? AND status='active' ORDER BY id LIMIT 20",
                (campaign_id,),
            ).fetchall()]
            quest_counts = {r["status"]: int(r["n"]) for r in db.execute(
                "SELECT status,COUNT(*) AS n FROM quests WHERE campaign_id=? GROUP BY status", (campaign_id,)
            ).fetchall()}
            world_rows = db.execute(
                "SELECT scope_type,scope_id,state_key,value_json FROM world_state WHERE campaign_id=? ORDER BY scope_type,scope_id,state_key LIMIT 500",
                (campaign_id,),
            ).fetchall()
            active_scene_row = db.execute(
                "SELECT id FROM scenes WHERE campaign_id=? AND status='active' ORDER BY updated_at DESC LIMIT 1", (campaign_id,)
            ).fetchone()
            scene_count = int(db.execute("SELECT COUNT(*) FROM scenes WHERE campaign_id=? AND status='active'", (campaign_id,)).fetchone()[0])
            living_total = int(db.execute("SELECT COUNT(*) FROM npcs WHERE campaign_id=? AND status='alive'", (campaign_id,)).fetchone()[0])
            dead_total = int(db.execute("SELECT COUNT(*) FROM npcs WHERE campaign_id=? AND status='dead'", (campaign_id,)).fetchone()[0])
            directors = WorldLayerKernel(self).active_directors_db(db, campaign_id, location_id=location, scene_id=active_scene_row["id"] if active_scene_row else None) if location else {"stack": [], "policies": {}, "event_multipliers": {}}

        characters = []
        for row in char_rows:
            c = self._decode_character_row(row)
            characters.append({
                "id": c["id"], "level": int(c["level"]), "hp": int(c["hp"]), "max_hp": int(c["max_hp"]),
                "hp_pct": round((c["hp"] / c["max_hp"]) * 100) if c["max_hp"] else 0, "ac": int(c["ac"]),
                "condition_count": len(c.get("conditions", [])), "inventory_count": len(c.get("inventory", [])),
                "resource_count": len(c.get("resources", {})),
            })
        npcs = []
        for row in npc_rows:
            n = self._decode_npc_row(row)
            npcs.append({
                "id": n["id"], "hp": int(n["hp"]), "max_hp": int(n["max_hp"]),
                "hp_pct": round((n["hp"] / n["max_hp"]) * 100) if n["max_hp"] else 0, "ac": int(n["ac"]),
                "attitude": int(n["attitude"]), "condition_count": len(n.get("conditions", [])),
                "belief_count": len(n.get("beliefs", [])), "goal_count": len(n.get("goals", [])), "memory_count": len(n.get("memory", [])),
            })
        factions = []
        for row in faction_rows:
            f = self._decode_faction_row(row)
            factions.append({"id": f["id"], "reputation": int(f["reputation"]), "reserve_score": int(f["reserve_score"]), "goal_count": len(f.get("goals", []))})
        active_combats = [{"id": c["id"], "round": int(c["round"]), "turn_index": int(c["turn_index"]), "participant_count": len(self._loads(c["participants_json"]))} for c in combats]
        numeric_world_state = []
        for row in world_rows:
            value = self._loads(row["value_json"])
            if not isinstance(value, (bool, int, float)):
                continue
            if location and row["scope_type"] == "location" and row["scope_id"] != location:
                continue
            numeric_world_state.append({"scope_type": row["scope_type"], "scope_id": row["scope_id"], "key": row["state_key"], "value": int(value) if isinstance(value, bool) else value})

        total = char_total + npc_total
        return {
            "internal_only": 1, "campaign_id": campaign_id, "revision": int(campaign["revision"]),
            "day_ordinal": int(world_dt.date().toordinal()), "minute_of_day": int(world_dt.hour * 60 + world_dt.minute), "weather_code": weather_code,
            "entity_window": {"limit": entity_limit, "total_count": total, "returned_count": len(characters)+len(npcs), "truncated": total > len(characters)+len(npcs)},
            "characters": characters, "npcs": npcs, "factions": factions, "relationships": relationships,
            "active_combats": active_combats, "quest_counts": quest_counts, "numeric_world_state": numeric_world_state,
            "world_tracking": {"living_npcs_total": living_total, "dead_npcs_total": dead_total, "active_scene_count": scene_count},
            "director_count": len(directors.get("stack", [])),
        }

    # ---------- visual continuity / image generation support ----------

    def set_visual_preferences(
        self,
        campaign_id: str = "default",
        *,
        auto_images: bool = True,
        scene_start: bool = True,
        battle_start: bool = True,
        new_location: bool = True,
        event_choice: bool = True,
        character_reference: bool = True,
        major_npc_reference: bool = True,
        art_style: str = "cinematic setting-authentic illustration",
        additional_instructions: str = "",
        negative_instructions: str = "",
    ) -> dict[str, Any]:
        campaign_id = self._clean_id(campaign_id)
        self._ensure_campaign_exists(campaign_id)
        with self._write_db() as db:
            db.execute(
                """INSERT INTO visual_preferences(campaign_id,auto_images,scene_start,battle_start,new_location,event_choice,character_reference,major_npc_reference,art_style,additional_instructions,negative_instructions,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id) DO UPDATE SET auto_images=excluded.auto_images,scene_start=excluded.scene_start,battle_start=excluded.battle_start,
                   new_location=excluded.new_location,event_choice=excluded.event_choice,character_reference=excluded.character_reference,major_npc_reference=excluded.major_npc_reference,art_style=excluded.art_style,
                   additional_instructions=excluded.additional_instructions,negative_instructions=excluded.negative_instructions,updated_at=excluded.updated_at""",
                (campaign_id, int(bool(auto_images)), int(bool(scene_start)), int(bool(battle_start)), int(bool(new_location)), int(bool(event_choice)), int(bool(character_reference)), int(bool(major_npc_reference)), art_style[:200], additional_instructions[:2000], negative_instructions[:2000], self._now()),
            )
        return self.get_visual_preferences(campaign_id)

    def get_visual_preferences(self, campaign_id: str = "default") -> dict[str, Any]:
        campaign_id = self._clean_id(campaign_id)
        self._ensure_campaign_exists(campaign_id)
        with self._db() as db:
            row = db.execute("SELECT * FROM visual_preferences WHERE campaign_id=?", (campaign_id,)).fetchone()
        defaults = self._default_visual_preferences()
        if not row:
            return {"campaign_id": campaign_id, **defaults}
        data = dict(row)
        for key in ("auto_images", "scene_start", "battle_start", "new_location", "event_choice", "character_reference", "major_npc_reference"):
            data[key] = bool(data[key])
        return data

    def set_visual_profile(
        self,
        campaign_id: str,
        entity_kind: str,
        entity_id: str,
        profile: dict[str, Any],
        *,
        merge: bool = True,
    ) -> dict[str, Any]:
        campaign_id = self._clean_id(campaign_id)
        entity_kind = self._actor_table(entity_kind)  # validates kind
        normalized_kind = "character" if entity_kind == "characters" else "npc"
        entity_id = self._clean_id(entity_id)
        with self._write_db() as db:
            self._get_actor_db(db, campaign_id, normalized_kind, entity_id)  # require real actor
            row = db.execute("SELECT profile_json FROM visual_profiles WHERE campaign_id=? AND entity_kind=? AND entity_id=?", (campaign_id, normalized_kind, entity_id)).fetchone()
            current = self._loads(row["profile_json"]) if row else {}
            current = dict(current) if merge else {}
            current.update(profile)
            db.execute(
                """INSERT INTO visual_profiles(campaign_id,entity_kind,entity_id,profile_json,updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(campaign_id,entity_kind,entity_id) DO UPDATE SET profile_json=excluded.profile_json,updated_at=excluded.updated_at""",
                (campaign_id, normalized_kind, entity_id, self._dumps(current), self._now()),
            )
        return self.get_visual_profile(campaign_id, normalized_kind, entity_id)

    def get_visual_profile(self, campaign_id: str, entity_kind: str, entity_id: str, *, missing_ok: bool = False) -> dict[str, Any]:
        campaign_id = self._clean_id(campaign_id)
        if entity_kind not in _ENTITY_KINDS:
            raise ValueError("entity_kind must be character or npc")
        entity_id = self._clean_id(entity_id)
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM visual_profiles WHERE campaign_id=? AND entity_kind=? AND entity_id=?",
                (campaign_id, entity_kind, entity_id),
            ).fetchone()
        if not row:
            if missing_ok:
                return {"campaign_id": campaign_id, "entity_kind": entity_kind, "entity_id": entity_id, "profile": {}}
            raise KeyError(f"visual profile not found: {entity_kind}:{entity_id}")
        data = dict(row)
        data["profile"] = self._loads(data.pop("profile_json"))
        return data

    def _visual_profiles_for_actors(self, campaign_id: str, actors: Sequence[dict[str, str]]) -> list[dict[str, Any]]:
        out = []
        with self._db() as db:
            for ref in actors:
                kind, actor_id = ref["kind"], ref["id"]
                actor = self._get_actor_db(db, campaign_id, kind, actor_id)
                row = db.execute("SELECT profile_json FROM visual_profiles WHERE campaign_id=? AND entity_kind=? AND entity_id=?", (campaign_id, kind, actor_id)).fetchone()
                profile = self._loads(row["profile_json"]) if row else {}
                gear=[]
                for ir in db.execute("SELECT item_id,qty,metadata_json FROM inventories WHERE campaign_id=? AND owner_kind=? AND owner_id=? AND qty>0 ORDER BY item_id LIMIT 24",(campaign_id,kind,actor_id)).fetchall():
                    gear.append({"item_id":ir["item_id"],"qty":float(ir["qty"]),"metadata":self._loads(ir["metadata_json"])})
                if kind == "character":
                    for item in actor.get("inventory", [])[:24]:
                        gear.append({"item_id":item,"qty":1} if isinstance(item,str) else dict(item))
                refrow=db.execute("SELECT image_ref,reference_prompt,visual_fingerprint_json,status FROM entity_visual_references WHERE campaign_id=? AND entity_kind=? AND entity_id=?",(campaign_id,kind,actor_id)).fetchone()
                reference=None
                if refrow:
                    reference={"image_ref":refrow["image_ref"],"reference_prompt":refrow["reference_prompt"],"visual_fingerprint":self._loads(refrow["visual_fingerprint_json"]),"status":refrow["status"]}
                if profile or gear or reference:
                    out.append({"kind":kind,"id":actor_id,"name":actor["name"],"profile":profile,"gear":gear,"reference":reference})
        return out

    def get_visual_reference(self, campaign_id: str, entity_kind: str, entity_id: str, *, missing_ok: bool = True) -> dict[str, Any]:
        campaign_id = self._clean_id(campaign_id)
        if entity_kind not in _ENTITY_KINDS:
            raise ValueError("entity_kind must be character or npc")
        entity_id = self._clean_id(entity_id)
        with self._db() as db:
            row = db.execute("SELECT * FROM entity_visual_references WHERE campaign_id=? AND entity_kind=? AND entity_id=?", (campaign_id, entity_kind, entity_id)).fetchone()
        if not row:
            if missing_ok:
                return {"campaign_id":campaign_id,"entity_kind":entity_kind,"entity_id":entity_id,"status":"missing","image_ref":None,"reference_prompt":"","visual_fingerprint":{}}
            raise KeyError(f"visual reference not found: {entity_kind}:{entity_id}")
        data=dict(row)
        data["visual_fingerprint"]=self._loads(data.pop("visual_fingerprint_json"))
        return data

    def _upsert_visual_reference_db(self, db: sqlite3.Connection, campaign_id: str, entity_kind: str, entity_id: str, *, image_ref: str | None, reference_prompt: str, visual_fingerprint: dict[str, Any], status: str, source_scene_key: str | None, source_revision: int) -> None:
        now=self._now()
        db.execute("""INSERT INTO entity_visual_references(campaign_id,entity_kind,entity_id,image_ref,reference_prompt,visual_fingerprint_json,status,source_scene_key,source_revision,created_at,updated_at)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(campaign_id,entity_kind,entity_id) DO UPDATE SET image_ref=excluded.image_ref,reference_prompt=excluded.reference_prompt,visual_fingerprint_json=excluded.visual_fingerprint_json,status=excluded.status,source_scene_key=excluded.source_scene_key,source_revision=excluded.source_revision,updated_at=excluded.updated_at""",
                   (campaign_id,entity_kind,entity_id,image_ref,reference_prompt[:12000],self._dumps(visual_fingerprint or {}),status[:40],source_scene_key,source_revision,now,now))

    def set_visual_state(
        self,
        campaign_id: str,
        scope_type: str,
        scope_id: str,
        state: dict[str, Any],
        *,
        merge: bool = True,
    ) -> dict[str, Any]:
        campaign_id = self._clean_id(campaign_id)
        scope_type = self._clean_id(scope_type)
        scope_id = self._clean_id(scope_id)
        if scope_type not in {"location", "scene", "combat"}:
            raise ValueError("scope_type must be location, scene, or combat")
        self._ensure_campaign_exists(campaign_id)
        with self._write_db() as db:
            row = db.execute("SELECT state_json FROM visual_states WHERE campaign_id=? AND scope_type=? AND scope_id=?", (campaign_id, scope_type, scope_id)).fetchone()
            current = self._loads(row["state_json"]) if row else {}
            next_state = dict(current) if merge else {}
            next_state.update(state)
            campaign_row = db.execute("SELECT revision FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
            if not campaign_row:
                raise KeyError(f"unknown campaign: {campaign_id}")
            source_revision = int(campaign_row["revision"])
            db.execute(
                """INSERT INTO visual_states(campaign_id,scope_type,scope_id,state_json,source_revision,updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,scope_type,scope_id) DO UPDATE SET state_json=excluded.state_json,source_revision=excluded.source_revision,updated_at=excluded.updated_at""",
                (campaign_id, scope_type, scope_id, self._dumps(next_state), source_revision, self._now()),
            )
        return self.get_visual_state(campaign_id, scope_type, scope_id)

    def get_visual_state(self, campaign_id: str, scope_type: str, scope_id: str, *, missing_ok: bool = False) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM visual_states WHERE campaign_id=? AND scope_type=? AND scope_id=?",
                (campaign_id, scope_type, scope_id),
            ).fetchone()
        if not row:
            if missing_ok:
                return {"campaign_id": campaign_id, "scope_type": scope_type, "scope_id": scope_id, "state": {}}
            raise KeyError(f"visual state not found: {scope_type}:{scope_id}")
        data = dict(row)
        data["state"] = self._loads(data.pop("state_json"))
        return data

    @staticmethod
    def _non_numeric_visual_descriptor(value: Any) -> Any:
        """Strip raw numbers before they are placed into image prompts."""
        if isinstance(value, bool):
            return "active" if value else "inactive"
        if isinstance(value, (int, float)):
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            clean = [WorldEngine._non_numeric_visual_descriptor(v) for v in value]
            return [v for v in clean if v not in (None, "", [], {})]
        if isinstance(value, dict):
            clean = {str(k): WorldEngine._non_numeric_visual_descriptor(v) for k, v in value.items()}
            return {k: v for k, v in clean.items() if v not in (None, "", [], {})}
        return None

    @staticmethod
    def _time_of_day_label(world_time: str) -> str:
        dt = datetime.fromisoformat(world_time)
        hour = dt.hour
        if 5 <= hour < 7:
            return "dawn"
        if 7 <= hour < 12:
            return "morning"
        if 12 <= hour < 17:
            return "afternoon"
        if 17 <= hour < 19:
            return "dusk"
        if 19 <= hour < 22:
            return "evening"
        return "night"

    @staticmethod
    def _severity(value: int | float | bool) -> float:
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        v = max(0.0, float(value))
        if v <= 1:
            return v
        if v <= 10:
            return v / 10.0
        if v <= 100:
            return v / 100.0
        return min(v / 1000.0, 1.0)

    def _derive_visual_hints(self, campaign_id: str, actors: Sequence[dict[str, str]], location_id: str | None) -> list[str]:
        """Translate hidden numeric state into qualitative image consequences without leaking numbers."""
        hints: list[str] = []
        for ref in actors:
            actor = self.get_actor(campaign_id, ref["kind"], ref["id"])
            ratio = actor["hp"] / actor["max_hp"] if actor.get("max_hp") else 0
            if ratio <= 0:
                hints.append(f"{actor['name']} is down and motionless unless the current narrative says otherwise")
            elif ratio <= 0.25:
                hints.append(f"{actor['name']} appears severely wounded and exhausted")
            elif ratio <= 0.5:
                hints.append(f"{actor['name']} appears visibly wounded")
            conditions = set(actor.get("conditions", []))
            if "poisoned" in conditions:
                hints.append(f"{actor['name']} shows visible signs of poison or sickness")
            if "frightened" in conditions:
                hints.append(f"{actor['name']} has a guarded, fearful posture")
            if "prone" in conditions:
                hints.append(f"{actor['name']} is on the ground")
            if "unconscious" in conditions:
                hints.append(f"{actor['name']} is unconscious")

        if location_id:
            tokens = {
                "corruption": ("faint unnatural corruption", "noticeable environmental corruption", "severe warped corruption"),
                "blight": ("subtle signs of blight", "visible dead or sick vegetation", "severe blight and dying terrain"),
                "decay": ("subtle decay", "obvious decay and deterioration", "extreme decay and ruin"),
                "damage": ("minor physical damage", "substantial environmental damage", "severe destruction"),
                "destruction": ("minor physical damage", "substantial environmental damage", "severe destruction"),
                "fire": ("small traces of fire or smoke", "active fire and smoke", "intense fire and heavy smoke"),
                "flood": ("standing water and wet terrain", "significant flooding", "severe flood conditions"),
                "fog": ("light mist", "thick fog", "dense obscuring fog"),
                "darkness": ("dim lighting", "deep shadows", "oppressive darkness"),
                "threat": ("subtle signs of danger", "obvious tension and danger", "immediate severe danger"),
                "alert": ("subtle alertness", "visible defensive readiness", "maximum defensive readiness"),
            }
            for row in self.get_world_state(campaign_id, "location", location_id):
                value = row["value"]
                if not isinstance(value, (bool, int, float)):
                    continue
                key = row["state_key"].lower()
                match = next((name for name in tokens if name in key), None)
                if not match:
                    continue
                sev = self._severity(value)
                if sev <= 0.05:
                    continue
                low, medium, high = tokens[match]
                hints.append(low if sev < 0.34 else medium if sev < 0.67 else high)
        # stable dedupe preserving order
        return list(dict.fromkeys(hints))

    def _get_image_generation(self, campaign_id: str, trigger_type: str, scene_key: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM image_generations WHERE campaign_id=? AND trigger_type=? AND scene_key=?",
                (campaign_id, trigger_type, scene_key),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        raw = data.pop("visual_context_json", "{}")
        data["visual_context"] = self._loads(raw or "{}")
        return data

    def recent_image_generations(self, campaign_id: str = "default", limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        with self._db() as db:
            rows = db.execute("SELECT * FROM image_generations WHERE campaign_id=? ORDER BY id DESC LIMIT ?", (campaign_id, limit)).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            raw = data.pop("visual_context_json", "{}")
            data["visual_context"] = self._loads(raw or "{}")
            out.append(data)
        return out

    def get_recent_image_context(self, campaign_id: str = "default", location_id: str | None = None, limit: int = 5) -> dict[str, Any]:
        limit = max(1, min(int(limit), 20))
        images = self.recent_image_generations(campaign_id, 100)
        if location_id:
            images = [x for x in images if x.get("location_id") == location_id]
        return {
            "campaign_id": campaign_id,
            "location_id": location_id,
            "recent": images[:limit],
            "location_visual_state": self.get_visual_state(campaign_id, "location", location_id, missing_ok=True) if location_id else None,
        }

    def record_image_generation(
        self,
        campaign_id: str,
        trigger_type: str,
        scene_key: str,
        *,
        title: str,
        prompt: str,
        aspect_ratio: str = "4:3",
        location_id: str | None = None,
        combat_id: str | None = None,
        image_ref: str | None = None,
        status: str = "generated",
        visual_context: dict[str, Any] | None = None,
        entity_kind: str | None = None,
        entity_id: str | None = None,
        set_as_primary_reference: bool = False,
    ) -> dict[str, Any]:
        campaign_id = self._clean_id(campaign_id)
        trigger_type = self._clean_id(trigger_type)
        scene_key = self._clean_id(scene_key)
        self._ensure_campaign_exists(campaign_id)
        now = self._now()
        with self._write_db() as db:
            campaign_row = db.execute("SELECT revision FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
            if not campaign_row:
                raise KeyError(f"unknown campaign: {campaign_id}")
            source_revision = int(campaign_row["revision"])
            db.execute(
                """INSERT INTO image_generations(campaign_id,trigger_type,scene_key,location_id,combat_id,title,prompt,aspect_ratio,status,image_ref,visual_context_json,source_revision,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,trigger_type,scene_key) DO UPDATE SET location_id=excluded.location_id,combat_id=excluded.combat_id,
                   title=excluded.title,prompt=excluded.prompt,aspect_ratio=excluded.aspect_ratio,status=excluded.status,image_ref=excluded.image_ref,
                   visual_context_json=excluded.visual_context_json,source_revision=excluded.source_revision,created_at=excluded.created_at""",
                (campaign_id, trigger_type, scene_key, location_id, combat_id, title[:200], prompt[:12000], aspect_ratio[:20], status[:40], image_ref, self._dumps(visual_context or {}), source_revision, now),
            )
            if set_as_primary_reference or trigger_type in {"character_reference","npc_reference"}:
                if entity_kind not in _ENTITY_KINDS or not entity_id:
                    raise ValueError("entity_kind and entity_id are required for a primary visual reference")
                self._get_actor_db(db,campaign_id,entity_kind,entity_id)
                prow=db.execute("SELECT profile_json FROM visual_profiles WHERE campaign_id=? AND entity_kind=? AND entity_id=?",(campaign_id,entity_kind,entity_id)).fetchone()
                fingerprint=self._loads(prow["profile_json"]) if prow else {}
                self._upsert_visual_reference_db(db,campaign_id,entity_kind,entity_id,image_ref=image_ref,reference_prompt=prompt,visual_fingerprint=fingerprint,status=status,source_scene_key=scene_key,source_revision=source_revision)
            # Visual rendering is metadata, not a simulation mutation: do not advance campaign revision.
            self._insert_event(
                db, campaign_id, source_revision, "image_generation", f"Image cue recorded: {title}",
                region=location_id, actor_id=combat_id or location_id or scene_key,
                payload={"trigger_type": trigger_type, "scene_key": scene_key, "aspect_ratio": aspect_ratio, "status": status, "image_ref": image_ref},
            )
        return self._get_image_generation(campaign_id, trigger_type, scene_key) or {}

    def _format_profile_for_prompt(self, item: dict[str, Any]) -> str:
        cleaned = self._non_numeric_visual_descriptor(item.get("profile", {})) or {}
        parts = []
        for key, value in cleaned.items():
            if isinstance(value, list):
                rendered = ", ".join(str(x) for x in value)
            elif isinstance(value, dict):
                rendered = ", ".join(f"{k}={v}" for k, v in value.items())
            else:
                rendered = str(value)
            if rendered:
                parts.append(f"{key}: {rendered}")
        return f"{item['name']} — " + "; ".join(parts) if parts else item["name"]

    def _format_visual_state_for_prompt(self, state: dict[str, Any]) -> str:
        cleaned = self._non_numeric_visual_descriptor(state) or {}
        if not cleaned:
            return ""
        return self._dumps(cleaned)

    @staticmethod
    def _director_context_for_prompt(directors: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
        """Render only qualitative authority context; never leak numeric authority/weights."""
        if not directors or not directors.get("stack"):
            return "", {}
        labels=[]
        compact=[]
        for d in directors["stack"][:8]:
            source=d.get("resolved_source_name") or d.get("name")
            kind=str(d.get("director_kind","power")).replace("_"," ")
            labels.append(f"{d.get('name')} ({kind}, represented by {source})")
            compact.append({"name":d.get("name"),"kind":d.get("director_kind"),"source":source,"policies":d.get("policies",{})})
        policy_bits=[]
        for key,value in (directors.get("policies") or {}).items():
            if isinstance(value,(str,bool)):
                policy_bits.append(f"{key}={value}")
        text="Regional powers shaping the scene: " + "; ".join(labels) + "."
        if policy_bits:
            text += " Visible policy/cultural cues: " + "; ".join(policy_bits[:8]) + "."
        return text, {"stack":compact,"policies":directors.get("policies",{})}

    @staticmethod
    def _scene_staging_for_prompt(scene: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
        if not scene:
            return "", {}
        actors=[]
        for ent in scene.get("entities",[])[:12]:
            actors.append(f"{ent.get('actor_id')} is staged in the {ent.get('zone','center')} with {ent.get('stance','neutral')} posture")
        feats=[]
        for feat in scene.get("features",[])[:16]:
            traits=[]
            if feat.get("blocks_los"): traits.append("blocks sight")
            if feat.get("difficult"): traits.append("difficult terrain")
            if feat.get("persistent"): traits.append("persistent")
            label=f"{feat.get('kind','feature')}"
            if traits: label += " ("+", ".join(traits)+")"
            feats.append(label)
        bits=[]
        if actors: bits.append("Scene staging: "+"; ".join(actors)+".")
        if feats: bits.append("Scene features: "+"; ".join(feats)+".")
        return " ".join(bits), {"scene_id":scene.get("id"),"scene_type":scene.get("scene_type"),"entities":[{"kind":x.get("actor_kind"),"id":x.get("actor_id"),"zone":x.get("zone"),"stance":x.get("stance")} for x in scene.get("entities",[])[:12]],"features":[{"id":x.get("id"),"kind":x.get("kind"),"blocks_los":bool(x.get("blocks_los")),"difficult":bool(x.get("difficult")),"persistent":bool(x.get("persistent"))} for x in scene.get("features",[])[:16]]}

    def build_image_cue(
        self,
        campaign_id: str = "default",
        *,
        trigger_type: str,
        location_id: str | None = None,
        combat_id: str | None = None,
        scene_key: str | None = None,
        summary: str | None = None,
        choice_options: Sequence[str] = (),
        aspect_ratio: str | None = None,
        force: bool = False,
        decision_phase: str = "before",
        entity_kind: str | None = None,
        entity_id: str | None = None,
    ) -> dict[str, Any]:
        campaign_id = self._clean_id(campaign_id)
        trigger_type = self._clean_id(trigger_type)
        decision_phase = str(decision_phase or "before").strip().lower()
        if decision_phase not in {"before", "after"}:
            raise ValueError("decision_phase must be before or after")
        prefs = self.get_visual_preferences(campaign_id)
        valid = {"scene_start", "battle_start", "new_location", "event_choice", "character_reference", "npc_reference"}
        if trigger_type not in valid:
            raise ValueError(f"trigger_type must be one of {sorted(valid)}")
        if not prefs["auto_images"]:
            return {"should_generate": False, "reason": "auto_images_disabled", "trigger_type": trigger_type, "campaign_id": campaign_id}
        pref_key = "character_reference" if trigger_type == "character_reference" else ("major_npc_reference" if trigger_type == "npc_reference" else trigger_type)
        if not prefs[pref_key]:
            return {"should_generate": False, "reason": f"{pref_key}_disabled", "trigger_type": trigger_type, "campaign_id": campaign_id}

        campaign = self.get_campaign(campaign_id)
        location = None
        if location_id:
            try:
                location = self.get_location(campaign_id, location_id)
            except KeyError:
                location = None

        title = ""
        actors: list[dict[str, str]] = []
        prompt_parts = [
            "Create one cinematic scene image faithful to the current campaign setting and World Bible.",
            f"Visual style: {prefs['art_style']}.",
            prefs["additional_instructions"],
        ]
        context: dict[str, Any] = {
            "campaign_time": campaign["world_time"],
            "weather": campaign["weather"],
            "trigger_type": trigger_type,
            "source_revision": int(campaign["revision"]),
        }
        bible = self.get_world_bible(campaign_id)
        bible_bits=[]
        for key,value in (bible.get("bible") or {}).items():
            if isinstance(value,str) and value.strip():
                bible_bits.append(f"{key}: {value.strip()}")
            elif isinstance(value,bool):
                bible_bits.append(f"{key}: {'yes' if value else 'no'}")
            elif isinstance(value,list) and value and all(isinstance(x,str) for x in value[:8]):
                bible_bits.append(f"{key}: {', '.join(value[:8])}")
            if len(bible_bits) >= 8:
                break
        if bible_bits:
            prompt_parts.append("World canon constraints: " + "; ".join(bible_bits) + ".")
            context["world_bible"]={"canon_version":bible.get("canon_version",0),"constraints":bible_bits}

        if trigger_type in {"character_reference", "npc_reference"}:
            expected_kind = "character" if trigger_type == "character_reference" else "npc"
            entity_kind = entity_kind or expected_kind
            if entity_kind != expected_kind or not entity_id:
                raise ValueError(f"{trigger_type} requires entity_kind={expected_kind} and entity_id")
            actor=self.get_actor(campaign_id,entity_kind,entity_id)
            if entity_kind == "npc" and str(actor.get("importance","minor")) != "major" and not force:
                return {"should_generate":False,"reason":"npc_not_major","trigger_type":trigger_type,"campaign_id":campaign_id,"entity_id":entity_id}
            existing_ref=self.get_visual_reference(campaign_id,entity_kind,entity_id,missing_ok=True)
            if str(existing_ref.get("status","")).lower() in {"generated","success","complete","completed"} and not force:
                return {"should_generate":False,"reason":"primary_reference_exists","trigger_type":trigger_type,"campaign_id":campaign_id,"entity_kind":entity_kind,"entity_id":entity_id,"existing_reference":existing_ref}
            profile=self.get_visual_profile(campaign_id,entity_kind,entity_id,missing_ok=True).get("profile",{})
            scene_key=scene_key or f"reference:{entity_kind}:{entity_id}"
            title=f"{actor['name']} — Canonical Character Reference"
            actors=[{"kind":entity_kind,"id":entity_id}]
            prompt_parts.extend([
                f"Create the canonical identity reference image for {actor['name']}. This establishes persistent visual continuity for future scene generations.",
                "Show a clear three-quarter full-body character view with a readable face, body proportions, species traits, clothing, armor, carried gear, and distinctive accessories in a simple setting-authentic environment.",
                "Preserve these identity-defining traits in later images. Do not redesign the face, body, species traits, hairstyle, signature clothing, armor, weapons, or persistent accessories unless authoritative World Engine state later changes them.",
            ])
            if profile:
                prompt_parts.append("Authoritative appearance profile: "+self._dumps(self._non_numeric_visual_descriptor(profile) or {})+".")
            context.update({"entity_kind":entity_kind,"entity_id":entity_id,"canonical_reference":True})
            if not location_id and actor.get("location") not in {None,"unknown"}:
                location_id=actor.get("location")
        elif trigger_type in {"scene_start", "new_location"}:
            if not location_id:
                raise ValueError("location_id is required for scene_start and new_location image cues")
            if not location:
                raise KeyError(f"unknown location: {location_id}")
            scene_key = scene_key or (f"new_location:{location_id}" if trigger_type == "new_location" else f"scene_start:{location_id}:r{campaign['revision']}")
            title = f"{location['name']} — {'Arrival' if trigger_type == 'new_location' else 'Scene Start'}"
            ctx = self.get_world_context(campaign_id, location_id, 6)
            actors = ([{"kind": "character", "id": c["id"]} for c in ctx["characters"][:4]] +
                      [{"kind": "npc", "id": n["id"]} for n in ctx["npcs"][:6]])
            char_names = [c["name"] for c in ctx["characters"][:4]]
            npc_names = [n["name"] for n in ctx["npcs"][:6]]
            prompt_parts.extend([
                f"Subject: {title}.",
                f"Location: {location['name']} in region {location['region']}.",
                f"Location description: {location['description'] or 'Setting-authentic environment inferred from the location identity, World Bible, and tags.'}",
                f"Tags: {', '.join(location.get('tags', [])) or 'none'}.",
                f"Time of day: {self._time_of_day_label(campaign['world_time'])}. Weather: {campaign['weather']}.",
                f"Characters present: {', '.join(char_names) or 'none recorded'}.",
                f"NPCs present: {', '.join(npc_names) or 'none recorded'}.",
                f"Narrative summary: {summary or 'Opening establishing shot of the current playable scene.'}",
                "Compose an establishing shot that helps players visualize the playable space and mood.",
            ])
            context.update({"location_id": location_id, "characters": char_names, "npcs": npc_names})
            scene_text, scene_compact = self._scene_staging_for_prompt(ctx.get("active_scene"))
            if scene_text:
                prompt_parts.append(scene_text)
                context["scene_tracking"] = scene_compact
            director_text, director_compact = self._director_context_for_prompt(ctx.get("directors"))
            if director_text:
                prompt_parts.append(director_text)
                context["directors"] = director_compact
        elif trigger_type == "battle_start":
            if not combat_id:
                raise ValueError("combat_id is required for battle_start image cues")
            combat = self.get_combat(campaign_id, combat_id)
            scene_key = scene_key or f"battle_start:{combat_id}"
            if not location_id:
                location_id = combat.get("location")
            if not location and location_id:
                try:
                    location = self.get_location(campaign_id, location_id)
                except KeyError:
                    location = None
            names = []
            actor_names: dict[tuple[str, str], str] = {}
            for p in combat["initiative"]:
                actor = self.get_actor(campaign_id, p["kind"], p["id"])
                names.append(actor["name"])
                actor_names[(p["kind"], p["id"])] = actor["name"]
                actors.append({"kind": p["kind"], "id": p["id"]})

            def zone_label(x: int, y: int) -> str:
                w=max(1,int(combat.get("grid_width",20))); h=max(1,int(combat.get("grid_height",20)))
                horiz="west" if x < w/3 else ("east" if x >= 2*w/3 else "center")
                vert="north" if y < h/3 else ("south" if y >= 2*h/3 else "center")
                if horiz=="center" and vert=="center": return "center"
                if horiz=="center": return vert
                if vert=="center": return horiz
                return f"{vert}-{horiz}"

            staging=[]
            for pos in combat.get("positions", []):
                name=actor_names.get((pos["actor_kind"],pos["actor_id"]),pos["actor_id"])
                label=zone_label(int(pos["x"]),int(pos["y"]))
                cover=str(pos.get("cover") or "none")
                staging.append(f"{name} is in the {label}" + (f" with {cover.replace('_',' ')} cover" if cover != "none" else ""))
            terrain_notes=[]
            for tile in combat.get("terrain", [])[:12]:
                traits=[]
                if tile.get("blocks_los"): traits.append("blocks sight")
                if tile.get("difficult"): traits.append("difficult terrain")
                if tile.get("hazard"): traits.append("hazard")
                if traits:
                    terrain_notes.append(f"{tile.get('kind','terrain')} in the {zone_label(int(tile['x']),int(tile['y']))} ({', '.join(traits)})")

            title = f"Battle at {location['name'] if location else combat['location']}"
            prompt_parts.extend([
                f"Subject: {title}.",
                f"Combat scene at location: {(location['name'] if location else combat['location'])}.",
                f"Participants: {', '.join(names)}.",
                f"Time of day: {self._time_of_day_label(campaign['world_time'])}. Weather: {campaign['weather']}.",
                f"Narrative summary: {summary or 'Opening battle shot as combat begins.'}",
                "Show clear opposing forces, readable action, and a battle-ready cinematic composition.",
            ])
            if staging:
                prompt_parts.append("Respect tactical staging from the active combat grid: " + "; ".join(staging) + ".")
            if terrain_notes:
                prompt_parts.append("Important battlefield terrain: " + "; ".join(terrain_notes) + ".")
            if location and location.get("description"):
                prompt_parts.append(f"Environment description: {location['description']}")
            context.update({"combat_id": combat_id, "participants": names, "location_id": location_id,
                            "combat_grid": {"width": combat.get("grid_width"), "height": combat.get("grid_height"),
                                            "positions": combat.get("positions", []), "terrain": combat.get("terrain", [])}})
            active_scene = self.get_scene(campaign_id)
            director_data = self.get_active_directors(campaign_id, location_id, active_scene.get("id") if active_scene and active_scene.get("location_id") == location_id else None)
            director_text, director_compact = self._director_context_for_prompt(director_data)
            if director_text:
                prompt_parts.append(director_text)
                context["directors"] = director_compact
        elif trigger_type == "event_choice":
            if not scene_key:
                raise ValueError("scene_key is required for event_choice image cues")
            title = "Critical Choice" if decision_phase == "before" else "Decision Consequence"
            if location_id:
                ctx = self.get_world_context(campaign_id, location_id, 6)
                actors = ([{"kind": "character", "id": c["id"]} for c in ctx["characters"][:4]] +
                          [{"kind": "npc", "id": n["id"]} for n in ctx["npcs"][:6]])
            if decision_phase == "before":
                decision_instruction = "Illustrate the dramatic moment immediately before the player must choose. Do not render choice text or UI."
                fallback_summary = "A major decision point in the story."
            else:
                decision_instruction = "Illustrate the immediate visible state after the player's committed decision and its resolved consequences. Do not render choice text or UI."
                fallback_summary = "The immediate visible consequences of the player's committed decision."
            prompt_parts.extend([
                f"Subject: {title}.",
                f"Time of day: {self._time_of_day_label(campaign['world_time'])}. Weather: {campaign['weather']}.",
                f"Narrative summary: {summary or fallback_summary}",
                f"Decision context: {' | '.join(choice_options) if choice_options else 'not supplied'}.",
                decision_instruction,
            ])
            if location:
                prompt_parts.append(f"Location: {location['name']} in region {location['region']}. {location['description']}")
            context.update({"location_id": location_id, "choice_options": list(choice_options), "decision_phase": decision_phase})
            if location_id:
                scene_data = self.get_scene(campaign_id)
                if scene_data and scene_data.get("location_id") == location_id:
                    scene_text, scene_compact = self._scene_staging_for_prompt(scene_data)
                    if scene_text:
                        prompt_parts.append(scene_text)
                        context["scene_tracking"] = scene_compact
                director_data = self.get_active_directors(campaign_id, location_id, scene_data.get("id") if scene_data and scene_data.get("location_id") == location_id else None)
                director_text, director_compact = self._director_context_for_prompt(director_data)
                if director_text:
                    prompt_parts.append(director_text)
                    context["directors"] = director_compact

        existing = self._get_image_generation(campaign_id, trigger_type, scene_key)
        completed_statuses = {"generated", "success", "complete", "completed"}
        if existing and str(existing.get("status", "")).strip().lower() in completed_statuses and not force:
            return {
                "should_generate": False,
                "reason": "already_generated_for_scene_key",
                "trigger_type": trigger_type,
                "campaign_id": campaign_id,
                "scene_key": scene_key,
                "existing": existing,
            }

        profiles = self._visual_profiles_for_actors(campaign_id, actors)
        if profiles:
            prompt_parts.append("Appearance continuity: " + " | ".join(self._format_profile_for_prompt(p) for p in profiles))
            gear_bits=[]
            for p in profiles:
                if p.get("gear"):
                    gear_bits.append(f"{p['name']} current authoritative gear: "+self._dumps(p["gear"]))
            if gear_bits:
                prompt_parts.append("Gear continuity: " + " | ".join(gear_bits) + ".")
            references=[]
            for p in profiles:
                ref=p.get("reference") or {}
                if str(ref.get("status","")).lower() in {"generated","success","complete","completed"}:
                    references.append({"kind":p["kind"],"id":p["id"],"name":p["name"],"image_ref":ref.get("image_ref"),"reference_prompt":ref.get("reference_prompt"),"visual_fingerprint":ref.get("visual_fingerprint")})
            if references:
                prompt_parts.append("IDENTITY REFERENCES: use each accessible reference image as the primary identity reference for that person. Preserve face, body proportions, species traits, hair, signature clothing/armor and persistent gear. If an image_ref is not accessible to the native image tool, reproduce the stored reference prompt and visual fingerprint exactly rather than inventing a redesign.")
                context["reference_images"]=references
            context["visual_profiles"] = profiles

        continuity: dict[str, Any] = {}
        if location_id:
            loc_state = self.get_visual_state(campaign_id, "location", location_id, missing_ok=True)["state"]
            if loc_state:
                continuity["location"] = loc_state
                rendered = self._format_visual_state_for_prompt(loc_state)
                if rendered:
                    prompt_parts.append(f"Persistent location appearance: {rendered}.")
        scene_state = self.get_visual_state(campaign_id, "scene", scene_key, missing_ok=True)["state"]
        if scene_state:
            continuity["scene"] = scene_state
            rendered = self._format_visual_state_for_prompt(scene_state)
            if rendered:
                prompt_parts.append(f"Scene continuity: {rendered}.")
        if combat_id:
            combat_state = self.get_visual_state(campaign_id, "combat", combat_id, missing_ok=True)["state"]
            if combat_state:
                continuity["combat"] = combat_state
                rendered = self._format_visual_state_for_prompt(combat_state)
                if rendered:
                    prompt_parts.append(f"Combat visual continuity: {rendered}.")
        if continuity:
            context["visual_state"] = continuity

        hints = self._derive_visual_hints(campaign_id, actors, location_id)
        if hints:
            prompt_parts.append("Current visible consequences of simulation state: " + "; ".join(hints) + ".")
            context["derived_visual_hints"] = hints

        recent = self.get_recent_image_context(campaign_id, location_id, 2)["recent"]
        if recent:
            continuity_titles = [x["title"] for x in recent if x.get("title")]
            if continuity_titles:
                context["recent_image_titles"] = continuity_titles

        aspect_ratio = aspect_ratio or ("3:4" if trigger_type in {"character_reference","npc_reference"} else ("16:9" if trigger_type in {"scene_start", "battle_start", "new_location"} else "4:3"))
        negative = prefs["negative_instructions"].strip()
        if negative:
            prompt_parts.append(f"Avoid: {negative}")
        prompt = " ".join(part.strip() for part in prompt_parts if part and part.strip())
        return {
            "should_generate": True,
            "trigger_type": trigger_type,
            "campaign_id": campaign_id,
            "scene_key": scene_key,
            "location_id": location_id,
            "combat_id": combat_id,
            "title": title,
            "aspect_ratio": aspect_ratio,
            "prompt": prompt,
            "visual_context": context,
            "instructions_for_gpt": "MANDATORY WHEN IMAGE GENERATION IS ENABLED: immediately generate exactly one native ChatGPT scene image from this prompt before continuing the scene or presenting the decision. Do not merely describe the image. Do not expose hidden simulation numbers. After successful generation, call recordImageGeneration with this scene_key and visual_context. If the native image tool is genuinely unavailable, continue play without inventing an image result.",
        }

    def snapshot(self, campaign_id: str = "default") -> dict[str, Any]:
        """Legacy core-domain JSON diagnostic; use SQLite backup for complete state."""
        with self._db() as db:
            char_ids = [r["id"] for r in db.execute("SELECT id FROM characters WHERE campaign_id=? ORDER BY id", (campaign_id,))]
            npc_ids = [r["id"] for r in db.execute("SELECT id FROM npcs WHERE campaign_id=? ORDER BY id", (campaign_id,))]
            faction_ids = [r["id"] for r in db.execute("SELECT id FROM factions WHERE campaign_id=? ORDER BY id", (campaign_id,))]
            location_ids = [r["id"] for r in db.execute("SELECT id FROM locations WHERE campaign_id=? ORDER BY id", (campaign_id,))]
            quest_ids = [r["id"] for r in db.execute("SELECT id FROM quests WHERE campaign_id=? ORDER BY id", (campaign_id,))]
            combat_ids = [r["id"] for r in db.execute("SELECT id FROM combats WHERE campaign_id=? ORDER BY id", (campaign_id,))]
            relationships = [dict(r) for r in db.execute("SELECT * FROM relationships WHERE campaign_id=? ORDER BY source_id,target_id", (campaign_id,))]
            director_rows = [dict(r) for r in db.execute("SELECT * FROM directors WHERE campaign_id=? ORDER BY priority,id", (campaign_id,))]
            ownership_rows = [dict(r) for r in db.execute("SELECT * FROM ownership WHERE campaign_id=? ORDER BY asset_kind,asset_id", (campaign_id,))]
            scene_ids = [r["id"] for r in db.execute("SELECT id FROM scenes WHERE campaign_id=? ORDER BY id", (campaign_id,))]
        for r in relationships:
            r["notes"] = self._loads(r.pop("notes_json"))
        for r in director_rows:
            r["weights"] = self._loads(r.pop("weights_json")); r["policies"] = self._loads(r.pop("policies_json")); r["enabled"] = bool(r["enabled"])
        for r in ownership_rows:
            r["metadata"] = self._loads(r.pop("metadata_json"))
        return {
            "schema_version": self.SCHEMA_VERSION,
            "campaign": self.get_campaign(campaign_id),
            "visual_preferences": self.get_visual_preferences(campaign_id),
            "internal_state": self.get_internal_state_block(campaign_id),
            "characters": [self.get_character(campaign_id, i) for i in char_ids],
            "npcs": [self.get_npc(campaign_id, i) for i in npc_ids],
            "factions": [self.get_faction(campaign_id, i) for i in faction_ids],
            "locations": [self.get_location(campaign_id, i) for i in location_ids],
            "world_state": self.get_world_state(campaign_id),
            "relationships": relationships,
            "quests": [self.get_quest(campaign_id, i) for i in quest_ids],
            "combats": [self.get_combat(campaign_id, i) for i in combat_ids],
            "scenes": [self.get_scene(campaign_id, i) for i in scene_ids],
            "directors": director_rows,
            "ownership": ownership_rows,
            "visual_profiles": [
                self.get_visual_profile(campaign_id, "character", i, missing_ok=True) for i in char_ids
            ] + [
                self.get_visual_profile(campaign_id, "npc", i, missing_ok=True) for i in npc_ids
            ],
            "image_generations": list(reversed(self.recent_image_generations(campaign_id, 100))),
            "simulation": {"config": self.simulation_config(campaign_id), "rules": self.list_simulation_rules(campaign_id)},
            "rules_kernel": RulesKernel(self).snapshot(campaign_id),
            "relationship_events": list(reversed(self.get_relationship_events(campaign_id, limit=100))),
            "world_bible": self.get_world_bible(campaign_id),
            "authoring_digest": self.author_world_digest(campaign_id),
            "open_content_gaps": self.author_list_gaps(campaign_id, 50),
            "events": list(reversed(self.recent_events(campaign_id, 100))),
        }
