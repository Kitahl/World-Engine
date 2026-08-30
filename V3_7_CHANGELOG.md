# World Engine v3.7.0 Changelog

## Deterministic generalized rules kernel

- Schema migration **8 → 9**.
- New `world_engine/rules.py`.
- Rule objects for spells, feats, class/subclass/species/monster features, magic items, rituals, conditions, and custom mechanics.
- Data-driven Activities: attack, save, damage, heal, utility, summon, transform, teleport.
- Campaign/actor/rule version gating for 2014, 2024, or both.

## Mechanics

- Attack modifier composition, critical threshold, advantage/disadvantage.
- Saving throws, calculated/explicit DC, full/half/zero success damage.
- Typed damage, immunity, resistance, vulnerability, temporary HP.
- Structured effects, conditions, stacking, durations, expiry reasons.
- First-class concentration groups, replacement, and damage checks.
- Deterministic reaction windows and resource consumption.
- Combat action/bonus/reaction/movement state.
- Explicit path movement with blocking, occupancy, difficult terrain, and movement cost.
- Spell-slot and generic resource consumption/recovery.
- Slot/level scaling.
- Atomic short/long rest plus WORLD time/simulation.
- Dawn recovery across multi-day jumps.
- Persistent death saves/stability/death.
- Data-driven advancement grants.
- Temporary summon lifecycle and initiative integration.
- Generic transformation snapshot/restore primitive.
- Combat/WORLD teleportation with SCENE consistency.

## WORLD integration

- Normalized Activity `world_event_type` is written and dispatched into deterministic CASCADE in the same rules transaction.
- Legacy `resolveAttack` now uses the shared mitigation/temp-HP/concentration/death path.
- Rules state is surfaced in bounded world context and snapshots.

## Interfaces

- `POST /api/rules`, operation ID `runRulesKernel`.
- Equivalent MCP `run_rules_kernel` tool.
- GPT-visible OpenAPI remains exactly 30 operations by keeping a low-level visual-state write backend/MCP-only.

## Verification fixes found during implementation

- Effect-owned conditions no longer remain after the final owning effect ends.
- A before-damage reaction that grants resistance/immunity/temp HP is re-read before HP application.
- A failed automatic rest reaction now rolls back WORLD time and recovery with the rest.
- Dawn resources recover across every elapsed dawn and do not require a pre-existing actor profile.
- Concurrent one-charge rule use produces exactly one success.
- Summons join initiative and are removed cleanly when dismissed.
- WORLD teleportation updates active SCENE membership.

## Content boundary

No official D&D/SRD content dataset is bundled. `scripts/seed_rules_demo.py` contains only original demonstration definitions.
