---
doc_id: WE31-SIM-001
title: Simulating Everything Stored — the 5-Archetype Tick, What's Worth Copying, and a Deep-Search Brief
companion_docs: [WE31-DEPTH-001, WE31-AUDIT-001]
artifact: sim.py (163 lines, working, tested against the v3.1 schema)
date: 2026-08-28
short_answer: Yes, and it is smaller than you think. Almost everything you store updates by one of
              FIVE patterns. Write 5 generic handlers driven by a rules table instead of 20 bespoke
              ones. Working prototype: 163 lines. Nothing worth copy-pasting exists; the borrowable
              things are DATA and PATTERNS, not code.
---

# 1. THE EASY WAY — FIVE ARCHETYPES

The instinct is "I need a herbalism system, an economy system, a routine system, a crime system, a
gossip system…" — 20 handlers, each a project. That is the wrong decomposition.

Look at what actually changes in your database and you find **five update shapes**. Every system on
your 47-item list is one of them:

| # | Archetype | Formula | Covers |
|---|-----------|---------|--------|
| 1 | **DRIFT** | `x → x + k·(baseline − x)` | relationships cooling, reputation fading, bounty heat dying down, wounds healing, grudges softening |
| 2 | **SCHEDULE** | `entity.field = table[hour]` | NPC routines and jobs, shop hours, guard patrols, temple services, market days |
| 3 | **STOCK** | `x → clamp(x + rate·dt)` | herb regrowth, food stores, faction reserve_score, coin, ore veins, population, mana wells |
| 4 | **CHANCE** | `if rand() < p: emit(event)` | disasters, crime, births, deaths, caravans, weather shifts, random encounters |
| 5 | **SPREAD** | one graph hop per tick | rumour propagation, disease, panic, faction tension, heresy, fashion |

Five handlers. A `sim_rules` table saying which rule uses which archetype with which parameters. Then
"add herbalism" is **an INSERT, not a code change.**

## 1.1 Proof — it runs, on your existing schema

`sim.py` is attached: **163 lines**, seeded, transactional, reading and writing only tables v3.1
already has, logging to the `events` ledger you already trust.

Test world: Waterdeep, 8 NPCs with jobs and routines, 2 factions, 8 herb nodes, a relationship graph,
7 rules. Advanced 30 days:

```
  day 0    smith@home     trust(pc,smith)= 40   harpers_rep= 8   herb0= 0.0   rumour_known_by=1
  day 30   smith@forge    trust(pc,smith)= 10   harpers_rep= 8   herb0= 7.2   rumour_known_by=9

  tally: {'drift': 325, 'schedule': 8, 'stock': 240, 'chance': 1, 'spread': 8}
  events readable from recent_events: {sim_growth: 30, sim_drift: 24, sim_spread: 5,
                                       crime: 1, sim_routine: 1, world_advance: 1}

  sample "what happened while you were away":
   - crime      | a theft in the dock quarter
   - sim_spread | 'knows_pc_is_a_necromancer' spread to 3 more people
```

Compare to v3.1 as shipped, where 30 days changed exactly one field (`world_time`). Here: the smith
went to work, the herbs regrew at autumn rate, an untended friendship cooled from 40 to 10, a theft
happened, and **your secret reached 9 people who did not know it before** — through the relationship
graph you already built, without the model deciding any of it.

## 1.2 Determinism comes free

```
seed 7  run A: 13 crimes    run B: 13 crimes    seed 99: 7 crimes
```

Because the ticker takes its own seeded `random.Random`, the whole simulation is replayable and
diffable. This also closes audit §3.4 — store the seed on the campaign row and the entire world
becomes reproducible, which is the one thing no off-the-shelf tool gives you.

## 1.3 Two calibration bugs the test exposed — reported, not hidden

1. **Integer rounding eats small drift.** `harpers_rep` never moved: `k=0.03 × (0−8) = −0.24`, which
   rounds to 0 every single day, forever. Fix: keep a float accumulator column, or set `k` so that
   `k·range ≥ 1`. Any `k < 1/range` is a silent no-op.
2. **Day-granularity ticks break routines.** `sim_routine` fired once, not 30 times, because
   advancing by whole days leaves `hour` constant, so nobody ever "goes home." Routines need hourly
   ticks. Fix: tick hourly for the first N days, daily beyond, so a "one year passes" call does not
   generate 8,760 events.

Both are 10-minute fixes. They are the kind of thing that only shows up when you run the code, which
is the argument for building the small version first.

## 1.4 Effort

| Piece | Lines | Time |
|---|---:|---|
| 5 archetype handlers + rules table | ~165 | done — attached |
| Fix the two calibration bugs above | ~20 | 30 min |
| Hourly/daily tick budgeting | ~30 | 1 hr |
| `items` + `inventories` tables (Depth §6.1) | ~60 | half a day |
| Wire `advanceWorld` to the ticker + 2 new Actions (`listSimRules`, `saveSimRule`) | ~50 | 2 hrs |
| **Total** | **~325** | **~1.5 days** |

That is the entire gap between "a save file" and "a world."

---

# 2. CAN WE COPY-PASTE OTHER TOOLS?

Short answer: **not for this.** Here is the honest per-candidate assessment.

| Candidate | License | Status | Verdict |
|---|---|---|---|
| **Open5e API / data** | CC-BY 4.0 (2024 SRD) | live, maintained | ✅ **YES — paste the DATA.** 339 spells, 330 creatures, 56 rules sections. The single highest-value external thing. Not code — content. |
| **Mesa** (agent-based modelling) | Apache-2.0 | v3.5.1 Mar 2026, 3.8k stars, Mesa 4 in pre-release | ⚠ A framework you'd restructure *around*, not paste *in*. It owns the scheduler, the agent model, and the data collection. Adopting it means rewriting your engine as Mesa agents. Read its scheduler design; don't vendor it. |
| **SimPy** (discrete-event sim) | MIT | v4.x, stable since 2002 | ⚠ Its own docs say it is *"overkill for simulations with a fixed step size where your processes don't interact"* — which is exactly your case. Borrow the generator-based event-queue idea if you later want hour-resolution; don't add the dependency now. |
| **GOAP implementations** | mixed | `pygoap` self-describes as *"very early stages"*; `dogoap` is Bevy/Rust; `GPGOAP` is C; `godot-goap` is GDScript; `GameReadyGoap` is C# | ❌ **No usable Python drop-in.** All are engine-bound or stale. GOAP is also the wrong tool — it plans *how an agent achieves a goal*, which you don't need. Archetype 2 (SCHEDULE) gets you jobs for 15 lines. |
| **py_trees** (behaviour trees) | BSD | maintained, ROS ecosystem | ❌ Same reason. Behaviour trees solve per-agent decision-making in real time. Your NPCs need to be somewhere and want something, not to make tactical choices. |
| **SillyTavern extensions** | **AGPL-3.0** | active | ⚠ **License hazard.** AGPL is viral over a network — if you vendor AGPL code into a service you expose over a tunnel, the obligations follow. Read them for design; do not copy code into your repo. |
| **networkx** | BSD-3 | mature | ✅ Worth adding if SPREAD gets more complex than one BFS hop. Not needed yet. |

## 2.1 The conclusion that matters

**There is no drop-in "NPC lives simulator" for Python.** I looked. What exists is either a generic
academic ABM framework (Mesa), a generic event scheduler (SimPy), or per-agent tactical AI
(GOAP/behaviour trees) — none of which is the thing you want, and all of which cost more integration
effort than the 163 lines above.

**What IS worth taking from outside:**
1. **Data** — Open5e (free, CC-BY, no auth, covers 2024 and 2014 SRD).
2. **Patterns** — Mesa's scheduler design, SimPy's event queue, `resource_nodes`-style stock-and-flow
   from any colony sim.
3. **Nothing else.**

Your engine's shape is already right. The missing piece is small and specific, and vendoring a
general-purpose framework to get it would be a net loss.

---

# 3. DEEP-SEARCH BRIEF

Everything below is written to be run as-is. For each block: the queries, what to extract, and the
pass/fail bar. **Reject anything that fails the license or maintenance gate regardless of how good it
looks.**

## 3.0 Universal gates — apply to every result

| Gate | Requirement | Why |
|---|---|---|
| **License** | MIT / BSD / Apache-2.0 / CC-BY / CC0 only. **Flag GPL, reject AGPL** for anything vendored into a networked service. | AGPL obligations follow a tunneled service |
| **Maintenance** | commit within 12 months, or explicitly "stable/complete" | dead sim code is a trap |
| **Dependencies** | must run on Python 3.11+ with no game engine, no GUI, no C build toolchain | your runtime is FastAPI + SQLite |
| **Extractability** | can you lift one file/algorithm, or is it entangled with a framework? | you want ~300 lines, not a rewrite |

## 3.1 PRIORITY 1 — Content/data to ingest (highest value, lowest risk)

```
open5e API bulk download JSON dump srd-2024
5e SRD 2024 CC-BY monsters spells JSON github
open source herbalism alchemy ingredient table 5e OGL JSON
OGL creative commons trade goods price list medieval economy dataset
fantasy settlement name generator open data CC0
```
**Extract:** download URL, record counts, license string, schema shape, whether a bulk dump exists
(vs. paginated API only).
**Bar:** CC-BY or freer, machine-readable, ≥100 records in its category.
**Deliverable to bring back:** a table of `source | records | license | bulk-dump URL | fields`.

## 3.2 PRIORITY 2 — Tick / scheduling patterns (read, don't vendor)

```
stock and flow simulation python sqlite tick loop github
"catch-up" simulation offline progress algorithm game server
hourly tick budget large time skip simulation game design
Mesa scheduler RandomActivationByType source
```
**Extract:** how they handle a large time skip without generating N events; how they decide tick
granularity; how they persist partial ticks.
**Bar:** an actual algorithm you can restate in a paragraph.
**This is the single thing I most want answered** — how to make "one year passes" cheap. My §1.3 fix
(hourly for N days, daily beyond) is a guess, not a researched answer.

## 3.3 PRIORITY 3 — Rumour / information propagation

```
information diffusion model social network SI SIR python implementation
rumour spreading algorithm game NPC gossip network github
independent cascade model linear threshold model python
```
**Extract:** the propagation rule, decay/distortion handling, and whether it needs a full graph in
memory.
**Bar:** works on ≤500 nodes, no heavy deps.
**Why:** your relationship table is already a weighted graph. This is the cheapest place to get
emergent behaviour that feels alive, and my one-hop BFS is the crudest possible version.

## 3.4 PRIORITY 4 — Economy / production

```
open source medieval economy simulation supply demand python github MIT
market price equilibrium simulation agent based trading pit github
production chain simulation resource nodes game server
```
**Extract:** the price-update rule, how they avoid runaway inflation, how many entity types before it
gets slow.
**Bar:** ≤500 lines for the core, no engine dependency.
**Caveat:** economies are the easiest system to over-build and the least visible in play. Time-box
this one.

## 3.5 PRIORITY 5 — Verify the audit's open questions

```
Foundry MCP bridge ChatGPT connector support 2026
OpenAI ChatGPT MCP connectors consumer availability 2026
GPT Actions maximum number of operations limit
FastAPI sqlite BEGIN IMMEDIATE concurrent write pattern
```
**Why:** the v3.1 audit left two items **[UNVERIFIED]** — whether the Foundry bridges work against
ChatGPT rather than Claude Desktop, and current MCP connector support on consumer ChatGPT. Also:
whether **33 GPT Actions** is near any documented ceiling, since that affects whether you can add
`saveSimRule` / `listSimRules` / `grantItem` / `consumeItem` (→ 37).

## 3.6 EXPLICITLY DO NOT SEARCH FOR

Time-wasters, based on §2:
- "Python NPC life simulator library" — does not exist
- GOAP / behaviour tree / utility AI libraries — wrong problem, all engine-bound
- LLM-driven NPC agent frameworks — reintroduces the nondeterminism you removed by building v3.1
- "Dwarf Fortress source code" — closed source
- General ABM frameworks beyond confirming Mesa's design — you already know the answer

## 3.7 Return format

For each candidate, one row:

```
name | url | license | last commit | LOC of the part you'd take | deps | what it gives you | extractable Y/N
```

Then one line per priority block: **"best option, or none found."** "None found" is a real and useful
result — §2 above is mostly a list of none-founds, and knowing that is what makes the 163 lines the
right call.

---

# 4. RECOMMENDED ORDER

1. **Run `sim.py` against a copy of your real campaign DB.** Half an hour. It will tell you more than
   any search, because it will show you which rules produce interesting events and which produce
   noise.
2. **Fix the two calibration bugs (§1.3).** 30 minutes.
3. **Then** run §3.1 (Open5e ingest) and §3.2 (tick budgeting) — the two searches with the clearest
   payoff.
4. Add `items`/`inventories` (Depth §6.1) so plants become real.
5. Everything else is optional.

Do not run the whole search list before writing code. The prototype is cheap enough that it should
come first, and it will change what you search for.
