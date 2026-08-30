# WORLD ENGINE v3.7 — DETERMINISTIC TABLETOP-RPG RULES KERNEL BUILD DIRECTIVE

## 0. DOMAIN AND TERMINOLOGY

This is a **software-engineering task for a fictional tabletop role-playing game simulation**.

All references to attacks, damage, conditions, death, reactions, weapons, spells, creatures, combat, movement, saving throws, hit points, temporary hit points, concentration, recovery, and similar terms refer exclusively to **abstract D&D-style game mechanics applied to virtual game entities stored in the World Engine database**.

Do not interpret this work as physical-world planning or real-world operational guidance.

The implementation domain is:

**Python 3.11+ · SQLite · deterministic game-state resolution · tabletop-RPG rules modelling · simulation architecture · automated testing · GPT Actions/MCP integration.**

The target is the World Engine codebase already supplied for this project. Continue the latest verified working tree. If a partially implemented v3.7 rules-kernel worktree already exists, inspect and continue it rather than recreating the work from an older release.

---

# 1. PRIMARY OBJECTIVE

Build a **headless deterministic D&D 5e / 5.5e rules kernel** for World Engine.

The kernel must resolve player-facing mechanics authoritatively in Python and SQLite.

The language model may interpret player intent and narrate results, but it must **not calculate or invent authoritative mechanical outcomes**.

Required separation:

```text
PLAYER INTENT
    ↓
RULES KERNEL — RESOLVED
    ↓
authoritative SCENE result
    ↓
persistent consequence?
    ↓
WORLD / CASCADE — SIMULATED
    ↓
GPT — NARRATED

```

Do not merge these layers.

A RESOLVED mechanic must not become a recurring tick handler merely because it affects the world.

A SIMULATED world system must not be resolved by language-model improvisation.

Narration must not silently mutate authoritative state.

---

# 2. ARCHITECTURAL TARGET

Do **not** create one Python function per spell, feat, class ability, item, monster feature, or ritual.

Implement a generalized data-driven rules model.

Use this abstraction:

```text
RULE OBJECT
    │
    └── one or more ACTIVITIES
             │
             ├── activation
             ├── prerequisites
             ├── targeting
             ├── range / geometry
             ├── action economy
             ├── resource consumption
             ├── attack / save / automatic resolution
             ├── damage
             ├── healing
             ├── effects
             ├── duration
             ├── concentration
             ├── scaling
             ├── reactions / triggers
             ├── recovery
             └── persistent-world consequence

```

A RULE OBJECT may represent a spell, feat, class feature, subclass feature, species feature, monster feature, magic item, ritual, condition, or World Engine-specific fictional magic system.

The same underlying mechanics must be reusable across all of them.

---

# 3. FOUNDRY RESEARCH USAGE

Use the verified Foundry D&D5e `6.0.x` `AttackActivity` research as an **architectural and semantic reference**, not as a runtime dependency.

The relevant verified source family includes:

```text
module/documents/activity/attack.mjs
module/data/activity/attack-data.mjs
module/documents/activity/mixin.mjs
module/documents/item.mjs

```

The source investigation established that attack behavior spans the activity schema, Item→Activity invocation, roll configuration, actor modifiers, targeting, ammunition/resource mutation, damage handoff, hooks, and chat/result lifecycle. The original research checklist correctly requires tracing the complete workflow rather than merely reproducing the numeric d20 calculation.

Extract the reusable concepts.

Do not import Foundry UI, ChatMessage, browser globals, Actor/Item document runtime, sheets, dialogs, templates, `game.*`, or `CONFIG.*`.

Implement the useful semantics natively in Python/SQLite.

---

# 4. HARD ENGINEERING INVARIANTS

Every change must obey the following requirements.

All authoritative randomness must use the existing campaign-seeded deterministic RNG stream.

Do not introduce module-level `random.random`, `random.randint`, `SystemRandom`, or any independent random source.

A replay with the same database state, seed, and command sequence must produce the same results.

All authoritative game mutations must be atomic SQLite write transactions.

While a write transaction is open, do not call public WorldEngine methods that open another database connection. Use transaction-aware `_..._db` helpers and pass the active `db` connection downward.

Do not silently truncate targets, effects, reactions, entities, dice, event queues, or other bounded collections. Any cap must be reported in the returned result.

Every new behavior requires an automated regression test.

Do not reduce the existing test count or weaken existing assertions merely to make new code pass.

Do not introduce a permanent dense WORLD grid, fluid simulation, body-part/tissue simulation, temperature propagation, individual hauling, or other fortress-management mechanics. Those remain deliberately outside the product architecture. The project requirements explicitly distinguish the useful abstract world-activity layer from dense fortress simulation.

---

# 5. RULES DATA MODEL

Implement or finish a schema version migration containing generalized rules tables.

The minimum model should cover:

```text
rules_config
rule_actor_profiles
rule_objects
rule_activities
rule_actor_objects
rule_resources
rule_effects
rule_reactions
rule_turn_state
rule_advancements

```

Do not duplicate information already owned by existing character, NPC, combat, inventory, WORLD, SCENE, relationship, director, or lifecycle tables.

Rules tables should reference those authoritative entities.

Support explicit:

```text
rules_version = 2014
rules_version = 2024
rules_version = both

```

The campaign must have one active rules version.

An incompatible version-specific rule must fail explicitly rather than silently execute under the wrong edition.

---

# 6. REQUIRED ACTIVITY TYPES

The first generalized kernel must support these activity classes:

```text
attack
save
damage
heal
utility
summon
transform
teleport

```

Do not create separate subsystems for individual spells.

The activity resolver should receive structured data and perform deterministic resolution using the same common machinery.

---

# 7. ATTACK RESOLUTION

Implement attack resolution through the generalized Activity system.

The authoritative calculation must support:

```text
d20
+ ability modifier
+ proficiency where applicable
+ configured activity bonus
+ active-effect attack modifiers
+ situational modifiers

```

Then apply:

```text
advantage / disadvantage
critical threshold
target AC
active-effect AC modifiers
range
long range
cover
line of sight

```

Existing `resolveAttack` must remain backward compatible but must route its damage/state handling through the same shared rules machinery so it cannot bypass:

```text
temporary HP
damage resistance
damage immunity
damage vulnerability
active-effect AC
active-effect attack bonuses
concentration checks
death handling

```

Do not maintain two contradictory damage engines.

---

# 8. SAVING THROWS

Implement deterministic saving throws through the common Activity resolver.

Support:

```text
ability
DC
ability modifier
save proficiency
active save modifiers
advantage / disadvantage
success result
failure result

```

Support at minimum:

```text
full damage on failure
half damage on success
zero damage on success
effects on failed save

```

Do not enforce an artificial DC ceiling that prevents valid deterministic calculations such as high-damage concentration DCs.

---

# 9. DAMAGE

Damage must be a reusable common operation.

Resolve in this order:

```text
raw rolled amount
    ↓
damage immunity
    ↓
damage resistance / vulnerability
    ↓
temporary HP
    ↓
normal HP
    ↓
concentration check if applicable
    ↓
death / status consequences

```

Damage types must be structured values rather than prose.

Return both raw and applied damage.

Report exactly how much temporary HP absorbed.

Do not hide mitigation.

---

# 10. TEMPORARY HIT POINTS

Store temporary HP as deterministic actor rules state.

Incoming damage consumes temporary HP before normal HP.

Temporary HP must not behave as ordinary healing.

Replacing or granting temporary HP should follow explicit rule data rather than narration.

---

# 11. EFFECT ENGINE

Implement effects as structured persistent SCENE/rules records.

Each effect should support:

```text
source activity
source actor
target
name
condition
modifiers
concentration owner
duration / expiry trigger
active state
end reason

```

Support modifiers such as:

```text
AC bonus
attack bonus
damage bonus
saving-throw bonus
resistance
immunity
vulnerability
advantage
disadvantage

```

Effect expiry must support at minimum:

```text
world time
turn start
turn end
short rest
long rest
combat end
manual removal

```

Condition removal must not delete a condition still supplied by another active effect.

---

# 12. CONCENTRATION

Concentration is a first-class effect relationship.

A concentration activity may create multiple sibling effects.

Starting a new concentration activity must end previous concentration effects from that actor but must **not accidentally delete sibling effects being created by the same new activity**.

When a concentrating actor receives damage:

```text
DC = max(10, floor(damage / 2))

```

Resolve the appropriate Constitution saving throw through the normal saving-throw machinery.

On failure, end the concentration group.

Record the concentration check and effect termination in the ledger.

---

# 13. REACTION ENGINE

Implement deterministic reaction windows as generalized triggers.

Initial supported trigger vocabulary should include:

```text
before_activity
on_cast
after_attack_roll
after_hit
before_damage
after_damage
before_save
after_save
turn_start
turn_end
on_rest
on_death

```

A reaction record must contain:

```text
owner
trigger
priority
conditions
effect
resource/action-economy consumption
enabled state

```

Reactions must execute deterministically.

Do not use arbitrary language-model decisions for NPC reactions.

For the first implementation, an automatic policy may choose the highest-priority eligible deterministic reaction.

Design the return structure so player-choice reaction opportunities can be added later without rewriting the resolver.

Reaction recursion must remain bounded.

---

# 14. ACTION ECONOMY

Inside active combat, track:

```text
action
bonus action
reaction
movement remaining

```

Turn start restores appropriate per-turn resources.

Attempting to consume an already spent action/bonus action/reaction must fail before the authoritative activity commits.

Actions and bonus actions should normally require the actor to be the current combat turn.

Do not accidentally make out-of-turn reactions impossible.

---

# 15. RESOURCE SYSTEM

Generalize consumable mechanical resources.

Examples include:

```text
spell slots
hit dice
class-feature uses
item charges
custom ritual resources
reaction-like resources

```

A resource has:

```text
current
maximum
recovery policy
optional recovery amount

```

Initial recovery policies:

```text
turn_start
short_rest
long_rest
dawn
never

```

Resource validation and consumption must occur inside the same transaction as activity resolution.

An invalid activity must not consume its resources.

---

# 16. SPELL SLOT AND SCALING SUPPORT

Activity consumption should be able to request a spell-slot resource.

Support deterministic slot-level selection.

Support data-driven scaling such as:

```text
base activity level
additional damage/effect components per higher slot
character-level thresholds

```

Do not implement upcasting individually for every spell.

---

# 17. REST RESOLUTION

Implement short and long rests as RESOLVED mechanics that also advance WORLD time.

Short rest should support:

```text
world-time advancement
optional hit-dice spending
short-rest resource recovery
short-rest effect expiration
rest-trigger reactions
WORLD simulation during elapsed time

```

Long rest should support:

```text
world-time advancement
HP recovery
eligible resource recovery
death-save reset where applicable
long-rest effect expiration
rest-trigger reactions
WORLD simulation during elapsed time

```

The world must continue operating while the character rests.

---

# 18. DEATH SAVES

Implement deterministic death-save state.

Track:

```text
successes
failures

```

Handle:

```text
normal success
normal failure
natural 20 behavior
natural 1 behavior
stable state
death

```

Do not model death solely as `hp == 0`.

Integrate with the existing lifecycle/status system.

---

# 19. ADVANCEMENT

Implement data-driven advancement definitions.

An advancement can grant:

```text
rule objects
features
resources
resource maximum changes
metadata

```

Support class-level progression without hardcoding every class into Python.

This architecture must be capable of representing:

```text
class features
subclass features
feats
species features
custom World Engine abilities

```

Project Infinity's principal mechanical weakness is manual handling of several of these categories; this subsystem is intended to close that gap generically.

---

# 20. SCENE GEOMETRY

Reuse the existing disposable combat grid.

Do not create another spatial model.

The rules kernel should read existing:

```text
combat positions
cover
terrain
LOS blockers
grid distance

```

Support:

```text
normal range
long range
cover AC
blocked LOS
area targeting by radius

```

Keep the architecture extensible for cone, line, cube and cylinder targeting later.

Area targeting must produce an authoritative target list before damage/state application.

---

# 21. SUMMON / TRANSFORM / TELEPORT BOUNDARY

Implement these through the common activity schema and structured effects/events.

Do not build a separate AI subsystem.

Temporary summons belong to SCENE and disappear on expiry unless the authored rule explicitly creates a persistent entity.

Transformations must preserve enough information to restore the base state.

Teleportation must update authoritative location/SCENE state through existing movement/world mechanisms.

If a complete implementation cannot be finished safely in this phase, preserve the generalized schema and return an explicit `NOT_IMPLEMENTED`/unsupported result rather than inventing behavior.

---

# 22. SCENE → WORLD CONSEQUENCES

This is a mandatory integration point.

An Activity may define a normalized `world_event_type`.

After exact mechanical resolution, emit that normalized event and feed it directly into the existing deterministic CASCADE system **within the same authoritative transaction where practical**.

Example:

```text
ritual activity fails
        ↓
rule result
        ↓
world_event_type = ritual_failure
        ↓
existing CASCADE reaction
        ↓
location corruption increases
        ↓
director/deity/faction consequences

```

Do not hardcode a different world-integration handler for every spell.

Use normalized event categories.

Potential examples include:

```text
destructive_magic
ritual_failure
theft
public_necromancy
resurrection
summoning
property_damage
important_death

```

The existing World Engine design explicitly requires RESOLVED systems to write outcomes while SIMULATED systems consume their own state on ticks; preserve that boundary.

---

# 23. API SURFACE

Expose one high-level authenticated endpoint:

```text
POST /api/rules
operationId = runRulesKernel

```

Use an operation discriminator rather than consuming many GPT Actions.

Initial operations should include:

```text
configure
set_actor_profile
define_object
define_activity
grant_object
set_resource
define_reaction
resolve_activity
rest
death_save
list_effects
end_effect
get_actor_rules
define_advancement
apply_advancement

```

Maintain the GPT schema limit at exactly 30 visible operations.

If another low-level visual/admin write must become backend/MCP-only to preserve 30 operations, document that decision and do not remove the backend functionality.

Add an equivalent MCP wrapper.

---

# 24. CONTENT AND LICENSING BOUNDARY

The kernel is mechanics infrastructure.

Do **not** claim that implementing the kernel means all official D&D content has been imported.

Separate:

```text
RULE ENGINE COVERAGE

```

from:

```text
RULE CONTENT COVERAGE

```

Do not bundle copyrighted non-SRD sourcebook text merely because the engine can represent it.

Use properly licensed SRD material for bundled datasets when such import work is performed.

Custom/private user content may use the same kernel without changing the engine architecture.

---

# 25. REQUIRED TEST MATRIX

Add focused tests for at least:

```text
schema migration
2014/2024 rule gating
attack modifier composition
hit/miss
critical
advantage/disadvantage
cover
long range
save success/failure
save-for-half
damage immunity
damage resistance
damage vulnerability
temporary HP
spell-slot consumption
upcasting
concentration start
concentration replacement
concentration damage save
effect expiry
reaction changes attack outcome
reaction already consumed
action already consumed
AoE target resolution
short rest
long rest
resource recovery
death saves
advancement feature grant
existing resolveAttack compatibility
DB reopen determinism
SCENE result → WORLD CASCADE

```

Migration testing must include opening a prior schema-8 database and preserving its campaign rows while adding schema 9 rules tables.

All existing v3.6 tests must remain green.

---

# 26. BUILD ORDER

Begin by running the existing complete test suite and recording the baseline.

Then inspect the latest schema and transaction helpers.

Add `world_engine/rules.py` and the rules schema with a forward migration.

Implement Rule Objects, Activities, Actor Profiles and Resources.

Implement Effects and Concentration.

Implement Attack, Save, Damage and Heal resolution.

Implement Reactions and action economy.

Integrate existing `resolveAttack` with shared rules damage/effects behavior.

Implement Rest and Death Save mechanics.

Implement Advancement.

Connect normalized Activity outcomes to existing CASCADE.

Add `/api/rules` and MCP dispatch.

Add focused rules tests.

Run all existing and new tests.

Regenerate OpenAPI and verify exactly 30 operations with zero duplicate IDs.

Only then update documentation and package a new release.

---

# 27. DO NOT CLAIM COMPLETION EARLY

Do not call v3.7 a “complete D&D 5e engine” merely because the generalized kernel exists.

Report separately:

```text
kernel mechanisms implemented
mechanics tested
bundled rules/content imported
mechanics still unsupported
2014 coverage
2024 coverage
Project Infinity comparison

```

A generalized engine with three demo activities is not the same thing as hundreds of SRD spells/features being encoded.

State limitations explicitly.

---

# 28. SUCCESS CONDITION

The architectural success test is that a player-facing action can be resolved without the model inventing mechanics.

For example, after an appropriate spell/activity definition exists:

```text
player requests fictional spell action
        ↓
GPT identifies intended stored Activity
        ↓
runRulesKernel(resolve_activity)
        ↓
backend validates actor
        ↓
validates target / area / range
        ↓
validates action economy
        ↓
validates and consumes resources
        ↓
performs seeded rolls
        ↓
resolves saves / damage / healing
        ↓
applies resistances / temp HP
        ↓
checks concentration
        ↓
handles reactions
        ↓
updates SCENE
        ↓
handles death/lifecycle state
        ↓
emits normalized WORLD event
        ↓
runs immediate deterministic CASCADE
        ↓
commits
        ↓
GPT narrates returned facts

```

No authoritative arithmetic, targeting, effect duration, resource consumption, concentration, reaction outcome, or persistent consequence should depend on model improvisation.

That is the goal.

Begin by inspecting the current working tree and existing tests. Preserve working systems. Continue existing v3.7 work if present. Make the smallest correct implementation at each layer, add tests immediately, and do not package or claim completion until the full regression suite and clean migration tests pass.