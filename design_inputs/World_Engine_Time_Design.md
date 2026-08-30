---
doc_id: WE31-TIME-001
title: Class-C Simulation Locally — Real Time, Game Time, or Time Skip?
companion_docs: [WE31-SIM-001, WE31-DEPTH-001]
artifacts: [sim.py (163 lines), clock.py (110 lines)] — both working and tested
date: 2026-08-28
short_answer: Yes, locally, on a laptop. But "real time vs time skip" is the wrong axis. The right
              question is WHAT DRIVES THE TICK, and the answer is an anchor, not a heartbeat.
              Dwarf Fortress itself does not tick continuously off-screen — it advances the world
              in fixed chunks. [CITED]
---

# 1. THE REFRAME

You framed it as three options — game time, time skip, or real time. They are not three options.
Two of them collapse into one, and here is why.

**Anything real-time on a laptop must survive the laptop.** You close the lid. It sleeps. The process
dies. You reboot. Every single one of those creates a gap between what the world *should* be and what
it *is*. So a real-time daemon needs "figure out how much time passed and catch up" logic **anyway**.

Which means: if you build the catch-up engine, you get time-skip for free and real-time becomes an
optional 15-line wrapper that calls the same function on a timer. If you build the daemon first, you
have to build catch-up second and you've written it twice.

> **Build the catch-up engine. Never build the daemon first.**

## 1.1 What Dwarf Fortress actually does — worth knowing before copying it

[CITED — Dwarf Fortress Wiki, *World activities*] After worldgen, DF continues developing the world
off-screen. <cite index="99-1">This is called "world activation," and the calendar is advanced by two weeks at a time.</cite> Historical figures marry, have children, work, and die; sites are conquered,
destroyed, and founded. <cite index="99-1">And for movement across the world map, the wiki notes plainly that some things simply teleport.</cite>

Three things follow from that:

1. **DF is not continuous off-screen.** It batches. Two-week chunks.
2. **DF abstracts aggressively where the player cannot see.** It does not pathfind a caravan across
   the continent; it moves it.
3. **The famous depth is in the ledger, not the tick.** DF's Legends mode is people reading the
   *history* — and you already have an event ledger that produces exactly that.

[CITED — Tarn Adams, PC Gamer 2017] Adams describes the hard problem as <cite index="97-1">having two places loaded at once that are not simply contiguous parts of one region</cite>. That is the level-of-detail problem, stated by
the author. It is the constraint, not an implementation detail.

**You do not need to out-engineer DF. You need to copy its two cheats: batch the ticks, abstract the
distance.**

---

# 2. WHY GRANULARITY IS THE WHOLE GAME

Cost of simulating one game year, by tick size:

| Granularity | Ticks/year | Events @1%/tick | Events @10%/tick |
|---|---:|---:|---:|
| real-time (1s) | 31,536,000 | 315,360 | 3,153,600 |
| minute | 525,600 | 5,256 | 52,560 |
| hour | 8,760 | 87 | 876 |
| **day** | **365** | **3** | **36** |
| week | 52 | 0 | 5 |
| **DF: 2 weeks** | **26** | **0** | **2** |
| month | 12 | 0 | 1 |

A 32K context holds roughly 800 short event lines. **Anything finer than "day" overflows the ledger
the model reads**, unless you roll events up (§4).

This is why real-time-per-second is not a design choice you get to make. It is 31.5 million ticks a
year for a world where the player experiences maybe 200 scenes. The resolution has nowhere to go.

---

# 3. THE ANCHOR PATTERN — recommended

Instead of a heartbeat, store two values on the campaign:

```
anchor_real = 2026-08-28T14:00:00Z     -- real timestamp of last sync
anchor_game = 1492-06-14T08:00:00      -- game time at that moment
ratio       = game minutes per real minute
```

Then, at the top of **every read**, compute the gap and simulate it before answering:

```python
def ensure_current(cid):
    days = (now_real - anchor_real) * ratio / 1440
    if days >= 1:
        digest = ticker.advance(cid, days=min(days, max_catchup_days))
        update anchor
        return digest          # "here is what happened while you were away"
```

Nothing runs in the background. Nothing to crash, nothing to daemonize, nothing to keep alive. The
world is *correct on read*, which is the only moment correctness matters.

`clock.py` implements this — **110 lines**, tested.

## 3.1 Three modes, one mechanism

| Mode | `ratio` | Behaviour | Best for |
|---|---|---|---|
| `paused` | 0 | Time moves only inside a scene | Tight, dramatic arcs where a month must not pass between sessions |
| `skip_only` | 0 | Time moves only on explicit `advanceWorld(days=N)` | **Default.** You control the pacing; downtime is a deliberate act |
| `anchored` | >0 | Game time tracks wall-clock | The world moves between sessions on its own |

All three run the same code path. Switching is one UPDATE.

## 3.2 Picking a ratio — my first guess was badly wrong

I set `ratio = 1440` (1 real hour = 1 game day) and tested it:

```
player returns after   0.5 real hours ->  30.0 game days pending
player returns after   6.0 real hours ->  90.0 game days pending  (hit the cap)
```

**Half an hour away and a month has passed.** You would never see your world; you would only ever
read summaries of it. Corrected table:

| Mapping | `ratio` | Drift after a 2-week real break |
|---|---:|---:|
| paused | 0 | 0 days |
| **1 real day = 1 game day** | **1** | **14 game days** |
| 1 real day = 1 game week | 7 | 98 game days |
| 1 real hour = 1 game day | 1440 | 336 game days ❌ |

**Recommendation: `skip_only` as the default, `ratio=1` if you want an ambient world.** And always
set `max_catchup_days` (default 90 in `clock.py`) so a six-month gap does not try to simulate 180
days on your first API call.

---

# 4. THE DISPLAY PROBLEM — "shown based on game time"

This is the half of your question that matters more than the tick.

If 90 game days pass, the ledger fills with events. The model cannot read them all — and would not
want to. So the return value of a catch-up is **not the events. It is a digest.**

Measured, from the working prototype (90 game days, 6 NPCs, 4 rules):

```
raw events generated          : 47
lines the model actually reads: 12 headlines + 2 rollup counters
rolled_up : {'sim_drift': 31, 'world_advance': 1}
headlines :
 - sim_spread: 'knows_the_secret' spread to 2 more people
 - crime: a purse cut in the market
 ...
token cost: raw ledger ~564  vs  digest ~161   (71% smaller)
```

The rule is simple: **notable event types render verbatim; everything else becomes a counter.**
`crime`, `disaster`, `death`, `birth`, `war`, `sim_spread` are headlines. Thirty-one relationship
drifts are one line saying `sim_drift: 31`.

At a full year of hourly ticks this is the difference between an unreadable 8,760-line wall and
fifteen lines the model narrates as *"while you were away: two floods, a theft in the dock quarter,
the smith's daughter married, and the rumour about you reached the temple."*

Which is Legends mode. That is the feature.

---

# 5. LEVEL OF DETAIL — how you get DF scale on a laptop

The second DF cheat. Three tiers, assigned per region:

| Tier | Where | Cadence | What is simulated |
|---|---|---|---|
| **near** | Player's location ±1 | hourly | Individual NPCs, routines, full detail |
| **mid** | Same region | daily | Named NPCs only; population as aggregate |
| **far** | Everywhere else | fortnightly (DF's number) | **Faction aggregates only. No individual NPCs.** |

Measured cost of one game year, 300 entities:

```
flat  (300 entities, hourly)                  : 2,628,000 handler-ops
LOD   (40 hourly / 60 daily / 8 fortnightly)  :   372,508 handler-ops
reduction                                     :        85.8%
```

## 5.1 Aggregates solve your 130,000-person city

You measured earlier that the context ceiling is ~40–60 NPCs per location. LOD is how you get past it
without lying:

```
individual rows : 130,000 npc rows                        -> impossible
aggregate row   : {pop:130000, unrest:0.2, wealth:0.6,
                   food:0.8, garrison:400, guilds:[...]}   -> 1 row
named NPCs kept : ~30 the player has actually met
```

**Promotion / demotion is the trick.** When the player travels to a far region:
1. Run its deferred aggregate forward to the current game time (cheap — it's one row).
2. **Materialise** individual NPCs *from* the aggregate — if `unrest=0.8`, the NPCs you generate are
   angry, and that is not arbitrary, it is derived.
3. On leaving, **demote**: fold those NPCs back into the aggregate, keeping only the ones the player
   named or interacted with.

The city was always 130,000 people. It was just stored as five numbers until you looked.

---

# 6. WHAT THIS COSTS YOU

| Piece | Lines | Status |
|---|---:|---|
| 5-archetype ticker (`sim.py`) | 163 | ✅ built, tested |
| Anchor + catch-up + digest (`clock.py`) | 110 | ✅ built, tested |
| LOD tiers + promote/demote | ~120 | design above, not built |
| Wire into `advanceWorld` + `getWorldContext` | ~40 | not built |
| **Total** | **~430** | **~2 days** |

Two new GPT Actions: `setTimePolicy` and `getWorldDigest`. That takes you from 33 operations to 35.

---

# 7. RECOMMENDATION

**Do this:**
1. `skip_only` mode as the default. You call `advanceWorld(days=N)` when the fiction says time passes.
2. Day-granularity ticks for near, fortnightly for far. Copy DF's number; it is load-bearing, not arbitrary.
3. Digest on return, always. Never hand the model a raw ledger after a skip.
4. `max_catchup_days = 90`.

**Then, if you want an ambient world:** flip to `anchored` with `ratio = 1`. Same code path, one
UPDATE. Your world will be about two weeks older each time you come back from a two-week break, which
is enough for a harvest to come in, a feud to cool, and a rumour to reach the far side of the city.

**Do not build:** a background daemon, per-second ticks, or a websocket that streams world events at
you. All three cost real complexity and none of them changes what you experience, because you only
ever perceive the world at the moment you read it.

**The honest summary:** what makes Dwarf Fortress feel deep is not the tick rate. It is that the
history is *legible* — you can read what happened and why. You already have the ledger that produces
that. The tick is 163 lines and the clock is 110. The hard part was the part you already built.
