---
doc_id: WE31-BUILD-002
title: Two Layers, the Grid Question, the 11 Absent Systems, and a Codex Guide for DECIDE + CASCADE
companion_docs: [WE31-GAP-001, WE31-TIME-001, WE31-SIM-001]
artifacts: [decide.py (148 lines), cascade.py (117 lines)] — both written and tested here
date: 2026-08-28
evidence: §1–§4 are [MEASURED] from test runs shown inline. §6 search leads are
          [UNVERIFIED] — recalled, not searched.
---

# 1. TWO LAYERS — YES, BUT NOT DF's TWO LAYERS

DF splits **fortress** (dense 3D grid) and **world** (abstract, batched). Copying that split
literally is wrong for you, because you have no fort. Your correct split is:

| | **Layer 1 — WORLD** | **Layer 2 — SCENE** |
|---|---|---|
| Scope | Every region, all NPCs, factions | Where the player is standing, right now |
| Lifetime | Permanent | Created on scene start, folded back on scene end |
| Tick | Day (near) / fortnight (far) | Per action / per combat round |
| Entities | 40–60 named per location + aggregates | ≤12 |
| Space | **Region graph** (adjacency + travel hours) | **Tactical grid**, ~20×20, only if in combat |
| Detail | Needs, jobs, relationships, stocks | Positions, cover, initiative, LoS |
| Who drives it | The tick engine | The player and the model, turn by turn |

The important property: **Layer 2 is disposable.** It materialises from Layer 1 on arrival and folds
back on exit — the same promote/demote mechanic as LOD. You never persist a 20×20 grid for 300
locations; you persist one per *active* combat.

---

# 2. THE GRID QUESTION — you have none, and you need one and a half

**Verified against the schema:** `characters.location` and `npcs.location` are TEXT strings.
`locations` has no coordinates. `combats` stores participants and initiative but **no positions**.
There is no spatial representation anywhere in v3.1.

Split the question three ways — they have completely different cost/benefit:

## 2.1 World grid — **BUILD IT. Cheap, high value.**

```sql
ALTER TABLE locations ADD COLUMN x REAL;      -- or hex q,r
ALTER TABLE locations ADD COLUMN y REAL;
CREATE TABLE location_links (
  campaign_id TEXT, from_id TEXT, to_id TEXT,
  travel_hours REAL NOT NULL, road_quality TEXT DEFAULT 'road',
  PRIMARY KEY(campaign_id, from_id, to_id));
```

Two columns and one table. What it unlocks that you cannot currently do:

- **Travel time is real** — `advanceWorld` derives hours from the route instead of the model guessing.
- **SPREAD gets geography.** Right now rumours travel only along the relationship graph. With
  adjacency they also travel by road, at road speed. That is the difference between a rumour that
  teleports and a rumour that *arrives*.
- **LOD tiers assign themselves** — near/mid/far becomes graph distance from the player, not a manual tag.
- **Faction borders, migration, raid range, trade routes** all become computable instead of narrated.
- **Proximity in DECIDE stops being a magic constant.** My `decide.py` currently hardcodes
  `off-site = 0.6` because there is no distance to measure. With links it becomes `1/(1+travel_hours)`.

## 2.2 Tactical grid — **BUILD IT, scoped to active combat only.**

```sql
CREATE TABLE combat_positions (
  campaign_id TEXT, combat_id TEXT, actor_kind TEXT, actor_id TEXT,
  x INTEGER, y INTEGER, cover TEXT DEFAULT 'none',
  PRIMARY KEY(campaign_id, combat_id, actor_kind, actor_id));
CREATE TABLE combat_terrain (
  campaign_id TEXT, combat_id TEXT, x INTEGER, y INTEGER,
  kind TEXT,                      -- wall/difficult/water/hazard
  PRIMARY KEY(campaign_id, combat_id, x, y));
```

Rows exist only while a combat is active; `endCombat` deletes them. A 20×20 grid with 12 actors is
~412 rows and dies within the hour. This is what makes cover, flanking, opportunity attacks, and area
spells *mechanically real* rather than narrated — and it also fixes 1.63's broken ASCII map, because
now there is actual data to render instead of the model improvising a picture.

## 2.3 Dense fortress grid — **DO NOT BUILD.**

Fluids, temperature diffusion, item hauling, room quality, pathfinding at building scale. Different
program. Your player experiences prose, not tiles. Nothing here survives narration.

**Verdict: "one and a half grids."** World graph (~30 lines) + combat grid (~80 lines). Not the third.

---

# 3. THE 11 ABSENT SYSTEMS — TRIAGED

From the coverage table. You are right that you do not need them all — **you need four, want three,
and should skip four.**

## BUILD (4) — cheap, and they are what make it feel alive

| # | System | Lines | Why |
|---|---|---:|---|
| 11 | **DECIDE** — utility AI | ~150 | Built and tested below. Turns a timetable into a life. |
| 12 | **CASCADE** — consequence chains | ~120 | Built and tested below. Where DF's depth actually comes from. |
| 13 | **REASONS** — why a relationship changed | ~30 | Highest narrative value per line in the whole project. One table. |
| 20 | **DRAMA MANAGER** — pace events against player state | ~60 | RimWorld's signature. A weighting function over CHANCE rules you already have. |

## BUILD SMALL (3) — thin versions only

| # | System | Thin version | Skip the rest |
|---|---|---|---|
| 14 | Item tracking | `item_defs` + `inventories` with a parseable `effect_dice` (Depth §6.1) | quality tiers, wear, decay, ownership chains |
| 16 | Spatial | §2.1 world graph + §2.2 combat grid | dense fortress grid |
| 9 | Lifecycle | age, birth year, parents, spouse, death roll from an age curve — 5 fields | full demographic pyramids, migration cohorts |

## SKIP (4)

| # | System | Why skip |
|---|---|---|
| 15 | Full economy | **You get ~80% free.** STOCK already tracks quantities; add `price = base * (1 + scarcity)` where scarcity comes from the stock level. One formula. Agent-based markets buy the last 20% for ten times the work and are invisible in play. |
| 17 | Fluids / temperature / fire propagation | Cellular automata on a grid you are not building |
| 18 | Body-part / tissue combat | Enormous, invisible in prose |
| 19 | Worldgen | **Don't build — import.** Generate once with an external tool or by hand, load it, simulate forward. Worldgen is a one-time cost you can pay with someone else's software. |

**Net: ~7 systems, roughly 400 lines beyond what exists, plus the grids.**

---

# 4. CODEX BUILD GUIDE — DECIDE (6th archetype)

`decide.py` is attached: **148 lines, written and tested here.** Treat it as the reference
implementation. Everything below is what Codex needs to know to extend or rebuild it.

## 4.1 The model

```
needs      agent holds need_key -> 0..100   (100 = desperate). Needs RISE each tick.
actions    each advertises {need_key: amount_satisfied} and declares requirements.

score(A) = Σ over needs [ urgency(need_value) × (satisfies/100) × personality_weight ]
           × feasibility(A)      ← HARD GATE, 0 or 1
           × proximity(A)

selection  = seeded softmax over the TOP-K, NOT argmax.
```

## 4.2 Schema

```sql
CREATE TABLE agent_needs (
  campaign_id, agent_kind, agent_id, need_key,
  value REAL CHECK(value BETWEEN 0 AND 100), rise_per_day REAL DEFAULT 5,
  PRIMARY KEY(campaign_id, agent_kind, agent_id, need_key));

CREATE TABLE actions (
  campaign_id, id, name,
  location_id,                       -- NULL = performable anywhere
  requires_json,                     -- {"item":{"flour":1},"world_state":{"market_open":true}}
  satisfies_json,                    -- {"coin":40,"pride":30}
  cost_hours REAL, tags_json,
  PRIMARY KEY(campaign_id,id));

CREATE TABLE agent_weights (         -- personality
  campaign_id, agent_kind, agent_id, need_key, weight REAL DEFAULT 1.0,
  PRIMARY KEY(campaign_id, agent_kind, agent_id, need_key));
```

## 4.3 Response curves — **per need, not global. This is the design's load-bearing decision.**

```python
CURVES = {
  "linear":    lambda v: v/100,
  "quadratic": lambda v: (v/100)**2,             # ignorable until it isn't — social, pride
  "urgent":    lambda v: (v/100)**0.5,           # matters immediately — duty, fear
  "threshold": lambda v: 0 if v<60 else (v-60)/40,  # ignored, then panic — hunger, thirst, sleep
}
```

**A test caught this.** With one global `quadratic` curve:

```
hunger= 95  duty=80  -> best=patrol   [('patrol', 0.384), ('eat', 0.379)]
```

**A guard at 95/100 hunger kept patrolling.** Switching hunger to `threshold`:

```
hunger= 70  duty=80  -> best=patrol   [('patrol', 0.384), ('eat', 0.175)]
hunger= 95  duty=80  -> best=eat      [('eat', 0.612), ('patrol', 0.384)]
```

**Rule for Codex: survival needs use `threshold`, social/ambition needs use `quadratic`, obligation
uses `urgent`.** Store the curve name per need, not per engine.

## 4.4 Selection — softmax over top-K, never argmax

Argmax makes every baker in the city identical and makes the world snap between states in lockstep.
Softmax over the top 3 with the ticker's seeded RNG gives variety *and* replayability:

```
seed 11 run A: ['a2','a2','a3','a2','a2','a2']
seed 11 run B: ['a2','a2','a3','a2','a2','a2']   ← identical
seed 99      : ['a2','a1','a1','a1','a3','a1']   ← different world
```

Temperature 0.25 is a reasonable start. Higher = more chaotic, lower = closer to argmax.

## 4.5 Determinism requirements — non-negotiable

- Iterate agents in `ORDER BY id`. Iterate actions in `ORDER BY id`.
- Tiebreak on `(-score, action_id)`, never on dict/set iteration order.
- Use the ticker's seeded `random.Random`. **Never `random.random()` module-level.**
- No `datetime.now()` inside scoring.

## 4.6 Verified behaviours — Codex must keep all four passing

**T1 — feasibility gate (the whole point):**
```
HAS flour  -> [('bake',0.163), ('market',0.022), ('idle',0.003)]   best='bake'
NO flour   -> [('market',0.022), ('idle',0.003)]                   best='market'
```
The baker without flour goes to the market. That single behaviour is the difference between a
schedule and a life.

**T2 — personality:**
```
greedy    -> [('work',0.324), ('tavern',0.022)]     best='work'
sociable  -> [('tavern',0.324), ('work',0.022)]     best='tavern'
```
Same town, same needs, same actions, different lives.

**T3 — urgency curves:** §4.3 above.

**T4 — determinism:** §4.4 above.

## 4.7 Known limitations in the reference implementation

- `proximity` is hardcoded `0.6` for off-site. Replace with `1/(1+travel_hours)` once §2.1 exists.
- No action *duration* — `cost_hours` is stored but unused. Multi-day actions need an
  `in_progress` state so an agent can't start a new job every tick.
- No group actions (two NPCs cooperating). Out of scope; do not add speculatively.

---

# 5. CODEX BUILD GUIDE — CASCADE (7th archetype)

`cascade.py` attached: **117 lines, written and tested here.**

## 5.1 The model

Handlers stop applying effects directly. They **emit** into a queue; a drain loop processes it, and
processing may emit more.

```python
queue = deque(seed_effects)
while queue and applied < max_effects:
    effect = queue.popleft()                    # FIFO -> BFS
    if (effect.type, who, target) in seen: continue   # LOOP GUARD
    seen.add(key); apply(effect); applied += 1
    if effect.depth >= max_depth: continue      # DEPTH CAP
    for rule in reactions(effect.type) ORDER BY id:   # DETERMINISTIC
        for who in selected(rule.selector, effect):
            if rng.random() <= rule.probability:
                queue.append(build(rule, effect, depth+1))
```

## 5.2 Four correctness properties, all load-bearing

1. **BFS, not DFS.** Same-generation consequences resolve together, so the ledger reads *"the death
   caused five people to grieve"* rather than one deep unreadable thread.
2. **Deterministic ordering.** Reactions sorted by rule id; queue is strict FIFO.
3. **TWO caps, not one.** `max_depth` stops recursion; `max_effects` stops breadth explosion. A death
   with 50 friends is already 50 effects at depth 1 — depth alone will not save you.
4. **Loop guard on `(event_type, who, target)`.** Without it, A grieves → B grieves → A grieves,
   forever.

## 5.3 Verified output — one death, three rules

```
stats: {'applied': 17, 'loops_suppressed': 17, 'depth_truncated': 6, 'budget_dropped': 0}

  0  death    Victim is killed at the ford
  1  grief    brann grieves for victim
  1  grief    kell grieves for victim
  1  grief    mira grieves for victim
  1  grief    sera grieves for victim
  1  grief    tomas grieves for victim
  2  tantrum  kell breaks something in anger
  2  tantrum  mira breaks something in anger
  2  tantrum  sera breaks something in anger
  ...
  3  fear     mira is frightened by the outburst
```

**Three reaction rules turned one event into seventeen.** Every line lands in the ledger the model
already reads. That is DF's depth mechanic, in 117 lines.

## 5.4 Safety verified — a self-triggering rule terminates

Rule `r2` was deliberately written as `grief → grief`, an infinite loop:

```
depth cap = 2          -> {'applied': 6, 'loops_suppressed': 25, ...}
budget cap = 8         -> {'applied': 6, 'loops_suppressed': 25, ...}
depth 6 / budget 200   -> {'applied': 6, 'loops_suppressed': 25, ...}
```

All three configurations terminated. `loops_suppressed: 25` is the guard doing its job.

## 5.5 Determinism verified

```
seed 5 A: [death:None, grief:brann, grief:kell, grief:mira, grief:sera, grief:tomas, tantrum:kell]
seed 5 B: [death:None, grief:brann, grief:kell, grief:mira, grief:sera, grief:tomas, tantrum:kell]
seed 42 : [... , tantrum:mira]
```

## 5.6 Two bugs the tests exposed — Codex must fix both

1. **The `same_location` selector does not exclude the target.** Test output contains
   `fear | victim is frightened by the outburst` — the dead man got scared. Selectors must exclude
   `effect.target` and the dead.
2. **The loop-guard key may be too coarse.** `(event, who, target)` means a person can experience
   each event type at most **once per cascade**. Correct for `grief`; probably wrong for `damage`,
   where two separate tantrums should both break something. Consider a per-event-type policy:
   `once_per_cascade` vs `count_limited(n)`.

## 5.7 Integration into `sim.py`

Add `cascade` as archetype 7. The other six emit seeds instead of applying directly; `cascade.run()`
drains at the end of each tick, **inside the same `BEGIN IMMEDIATE` transaction** so a partial cascade
can never commit.

---

# 6. SEARCH BLOCK A — UTILITY AI *(revised, with method)*

All leads below are **[UNVERIFIED]** — recalled, not searched. Names may be wrong or dead.

**What I now want, having built it:** the reference implementation works, so the searches are no
longer "how do I do this" but four specific open questions.

| # | Open question | Queries | Bar |
|---|---|---|---|
| 1 | **Curve libraries.** Which response curve for which need class? I picked 4 by intuition and one was wrong on first test. | `"infinite axis utility system" response curve types` · `utility AI curve shapes game AI pro` · `Dave Mark behavioral mathematics utility` | A named taxonomy of curves mapped to need classes |
| 2 | **Selection policy.** Is softmax-over-top-K the accepted answer, or is there something better than my `temperature=0.25` guess? | `utility AI weighted random top N selection vs argmax` · `dual utility reasoner selection` | A stated rationale, not just a formula |
| 3 | **The Sims advertisement model.** Objects advertise scores to nearby agents — the inverse of my design, where agents score actions. Which scales better at 300 NPCs? | `The Sims object advertisement needs architecture design` · `Sims 2 motive engine advertised score` | A clear statement of the tradeoff |
| 4 | **Action duration / commitment.** How do you stop an agent re-deciding every tick and never finishing anything? §4.7's known hole. | `utility AI action commitment hysteresis inertia` · `AI oscillation prevention behaviour switching cost` | A named technique (I expect "commitment bonus" or "hysteresis band") |

**Domains, ranked by expected yield:** *Game AI Pro* volumes 1–4 (several chapters believed free
online; I believe Dave Mark wrote the utility chapters) → GDC AI Summit talks → RimWorld modding docs
on ThinkTrees/JobGivers → The Sims design retrospectives → r/roguelikedev FAQ Friday archives →
RogueBasin.

**Method note:** run these searches *after* reading `decide.py`. You now have a working baseline, so
the useful question is "what does the literature do differently and why," not "how does this work."
That is a much better search than the one I gave you last time.

# 7. SEARCH BLOCK B — CASCADE *(revised)*

The pattern is implemented and terminating, so only two questions remain worth searching:

| # | Question | Queries |
|---|---|---|
| 1 | **Loop-guard granularity** (§5.6.2). Is there a standard policy for "which events may repeat within one cascade"? | `event cascade deduplication policy simulation` · `forward chaining rule engine cycle detection` · `Rete algorithm truth maintenance` |
| 2 | **Budget policy.** When you hit the cap, is dropping the tail right, or should you sample the queue so late-generation effects aren't systematically lost? | `bounded event propagation fairness simulation` · `priority queue effect budget game simulation` |

Both are refinements. Neither blocks shipping.

---

# 8. BUILD ORDER

| # | Task | Lines | Depends on |
|---|---|---:|---|
| 1 | Fix the two cascade bugs (§5.6) | ~15 | — |
| 2 | Per-need curve column (§4.3) | ~10 | — |
| 3 | World graph: coords + `location_links` (§2.1) | ~30 | — |
| 4 | Wire `proximity` to travel_hours | ~5 | 3 |
| 5 | `relationship_events` — reasons (§3 BUILD) | ~30 | — |
| 6 | Action duration / commitment (§4.7) | ~40 | — |
| 7 | Integrate DECIDE + CASCADE into `sim.py` | ~40 | 1, 2 |
| 8 | Combat grid (§2.2) | ~80 | — |
| 9 | Drama manager | ~60 | — |

**Items 1–2 first — they are bugs in code you already have, found by tests that already exist.**
Everything else is new surface, and new surface on top of known bugs is how you get a v3.1 audit.
