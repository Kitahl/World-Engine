# World Engine v3.9.3 — Authoritative Game-State Coverage

World Engine is authoritative only for mechanisms it actually implements. This release does not claim complete D&D 5e/5.5e content coverage.

## Character and progression state
Tracked and reportable to ChatGPT:
- current/max HP, AC, location, status/death state;
- ability/resource state and generic inventories;
- spell/resource recovery through the rules kernel;
- structured effects/conditions and concentration;
- death saves and stability;
- XP or milestone progression mode;
- cumulative XP, XP-to-next-level, eligible/pending/current level;
- milestone count;
- pending level-up and synchronized advancement completion;
- currency balances, item rewards and faction-reputation rewards.
- normalized reward inventory and currency balances are included in normal character/world-context readback.

A threshold crossing never silently invents class/subclass/feat/spell decisions. It reports a pending level-up, and the configured rules advancement mechanism applies supported grants after choices are resolved.

## NPC state
Tracked:
- persistent identity/profile/gear;
- importance class;
- relationships, beliefs, goals and memories;
- causal mood thoughts;
- needs and drift;
- routines/jobs/reservations;
- DECIDE results and bounded GOAP planning;
- cognition snapshot with dominant motive reasons;
- lifecycle/succession and faction associations.

## World / scene / combat
Tracked mechanisms include persistent world time/state, graph travel, sparse 3D map/portals/z-levels, factions/directors, quests/plots, rumors, crime/bounty, production, population/migration, climate/divine/affliction state, SCENE materialization/foldback, combat position/initiative/action economy, attacks/saves/damage/healing and persistent consequences.

## Known bounded/incomplete areas
See `RULES_COVERAGE_MATRIX.md`. In particular, complete player-choice reaction continuation, complete official SRD content, some tactical movement interrupts, exact every-shape AoE semantics, and every special-case summon/transformation rule are not claimed complete.
