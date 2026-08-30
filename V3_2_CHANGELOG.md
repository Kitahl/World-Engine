# World Engine v3.2.0 Changelog

## Added
- `DECIDE` utility AI archetype for NPC action selection.
- `CASCADE` bounded same-tick effect/reaction queue.
- Cause-bearing `relationship_events`.
- `recent_social_history` in `getWorldContext`.
- Persistent NPC needs, actions, simulation rules, resource nodes, agent decision state, and reactions.
- Deterministic keyed stochastic CHANCE/SPREAD outcomes.
- Boundary-aware long-time catch-up.
- One-click simulation configuration through the existing 30-operation GPT Action budget.
- 200-agent/year benchmark script.

## Correctness hardening
- Stable stochastic outcomes across one-shot vs chunked time advancement.
- Stable CHANCE stream when unrelated random rules are added.
- DRIFT shares exact-boundary priority ordering with DECIDE/SPREAD/CHANCE.
- Repeated unchanged DECIDE actions do not replay transition reactions unless `emit_on_continue=true`.
- Year-1492 cadence math avoids platform timestamp functions.
- One-shot catch-up final state is regression-tested against daily chunking.

## Retained
- v3.1.2 fail-closed API authentication.
- SQLite write serialization/concurrency fixes.
- 30-operation GPT Actions cap.
- persistent visual profiles and image continuity.
