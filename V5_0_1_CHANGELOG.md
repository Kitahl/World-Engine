# World Engine 5.0.1 Changelog

World Engine 5.0.1 is a schema-24 bugfix release produced by post-build stress, play, and persistence qualification of 5.0.0.

## Fixed

- Serialized schema installation across threads and sibling local processes with a crash-releasing advisory lock.
- Made the complete additive schema migration rollback-atomic by removing implicit `sqlite3.executescript` commits from the engine, agency, and quest installers.
- Rejected `NaN`, positive infinity, and negative infinity at the canonical JSON persistence boundary.
- Made routed dialogue emit the canonical public `npc_interaction` event it declares.
- Changed new WEGEN-2.0 arrival objectives to match canonical character `movement` events.
- Added a narrow compatibility projection so already-instantiated 5.0.0 quests expecting `character_arrived` continue to work without rewriting stored quest graphs.
- Synchronized active quest graphs after successful mutating turns, with no idle revision when no quest is active.
- Prevented the per-database initialization lock registry from retaining abandoned database paths.

## Qualification additions

- Persistence/migration qualification, including staged schema 20–23 upgrades, injected late failure rollback, finite JSON, replay normalization, and chronological cursors.
- End-to-end ordinary and adverse playtests, including procedural generation, routed actions, quest completion, agency, simulation, secrecy, narrative publication, desktop projection, and reopen.
- Bounded stress tests for concurrent idempotent writes, 12-thread and eight-process construction, scheduler chunk equivalence, 250 quests, 300-event draining, 270 actors, and repeated reopen/integrity checks.

## Compatibility

- SQLite schema remains **24**.
- Public GPT Action count remains **5**.
- PBEM remains **2.2**.
- Narrative packet/receipt contracts remain **NRP-1.2 / NQR-1.2**.
- Desktop projection remains **WE-DESKTOP-5.0.0**; the application release identity is **5.0.1**.
