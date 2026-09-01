"""Closed, player-safe read model for the standalone desktop companion.

The webview never receives a database handle, API key, raw event ledger, world
context packet, hidden facts, NPC beliefs/goals/memory, rejected narration, or
endpoint credentials. Every returned field is explicitly declassified here.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any, Mapping

from .economy import EconomyKernel
from .environment import EnvironmentKernel
from .population import PopulationKernel

if TYPE_CHECKING:
    from .engine import WorldEngine


DESKTOP_PROJECTION_VERSION = "WE-DESKTOP-1.1"
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")


def _text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def _json(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _relationship_label(value: int) -> str:
    if value <= -60:
        return "hostile"
    if value <= -20:
        return "strained"
    if value < 20:
        return "neutral"
    if value < 60:
        return "warm"
    return "trusted"


def _safe_inventory(value: Any) -> list[Any]:
    result: list[Any] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, str):
            result.append(_text(item, 200))
        elif isinstance(item, Mapping):
            safe = {
                key: item[key]
                for key in ("id", "item_id", "name", "qty")
                if key in item and isinstance(item[key], (str, int, float))
            }
            if safe:
                result.append(safe)
        if len(result) >= 200:
            break
    return result


def desktop_projection(latest: Mapping[str, Any] | None) -> dict[str, Any]:
    """Compatibility projection for an already-public presentation response."""
    source = latest if isinstance(latest, Mapping) else {}
    envelope = source.get("presentation")
    envelope = envelope if isinstance(envelope, Mapping) else {}
    choices = envelope.get("choices")
    public_presentation: dict[str, Any] = {
        "narration": _text(envelope.get("narration"), 12_000),
        "choices": [
            _text(choice, 500) for choice in choices[:9]
        ] if isinstance(choices, list) else [],
        "revision": envelope.get("revision") if isinstance(envelope.get("revision"), int) else None,
        "turn_id": _text(envelope.get("turn_id"), 128) or None,
        "presentation_id": _text(envelope.get("presentation_id"), 128) or None,
    }
    accepted = envelope.get("presentation")
    if isinstance(accepted, Mapping):
        public_presentation["accepted"] = {
            key: accepted[key]
            for key in (
                "presentation_version",
                "kind",
                "presentation_id",
                "narrative_evidence",
            )
            if key in accepted
        }
    return {
        "schema": DESKTOP_PROJECTION_VERSION,
        "campaign_id": _text(source.get("campaign_id"), 100) or "default",
        "presentation": public_presentation,
    }


class DesktopProjectionKernel:
    """Build the complete desktop snapshot through a strict field allowlist."""

    def __init__(
        self,
        engine: "WorldEngine",
        campaign_id: str = "default",
        character_id: str | None = None,
    ) -> None:
        if not _ID_RE.fullmatch(campaign_id):
            raise ValueError("invalid campaign id")
        if character_id is not None and not _ID_RE.fullmatch(character_id):
            raise ValueError("invalid character id")
        self.engine = engine
        self.campaign_id = campaign_id
        self.character_id = character_id

    def select_character(self, character_id: str) -> dict[str, str]:
        if not _ID_RE.fullmatch(str(character_id)):
            raise ValueError("invalid character id")
        self.engine.get_character(self.campaign_id, str(character_id))
        self.character_id = str(character_id)
        return {"status": "SELECTED", "character_id": self.character_id}

    def _player_row(self, db: Any) -> Any:
        if self.character_id:
            row = db.execute(
                "SELECT * FROM characters WHERE campaign_id=? AND id=?",
                (self.campaign_id, self.character_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown character: {self.character_id}")
            return row
        row = db.execute(
            "SELECT * FROM characters WHERE campaign_id=? AND status='alive' "
            "ORDER BY level DESC,name,id LIMIT 1",
            (self.campaign_id,),
        ).fetchone()
        if row is not None:
            self.character_id = str(row["id"])
        return row

    @staticmethod
    def _safe_player(row: Any | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": _text(row["id"], 100),
            "name": _text(row["name"], 200),
            "level": int(row["level"]),
            "hp": int(row["hp"]),
            "max_hp": int(row["max_hp"]),
            "ac": int(row["ac"]),
            "location_id": _text(row["location"], 100),
            "status": _text(row["status"], 20),
            "abilities": _json(row["abilities_json"], {}),
            "proficiency_bonus": int(row["proficiency_bonus"]),
            "conditions": _json(row["conditions_json"], []),
            "resources": _json(row["resources_json"], {}),
            "inventory": _safe_inventory(_json(row["inventory_json"], [])),
        }

    @staticmethod
    def _safe_location(row: Any | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": _text(row["id"], 100),
            "name": _text(row["name"], 200),
            "region": _text(row["region"], 200),
            "description": _text(row["description"], 2_000),
            "x": float(row["x"]) if row["x"] is not None else None,
            "y": float(row["y"]) if row["y"] is not None else None,
        }

    def _safe_map(self, db: Any, current_id: str | None) -> dict[str, Any]:
        rows = db.execute(
            "SELECT id,name,region,description,x,y,tags_json,state_json "
            "FROM locations WHERE campaign_id=? ORDER BY id LIMIT 512",
            (self.campaign_id,),
        ).fetchall()
        known: list[dict[str, Any]] = []
        known_ids: set[str] = set()
        for row in rows:
            tags = _json(row["tags_json"], [])
            state = _json(row["state_json"], {})
            is_public = (
                str(row["id"]) == current_id
                or "public_map" in tags
                or (isinstance(state, Mapping) and state.get("visibility") == "public_map")
            )
            if not is_public:
                continue
            safe = self._safe_location(row)
            if safe:
                known.append(safe)
                known_ids.add(safe["id"])
        links = []
        for row in db.execute(
            "SELECT from_id,to_id,travel_hours,road_quality FROM location_links "
            "WHERE campaign_id=? ORDER BY from_id,to_id LIMIT 2048",
            (self.campaign_id,),
        ).fetchall():
            if str(row["from_id"]) in known_ids and str(row["to_id"]) in known_ids:
                links.append(
                    {
                        "from_id": _text(row["from_id"], 100),
                        "to_id": _text(row["to_id"], 100),
                        "travel_hours": float(row["travel_hours"]),
                        "road_quality": _text(row["road_quality"], 80),
                    }
                )
        return {
            "locations": known,
            "links": links,
            "current_location_id": current_id,
        }

    def _safe_local_npcs(self, db: Any, location_id: str | None) -> list[dict[str, Any]]:
        if not location_id:
            return []
        return [
            {
                "id": _text(row["id"], 100),
                "name": _text(row["name"], 200),
                "faction_id": _text(row["faction_id"], 100) or None,
                "attitude": int(row["attitude"]),
                "status": _text(row["status"], 20),
            }
            for row in db.execute(
                "SELECT id,name,faction_id,attitude,status FROM npcs "
                "WHERE campaign_id=? AND location=? AND status='alive' "
                "ORDER BY name,id LIMIT 80",
                (self.campaign_id, location_id),
            ).fetchall()
        ]

    def _safe_factions(self, db: Any, local_npcs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        visible_ids = {str(npc["faction_id"]) for npc in local_npcs if npc.get("faction_id")}
        result = []
        for row in db.execute(
            "SELECT id,name,region,reputation,state_json FROM factions "
            "WHERE campaign_id=? ORDER BY name,id LIMIT 100",
            (self.campaign_id,),
        ).fetchall():
            state = _json(row["state_json"], {})
            if str(row["id"]) not in visible_ids and not (
                isinstance(state, Mapping) and state.get("visibility") == "public"
            ):
                continue
            result.append(
                {
                    "id": _text(row["id"], 100),
                    "name": _text(row["name"], 200),
                    "region": _text(row["region"], 200),
                    "reputation": int(row["reputation"]),
                }
            )
        return result

    def _safe_quests(self, db: Any, player_id: str | None) -> list[dict[str, Any]]:
        if not player_id:
            return []
        result = []
        for row in db.execute(
            "SELECT id,title,status,region,objectives_json FROM quests "
            "WHERE campaign_id=? AND owner_id=? ORDER BY updated_at DESC,id LIMIT 100",
            (self.campaign_id, player_id),
        ).fetchall():
            objectives = _json(row["objectives_json"], [])
            result.append(
                {
                    "id": _text(row["id"], 100),
                    "title": _text(row["title"], 300),
                    "status": _text(row["status"], 30),
                    "region": _text(row["region"], 200) or None,
                    "objectives": [
                        {"text": _text(item.get("text"), 500)}
                        if isinstance(item, Mapping)
                        else {"text": _text(item, 500)}
                        for item in objectives[:30]
                    ] if isinstance(objectives, list) else [],
                }
            )
        return result

    def _safe_relationships(self, db: Any, player_id: str | None) -> list[dict[str, Any]]:
        if not player_id:
            return []
        rows = db.execute(
            "SELECT source_id,target_id,trust,fear,respect,affection FROM relationships "
            "WHERE campaign_id=? AND (source_id=? OR target_id=?) "
            "ORDER BY source_id,target_id LIMIT 100",
            (self.campaign_id, player_id, player_id),
        ).fetchall()
        return [
            {
                "source_id": _text(row["source_id"], 100),
                "target_id": _text(row["target_id"], 100),
                "trust": _relationship_label(int(row["trust"])),
                "fear": _relationship_label(-int(row["fear"])),
                "respect": _relationship_label(int(row["respect"])),
                "affection": _relationship_label(int(row["affection"])),
            }
            for row in rows
        ]

    def _safe_combat(self, db: Any, player_id: str | None) -> dict[str, Any] | None:
        if not player_id:
            return None
        for row in db.execute(
            "SELECT * FROM combats WHERE campaign_id=? AND status='active' "
            "ORDER BY updated_at DESC LIMIT 10",
            (self.campaign_id,),
        ).fetchall():
            participants = _json(row["participants_json"], [])
            if not any(
                isinstance(item, Mapping)
                and item.get("kind") == "character"
                and item.get("id") == player_id
                for item in participants
            ):
                continue
            initiative = _json(row["initiative_json"], [])
            actors = []
            for item in participants[:40] if isinstance(participants, list) else []:
                if not isinstance(item, Mapping):
                    continue
                kind, actor_id = str(item.get("kind") or ""), str(item.get("id") or "")
                if kind not in {"character", "npc"} or not _ID_RE.fullmatch(actor_id):
                    continue
                table = "characters" if kind == "character" else "npcs"
                actor = db.execute(
                    f"SELECT name,status FROM {table} WHERE campaign_id=? AND id=?",
                    (self.campaign_id, actor_id),
                ).fetchone()
                actors.append(
                    {
                        "kind": kind,
                        "id": actor_id,
                        "name": _text(actor["name"], 200) if actor else actor_id,
                        "status": _text(actor["status"], 20) if actor else "unknown",
                        "is_player": kind == "character" and actor_id == player_id,
                    }
                )
            return {
                "id": _text(row["id"], 100),
                "location_id": _text(row["location"], 100),
                "round": int(row["round"]),
                "turn_index": int(row["turn_index"]),
                "turn_actor": initiative[int(row["turn_index"])]
                if isinstance(initiative, list) and 0 <= int(row["turn_index"]) < len(initiative)
                else None,
                "participants": actors,
            }
        return None

    def snapshot(self) -> dict[str, Any]:
        campaign = self.engine.get_campaign(self.campaign_id)
        latest = desktop_projection(self.engine.latest_accepted_presentation(self.campaign_id))
        with self.engine._db() as db:
            player_row = self._player_row(db)
            player = self._safe_player(player_row)
            player_id = player["id"] if player else None
            location_id = player["location_id"] if player else None
            legacy_inventory = list((player or {}).get("inventory") or [])
            inventory_ledger: list[dict[str, Any]] = []
            balances: list[dict[str, Any]] = []
            if player_id:
                ledger = self.engine._actor_ledger_db(
                    db, self.campaign_id, "character", player_id
                )
                for item in list(ledger.get("inventory_ledger") or [])[:200]:
                    item_id = _text(item.get("item_id"), 100)
                    definition = db.execute(
                        "SELECT name FROM item_defs WHERE campaign_id=? AND id=?",
                        (self.campaign_id, item_id),
                    ).fetchone()
                    inventory_ledger.append(
                        {
                            "item_id": item_id,
                            "name": _text(definition["name"], 200) if definition else item_id,
                            "qty": float(item.get("qty", 0)),
                        }
                    )
                balances = [
                    {"currency_key": _text(currency_key, 40), "amount": float(amount)}
                    for currency_key, amount in list(
                        dict(ledger.get("balances") or {}).items()
                    )[:40]
                ]
                player["inventory_ledger"] = inventory_ledger
                player["legacy_inventory"] = legacy_inventory
                player["balances"] = balances
            location_row = db.execute(
                "SELECT * FROM locations WHERE campaign_id=? AND id=?",
                (self.campaign_id, location_id),
            ).fetchone() if location_id else None
            location = self._safe_location(location_row)
            world_map = self._safe_map(db, location_id)
            local_npcs = self._safe_local_npcs(db, location_id)
            factions = self._safe_factions(db, local_npcs)
            quests = self._safe_quests(db, player_id)
            relationships = self._safe_relationships(db, player_id)
            combat = self._safe_combat(db, player_id)
            environment = EnvironmentKernel(self.engine).public_summary_db(
                db, self.campaign_id, location_id=location_id
            )
            economy = EconomyKernel(self.engine).public_snapshot_db(
                db, self.campaign_id, location_id=location_id
            ) if location_id else None
            population = PopulationKernel(self.engine).public_snapshot_db(
                db, self.campaign_id, location_id=location_id
            ) if location_id else None

        result = {
            "schema": DESKTOP_PROJECTION_VERSION,
            "campaign_id": self.campaign_id,
            "campaign": {
                "name": _text(campaign.get("name"), 200),
                "world_time": _text(campaign.get("world_time"), 80),
                "weather": _text(campaign.get("weather"), 80),
                "revision": int(campaign.get("revision", 0)),
            },
            "mode": "COMBAT" if combat else ("STORY" if latest["presentation"]["narration"] else "EXPLORE"),
            "presentation": latest["presentation"],
            "player": player,
            "location": location,
            "environment": environment,
            "economy": economy,
            "population": population,
            "world_map": world_map,
            "combat": combat,
            "quests": quests,
            "inventory": inventory_ledger or legacy_inventory,
            "balances": balances,
            "known_npcs": local_npcs,
            "known_factions": factions,
            "known_relationships": relationships,
            "journal": {
                "quests": quests,
                "accepted_presentation_id": latest["presentation"].get("presentation_id"),
            },
            "investigation": {
                "leads": [],
                "note": "Only explicitly player-visible leads are shown.",
            },
        }
        canonical = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        result["projection_sha256"] = hashlib.sha256(canonical).hexdigest()
        return result


__all__ = [
    "DESKTOP_PROJECTION_VERSION",
    "DesktopProjectionKernel",
    "desktop_projection",
]
