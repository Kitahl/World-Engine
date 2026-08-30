# World Engine v3.4 — Current Architecture Comparison

Date: 2026-08-29

This is a feature/architecture comparison, not a benchmark ranking. Scores are internal engineering assessments where used, not external measurements.

## Closest current projects

### Project Infinity
Source: https://github.com/electronistu/Project_Infinity

Strongest relative area: deterministic D&D 5e rules execution. Its README documents external dice/state resolution, spell attacks, saving throws, spell slots, criticals, kill detection, XP and multi-target AoE.

World Engine advantage: off-screen world simulation, causal social history, WORLD/SCENE materialization, hierarchical local authority/director stacks, and ChatGPT GPT Actions integration.

Priority conclusion: borrow its rules-engine discipline, not its overall architecture.

### Mnehmos Engine
Source: https://github.com/Mnehmos/mnehmos-engine
License: MIT.

Strongest relative area: generalized autonomous multi-agent world with LLM agents, persistent memory, scarcity/labor economy, retries/corrections and learned constraints.

World Engine advantage: deterministic utility DECIDE does not require an LLM for every NPC; stronger replayability, D&D-specific gameplay surface, smaller Python/SQLite core, and explicit disposable active scenes.

Priority conclusion: Mnehmos is the best conceptual comparison for "world lives without the player," but World Engine should retain non-LLM off-screen decisions.

### Bunnyland
Source: https://github.com/thalismind/bunnyland-server
License: AGPL-3.0-or-later.

Strongest relative area: mature generic ECS/action surface, humans and LLM controllers using the same validated actions, multiple clients, plugins, MCP and observability.

World Engine advantage: much narrower TTRPG/ChatGPT target, compact SQL model, deterministic utility/world simulation without requiring autonomous LLM controllers, explicit causal relationship ledger and D&D resolver path.

Priority conclusion: study its validation/observability boundaries, but do not adopt its whole server architecture or AGPL code under the project's current license policy.

### llm-fortress
Source: https://github.com/kttalley/llm-fortress

Strongest relative area: dense active-world simulation — pathfinding, animals, weather accumulation, local agent cognition, hunting, crafting and a 13-stage tick.

World Engine advantage: does not pay that simulation cost for every inactive location; WORLD remains graph/aggregate state while SCENE is materialized only where the player is. Better fit for one adventurer in narrated play.

Priority conclusion: this is deliberately outside World Engine's permanent-world spatial scope.

### llm-rpg-world-simulator
Source: https://github.com/subho004/llm-rpg-world-simulator

Strongest relative area: Python/FastAPI persistent RPG world, NPC goals/memory, economy, rumours and emergent quests, with LLM intent/planning behind deterministic validation.

World Engine advantage: off-screen agent choice is deterministic and seedable without an LLM; stronger separation of simulation from narration and current ChatGPT Actions-specific deployment.

### Project Lunar / Narrative Engine
Sources:
- https://github.com/horizonfps/project-lunar
- https://github.com/Sagesheep/NarrativeEngine-P

Strongest relative area: narrative memory/retrieval, scenario authoring, broad LLM-provider support and long-form campaign continuity.

World Engine advantage: authoritative game-state mutation and deterministic world mechanics rather than primarily narrative-memory management.

## Relative position after v3.4

| Capability | World Engine v3.4 | Project Infinity | Mnehmos | Bunnyland | llm-fortress |
|---|---:|---:|---:|---:|---:|
| Deterministic 5e rules depth | 6 | 10 | 1 | 2 | 2 |
| Persistent canonical state | 10 | 9 | 9 | 10 | 8 |
| Off-screen deterministic world activity | 9 | 3 | 7 rule-fallback / LLM-first | 9 | 9 |
| Causal social-history ledger | 9 | 4 | 8 | 7 | 7 |
| WORLD graph / travel | 8 | 4 | 8 | 8 | 10 dense map |
| Disposable active SCENE layer | 9 | 5 | interaction zones | world ECS | persistent dense local grid |
| Regional authority / deity director stack | 10 | 2 | DM LLM | storyteller/plugins | events/history |
| ChatGPT GPT Actions fit | 10 | 2 | 2 | MCP/server | 1 |
| Native scene image cue continuity | 9 | image model support | 2 | 2 | sprite/browser |
| Local dense physics/pathfinding | 3 intentionally | 4 | 4 | 7 | 10 |

Scores are internal design-fit estimates, not external benchmark results.

## Strategic conclusion

World Engine should not turn into a generic multi-agent sandbox. The defensible architecture is:

1. Authoritative persistent DB.
2. Deterministic RESOLVED gameplay operations.
3. Deterministic SIMULATED off-screen world activity.
4. WORLD graph for geography/travel/LOD.
5. Disposable SCENE state for the player's active area.
6. Disposable combat grid materialized from SCENE.
7. Causal ledger and relationship reasons.
8. Hierarchical director stack: civic → faction → realm → divine/power, scoped to place.
9. LLM restricted to NARRATED output and interpretation.
10. ChatGPT-native image generation fed by authoritative scene/world state.

The largest remaining competitive deficit is full deterministic D&D 5e resolution, where Project Infinity is materially ahead.
