#!/usr/bin/env python3
"""Confined, process-oriented headless player adapter for World Engine 5.1.1.

``new`` is an explicit controller operation.  ``observe`` and ``act`` are the
player surface: they return only the closed desktop projection and a small turn
receipt, and they never return an engine object, database handle, authoring
payload, context packet, raw event, or private cognition.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import random
import secrets
import sys
import time
import tempfile
from collections.abc import Iterator, Mapping, Sequence
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from world_engine import WorldEngine
from world_engine.desktop import DesktopProjectionKernel

ADAPTER_VERSION = "WE-HEADLESS-PLAYER-5.1.1"
SESSION_VERSION = 1
DATABASE_NAME = "world_engine.sqlite3"
SESSION_NAME = "headless_session.json"
LOCK_NAME = ".headless_player.lock"
OUTPUT_CHAR_LIMIT = 64_000
MAX_INTENTS = 20
MAX_PLAYER_TEXT = 20_000
MAX_INTENT_JSON = 12_000
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}")
_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}")

DEFAULT_CONFIG: dict[str, int] = {
    "location_count": 3,
    "faction_count": 2,
    "npcs_per_faction": 1,
    "resource_count": 2,
    "quest_count": 1,
}

# This is intentionally narrower than the engine manifest.  AUTHOR operations,
# direct consequence writers, raw event commits, legacy caller-authored attacks,
# state/config editors, and presentation/admin controls are absent by structure.
PLAYER_INTENT_CAPABILITIES: dict[str, str] = {
    "advance_time": "world.advance",
    "buy": "economy.interact",
    "census": "population.inspect",
    "check": "rules.check",
    "community": "population.inspect",
    "demography": "population.inspect",
    "dialogue": "npc.dialogue.context",
    "douse": "environment.interact",
    "economy": "economy.interact",
    "environment": "environment.interact",
    "extinguish": "environment.interact",
    "ignite": "environment.interact",
    "interact": "npc.dialogue.context",
    "market": "economy.interact",
    "move": "actor.move",
    "population": "population.inspect",
    "quote": "economy.interact",
    "route": "space.route",
    "rules": "rules.generic",
    "sell": "economy.interact",
    "settlement": "population.inspect",
    "shop": "economy.interact",
    "talk": "npc.dialogue.context",
    "trade": "economy.interact",
    "travel": "actor.move",
}
PLAYER_RULE_OPERATIONS = frozenset(
    {"resolve_activity", "move", "rest", "death_save", "list_effects", "get_actor_rules"}
)
PLAYER_OBSERVATION_FIELDS = (
    "schema",
    "campaign_id",
    "campaign",
    "mode",
    "presentation",
    "player",
    "location",
    "environment",
    "economy",
    "population",
    "world_map",
    "combat",
    "quests",
    "executable_quests",
    "inventory",
    "balances",
    "known_npcs",
    "known_factions",
    "known_relationships",
    "agency",
    "politics",
    "journal",
    "investigation",
    "projection_sequence",
    "terrain_seed",
    "notification_summary",
    "projection_sha256",
)
TURN_FIELDS = (
    "protocol_version",
    "campaign_id",
    "turn_id",
    "mode",
    "status",
    "actor_key",
    "revision_before",
    "revision_after",
    "revision_delta",
    "completed_intents",
    "failed_intents",
    "authoritative",
    "idempotent_replay",
    "turn_record_status",
    "retry_blocked",
)
MECHANICAL_RESULT_FIELDS = frozenset(
    {"success", "roll", "total", "dc", "modifier", "mode", "status", "damage_applied"}
)
PRIVATE_EVENT_TYPE = "headless_private_event_canary"
SETUP_EVENT_TYPE = "headless_session_setup_receipt"
SETUP_EVENT_SUMMARY = "Headless session setup receipt"
PRIVATE_BELIEF_PREFIX = "WE511_PRIVATE_BELIEF_CANARY_"


class PlayerError(Exception):
    """A typed, non-secret error suitable for the player stdout boundary."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise PlayerError("USAGE_ERROR", "Invalid command arguments. Use --help for usage.")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise PlayerError("DUPLICATE_JSON_KEY", "JSON objects may not contain duplicate keys.")
        out[key] = value
    return out


def _parse_object(raw: str, *, code: str, max_chars: int) -> dict[str, Any]:
    if len(raw) > max_chars:
        raise PlayerError(code, "JSON input exceeds the permitted size.")
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs)
    except PlayerError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PlayerError(code, "Input must be one valid JSON object.") from exc
    if not isinstance(value, dict):
        raise PlayerError(code, "Input must be one JSON object.")
    _validate_json_shape(value)
    return value


def _validate_json_shape(value: Any, *, depth: int = 0) -> None:
    if depth > 7:
        raise PlayerError("INTENT_SHAPE_INVALID", "JSON nesting is too deep.")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PlayerError("INTENT_SHAPE_INVALID", "JSON numbers must be finite.")
        return
    if isinstance(value, list):
        if len(value) > 100:
            raise PlayerError("INTENT_SHAPE_INVALID", "JSON arrays are limited to 100 items.")
        for item in value:
            _validate_json_shape(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 100:
            raise PlayerError("INTENT_SHAPE_INVALID", "JSON objects are limited to 100 fields.")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 128:
                raise PlayerError("INTENT_SHAPE_INVALID", "JSON field names are invalid.")
            _validate_json_shape(item, depth=depth + 1)
        return
    raise PlayerError("INTENT_SHAPE_INVALID", "Only ordinary JSON values are accepted.")


def _normalize_intent(value: Mapping[str, Any], index: int) -> dict[str, Any]:
    allowed_keys = {
        "intent_id",
        "type",
        "parameters",
        "depends_on",
        "requires_success_of",
        "optional",
    }
    if set(value) - allowed_keys:
        raise PlayerError(
            "PLAYER_INTENT_FIELD_NOT_ALLOWED",
            "Intent contains a field outside the player action contract.",
        )
    intent_type = str(value.get("type") or "").strip().lower()
    capability = PLAYER_INTENT_CAPABILITIES.get(intent_type)
    if capability is None:
        raise PlayerError(
            "PLAYER_INTENT_NOT_ALLOWED",
            "That intent is not available on the confined player surface.",
        )
    parameters = value.get("parameters", {})
    if not isinstance(parameters, dict):
        raise PlayerError("PLAYER_PARAMETERS_INVALID", "Intent parameters must be a JSON object.")
    if capability == "rules.generic":
        operation = str(parameters.get("operation") or "").strip().lower()
        if operation not in PLAYER_RULE_OPERATIONS:
            raise PlayerError(
                "PLAYER_RULE_OPERATION_NOT_ALLOWED",
                "That rules operation is not available on the confined player surface.",
            )
    if capability == "economy.interact" and any(
        key in parameters
        for key in ("actor_kind", "actor_id", "owner_kind", "owner_id", "transaction_key", "idempotency_key")
    ):
        raise PlayerError(
            "PLAYER_IDENTITY_OVERRIDE_FORBIDDEN",
            "Player market actions may not supply identity or replay authority.",
        )
    intent_id = str(value.get("intent_id") or f"intent_{index}").strip()
    if not _ID_RE.fullmatch(intent_id):
        raise PlayerError("PLAYER_INTENT_ID_INVALID", "Intent ID is invalid.")
    normalized: dict[str, Any] = {
        "intent_id": intent_id,
        "type": intent_type,
        "capability": capability,
        "parameters": dict(parameters),
    }
    for name in ("depends_on", "requires_success_of"):
        refs = value.get(name, [])
        if not isinstance(refs, list) or len(refs) > MAX_INTENTS:
            raise PlayerError("PLAYER_DEPENDENCY_INVALID", "Intent dependencies must be a bounded array.")
        clean_refs = []
        for ref in refs:
            token = str(ref).strip()
            if not _ID_RE.fullmatch(token):
                raise PlayerError("PLAYER_DEPENDENCY_INVALID", "Intent dependency ID is invalid.")
            clean_refs.append(token)
        if clean_refs:
            normalized[name] = clean_refs
    if value.get("optional") is not None:
        if not isinstance(value.get("optional"), bool):
            raise PlayerError("PLAYER_OPTIONAL_INVALID", "optional must be true or false.")
        normalized["optional"] = bool(value["optional"])
    return normalized


def _session_dir(raw: Path) -> Path:
    if not str(raw).strip():
        raise PlayerError("SESSION_DIR_REQUIRED", "An explicit session directory is required.")
    return raw.expanduser().resolve()


@contextmanager
def _session_lock(directory: Path, *, timeout: float = 5.0) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / LOCK_NAME
    handle = lock_path.open("a+b")
    try:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise PlayerError("SESSION_BUSY", "The session is busy; retry shortly.", retryable=True) from exc
                time.sleep(0.05)
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def _metadata_path(directory: Path) -> Path:
    return directory / SESSION_NAME


def _database_path(directory: Path) -> Path:
    return directory / DATABASE_NAME


def _write_metadata(directory: Path, metadata: dict[str, Any]) -> None:
    destination = _metadata_path(directory)
    temporary = destination.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _validate_metadata_value(value: Any) -> dict[str, Any]:
    required = {
        "adapter_version",
        "session_version",
        "session_id",
        "campaign_id",
        "character_id",
        "seed",
        "config",
        "config_sha256",
        "database",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise PlayerError("SESSION_METADATA_INVALID", "Session metadata has an unexpected shape.")
    if value.get("adapter_version") != ADAPTER_VERSION or value.get("session_version") != SESSION_VERSION:
        raise PlayerError("SESSION_VERSION_UNSUPPORTED", "Session metadata version is unsupported.")
    if value.get("database") != DATABASE_NAME:
        raise PlayerError("SESSION_METADATA_INVALID", "Session database binding is invalid.")
    if not all(
        _ID_RE.fullmatch(str(value.get(key) or ""))
        for key in ("session_id", "campaign_id", "character_id")
    ):
        raise PlayerError("SESSION_METADATA_INVALID", "Session identity binding is invalid.")
    if not isinstance(value.get("seed"), str) or not isinstance(value.get("config"), dict):
        raise PlayerError("SESSION_METADATA_INVALID", "Session request metadata is invalid.")
    _validate_json_shape(value["config"])
    if _fingerprint(value["config"]) != value.get("config_sha256"):
        raise PlayerError("SESSION_METADATA_INVALID", "Session configuration fingerprint does not match.")
    return dict(value)

def _load_metadata(directory: Path) -> dict[str, Any]:


    path = _metadata_path(directory)
    db_path = _database_path(directory)
    if path.is_symlink() or db_path.is_symlink():
        raise PlayerError("SESSION_PATH_INVALID", "Session state files may not be symbolic links.")
    if not path.is_file() or not db_path.is_file():
        raise PlayerError("SESSION_NOT_FOUND", "No complete headless session exists in that directory.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except PlayerError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PlayerError("SESSION_METADATA_INVALID", "Session metadata is invalid.") from exc
    return _validate_metadata_value(value)


def _private_canaries(engine: WorldEngine, campaign_id: str) -> list[str]:
    values: list[str] = []
    with engine._db() as db:
        rows = db.execute(
            "SELECT summary FROM events WHERE campaign_id=? AND event_type=? AND sensitivity='SECRET' ORDER BY id",
            (campaign_id, PRIVATE_EVENT_TYPE),
        ).fetchall()
        values.extend(str(row["summary"]) for row in rows)
        for row in db.execute(
            "SELECT beliefs_json FROM npcs WHERE campaign_id=? ORDER BY id", (campaign_id,)
        ).fetchall():
            beliefs = engine._loads(row["beliefs_json"])
            values.extend(
                str(item)
                for item in beliefs
                if isinstance(item, str) and item.startswith(PRIVATE_BELIEF_PREFIX)
            )
    if len(values) < 2:
        raise PlayerError("CONFIDENTIALITY_CANARY_MISSING", "Session confidentiality canaries are incomplete.")
    return values


def _scan_public(engine: WorldEngine, campaign_id: str, value: Any) -> None:
    encoded = _canonical(value)
    if any(marker and marker in encoded for marker in _private_canaries(engine, campaign_id)):
        raise PlayerError("CONFIDENTIALITY_GATE_FAILED", "Private campaign data reached the player projection.")


def _observation(engine: WorldEngine, metadata: Mapping[str, Any]) -> dict[str, Any]:
    raw = DesktopProjectionKernel(
        engine,
        str(metadata["campaign_id"]),
        str(metadata["character_id"]),
    ).snapshot()
    observation = {key: raw[key] for key in PLAYER_OBSERVATION_FIELDS if key in raw}
    campaign = observation.get("campaign")
    if not isinstance(campaign, dict) or observation.get("projection_sequence") != campaign.get("revision"):
        raise PlayerError("OBSERVATION_REVISION_MISMATCH", "Player observation is not revision-coherent.")
    player = observation.get("player")
    if not isinstance(player, dict) or player.get("id") != metadata["character_id"]:
        raise PlayerError("OBSERVATION_ACTOR_MISMATCH", "Player observation is not bound to the session actor.")
    _scan_public(engine, str(metadata["campaign_id"]), observation)
    return observation


def _session_public(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "session_id": metadata["session_id"],
        "campaign_id": metadata["campaign_id"],
        "character_id": metadata["character_id"],
    }


def _allowed_intents_public() -> dict[str, Any]:
    return {
        "types": dict(sorted(PLAYER_INTENT_CAPABILITIES.items())),
        "rules_operations": sorted(PLAYER_RULE_OPERATIONS),
        "contract": {
            "intent": {"type": "required", "parameters": "object", "intent_id": "optional"},
            "act_requires": ["text", "intent_json", "expected_revision", "idempotency_key"],
        },
    }


def _setup_request_fingerprint(
    *,
    seed: str,
    config: Mapping[str, Any],
    campaign_id: str,
    campaign_name: str,
) -> str:
    return _fingerprint(
        {
            "seed": seed,
            "config": dict(config),
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
        }
    )


def _setup_response(
    engine: WorldEngine,
    metadata: Mapping[str, Any],
    *,
    idempotent_replay: bool,
    generation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "ok": True,
        "command": "new",
        "phase": "controller_setup",
        "idempotent_replay": idempotent_replay,
        "session": _session_public(metadata),
        "gates": {
            "generated": True,
            "validated": True,
            "dry_run": True,
            "promoted": True,
            "confidentiality": "pass",
        },
        "observation": _observation(engine, metadata),
        "allowed_intents": _allowed_intents_public(),
    }
    if generation is not None:
        response["generation"] = {
            "contract_version": generation.get("contract_version"),
            "content_digest": generation.get("content_digest"),
        }
    return response


def _checkpoint_staged_database(engine: WorldEngine) -> None:
    with engine._db() as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or str(integrity[0]).casefold() != "ok":
            raise PlayerError("SESSION_DATABASE_INVALID", "Staged session database failed integrity validation.")
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()


def _read_setup_receipt(engine: WorldEngine) -> tuple[dict[str, Any], str]:
    with engine._db() as db:
        rows = db.execute(
            "SELECT campaign_id,summary,payload_json,sensitivity,scope_type "
            "FROM events WHERE event_type=? ORDER BY id",
            (SETUP_EVENT_TYPE,),
        ).fetchall()
    if len(rows) != 1:
        raise PlayerError(
            "SESSION_RECOVERY_INVALID",
            "The published database has no unique private setup receipt.",
        )
    row = rows[0]
    if (
        row["summary"] != SETUP_EVENT_SUMMARY
        or row["sensitivity"] != "SECRET"
        or row["scope_type"] != "SYSTEM"
    ):
        raise PlayerError("SESSION_RECOVERY_INVALID", "The private setup receipt is invalid.")
    payload = engine._loads(row["payload_json"])
    if not isinstance(payload, dict) or set(payload) != {
        "adapter_version",
        "session_version",
        "metadata",
        "request_sha256",
    }:
        raise PlayerError("SESSION_RECOVERY_INVALID", "The private setup receipt shape is invalid.")
    if payload.get("adapter_version") != ADAPTER_VERSION or payload.get("session_version") != SESSION_VERSION:
        raise PlayerError("SESSION_RECOVERY_INVALID", "The private setup receipt version is invalid.")
    metadata = _validate_metadata_value(payload.get("metadata"))
    if row["campaign_id"] != metadata["campaign_id"]:
        raise PlayerError("SESSION_RECOVERY_INVALID", "The private setup receipt campaign is invalid.")
    request_sha256 = str(payload.get("request_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", request_sha256):
        raise PlayerError("SESSION_RECOVERY_INVALID", "The private setup request fingerprint is invalid.")
    return metadata, request_sha256

def _open_published_engine_without_initialization(db_path: Path) -> WorldEngine:
    """Bind the current engine API to an already-qualified DB without schema writes."""
    engine = object.__new__(WorldEngine)
    engine.db_path = Path(db_path)
    engine.rng = random.Random(0)
    engine._turn_lock = threading.RLock()
    return engine



def _recover_published_session(
    directory: Path,
    *,
    seed: str,
    config: Mapping[str, Any],
    campaign_id: str,
    campaign_name: str,
) -> dict[str, Any]:
    db_path = _database_path(directory)
    engine = _open_published_engine_without_initialization(db_path)
    metadata, recorded_request = _read_setup_receipt(engine)
    requested = _setup_request_fingerprint(
        seed=seed,
        config=config,
        campaign_id=campaign_id,
        campaign_name=campaign_name,
    )
    if (
        recorded_request != requested
        or metadata["seed"] != seed
        or metadata["config_sha256"] != _fingerprint(dict(config))
        or metadata["campaign_id"] != campaign_id
    ):
        raise PlayerError(
            "SESSION_CREATE_CONFLICT",
            "The session directory already belongs to a different world request.",
        )
    _private_canaries(engine, campaign_id)
    response = _setup_response(engine, metadata, idempotent_replay=True)
    if _metadata_path(directory).exists():
        raise PlayerError("SESSION_CREATE_CONFLICT", "Session metadata appeared during recovery.")
    _write_metadata(directory, metadata)
    return response


def _create_session_transaction(
    directory: Path,
    *,
    seed: str,
    config: dict[str, Any],
    campaign_id: str,
    campaign_name: str,
    fault_stage: str | None,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=".headless-build-", dir=directory) as temporary:
        staged_db = Path(temporary) / DATABASE_NAME
        engine = WorldEngine(staged_db)
        engine.ensure_campaign(campaign_id, campaign_name, "1492-01-01T08:00:00+00:00")
        simulation_seed = int.from_bytes(
            hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big"
        ) & 0x7FFFFFFF
        engine.set_simulation_seed(campaign_id, simulation_seed)
        revision = int(engine.get_campaign(campaign_id)["revision"])
        staged = engine.stage_generated_world(
            campaign_id,
            "headless_bootstrap",
            seed,
            config,
            namespace="headless",
            expected_revision=revision,
        )
        validation = engine.author_validate(campaign_id, "headless_bootstrap")
        if not validation.get("valid"):
            raise PlayerError("WORLD_VALIDATION_FAILED", "Generated world failed authoring validation.")
        dry_run = engine.author_dry_run(campaign_id, "headless_bootstrap", days=1)
        if not dry_run.get("passed"):
            raise PlayerError("WORLD_DRY_RUN_FAILED", "Generated world failed the bounded dry run.")
        promoted = engine.author_promote(campaign_id, "headless_bootstrap")
        if promoted.get("status") != "promoted":
            raise PlayerError("WORLD_PROMOTION_FAILED", "Generated world was not promoted.")
        payload = staged["generation"]["payload"]
        character_id = str(payload["characters"][0]["id"])
        npc_id = str(payload["npcs"][0]["id"])
        engine.commit_event(
            campaign_id,
            PRIVATE_EVENT_TYPE,
            "WE511_PRIVATE_EVENT_CANARY_" + secrets.token_hex(16),
            sensitivity="SECRET",
            scope_type="GM",
        )
        engine.update_npc_state(
            campaign_id,
            npc_id,
            add_beliefs=[PRIVATE_BELIEF_PREFIX + secrets.token_hex(16)],
            reason="private headless qualification belief",
        )
        metadata = {
            "adapter_version": ADAPTER_VERSION,
            "session_version": SESSION_VERSION,
            "session_id": "headless_" + secrets.token_hex(8),
            "campaign_id": campaign_id,
            "character_id": character_id,
            "seed": seed,
            "config": config,
            "config_sha256": _fingerprint(config),
            "database": DATABASE_NAME,
        }
        request_sha256 = _setup_request_fingerprint(
            seed=seed,
            config=config,
            campaign_id=campaign_id,
            campaign_name=campaign_name,
        )
        engine.commit_event(
            campaign_id,
            SETUP_EVENT_TYPE,
            SETUP_EVENT_SUMMARY,
            payload={
                "adapter_version": ADAPTER_VERSION,
                "session_version": SESSION_VERSION,
                "metadata": metadata,
                "request_sha256": request_sha256,
            },
            sensitivity="SECRET",
            scope_type="SYSTEM",
        )
        _checkpoint_staged_database(engine)
        response = _setup_response(
            engine,
            metadata,
            idempotent_replay=False,
            generation=staged["generation"],
        )
        if fault_stage == "before_database_publish":
            raise PlayerError("TEST_FAULT_INJECTED", "Test interruption before database publication.")
        db_path = _database_path(directory)
        metadata_path = _metadata_path(directory)
        if db_path.exists() or metadata_path.exists() or db_path.is_symlink() or metadata_path.is_symlink():
            raise PlayerError("SESSION_CREATE_CONFLICT", "Session state appeared during publication.")
        try:
            os.link(staged_db, db_path)
        except FileExistsError as exc:
            raise PlayerError("SESSION_CREATE_CONFLICT", "Session database already exists.") from exc
        except OSError as exc:
            raise PlayerError("SESSION_PUBLISH_FAILED", "Session database could not be published atomically.") from exc
        if fault_stage == "after_database_publish":
            raise PlayerError("TEST_FAULT_INJECTED", "Test interruption after database publication.")
        _write_metadata(directory, metadata)
        return response


def controller_new(
    directory: Path,
    *,
    seed: str,
    config: Mapping[str, Any],
    campaign_id: str = "headless",
    campaign_name: str = "Headless World",
    fault_stage: str | None = None,
) -> dict[str, Any]:
    directory = _session_dir(directory)
    if len(seed) > 200 or not seed:
        raise PlayerError("SEED_INVALID", "Seed must contain 1 to 200 characters.")
    if not _ID_RE.fullmatch(campaign_id):
        raise PlayerError("CAMPAIGN_ID_INVALID", "Campaign ID is invalid.")
    if fault_stage not in {None, "before_database_publish", "after_database_publish"}:
        raise PlayerError("TEST_FAULT_INVALID", "Unknown internal test fault stage.")
    config_value = dict(config)
    _validate_json_shape(config_value)
    with _session_lock(directory):
        metadata_path = _metadata_path(directory)
        db_path = _database_path(directory)
        if metadata_path.is_symlink() or db_path.is_symlink():
            raise PlayerError("SESSION_PATH_INVALID", "Session state files may not be symbolic links.")
        if metadata_path.is_file() and db_path.is_file():
            metadata = _load_metadata(directory)
            if (
                metadata["seed"] != seed
                or metadata["config_sha256"] != _fingerprint(config_value)
                or metadata["campaign_id"] != campaign_id
            ):
                raise PlayerError(
                    "SESSION_CREATE_CONFLICT",
                    "The session directory already belongs to a different world request.",
                )
            return _setup_response(WorldEngine(db_path), metadata, idempotent_replay=True)
        if db_path.is_file() and not metadata_path.exists():
            return _recover_published_session(
                directory,
                seed=seed,
                config=config_value,
                campaign_id=campaign_id,
                campaign_name=campaign_name,
            )
        if metadata_path.exists() or db_path.exists():
            raise PlayerError(
                "SESSION_RECOVERY_INVALID",
                "Incomplete session state was preserved and cannot be recovered automatically.",
            )
        return _create_session_transaction(
            directory,
            seed=seed,
            config=config_value,
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            fault_stage=fault_stage,
        )


def player_observe(directory: Path) -> dict[str, Any]:
    directory = _session_dir(directory)
    with _session_lock(directory):
        metadata = _load_metadata(directory)
        engine = WorldEngine(_database_path(directory))
        observation = _observation(engine, metadata)
        return {
            "ok": True,
            "command": "observe",
            "phase": "player",
            "session": _session_public(metadata),
            "observation": observation,
            "allowed_intents": _allowed_intents_public(),
        }


def _safe_mechanical_result(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    for key in MECHANICAL_RESULT_FIELDS:
        item = value.get(key)
        if item is not None and isinstance(item, (str, bool, int, float)):
            result[key] = item
    check = value.get("check")
    if isinstance(check, dict):
        safe_check = {
            key: check[key]
            for key in MECHANICAL_RESULT_FIELDS
            if key in check and isinstance(check[key], (str, bool, int, float))
        }
        if safe_check:
            result["check"] = safe_check
    return result or None


def _project_turn(result: Mapping[str, Any]) -> dict[str, Any]:
    public = {key: result[key] for key in TURN_FIELDS if key in result}
    pbem = result.get("pbem")
    if isinstance(pbem, dict):
        decisions = []
        for item in list(pbem.get("decisions") or [])[:MAX_INTENTS]:
            if isinstance(item, dict):
                decisions.append(
                    {
                        key: item[key]
                        for key in ("intent_id", "capability_id", "decision", "code", "challengeable")
                        if key in item
                    }
                )
        public["pbem"] = {
            "version": pbem.get("version"),
            "enforced": bool(pbem.get("enforced")),
            "decisions": decisions,
            "rejected_intents": list(pbem.get("rejected_intents") or [])[:MAX_INTENTS],
        }
    steps = []
    for item in list(result.get("steps") or [])[:MAX_INTENTS]:
        if not isinstance(item, dict):
            continue
        step = {
            key: item[key]
            for key in (
                "intent_id",
                "intent_type",
                "capability_id",
                "status",
                "optional",
                "revision_before",
                "revision_after",
                "revision_delta",
            )
            if key in item
        }
        if isinstance(item.get("error"), dict):
            step["error"] = {
                "code": str(item["error"].get("code") or "ACTION_FAILED")[:100],
                "retryable": bool(item["error"].get("retryable")),
            }
        mechanical = _safe_mechanical_result(item.get("result"))
        if mechanical:
            step["mechanical_result"] = mechanical
        if isinstance(item.get("pbem"), dict):
            step["pbem"] = {
                key: item["pbem"][key]
                for key in ("version", "decision", "code", "challengeable")
                if key in item["pbem"]
            }
        steps.append(step)
    public["steps"] = steps
    return public


def player_act(
    directory: Path,
    *,
    text: str,
    intents: Sequence[Mapping[str, Any]],
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    directory = _session_dir(directory)
    if not text.strip() or len(text) > MAX_PLAYER_TEXT:
        raise PlayerError("PLAYER_TEXT_INVALID", "Player text must contain 1 to 20,000 characters.")
    if not 1 <= len(intents) <= MAX_INTENTS:
        raise PlayerError("PLAYER_INTENT_COUNT_INVALID", "An action requires 1 to 20 intents.")
    if isinstance(expected_revision, bool) or expected_revision < 0:
        raise PlayerError("EXPECTED_REVISION_INVALID", "Expected revision must be a non-negative integer.")
    if not _KEY_RE.fullmatch(idempotency_key):
        raise PlayerError("IDEMPOTENCY_KEY_INVALID", "Idempotency key is invalid.")
    normalized = [_normalize_intent(item, index) for index, item in enumerate(intents, start=1)]
    if len({item["intent_id"] for item in normalized}) != len(normalized):
        raise PlayerError("PLAYER_INTENT_ID_DUPLICATE", "Intent IDs must be unique within a turn.")

    with _session_lock(directory):
        metadata = _load_metadata(directory)
        engine = WorldEngine(_database_path(directory))
        try:
            result = engine.resolve_turn(
                str(metadata["campaign_id"]),
                actor_kind="character",
                actor_id=str(metadata["character_id"]),
                raw_player_text=text,
                intents=normalized,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                mode="execute",
                max_context_chars=14_000,
                include_archive=False,
                continue_on_error=False,
                retry_failed=False,
                enforce_pbem=True,
            )
        except ValueError as exc:
            message = str(exc)
            if "revision conflict" in message:
                raise PlayerError("REVISION_CONFLICT", "Observation is stale; observe again before acting.", retryable=True) from exc
            if "IDEMPOTENCY_KEY_CONFLICT" in message:
                raise PlayerError("IDEMPOTENCY_KEY_CONFLICT", "Idempotency key was already used for a different turn.") from exc
            raise PlayerError("ACTION_REJECTED", "The engine rejected the player action.") from exc
        public_turn = _project_turn(result)
        if not bool((public_turn.get("pbem") or {}).get("enforced")):
            raise PlayerError("PBEM_NOT_ENFORCED", "Player action did not pass through the PBEM boundary.")
        observation = _observation(engine, metadata)
        response = {
            "ok": True,
            "command": "act",
            "phase": "player",
            "session": _session_public(metadata),
            "normalized_intents": [
                {
                    "intent_id": item["intent_id"],
                    "type": item["type"],
                    "capability": item["capability"],
                }
                for item in normalized
            ],
            "turn": public_turn,
            "observation": observation,
        }
        _scan_public(engine, str(metadata["campaign_id"]), response)
        return response


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="controller-only: generate, validate, dry-run and promote a disposable world")
    new.add_argument("--session-dir", type=Path, required=True)
    new.add_argument("--seed", default="world-engine-headless-v511")
    new.add_argument("--config-json", default=_canonical(DEFAULT_CONFIG))
    new.add_argument("--campaign-id", default="headless")
    new.add_argument("--campaign-name", default="Headless World")

    observe = sub.add_parser("observe", help="read one bounded public player observation")
    observe.add_argument("--session-dir", type=Path, required=True)

    act = sub.add_parser("act", help="submit typed text and normalized intents through PBEM")
    act.add_argument("--session-dir", type=Path, required=True)
    act.add_argument("--text", required=True)
    act.add_argument("--intent-json", action="append", required=True)
    act.add_argument("--expected-revision", type=int, required=True)
    act.add_argument("--idempotency-key", required=True)
    return parser


def _error_response(exc: PlayerError, command: str | None) -> dict[str, Any]:
    return {
        "ok": False,
        "command": command,
        "error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable},
    }


def _emit(value: dict[str, Any]) -> None:
    encoded = _canonical(value)
    if len(encoded) > OUTPUT_CHAR_LIMIT:
        encoded = _canonical(
            {
                "ok": False,
                "command": value.get("command"),
                "error": {
                    "code": "PLAYER_OUTPUT_LIMIT_EXCEEDED",
                    "message": "The bounded player response exceeded its hard limit.",
                    "retryable": False,
                },
            }
        )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(encoded)


def main(argv: Sequence[str] | None = None) -> int:
    command: str | None = None
    try:
        args = _build_parser().parse_args(argv)
        command = args.command
        if command == "new":
            config = _parse_object(args.config_json, code="CONFIG_JSON_INVALID", max_chars=MAX_INTENT_JSON)
            response = controller_new(
                args.session_dir,
                seed=args.seed,
                config=config,
                campaign_id=args.campaign_id,
                campaign_name=args.campaign_name,
                fault_stage=os.environ.get("WORLD_ENGINE_HEADLESS_TEST_FAULT") or None,
            )
        elif command == "observe":
            response = player_observe(args.session_dir)
        elif command == "act":
            intents = [
                _parse_object(raw, code="INTENT_JSON_INVALID", max_chars=MAX_INTENT_JSON)
                for raw in args.intent_json
            ]
            response = player_act(
                args.session_dir,
                text=args.text,
                intents=intents,
                expected_revision=args.expected_revision,
                idempotency_key=args.idempotency_key,
            )
        else:  # pragma: no cover - argparse requires a known command
            raise PlayerError("USAGE_ERROR", "Unknown command.")
    except PlayerError as exc:
        _emit(_error_response(exc, command))
        return 2
    except Exception:  # noqa: BLE001 - stdout must fail closed without private exception text
        # Never reflect exception text: it can contain DB paths, private values,
        # or engine internals.  Detailed diagnostics remain on the controller side.
        _emit(
            _error_response(
                PlayerError("HEADLESS_PLAYER_FAILED", "Headless player operation failed closed."),
                command,
            )
        )
        return 1
    _emit(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
