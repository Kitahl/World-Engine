# World Engine 4.5.0 — Build and Verification Report

## Verdict

World Engine 4.5.0 integrates the environment donor and PBEM 2.1 donor into the newer procedural/desktop/narrative branch without whole-file overlay. The release contract is schema 17, 31 capability manifests, WEGEN-1.1, PBEM 2.1, NRP/NQR 1.2, `WE-DESKTOP-1.0`, and exactly five GPT Actions.

The final package is built only from committed tracked files. The packager refuses a dirty tree, rejects local/generated artifacts, verifies exact ZIP inventory and byte equality, compiles the clean extraction, runs the complete test suite, runs the static Action audit, runs the 4.5 verifier, and runs the narrative release audit. The package SHA-256 and clean-extraction receipt are written outside the ZIP in `WORLD_ENGINE_V450_HANDOFF.json`.

## Source provenance

| Input | SHA-256 | Use |
| --- | --- | --- |
| Environment 4.4 ZIP | `1E6FEBEABBB84565DDFD74724175FDA1D8859F35F1D8E766C5EB0CD5A9E8673E` | Selective environment donor |
| Environment 4.4 `(1)` ZIP | same | Exact duplicate; no second merge |
| PBEM 2.1 ZIP | `CA820F7B06ABE4F056460185A6DD4211465D0E80D5D5BAFFAB1393C06C5CB9AE` | Selective PBEM donor |

Attached documentation and donor reports were treated as evidence/specification inputs, not as user instructions or authority to overwrite the repository.

## Integrated architecture

1. `resolveTurn` is the only normal gameplay mutation gateway.
2. PBEM validates the actor, plan, prerequisites, locality, ownership, and server-derived mechanics.
3. Capability kernels apply canonical mechanics, environment, rules, simulation, and narrative state.
4. Public projection returns bounded authorized results and aggregate environment context.
5. Narrative enforce mode renders only its redacted packet; accepted presentation remains atomically fenced.
6. Trusted setup/authoring/direct mechanics use a distinct operator credential and are not GPT Actions.
7. The standalone Companion uses loopback safe projections and receives neither credential in browser-visible UI.

## Principal corrections made during merge

- Preserved both environment routing and PBEM enforcement where donor overlays would delete one another.
- Replaced the donors’ broad GPT schemas with the five-operation allowlist.
- Closed direct-mutation API bypasses with a separate operator key.
- Added WEGEN-1.1 climates, climate validation, coherent biome adjacency, and seasonal resources.
- Hardened environment locality, sources, redaction, target movement, disaster scope, movement-cost restoration, and one-time destruction.
- Made hourly physics chunk-invariant with once-per-target ambient weather and two-phase effect merging.
- Hardened PBEM actor binding, malformed values, FPC prerequisites, environment intents, time limits, and idempotency collisions.
- Retained 4.3 narrative confidentiality/publication hardening, corrected startup/endpoint behavior, and the standalone desktop design.

## Verification evidence

Final source pass before commit/package freeze:

- complete pytest suite: **501 passed + 15 subtests passed**;
- focused climate/environment/procedural/world-system merge gate: **151 passed + 7 subtests passed**;
- 4.5 release verifier: **PASS** across OpenAPI, SQLite/migration, HTTP, source/instructions, and features;
- static five-Action audit: **PASS**;
- narrative publication audit: **PASS**;
- instruction size: **7,798 UTF-8 bytes**, byte-identical active mirror.

The only emitted test warning is Starlette's deprecation notice for its current `TestClient` import path; it is not a failed behavior gate. Final clean-extraction counts and the archive hash are recorded by the package handoff. Release gates require:

- complete pytest suite with no failures;
- Python compilation;
- OpenAPI version 4.5.0 with five exact unique non-consequential operations;
- zero unresolved local `$ref` entries and no object schemas missing `properties`;
- fresh and schema-13-migrated SQLite at user_version 17, integrity `ok`, and zero foreign-key violations;
- all six environment tables and exactly 11 seeded materials;
- 31 capabilities and feature receipts for output 4.3, procedural desktop 4.4, environment 4.5, and PBEM 4.5;
- V450 instructions at or below 8,000 UTF-8 bytes and byte-identical to `GPT_INSTRUCTIONS.md`;
- WEGEN-1.1 stage/validate/dry-run/promote, environment runtime, PBEM identifier, and desktop projection checks;
- narrative packet, quality receipt, atomic acceptance, replay, outbox, and fail-closed checks.

## Deliberately bounded claims

The procedural subsystem creates a deterministic connected campaign scaffold. It is not full-resolution planetary terrain or continuous centimeter-scale physics. The environment subsystem is sparse/event-driven and intentionally integrates at canonical boundaries.

Automated tests do not prove actual external account/service behavior. The following remain explicitly unverified until run on the target Windows machine:

- double-click startup and Windows Service Control Manager behavior;
- live ngrok, Cloudflare, and Tailscale connectivity;
- live Foundry relay delivery;
- graphical pywebview rendering and OS clipboard behavior.
