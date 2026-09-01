from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

GENERATION_CONTRACT_VERSION = "WEGEN-1.2"
SUPPORTED_GENERATION_CONTRACTS = frozenset({"WEGEN-1.0", "WEGEN-1.1", GENERATION_CONTRACT_VERSION})

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
_RAW_ITEMS = (("grain", "Field Grain"), ("ore", "Iron Ore"), ("herbs", "Medicinal Herbs"), ("timber", "Seasoned Timber"))
_PRODUCT_ITEMS = (("provisions", "Travel Provisions"),)
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
            for index, (key, name) in enumerate(_RAW_ITEMS)
        ] + [
            {"id": f"{normalized_namespace}__item_{key}", "name": name, "base_price": 5 + index, "tags": ["crafted", "consumable", normalized_namespace]}
            for index, (key, name) in enumerate(_PRODUCT_ITEMS)
        ]
        raw_items = items[:len(_RAW_ITEMS)]
        resource_nodes: list[dict[str, Any]] = []
        for index in range(normalized_config["resource_count"]):
            item = raw_items[index % len(raw_items)]
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

        # WEGEN-1.2 extends the same staged, deterministic payload with a usable
        # finite economy and aggregate population. Generation itself is still
        # side-effect free; these rows must pass validation and dry-run before
        # one atomic authoring promotion may install them.
        grain_id = f"{normalized_namespace}__item_grain"
        provisions_id = f"{normalized_namespace}__item_provisions"
        provisions_recipe_id = f"{normalized_namespace}__recipe_provisions"
        recipes = [
            {
                "id": provisions_recipe_id,
                "kind": "cook",
                "inputs": {grain_id: 2.0},
                "output_item_id": provisions_id,
                "output_qty": 1.0,
                "dc": 10,
                "hours": 4.0,
                "metadata": {"generated_namespace": normalized_namespace, "economy_recipe": True},
            }
        ]

        economy_markets: list[dict[str, Any]] = []
        economy_market_items: list[dict[str, Any]] = []
        economy_inventories: list[dict[str, Any]] = []
        economy_balances: list[dict[str, Any]] = []
        economy_producers: list[dict[str, Any]] = []
        settlement_profiles: list[dict[str, Any]] = []
        population_cohorts: list[dict[str, Any]] = []
        for index, location in enumerate(locations):
            location_id = str(location["id"])
            market_id = f"{normalized_namespace}__market_{index + 1:02d}"
            population = float(location["state"]["population"])
            economy_markets.append(
                {
                    "id": market_id,
                    "location_id": location_id,
                    "name": f"{location['name']} Market",
                    "owner_kind": "location",
                    "owner_id": location_id,
                    "currency_key": "gp",
                    "buy_markup": 1.1,
                    "sell_discount": 0.65,
                    "visibility": "public",
                    "state": {"generated_namespace": normalized_namespace},
                }
            )
            for item_index, item in enumerate(items):
                is_provisions = str(item["id"]) == provisions_id
                target_stock = 12.0 + float(
                    self._number(
                        normalized_seed,
                        normalized_namespace,
                        "market-target",
                        index * len(items) + item_index,
                    )
                    % 25
                )
                economy_market_items.append(
                    {
                        "market_id": market_id,
                        "item_id": item["id"],
                        "target_stock": target_stock,
                        "reorder_point": round(target_stock * 0.3, 3),
                        "demand_per_day": round(
                            (population / 500.0) * (1.0 if is_provisions else 0.15), 3
                        ),
                        "demand_pressure": 0.0,
                        "floor_mult": 0.25,
                        "ceiling_mult": 4.0,
                        "enabled": True,
                        "state": {"generated_namespace": normalized_namespace},
                    }
                )
                economy_inventories.append(
                    {
                        "owner_kind": "location",
                        "owner_id": location_id,
                        "item_id": item["id"],
                        "qty": round(
                            target_stock
                            * (
                                0.55
                                + (
                                    self._number(
                                        normalized_seed,
                                        normalized_namespace,
                                        "market-stock",
                                        index * len(items) + item_index,
                                    )
                                    % 31
                                )
                                / 100.0
                            ),
                            3,
                        ),
                        "metadata": {
                            "generated_namespace": normalized_namespace,
                            "market_id": market_id,
                        },
                    }
                )
            economy_balances.append(
                {
                    "owner_kind": "location",
                    "owner_id": location_id,
                    "currency_key": "gp",
                    "amount": 250.0
                    + float(
                        self._number(
                            normalized_seed, normalized_namespace, "market-purse", index
                        )
                        % 501
                    ),
                }
            )
            economy_producers.append(
                {
                    "id": f"{normalized_namespace}__producer_{index + 1:02d}",
                    "location_id": location_id,
                    "owner_kind": "location",
                    "owner_id": location_id,
                    "recipe_id": provisions_recipe_id,
                    "batches_per_day": round(0.5 + population / 800.0, 3),
                    "max_batches_per_step": 24,
                    "active": True,
                    "state": {
                        "generated_namespace": normalized_namespace,
                        "workers_required": max(1.0, round(population / 250.0, 3)),
                        "occupation": "general",
                    },
                }
            )
            settlement_profiles.append(
                {
                    "location_id": location_id,
                    "settlement_type": "settlement",
                    "housing_capacity": round(population * 1.25, 3),
                    "water_capacity": round(population * 1.35, 3),
                    "sanitation": round(
                        0.45
                        + (
                            self._number(
                                normalized_seed, normalized_namespace, "sanitation", index
                            )
                            % 31
                        )
                        / 100.0,
                        3,
                    ),
                    "healthcare": round(
                        0.4
                        + (
                            self._number(
                                normalized_seed, normalized_namespace, "healthcare", index
                            )
                            % 31
                        )
                        / 100.0,
                        3,
                    ),
                    "prosperity": round(
                        0.4
                        + (
                            self._number(
                                normalized_seed, normalized_namespace, "prosperity", index
                            )
                            % 31
                        )
                        / 100.0,
                        3,
                    ),
                    "stability": round(
                        0.45
                        + (
                            self._number(
                                normalized_seed, normalized_namespace, "stability", index
                            )
                            % 31
                        )
                        / 100.0,
                        3,
                    ),
                    "attractiveness": round(
                        0.4
                        + (
                            self._number(
                                normalized_seed,
                                normalized_namespace,
                                "attractiveness",
                                index,
                            )
                            % 31
                        )
                        / 100.0,
                        3,
                    ),
                    "auto_rank": True,
                    "state": {
                        "generated_namespace": normalized_namespace,
                        "service_model": "derived",
                    },
                }
            )
            population_cohorts.append(
                {
                    "id": f"{normalized_namespace}__cohort_{index + 1:02d}",
                    "location_id": location_id,
                    "species": "human",
                    "culture": normalized_namespace,
                    "age_band": "mixed",
                    "livelihood": "general",
                    "count": population,
                    "birth_rate_annual": 0.02,
                    "death_rate_annual": 0.015,
                    "labor_participation": 0.55,
                    "migration_affinity": 1.0,
                    "health": 0.7,
                    "wealth": 0.5,
                    "state": {"generated_namespace": normalized_namespace},
                }
            )

        economy_extractors = [
            {
                "id": f"{normalized_namespace}__extractor_{index + 1:02d}",
                "location_id": node["location_id"],
                "owner_kind": "location",
                "owner_id": node["location_id"],
                "resource_node_id": node["id"],
                "units_per_day": max(
                    0.25, round(float(node["regen_per_day"]) * 0.8, 3)
                ),
                "max_units_per_step": 10.0,
                "active": True,
                "state": {
                    "generated_namespace": normalized_namespace,
                    "workers_required": 1.0,
                    "occupation": "general",
                },
            }
            for index, node in enumerate(resource_nodes)
        ]

        economy_routes: list[dict[str, Any]] = []
        for index, link in enumerate(location_links):
            directions = [(str(link["from_id"]), str(link["to_id"]))]
            if bool(link.get("bidirectional", True)):
                directions.append((str(link["to_id"]), str(link["from_id"])))
            for direction_index, (from_id, to_id) in enumerate(directions):
                economy_routes.append(
                    {
                        "id": f"{normalized_namespace}__route_{index + 1:02d}_{direction_index + 1}",
                        "from_location_id": from_id,
                        "to_location_id": to_id,
                        "travel_hours": float(link["travel_hours"]),
                        "capacity_qty_per_day": 50.0,
                        "risk": 0.05,
                        "cost_per_qty": 0.1,
                        "active": True,
                        "state": {"generated_namespace": normalized_namespace},
                    }
                )

        economy_supply_links: list[dict[str, Any]] = []
        market_by_location = {row["location_id"]: row["id"] for row in economy_markets}
        for route in economy_routes:
            source_market_id = market_by_location.get(route["from_location_id"])
            dest_market_id = market_by_location.get(route["to_location_id"])
            if source_market_id and dest_market_id:
                economy_supply_links.append(
                    {
                        "id": f"{route['id']}__provisions",
                        "source_market_id": source_market_id,
                        "dest_market_id": dest_market_id,
                        "item_id": provisions_id,
                        "reorder_point": 4.0,
                        "reorder_qty": 8.0,
                        "source_reserve": 6.0,
                        "route_id": route["id"],
                        "settle": True,
                        "enabled": True,
                        "state": {"generated_namespace": normalized_namespace},
                    }
                )

        economy_balances.append(
            {
                "owner_kind": "character",
                "owner_id": character_id,
                "currency_key": "gp",
                "amount": 75.0,
            }
        )

        rules = [
            {
                "id": f"{normalized_namespace}__rule_resource_regeneration",
                "archetype": "stock",
                "cadence": "day",
                "target": "resource_nodes.qty",
                "priority": 100,
                "params": {},
                "enabled": True,
            }
        ]

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
            "recipes": recipes,
            "rules": rules,
            "economy_markets": economy_markets,
            "economy_market_items": economy_market_items,
            "economy_extractors": economy_extractors,
            "economy_producers": economy_producers,
            "economy_routes": economy_routes,
            "economy_supply_links": economy_supply_links,
            "economy_inventories": economy_inventories,
            "economy_balances": economy_balances,
            "settlement_profiles": settlement_profiles,
            "population_cohorts": population_cohorts,
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
