from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .world_layers import WorldLayerKernel

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_video_id(value: str) -> str:
    """Return one canonical YouTube video ID or raise ``ValueError``.

    Raw 11-character IDs are accepted. URL input must use HTTP(S), an exact
    supported YouTube host, and one recognized path shape. Credentials,
    fragments, duplicate ``v`` parameters, and extra path segments are
    rejected so malformed input never reaches ``YT.Player``.
    """
    if not isinstance(value, str):
        raise ValueError("expected a YouTube video URL or 11-character video id")
    raw = value.strip()
    if not raw:
        raise ValueError("expected a YouTube video URL or 11-character video id")
    if _VIDEO_ID_RE.fullmatch(raw):
        return raw
    try:
        parsed = urlparse(raw)
    except Exception as exc:
        raise ValueError("invalid YouTube URL") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("YouTube URL must use http or https")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("YouTube URL contains unsupported credentials or fragment")
    host = (parsed.hostname or "").lower().rstrip(".")
    youtube_hosts = {
        "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
        "youtube-nocookie.com", "www.youtube-nocookie.com",
    }
    short_hosts = {"youtu.be", "www.youtu.be"}
    if host not in youtube_hosts | short_hosts:
        raise ValueError("unsupported YouTube host")
    parts = [part for part in parsed.path.split("/") if part]
    candidate = ""
    if host in short_hosts:
        if len(parts) != 1:
            raise ValueError("invalid youtu.be path")
        candidate = parts[0]
    elif parts == ["watch"]:
        values = parse_qs(parsed.query, keep_blank_values=True).get("v", [])
        if len(values) != 1:
            raise ValueError("YouTube watch URL must contain exactly one v parameter")
        candidate = values[0]
    elif len(parts) == 2 and parts[0] in {"embed", "shorts", "live"}:
        candidate = parts[1]
    else:
        raise ValueError("unsupported YouTube URL path")
    if not _VIDEO_ID_RE.fullmatch(candidate):
        raise ValueError("expected a YouTube video URL or 11-character video id")
    return candidate


def _load_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _daypart(world_time: str) -> str:
    try:
        hour = datetime.fromisoformat(world_time).hour
    except Exception:
        hour = 12
    if 5 <= hour < 8:
        return "dawn"
    if 8 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _safe_float(value: Any, default: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    if number != number or number in {float("inf"), float("-inf")}:
        return float(default)
    return number


def normalize_music_track(track: Any) -> dict[str, Any] | None:
    """Normalize one catalog entry and reject malformed YouTube identities."""
    if not isinstance(track, dict):
        return None
    item = dict(track)
    supplied = item.get("youtube") or item.get("youtube_url") or item.get("source_url")
    try:
        video_id = youtube_video_id(str(supplied or ""))
    except ValueError:
        return None
    item["youtube"] = video_id
    if isinstance(supplied, str) and supplied.strip().lower().startswith(("http://", "https://")):
        item["source_url"] = supplied.strip()
    else:
        item["source_url"] = f"https://www.youtube.com/watch?v={video_id}"
    match = item.get("match")
    if not isinstance(match, dict):
        match = {}
    item["match"] = match
    item["binding_tags"] = dict(match)
    item["priority"] = _safe_int(item.get("priority", 0), 0)
    item["volume"] = max(0.0, min(100.0, _safe_float(item.get("volume", 55.0), 55.0)))
    item["loop"] = bool(item.get("loop", True))
    item["enabled"] = bool(item.get("enabled", True))
    status = str(item.get("validation_status") or "unverified").strip().lower()
    if status not in {"unverified", "playable", "error", "invalid", "blocked"}:
        status = "unverified"
    item["validation_status"] = status
    item.setdefault("last_validation_result", None)
    return item


def normalize_music_catalog(payload: Any) -> dict[str, Any]:
    """Return a safe v1 catalog while preserving valid top-level settings."""
    if not isinstance(payload, dict):
        payload = {}
    result = dict(payload)
    result.setdefault("version", 1)
    defaults = result.get("defaults")
    result["defaults"] = dict(defaults) if isinstance(defaults, dict) else {}
    tracks = result.get("tracks")
    tracks = tracks if isinstance(tracks, list) else []
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in tracks:
        item = normalize_music_track(raw)
        if item is None:
            continue
        identity = (str(item.get("id") or ""), str(item["youtube"]))
        if identity in seen:
            continue
        seen.add(identity)
        normalized.append(item)
    result["tracks"] = normalized
    return result


@dataclass(frozen=True)
class MusicDecision:
    track: dict[str, Any] | None
    context: dict[str, Any]
    score: int | None
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "track": self.track,
            "context": self.context,
            "score": self.score,
            "reasons": list(self.reasons),
        }


class MusicResolver:
    """Deterministically select configured YouTube music from authoritative world state.

    Music is presentation state, not gameplay truth. The resolver reads the DB but never mutates
    the campaign. Tracks live in a small JSON catalog so users can edit/add YouTube URLs without
    consuming GPT Actions or altering the game schema.
    """

    MATCH_WEIGHTS = {
        "location_ids": 140,
        "combat": 130,
        "scene_types": 90,
        "director_ids": 85,
        "director_kinds": 70,
        "regions": 60,
        "realm_ids": 50,
        "scene_tags_any": 40,
        "location_tags_any": 35,
        "weather": 25,
        "time_of_day": 20,
    }

    FAILURE_COOLDOWN_SECONDS = 900.0
    FALLBACK_ERROR_CODES = {2, 5, 100, 101, 150}

    def __init__(self, engine: Any, catalog_path: str | Path):
        self.engine = engine
        self.catalog_path = Path(catalog_path)
        self._failure_lock = threading.RLock()
        self._player_failures: dict[str, dict[str, Any]] = {}

    @staticmethod
    def default_catalog() -> dict[str, Any]:
        return {
            "version": 1,
            "defaults": {"volume": 55, "poll_seconds": 2.0},
            "tracks": [],
        }

    def ensure_catalog(self) -> Path:
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.catalog_path.exists():
            self.catalog_path.write_text(json.dumps(self.default_catalog(), indent=2) + "\n", encoding="utf-8")
        return self.catalog_path

    def load_catalog(self) -> dict[str, Any]:
        self.ensure_catalog()
        try:
            payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except Exception:
            payload = self.default_catalog()
        return normalize_music_catalog(payload)

    def save_catalog(self, payload: dict[str, Any]) -> None:
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        normalized = normalize_music_catalog(payload)
        tmp = self.catalog_path.with_suffix(self.catalog_path.suffix + ".tmp")
        tmp.write_text(json.dumps(normalized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(self.catalog_path)

    def active_failed_video_ids(self) -> set[str]:
        now = time.monotonic()
        with self._failure_lock:
            expired = [video_id for video_id, item in self._player_failures.items()
                       if float(item.get("expires_at", 0.0)) <= now]
            for video_id in expired:
                self._player_failures.pop(video_id, None)
            return set(self._player_failures)

    def clear_player_failures(self) -> None:
        with self._failure_lock:
            self._player_failures.clear()

    def _persist_validation_result(self, video_id: str, code: int, message: str = "") -> bool:
        try:
            payload = self.load_catalog()
            changed = False
            if code == 2:
                status = "invalid"
            elif code in {101, 150}:
                status = "blocked"
            else:
                status = "error"
            result = {
                "status": status,
                "error_code": int(code),
                "message": str(message)[:500],
                "recorded_at_unix": int(time.time()),
            }
            for track in payload.get("tracks", []):
                if track.get("youtube") == video_id:
                    track["validation_status"] = status
                    track["last_validation_result"] = result
                    changed = True
            if changed:
                self.save_catalog(payload)
            return changed
        except Exception:
            return False

    def record_player_error(self, video_id: str | None, error_code: int, *, context: dict[str, Any] | None = None, message: str = "") -> dict[str, Any]:
        try:
            canonical = youtube_video_id(str(video_id or ""))
        except ValueError:
            return {"accepted": False, "reason": "invalid_video_id", "fallback_requested": False}
        code = _safe_int(error_code, -1)
        fallback = code in self.FALLBACK_ERROR_CODES
        if fallback:
            now = time.monotonic()
            with self._failure_lock:
                previous = self._player_failures.get(canonical, {})
                self._player_failures[canonical] = {
                    "expires_at": now + self.FAILURE_COOLDOWN_SECONDS,
                    "error_code": code,
                    "context": dict(context) if isinstance(context, dict) else None,
                    "attempts": _safe_int(previous.get("attempts", 0), 0) + 1,
                }
            persisted = self._persist_validation_result(canonical, code, message)
        else:
            persisted = False
        return {
            "accepted": True,
            "video_id": canonical,
            "error_code": code,
            "cooldown_seconds": int(self.FAILURE_COOLDOWN_SECONDS) if fallback else 0,
            "fallback_requested": fallback,
            "validation_persisted": bool(persisted),
        }

    def current_context(self, campaign_id: str = "default") -> dict[str, Any]:
        campaign_id = self.engine._clean_id(campaign_id)
        with self.engine._db() as db:
            campaign = db.execute(
                "SELECT world_time,weather FROM campaigns WHERE id=?", (campaign_id,)
            ).fetchone()
            if not campaign:
                self.engine.ensure_campaign(campaign_id)
                campaign = db.execute(
                    "SELECT world_time,weather FROM campaigns WHERE id=?", (campaign_id,)
                ).fetchone()
            scene = db.execute(
                "SELECT * FROM scenes WHERE campaign_id=? AND status='active' ORDER BY updated_at DESC LIMIT 1",
                (campaign_id,),
            ).fetchone()
            combat = db.execute(
                "SELECT id,location,round FROM combats WHERE campaign_id=? AND status='active' ORDER BY updated_at DESC LIMIT 1",
                (campaign_id,),
            ).fetchone()
            location_id = str(scene["location_id"]) if scene else None
            if not location_id and combat:
                location_id = str(combat["location"])
            if not location_id:
                pc = db.execute(
                    "SELECT location FROM characters WHERE campaign_id=? AND status='alive' ORDER BY id LIMIT 1",
                    (campaign_id,),
                ).fetchone()
                location_id = str(pc["location"]) if pc and pc["location"] else None

            location = None
            if location_id:
                location = db.execute(
                    "SELECT id,name,region,realm_id,tags_json,state_json FROM locations WHERE campaign_id=? AND id=?",
                    (campaign_id, location_id),
                ).fetchone()

            directors = (
                WorldLayerKernel(self.engine).active_directors_db(
                    db, campaign_id, location_id, str(scene["id"]) if scene else None
                )
                if location_id
                else {"stack": [], "policies": {}}
            )

        scene_state = _load_json(scene["state_json"], {}) if scene else {}
        location_state = _load_json(location["state_json"], {}) if location else {}
        location_tags = set(_load_json(location["tags_json"], []) if location else [])
        scene_tags: set[str] = set()
        for key in ("tags", "music_tags"):
            values = scene_state.get(key, []) if isinstance(scene_state, dict) else []
            if isinstance(values, str):
                scene_tags.add(values)
            elif isinstance(values, list):
                scene_tags.update(str(v) for v in values)
        if isinstance(location_state, dict):
            values = location_state.get("music_tags", [])
            if isinstance(values, str):
                location_tags.add(values)
            elif isinstance(values, list):
                location_tags.update(str(v) for v in values)

        stack = directors.get("stack", []) if isinstance(directors, dict) else []
        return {
            "campaign_id": campaign_id,
            "world_time": str(campaign["world_time"]),
            "weather": str(campaign["weather"] or "clear").lower(),
            "time_of_day": _daypart(str(campaign["world_time"])),
            "location_id": location_id,
            "location_name": str(location["name"]) if location else location_id,
            "region": str(location["region"]) if location and location["region"] else None,
            "realm_id": str(location["realm_id"]) if location and location["realm_id"] else None,
            "location_tags": sorted(location_tags),
            "scene_id": str(scene["id"]) if scene else None,
            "scene_type": str(scene["scene_type"]) if scene else "ambient",
            "scene_tags": sorted(scene_tags),
            "combat": bool(combat),
            "combat_id": str(combat["id"]) if combat else None,
            "combat_round": int(combat["round"]) if combat else None,
            "director_ids": [str(d.get("id")) for d in stack if d.get("id")],
            "director_kinds": sorted({str(d.get("director_kind")) for d in stack if d.get("director_kind")}),
            "director_names": [str(d.get("name")) for d in stack if d.get("name")],
        }

    @staticmethod
    def _as_set(value: Any) -> set[str]:
        if value is None:
            return set()
        if isinstance(value, str):
            return {value}
        if isinstance(value, list):
            return {str(v) for v in value}
        return {str(value)}

    def _match_track(self, track: dict[str, Any], context: dict[str, Any]) -> tuple[int, list[str]] | None:
        if not bool(track.get("enabled", True)):
            return None
        try:
            youtube_video_id(str(track.get("youtube", "")))
        except ValueError:
            return None
        match = track.get("match") or {}
        if not isinstance(match, dict):
            return None
        score = int(track.get("priority", 0))
        reasons: list[str] = []

        scalar_to_list = {
            "location_ids": context.get("location_id"),
            "regions": context.get("region"),
            "realm_ids": context.get("realm_id"),
            "scene_types": context.get("scene_type"),
            "weather": context.get("weather"),
            "time_of_day": context.get("time_of_day"),
        }
        for field, current in scalar_to_list.items():
            wanted = self._as_set(match.get(field))
            if wanted:
                if current is None or str(current) not in wanted:
                    return None
                score += self.MATCH_WEIGHTS[field]
                reasons.append(f"{field}={current}")

        if "combat" in match:
            if bool(match["combat"]) != bool(context.get("combat")):
                return None
            score += self.MATCH_WEIGHTS["combat"]
            reasons.append(f"combat={bool(context.get('combat'))}")

        set_fields = {
            "director_ids": set(context.get("director_ids") or []),
            "director_kinds": set(context.get("director_kinds") or []),
            "location_tags_any": set(context.get("location_tags") or []),
            "scene_tags_any": set(context.get("scene_tags") or []),
        }
        for field, current_set in set_fields.items():
            wanted = self._as_set(match.get(field))
            if wanted:
                overlap = wanted & current_set
                if not overlap:
                    return None
                score += self.MATCH_WEIGHTS[field]
                reasons.append(f"{field}={','.join(sorted(overlap))}")

        return score, reasons

    def resolve(self, campaign_id: str = "default", *, exclude_video_ids: set[str] | None = None) -> MusicDecision:
        catalog = self.load_catalog()
        context = self.current_context(campaign_id)
        excluded = set(exclude_video_ids or set()) | self.active_failed_video_ids()
        candidates: list[tuple[int, str, dict[str, Any], list[str]]] = []
        defaults = catalog.get("defaults") or {}
        for raw in catalog.get("tracks", []):
            if not isinstance(raw, dict):
                continue
            track = dict(raw)
            result = self._match_track(track, context)
            if not result:
                continue
            score, reasons = result
            video_id = youtube_video_id(str(track.get("youtube", "")))
            if video_id in excluded:
                continue
            track["youtube_video_id"] = video_id
            track.setdefault("volume", int(defaults.get("volume", 55)))
            track.setdefault("loop", True)
            track.setdefault("name", track.get("id") or video_id)
            track_id = str(track.get("id") or video_id)
            candidates.append((score, track_id, track, reasons))
        if not candidates:
            return MusicDecision(None, context, None, ("no matching configured track",))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        score, _, track, reasons = candidates[0]
        return MusicDecision(track, context, score, tuple(reasons or ["fallback/default match"]))

    def add_track_for_context(
        self,
        campaign_id: str,
        youtube: str,
        *,
        name: str = "",
        scope: str = "location",
        volume: int = 55,
    ) -> dict[str, Any]:
        video_id = youtube_video_id(youtube)
        context = self.current_context(campaign_id)
        scope = scope.strip().lower()
        match: dict[str, Any]
        priority = 0
        if scope == "location":
            if not context.get("location_id"):
                raise ValueError("no current location")
            match = {"location_ids": [context["location_id"]], "combat": False}
            priority = 100
        elif scope == "combat":
            match = {"combat": True}
            priority = 400
        elif scope == "location_combat":
            if not context.get("location_id"):
                raise ValueError("no current location")
            match = {"location_ids": [context["location_id"]], "combat": True}
            priority = 500
        elif scope == "scene":
            match = {"scene_types": [context.get("scene_type") or "ambient"], "combat": False}
            priority = 250
        elif scope == "director":
            if context.get("director_ids"):
                match = {"director_ids": [context["director_ids"][0]]}
            elif context.get("director_kinds"):
                match = {"director_kinds": [context["director_kinds"][0]]}
            else:
                raise ValueError("no active director/deity/power in current context")
            priority = 350
        elif scope == "fallback":
            match = {}
            priority = -100
        else:
            raise ValueError("scope must be location, combat, location_combat, scene, director, or fallback")

        catalog = self.load_catalog()
        base = re.sub(r"[^A-Za-z0-9_.-]+", "-", (name or scope).strip()).strip("-") or scope
        track_id = f"{base[:50]}-{video_id}"
        entry = {
            "id": track_id,
            "name": name.strip() or track_id,
            "youtube": video_id,
            "source_url": youtube.strip() if isinstance(youtube, str) and youtube.strip().lower().startswith(("http://", "https://")) else f"https://www.youtube.com/watch?v={video_id}",
            "enabled": True,
            "priority": priority,
            "volume": max(0, min(100, int(volume))),
            "loop": True,
            "match": match,
            "binding_tags": dict(match),
            "validation_status": "unverified",
            "last_validation_result": None,
        }
        tracks = [t for t in catalog.get("tracks", []) if isinstance(t, dict) and t.get("id") != track_id]
        tracks.append(entry)
        catalog["tracks"] = tracks
        self.save_catalog(catalog)
        return entry
