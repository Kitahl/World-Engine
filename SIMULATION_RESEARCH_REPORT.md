# World Engine v3.3 — Research-to-Implementation Report

## 1. Decision-selection findings

### Response curves
Verified as established Utility-AI practice. Dave Mark / Kevin Dill's GDC material explicitly describes response curves and weighted random selection as tools for utility-theory decision modelling.

v3.3 therefore stores the response curve **per need**, not globally. The available curve presets (`linear`, `quadratic`, `urgent`, `threshold`) are implementation presets. The specific campaign-design mapping:

- survival → threshold
- social / ambition → quadratic
- obligation → urgent

is **internal design guidance**, not claimed to be an industry taxonomy.

### Stochastic selection
Weighted-random utility selection is established prior art. Secondary documentation of The Sims 3 attributes a modified Boltzmann distribution to Richard Evans's action selection. The exact v3.3 policy — **softmax over top K with a keyed campaign seed** — is our deterministic engineering choice, not claimed as a universal standard.

Why it stays: it passes the supplied prototype's behavioral goal: same seed/config/time gives the same result, while different seeds can choose different near-optimal actions instead of forcing every identical NPC into lockstep.

### Smart-object / advertisement model
The Sims' “smart object” architecture is real: objects advertise capabilities that satisfy agent motives. This is highly relevant to scale because new objects can contribute possible interactions without hard-coding every object type into the agent.

v3.3 does **not** rewrite DECIDE into a full smart-object architecture yet. Actions are still stored per NPC, but the feasibility/need/graph model is compatible with adding location/item advertisements later if profiling shows the action table becomes the bottleneck.

### Commitment / hysteresis
Current utility-AI implementations document momentum/commitment and switch-margin/hysteresis as ways to prevent flip-flopping. v3.3 implements a minimum action commitment duration plus a small incumbent bonus. This solves the supplied “re-decide every tick and never finish” failure without adding a planner.

## 2. Spatial findings

The correct spatial target is not a persistent fortress grid.

v3.3 implements:

- **WORLD:** locations + coordinates + weighted graph links.
- **SCENE/COMBAT:** an ephemeral grid that exists only for active combat.

This allows real travel cost, graph LOD, road spread, combat range/cover, and tactical image staging without simulating empty tiles across the entire world.

## 3. Cascade findings

The supplied prototype's four load-bearing properties are retained:

1. FIFO/BFS ordering.
2. deterministic reaction ordering.
3. depth cap plus total-event cap.
4. loop/repeat control.

Two prototype defects were explicitly fixed:

- same-location reaction selection excludes the event target and `hp <= 0` NPCs;
- repeat suppression is configurable per reaction (`once_per_cascade` or `count_limited`) so independent repeated damage-like effects are not wrongly suppressed.

## 4. Thin-system findings

### Items/economy
No full economy was built. Item definitions and inventories are persistent, and local STOCK produces a bounded scarcity multiplier:

`scarcity = clamp(1 - qty / capacity, 0, 1)`

`price = base_price × (1 + scarcity)`

No local stock definition means neutral/base price rather than invented scarcity.

### Lifecycle
A thin lifecycle record stores birth year, up to two parents, spouse, alive state and mortality parameters. Optional mortality uses a simple Gompertz-Makeham-style hazard. This is not a full demographic/fertility/succession simulator.

### Drama manager
The drama manager is a modifier over CHANCE rather than an autonomous story generator. Recent hardship and low party HP can suppress threat-role events and boost relief-role events. The resulting event still comes from the deterministic seeded simulation.

## 5. What remains intentionally absent

- dense global pathfinding grid
- fluids / temperature / hauling
- tissue/body-part combat
- full macroeconomy
- full procedural world generation
- unconstrained LLM-controlled world mutation

## 6. Evidence status

External research validates utility response curves, weighted random selection, smart-object advertisements, social-history reasoning, and pacing/director concepts. It does **not** validate internal LOC estimates or numerical “percentage of Dwarf Fortress” coverage scores. Those remain engineering heuristics.
