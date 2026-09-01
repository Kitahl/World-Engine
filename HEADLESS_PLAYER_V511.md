# World Engine 5.1.1 headless player

World Engine 5.1.1 includes a process-oriented adapter for running a disposable,
generated campaign without a browser or desktop UI. It is suitable for a human
operator, Codex, or another LLM controller that can invoke a command, read one
JSON response, choose an action, and invoke the next command.

The boundary is intentionally split:

- `new` is a **controller/setup phase**. It creates the local database, generates
  a deterministic WEGEN-2.0 world, stages it, validates it, runs the bounded
  authoring dry-run, promotes it, and plants two private confidentiality canaries.
- `observe` and `act` are the **player phase**. They expose only a closed
  player-facing projection and a narrow positive action allowlist. They do not
  expose `WorldEngine`, SQLite, files, raw events, world truth, NPC cognition,
  authoring, admin methods, context packets, or private narrative validation.

## Quick start

Use the package's private Python interpreter when available. The session path is
always explicit; the adapter never silently selects the normal World Engine DB.

```powershell
.\.venv\Scripts\python.exe scripts\headless_player_v511.py new `
  --session-dir C:\Temp\world-engine-headless-demo `
  --seed my-demo-world
```

The command prints exactly one bounded JSON object. Read
`observation.campaign.revision`, then submit typed player text plus one normalized
intent. For example, replace `headless__location_02` with an adjacent location
from `observation.world_map.links` and replace `REVISION` with the observed
revision:

```powershell
.\.venv\Scripts\python.exe scripts\headless_player_v511.py act `
  --session-dir C:\Temp\world-engine-headless-demo `
  --text "I follow the road toward the next settlement." `
  --intent-json '{"type":"move","parameters":{"destination":"headless__location_02"}}' `
  --expected-revision REVISION `
  --idempotency-key player-turn-001
```

Read the next state at any time:

```powershell
.\.venv\Scripts\python.exe scripts\headless_player_v511.py observe `
  --session-dir C:\Temp\world-engine-headless-demo
```

Use a new idempotency key for each distinct turn. Retrying the exact same `act`
command returns the recorded turn without applying it twice. A stale revision
fails with `REVISION_CONFLICT`; observe again before choosing the next action.

## LLM driver contract

Give the player model only these capabilities:

1. Run `observe` for the fixed session directory.
2. Read the returned JSON.
3. Choose only an intent type advertised by `allowed_intents`.
   Obey any per-intent limits in `allowed_intents.constraints`; player
   `advance_time` is limited to 0..1440 minutes per turn, always simulates, and
   cannot override weather or season. Longer controller-owned catch-up remains
   an administrative operation rather than a player capability.
4. Run `act` with its own natural-language player text, normalized intent JSON,
   the exact observed revision, and a fresh idempotency key.
5. Repeat from the returned `observation`.

Do not give the player model a Python import surface, database access, filesystem
tools, or the controller-only `new` command if adversarial confinement is a
requirement. The adapter is a **capability and disclosure boundary**, not an
operating-system sandbox. A process that can independently read the session's
SQLite file is not confined by this adapter. Put an untrusted model in an OS
sandbox/container and grant only the `observe`/`act` command broker.

## Enforced safety properties

- Output is one JSON object with a hard 64,000-character limit.
- The observation is a strict allowlist over `DesktopProjectionKernel`.
- Player intents are a positive allowlist; authoring, admin/configuration, raw
  event commits, direct consequence writers, legacy caller-authored attacks,
  and unknown future capabilities are rejected before engine mutation.
- `act` always binds the actor to the generated player character and always
  invokes the existing turn router with `enforce_pbem=True`.
- `rules.generic` is limited to already-authored, PBEM-reviewed operations.
- Every action requires an exact expected revision and a bounded idempotency key.
- Turn output is re-projected: context packets, capability plans, raw provider
  results, PBEM audit internals, exception text, and private data are omitted.
- Each response is scanned against a private SECRET event canary and a private
  NPC-belief canary stored only in the campaign DB. A match fails closed.
- A cross-process file lock serializes session commands around the existing
  SQLite/turn transaction path.

## Qualification

Run the dedicated tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_v511_headless_player.py
```

The tests exercise the real CLI as separate processes, generated-world
promotion, multiple player commands, exact replay, stale revisions, conflicting
idempotency keys, pre-mutation unsafe-action rejection, output bounds, stored raw
player text/normalized intents, and confidentiality canaries.
