# World Engine v3.7 — Deterministic Rules Kernel Guide

## 1. Scope

v3.7 adds a **headless, data-driven tabletop-RPG mechanics kernel**. It resolves abstract game entities stored in SQLite. It does not bundle a complete official rules compendium and does not use a language model to calculate runtime mechanics.

```text
PLAYER INTENT
    -> runRulesKernel
    -> deterministic RESOLVED result
    -> SCENE mutation
    -> optional normalized WORLD event
    -> existing deterministic CASCADE
    -> GPT narration
```

## 2. One authenticated API

`POST /api/rules`

```json
{
  "campaign_id": "default",
  "operation": "resolve_activity",
  "payload": {
    "activity_id": "demo_blade_attack",
    "actor_kind": "character",
    "actor_id": "player",
    "targets": [{"kind": "npc", "id": "training_construct"}],
    "combat_id": "training_combat"
  }
}
```

The same dispatcher is available through MCP as `run_rules_kernel`.

Supported operations:

1. `configure`
2. `set_actor_profile`
3. `define_object`
4. `define_activity`
5. `grant_object`
6. `set_resource`
7. `define_reaction`
8. `resolve_activity`
9. `move`
10. `rest`
11. `death_save`
12. `list_effects`
13. `end_effect`
14. `get_actor_rules`
15. `define_advancement`
16. `apply_advancement`

## 3. Rule objects and activities

A rule object can represent:

- spell
- feat
- class feature
- subclass feature
- species feature
- monster feature
- magic item
- ritual
- condition
- other custom mechanics

An object owns one or more data-driven Activities. Supported Activity types are:

- `attack`
- `save`
- `damage`
- `heal`
- `utility`
- `summon`
- `transform`
- `teleport`

The engine intentionally does not define one Python function per spell or feature.

## 4. Attack resolution

Attack Activities combine:

- selected ability modifier
- proficiency when configured
- authored activity bonus
- active-effect attack modifiers
- advantage/disadvantage
- critical threshold
- target AC + active-effect AC + cover
- normal/long range
- line of sight

Natural 1 misses and natural 20 behavior are deterministic. The existing legacy `resolveAttack` endpoint now uses the shared v3.7 damage/effects primitives, including typed damage, temporary HP, mitigation, concentration, and death handling.

## 5. Saving throws and damage

A Save Activity supports:

- explicit DC or calculated spell DC
- target ability modifier
- target save proficiency
- effect-derived save bonuses
- advantage/disadvantage
- full, half, or zero damage on success
- failure-only effects

Damage order:

```text
raw typed damage
 -> immunity
 -> resistance
 -> vulnerability
 -> temporary HP
 -> normal HP
 -> concentration check
 -> unconscious/death handling
```

The returned result reports raw and applied totals, mitigation per part, temporary HP absorbed, remaining temporary HP, old/new HP, concentration outcome, and reaction results.

## 6. Effects and concentration

Effects persist in `rule_effects` and carry:

- source Activity and actor
- target
- name and optional condition
- structured modifiers
- stacking policy
- concentration ownership/group
- expiry rule
- active/end state and reason

Supported expiry boundaries:

- absolute world time
- turn start
- turn end
- short rest
- long rest
- combat end
- round count
- manual removal

Multiple effects created by one concentration Activity remain siblings. A later concentration Activity ends the prior concentration group without deleting its own newly created siblings. Damage makes the concentration saving throw at `max(10, floor(applied_damage / 2))`.

## 7. Reactions

Supported trigger vocabulary:

- `before_activity`
- `on_cast`
- `after_attack_roll`
- `after_hit`
- `before_damage`
- `after_damage`
- `before_save`
- `after_save`
- `turn_start`
- `turn_end`
- `on_rest`
- `on_death`

Automatic reactions are ordered deterministically by reaction priority, initiative order, owner identity, and reaction ID. The first eligible automatic reaction is selected in v3.7. It can consume the combat reaction and additional resources, cancel an Activity/damage, grant temporary HP, or apply a structured effect.

`selection_mode="prompt"` is intentionally rejected with an explicit continuation-not-implemented error. This preserves room for later player-choice continuations without pretending an asynchronous choice system already exists.

## 8. Action economy and movement

Per active combat actor, `rule_turn_state` tracks:

- action available
- bonus action available
- reaction available
- movement remaining

Action and bonus-action Activities require the actor's turn. Reactions remain available out of turn. Turn start restores turn resources and runs expiry/recovery/trigger handling.

`move` accepts an explicit contiguous path. It checks:

- current turn
- grid bounds
- adjacency
- blocking terrain
- occupied cells
- difficult-terrain cost
- remaining movement
- 100-cell reported path cap

The position update and movement spend commit atomically.

## 9. Resources, slots, and scaling

Structured resources support:

- current and maximum value
- optional partial recovery amount
- `turn_start`
- `short_rest`
- `long_rest`
- `dawn`
- `never`

Activity consumption is fully validated before any resource row is changed. Spell-slot consumption maps a selected slot level to `spell_slot:N`. Scaling supports additional parts per higher slot and level-threshold additions.

Concurrent use is serialized with `BEGIN IMMEDIATE`; a 1-charge Activity can succeed exactly once under concurrent calls.

## 10. Rest

Short and long rests are composite atomic transactions:

```text
validate actor / hit dice / prompt reactions
 -> advance WORLD time and simulation
 -> expire timed effects / process dawn recovery
 -> recover rest resources
 -> spend and roll hit dice or apply long-rest healing
 -> expire rest-bound effects
 -> resolve automatic rest reactions
 -> write ledger
 -> COMMIT
```

A post-tick failure rolls back world time, world simulation, recovery, and rest ledger together.

## 11. Death saves and advancement

Death-save state persists successes, failures, and stability. Natural 1, natural 20, three successes, and three failures are represented. NPC death continues through the existing lifecycle/succession system.

Advancement definitions can grant rule objects and resources at a class/level/rules-version boundary without hardcoding every class into Python.

## 12. SCENE geometry and temporary entities

Area targeting supports deterministic grid approximations for:

- radius/sphere
- cube
- line
- cone
- cylinder

Targets are selected before damage is applied. Supplied targets above their cap fail explicitly; area results return a truncation/cap report.

Summons materialize temporary NPC/SCENE/combat records, join initiative using an authored mode (`after_owner`, `roll`, or `end`), and are removed from initiative, scene, combat, actor rules, relationships, inventory, and ownership when their effect ends.

Transformations currently support safe temporary overrides of HP, maximum HP, and AC with snapshot restoration. This is a generic transformation primitive, not a complete encoding of every edition-specific transformation rule.

Teleportation supports combat-grid relocation or persistent WORLD location transfer. A WORLD teleport removes or inserts the actor in the active disposable SCENE consistently.

## 13. SCENE to WORLD

An Activity may set `world_event_type`. After exact rules resolution, the kernel writes the normalized event and passes it directly into the existing deterministic CASCADE queue in the same Activity transaction.

Example:

```text
ritual resolution
 -> world_event_type = ritual_failure
 -> CASCADE reaction
 -> location corruption state
 -> director/faction/deity consequences
```

The rule kernel does not hardcode one persistent-world handler per spell.

## 14. Rules versions

Campaigns are configured for `2014` or `2024`. Objects and Activities may be `2014`, `2024`, or `both`. Incompatible Activity use fails explicitly.

This is **version gating infrastructure**. It does not mean every 2014/2024 rules difference or content row has already been encoded.

## 15. Content boundary

Bundled official SRD content rows in v3.7: **0**.

The package contains an original mechanics demonstration script at `scripts/seed_rules_demo.py`. The engine can represent licensed or private content, but kernel mechanism coverage and rules-content coverage must be reported separately.

## 16. Important limitations

Not yet implemented as complete rules content/semantics:

- full SRD 5.1/5.2.1 data import
- hundreds of spell/feature/feat/monster/equipment definitions
- player-choice reaction continuation
- exact shape templates for every grid convention
- complete opportunity-attack/movement interrupt logic
- legendary/lair action pools
- complete death/dying variations for both editions
- complete transformation-specific overflow/carryover rules
- full spell preparation/known-spell legality
- material/somatic/verbal component restrictions
- dispel/counterspell-specific algorithms
- comprehensive class/subclass progression tables

Those are content and later conformance phases, not silently claimed by the generalized kernel.
