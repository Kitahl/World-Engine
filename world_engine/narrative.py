from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from difflib import SequenceMatcher
from statistics import mean, pvariance
from typing import Any, Iterable, Sequence, TYPE_CHECKING, TypedDict

from .turn_policy import narrative_policy

if TYPE_CHECKING:
    import sqlite3
    from .engine import WorldEngine


NARRATIVE_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS we4_narrative_config (
    campaign_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL DEFAULT 'off' CHECK(mode IN ('off','shadow','compare','enforce')),
    style_profile_json TEXT NOT NULL DEFAULT '{}',
    quality_config_json TEXT NOT NULL DEFAULT '{}',
    generation_policy_json TEXT NOT NULL DEFAULT '{}',
    source_version TEXT NOT NULL DEFAULT '4.5.0',
    updated_at TEXT NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS we4_npc_voice_profiles (
    campaign_id TEXT NOT NULL,
    npc_id TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    source_revision INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,npc_id),
    FOREIGN KEY(campaign_id,npc_id) REFERENCES npcs(campaign_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS we4_narrative_beats (
    campaign_id TEXT NOT NULL,
    beat_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    preconditions_json TEXT NOT NULL DEFAULT '{}',
    involved_entities_json TEXT NOT NULL DEFAULT '[]',
    dramatic_objective TEXT NOT NULL DEFAULT '',
    information_to_reveal_json TEXT NOT NULL DEFAULT '[]',
    information_to_withhold_json TEXT NOT NULL DEFAULT '[]',
    relationship_pressure REAL NOT NULL DEFAULT 0 CHECK(relationship_pressure BETWEEN 0 AND 1),
    tension_before REAL NOT NULL DEFAULT 0 CHECK(tension_before BETWEEN 0 AND 1),
    tension_target REAL NOT NULL DEFAULT 0.5 CHECK(tension_target BETWEEN 0 AND 1),
    urgency REAL NOT NULL DEFAULT 0 CHECK(urgency BETWEEN 0 AND 1),
    saliency REAL NOT NULL DEFAULT 0.5 CHECK(saliency BETWEEN 0 AND 1),
    cooldown_turns INTEGER NOT NULL DEFAULT 0 CHECK(cooldown_turns >= 0),
    once_flag INTEGER NOT NULL DEFAULT 0 CHECK(once_flag IN (0,1)),
    repeat_policy TEXT NOT NULL DEFAULT 'state_change_or_cooldown',
    quest_links_json TEXT NOT NULL DEFAULT '[]',
    motif_candidates_json TEXT NOT NULL DEFAULT '[]',
    resolution_state TEXT NOT NULL DEFAULT 'eligible' CHECK(resolution_state IN ('eligible','blocked','resolved','retired')),
    last_selected_turn INTEGER,
    use_count INTEGER NOT NULL DEFAULT 0 CHECK(use_count >= 0),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,beat_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_we4_narrative_beats_selection
    ON we4_narrative_beats(campaign_id,resolution_state,saliency DESC,urgency DESC,beat_id);

CREATE TABLE IF NOT EXISTS we4_motif_threads (
    campaign_id TEXT NOT NULL,
    motif_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    meaning TEXT NOT NULL DEFAULT '',
    linked_arc TEXT,
    linked_entities_json TEXT NOT NULL DEFAULT '[]',
    activation_conditions_json TEXT NOT NULL DEFAULT '{}',
    last_used_turn INTEGER,
    use_count INTEGER NOT NULL DEFAULT 0 CHECK(use_count >= 0),
    cooldown_turns INTEGER NOT NULL DEFAULT 3 CHECK(cooldown_turns >= 0),
    max_recurrences INTEGER NOT NULL DEFAULT 4 CHECK(max_recurrences >= 1),
    transformation_stage INTEGER NOT NULL DEFAULT 0 CHECK(transformation_stage >= 0),
    eligible_scene_types_json TEXT NOT NULL DEFAULT '[]',
    recent_realizations_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','dormant','resolved','retired')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,motif_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_we4_motif_threads_selection
    ON we4_motif_threads(campaign_id,status,use_count,last_used_turn,motif_id);

CREATE TABLE IF NOT EXISTS we4_dialogue_state (
    campaign_id TEXT NOT NULL,
    speaker_key TEXT NOT NULL,
    listener_key TEXT NOT NULL,
    topic TEXT NOT NULL DEFAULT 'general',
    communicated_fact_ids_json TEXT NOT NULL DEFAULT '[]',
    concealed_fact_ids_json TEXT NOT NULL DEFAULT '[]',
    recent_speech_acts_json TEXT NOT NULL DEFAULT '[]',
    recent_realizations_json TEXT NOT NULL DEFAULT '[]',
    voice_state_json TEXT NOT NULL DEFAULT '{}',
    subtext_state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,speaker_key,listener_key,topic),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS we4_narrative_packets (
    campaign_id TEXT NOT NULL,
    packet_id TEXT NOT NULL,
    turn_id TEXT,
    mode TEXT NOT NULL CHECK(mode IN ('shadow','compare','enforce')),
    packet_version TEXT NOT NULL,
    packet_json TEXT NOT NULL,
    digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,packet_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_we4_narrative_packets_turn
    ON we4_narrative_packets(campaign_id,turn_id,created_at DESC);

CREATE TABLE IF NOT EXISTS we43_narrative_validation_contexts (
    campaign_id TEXT NOT NULL,
    packet_id TEXT NOT NULL,
    packet_digest TEXT NOT NULL,
    validation_context_json TEXT NOT NULL,
    context_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,packet_id),
    FOREIGN KEY(campaign_id,packet_id)
        REFERENCES we4_narrative_packets(campaign_id,packet_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS we4_narrative_outputs (
    campaign_id TEXT NOT NULL,
    output_id TEXT NOT NULL,
    packet_id TEXT NOT NULL,
    output_text TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    accepted INTEGER NOT NULL DEFAULT 0 CHECK(accepted IN (0,1)),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,output_id),
    FOREIGN KEY(campaign_id,packet_id) REFERENCES we4_narrative_packets(campaign_id,packet_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_we4_narrative_outputs_recent
    ON we4_narrative_outputs(campaign_id,accepted,created_at DESC);

CREATE TABLE IF NOT EXISTS we4_narrative_quality_receipts (
    campaign_id TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    packet_id TEXT,
    output_hash TEXT NOT NULL,
    hard_pass INTEGER NOT NULL CHECK(hard_pass IN (0,1)),
    hard_failures_json TEXT NOT NULL DEFAULT '[]',
    soft_warnings_json TEXT NOT NULL DEFAULT '[]',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    revision_required INTEGER NOT NULL DEFAULT 0 CHECK(revision_required IN (0,1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,receipt_id),
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_we4_narrative_quality_packet
    ON we4_narrative_quality_receipts(campaign_id,packet_id,created_at DESC);

CREATE TABLE IF NOT EXISTS we4_narrative_director_state (
    campaign_id TEXT PRIMARY KEY,
    turn_index INTEGER NOT NULL DEFAULT 0 CHECK(turn_index >= 0),
    tension REAL NOT NULL DEFAULT 0.25 CHECK(tension BETWEEN 0 AND 1),
    quiet_turns INTEGER NOT NULL DEFAULT 0 CHECK(quiet_turns >= 0),
    recent_beats_json TEXT NOT NULL DEFAULT '[]',
    last_major_turn INTEGER,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS we43_narrative_publication_attempts (
    campaign_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    packet_id TEXT NOT NULL,
    candidate_version TEXT NOT NULL CHECK(candidate_version = 'WEPUB-1.0'),
    candidate_digest TEXT NOT NULL,
    canonical_candidate_json TEXT,
    status TEXT NOT NULL CHECK(status IN (
        'rejected','semantic_review_required','semantic_rejected','accepted'
    )),
    reason_codes_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,attempt_id),
    UNIQUE(campaign_id,packet_id,candidate_digest),
    CHECK(status != 'rejected' OR canonical_candidate_json IS NULL),
    FOREIGN KEY(campaign_id,packet_id)
        REFERENCES we4_narrative_packets(campaign_id,packet_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_we43_publication_attempt_packet
    ON we43_narrative_publication_attempts(campaign_id,packet_id,created_at DESC);

CREATE TABLE IF NOT EXISTS we43_narrative_semantic_attestations (
    campaign_id TEXT NOT NULL,
    attestation_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    packet_id TEXT NOT NULL,
    candidate_digest TEXT NOT NULL,
    authority_kind TEXT NOT NULL CHECK(authority_kind IN ('human','trusted_server')),
    reviewer_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('approve','reject')),
    created_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,attestation_id),
    UNIQUE(campaign_id,packet_id,candidate_digest),
    FOREIGN KEY(campaign_id,attempt_id)
        REFERENCES we43_narrative_publication_attempts(campaign_id,attempt_id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id,packet_id)
        REFERENCES we4_narrative_packets(campaign_id,packet_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS we43_narrative_packet_acceptances (
    campaign_id TEXT NOT NULL,
    packet_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    candidate_digest TEXT NOT NULL,
    accepted_output_id TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    presentation_id TEXT NOT NULL,
    outbox_id TEXT NOT NULL,
    acceptance_mode TEXT NOT NULL CHECK(acceptance_mode IN ('deterministic','semantic_attested')),
    semantic_attestation_id TEXT,
    accepted_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,packet_id),
    FOREIGN KEY(campaign_id,attempt_id)
        REFERENCES we43_narrative_publication_attempts(campaign_id,attempt_id),
    FOREIGN KEY(campaign_id,accepted_output_id)
        REFERENCES we4_narrative_outputs(campaign_id,output_id),
    FOREIGN KEY(campaign_id,receipt_id)
        REFERENCES we4_narrative_quality_receipts(campaign_id,receipt_id),
    FOREIGN KEY(campaign_id,presentation_id)
        REFERENCES we_companion_presentations(campaign_id,presentation_id),
    FOREIGN KEY(campaign_id,outbox_id)
        REFERENCES we_companion_outbox(campaign_id,outbox_id),
    FOREIGN KEY(campaign_id,semantic_attestation_id)
        REFERENCES we43_narrative_semantic_attestations(campaign_id,attestation_id)
);
"""


class NarrativeStyleProfile(TypedDict, total=False):
    pov: str
    tense: str
    narrative_distance: str
    interiority_policy: str
    lexical_complexity: int
    sentence_length_variance: int
    paragraph_length: int
    sensory_density: int
    dialogue_density: int
    description_density: int
    metaphor_density: int
    figurative_language: int
    humor: int
    horror_intensity: int
    romanticism: int
    action_speed: int
    exposition_tolerance: int
    explicitness: int
    environment_emphasis: int
    positive_examples: list[str]
    custom_guidance: str


class NPCVoiceProfile(TypedDict, total=False):
    formality: str
    register: str
    vocabulary_complexity: str
    sentence_length: str
    verbosity: str
    directness: str
    humor: str
    sarcasm: str
    metaphor_preference: str
    dialect_strength: str
    cultural_idiom: list[str]
    religious_idiom: list[str]
    profession_vocabulary: list[str]
    hesitation_patterns: list[str]
    contraction_rate: str
    swearing_level: str
    taboo_topics: list[str]
    preferred_address_forms: list[str]
    catchphrases: list[str]
    catchphrase_cooldown: int
    speech_quirks: list[str]
    example_utterances: list[dict[str, Any]]


class NarrativeBeat(TypedDict, total=False):
    beat_id: str
    kind: str
    preconditions: dict[str, Any]
    involved_entities: list[str]
    dramatic_objective: str
    information_to_reveal: list[Any]
    information_to_withhold: list[Any]
    relationship_pressure: float
    tension_before: float
    tension_target: float
    urgency: float
    saliency: float
    cooldown_turns: int
    once: bool
    repeat_policy: str
    quest_links: list[str]
    motif_candidates: list[str]
    resolution_state: str


class DialoguePlan(TypedDict, total=False):
    speaker: str
    listener: str
    speech_act: str
    objective: str
    topic: str
    facts_known: list[dict[str, Any]]
    facts_to_reveal: list[str]
    facts_to_conceal: list[str]
    relationship: dict[str, Any]
    current_goal: Any
    dominant_motive: Any
    emotion: Any
    subtext: Any
    desired_effect: Any
    interruptibility: str
    voice_profile: NPCVoiceProfile
    voice_anchors: list[str]


class MotifThread(TypedDict, total=False):
    motif_id: str
    symbol: str
    meaning: str
    linked_arc: str
    linked_entities: list[str]
    activation_conditions: dict[str, Any]
    last_used_turn: int
    use_count: int
    cooldown_turns: int
    max_recurrences: int
    transformation_stage: int
    eligible_scene_types: list[str]
    recent_realizations: list[dict[str, Any]]
    status: str


class CutscenePacket(TypedDict, total=False):
    cutscene_version: str
    cutscene_id: str
    scene_goal: str
    location: str
    world_time: str
    participants: list[str]
    opening_image: str
    visual_focus: list[str]
    sound: list[str]
    music: dict[str, Any]
    beats: list[dict[str, Any]]
    dialogue_intents: list[dict[str, Any]]
    reveals: list[Any]
    physical_actions: list[dict[str, Any]]
    emotional_state: dict[str, Any]
    motifs: list[str]
    choices: list[dict[str, Any]]
    conditions: list[dict[str, Any]]
    ending_state: dict[str, Any]
    hidden_structure: bool
    authority_note: str


class NarrativeRenderPacket(TypedDict, total=False):
    packet_version: str
    engine_version: str
    packet_id: str
    digest: str
    mode: str
    authority: dict[str, Any]
    scene: dict[str, Any]
    narrative_director: dict[str, Any]
    dialogue_plan: DialoguePlan
    style_profile: NarrativeStyleProfile
    motif_thread: MotifThread
    cutscene_packet: CutscenePacket
    render_contract: dict[str, Any]
    quality_contract: dict[str, Any]
    generation_plan: dict[str, Any]


class NarrativeQualityReceipt(TypedDict, total=False):
    receipt_version: str
    receipt_id: str
    packet_id: str
    output_hash: str
    hard_pass: bool
    hard_failures: list[dict[str, Any]]
    soft_warnings: list[dict[str, Any]]
    metrics: dict[str, Any]
    revision_required: bool


class NarrativeDirectorState(TypedDict, total=False):
    campaign_id: str
    turn_index: int
    tension: float
    quiet_turns: int
    recent_beats: list[str]
    last_major_turn: int
    state: dict[str, Any]


NARRATIVE_MODES = {"off", "shadow", "compare", "enforce"}
POV_VALUES = {"first_person", "second_person", "third_person_limited", "third_person_omniscient"}
TENSE_VALUES = {"present", "past"}
INTERIORITY_VALUES = {"player_locked", "mechanically_supported_only", "authored_character"}
NUMERIC_STYLE_FIELDS = {
    "lexical_complexity", "sentence_length_variance", "paragraph_length",
    "sensory_density", "dialogue_density", "description_density", "metaphor_density",
    "figurative_language", "humor", "horror_intensity", "romanticism", "action_speed",
    "exposition_tolerance", "explicitness", "environment_emphasis",
}
VOICE_ENUM_FIELDS = {
    "formality", "register", "vocabulary_complexity", "sentence_length", "verbosity",
    "directness", "humor", "sarcasm", "metaphor_preference", "dialect_strength",
    "contraction_rate", "swearing_level",
}

DEFAULT_STYLE_PROFILE: dict[str, Any] = {
    "pov": "second_person",
    "tense": "present",
    "narrative_distance": "close",
    "interiority_policy": "player_locked",
    "lexical_complexity": 2,
    "sentence_length_variance": 3,
    "paragraph_length": 2,
    "sensory_density": 2,
    "dialogue_density": 2,
    "description_density": 2,
    "metaphor_density": 1,
    "figurative_language": 1,
    "humor": 1,
    "horror_intensity": 0,
    "romanticism": 0,
    "action_speed": 2,
    "exposition_tolerance": 1,
    "explicitness": 2,
    "environment_emphasis": 2,
    "positive_examples": [],
    "custom_guidance": "",
}

DEFAULT_QUALITY_CONFIG: dict[str, Any] = {
    "recent_output_window": 20,
    "near_duplicate_threshold": 0.88,
    "long_text_shingle_threshold": 0.72,
    "long_text_min_words": 80,
    "shingle_width": 5,
    "max_repeated_fourgrams": 2,
    "max_repeated_openings": 2,
    "max_you_see_notice": 2,
    "max_cliche_hits": 2,
    "hard_max_word_multiplier": 1.50,
    "hard_min_word_multiplier": 0.35,
    "revise_on_severe_soft_failure": True,
    "semantic_authority_review_required": True,
    # NRP-1.2 default. Set false only while upgrading a caller that cannot yet
    # declare beat_realizations; false restores the legacy offered==consumed rule.
    "strict_beat_realization": True,
}

DEFAULT_GENERATION_POLICY: dict[str, Any] = {
    "ordinary": "one_pass_then_deterministic_lint",
    "failed_gate": "targeted_revision_then_recheck",
    "major_scene": "up_to_two_candidates_rerank_then_optional_revision",
    "major_scene_candidate_count": 2,
    "major_scene_functions": ["major_consequence", "scene_opening", "quest_climax", "cutscene"],
    "model_owns": ["wording", "paragraphing", "surface_dialogue", "sensory_realization"],
    "model_does_not_own": ["world_facts", "mechanical_results", "secrets", "npc_knowledge", "player_authorship"],
}

DEFAULT_VOICE_PROFILE: dict[str, Any] = {
    "formality": "neutral",
    "register": "conversational",
    "vocabulary_complexity": "moderate",
    "sentence_length": "varied",
    "verbosity": "moderate",
    "directness": "contextual",
    "humor": "low",
    "sarcasm": "low",
    "metaphor_preference": "low",
    "dialect_strength": "light",
    "cultural_idiom": [],
    "religious_idiom": [],
    "profession_vocabulary": [],
    "hesitation_patterns": [],
    "contractions": "natural",
    "contraction_rate": "natural",
    "swearing_level": "none",
    "taboo_topics": [],
    "preferred_address_forms": [],
    "catchphrases": [],
    "catchphrase_cooldown": 8,
    "speech_quirks": [],
    "example_utterances": [],
}

Cliche_PATTERNS: tuple[str, ...] = (
    "a chill runs down your spine", "you can't help but", "little did you know",
    "the air was thick with tension", "time seemed to stand still", "a deafening silence",
    "a mixture of", "a testament to", "echoed through the air", "sent shivers down",
    "eyes glinting", "a smirk played", "heart pounding in your chest", "pregnant pause",
)

MECHANICS_LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("internal_tag", re.compile(r"::(?:WST|TMAF|NCSE|SCAL|TEMS|trace|debug|delta|audit)[A-Za-z0-9_:\[\]{}|.-]*", re.I)),
    ("compressed_tag", re.compile(r"\bT\[[A-Z][^\]\n]{0,200}\]")),
    ("engine_receipt", re.compile(r"\b_engine_receipt\b", re.I)),
    ("context_packet", re.compile(r"\bcontext_packet\b", re.I)),
    ("capability_plan", re.compile(r"\bcapability_(?:plan|id)\b", re.I)),
    ("revision_field", re.compile(r"\brevision_(?:before|after|delta)\b", re.I)),
    ("database_identifier", re.compile(r"\bwe4_[a-z0-9_]+\b", re.I)),
)

PLAYER_AGENCY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("invented_decision", re.compile(r"\byou\s+(?:decide|choose|resolve|intend|determine|opt)\b", re.I)),
    ("invented_private_thought", re.compile(r"\byou\s+(?:think|believe|realize|remember|suspect|wonder|hope|want|wish|know)\b", re.I)),
    ("invented_player_dialogue", re.compile(r"\byou\s+(?:say|ask|reply|answer|whisper|shout|mutter|promise|admit|confess|declare)\b", re.I)),
    ("invented_emotional_conclusion", re.compile(r"\byou\s+(?:feel|are)\s+(?:afraid|fearful|angry|furious|sad|grief-stricken|relieved|ashamed|guilty|jealous|attracted|hopeful|despairing|in love)\b", re.I)),
    ("forced_interiority", re.compile(r"\byou\s+can(?:not|'t)\s+help\s+but\b", re.I)),
)


def _merge_dict(base: dict[str, Any], patch: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(base)
    for key, value in dict(patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_dict(out[key], value)
        else:
            out[key] = value
    return out


class NarrativeKernel:
    """Typed narrative-control layer for World Engine 4.5.0.

    It compiles authoritative state into a rendering contract. It never resolves
    mechanics and never treats model prose as world truth. Persistent dialogue
    caching stores semantic state, not literal generated lines.
    """

    VERSION = "4.5.0"
    PACKET_VERSION = "NRP-1.2"
    RECEIPT_VERSION = "NQR-1.2"

    def __init__(self, engine: "WorldEngine"):
        self.e = engine

    @staticmethod
    def _table_exists(db: "sqlite3.Connection", table: str) -> bool:
        return db.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
            (table,),
        ).fetchone() is not None

    def migrate_v41_rows_db(self, db: "sqlite3.Connection") -> dict[str, Any]:
        """Import compatible v4.1 rows without deleting or overwriting source data.

        v4.1 used a separate ``we41_*`` schema and an incompatible packet
        contract. Mutable configuration, voices, beats, motifs, and dialogue
        memory are converted once. Historical v4.1 receipts remain in their
        original table because their packet contract cannot be losslessly
        rewritten as NRP-1.2/NQR-1.2.
        """

        feature_id = "v41_narrative_import"
        prior = db.execute(
            "SELECT details_json FROM we42_schema_features WHERE feature_id=?",
            (feature_id,),
        ).fetchone()
        if prior:
            return self.e._loads(prior["details_json"])

        required = {
            "we41_narrative_config", "we41_npc_voice_profiles", "we41_narrative_beats",
            "we41_motif_threads", "we41_dialogue_memory", "we41_narrative_receipts",
        }
        present = {name for name in required if self._table_exists(db, name)}
        details: dict[str, Any] = {
            "source_schema_detected": bool(present),
            "source_tables": sorted(present),
            "config_imported": 0,
            "voices_imported": 0,
            "voices_skipped_missing_npc": 0,
            "voice_fields_removed": 0,
            "beats_imported": 0,
            "motifs_imported": 0,
            "dialogue_rows_imported": 0,
            "historical_receipts_preserved": 0,
        }

        if "we41_narrative_config" in present:
            for row in db.execute("SELECT * FROM we41_narrative_config ORDER BY campaign_id").fetchall():
                campaign_id = row["campaign_id"]
                if db.execute("SELECT 1 FROM we4_narrative_config WHERE campaign_id=?", (campaign_id,)).fetchone():
                    continue
                style = _merge_dict(DEFAULT_STYLE_PROFILE, self.e._loads(row["style_json"]))
                quality = _merge_dict(DEFAULT_QUALITY_CONFIG, self.e._loads(row["quality_json"]))
                mode = str(row["rollout_mode"] or "off").lower()
                if mode not in NARRATIVE_MODES:
                    mode = "off"
                db.execute(
                    """INSERT INTO we4_narrative_config(
                           campaign_id,mode,style_profile_json,quality_config_json,generation_policy_json,source_version,updated_at)
                       VALUES(?,?,?,?,?,'4.1.0-imported',?)""",
                    (
                        campaign_id, mode, self.e._dumps(style), self.e._dumps(quality),
                        self.e._dumps(DEFAULT_GENERATION_POLICY), row["updated_at"],
                    ),
                )
                db.execute(
                    """INSERT INTO we4_narrative_director_state(
                           campaign_id,turn_index,tension,quiet_turns,recent_beats_json,last_major_turn,state_json,updated_at)
                       VALUES(?,?,0.25,0,'[]',NULL,'{}',?)
                       ON CONFLICT(campaign_id) DO NOTHING""",
                    (campaign_id, max(0, int(row["output_counter"] or 0)), row["updated_at"]),
                )
                details["config_imported"] += 1

        forbidden_voice_fields = {"author_style", "famous_author", "imitate", "copyrighted_author"}
        if "we41_npc_voice_profiles" in present:
            for row in db.execute("SELECT * FROM we41_npc_voice_profiles ORDER BY campaign_id,npc_id").fetchall():
                campaign_id, npc_id = row["campaign_id"], row["npc_id"]
                if db.execute(
                    "SELECT 1 FROM we4_npc_voice_profiles WHERE campaign_id=? AND npc_id=?",
                    (campaign_id, npc_id),
                ).fetchone():
                    continue
                if not db.execute(
                    "SELECT 1 FROM npcs WHERE campaign_id=? AND id=?", (campaign_id, npc_id)
                ).fetchone():
                    details["voices_skipped_missing_npc"] += 1
                    continue
                profile = _merge_dict(DEFAULT_VOICE_PROFILE, self.e._loads(row["profile_json"]))
                removed = sorted(forbidden_voice_fields.intersection(profile))
                for key in removed:
                    profile.pop(key, None)
                details["voice_fields_removed"] += len(removed)
                examples: list[dict[str, Any]] = []
                for item in list(profile.get("example_utterances") or [])[:5]:
                    if isinstance(item, dict):
                        text = self._clean_text(item.get("text"), limit=500)
                        contexts = [self._clean_text(x, limit=80) for x in (item.get("contexts") or [])][:8]
                    else:
                        text = self._clean_text(item, limit=500)
                        contexts = []
                    if text and not re.search(r"\bin the style of\b|\bwrite like\b|\bimitate\b", text, re.I):
                        examples.append({"text": text, "contexts": contexts, "source": "v4.1_import"})
                profile["example_utterances"] = examples
                profile["voice_anchor_ready"] = len(examples) >= 2
                profile["originality_status"] = "v4.1 import sanitized; not independently copyright-verified"
                if removed:
                    profile["migration_removed_fields"] = removed
                revision_row = db.execute("SELECT revision FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
                revision = int(revision_row[0]) if revision_row else 0
                db.execute(
                    """INSERT INTO we4_npc_voice_profiles(
                           campaign_id,npc_id,profile_json,source_revision,created_at,updated_at)
                       VALUES(?,?,?,?,?,?)""",
                    (campaign_id, npc_id, self.e._dumps(profile), revision, row["updated_at"], row["updated_at"]),
                )
                details["voices_imported"] += 1

        if "we41_narrative_beats" in present:
            for row in db.execute("SELECT * FROM we41_narrative_beats ORDER BY campaign_id,beat_id").fetchall():
                campaign_id, beat_id = row["campaign_id"], row["beat_id"]
                if db.execute(
                    "SELECT 1 FROM we4_narrative_beats WHERE campaign_id=? AND beat_id=?", (campaign_id, beat_id)
                ).fetchone():
                    continue
                beat = dict(self.e._loads(row["beat_json"]))
                state = str(beat.get("resolution_state") or "eligible").lower()
                if not bool(row["enabled"]) and state == "eligible":
                    state = "blocked"
                if state not in {"eligible", "blocked", "resolved", "retired"}:
                    state = "blocked"
                metadata = dict(beat.get("metadata") or {})
                metadata["imported_from"] = "4.1.0"
                now = row["updated_at"]
                db.execute(
                    """INSERT INTO we4_narrative_beats(
                           campaign_id,beat_id,kind,preconditions_json,involved_entities_json,dramatic_objective,
                           information_to_reveal_json,information_to_withhold_json,relationship_pressure,tension_before,
                           tension_target,urgency,saliency,cooldown_turns,once_flag,repeat_policy,quest_links_json,
                           motif_candidates_json,resolution_state,last_selected_turn,use_count,metadata_json,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        campaign_id, beat_id, self._clean_text(beat.get("kind") or "storylet", limit=80),
                        self.e._dumps(dict(beat.get("preconditions") or {})),
                        self.e._dumps(self._dedupe(beat.get("involved_entities") or [], limit=40)),
                        self._clean_text(beat.get("dramatic_objective"), limit=1000),
                        self.e._dumps(self._dedupe(beat.get("information_to_reveal") or [], limit=50)),
                        self.e._dumps(self._dedupe(beat.get("information_to_withhold") or [], limit=50)),
                        self._clamp(beat.get("relationship_pressure", 0) or 0), self._clamp(beat.get("tension_before", 0) or 0),
                        self._clamp(beat.get("tension_target", 0.5) if beat.get("tension_target") is not None else 0.5),
                        self._clamp(beat.get("urgency", 0) or 0), self._clamp(beat.get("saliency", 0.5) or 0.5),
                        max(0, int(beat.get("cooldown_turns", beat.get("cooldown", 0)) or 0)),
                        int(bool(beat.get("once", False))), self._clean_text(beat.get("repeat_policy") or "state_change_or_cooldown", limit=120),
                        self.e._dumps(self._dedupe(beat.get("quest_links") or [], limit=30)),
                        self.e._dumps(self._dedupe(beat.get("motif_candidates") or [], limit=30)), state,
                        row["last_used_counter"], max(0, int(row["use_count"] or 0)), self.e._dumps(metadata), now, now,
                    ),
                )
                details["beats_imported"] += 1

        if "we41_motif_threads" in present:
            for row in db.execute("SELECT * FROM we41_motif_threads ORDER BY campaign_id,motif_id").fetchall():
                campaign_id, motif_id = row["campaign_id"], row["motif_id"]
                if db.execute(
                    "SELECT 1 FROM we4_motif_threads WHERE campaign_id=? AND motif_id=?", (campaign_id, motif_id)
                ).fetchone():
                    continue
                motif = dict(self.e._loads(row["motif_json"]))
                status = str(motif.get("status") or ("active" if bool(row["enabled"]) else "dormant")).lower()
                if status not in {"active", "dormant", "resolved", "retired"}:
                    status = "dormant"
                metadata = dict(motif.get("metadata") or {})
                metadata["imported_from"] = "4.1.0"
                now = row["updated_at"]
                db.execute(
                    """INSERT INTO we4_motif_threads(
                           campaign_id,motif_id,symbol,meaning,linked_arc,linked_entities_json,activation_conditions_json,
                           last_used_turn,use_count,cooldown_turns,max_recurrences,transformation_stage,
                           eligible_scene_types_json,recent_realizations_json,status,metadata_json,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        campaign_id, motif_id, self._clean_text(motif.get("symbol") or motif_id, limit=160),
                        self._clean_text(motif.get("meaning"), limit=1000), self._clean_text(motif.get("linked_arc"), limit=160) or None,
                        self.e._dumps(self._dedupe(motif.get("linked_entities") or [], limit=40)),
                        self.e._dumps(dict(motif.get("activation_conditions") or {})), row["last_used_counter"],
                        max(0, int(row["use_count"] or 0)), max(0, int(motif.get("cooldown_turns", motif.get("cooldown", 3)) or 0)),
                        max(1, int(motif.get("max_recurrences", 4) or 4)), max(0, int(motif.get("transformation_stage", 0) or 0)),
                        self.e._dumps(self._dedupe(motif.get("eligible_scene_types") or [], limit=50)),
                        self.e._dumps(self._dedupe(motif.get("recent_realizations") or [], limit=50)), status,
                        self.e._dumps(metadata), now, now,
                    ),
                )
                details["motifs_imported"] += 1

        if "we41_dialogue_memory" in present:
            for row in db.execute("SELECT * FROM we41_dialogue_memory ORDER BY campaign_id,npc_id,thread_id").fetchall():
                dialogue_state = dict(self.e._loads(row["state_json"]))
                db.execute(
                    """INSERT INTO we4_dialogue_state(
                           campaign_id,speaker_key,listener_key,topic,communicated_fact_ids_json,concealed_fact_ids_json,
                           recent_speech_acts_json,recent_realizations_json,voice_state_json,subtext_state_json,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(campaign_id,speaker_key,listener_key,topic) DO NOTHING""",
                    (
                        row["campaign_id"], f"npc:{row['npc_id']}", "player:local-player", row["thread_id"],
                        self.e._dumps(dialogue_state.get("facts_communicated") or []),
                        self.e._dumps(dialogue_state.get("facts_concealed") or []),
                        self.e._dumps(dialogue_state.get("speech_acts") or []),
                        self.e._dumps(dialogue_state.get("recent_realization_hashes") or []),
                        "{}", self.e._dumps({
                            "imported_from": "4.1.0",
                            "source_state_preserved_in": "we41_dialogue_memory",
                        }), row["updated_at"],
                    ),
                )
                details["dialogue_rows_imported"] += 1

        if "we41_narrative_receipts" in present:
            details["historical_receipts_preserved"] = int(
                db.execute("SELECT COUNT(*) FROM we41_narrative_receipts").fetchone()[0]
            )

        db.execute(
            "INSERT INTO we42_schema_features(feature_id,feature_version,applied_at,details_json) VALUES(?,?,?,?)",
            (feature_id, "1", self.e._now(), self.e._dumps(details)),
        )
        return details

    @staticmethod
    def _canonical(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

    @classmethod
    def _digest(cls, value: Any) -> str:
        return hashlib.sha256(cls._canonical(value).encode("utf-8")).hexdigest()

    @staticmethod
    def _clamp(value: Any, low: float = 0.0, high: float = 1.0) -> float:
        return max(low, min(high, float(value)))

    @staticmethod
    def _clean_text(value: Any, *, limit: int = 1000) -> str:
        text = str(value or "").strip()
        return text[:limit]

    @staticmethod
    def _dedupe(values: Iterable[Any], *, limit: int = 100) -> list[Any]:
        out: list[Any] = []
        seen: set[str] = set()
        for value in values:
            marker = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            if marker in seen:
                continue
            seen.add(marker)
            out.append(value)
            if len(out) >= limit:
                break
        return out

    @classmethod
    def _forbidden_phrases(cls, values: Iterable[Any]) -> list[str]:
        phrases: list[str] = []
        pending = list(values)
        while pending:
            value = pending.pop()
            if isinstance(value, str):
                phrase = value.strip()
            elif isinstance(value, dict):
                pending.extend(value.values())
                phrase = ""
            elif isinstance(value, (list, tuple, set)):
                pending.extend(value)
                phrase = ""
            else:
                phrase = ""
            if len(phrase) >= 4:
                phrases.append(phrase)
        return [str(x) for x in cls._dedupe(phrases, limit=150)]

    @classmethod
    def _public_packet_value(cls, value: Any, forbidden_phrases: Sequence[str]) -> Any:
        """Remove private control fields and redact forbidden literals recursively."""
        private_keys = {
            "information_to_withhold", "forbidden_facts", "facts_to_conceal",
            "concealed_fact_ids", "context_packet", "activation_inspector",
            "principal", "_engine_receipt", "capability_plan", "capability_id",
            "revision_before", "revision_after", "revision_delta", "expected_revision",
            "commit_model", "debug", "validation_context", "validation_context_json",
            "context_digest", "forbidden_literals",
        }
        if isinstance(value, dict):
            return {
                str(key): cls._public_packet_value(item, forbidden_phrases)
                for key, item in value.items()
                if str(key) not in private_keys
            }
        if isinstance(value, list):
            return [cls._public_packet_value(item, forbidden_phrases) for item in value]
        if isinstance(value, tuple):
            return [cls._public_packet_value(item, forbidden_phrases) for item in value]
        if isinstance(value, str):
            redacted = value
            for phrase in sorted(forbidden_phrases, key=len, reverse=True):
                redacted = re.sub(re.escape(phrase), "[REDACTED]", redacted, flags=re.I)
            return redacted
        return value

    @classmethod
    def _bounded(cls, value: Any, max_chars: int = 2400) -> Any:
        text = cls._canonical(value)
        if len(text) <= max_chars:
            return value
        if isinstance(value, dict):
            out: dict[str, Any] = {"_truncated": True}
            for key in sorted(value):
                candidate = dict(out)
                candidate[str(key)] = value[key]
                if len(cls._canonical(candidate)) <= max_chars:
                    out[str(key)] = value[key]
                else:
                    preview = cls._canonical(value[key])
                    remaining = max(40, max_chars - len(cls._canonical(out)) - len(str(key)) - 40)
                    out[str(key)] = {"_preview": preview[:remaining], "_truncated": True}
                    break
            return out
        if isinstance(value, list):
            out_list: list[Any] = []
            for item in value:
                if len(cls._canonical([*out_list, item, {"_truncated": True}])) <= max_chars:
                    out_list.append(item)
                else:
                    break
            return [*out_list, {"_truncated": True, "omitted": max(0, len(value) - len(out_list))}]
        return {"_preview": text[: max(20, max_chars - 40)], "_truncated": True}

    def _ensure_config_db(self, db: "sqlite3.Connection", campaign_id: str) -> None:
        now = self.e._now()
        db.execute(
            """INSERT INTO we4_narrative_config(
                   campaign_id,mode,style_profile_json,quality_config_json,generation_policy_json,source_version,updated_at)
               VALUES(?,'off',?,?,?,'4.5.0',?)
               ON CONFLICT(campaign_id) DO NOTHING""",
            (
                campaign_id,
                self.e._dumps(DEFAULT_STYLE_PROFILE),
                self.e._dumps(DEFAULT_QUALITY_CONFIG),
                self.e._dumps(DEFAULT_GENERATION_POLICY),
                now,
            ),
        )
        db.execute(
            """INSERT INTO we4_narrative_director_state(
                   campaign_id,turn_index,tension,quiet_turns,recent_beats_json,last_major_turn,state_json,updated_at)
               VALUES(?,0,0.25,0,'[]',NULL,'{}',?)
               ON CONFLICT(campaign_id) DO NOTHING""",
            (campaign_id, now),
        )

    def _decode_config(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        data["style_profile"] = self.e._loads(data.pop("style_profile_json"))
        data["quality_config"] = self.e._loads(data.pop("quality_config_json"))
        data["generation_policy"] = self.e._loads(data.pop("generation_policy_json"))
        return data

    def get_config(self, campaign_id: str) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        with self.e._write_db() as db:
            self._ensure_config_db(db, campaign_id)
            row = db.execute("SELECT * FROM we4_narrative_config WHERE campaign_id=?", (campaign_id,)).fetchone()
        config = self._decode_config(row)
        # Existing databases may hold a pre-v4.3 JSON object. Additive defaults
        # are normalized on read without rewriting historical configuration.
        config["style_profile"] = self._validate_style_profile(config["style_profile"])
        config["quality_config"] = self._validate_quality_config(config["quality_config"])
        config["generation_policy"] = self._validate_generation_policy(config["generation_policy"])
        return config

    def _validate_style_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        out = _merge_dict(DEFAULT_STYLE_PROFILE, profile)
        if out.get("pov") not in POV_VALUES:
            raise ValueError(f"pov must be one of {sorted(POV_VALUES)}")
        if out.get("tense") not in TENSE_VALUES:
            raise ValueError(f"tense must be one of {sorted(TENSE_VALUES)}")
        if out.get("interiority_policy") not in INTERIORITY_VALUES:
            raise ValueError(f"interiority_policy must be one of {sorted(INTERIORITY_VALUES)}")
        for field in NUMERIC_STYLE_FIELDS:
            value = int(out.get(field, DEFAULT_STYLE_PROFILE.get(field, 0)))
            if not 0 <= value <= 4:
                raise ValueError(f"style field {field} must be 0..4")
            out[field] = value
        examples = out.get("positive_examples") or []
        if not isinstance(examples, list) or len(examples) > 5:
            raise ValueError("positive_examples must be a list of at most 5 original examples")
        out["positive_examples"] = [self._clean_text(x, limit=700) for x in examples if self._clean_text(x, limit=700)]
        out["custom_guidance"] = self._clean_text(out.get("custom_guidance"), limit=1200)
        return out

    def _validate_quality_config(self, config: dict[str, Any]) -> dict[str, Any]:
        out = _merge_dict(DEFAULT_QUALITY_CONFIG, config)
        out["recent_output_window"] = max(1, min(int(out["recent_output_window"]), 100))
        out["near_duplicate_threshold"] = self._clamp(out["near_duplicate_threshold"], 0.5, 1.0)
        out["long_text_shingle_threshold"] = self._clamp(out["long_text_shingle_threshold"], 0.25, 1.0)
        out["long_text_min_words"] = max(20, min(int(out["long_text_min_words"]), 2000))
        out["shingle_width"] = max(3, min(int(out["shingle_width"]), 12))
        for key in ("max_repeated_fourgrams", "max_repeated_openings", "max_you_see_notice", "max_cliche_hits"):
            out[key] = max(0, min(int(out[key]), 50))
        out["hard_max_word_multiplier"] = self._clamp(out["hard_max_word_multiplier"], 1.0, 4.0)
        out["hard_min_word_multiplier"] = self._clamp(out["hard_min_word_multiplier"], 0.0, 1.0)
        out["revise_on_severe_soft_failure"] = bool(out["revise_on_severe_soft_failure"])
        out["semantic_authority_review_required"] = bool(out["semantic_authority_review_required"])
        out["strict_beat_realization"] = bool(out["strict_beat_realization"])
        return out

    def _validate_generation_policy(self, policy: dict[str, Any]) -> dict[str, Any]:
        out = _merge_dict(DEFAULT_GENERATION_POLICY, policy)
        out["major_scene_candidate_count"] = max(1, min(int(out.get("major_scene_candidate_count", 2)), 4))
        scene_types = out.get("major_scene_functions") or []
        if not isinstance(scene_types, list):
            raise ValueError("major_scene_functions must be a list")
        out["major_scene_functions"] = [self._clean_text(x, limit=80) for x in scene_types if self._clean_text(x, limit=80)]
        return out

    def configure(
        self,
        campaign_id: str,
        *,
        mode: str | None = None,
        style_profile: dict[str, Any] | None = None,
        quality_config: dict[str, Any] | None = None,
        generation_policy: dict[str, Any] | None = None,
        reason: str = "narrative configuration updated",
    ) -> dict[str, Any]:
        current = self.get_config(campaign_id)
        next_mode = str(mode or current["mode"]).strip().lower()
        if next_mode not in NARRATIVE_MODES:
            raise ValueError(f"mode must be one of {sorted(NARRATIVE_MODES)}")
        style = self._validate_style_profile(_merge_dict(current["style_profile"], style_profile))
        quality = self._validate_quality_config(_merge_dict(current["quality_config"], quality_config))
        generation = self._validate_generation_policy(_merge_dict(current["generation_policy"], generation_policy))
        with self.e._write_db() as db:
            now = self.e._now()
            db.execute(
                """UPDATE we4_narrative_config
                   SET mode=?,style_profile_json=?,quality_config_json=?,generation_policy_json=?,source_version='4.5.0',updated_at=?
                   WHERE campaign_id=?""",
                (next_mode, self.e._dumps(style), self.e._dumps(quality), self.e._dumps(generation), now, campaign_id),
            )
            revision = self.e._next_revision(db, campaign_id)
            self.e._insert_event(
                db, campaign_id, revision, "narrative_config_updated", reason,
                payload={"mode": next_mode, "source_version": self.VERSION},
            )
        result = self.get_config(campaign_id)
        result["revision"] = revision
        return result

    # ------------------------------------------------------------------
    # Persistent NPC voice profiles
    # ------------------------------------------------------------------

    def _validate_voice_profile(self, profile: dict[str, Any], *, require_examples: bool) -> dict[str, Any]:
        forbidden_keys = {"author_style", "famous_author", "imitate", "copyrighted_author"}
        bad_keys = sorted(forbidden_keys.intersection(profile))
        if bad_keys:
            raise ValueError(f"copyrighted-author imitation fields are not supported: {bad_keys}")
        out = _merge_dict(DEFAULT_VOICE_PROFILE, profile)
        for field in VOICE_ENUM_FIELDS:
            out[field] = self._clean_text(out.get(field), limit=80) or str(DEFAULT_VOICE_PROFILE[field])
        list_fields = (
            "cultural_idiom", "religious_idiom", "profession_vocabulary", "hesitation_patterns",
            "taboo_topics", "preferred_address_forms", "catchphrases", "speech_quirks",
        )
        for field in list_fields:
            values = out.get(field) or []
            if not isinstance(values, list):
                raise ValueError(f"{field} must be a list")
            out[field] = self._dedupe([self._clean_text(x, limit=160) for x in values if self._clean_text(x, limit=160)], limit=20)
        out["catchphrase_cooldown"] = max(1, min(int(out.get("catchphrase_cooldown", 8)), 100))
        raw_examples = out.get("example_utterances") or []
        if not isinstance(raw_examples, list) or len(raw_examples) > 5:
            raise ValueError("example_utterances must contain at most 5 original examples")
        examples: list[dict[str, Any]] = []
        for item in raw_examples:
            if isinstance(item, str):
                text = self._clean_text(item, limit=500)
                contexts: list[str] = []
            elif isinstance(item, dict):
                text = self._clean_text(item.get("text"), limit=500)
                contexts = [self._clean_text(x, limit=80) for x in (item.get("contexts") or [])][:8]
            else:
                raise ValueError("each example utterance must be text or an object with text/contexts")
            if not text:
                continue
            if re.search(r"\bin the style of\b|\bwrite like\b|\bimitate\b", text, re.I):
                raise ValueError("voice examples must be original character utterances, not author-imitation instructions")
            examples.append({"text": text, "contexts": contexts, "source": "world_engine_original"})
        if require_examples and not 2 <= len(examples) <= 5:
            raise ValueError("saved NPC voice profiles require 2-5 original example utterances")
        out["example_utterances"] = examples
        out["voice_anchor_ready"] = len(examples) >= 2
        out["originality_status"] = "user_or_world_engine_attested; not independently copyright-verified"
        return out

    def save_voice_profile(
        self,
        campaign_id: str,
        npc_id: str,
        profile: dict[str, Any],
        *,
        reason: str = "NPC voice profile updated",
    ) -> dict[str, Any]:
        npc_id = self.e._clean_id(npc_id)
        self.e.get_npc(campaign_id, npc_id)
        cleaned = self._validate_voice_profile(dict(profile or {}), require_examples=True)
        with self.e._write_db() as db:
            now = self.e._now()
            source_revision = int(db.execute("SELECT revision FROM campaigns WHERE id=?", (campaign_id,)).fetchone()[0])
            db.execute(
                """INSERT INTO we4_npc_voice_profiles(campaign_id,npc_id,profile_json,source_revision,created_at,updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,npc_id) DO UPDATE SET
                       profile_json=excluded.profile_json,source_revision=excluded.source_revision,updated_at=excluded.updated_at""",
                (campaign_id, npc_id, self.e._dumps(cleaned), source_revision, now, now),
            )
            revision = self.e._next_revision(db, campaign_id)
            db.execute(
                "UPDATE we4_npc_voice_profiles SET source_revision=? WHERE campaign_id=? AND npc_id=?",
                (revision, campaign_id, npc_id),
            )
            self.e._insert_event(
                db, campaign_id, revision, "npc_voice_profile_updated", reason,
                actor_id=npc_id, payload={"npc_id": npc_id, "example_count": len(cleaned["example_utterances"])},
            )
        return self.get_voice_profile(campaign_id, npc_id)

    def _fallback_voice_profile(self, npc: dict[str, Any]) -> dict[str, Any]:
        profile = dict(DEFAULT_VOICE_PROFILE)
        importance = str(npc.get("importance") or "minor")
        profile["formality"] = "formal" if importance == "major" else "neutral"
        profile["register"] = "institutional" if npc.get("faction_id") else "conversational"
        profile["voice_anchor_ready"] = False
        profile["originality_status"] = "no stored examples"
        profile["derived_from"] = ["npc.importance", "npc.faction_id"]
        return profile

    def get_voice_profile(self, campaign_id: str, npc_id: str) -> dict[str, Any]:
        npc_id = self.e._clean_id(npc_id)
        npc = self.e.get_npc(campaign_id, npc_id)
        with self.e._db() as db:
            row = db.execute(
                "SELECT * FROM we4_npc_voice_profiles WHERE campaign_id=? AND npc_id=?",
                (campaign_id, npc_id),
            ).fetchone()
        if row:
            data = dict(row)
            data["profile"] = self.e._loads(data.pop("profile_json"))
            data["stored"] = True
            return data
        return {
            "campaign_id": campaign_id,
            "npc_id": npc_id,
            "profile": self._fallback_voice_profile(npc),
            "source_revision": int(self.e.get_campaign(campaign_id)["revision"]),
            "stored": False,
        }

    def list_voice_profiles(self, campaign_id: str) -> list[dict[str, Any]]:
        with self.e._db() as db:
            rows = db.execute(
                "SELECT * FROM we4_npc_voice_profiles WHERE campaign_id=? ORDER BY npc_id",
                (campaign_id,),
            ).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["profile"] = self.e._loads(data.pop("profile_json"))
            data["stored"] = True
            out.append(data)
        return out

    @staticmethod
    def _select_voice_examples(profile: dict[str, Any], scene_function: str, topic: str | None) -> list[str]:
        examples = profile.get("example_utterances") or []
        ranked: list[tuple[int, int, str]] = []
        for idx, item in enumerate(examples):
            if isinstance(item, str):
                text, contexts = item, []
            else:
                text, contexts = str(item.get("text") or ""), [str(x).lower() for x in (item.get("contexts") or [])]
            score = 0
            if scene_function.lower() in contexts:
                score += 3
            if topic and str(topic).lower() in contexts:
                score += 2
            ranked.append((-score, idx, text))
        ranked.sort()
        return [text for _, _, text in ranked[:3] if text]

    # ------------------------------------------------------------------
    # Narrative beats / storylets
    # ------------------------------------------------------------------

    def save_beat(self, campaign_id: str, beat_id: str, **values: Any) -> dict[str, Any]:
        beat_id = self.e._clean_id(beat_id)
        kind = self._clean_text(values.get("kind") or "general", limit=80)
        preconditions = dict(values.get("preconditions") or {})
        involved = self._dedupe(values.get("involved_entities") or [], limit=40)
        reveal = self._dedupe(values.get("information_to_reveal") or [], limit=50)
        withhold = self._dedupe(values.get("information_to_withhold") or [], limit=50)
        quest_links = self._dedupe(values.get("quest_links") or [], limit=30)
        motifs = self._dedupe(values.get("motif_candidates") or [], limit=30)
        resolution_state = str(values.get("resolution_state") or "eligible").lower()
        if resolution_state not in {"eligible", "blocked", "resolved", "retired"}:
            raise ValueError("resolution_state must be eligible, blocked, resolved, or retired")
        numeric = {
            "relationship_pressure": self._clamp(values.get("relationship_pressure", 0)),
            "tension_before": self._clamp(values.get("tension_before", 0)),
            "tension_target": self._clamp(values.get("tension_target", 0.5)),
            "urgency": self._clamp(values.get("urgency", 0)),
            "saliency": self._clamp(values.get("saliency", 0.5)),
        }
        cooldown = max(0, min(int(values.get("cooldown_turns", 0)), 100000))
        once = bool(values.get("once", values.get("once_flag", False)))
        repeat_policy = self._clean_text(values.get("repeat_policy") or "state_change_or_cooldown", limit=120)
        metadata = dict(values.get("metadata") or {})
        objective = self._clean_text(values.get("dramatic_objective"), limit=1000)
        self.e._ensure_campaign_exists(campaign_id)
        with self.e._write_db() as db:
            now = self.e._now()
            db.execute(
                """INSERT INTO we4_narrative_beats(
                       campaign_id,beat_id,kind,preconditions_json,involved_entities_json,dramatic_objective,
                       information_to_reveal_json,information_to_withhold_json,relationship_pressure,tension_before,
                       tension_target,urgency,saliency,cooldown_turns,once_flag,repeat_policy,quest_links_json,
                       motif_candidates_json,resolution_state,last_selected_turn,use_count,metadata_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,0,?,?,?)
                   ON CONFLICT(campaign_id,beat_id) DO UPDATE SET
                       kind=excluded.kind,preconditions_json=excluded.preconditions_json,
                       involved_entities_json=excluded.involved_entities_json,dramatic_objective=excluded.dramatic_objective,
                       information_to_reveal_json=excluded.information_to_reveal_json,
                       information_to_withhold_json=excluded.information_to_withhold_json,
                       relationship_pressure=excluded.relationship_pressure,tension_before=excluded.tension_before,
                       tension_target=excluded.tension_target,urgency=excluded.urgency,saliency=excluded.saliency,
                       cooldown_turns=excluded.cooldown_turns,once_flag=excluded.once_flag,
                       repeat_policy=excluded.repeat_policy,quest_links_json=excluded.quest_links_json,
                       motif_candidates_json=excluded.motif_candidates_json,resolution_state=excluded.resolution_state,
                       metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                (
                    campaign_id, beat_id, kind, self.e._dumps(preconditions), self.e._dumps(involved), objective,
                    self.e._dumps(reveal), self.e._dumps(withhold), numeric["relationship_pressure"],
                    numeric["tension_before"], numeric["tension_target"], numeric["urgency"], numeric["saliency"],
                    cooldown, int(once), repeat_policy, self.e._dumps(quest_links), self.e._dumps(motifs),
                    resolution_state, self.e._dumps(metadata), now, now,
                ),
            )
            revision = self.e._next_revision(db, campaign_id)
            self.e._insert_event(
                db, campaign_id, revision, "narrative_beat_updated", f"Narrative beat saved: {beat_id}",
                payload={"beat_id": beat_id, "kind": kind, "resolution_state": resolution_state},
            )
        result = self.get_beat(campaign_id, beat_id)
        result["revision"] = revision
        return result

    def _decode_beat(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        for source, target in (
            ("preconditions_json", "preconditions"),
            ("involved_entities_json", "involved_entities"),
            ("information_to_reveal_json", "information_to_reveal"),
            ("information_to_withhold_json", "information_to_withhold"),
            ("quest_links_json", "quest_links"),
            ("motif_candidates_json", "motif_candidates"),
            ("metadata_json", "metadata"),
        ):
            data[target] = self.e._loads(data.pop(source))
        data["once"] = bool(data.pop("once_flag"))
        return data

    def get_beat(self, campaign_id: str, beat_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            row = db.execute(
                "SELECT * FROM we4_narrative_beats WHERE campaign_id=? AND beat_id=?",
                (campaign_id, self.e._clean_id(beat_id)),
            ).fetchone()
        if not row:
            raise KeyError(f"unknown narrative beat: {beat_id}")
        return self._decode_beat(row)

    def list_beats(self, campaign_id: str, *, resolution_state: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM we4_narrative_beats WHERE campaign_id=?"
        params: list[Any] = [campaign_id]
        if resolution_state:
            sql += " AND resolution_state=?"
            params.append(str(resolution_state).lower())
        sql += " ORDER BY saliency DESC,urgency DESC,beat_id LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        with self.e._db() as db:
            return [self._decode_beat(r) for r in db.execute(sql, params).fetchall()]

    # ------------------------------------------------------------------
    # Motifs / foreshadowing threads
    # ------------------------------------------------------------------

    def save_motif(self, campaign_id: str, motif_id: str, **values: Any) -> dict[str, Any]:
        motif_id = self.e._clean_id(motif_id)
        symbol = self._clean_text(values.get("symbol"), limit=160)
        if not symbol:
            raise ValueError("motif symbol is required")
        status = str(values.get("status") or "active").lower()
        if status not in {"active", "dormant", "resolved", "retired"}:
            raise ValueError("motif status must be active, dormant, resolved, or retired")
        cooldown = max(0, min(int(values.get("cooldown_turns", values.get("cooldown", 3))), 100000))
        max_recurrences = max(1, min(int(values.get("max_recurrences", 4)), 1000))
        stage = max(0, min(int(values.get("transformation_stage", 0)), 1000))
        self.e._ensure_campaign_exists(campaign_id)
        with self.e._write_db() as db:
            now = self.e._now()
            db.execute(
                """INSERT INTO we4_motif_threads(
                       campaign_id,motif_id,symbol,meaning,linked_arc,linked_entities_json,activation_conditions_json,
                       last_used_turn,use_count,cooldown_turns,max_recurrences,transformation_stage,
                       eligible_scene_types_json,recent_realizations_json,status,metadata_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,NULL,0,?,?,? ,?,'[]',?,?,?,?)
                   ON CONFLICT(campaign_id,motif_id) DO UPDATE SET
                       symbol=excluded.symbol,meaning=excluded.meaning,linked_arc=excluded.linked_arc,
                       linked_entities_json=excluded.linked_entities_json,
                       activation_conditions_json=excluded.activation_conditions_json,
                       cooldown_turns=excluded.cooldown_turns,max_recurrences=excluded.max_recurrences,
                       transformation_stage=excluded.transformation_stage,
                       eligible_scene_types_json=excluded.eligible_scene_types_json,status=excluded.status,
                       metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                (
                    campaign_id, motif_id, symbol, self._clean_text(values.get("meaning"), limit=1000),
                    self._clean_text(values.get("linked_arc"), limit=160) or None,
                    self.e._dumps(self._dedupe(values.get("linked_entities") or [], limit=40)),
                    self.e._dumps(dict(values.get("activation_conditions") or {})), cooldown, max_recurrences,
                    stage, self.e._dumps(self._dedupe(values.get("eligible_scene_types") or [], limit=50)),
                    status, self.e._dumps(dict(values.get("metadata") or {})), now, now,
                ),
            )
            revision = self.e._next_revision(db, campaign_id)
            self.e._insert_event(
                db, campaign_id, revision, "motif_thread_updated", f"Motif thread saved: {motif_id}",
                payload={"motif_id": motif_id, "symbol": symbol, "status": status},
            )
        result = self.get_motif(campaign_id, motif_id)
        result["revision"] = revision
        return result

    def _decode_motif(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        for source, target in (
            ("linked_entities_json", "linked_entities"),
            ("activation_conditions_json", "activation_conditions"),
            ("eligible_scene_types_json", "eligible_scene_types"),
            ("recent_realizations_json", "recent_realizations"),
            ("metadata_json", "metadata"),
        ):
            data[target] = self.e._loads(data.pop(source))
        return data

    def get_motif(self, campaign_id: str, motif_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            row = db.execute(
                "SELECT * FROM we4_motif_threads WHERE campaign_id=? AND motif_id=?",
                (campaign_id, self.e._clean_id(motif_id)),
            ).fetchone()
        if not row:
            raise KeyError(f"unknown motif: {motif_id}")
        return self._decode_motif(row)

    def list_motifs(self, campaign_id: str, *, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM we4_motif_threads WHERE campaign_id=?"
        params: list[Any] = [campaign_id]
        if status:
            sql += " AND status=?"
            params.append(str(status).lower())
        sql += " ORDER BY status,motif_id LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        with self.e._db() as db:
            return [self._decode_motif(r) for r in db.execute(sql, params).fetchall()]

    # ------------------------------------------------------------------
    # Semantic dialogue cache
    # ------------------------------------------------------------------

    @staticmethod
    def _entity_key(kind: str | None, entity_id: str | None) -> str | None:
        if not kind or not entity_id:
            return None
        return f"{kind}:{entity_id}"

    def get_dialogue_state(self, campaign_id: str, speaker_key: str, listener_key: str, topic: str = "general") -> dict[str, Any]:
        with self.e._db() as db:
            row = db.execute(
                """SELECT * FROM we4_dialogue_state
                   WHERE campaign_id=? AND speaker_key=? AND listener_key=? AND topic=?""",
                (campaign_id, speaker_key, listener_key, topic or "general"),
            ).fetchone()
        if not row:
            return {
                "campaign_id": campaign_id, "speaker_key": speaker_key, "listener_key": listener_key,
                "topic": topic or "general", "communicated_fact_ids": [], "concealed_fact_ids": [],
                "recent_speech_acts": [], "recent_realizations": [], "voice_state": {}, "subtext_state": {},
            }
        data = dict(row)
        for source, target in (
            ("communicated_fact_ids_json", "communicated_fact_ids"),
            ("concealed_fact_ids_json", "concealed_fact_ids"),
            ("recent_speech_acts_json", "recent_speech_acts"),
            ("recent_realizations_json", "recent_realizations"),
            ("voice_state_json", "voice_state"),
            ("subtext_state_json", "subtext_state"),
        ):
            data[target] = self.e._loads(data.pop(source))
        return data

    def record_dialogue_state(
        self,
        campaign_id: str,
        speaker_key: str,
        listener_key: str,
        *,
        topic: str = "general",
        communicated_fact_ids: Sequence[str] = (),
        concealed_fact_ids: Sequence[str] = (),
        speech_act: str | None = None,
        realization_fingerprint: str | None = None,
        voice_state: dict[str, Any] | None = None,
        subtext_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        topic = self._clean_text(topic or "general", limit=160)
        current = self.get_dialogue_state(campaign_id, speaker_key, listener_key, topic)
        communicated = self._dedupe([*current["communicated_fact_ids"], *communicated_fact_ids], limit=200)
        concealed = self._dedupe([*current["concealed_fact_ids"], *concealed_fact_ids], limit=200)
        speech_acts = list(current["recent_speech_acts"])
        if speech_act:
            speech_acts.append(self._clean_text(speech_act, limit=80))
        speech_acts = speech_acts[-12:]
        realizations = list(current["recent_realizations"])
        if realization_fingerprint:
            realizations.append(self._clean_text(realization_fingerprint, limit=256))
        realizations = realizations[-20:]
        next_voice = _merge_dict(current["voice_state"], voice_state)
        next_subtext = _merge_dict(current["subtext_state"], subtext_state)
        with self.e._write_db() as db:
            now = self.e._now()
            db.execute(
                """INSERT INTO we4_dialogue_state(
                       campaign_id,speaker_key,listener_key,topic,communicated_fact_ids_json,concealed_fact_ids_json,
                       recent_speech_acts_json,recent_realizations_json,voice_state_json,subtext_state_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,speaker_key,listener_key,topic) DO UPDATE SET
                       communicated_fact_ids_json=excluded.communicated_fact_ids_json,
                       concealed_fact_ids_json=excluded.concealed_fact_ids_json,
                       recent_speech_acts_json=excluded.recent_speech_acts_json,
                       recent_realizations_json=excluded.recent_realizations_json,
                       voice_state_json=excluded.voice_state_json,subtext_state_json=excluded.subtext_state_json,
                       updated_at=excluded.updated_at""",
                (
                    campaign_id, speaker_key, listener_key, topic, self.e._dumps(communicated),
                    self.e._dumps(concealed), self.e._dumps(speech_acts), self.e._dumps(realizations),
                    self.e._dumps(next_voice), self.e._dumps(next_subtext), now,
                ),
            )
        return self.get_dialogue_state(campaign_id, speaker_key, listener_key, topic)

    # ------------------------------------------------------------------
    # Director state and selection
    # ------------------------------------------------------------------

    def get_director_state(self, campaign_id: str) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        with self.e._write_db() as db:
            self._ensure_config_db(db, campaign_id)
            row = db.execute("SELECT * FROM we4_narrative_director_state WHERE campaign_id=?", (campaign_id,)).fetchone()
        data = dict(row)
        data["recent_beats"] = self.e._loads(data.pop("recent_beats_json"))
        data["state"] = self.e._loads(data.pop("state_json"))
        return data

    @staticmethod
    def _condition_list_matches(conditions: dict[str, Any], key: str, value: str | None) -> bool:
        requested = conditions.get(key)
        if not requested:
            return True
        if not isinstance(requested, list):
            requested = [requested]
        return value is not None and str(value) in {str(x) for x in requested}

    def _conditions_match(
        self,
        conditions: dict[str, Any],
        *,
        scene_function: str,
        trigger_type: str | None,
        capability_ids: set[str],
        location_id: str | None,
        major_consequence: bool,
        tension: float,
        has_dialogue: bool,
    ) -> bool:
        if not self._condition_list_matches(conditions, "scene_functions", scene_function):
            return False
        if not self._condition_list_matches(conditions, "trigger_types", trigger_type):
            return False
        if not self._condition_list_matches(conditions, "location_ids", location_id):
            return False
        any_caps = {str(x) for x in (conditions.get("capability_any") or [])}
        all_caps = {str(x) for x in (conditions.get("capability_all") or [])}
        if any_caps and not capability_ids.intersection(any_caps):
            return False
        if all_caps and not all_caps.issubset(capability_ids):
            return False
        if "major_consequence" in conditions and bool(conditions["major_consequence"]) != bool(major_consequence):
            return False
        if "requires_dialogue" in conditions and bool(conditions["requires_dialogue"]) != bool(has_dialogue):
            return False
        if tension < float(conditions.get("min_tension", 0.0)):
            return False
        if tension > float(conditions.get("max_tension", 1.0)):
            return False
        return True

    def _select_beat(
        self,
        campaign_id: str,
        *,
        scene_function: str,
        trigger_type: str | None,
        capability_ids: set[str],
        location_id: str | None,
        major_consequence: bool,
        has_dialogue: bool,
        turn_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        state = self.get_director_state(campaign_id)
        turn_index = int(state["turn_index"])
        tension = float(state["tension"])
        candidates: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
        rejected: list[dict[str, Any]] = []
        for beat in self.list_beats(campaign_id, resolution_state="eligible", limit=500):
            if beat["once"] and int(beat["use_count"]) > 0:
                rejected.append({"beat_id": beat["beat_id"], "reason": "once_already_used"})
                continue
            last = beat.get("last_selected_turn")
            if last is not None and turn_index - int(last) < int(beat["cooldown_turns"]):
                rejected.append({"beat_id": beat["beat_id"], "reason": "cooldown"})
                continue
            if not self._conditions_match(
                beat["preconditions"], scene_function=scene_function, trigger_type=trigger_type,
                capability_ids=capability_ids, location_id=location_id,
                major_consequence=major_consequence, tension=tension, has_dialogue=has_dialogue,
            ):
                rejected.append({"beat_id": beat["beat_id"], "reason": "preconditions"})
                continue
            target_fit = 1.0 - abs(float(beat["tension_before"]) - tension)
            stable_jitter = int(self._digest([campaign_id, turn_id, beat["beat_id"]])[:8], 16) / 0xFFFFFFFF
            score = (
                float(beat["saliency"]) * 0.50
                + float(beat["urgency"]) * 0.20
                + float(beat["relationship_pressure"]) * 0.10
                + target_fit * 0.10
                + (0.08 if major_consequence else 0.0)
                + stable_jitter * 0.02
            )
            breakdown = {
                "saliency": round(float(beat["saliency"]) * 0.50, 6),
                "urgency": round(float(beat["urgency"]) * 0.20, 6),
                "relationship_pressure": round(float(beat["relationship_pressure"]) * 0.10, 6),
                "tension_fit": round(target_fit * 0.10, 6),
                "major_bonus": 0.08 if major_consequence else 0.0,
                "stable_jitter": round(stable_jitter * 0.02, 6),
            }
            candidates.append((score, beat, breakdown))
        if candidates:
            candidates.sort(key=lambda item: (-item[0], item[1]["beat_id"]))
            score, beat, breakdown = candidates[0]
            selected = dict(beat)
            selected["source"] = "persistent_storylet"
            selected["selection_score"] = round(score, 6)
            selected["score_breakdown"] = breakdown
            selected["foreground_only"] = True
            selected["does_not_create_world_event"] = True
            return selected, rejected

        objectives = {
            "combat_beat": "Render the authoritative combat result clearly and quickly.",
            "dialogue_scene": "Foreground the NPC's immediate communicative objective and relationship pressure.",
            "scene_opening": "Establish the new place through selective concrete sensory detail and a live situation.",
            "major_consequence": "Make the authoritative consequence legible without inventing additional outcomes.",
            "action_result": "Render the outcome and preserve forward motion.",
            "setup_or_reveal": "Orient the player and expose only established facts.",
            "routine_adventure": "Render the current change with proportionate emphasis.",
        }
        default = {
            "beat_id": f"default:{scene_function}",
            "kind": scene_function,
            "preconditions": {},
            "involved_entities": [],
            "dramatic_objective": objectives.get(scene_function, objectives["routine_adventure"]),
            "information_to_reveal": [],
            "information_to_withhold": [],
            "relationship_pressure": 0.0,
            "tension_before": tension,
            "tension_target": min(1.0, tension + (0.20 if major_consequence else 0.05)),
            "urgency": 0.7 if scene_function == "combat_beat" else 0.3,
            "saliency": 0.5,
            "cooldown_turns": 0,
            "once": False,
            "repeat_policy": "semantic_variation_without_literal_cache",
            "quest_links": [],
            "motif_candidates": [],
            "resolution_state": "eligible",
            "source": "baseline_dynamic_policy",
            "selection_score": 0.5,
            "score_breakdown": {"default_policy": 0.5},
            "foreground_only": True,
            "does_not_create_world_event": True,
        }
        return default, rejected

    def _select_motif(
        self,
        campaign_id: str,
        *,
        scene_function: str,
        trigger_type: str | None,
        capability_ids: set[str],
        location_id: str | None,
        major_consequence: bool,
        has_dialogue: bool,
        involved_entities: set[str],
        turn_id: str,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        state = self.get_director_state(campaign_id)
        turn_index = int(state["turn_index"])
        tension = float(state["tension"])
        candidates: list[tuple[float, dict[str, Any]]] = []
        rejected: list[dict[str, Any]] = []
        for motif in self.list_motifs(campaign_id, status="active", limit=500):
            if int(motif["use_count"]) >= int(motif["max_recurrences"]):
                rejected.append({"motif_id": motif["motif_id"], "reason": "max_recurrences"})
                continue
            last = motif.get("last_used_turn")
            if last is not None and turn_index - int(last) < int(motif["cooldown_turns"]):
                rejected.append({"motif_id": motif["motif_id"], "reason": "cooldown"})
                continue
            scene_types = {str(x) for x in motif.get("eligible_scene_types") or []}
            if scene_types and scene_function not in scene_types:
                rejected.append({"motif_id": motif["motif_id"], "reason": "scene_type"})
                continue
            if not self._conditions_match(
                motif["activation_conditions"], scene_function=scene_function, trigger_type=trigger_type,
                capability_ids=capability_ids, location_id=location_id,
                major_consequence=major_consequence, tension=tension, has_dialogue=has_dialogue,
            ):
                rejected.append({"motif_id": motif["motif_id"], "reason": "activation_conditions"})
                continue
            linked = {str(x) for x in motif.get("linked_entities") or []}
            entity_fit = 1.0 if not linked else (1.0 if linked.intersection(involved_entities) else 0.0)
            if linked and entity_fit == 0.0:
                rejected.append({"motif_id": motif["motif_id"], "reason": "linked_entity_absent"})
                continue
            priority = self._clamp((motif.get("metadata") or {}).get("priority", 0.5))
            stable_jitter = int(self._digest([campaign_id, turn_id, motif["motif_id"]])[:8], 16) / 0xFFFFFFFF
            score = priority * 0.55 + entity_fit * 0.25 + (0.15 if major_consequence else 0.0) + stable_jitter * 0.05
            candidates.append((score, motif))
        if not candidates:
            return None, rejected
        candidates.sort(key=lambda item: (-item[0], item[1]["motif_id"]))
        score, motif = candidates[0]
        return {
            "motif_id": motif["motif_id"],
            "symbol": motif["symbol"],
            "meaning": motif["meaning"],
            "linked_arc": motif["linked_arc"],
            "transformation_stage": motif["transformation_stage"],
            "selection_score": round(score, 6),
            "use_policy": "eligible_not_mandatory; subtle recurrence only when natural; never invent future facts",
            "backend_selected": True,
            "renderer_owns_surface_realization_only": True,
            "recent_realizations": list(motif["recent_realizations"])[-3:],
        }, rejected

    # ------------------------------------------------------------------
    # Dialogue planning
    # ------------------------------------------------------------------

    def _speaker_from_inputs(self, intents: Sequence[dict[str, Any]], hint: dict[str, Any]) -> str | None:
        speaker = hint.get("speaker_id") or hint.get("speaker")
        if isinstance(speaker, dict):
            speaker = speaker.get("id")
        if isinstance(speaker, str) and speaker.startswith("npc:"):
            speaker = speaker.split(":", 1)[1]
        if speaker:
            return self.e._clean_id(str(speaker))
        for intent in intents:
            params = dict(intent.get("parameters") or {})
            capability = str(intent.get("capability") or intent.get("capability_id") or "")
            intent_type = str(intent.get("type") or "")
            if capability == "npc.dialogue.context" or intent_type in {"interact", "talk", "dialogue"}:
                value = params.get("npc_id") or params.get("target_id")
                if value:
                    return self.e._clean_id(str(value))
        return None

    @staticmethod
    def _topic_from_inputs(intents: Sequence[dict[str, Any]], hint: dict[str, Any]) -> str:
        if hint.get("conversation_topic") or hint.get("topic"):
            return str(hint.get("conversation_topic") or hint.get("topic"))[:160]
        for intent in intents:
            params = dict(intent.get("parameters") or {})
            if params.get("topic"):
                return str(params["topic"])[:160]
        return "general"

    def _belief_snapshot(self, campaign_id: str, speaker_key: str, limit: int = 30) -> list[dict[str, Any]]:
        with self.e._db() as db:
            rows = db.execute(
                """SELECT b.fact_id,b.belief_value_json,b.confidence AS belief_confidence,b.status AS belief_status,
                          b.source_key,b.acquired_world_time,f.subject_key,f.predicate,f.object_type,f.status AS fact_status
                   FROM we4_beliefs b
                   JOIN we4_facts f ON f.campaign_id=b.campaign_id AND f.fact_id=b.fact_id
                   WHERE b.campaign_id=? AND b.believer_key=? AND b.status IN ('believes','doubts','rejects')
                   ORDER BY b.confidence DESC,b.updated_at DESC,b.fact_id LIMIT ?""",
                (campaign_id, speaker_key, max(1, min(int(limit), 100))),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["belief_value"] = self.e._loads(item.pop("belief_value_json"))
            out.append(item)
        return out

    @staticmethod
    def _extract_mood(cognition: dict[str, Any], npc: dict[str, Any], hint: dict[str, Any]) -> Any:
        if hint.get("emotion") is not None:
            return hint["emotion"]
        for path in (
            ("mood",), ("state", "mood"), ("current", "mood"), ("thoughts", "mood"),
        ):
            current: Any = cognition
            for key in path:
                if not isinstance(current, dict) or key not in current:
                    current = None
                    break
                current = current[key]
            if current not in (None, {}, []):
                return current
        return (npc.get("stats") or {}).get("mood") or "not explicitly recorded"

    def _build_dialogue_plan(
        self,
        campaign_id: str,
        *,
        actor_kind: str | None,
        actor_id: str | None,
        intents: Sequence[dict[str, Any]],
        hint: dict[str, Any],
        scene_function: str,
    ) -> dict[str, Any] | None:
        speaker_id = self._speaker_from_inputs(intents, hint)
        if not speaker_id:
            return None
        npc = self.e.get_npc_sheet(campaign_id, speaker_id)
        speaker_key = f"npc:{speaker_id}"
        listener_key = self._entity_key(actor_kind, actor_id) or str(hint.get("listener") or "player:unknown")
        if isinstance(listener_key, str) and ":" not in listener_key and listener_key != "player:unknown":
            listener_key = f"character:{listener_key}"
        topic = self._topic_from_inputs(intents, hint)
        state = self.get_dialogue_state(campaign_id, speaker_key, listener_key, topic)
        beliefs = self._belief_snapshot(campaign_id, speaker_key)
        known_ids = {x["fact_id"] for x in beliefs}
        requested_reveal = [str(x) for x in (hint.get("facts_to_reveal") or hint.get("fact_ids_to_reveal") or [])]
        accepted_reveal = [x for x in requested_reveal if x in known_ids]
        rejected_reveal = [x for x in requested_reveal if x not in known_ids]
        authorized_reveal_records = [x for x in beliefs if x["fact_id"] in set(accepted_reveal)]
        conceal = self._dedupe([
            *state["concealed_fact_ids"],
            *(hint.get("facts_to_conceal") or hint.get("facts_to_withhold") or []),
        ], limit=100)
        relationship = self.e.get_relationship(campaign_id, speaker_id, actor_id) if actor_id else {
            "trust": 0, "fear": 0, "respect": 0, "affection": 0, "notes": {},
        }
        cognition = npc.get("cognition") or {}
        goals = list(npc.get("goals") or [])
        dominant_motive = hint.get("dominant_motive")
        if dominant_motive is None:
            dominant = cognition.get("dominant_motives") if isinstance(cognition, dict) else None
            if isinstance(dominant, list) and dominant:
                dominant_motive = dominant[0]
            elif goals:
                dominant_motive = goals[0]
        speech_act = self._clean_text(hint.get("speech_act"), limit=80)
        if not speech_act:
            speech_act = "evade" if conceal else "respond"
        objective = self._clean_text(hint.get("objective"), limit=500)
        if not objective:
            objective = "Respond consistently with the NPC's current goals without exposing unapproved private cognition."
        subtext = hint.get("subtext")
        if subtext is None and conceal:
            subtext = "The speaker is protecting explicitly concealed information."
        voice_record = self.get_voice_profile(campaign_id, speaker_id)
        voice_profile = voice_record["profile"]
        voice_anchors = self._select_voice_examples(voice_profile, scene_function, topic)
        plan_core = {
            "speaker": speaker_key,
            "speaker_name": npc.get("name"),
            "listener": listener_key,
            "speech_act": speech_act,
            "objective": objective,
            "topic": topic,
            "facts_authorized_to_reveal": authorized_reveal_records,
            "known_fact_count": len(beliefs),
            "facts_to_reveal": accepted_reveal,
            "requested_but_unknown_fact_ids": rejected_reveal,
            "facts_to_conceal": conceal,
            "relationship": relationship,
            "status_difference": hint.get("status_difference"),
            "dominant_motive": hint.get("dominant_motive") if "dominant_motive" in hint else {
                "source": "private_cognition", "present": dominant_motive is not None,
            },
            "emotion": hint.get("emotion") if "emotion" in hint else "private_cognition_not_exposed",
            "subtext": subtext,
            "desired_effect": hint.get("desired_effect"),
            "interruptibility": hint.get("interruptibility", "normal"),
            "conversation_topic": topic,
            "already_communicated_fact_ids": state["communicated_fact_ids"],
            "recent_speech_acts": state["recent_speech_acts"],
            "recent_realization_fingerprints": state["recent_realizations"],
            "voice_profile": voice_profile,
            "voice_anchors": voice_anchors,
            "literal_line_cache": False,
            "repeat_policy": "cache semantic intent/facts/subtext/voice state; generate a fresh realization unless fixed_line is explicitly authored",
            "knowledge_boundary": "The NPC may assert its beliefs, including false beliefs, but may not know unacquired canonical facts.",
            "private_cognition_redacted": True,
        }
        plan_core["semantic_cache_key"] = self._digest(plan_core)[:24]
        return plan_core

    def plan_dialogue(
        self,
        campaign_id: str,
        speaker_id: str,
        *,
        listener_kind: str | None = None,
        listener_id: str | None = None,
        topic: str = "general",
        hint: dict[str, Any] | None = None,
        scene_function: str = "dialogue_scene",
    ) -> dict[str, Any]:
        dialogue_hint = dict(hint or {})
        dialogue_hint.setdefault("speaker_id", speaker_id)
        dialogue_hint.setdefault("topic", topic)
        result = self._build_dialogue_plan(
            campaign_id, actor_kind=listener_kind, actor_id=listener_id,
            intents=[{"type": "dialogue", "parameters": {"npc_id": speaker_id, "topic": topic}}],
            hint=dialogue_hint, scene_function=scene_function,
        )
        if result is None:
            raise ValueError("dialogue plan requires a valid NPC speaker")
        return result

    # ------------------------------------------------------------------
    # Typed cutscene packet
    # ------------------------------------------------------------------

    def validate_cutscene_packet(self, campaign_id: str, packet: dict[str, Any]) -> CutscenePacket:
        self.e._ensure_campaign_exists(campaign_id)
        if not isinstance(packet, dict):
            raise ValueError("cutscene_packet must be an object")
        scene_goal = self._clean_text(packet.get("scene_goal"), limit=700)
        if not scene_goal:
            raise ValueError("cutscene_packet.scene_goal is required")
        location = self._clean_text(packet.get("location") or packet.get("location_id"), limit=160)
        participants = self._dedupe(
            [self._clean_text(x, limit=160) for x in (packet.get("participants") or []) if self._clean_text(x, limit=160)],
            limit=40,
        )
        raw_beats = packet.get("beats") or []
        if not isinstance(raw_beats, list) or not 1 <= len(raw_beats) <= 50:
            raise ValueError("cutscene_packet.beats must contain 1-50 ordered beats")
        beats: list[dict[str, Any]] = []
        for index, beat in enumerate(raw_beats, 1):
            if isinstance(beat, str):
                objective = self._clean_text(beat, limit=700)
                item = {"index": index, "objective": objective}
            elif isinstance(beat, dict):
                item = self._bounded(dict(beat), 1600)
                item["index"] = index
                if not self._clean_text(item.get("objective") or item.get("description"), limit=700):
                    raise ValueError(f"cutscene beat {index} requires objective or description")
            else:
                raise ValueError("each cutscene beat must be text or an object")
            beats.append(item)
        physical_actions: list[dict[str, Any]] = []
        for raw in packet.get("physical_actions") or []:
            if not isinstance(raw, dict):
                raise ValueError("physical_actions entries must be objects")
            action = self._bounded(dict(raw), 1000)
            actor = str(action.get("actor") or action.get("actor_id") or "")
            if actor.startswith(("player:", "character:")):
                authority = str(action.get("authority") or "")
                if authority not in {"player_supplied", "authoritative_result", "mechanically_forced"}:
                    raise ValueError("player-character cutscene actions require player_supplied, authoritative_result, or mechanically_forced authority")
            physical_actions.append(action)
        choices: list[dict[str, Any]] = []
        for index, raw in enumerate(packet.get("choices") or [], 1):
            if isinstance(raw, str):
                choices.append({"choice_id": f"choice_{index}", "label": self._clean_text(raw, limit=300)})
            elif isinstance(raw, dict):
                item = self._bounded(dict(raw), 900)
                item.setdefault("choice_id", f"choice_{index}")
                if not self._clean_text(item.get("label") or item.get("text"), limit=300):
                    raise ValueError(f"cutscene choice {index} requires label or text")
                choices.append(item)
            else:
                raise ValueError("cutscene choices must be text or objects")
        cutscene_id = self._clean_text(packet.get("cutscene_id"), limit=100)
        normalized: CutscenePacket = {
            "cutscene_version": "CUT-1.0",
            "cutscene_id": cutscene_id or f"cut_{self._digest(packet)[:16]}",
            "scene_goal": scene_goal,
            "location": location,
            "world_time": self._clean_text(packet.get("world_time"), limit=100),
            "participants": participants,
            "opening_image": self._clean_text(packet.get("opening_image"), limit=700),
            "visual_focus": self._dedupe(packet.get("visual_focus") or [], limit=20),
            "sound": self._dedupe(packet.get("sound") or [], limit=20),
            "music": self._bounded(dict(packet.get("music") or {}), 1000),
            "beats": beats,
            "dialogue_intents": [self._bounded(dict(x), 1200) for x in (packet.get("dialogue_intents") or []) if isinstance(x, dict)][:50],
            "reveals": self._dedupe(packet.get("reveals") or [], limit=50),
            "physical_actions": physical_actions[:50],
            "emotional_state": self._bounded(dict(packet.get("emotional_state") or {}), 1600),
            "motifs": self._dedupe(packet.get("motifs") or [], limit=20),
            "choices": choices[:20],
            "conditions": [self._bounded(dict(x), 1000) for x in (packet.get("conditions") or []) if isinstance(x, dict)][:30],
            "ending_state": self._bounded(dict(packet.get("ending_state") or {}), 1600),
        }
        normalized["hidden_structure"] = True
        normalized["authority_note"] = "Structure is renderer-private; actions/reveals/ending state must originate from authoritative or explicitly authored inputs."
        return normalized

    # ------------------------------------------------------------------
    # Render-packet compiler
    # ------------------------------------------------------------------

    @staticmethod
    def _scene_function(task: str, trigger_type: str | None, major_consequence: bool, hint: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        explicit = hint.get("scene_function")
        policy = narrative_policy(task=task, trigger_type=trigger_type, major_consequence=major_consequence)
        return (str(explicit)[:80] if explicit else str(policy["response_kind"]), policy)

    @staticmethod
    def _capability_ids(turn_result: dict[str, Any]) -> set[str]:
        result = set()
        for item in turn_result.get("capability_plan") or []:
            if isinstance(item, dict) and item.get("capability_id"):
                result.add(str(item["capability_id"]))
        for item in turn_result.get("steps") or []:
            if isinstance(item, dict) and item.get("capability_id"):
                result.add(str(item["capability_id"]))
        return result

    def _authoritative_result_projection(self, turn_result: dict[str, Any]) -> dict[str, Any]:
        steps = []
        for step in turn_result.get("steps") or []:
            if not isinstance(step, dict):
                continue
            item = {
                "intent_id": step.get("intent_id"),
                "capability_id": step.get("capability_id"),
                "status": step.get("status"),
                "revision_delta": step.get("revision_delta", 0),
            }
            if step.get("status") == "completed":
                item["result"] = self._bounded(step.get("result"), 2600)
            elif step.get("error"):
                item["error"] = self._bounded(step.get("error"), 500)
            steps.append(item)
        return {
            "status": turn_result.get("status"),
            "revision_before": turn_result.get("revision_before"),
            "revision_after": turn_result.get("revision_after"),
            "revision_delta": turn_result.get("revision_delta"),
            "completed_intents": turn_result.get("completed_intents") or [],
            "failed_intents": turn_result.get("failed_intents") or [],
            "steps": steps,
        }

    def _involved_entities(
        self,
        actor_kind: str | None,
        actor_id: str | None,
        intents: Sequence[dict[str, Any]],
        beat: dict[str, Any],
    ) -> set[str]:
        entities = {str(x) for x in (beat.get("involved_entities") or [])}
        actor_key = self._entity_key(actor_kind, actor_id)
        if actor_key:
            entities.add(actor_key)
        for intent in intents:
            params = dict(intent.get("parameters") or {})
            for kind_key, id_key in (
                ("target_kind", "target_id"), ("attacker_kind", "attacker_id"),
                ("actor_kind", "actor_id"), ("owner_kind", "owner_id"),
            ):
                if params.get(kind_key) and params.get(id_key):
                    entities.add(f"{params[kind_key]}:{params[id_key]}")
            if params.get("npc_id"):
                entities.add(f"npc:{params['npc_id']}")
            if params.get("faction_id"):
                entities.add(f"faction:{params['faction_id']}")
            if params.get("quest_id"):
                entities.add(f"quest:{params['quest_id']}")
            if params.get("location_id"):
                entities.add(f"location:{params['location_id']}")
        return entities

    def build_packet(
        self,
        campaign_id: str,
        *,
        turn_result: dict[str, Any],
        task: str = "routine",
        trigger_type: str | None = None,
        actor_kind: str | None = None,
        actor_id: str | None = None,
        intents: Sequence[dict[str, Any]] = (),
        raw_player_text: str = "",
        choice_options: Sequence[str] = (),
        major_consequence: bool = False,
        location_id: str | None = None,
        narrative_hint: dict[str, Any] | None = None,
        mode_override: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        config = self.get_config(campaign_id)
        mode = str(mode_override or config["mode"]).strip().lower()
        if mode not in NARRATIVE_MODES:
            raise ValueError(f"narrative mode must be one of {sorted(NARRATIVE_MODES)}")
        if mode == "off":
            return {
                "packet_version": self.PACKET_VERSION,
                "engine_version": self.VERSION,
                "enabled": False,
                "mode": "off",
                "activation": {"baseline_policy_remains_authoritative": True, "packet_generated": False},
            }
        hint = dict(narrative_hint or {})
        cutscene_packet = None
        if hint.get("cutscene_packet") is not None:
            cutscene_packet = self.validate_cutscene_packet(campaign_id, dict(hint["cutscene_packet"]))
            hint["scene_function"] = "cutscene"
        scene_function, baseline_policy = self._scene_function(task, trigger_type, major_consequence, hint)
        capability_ids = self._capability_ids(turn_result)
        turn_id = str(turn_result.get("turn_id") or f"manual_{self._digest([campaign_id, turn_result])[:16]}")
        has_dialogue = "npc.dialogue.context" in capability_ids or self._speaker_from_inputs(intents, hint) is not None
        selected_beat, rejected_beats = self._select_beat(
            campaign_id, scene_function=scene_function, trigger_type=trigger_type,
            capability_ids=capability_ids, location_id=location_id,
            major_consequence=major_consequence, has_dialogue=has_dialogue, turn_id=turn_id,
        )
        involved_entities = self._involved_entities(actor_kind, actor_id, intents, selected_beat)
        if cutscene_packet:
            involved_entities.update(str(x) for x in cutscene_packet.get("participants") or [])
        dialogue_plan = self._build_dialogue_plan(
            campaign_id, actor_kind=actor_kind, actor_id=actor_id, intents=intents,
            hint=hint, scene_function=scene_function,
        )
        motif, rejected_motifs = self._select_motif(
            campaign_id, scene_function=scene_function, trigger_type=trigger_type,
            capability_ids=capability_ids, location_id=location_id,
            major_consequence=major_consequence, has_dialogue=has_dialogue,
            involved_entities=involved_entities, turn_id=turn_id,
        )
        campaign = self.e.get_campaign(campaign_id)
        authorized_facts = self._dedupe(hint.get("authorized_facts") or [], limit=100)
        to_reveal = self._dedupe([
            *selected_beat.get("information_to_reveal", []),
            *authorized_facts,
        ], limit=150)
        to_withhold = self._dedupe([
            *selected_beat.get("information_to_withhold", []),
            *(hint.get("information_to_withhold") or hint.get("forbidden_facts") or []),
            *((dialogue_plan or {}).get("facts_to_conceal") or []),
        ], limit=150)
        mechanically_supported_effects = self._dedupe(hint.get("mechanically_supported_player_effects") or [], limit=30)
        target_words = dict(baseline_policy["target_words"])
        major_scene_functions = set(config["generation_policy"].get("major_scene_functions") or [])
        is_major_scene = bool(major_consequence or scene_function in major_scene_functions)
        generation_plan = dict(config["generation_policy"])
        generation_plan["selected_path"] = generation_plan["major_scene"] if is_major_scene else generation_plan["ordinary"]
        generation_plan["candidate_count"] = generation_plan["major_scene_candidate_count"] if is_major_scene else 1
        activation = {
            "mode": mode,
            "baseline_policy_remains_default": mode in {"shadow", "compare"},
            "candidate_packet_player_facing": mode == "enforce",
            "consume_beat_or_motif_on_compile": False,
            "consume_only_after_accepted_output": True,
        }
        packet: dict[str, Any] = {
            "packet_version": self.PACKET_VERSION,
            "engine_version": self.VERSION,
            "enabled": True,
            "campaign_id": campaign_id,
            "turn_id": turn_id,
            "mode": mode,
            "activation": activation,
            "authority": {
                "owner": "world_engine_backend",
                "authoritative_state": {
                    "campaign": {
                        "world_time": campaign.get("world_time"),
                        "weather": campaign.get("weather"),
                        "revision": campaign.get("revision"),
                    },
                    "turn_result": self._authoritative_result_projection(turn_result),
                    "authorized_facts": to_reveal,
                },
                "model_owns": ["wording", "sentence rhythm", "paragraphing", "surface dialogue", "authorized sensory realization"],
                "model_must_not_change": [
                    "mechanical results", "world facts", "secret visibility", "NPC knowledge",
                    "relationship state", "world time", "player speech", "player decisions", "player private thoughts",
                ],
                "director_scope": "foregrounding only; it does not decide what mechanically happened",
            },
            "scene": {
                "scene_function": scene_function,
                "task": task,
                "trigger_type": trigger_type,
                "location_id": location_id,
                "major_consequence": bool(major_consequence),
                "target_words": target_words,
                "choice_options": [str(x) for x in choice_options][:12],
                "raw_player_text": self._clean_text(raw_player_text, limit=20000),
            },
            "narrative_director": {
                "selected_beat": selected_beat,
                "selection_method": "eligibility + deterministic utility/saliency + stable hash jitter",
                "rejected_candidate_count": len(rejected_beats),
            },
            "dialogue_plan": dialogue_plan,
            "style_profile": config["style_profile"],
            "motif_thread": motif,
            "cutscene_packet": cutscene_packet,
            "render_contract": {
                "prose": baseline_policy,
                "must": [
                    "Render only completed authoritative results.",
                    "Preserve speaker knowledge and false-belief boundaries.",
                    "Use selective concrete sensory detail and natural paragraphs.",
                    "Use a fresh surface realization rather than literal dialogue caching.",
                ],
                "must_not": [
                    "Expose internal tags, context packets, revisions, capability IDs, or audit markers.",
                    "Invent player speech, voluntary action, beliefs, feelings, decisions, or private thoughts.",
                    "Invent facts, consequences, secrets, memories, or future events.",
                    "Force a motif or metaphor where it is not natural.",
                ],
                "ending": baseline_policy["ending"],
                "player_agency": {
                    "interiority_policy": config["style_profile"].get("interiority_policy", "player_locked"),
                    "allowed": ["sensory perception", "environmental implication", "authoritative forced movement", "mechanically supported involuntary physical effect"],
                    "mechanically_supported_effects": mechanically_supported_effects,
                    "forbidden": ["invented dialogue", "invented decision", "invented private thought", "invented emotional conclusion"],
                },
            },
            "quality_contract": {
                "target_hard_failures": 0,
                "hard_checks": [
                    "mechanics_or_debug_leakage", "player_agency", "withheld_information",
                    "gross_word_budget", "POV_contract",
                ],
                "semantic_checks_requiring_authoritative_or_human_review": [
                    "unauthorized_fact_generation", "wrong speaker knowledge", "wrong relationship state",
                    "wrong name/title", "subtle secret leakage",
                ],
                "soft_checks": [
                    "near_duplicate_output", "repeated_sentence_openings", "fourgram_repetition",
                    "cliche_density", "you_see_notice_repetition", "catchphrase_overuse", "motif_overuse",
                ],
                "config": config["quality_config"],
            },
            "generation_plan": generation_plan,
            "field_authority": {
                "authoritative": ["authority.authoritative_state", "scene.raw_player_text", "scene.choice_options"],
                "derived": ["narrative_director", "dialogue_plan", "style_profile", "motif_thread", "cutscene_packet", "render_contract"],
                "model_authored": ["final_player_facing_prose_only"],
                "temporary": ["this_packet", "quality_receipt"],
                "player_visible": ["accepted_final_prose"],
                "debug_only": ["selection_scores", "rejected_candidate_counts", "digests", "quality_metrics"],
            },
        }
        forbidden_phrases = self._forbidden_phrases(to_withhold)
        packet = self._public_packet_value(packet, forbidden_phrases)
        digest = self._digest(packet)
        packet_id = f"nrp_{re.sub(r'[^A-Za-z0-9_.:-]+', '_', turn_id)[:50]}_{digest[:16]}"
        packet["packet_id"] = packet_id
        packet["digest"] = digest
        packet["packet_hash"] = digest
        validation_context = {
            "validation_context_version": "NVC-1.0",
            "packet_id": packet_id,
            "packet_digest": digest,
            "forbidden_literals": forbidden_phrases,
        }
        validation_context_digest = self._digest(validation_context)
        if persist:
            with self.e._write_db() as db:
                db.execute(
                    """INSERT INTO we4_narrative_packets(
                           campaign_id,packet_id,turn_id,mode,packet_version,packet_json,digest,created_at)
                       VALUES(?,?,?,?,?,?,?,?)
                        ON CONFLICT(campaign_id,packet_id) DO NOTHING""",
                    (campaign_id, packet_id, turn_id, mode, self.PACKET_VERSION, self.e._dumps(packet), digest, self.e._now()),
                )
                db.execute(
                    """INSERT INTO we43_narrative_validation_contexts(
                           campaign_id,packet_id,packet_digest,validation_context_json,context_digest,created_at)
                       VALUES(?,?,?,?,?,?)
                       ON CONFLICT(campaign_id,packet_id) DO NOTHING""",
                    (
                        campaign_id, packet_id, digest, self.e._dumps(validation_context),
                        validation_context_digest, self.e._now(),
                    ),
                )
                stored = db.execute(
                    """SELECT p.packet_json,p.digest,v.packet_digest,v.validation_context_json,v.context_digest
                       FROM we4_narrative_packets p
                       LEFT JOIN we43_narrative_validation_contexts v
                         ON v.campaign_id=p.campaign_id AND v.packet_id=p.packet_id
                       WHERE p.campaign_id=? AND p.packet_id=?""",
                    (campaign_id, packet_id),
                ).fetchone()
                if (
                    not stored
                    or stored["digest"] != digest
                    or self._canonical(self.e._loads(stored["packet_json"])) != self._canonical(packet)
                    or stored["packet_digest"] != digest
                    or stored["context_digest"] != validation_context_digest
                    or self._canonical(self.e._loads(stored["validation_context_json"])) != self._canonical(validation_context)
                ):
                    raise ValueError("NARRATIVE_PACKET_IMMUTABILITY_CONFLICT")
        return packet

    def _verify_packet_hash(self, packet: dict[str, Any]) -> bool:
        version = str(packet.get("packet_version") or "")
        claimed_digest = str(packet.get("digest") or "")
        claimed_hash = str(packet.get("packet_hash") or claimed_digest)
        if version not in {"NRP-1.0", "NRP-1.1", self.PACKET_VERSION} or not claimed_digest or not claimed_hash:
            return False
        core = dict(packet)
        core.pop("packet_id", None)
        core.pop("digest", None)
        core.pop("packet_hash", None)
        actual = self._digest(core)
        return claimed_digest == actual and claimed_hash == actual

    def _load_packet_record(self, campaign_id: str, packet_id: str) -> dict[str, Any]:
        clean_packet_id = self.e._clean_id(packet_id)
        with self.e._db() as db:
            row = db.execute(
                """SELECT p.packet_json,p.digest AS stored_packet_digest,
                          v.packet_digest AS context_packet_digest,
                          v.validation_context_json,v.context_digest
                   FROM we4_narrative_packets p
                   LEFT JOIN we43_narrative_validation_contexts v
                     ON v.campaign_id=p.campaign_id AND v.packet_id=p.packet_id
                   WHERE p.campaign_id=? AND p.packet_id=?""",
                (campaign_id, clean_packet_id),
            ).fetchone()
        if not row:
            raise KeyError(f"unknown narrative packet: {packet_id}")
        validation_context = None
        if row["validation_context_json"] is not None:
            validation_context = self.e._loads(row["validation_context_json"])
        return {
            "packet": self.e._loads(row["packet_json"]),
            "stored_packet_digest": row["stored_packet_digest"],
            "context_packet_digest": row["context_packet_digest"],
            "validation_context": validation_context,
            "context_digest": row["context_digest"],
        }

    def get_packet(self, campaign_id: str, packet_id: str) -> dict[str, Any]:
        return self._load_packet_record(campaign_id, packet_id)["packet"]

    def list_packets(self, campaign_id: str, *, turn_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT packet_json FROM we4_narrative_packets WHERE campaign_id=?"
        params: list[Any] = [campaign_id]
        if turn_id is not None:
            sql += " AND turn_id=?"
            params.append(str(turn_id))
        sql += " ORDER BY created_at DESC,packet_id LIMIT ?"
        params.append(max(1, min(int(limit), 200)))
        with self.e._db() as db:
            return [self.e._loads(r["packet_json"]) for r in db.execute(sql, params).fetchall()]

    # ------------------------------------------------------------------
    # Deterministic/local prose quality gate
    # ------------------------------------------------------------------

    @staticmethod
    def _words(text: str) -> list[str]:
        return re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9']+", text)

    @staticmethod
    def _sentences(text: str) -> list[str]:
        return [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n{2,}", text.strip()) if x.strip()]

    @staticmethod
    def _paragraphs(text: str) -> list[str]:
        return [x.strip() for x in re.split(r"\n\s*\n", text.strip()) if x.strip()]

    @staticmethod
    def _strip_dialogue(text: str) -> str:
        text = re.sub(r'"[^"\n]*"', ' ', text)
        text = re.sub(r'“[^”\n]*”', ' ', text)
        text = re.sub(r"'[^'\n]*'", " ", text)
        return text

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        try:
            from rapidfuzz.fuzz import ratio  # type: ignore
            return float(ratio(a, b)) / 100.0
        except Exception:
            return SequenceMatcher(None, a, b).ratio()

    @classmethod
    def _shingle_similarity(cls, a: str, b: str, *, width: int = 5) -> float:
        """Return containment similarity for long prose, insensitive to moved blocks."""
        a_words = [word.lower() for word in cls._words(a)]
        b_words = [word.lower() for word in cls._words(b)]
        width = max(3, int(width))
        if len(a_words) < width or len(b_words) < width:
            return 0.0
        a_shingles = {tuple(a_words[i:i + width]) for i in range(len(a_words) - width + 1)}
        b_shingles = {tuple(b_words[i:i + width]) for i in range(len(b_words) - width + 1)}
        if not a_shingles or not b_shingles:
            return 0.0
        return len(a_shingles.intersection(b_shingles)) / min(len(a_shingles), len(b_shingles))

    @classmethod
    def _tense_metrics(cls, text: str, expected: str) -> dict[str, Any]:
        narration = cls._strip_dialogue(text)
        past_pattern = re.compile(
            r"\b(?:was|were|had|did|went|came|stood|sat|said|asked|looked|walked|turned|"
            r"opened|closed|moved|seemed|felt|saw|heard|found|knew|could|would|[a-z]{4,}ed)\b",
            re.I,
        )
        present_pattern = re.compile(
            r"\b(?:is|are|has|does|goes|comes|stands|sits|says|asks|looks|walks|turns|"
            r"opens|closes|moves|seems|feels|sees|hears|finds|knows|can|will)\b",
            re.I,
        )
        past = len(past_pattern.findall(narration))
        present = len(present_pattern.findall(narration))
        expected = str(expected or "").lower()
        if expected == "present":
            violating = past >= 3 and past >= present + 2
        elif expected == "past":
            violating = present >= 3 and present >= past + 2
        else:
            violating = False
        return {
            "expected": expected,
            "past_markers": past,
            "present_markers": present,
            "violating": violating,
        }

    def _recent_outputs(self, campaign_id: str, limit: int) -> list[dict[str, Any]]:
        with self.e._db() as db:
            rows = db.execute(
                """SELECT output_id,packet_id,output_text,output_hash,created_at
                   FROM we4_narrative_outputs
                   WHERE campaign_id=? AND accepted=1
                   ORDER BY created_at DESC,output_id DESC LIMIT ?""",
                (campaign_id, max(1, min(int(limit), 100))),
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _allowed_agency_exception(code: str, match_text: str, packet: dict[str, Any]) -> bool:
        effects = [str(x).lower() for x in (((packet.get("render_contract") or {}).get("player_agency") or {}).get("mechanically_supported_effects") or [])]
        lower = match_text.lower()
        if code == "invented_emotional_conclusion":
            if any(effect in lower for effect in effects):
                return True
            if "frightened" in effects and re.search(r"\byou (?:are|feel) (?:afraid|fearful)\b", lower):
                return True
        return False

    def quality_check(
        self,
        campaign_id: str,
        output_text: str,
        *,
        packet_id: str | None = None,
        packet: dict[str, Any] | None = None,
        record: bool = True,
        publication_read_only: bool = False,
    ) -> dict[str, Any]:
        # Publication calls this only after a read-only packet/campaign
        # prevalidation.  Avoid the historical ensure-on-read write path for an
        # existing campaign so deterministic checks cannot mutate before their
        # verdict is known.
        with self.e._db() as db:
            campaign_exists = db.execute(
                "SELECT 1 FROM campaigns WHERE id=?", (campaign_id,)
            ).fetchone()
            config_row = db.execute(
                "SELECT * FROM we4_narrative_config WHERE campaign_id=?",
                (campaign_id,),
            ).fetchone()
        if not campaign_exists and publication_read_only:
            raise KeyError("CAMPAIGN_NOT_FOUND")
        if not campaign_exists:
            self.e._ensure_campaign_exists(campaign_id)
        text = str(output_text or "")
        if len(text) > 100_000:
            raise ValueError("output_text must be at most 100000 characters")
        output_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        packet_source_mismatch = False
        supplied_packet = dict(packet) if packet is not None else None
        selected_packet_id = packet_id or (supplied_packet or {}).get("packet_id")
        packet_record: dict[str, Any] | None = None
        if selected_packet_id:
            packet_record = self._load_packet_record(campaign_id, str(selected_packet_id))
            stored_packet = packet_record["packet"]
            if supplied_packet is not None and self._canonical(supplied_packet) != self._canonical(stored_packet):
                packet_source_mismatch = True
            packet = stored_packet
            packet_id = str(selected_packet_id)
        packet = dict(packet or {})
        if packet_id is None:
            packet_id = packet.get("packet_id")
        if config_row is None and publication_read_only:
            raise ValueError("NARRATIVE_CONFIG_MISSING")
        if config_row is None:
            config = self._validate_quality_config(
                self.get_config(campaign_id)["quality_config"]
            )
        else:
            config = self._validate_quality_config(
                self.e._loads(config_row["quality_config_json"])
            )
        hard_failures: list[dict[str, Any]] = []
        soft_warnings: list[dict[str, Any]] = []
        metrics: dict[str, Any] = {}

        if not text.strip():
            hard_failures.append({"code": "empty_output", "evidence": "no player-facing prose supplied"})
        if packet_source_mismatch:
            hard_failures.append({
                "code": "packet_source_mismatch",
                "evidence": "caller-supplied packet differs from the stored packet selected by packet_id",
            })
        if packet.get("enabled") and not self._verify_packet_hash(packet):
            hard_failures.append({
                "code": "packet_integrity",
                "evidence": "narrative packet hash does not match its canonical content",
            })

        validation_context: dict[str, Any] | None = None
        if packet.get("enabled") and packet.get("packet_version") == self.PACKET_VERSION:
            if packet_record is None:
                hard_failures.append({
                    "code": "validation_context_missing",
                    "evidence": "NRP-1.2 validation requires a stored packet selected by packet_id",
                })
            else:
                candidate_context = packet_record.get("validation_context")
                context_digest = packet_record.get("context_digest")
                if not isinstance(candidate_context, dict) or not context_digest:
                    hard_failures.append({
                        "code": "validation_context_missing",
                        "evidence": "stored NRP-1.2 packet has no private validation context",
                    })
                elif self._digest(candidate_context) != context_digest:
                    hard_failures.append({
                        "code": "validation_context_integrity",
                        "evidence": "stored private validation context digest mismatch",
                    })
                elif (
                    candidate_context.get("packet_id") != packet_id
                    or candidate_context.get("packet_digest") != packet.get("digest")
                    or packet_record.get("stored_packet_digest") != packet.get("digest")
                    or packet_record.get("context_packet_digest") != packet.get("digest")
                ):
                    hard_failures.append({
                        "code": "validation_context_binding",
                        "evidence": "stored private validation context is not bound to this packet digest",
                    })
                else:
                    validation_context = candidate_context

        for code, pattern in MECHANICS_LEAK_PATTERNS:
            hits = [m.group(0)[:160] for m in pattern.finditer(text)][:20]
            if hits:
                hard_failures.append({"code": f"mechanics_leak:{code}", "evidence": hits})

        for code, pattern in PLAYER_AGENCY_PATTERNS:
            hits = []
            for match in pattern.finditer(text):
                snippet = match.group(0)
                if not self._allowed_agency_exception(code, snippet, packet):
                    hits.append(snippet[:160])
            if hits:
                hard_failures.append({"code": f"player_agency:{code}", "evidence": hits[:20]})

        if packet.get("packet_version") == self.PACKET_VERSION:
            withheld = (validation_context or {}).get("forbidden_literals") or []
        else:
            # Compatibility only for immutable historical NRP-1.0/1.1 rows.
            withheld = (((packet.get("authority") or {}).get("authoritative_state") or {}).get("information_to_withhold") or [])
        withheld_hits = []
        lower_text = text.lower()
        for item in withheld:
            if isinstance(item, str):
                phrase = item.strip()
            elif isinstance(item, dict):
                phrase = str(item.get("text") or item.get("value") or "").strip()
            else:
                phrase = ""
            if len(phrase) >= 4 and phrase.lower() in lower_text:
                withheld_hits.append(phrase[:200])
        if withheld_hits:
            hard_failures.append({
                "code": "withheld_information_leak",
                "evidence": {
                    "count": len(withheld_hits),
                    "match_digests": [hashlib.sha256(x.encode("utf-8")).hexdigest()[:16] for x in withheld_hits[:20]],
                },
            })

        words = self._words(text)
        sentences = self._sentences(text)
        paragraphs = self._paragraphs(text)
        sentence_lengths = [len(self._words(x)) for x in sentences]
        paragraph_lengths = [len(self._words(x)) for x in paragraphs]
        metrics["word_count"] = len(words)
        metrics["character_count"] = len(text)
        metrics["sentence_count"] = len(sentences)
        metrics["paragraph_count"] = len(paragraphs)
        metrics["sentence_words"] = {
            "mean": round(mean(sentence_lengths), 3) if sentence_lengths else 0,
            "variance": round(pvariance(sentence_lengths), 3) if len(sentence_lengths) > 1 else 0,
            "min": min(sentence_lengths) if sentence_lengths else 0,
            "max": max(sentence_lengths) if sentence_lengths else 0,
        }
        metrics["paragraph_words"] = {
            "mean": round(mean(paragraph_lengths), 3) if paragraph_lengths else 0,
            "variance": round(pvariance(paragraph_lengths), 3) if len(paragraph_lengths) > 1 else 0,
        }

        target = ((packet.get("scene") or {}).get("target_words") or {})
        if target:
            low = max(0, int(target.get("min", 0)))
            high = max(low, int(target.get("max", 0)))
            metrics["target_words"] = {"min": low, "max": high}
            if high and len(words) > high * float(config["hard_max_word_multiplier"]):
                hard_failures.append({"code": "gross_word_budget_overrun", "evidence": {"words": len(words), "hard_max": math.floor(high * float(config["hard_max_word_multiplier"]))}})
            if low and len(words) < low * float(config["hard_min_word_multiplier"]):
                hard_failures.append({"code": "gross_word_budget_underrun", "evidence": {"words": len(words), "hard_min": math.floor(low * float(config["hard_min_word_multiplier"]))}})
            elif (low and len(words) < low) or (high and len(words) > high):
                soft_warnings.append({"code": "word_budget_deviation", "evidence": {"words": len(words), "target": [low, high]}})

        style = packet.get("style_profile") or {}
        narration_only = self._strip_dialogue(text)
        if style.get("pov") == "second_person":
            first_person_hits = re.findall(r"\b(?:I|me|my|mine|we|our|ours)\b", narration_only, flags=re.I)
            metrics["first_person_narration_pronouns"] = len(first_person_hits)
            if len(first_person_hits) >= 3:
                hard_failures.append({"code": "pov_contract_violation", "evidence": first_person_hits[:20]})
        if style.get("tense") in TENSE_VALUES:
            tense_metrics = self._tense_metrics(text, str(style["tense"]))
            metrics["tense_contract"] = tense_metrics
            if tense_metrics["violating"]:
                soft_warnings.append({"code": "tense_contract_violation", "evidence": tense_metrics})

        lower_words = [x.lower() for x in words]
        fourgrams = [" ".join(lower_words[i:i + 4]) for i in range(max(0, len(lower_words) - 3))]
        fourgram_counts = Counter(fourgrams)
        repeated_fourgrams = {gram: count for gram, count in fourgram_counts.items() if count > 1}
        repeated_fourgram_excess = sum(count - 1 for count in repeated_fourgrams.values())
        metrics["repeated_fourgrams"] = repeated_fourgram_excess
        metrics["top_repeated_fourgrams"] = sorted(repeated_fourgrams.items(), key=lambda x: (-x[1], x[0]))[:10]
        if repeated_fourgram_excess > int(config["max_repeated_fourgrams"]):
            soft_warnings.append({"code": "fourgram_repetition", "evidence": metrics["top_repeated_fourgrams"]})

        openings = []
        for sentence in sentences:
            sentence_words = self._words(sentence)
            if sentence_words:
                openings.append(" ".join(x.lower() for x in sentence_words[:3]))
        opening_counts = Counter(openings)
        repeated_openings = {k: v for k, v in opening_counts.items() if v > 1}
        opening_excess = sum(v - 1 for v in repeated_openings.values())
        metrics["repeated_sentence_openings"] = opening_excess
        if opening_excess > int(config["max_repeated_openings"]):
            soft_warnings.append({"code": "repeated_sentence_openings", "evidence": sorted(repeated_openings.items(), key=lambda x: (-x[1], x[0]))[:10]})

        you_see_notice = len(re.findall(r"\byou\s+(?:see|notice|observe)\b", text, re.I))
        metrics["you_see_notice_count"] = you_see_notice
        if you_see_notice > int(config["max_you_see_notice"]):
            soft_warnings.append({"code": "you_see_notice_repetition", "evidence": you_see_notice})

        cliche_hits = [phrase for phrase in Cliche_PATTERNS if phrase in lower_text]
        metrics["cliche_hits"] = cliche_hits
        if len(cliche_hits) > int(config["max_cliche_hits"]):
            soft_warnings.append({"code": "cliche_density", "evidence": cliche_hits})

        adverbs = [w for w in lower_words if len(w) > 4 and w.endswith("ly")]
        metrics["adverb_density"] = round(len(adverbs) / max(1, len(words)), 5)
        metrics["adjective_stack_heuristic"] = len(re.findall(r"\b\w+,\s+\w+,\s+(?:and\s+)?\w+\s+\w+", text))
        quoted_words = self._words(" ".join(re.findall(r'["“]([^"”\n]+)["”]', text)))
        metrics["dialogue_ratio"] = round(len(quoted_words) / max(1, len(words)), 5)

        recent = self._recent_outputs(campaign_id, int(config["recent_output_window"]))
        similarities = []
        for item in recent:
            if str(item.get("output_hash") or "") == output_hash:
                continue
            prior_text = str(item["output_text"])
            sequence_similarity = self._similarity(text, prior_text)
            shingle_similarity = 0.0
            if min(len(words), len(self._words(prior_text))) >= int(config["long_text_min_words"]):
                shingle_similarity = self._shingle_similarity(
                    text, prior_text, width=int(config["shingle_width"]),
                )
            similarities.append({
                "output_id": item["output_id"],
                "packet_id": item["packet_id"],
                "similarity": round(max(sequence_similarity, shingle_similarity), 6),
                "sequence_similarity": round(sequence_similarity, 6),
                "shingle_similarity": round(shingle_similarity, 6),
            })
        similarities.sort(key=lambda x: (-x["similarity"], x["output_id"]))
        metrics["recent_output_similarity"] = similarities[:5]
        duplicate = next((
            item for item in similarities
            if item["sequence_similarity"] >= float(config["near_duplicate_threshold"])
            or item["shingle_similarity"] >= float(config["long_text_shingle_threshold"])
        ), None)
        if duplicate:
            soft_warnings.append({"code": "near_duplicate_recent_output", "evidence": duplicate})

        voice = ((packet.get("dialogue_plan") or {}).get("voice_profile") or {})
        catchphrase_hits: list[dict[str, Any]] = []
        for phrase in voice.get("catchphrases") or []:
            count = lower_text.count(str(phrase).lower())
            if count:
                catchphrase_hits.append({"phrase": phrase, "count": count})
                if count > 1:
                    soft_warnings.append({"code": "catchphrase_overuse", "evidence": {"phrase": phrase, "count": count}})
        metrics["catchphrase_hits"] = catchphrase_hits

        motif = packet.get("motif_thread") or {}
        motif_hits = lower_text.count(str(motif.get("symbol") or "").lower()) if motif.get("symbol") else 0
        metrics["motif_symbol_hits"] = motif_hits
        if motif_hits > 2:
            soft_warnings.append({"code": "motif_overuse", "evidence": {"motif_id": motif.get("motif_id"), "hits": motif_hits}})

        severe_soft_codes = {
            "near_duplicate_recent_output", "fourgram_repetition", "catchphrase_overuse",
            "motif_overuse", "tense_contract_violation",
        }
        severe_soft = any(x["code"] in severe_soft_codes for x in soft_warnings)
        hard_pass = not hard_failures
        revision_required = bool(hard_failures or (severe_soft and config["revise_on_severe_soft_failure"]))
        receipt_base = {
            "receipt_version": self.RECEIPT_VERSION,
            "campaign_id": campaign_id,
            "packet_id": packet_id,
            "output_hash": output_hash,
            "hard_pass": hard_pass,
            "hard_failures": hard_failures,
            "soft_warnings": soft_warnings,
            "metrics": metrics,
            "revision_required": revision_required,
            "semantic_review": {
                "required": bool(config["semantic_authority_review_required"]),
                "not_claimed_deterministic": [
                    "full factual entailment", "subtle speaker-knowledge correctness", "literary quality",
                    "emotional credibility", "human preference",
                ],
            },
        }
        receipt_id = f"nqr_{self._digest(receipt_base)[:24]}"
        receipt = dict(receipt_base)
        receipt["receipt_id"] = receipt_id
        if record:
            with self.e._write_db() as db:
                db.execute(
                    """INSERT INTO we4_narrative_quality_receipts(
                           campaign_id,receipt_id,packet_id,output_hash,hard_pass,hard_failures_json,
                           soft_warnings_json,metrics_json,revision_required,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(campaign_id,receipt_id) DO UPDATE SET
                           packet_id=excluded.packet_id,hard_pass=excluded.hard_pass,
                           hard_failures_json=excluded.hard_failures_json,
                           soft_warnings_json=excluded.soft_warnings_json,metrics_json=excluded.metrics_json,
                           revision_required=excluded.revision_required""",
                    (
                        campaign_id, receipt_id, packet_id, output_hash, int(hard_pass),
                        self.e._dumps(hard_failures), self.e._dumps(soft_warnings),
                        self.e._dumps(metrics), int(revision_required), self.e._now(),
                    ),
                )
        return receipt

    def verify_accepted_output(
        self,
        campaign_id: str,
        packet_id: str,
        *,
        output_hash: str | None = None,
        receipt_id: str | None = None,
    ) -> dict[str, Any]:
        """Return non-secret evidence for an accepted, hard-passing exact output.

        This verifier never returns packet JSON, prose, quality evidence, or the
        private validation context. It is the narrow publish/presentation gate:
        callers must identify an exact output hash or exact receipt ID.
        """
        self.e._ensure_campaign_exists(campaign_id)
        clean_packet_id = self.e._clean_id(packet_id)
        clean_output_hash = str(output_hash or "").strip().lower()
        clean_receipt_id = self.e._clean_id(receipt_id) if receipt_id else None
        if not clean_output_hash and not clean_receipt_id:
            raise ValueError("output_hash or receipt_id is required")
        if clean_output_hash and not re.fullmatch(r"[0-9a-f]{64}", clean_output_hash):
            raise ValueError("output_hash must be a lowercase SHA-256 digest")

        packet_record = self._load_packet_record(campaign_id, clean_packet_id)
        packet = packet_record["packet"]
        if packet.get("packet_version") != self.PACKET_VERSION:
            raise ValueError("NARRATIVE_PACKET_VERSION_NOT_PUBLISHABLE")
        packet_digest = str(packet.get("digest") or "")
        turn_id = packet.get("turn_id")
        authoritative_revision = (
            (((packet.get("authority") or {}).get("authoritative_state") or {}).get("campaign") or {}).get("revision")
        )
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise ValueError("NARRATIVE_PACKET_TURN_ID_INVALID")
        if isinstance(authoritative_revision, bool) or not isinstance(authoritative_revision, int):
            raise ValueError("NARRATIVE_PACKET_REVISION_INVALID")
        if not self._verify_packet_hash(packet):
            raise ValueError("NARRATIVE_PACKET_INTEGRITY_FAILED")
        if packet_record.get("stored_packet_digest") != packet_digest:
            raise ValueError("NARRATIVE_PACKET_DIGEST_BINDING_FAILED")
        context_digest = packet_record.get("context_digest")
        if packet.get("packet_version") == self.PACKET_VERSION:
            validation_context = packet_record.get("validation_context")
            if (
                not isinstance(validation_context, dict)
                or not context_digest
                or self._digest(validation_context) != context_digest
                or validation_context.get("packet_id") != clean_packet_id
                or validation_context.get("packet_digest") != packet_digest
                or packet_record.get("context_packet_digest") != packet_digest
            ):
                raise ValueError("NARRATIVE_VALIDATION_CONTEXT_BINDING_FAILED")

        with self.e._db() as db:
            if clean_receipt_id:
                receipt = db.execute(
                    """SELECT receipt_id,packet_id,output_hash,hard_pass
                       FROM we4_narrative_quality_receipts
                       WHERE campaign_id=? AND receipt_id=?""",
                    (campaign_id, clean_receipt_id),
                ).fetchone()
            else:
                receipt = db.execute(
                    """SELECT receipt_id,packet_id,output_hash,hard_pass
                       FROM we4_narrative_quality_receipts
                       WHERE campaign_id=? AND packet_id=? AND output_hash=?
                       ORDER BY created_at DESC,receipt_id LIMIT 1""",
                    (campaign_id, clean_packet_id, clean_output_hash),
                ).fetchone()
            if not receipt:
                raise KeyError("no matching narrative quality receipt")
            resolved_output_hash = str(receipt["output_hash"])
            if (
                receipt["packet_id"] != clean_packet_id
                or not bool(receipt["hard_pass"])
                or (clean_output_hash and resolved_output_hash != clean_output_hash)
            ):
                raise ValueError("NARRATIVE_QUALITY_RECEIPT_NOT_ACCEPTED")
            output = db.execute(
                """SELECT output_id,output_hash,accepted
                   FROM we4_narrative_outputs
                   WHERE campaign_id=? AND packet_id=? AND output_hash=?""",
                (campaign_id, clean_packet_id, resolved_output_hash),
            ).fetchone()
        if not output or not bool(output["accepted"]):
            raise ValueError("NARRATIVE_OUTPUT_NOT_ACCEPTED")

        evidence = {
            "verification_version": "NOV-1.0",
            "campaign_id": campaign_id,
            "packet_id": clean_packet_id,
            "turn_id": turn_id,
            "authoritative_revision": authoritative_revision,
            "packet_digest": packet_digest,
            "packet_version": packet.get("packet_version"),
            "output_id": output["output_id"],
            "output_hash": resolved_output_hash,
            "receipt_id": receipt["receipt_id"],
            "receipt_version": self.RECEIPT_VERSION,
            "accepted": True,
            "hard_pass": True,
        }
        evidence["evidence_digest"] = self._digest(evidence)
        return evidence

    def _store_publication_receipt_db(
        self,
        db: "sqlite3.Connection",
        campaign_id: str,
        packet_id: str,
        receipt: dict[str, Any],
    ) -> None:
        """Store one accepted deterministic receipt on the caller's transaction."""
        expected_keys = {
            "receipt_version",
            "campaign_id",
            "packet_id",
            "output_hash",
            "hard_pass",
            "hard_failures",
            "soft_warnings",
            "metrics",
            "revision_required",
            "semantic_review",
            "receipt_id",
        }
        if (
            set(receipt) != expected_keys
            or receipt.get("campaign_id") != campaign_id
            or receipt.get("packet_id") != packet_id
            or receipt.get("receipt_version") != self.RECEIPT_VERSION
            or receipt.get("hard_pass") is not True
            or receipt.get("revision_required") is not False
        ):
            raise ValueError("PUBLICATION_QUALITY_RECEIPT_INVALID")
        receipt_id = self.e._clean_id(str(receipt.get("receipt_id") or ""))
        receipt_base = dict(receipt)
        receipt_base.pop("receipt_id")
        if receipt_id != f"nqr_{self._digest(receipt_base)[:24]}":
            raise ValueError("PUBLICATION_QUALITY_RECEIPT_INVALID")
        output_hash = str(receipt.get("output_hash") or "")
        if re.fullmatch(r"[0-9a-f]{64}", output_hash) is None:
            raise ValueError("PUBLICATION_QUALITY_RECEIPT_INVALID")
        hard_failures = list(receipt.get("hard_failures") or [])
        soft_warnings = list(receipt.get("soft_warnings") or [])
        metrics = dict(receipt.get("metrics") or {})
        if hard_failures:
            raise ValueError("PUBLICATION_QUALITY_RECEIPT_INVALID")
        values = (
            campaign_id,
            receipt_id,
            packet_id,
            output_hash,
            1,
            self.e._dumps(hard_failures),
            self.e._dumps(soft_warnings),
            self.e._dumps(metrics),
            0,
            self.e._now(),
        )
        db.execute(
            """INSERT INTO we4_narrative_quality_receipts(
                   campaign_id,receipt_id,packet_id,output_hash,hard_pass,hard_failures_json,
                   soft_warnings_json,metrics_json,revision_required,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(campaign_id,receipt_id) DO NOTHING""",
            values,
        )
        stored = db.execute(
            """SELECT packet_id,output_hash,hard_pass,hard_failures_json,
                      soft_warnings_json,metrics_json,revision_required
               FROM we4_narrative_quality_receipts
               WHERE campaign_id=? AND receipt_id=?""",
            (campaign_id, receipt_id),
        ).fetchone()
        expected = values[2:9]
        if stored is None or tuple(stored) != expected:
            raise ValueError("PUBLICATION_QUALITY_RECEIPT_CONFLICT")

    def accept_publication_output_db(
        self,
        db: "sqlite3.Connection",
        campaign_id: str,
        packet_id: str,
        output_text: str,
        *,
        packet: dict[str, Any],
        receipt: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Accept prose and advance server-owned director state on one connection.

        Model-declared beat, motif, and communicated-fact consumption is
        deliberately absent.  This primitive consumes only the director
        progression derivable from the immutable server packet.
        """
        if not db.in_transaction:
            raise ValueError("PUBLICATION_TRANSACTION_REQUIRED")
        if packet.get("campaign_id") != campaign_id or packet.get("packet_id") != packet_id:
            raise ValueError("PRESENTATION_PACKET_BINDING_FAILED")
        if (
            not isinstance(output_text, str)
            or hashlib.sha256(output_text.encode("utf-8")).hexdigest()
            != receipt.get("output_hash")
        ):
            raise ValueError("PUBLICATION_QUALITY_RECEIPT_INVALID")
        self._store_publication_receipt_db(db, campaign_id, packet_id, receipt)

        output_hash = str(receipt["output_hash"])
        output_id = f"nout_{self._digest([packet_id, output_hash])[:24]}"
        prior = db.execute(
            """SELECT packet_id,output_text,output_hash,accepted
               FROM we4_narrative_outputs
               WHERE campaign_id=? AND output_id=?""",
            (campaign_id, output_id),
        ).fetchone()
        if prior is not None and (
            prior["packet_id"] != packet_id
            or prior["output_text"] != output_text
            or prior["output_hash"] != output_hash
        ):
            raise ValueError("NARRATIVE_OUTPUT_IMMUTABILITY_CONFLICT")
        already_accepted = bool(prior and prior["accepted"])
        now = self.e._now()
        if prior is None:
            db.execute(
                """INSERT INTO we4_narrative_outputs(
                       campaign_id,output_id,packet_id,output_text,output_hash,accepted,
                       metadata_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,1,?,?,?)""",
                (
                    campaign_id,
                    output_id,
                    packet_id,
                    output_text,
                    output_hash,
                    self.e._dumps(metadata),
                    now,
                    now,
                ),
            )
        elif not already_accepted:
            changed = db.execute(
                """UPDATE we4_narrative_outputs
                   SET accepted=1,metadata_json=?,updated_at=?
                   WHERE campaign_id=? AND output_id=? AND accepted=0""",
                (self.e._dumps(metadata), now, campaign_id, output_id),
            ).rowcount
            if changed != 1:
                raise ValueError("NARRATIVE_OUTPUT_ACCEPTANCE_CONFLICT")

        state_update: dict[str, Any] = {
            "consumed": False,
            "scope": "server_authorized_director_only",
        }
        if not already_accepted:
            director = db.execute(
                "SELECT * FROM we4_narrative_director_state WHERE campaign_id=?",
                (campaign_id,),
            ).fetchone()
            if director is None:
                raise ValueError("NARRATIVE_DIRECTOR_STATE_MISSING")
            next_turn = int(director["turn_index"]) + 1
            is_major = bool((packet.get("scene") or {}).get("major_consequence"))
            quiet_turns = 0 if is_major else int(director["quiet_turns"]) + 1
            last_major = next_turn if is_major else director["last_major_turn"]
            changed = db.execute(
                """UPDATE we4_narrative_director_state
                   SET turn_index=?,quiet_turns=?,last_major_turn=?,state_json=?,updated_at=?
                   WHERE campaign_id=? AND turn_index=?""",
                (
                    next_turn,
                    quiet_turns,
                    last_major,
                    self.e._dumps(
                        {"last_packet_id": packet_id, "last_output_hash": output_hash}
                    ),
                    now,
                    campaign_id,
                    int(director["turn_index"]),
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("NARRATIVE_DIRECTOR_STATE_CONFLICT")
            state_update = {
                "consumed": True,
                "scope": "server_authorized_director_only",
                "director_turn_index": next_turn,
                "beat_id": None,
                "motifs_used": [],
                "dialogue_state_updated": False,
            }
        return {
            "campaign_id": campaign_id,
            "output_id": output_id,
            "packet_id": packet_id,
            "output_hash": output_hash,
            "accepted": True,
            "quality_receipt": receipt,
            "state_update": state_update,
        }

    def record_output(
        self,
        campaign_id: str,
        packet_id: str,
        output_text: str,
        *,
        accepted: bool = True,
        communicated_fact_ids: Sequence[str] = (),
        motifs_used: Sequence[str] = (),
        beat_realizations: Sequence[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record prose and consume only narrative elements explicitly realized.

        NRP-1.2 compatibility decision: ``strict_beat_realization`` defaults to
        true, so an omitted/empty ``beat_realizations`` list leaves the selected
        beat eligible. Operators may temporarily set it false while upgrading
        old callers; only an omitted list then restores v4.2 offered==consumed
        behavior. An explicit empty list always means no beat was realized.
        """
        requested_acceptance = bool(accepted)
        packet = self.get_packet(campaign_id, packet_id)
        receipt = self.quality_check(campaign_id, output_text, packet_id=packet_id, packet=packet, record=True)
        output_hash = receipt["output_hash"]
        output_id = f"nout_{self._digest([packet_id, output_hash])[:24]}"
        with self.e._write_db() as db:
            prior = db.execute(
                "SELECT accepted FROM we4_narrative_outputs WHERE campaign_id=? AND output_id=?",
                (campaign_id, output_id),
            ).fetchone()
            already_accepted = bool(prior and prior["accepted"])
            accepted = bool(
                already_accepted
                or (
                    requested_acceptance
                    and receipt["hard_pass"]
                    and not receipt["revision_required"]
                )
            )
            now = self.e._now()
            db.execute(
                """INSERT INTO we4_narrative_outputs(
                       campaign_id,output_id,packet_id,output_text,output_hash,accepted,metadata_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,output_id) DO UPDATE SET
                       accepted=MAX(we4_narrative_outputs.accepted,excluded.accepted),
                       metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                (campaign_id, output_id, packet_id, output_text, output_hash, int(accepted), self.e._dumps(metadata or {}), now, now),
            )

        state_update: dict[str, Any] = {"consumed": False}
        if accepted and not already_accepted:
            director = self.get_director_state(campaign_id)
            next_turn = int(director["turn_index"]) + 1
            selected_beat = ((packet.get("narrative_director") or {}).get("selected_beat") or {})
            beat_id = selected_beat.get("beat_id")
            persistent_beat = selected_beat.get("source") == "persistent_storylet"
            quality_config = self._validate_quality_config(self.get_config(campaign_id)["quality_config"])
            strict_beats = bool(quality_config["strict_beat_realization"])
            declared_beat_ids = {
                str(item.get("beat_id"))
                for item in (beat_realizations or [])
                if isinstance(item, dict) and item.get("beat_id")
            }
            compatibility_implicit = beat_realizations is None and not strict_beats
            beat_realized = bool(beat_id and (str(beat_id) in declared_beat_ids or compatibility_implicit))
            ignored_beat_ids = sorted(declared_beat_ids - ({str(beat_id)} if beat_id else set()))
            motif_ids = self._dedupe([str(x) for x in motifs_used], limit=20)
            dialogue = packet.get("dialogue_plan") or None
            with self.e._write_db() as db:
                if persistent_beat and beat_realized:
                    db.execute(
                        """UPDATE we4_narrative_beats
                           SET last_selected_turn=?,use_count=use_count+1,updated_at=?
                           WHERE campaign_id=? AND beat_id=?""",
                        (next_turn, self.e._now(), campaign_id, beat_id),
                    )
                consumed_motifs = []
                for motif_id in motif_ids:
                    row = db.execute(
                        "SELECT recent_realizations_json,use_count,max_recurrences FROM we4_motif_threads WHERE campaign_id=? AND motif_id=? AND status='active'",
                        (campaign_id, motif_id),
                    ).fetchone()
                    if not row or int(row["use_count"]) >= int(row["max_recurrences"]):
                        continue
                    recent = self.e._loads(row["recent_realizations_json"])
                    recent = [*recent, {"output_hash": output_hash, "preview": output_text[:240]}][-8:]
                    db.execute(
                        """UPDATE we4_motif_threads
                           SET last_used_turn=?,use_count=use_count+1,recent_realizations_json=?,updated_at=?
                           WHERE campaign_id=? AND motif_id=?""",
                        (next_turn, self.e._dumps(recent), self.e._now(), campaign_id, motif_id),
                    )
                    consumed_motifs.append(motif_id)
                recent_beats = [*director["recent_beats"], beat_id][-20:] if beat_realized else list(director["recent_beats"])
                target_tension = (
                    self._clamp(selected_beat.get("tension_target", director["tension"]))
                    if beat_realized else float(director["tension"])
                )
                is_major = bool((packet.get("scene") or {}).get("major_consequence"))
                quiet_turns = 0 if is_major else int(director["quiet_turns"]) + 1
                last_major = next_turn if is_major else director.get("last_major_turn")
                db.execute(
                    """UPDATE we4_narrative_director_state
                       SET turn_index=?,tension=?,quiet_turns=?,recent_beats_json=?,last_major_turn=?,state_json=?,updated_at=?
                       WHERE campaign_id=?""",
                    (
                        next_turn, target_tension, quiet_turns, self.e._dumps(recent_beats), last_major,
                        self.e._dumps({"last_packet_id": packet_id, "last_output_hash": output_hash}), self.e._now(), campaign_id,
                    ),
                )
            if dialogue:
                self.record_dialogue_state(
                    campaign_id, dialogue["speaker"], dialogue["listener"], topic=dialogue["topic"],
                    communicated_fact_ids=communicated_fact_ids,
                    concealed_fact_ids=dialogue.get("facts_to_conceal") or [],
                    speech_act=dialogue.get("speech_act"),
                    realization_fingerprint=output_hash[:24],
                    voice_state={"semantic_cache_key": dialogue.get("semantic_cache_key")},
                    subtext_state={"subtext": dialogue.get("subtext")},
                )
            state_update = {
                "consumed": True,
                "director_turn_index": next_turn,
                "beat_id": beat_id if persistent_beat and beat_realized else None,
                "beat_realized": beat_realized,
                "beat_realization_required": bool(beat_id and strict_beats),
                "beat_realization_compatibility_implicit": compatibility_implicit,
                "ignored_beat_realization_ids": ignored_beat_ids,
                "motifs_used": consumed_motifs,
                "dialogue_state_updated": bool(dialogue),
            }
        return {
            "campaign_id": campaign_id,
            "output_id": output_id,
            "packet_id": packet_id,
            "output_hash": output_hash,
            "accepted": accepted,
            "quality_receipt": receipt,
            "state_update": state_update,
        }

    # ------------------------------------------------------------------
    # Unified narrative capability dispatcher
    # ------------------------------------------------------------------

    def dispatch(self, operation: str, campaign_id: str = "default", payload: dict[str, Any] | None = None) -> Any:
        p = dict(payload or {})
        operation = str(operation or "").strip().lower()
        if operation in {"get_config", "config"}:
            return self.get_config(campaign_id)
        if operation == "configure":
            return self.configure(campaign_id, **p)
        if operation == "save_voice":
            npc_id = p.pop("npc_id")
            profile = p.pop("profile")
            return self.save_voice_profile(campaign_id, npc_id, profile, **p)
        if operation == "get_voice":
            return self.get_voice_profile(campaign_id, p["npc_id"])
        if operation == "list_voices":
            return self.list_voice_profiles(campaign_id)
        if operation == "save_beat":
            beat_id = p.pop("beat_id")
            return self.save_beat(campaign_id, beat_id, **p)
        if operation == "get_beat":
            return self.get_beat(campaign_id, p["beat_id"])
        if operation == "list_beats":
            return self.list_beats(campaign_id, **p)
        if operation == "save_motif":
            motif_id = p.pop("motif_id")
            return self.save_motif(campaign_id, motif_id, **p)
        if operation == "get_motif":
            return self.get_motif(campaign_id, p["motif_id"])
        if operation == "list_motifs":
            return self.list_motifs(campaign_id, **p)
        if operation == "plan_dialogue":
            speaker_id = p.pop("speaker_id")
            return self.plan_dialogue(campaign_id, speaker_id, **p)
        if operation == "validate_cutscene":
            packet = p.pop("cutscene_packet", p)
            return self.validate_cutscene_packet(campaign_id, dict(packet))
        if operation == "build_packet":
            return self.build_packet(campaign_id, **p)
        if operation == "get_packet":
            return self.get_packet(campaign_id, p["packet_id"])
        if operation == "list_packets":
            return self.list_packets(campaign_id, **p)
        if operation == "quality_check":
            output_text = p.pop("output_text")
            return self.quality_check(campaign_id, output_text, **p)
        if operation == "verify_accepted_output":
            packet_id = p.pop("packet_id")
            return self.verify_accepted_output(campaign_id, packet_id, **p)
        if operation == "record_output":
            packet_id = p.pop("packet_id")
            output_text = p.pop("output_text")
            return self.record_output(campaign_id, packet_id, output_text, **p)
        if operation == "record_dialogue_state":
            return self.record_dialogue_state(campaign_id, **p)
        if operation == "get_dialogue_state":
            return self.get_dialogue_state(campaign_id, **p)
        if operation == "get_director_state":
            return self.get_director_state(campaign_id)
        raise ValueError(f"unknown narrative operation: {operation}")
