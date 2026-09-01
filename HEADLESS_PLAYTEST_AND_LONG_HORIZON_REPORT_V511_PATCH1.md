# World Engine 5.1.1 — Headless Playtest and Long-Horizon Patch 1

Date: 2026-09-01  
Scope: confined headless-player contract, exact simulation catch-up, generated-world playtest, regression qualification

## Outcome

The repaired allowlist surface passes. A real generated level-1 campaign can be
observed and played through the confined process interface. Long controller-owned
time skips now roll up high-volume routine economy audit events when no active
game rule depends on their individual identity.

This preserves hourly economy state execution and automatically retains the full
event stream if an enabled simulation reaction, active quest event condition,
active agency goal, or incident suppression rule depends on either compactable
event type.

## Defects found and fixed

### WE511-PLAY-001 — Player time limit was not advertised

`advance_time` was listed as available, but the public contract did not expose
the PBEM limit of 0..1440 minutes. An LLM player could therefore issue a nominally
allowed one-year intent which predictably failed.

Fix: `allowed_intents.constraints.advance_time` now publishes the integer range,
the server-forced simulation flag, and the ignored player-owned weather, season,
and simulation overrides. The headless guide documents the controller/player
authority boundary.

### WE511-SIM-001 — Routine economy ledger explosion

An exact generated-world year took 282.87 seconds, grew the database to
117,194,752 bytes, and left 154,106 events. The two routine event types
`economy_consumption` and `economy_resource_extracted` accounted for about 97%
of that ledger.

Fix: catch-ups longer than seven days aggregate those two audit streams into one
typed rollup per source event type. Compaction fails open to individual events
when a stateful consumer depends on them. Hourly extraction, production, demand,
weather, daily population, agency, incident, quest, and other canonical cadence
work still executes.

## Confined-player playtest

Campaign label: `Sword Coast - Codex Campaign`  
Player character: Hesta Grey, level 1, 12 HP  
Roleplay choice: ranger-flavored traveler; class is not a canonical stored field
in this generated rules profile.  
Start: Juniper Ford  
Quest: `Road to High Gate`

This is procedurally generated World Engine content under a Sword Coast campaign
label, not a canon-complete Forgotten Realms content pack.

Observed and executed through `scripts/headless_player_v511.py` only:

| Action | Result | Wall time |
|---|---|---:|
| Dialogue with Hesta Cairn | completed | 1.197 s |
| Scouting check, +2 vs DC 12 | total 16, success | 1.057 s |
| Travel to High Gate | completed; quest completed | 1.128 s |
| Five additional checks | 4 success / 1 failure | 5.481 s total |
| Post-patch travel | completed | 0.724 s |
| Post-patch check, +2 vs DC 12 | total 14, success | 0.822 s |
| Post-patch one-day camp | completed | 0.932 s |

Normal end-to-end local headless turns measured about 0.7–1.2 seconds each on
this host, excluding any external LLM inference time. Player-owned time passage
is intentionally limited to one day per turn. A one-year player intent was
denied without revision or world-time mutation; a 1440-minute intent completed.

## Exact long-horizon measurements

Long horizons are controller-owned simulation operations, not player authority.
The kernel accepts at most 525,600 minutes per call, so ten years were executed as
ten committed 365-day calls.

| Exact workload | Before patch | Patch 1 |
|---|---:|---:|
| One generated-world year | 282.868 s | 36.796 s |
| Speedup | — | 7.69x |
| Database after one year | 117,194,752 B | 8,572,928 B |
| Event ledger after one year | 154,106 | 5,317 |
| Ten 365-day years | not run before patch | 549.836 s |
| Ten-year database | not run before patch | 60,841,984 B |
| Ten-year event ledger | not run before patch | 48,753 |

Ten-year per-year wall times were:

`36.649, 40.600, 44.126, 49.553, 52.481, 57.328, 61.698, 65.835, 68.790, 72.676 seconds`.

An independent run performed concurrently with the full regression suite took
635.755 seconds and produced the same revision, world time, database size, event
count, rollup counts, integrity result, and final invariant values. The table
uses the isolated run because it is the clean speed measurement.

The ten-year run completed at revision 26 and world time
`1502-01-01T08:00:00+00:00`. World Engine advances a requested year as exactly
365 days; it is not a calendar-year primitive.

Final ten-year invariants:

- SQLite integrity: `ok`
- Foreign-key errors: 0
- Economy consumption operations: 1,314,000 across ten years
- Economy extraction operations: 175,200 across ten years
- Consumption rollups: 10
- Resource-extraction rollups: 10
- Minimum stored population: 229
- Minimum resource quantity: 27.141666666666595
- Player HP: 12

## Verification

- Repaired allowlist surface: 13/13 passed
- New focused headless and long-horizon suite: 27/27 passed
- Simulation/economy/environment/stress/allowlist gate: 85 tests plus 17 subtests passed
- Complete repository suite: 827 tests plus 46 subtests passed
- Python compilation: passed
- `git diff --check`: passed
- Generated one-year SQLite integrity/FK: `ok` / 0
- Generated ten-year SQLite integrity/FK: `ok` / 0

The packaged source includes the machine-readable ten-year benchmark receipt at
`benchmarks/WORLD_ENGINE_V511_PATCH1_HEADLESS_HORIZON_10Y.json`. Its first-year
checkpoint is an independent 36.649-second measurement from the later playtest
state. The receipt records the input and output database SHA-256 values,
campaign/setup seed metadata, simulation
configuration, code hashes, git state, Python/SQLite/platform versions, every
yearly checkpoint, final invariants, and per-domain tallies.

The receipt's output database SHA-256 is
`7b444858cfc79ef9e0a7352b3be43c89cd7f46c6a89873e8b403a2a1270c7665`; an
independent hash of the generated 60,841,984-byte database matched it exactly.

The complete suite emitted one existing Starlette/httpx deprecation warning and
no test failures.

## Remaining boundaries

- The confined process interface and controller/player authority separation were
  exercised locally. No third-party hosted LLM API was invoked in this pass.
- Reported turn timings exclude LLM reasoning and narration latency.
- The generated Sword Coast label is not evidence of setting-canon completeness.
- Physical Windows double-click behavior, live tunnel providers, external GPT
  Builder configuration, live Foundry relay, and physical audio output remain
  outside this headless simulation pass.
