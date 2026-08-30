# World Engine 4.0.2 Narrative Migration

## 1. Upgrade path

1. Back up `%LOCALAPPDATA%\WorldEngine\world_engine.sqlite3` and `launcher_config.json`.
2. Extract the complete 4.0.2 ZIP to a new folder.
3. Run `START_WORLD_ENGINE.bat` normally.
4. Startup reuses the persistent data directory; World Engine migrates SQLite `user_version` from 13 to 14 and creates 9 narrative tables with `CREATE TABLE IF NOT EXISTS`.
5. Import the regenerated permanent Action schema only when its public server URL differs from the schema already in GPT Builder.
6. Replace active GPT instructions with `CUSTOM_GPT_INSTRUCTIONS_V402.txt`.

The migration does not activate the legacy 1.63 prompt. Its exact bytes remain under `legacy/` for traceability.

## 2. Rollout states

```text
off → shadow → compare → enforce
```

- `off`: exact pre-4.0.2 narrative path.
- `shadow`: default; compiles/stores packets, baseline stays player-facing.
- `compare`: baseline stays player-facing; candidate is evaluated privately.
- `enforce`: candidate may be player-facing only through quality-check and accepted-output recording.

Rollback is immediate: configure `mode=off`. Schema 14 tables may remain unused; no destructive downgrade is needed.

## 3. Data conversion

Existing world facts, beliefs, relationships, NPC cognition, quests and events remain authoritative inputs. There is no automatic conversion of old prose into canonical facts.

Optional authoring work:

1. create voice profiles for major recurring NPCs using 2–5 original utterances;
2. author only high-value persistent storylets;
3. register motifs only when linked to a real arc/entity/event;
4. keep ordinary turns on ephemeral fallback beats;
5. do not import 1.63 Hilbert vectors or literal dialogue caches.

## 4. Promotion evidence

Before `enforce` becomes default, execute the 30-family benchmark and preserve:

- authoritative input packet/hash;
- baseline and candidate output hashes;
- hard correctness annotations;
- blinded ratings;
- latency and model/token cost;
- confidence intervals and agreement statistics.

A unit-test pass is necessary for release integrity but is not a narrative-quality win.
