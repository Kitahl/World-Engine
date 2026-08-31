from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

GENERATION_CONTRACT_VERSION = "WEGEN-1.1"
SUPPORTED_GENERATION_CONTRACTS = frozenset({"WEGEN-1.0", GENERATION_CONTRACT_VERSION})

_CONFIG_DEFAULTS = {
    "location_count": 6,
    "faction_count": 3,
    "npcs_per_faction": 2,
    "resource_count": 6,
    "quest_count": 2,
}
_CONFIG_BOUNDS = {
    "location_count": (3, 20),
    "faction_count": (2, 8),
    "npcs_per_faction": (1, 5),
    "resource_count": (1, 40),
    "quest_count": (1, 8),
}
_NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

_PLACE_PREFIXES = ("Amber", "Ash", "Briar", "Cinder", "Dawn", "Elder", "Frost", "Gloam", "High", "Iron", "Juniper", "Kings", "Lark", "Moon", "North", "Oak")
_PLACE_SUFFIXES = ("Cross", "Deep", "Fen", "Ford", "Gate", "Harbor", "Hollow", "Keep", "March", "Reach", "Spire", "Vale", "Watch", "Wood")
_REGIONS = ("Borderlands", "Green March", "High Country", "Low Vale", "North Reach", "Old Coast")
_FACTION_ADJECTIVES = ("Azure", "Brass", "Ember", "Grey", "Ivory", "Jade", "Sable", "Silver", "Verdant")
_FACTION_NOUNS = ("Accord", "Circle", "Company", "Concord", "Keepers", "League", "Order", "Wardens")
_PERSON_FIRST = ("Ader", "Bran", "Cerys", "Dara", "Elian", "Fara", "Galen", "Hesta", "Ilya", "Joren", "Kara", "Lio", "Mara", "Neris", "Orin", "Pella")
_PERSON_LAST = ("Ashdown", "Blackmere", "Cairn", "Dunlow", "Everly", "Fenwick", "Grey", "Harrow", "Kestrel", "Lorne", "Morrow", "North", "Quill", "Rook")
_ITEMS = (("grain", "Field Grain"), ("ore", "Iron Ore"), ("herbs", "Medicinal Herbs"), ("timber", "Seasoned Timber"))
_STANCES = ("allied", "neutral", "rival")
_BIOMES = ("forest", "plains", "coast", "highlands", "wetlands", "desert", "tundra")
_BIOME_CLIMATE = {
    "forest": "temperate",
    "plains": "temperate",
    "coast": "coastal",
    "highlands": "alpine",
    "wetlands": "coastal",
    "desert": "arid",
    "tundra": "arctic",
}
_BIOME_REGION = {
    "forest": "Green March",
    "plains": "Borderlands",
    "coast": "Old Coast",
    "highlands": "High Country",
    "wetlands": "Low Vale",
    "desert": "Borderlands",
    "tundra": "North Reach",
}
_BIOME_NEIGHBORS = {
    "forest": ("forest", "plains", "wetlands", "highlands", "tundra"),
    "plains": ("forest", "plains", "coast", "highlands", "wetlands", "desert"),
    "coast": ("plains", "coast", "wetlands", "desert"),
    "highlands": ("forest", "plains", "highlands", "tundra"),
    "wetlands": ("forest", "plains", "coast", "wetlands"),
    "desert": ("plains", "coast", "desert"),
    "tundra": ("forest", "highlands", "tundra"),
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    return text or "generated"


class ProceduralWorldGenerator:
    """Stateless deterministic authoring-payload generator.

    Entropy is derived independently for every labeled choice. Adding a new
    choice therefore cannot consume or shift a global RNG stream.
    """

    contract_version = GENERATION_CONTRACT_VERSION

    @classmethod
    def normalize_config(cls, config: Mapping[str, Any] | None = None) -> dict[str, int]:
        supplied = dict(config or {})
        unknown = sorted(set(supplied) - set(_CONFIG_DEFAULTS))
        if unknown:
            raise ValueError(f"unknown procedural config keys: {', '.join(unknown)}")
        normalized: dict[str, int] = {}
        for key, default in _CONFIG_DEFAULTS.items():
            value = supplied.get(key, default)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{key} must be an integer")
            low, high = _CONFIG_BOUNDS[key]
            if not low <= value <= high:
                raise ValueError(f"{key} must be {low}..{high}")
            normalized[key] = value
        return normalized

    @staticmethod
    def normalize_namespace(namespace: str) -> str:
        value = str(namespace).strip().lower()
        if not _NAMESPACE_RE.fullmatch(value):
            raise ValueError("namespace must match [a-z][a-z0-9_]{0,39}")
        return value

    @staticmethod
    def normalize_seed(seed: str | int) -> str:
        if isinstance(seed, bool) or not isinstance(seed, (str, int)):
            raise TypeError("seed must be a string or integer")
        value = str(seed)
        if not value or len(value.encode("utf-8")) > 256:
            raise ValueError("seed must contain 1..256 UTF-8 bytes")
        return value

    def _number(self, seed: str, namespace: str, label: str, index: int = 0) -> int:
        material = b"\0".join(
            (
                self.contract_version.encode("ascii"),
                seed.encode("utf-8"),
                namespace.encode("ascii"),
                label.encode("utf-8"),
                str(index).encode("ascii"),
            )
        )
        return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")

    def _pick(self, values: tuple[Any, ...], seed: str, namespace: str, label: str, index: int = 0) -> Any:
        return values[self._number(seed, namespace, label, index) % len(values)]

    @staticmethod
    def _id(namespace: str, kind: str, index: int) -> str:
        return f"{namespace}__{kind}_{index + 1:02d}"

    def generate(
        self,
        seed: str | int,
        config: Mapping[str, Any] | None = None,
        *,
        namespace: str = "bootstrap",
        mode: str = "bootstrap",
        anchor_location_id: str | None = None,
    ) -> dict[str, Any]:
        if mode not in {"bootstrap", "expansion"}:
            raise ValueError("mode must be bootstrap or expansion")
        normalized_seed = self.normalize_seed(seed)
        normalized_namespace = self.normalize_namespace(namespace)
        normalized_config = self.normalize_config(config)
        normalized_anchor = None
        if anchor_location_id is not None:
            normalized_anchor = str(anchor_location_id).strip()
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", normalized_anchor):
                raise ValueError("anchor_location_id must be a canonical identifier")
        if mode == "bootstrap" and normalized_anchor is not None:
            raise ValueError("bootstrap generation cannot use an existing-world anchor")
        if mode == "expansion" and normalized_anchor is None:
            raise ValueError("expansion generation requires an existing-world anchor")
        prefix = normalized_namespace + "__"

        locations: list[dict[str, Any]] = []
        used_names: set[str] = set()
        previous_biome: str | None = None
        for index in range(normalized_config["location_count"]):
            base = f"{self._pick(_PLACE_PREFIXES, normalized_seed, normalized_namespace, 'location-prefix', index)} {self._pick(_PLACE_SUFFIXES, normalized_seed, normalized_namespace, 'location-suffix', index)}"
            name = base if base not in used_names else f"{base} {index + 1}"
            used_names.add(name)
            biome_pool = _BIOMES if previous_biome is None else _BIOME_NEIGHBORS[previous_biome]
            biome = self._pick(biome_pool, normalized_seed, normalized_namespace, "location-biome", index)
            previous_biome = biome
            locations.append(
                {
                    "id": self._id(normalized_namespace, "location", index),
                    "name": name,
                    "region": _BIOME_REGION[biome],
                    "description": f"{name} is a generated {biome} settlement and travel anchor.",
                    "x": index * 12,
                    "y": int(self._number(normalized_seed, normalized_namespace, "location-y", index) % 17) - 8,
                    "tags": ["procedural", "public_map", biome, prefix.rstrip("_")],
                    "state": {
                        "population": 80 + self._number(normalized_seed, normalized_namespace, "population", index) % 721,
                        "visibility": "public_map",
                        "biome": biome,
                        "sheltered": True,
                    },
                }
            )

        climates = [
            {
                "scope_type": "location",
                "scope_id": location["id"],
                "climate": _BIOME_CLIMATE[str(location["state"]["biome"])],
                "season": "summer",
                "weather_weights": {},
                "state": {
                    "auto_weather": True,
                    "auto_season": True,
                    "actor_exposure": False,
                    "generated_namespace": normalized_namespace,
                    "biome": location["state"]["biome"],
                },
            }
            for location in locations
        ]

        location_links: list[dict[str, Any]] = []
        for index in range(len(locations) - 1):
            location_links.append(
                {
                    "from_id": locations[index]["id"],
                    "to_id": locations[index + 1]["id"],
                    "travel_hours": 2 + self._number(normalized_seed, normalized_namespace, "travel-hours", index) % 11,
                    "road_quality": self._pick(("trail", "road", "old road"), normalized_seed, normalized_namespace, "road-quality", index),
                    "bidirectional": True,
                    "metadata": {"generated_namespace": normalized_namespace},
                }
            )
        if len(locations) >= 4 and str(locations[-1]["state"]["biome"]) in _BIOME_NEIGHBORS[str(locations[0]["state"]["biome"])]:
            location_links.append(
                {
                    "from_id": locations[0]["id"],
                    "to_id": locations[-1]["id"],
                    "travel_hours": 8 + self._number(normalized_seed, normalized_namespace, "cross-route") % 9,
                    "road_quality": "trail",
                    "bidirectional": True,
                    "metadata": {"generated_namespace": normalized_namespace},
                }
            )
        if normalized_anchor is not None:
            location_links.append(
                {
                    "from_id": normalized_anchor,
                    "to_id": locations[0]["id"],
                    "travel_hours": 4 + self._number(normalized_seed, normalized_namespace, "anchor-route") % 9,
                    "road_quality": "road",
                    "bidirectional": True,
                    "metadata": {
                        "generated_namespace": normalized_namespace,
                        "expansion_anchor": True,
                    },
                }
            )

        archetype_id = f"{normalized_namespace}__archetype_citizen"
        archetypes = [
            {
                "id": archetype_id,
                "name": "Generated Citizen",
                "needs": {
                    "hunger": {"value": 35, "baseline": 35, "drift_per_day": 0.08, "curve": "threshold"},
                    "security": {"value": 55, "baseline": 55, "drift_per_day": 0.03, "curve": "quadratic"},
                },
                "actions": [{"id": "work", "base_utility": 0.2, "cost_hours": 8}, {"id": "rest", "base_utility": 0.1, "cost_hours": 8}],
                "tags": ["procedural", normalized_namespace],
            }
        ]

        factions: list[dict[str, Any]] = []
        npcs: list[dict[str, Any]] = []
        for faction_index in range(normalized_config["faction_count"]):
            faction_id = self._id(normalized_namespace, "faction", faction_index)
            leader_id = self._id(normalized_namespace, "npc", faction_index * normalized_config["npcs_per_faction"])
            faction_name = f"{self._pick(_FACTION_ADJECTIVES, normalized_seed, normalized_namespace, 'faction-adjective', faction_index)} {self._pick(_FACTION_NOUNS, normalized_seed, normalized_namespace, 'faction-noun', faction_index)}"
            factions.append(
                {
                    "id": faction_id,
                    "name": faction_name,
                    "region": locations[faction_index % len(locations)]["region"],
                    "reputation": 0,
                    "reserve_score": 10 + self._number(normalized_seed, normalized_namespace, "faction-reserve", faction_index) % 41,
                    "goals": ["protect local interests", "secure reliable trade"],
                    "state": {"generated_namespace": normalized_namespace, "visibility": "public"},
                    "leader_id": leader_id,
                }
            )
            for member_index in range(normalized_config["npcs_per_faction"]):
                npc_index = faction_index * normalized_config["npcs_per_faction"] + member_index
                first = self._pick(_PERSON_FIRST, normalized_seed, normalized_namespace, "npc-first", npc_index)
                last = self._pick(_PERSON_LAST, normalized_seed, normalized_namespace, "npc-last", npc_index)
                npcs.append(
                    {
                        "id": self._id(normalized_namespace, "npc", npc_index),
                        "name": f"{first} {last}",
                        "archetype_id": archetype_id,
                        "location": locations[(npc_index + faction_index) % len(locations)]["id"],
                        "faction_id": faction_id,
                        "hp": 8 + self._number(normalized_seed, normalized_namespace, "npc-hp", npc_index) % 9,
                        "max_hp": 16,
                        "ac": 10 + self._number(normalized_seed, normalized_namespace, "npc-ac", npc_index) % 5,
                        "attitude": int(self._number(normalized_seed, normalized_namespace, "npc-attitude", npc_index) % 7) - 3,
                        "importance": "major" if member_index == 0 else "supporting",
                        "goals": ["serve the faction", "protect the settlement"],
                    }
                )

        character_id = f"{normalized_namespace}__character_start"
        characters = [
            {
                "id": character_id,
                "name": f"{self._pick(_PERSON_FIRST, normalized_seed, normalized_namespace, 'hero-first')} {self._pick(_PERSON_LAST, normalized_seed, normalized_namespace, 'hero-last')}",
                "level": 1,
                "hp": 12,
                "max_hp": 12,
                "ac": 13,
                "location": locations[0]["id"],
                "abilities": {"str": 1, "dex": 1, "con": 1, "int": 0, "wis": 0, "cha": 0},
                "proficiency_bonus": 2,
                "notes": {"generated_namespace": normalized_namespace, "role": "starting_character"},
            }
        ]

        items = [
            {"id": f"{normalized_namespace}__item_{key}", "name": name, "base_price": index + 1, "tags": ["resource", normalized_namespace]}
            for index, (key, name) in enumerate(_ITEMS)
        ]
        resource_nodes: list[dict[str, Any]] = []
        for index in range(normalized_config["resource_count"]):
            item = items[index % len(items)]
            qty_max = 20 + self._number(normalized_seed, normalized_namespace, "resource-capacity", index) % 81
            resource_nodes.append(
                {
                    "id": self._id(normalized_namespace, "resource", index),
                    "location_id": locations[index % len(locations)]["id"],
                    "item_id": item["id"],
                    "qty": qty_max // 2,
                    "qty_max": qty_max,
                    "regen_per_day": round(0.25 + (self._number(normalized_seed, normalized_namespace, "resource-regen", index) % 176) / 100, 2),
                    "season_mult": {"winter": 0.7, "summer": 1.2},
                    "metadata": {"generated_namespace": normalized_namespace},
                }
            )

        quests: list[dict[str, Any]] = []
        for index in range(normalized_config["quest_count"]):
            target_location = locations[(index + 1) % len(locations)]
            contact = npcs[index % len(npcs)]
            quests.append(
                {
                    "id": self._id(normalized_namespace, "quest", index),
                    "title": f"Road to {target_location['name']}",
                    "status": "active",
                    "owner_id": character_id,
                    "region": target_location["region"],
                    "objectives": [
                        {"text": f"Speak with {contact['name']}", "target_kind": "npc", "target_id": contact["id"]},
                        {"text": f"Reach {target_location['name']}", "target_kind": "location", "target_id": target_location["id"]},
                    ],
                    "state": {"generated_namespace": normalized_namespace, "starter": True},
                }
            )

        faction_relations = []
        for index in range(len(factions) - 1):
            faction_relations.append(
                {
                    "faction_a": factions[index]["id"],
                    "faction_b": factions[index + 1]["id"],
                    "stance": self._pick(_STANCES, normalized_seed, normalized_namespace, "faction-stance", index),
                    "tension": int(self._number(normalized_seed, normalized_namespace, "faction-tension", index) % 81) - 40,
                    "trust": int(self._number(normalized_seed, normalized_namespace, "faction-trust", index) % 81) - 40,
                    "state": {"generated_namespace": normalized_namespace},
                }
            )

        payload: dict[str, Any] = {
            "items": items,
            "locations": locations,
            "climates": climates,
            "location_links": location_links,
            "factions": factions,
            "archetypes": archetypes,
            "npcs": npcs,
            "characters": characters,
            "resource_nodes": resource_nodes,
            "quests": quests,
            "faction_relations": faction_relations,
        }
        if mode == "bootstrap":
            payload["world_bible"] = {
                "generation_contract": self.contract_version,
                "generation_seed": normalized_seed,
                "generation_namespace": normalized_namespace,
                "tone": self._pick(("bright frontier", "grounded wonder", "heroic folklore", "somber mystery"), normalized_seed, normalized_namespace, "tone"),
                "magic": self._pick(("low", "rare but real", "widely known"), normalized_seed, normalized_namespace, "magic"),
            }

        generation_core = {
            "contract_version": self.contract_version,
            "seed": normalized_seed,
            "namespace": normalized_namespace,
            "mode": mode,
            "anchor_location_id": normalized_anchor,
            "config": normalized_config,
            "payload": payload,
        }
        content_digest = _digest(generation_core)
        manifest = {
            "contract_version": self.contract_version,
            "seed": normalized_seed,
            "namespace": normalized_namespace,
            "mode": mode,
            "anchor_location_id": normalized_anchor,
            "config": normalized_config,
            "counts": {key: len(value) for key, value in payload.items() if isinstance(value, list)},
            "ids": {key: sorted(str(row["id"]) for row in value if "id" in row) for key, value in payload.items() if isinstance(value, list)},
            "payload_digest": _digest(payload),
            "content_digest": content_digest,
        }
        payload["_generation"] = {
            "contract_version": self.contract_version,
            "seed": normalized_seed,
            "namespace": normalized_namespace,
            "mode": mode,
            "anchor_location_id": normalized_anchor,
            "config": normalized_config,
            "content_digest": content_digest,
        }
        return {
            "contract_version": self.contract_version,
            "seed": normalized_seed,
            "namespace": normalized_namespace,
            "mode": mode,
            "config": normalized_config,
            "manifest": manifest,
            "content_digest": content_digest,
            "payload": payload,
        }


__all__ = ["GENERATION_CONTRACT_VERSION", "SUPPORTED_GENERATION_CONTRACTS", "ProceduralWorldGenerator"]
