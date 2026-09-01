# World Engine 5.0.1 — Post-Build Qualification Report

## Verdict

World Engine 5.0.1 passes the post-build stress, persistence, bug, and play qualification performed against the 5.0.0 runtime-convergence release. The qualification found seven production defects or hardening gaps: four high-impact runtime/integrity defects, two medium-impact normalization/serialization defects, and one low-impact resource-retention defect. All seven were corrected and converted into durable regression tests.

The release keeps SQLite schema version 24, the five-operation public Action surface, PBEM 2.2, NRP/NQR 1.2, and the WE-DESKTOP-5.0.0 projection contract. The application and package release identity advance to 5.0.1.

## Final source qualification

| Gate | Result |
| --- | --- |
| Complete pytest regression | **641 passed** |
| unittest subtests included in that run | **42 passed** |
| Combined new qualification suite | **16 passed + 8 subtests** |
| Python compilation | **PASS** |
| Release-blocking Ruff rules (`E9,F63,F7,F82`) | **PASS** |
| v5.0.1 release verifier | **PASS** |
| Static public Action audit | **PASS — exactly 5 operations** |
| Narrative release audit | **PASS** |
| SQLite schema/integrity | **v24 / integrity OK / no FK violations** |

The complete regression took 484.02 seconds on the qualification host. It emitted one Starlette `TestClient` dependency-deprecation warning; this is not a World Engine runtime failure. A broader style-only Ruff scan also reports pre-existing import-order findings across the repository. Those findings were kept separate from defect qualification and were not mass-rewritten in this bugfix release.

## Defects found and fixed

### 1. Concurrent database construction could race

Multiple threads or sibling processes could enter schema installation for the same new database at once. The engine now serializes initialization with a per-database in-process lock plus a crash-releasing adjacent OS advisory lock. Qualification exercised 12 simultaneous threads and eight sibling processes, repeated three times, with no constructor failures, deadlocks, integrity damage, or foreign-key violations.

### 2. A late schema error could leave a partially installed upgrade

`sqlite3.executescript` can introduce implicit transaction boundaries, so the previous outer transaction did not make the entire additive engine/agency/quest installation rollback-atomic. Schema execution now parses complete SQLite statements and executes them within the explicit `BEGIN IMMEDIATE` transaction. An injected failure near the end of installation now restores tables, data, and `PRAGMA user_version` together.

### 3. Procedural quest objectives were disconnected from canonical play events

Generated arrival objectives expected the legacy `character_arrived` shape while normal movement emitted canonical `movement` events. New WEGEN-2.0 quests now consume canonical movement. A narrow compatibility projection lets already-created 5.0.0 graphs continue to complete without rewriting stored quest graphs. Routed dialogue now also emits the declared canonical `npc_interaction` event.

### 4. Public quest progression needed stronger trust and transaction boundaries

Public dialogue now requires a real local NPC; forged public `world.event.commit` requests are denied; no-op movement emits no event or revision; and active quest graphs advance automatically after successful mutating turns without allocating an unrelated second revision. Event draining processes up to four bounded 256-event batches in the current transaction and returns an explicit backlog warning if more work remains.

Arbitrary event commit remains available only as a trusted internal/administrative seam. It is not accepted from the public PBEM Action path.

### 5. Non-finite JSON values could enter persistence

The canonical JSON serializer now rejects `NaN`, positive infinity, and negative infinity, including nested occurrences. This prevents non-standard numeric values from being written into JSON-backed runtime state.

### 6. Whitespace-equivalent politics replays could consume a revision

Politics replay preflight now applies the same actor/request identifier normalization as dispatch. Case, object ordering, aliases, and surrounding whitespace all resolve to the same idempotent request without advancing the campaign revision.

### 7. The initialization-lock registry retained abandoned database paths

The in-process lock registry now uses weak ownership. A probe that opened and discarded 20 distinct databases returned the registry to its starting size after collection.

## Stress qualification

| Scenario | Load and invariant | Observed result |
| --- | --- | --- |
| Concurrent idempotent delivery | 48 deliveries | Collapsed to 16 economic transactions; money and inventory conserved exactly; 1.049–1.122 s; 0.150–0.154 MiB traced peak |
| Whole vs chunked scheduler | Equivalent full-stack simulation | Durable state and state-affecting tallies matched; whole 0.404–0.513 s, chunked 0.430–0.533 s |
| Quest/event pressure | 250 quests, 500 transitions, 300 queued events | Queue drained; integrity preserved; 4.482–4.831 s; 1.414 MiB traced peak |
| Actor/reopen pressure | 270 actor candidates and repeated reopen | Runtime caps held at 256/200; clean reopen/integrity; 6.226–6.894 s; 0.224–0.239 MiB traced peak |
| Schema construction | 12 threads and 8 processes | Three repeated passes; schema 24, integrity OK, no FK violations |

The whole/chunked runs can report different per-call observation counters such as `agency_actors` and `quest_events_processed` because the calls have different request boundaries. Durable state and the counters that affect state were equivalent; the observation-counter difference is not state corruption.

Memory values above are Python `tracemalloc` peaks and do not include all SQLite/native allocations. Database files in these scenarios ranged from about 1.96 to 2.81 MiB.

## Persistence and migration qualification

- Reopened fresh and upgraded databases without losing committed state.
- Exercised staged schema 20, 21, 22, and 23 upgrades to schema 24.
- Verified late-failure rollback across engine, agency, and quest installers.
- Verified finite JSON enforcement and chronological cursor behavior.
- Verified exact idempotent replay after identifier and payload normalization.
- Verified SQLite integrity and foreign keys after qualification workloads.

The schema 21–23 fixtures are down-staged current databases. They prove the migration control flow and rollback boundary, but they do not reproduce every historical table-shape difference. Pre-schema-20 historical packages were not part of this release gate.

## Play qualification

- Ran an ordinary generated campaign through staging, validation, promotion, movement, local NPC dialogue, agency, simulation, executable quest completion, narrative publication, public projection, desktop projection, and database reopen.
- Compared adverse/chunked execution with the ordinary path for durable equivalence.
- Proved that forged public quest events and dialogue with a remote NPC are denied.
- Proved that no-op movement and unrelated checks do not consume a revision.
- Drained a 257-event backlog and reached the current dialogue event.
- Repeated the complete playtest file three times and the highest-risk integrity cases three times.
- Checked player-facing results for private sentinel leakage; none was observed.

## Deliberately unverified boundaries

The following require environments or failure injection outside this qualification host and are not claimed as passed:

- Actual Windows double-click launch, Service Control Manager installation/recovery, and graphical `pywebview` rendering.
- Live ngrok, Cloudflare, Tailscale, and Foundry relay connectivity/accounts.
- Forced process termination or power loss at arbitrary WAL/fsync boundaries.
- Database path aliases, hardlinks, and network-filesystem advisory-lock semantics.
- Recovery behavior after more than 1,024 quest events remain pending in one turn.
- Every historical pre-schema-20 database shape.

The packaged-artifact handoff records the final source commit, ZIP hash, archive inventory, and independent clean-extracted verification results.
