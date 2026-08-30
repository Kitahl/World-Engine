# World Engine v3.7 Persistence

## Canonical save

`data/world_engine.sqlite3` is authoritative. Chat history is not the canonical state store.

## Existing persistent domains

WORLD geography/routes, characters/NPCs, factions/directors, lifecycle/succession, ownership, relationships/history, quests, resources/stocks, authoring batches/world bible/canon locks/content gaps, visual continuity, events, and deterministic RNG state.

## v3.7 rules persistence

- `rules_config`: active campaign rules version and dawn marker.
- `rule_actor_profiles`: save proficiencies, spell ability, temporary HP, death saves, mitigation, movement.
- `rule_objects`: feature/spell/item/ritual identity and source metadata.
- `rule_activities`: generalized deterministic mechanics definitions.
- `rule_actor_objects`: actor ownership/grants.
- `rule_resources`: current/max/recovery state.
- `rule_effects`: source, target, modifiers, conditions, concentration, duration, end reason.
- `rule_reactions`: deterministic trigger/condition/effect definitions.
- `rule_turn_state`: action, bonus, reaction, movement per combat actor.
- `rule_advancements`: data-driven grants at level boundaries.
- `rule_summons`: temporary actor ownership/lifetime.
- `rule_transform_snapshots`: reversible temporary form state.

## Transaction model

All authoritative rules mutations use serialized `BEGIN IMMEDIATE` transactions.

Activity resolution validates targets, version, action economy, and all required resources before committing mutations. Reactions, effects, damage, concentration, death, normalized WORLD events, and immediate CASCADE consequences are included in the Activity transaction.

Rest uses the same active transaction for WORLD time/simulation, timed-effect/dawn handling, recovery, hit dice/healing, rest effects/reactions, and ledger output.

## Temporary state

SCENE/combat/summon/transform records are removed or restored through their effect lifecycle. Persistent consequences must be written into WORLD state or normalized events before temporary rows disappear.
