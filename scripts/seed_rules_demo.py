"""Seed original demonstration rules for World Engine v3.7.

This file demonstrates the generalized rules kernel. It intentionally does not
bundle or reproduce official D&D spell/feature text.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from world_engine import WorldEngine


def main() -> int:
    db_path = Path(os.environ.get("WORLD_ENGINE_DB", ROOT / "data" / "world_engine.sqlite3"))
    campaign_id = os.environ.get("WORLD_ENGINE_CAMPAIGN", "default")
    engine = WorldEngine(db_path)
    engine.ensure_campaign(campaign_id, "World Engine Rules Demo")
    engine.upsert_location(campaign_id, "training_yard", "Training Yard", region="demo")

    try:
        engine.get_character(campaign_id, "player")
    except KeyError:
        engine.upsert_character(
            campaign_id, "player", "Adventurer", level=3, hp=24, max_hp=24, ac=14,
            location="training_yard", abilities={"str": 3, "dex": 2, "con": 2, "int": 3},
            proficiency_bonus=2,
        )
    try:
        engine.get_npc(campaign_id, "training_construct")
    except KeyError:
        engine.upsert_npc(
            campaign_id, "training_construct", "Training Construct", hp=30, max_hp=30,
            ac=12, location="training_yard", stats={"dex_mod": 1, "con_mod": 2},
        )

    engine.rules_dispatch("configure", campaign_id, {"rules_version": "2024", "grid_feet": 5})
    engine.rules_dispatch("set_actor_profile", campaign_id, {
        "actor_kind": "character", "actor_id": "player", "spellcasting_ability": "int",
        "save_proficiencies": ["con"], "movement_cells": 6,
    })
    engine.rules_dispatch("set_actor_profile", campaign_id, {
        "actor_kind": "npc", "actor_id": "training_construct", "resistances": ["fire"],
    })
    engine.rules_dispatch("set_resource", campaign_id, {
        "actor_kind": "character", "actor_id": "player", "resource_key": "spell_slot:1",
        "current": 2, "maximum": 2, "recovery": "long_rest",
    })
    engine.rules_dispatch("set_resource", campaign_id, {
        "actor_kind": "character", "actor_id": "player", "resource_key": "guard_reaction",
        "current": 1, "maximum": 1, "recovery": "short_rest",
    })

    objects = [
        {"object_id": "demo_blade", "name": "Training Blade", "object_kind": "magic_item", "rules_version": "both", "source": "World Engine original demo"},
        {"object_id": "demo_burst", "name": "Arcane Training Burst", "object_kind": "spell", "rules_version": "both", "level": 1, "source": "World Engine original demo"},
        {"object_id": "demo_focus", "name": "Focused Guard", "object_kind": "class_feature", "rules_version": "both", "source": "World Engine original demo"},
    ]
    for obj in objects:
        engine.rules_dispatch("define_object", campaign_id, obj)
        engine.rules_dispatch("grant_object", campaign_id, {
            "actor_kind": "character", "actor_id": "player", "object_id": obj["object_id"], "source": "demo seed",
        })

    engine.rules_dispatch("define_activity", campaign_id, {
        "activity_id": "demo_blade_attack", "object_id": "demo_blade", "name": "Training Blade Attack",
        "activity_type": "attack", "activation": "action", "rules_version": "both",
        "attack": {"ability": "str", "proficient": True, "critical_threshold": 20},
        "damage": [{"formula": "1d8+3", "type": "slashing"}],
        "targeting": {"mode": "single", "range_cells": 1},
    })
    engine.rules_dispatch("define_activity", campaign_id, {
        "activity_id": "demo_burst_cast", "object_id": "demo_burst", "name": "Arcane Training Burst",
        "activity_type": "save", "activation": "action", "rules_version": "both",
        "save": {"ability": "dex", "caster_ability": "int", "on_success": "half"},
        "damage": [{"formula": "2d6", "type": "force"}],
        "targeting": {"mode": "area", "shape": "sphere", "radius_cells": 2, "max_targets": 12},
        "consumption": [{"resource": "spell_slot", "minimum_level": 1, "amount": 1}],
        "scaling": {"type": "slot", "base_level": 1, "damage_per_level": [{"formula": "1d6", "type": "force"}]},
        "world_event_type": "public_magic",
    })
    engine.rules_dispatch("define_activity", campaign_id, {
        "activity_id": "demo_focus_use", "object_id": "demo_focus", "name": "Focused Guard",
        "activity_type": "utility", "activation": "action", "rules_version": "both",
        "targeting": {"mode": "self"},
        "effects": [{
            "name": "Focused Guard", "modifiers": {"ac_bonus": 1, "save_bonus": {"con": 1}},
            "concentration": True, "duration": {"unit": "hour", "value": 1},
        }],
    })
    engine.rules_dispatch("define_reaction", campaign_id, {
        "reaction_id": "demo_guard_reaction", "owner_kind": "character", "owner_id": "player",
        "trigger": "after_attack_roll", "name": "Guarding Response", "priority": 10,
        "conditions": {"attack_would_hit": True},
        "consumption": [{"resource": "guard_reaction", "amount": 1}],
        "effect": {"effect": {"name": "Guarding Response", "modifiers": {"ac_bonus": 2}, "duration": {"unit": "turn_start"}}},
        "consumes_reaction": True, "selection_mode": "automatic",
    })

    print(json.dumps({
        "campaign_id": campaign_id,
        "rules_version": engine.rules_dispatch("get_actor_rules", campaign_id, {"actor_kind": "character", "actor_id": "player"})["profile"]["rules_version"],
        "objects_seeded": len(objects),
        "activities_seeded": 3,
        "reactions_seeded": 1,
        "note": "Original mechanics demonstration only; no official SRD text is bundled.",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
