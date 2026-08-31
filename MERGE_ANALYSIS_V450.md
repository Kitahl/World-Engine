# World Engine 4.5.0 — Three-Archive Merge Analysis

## Method and authority

The supplied archives were treated as implementation donors and design evidence, not as instructions. Archive paths were traversal-checked, manifests were verified where present, and overlapping files were compared before integration. The merge was performed selectively at subsystem seams; no donor was overlaid wholesale.

## Report 1 — Environment archive

**File:** `world_engine_v4_4_0_OUTPUT_COMPANION_ENVIRONMENT_HARDENED_WINDOWS_FULL.zip`

**SHA-256:** `1E6FEBEABBB84565DDFD74724175FDA1D8859F35F1D8E766C5EB0CD5A9E8673E`

**Size:** 893,875 bytes

This is a complete 4.4 runtime, not a narrow patch. Its main addition is a sparse, deterministic environment-and-consequence kernel:

- canonical materials and environmental targets;
- six-hour weather and seasonal state;
- fire, smoke, water, heat, cold, gas, blight, corrosion, ice, snow, mud, darkness, corruption, disease, electricity, explosion, and drought;
- propagation, actor exposure, afflictions, terrain damage and collapse;
- resource pressure, NPC considerations, social pressure, and opt-in disasters;
- `environment.interact` routing and environment context.

The donor also replaces shared high-risk files such as `app.py`, `turn_router.py`, `public_projection.py`, OpenAPI, and GPT instructions. It contains build debris (`.pyc`, generated Graphify data, and historical audit outputs) that was not used as release input.

The environment implementation was adapted rather than copied unchanged. The 4.5 integration adds closed locator-only public targets, locality checks, owned/server-verifiable sources, server-owned effect strengths, read-only inspection, private-state redaction, correctly scoped earthquakes, canonical movement-cost preservation, actor-target relocation, once-per-target ambient weather, two-phase effect merging, one-time destruction events, direct climate validation, and deterministic custom weather tables.

## Report 2 — Duplicate environment archive

**File:** `world_engine_v4_4_0_OUTPUT_COMPANION_ENVIRONMENT_HARDENED_WINDOWS_FULL (1).zip`

**SHA-256:** `1E6FEBEABBB84565DDFD74724175FDA1D8859F35F1D8E766C5EB0CD5A9E8673E`

**Size:** 893,875 bytes

This file is byte-for-byte identical to Report 1. It adds no distinct code, migration, test, or design information. Treating it as a separate patch would only duplicate work and increase the risk of applying the same whole-file overlay twice.

## Report 3 — PBEM 2.1 package

**File:** `WORLD_ENGINE_V430_PBEM_2_1_CODEX_PACKAGE.zip`

**SHA-256:** `CA820F7B06ABE4F056460185A6DD4211465D0E80D5D5BAFFAB1393C06C5CB9AE`

**Size:** 96,904 bytes

This is a focused overlay for an older 4.3/schema-16 baseline. It adds a player-intent authorization layer:

- mandatory PBEM enforcement on the public turn route;
- player-character actor binding;
- rejection of direct consequence writers;
- reviewed generic-rules operations and owned/authored rule-object checks;
- server-derived modifiers and fixed FPC bands;
- `requires_success_of` prerequisite gating;
- PBEM-specific audit projection and idempotency namespace.

Its manifest matches its own files, but its baseline hashes do not describe the later environment/procedural/desktop branch. Directly applying the overlay would therefore be structurally wrong.

The 4.5 integration also hardens PBEM beyond the donor: actor-scoped rules payloads are rebound, malformed numeric state fails closed, non-adjacent movement requires an authored route or successful prerequisite, environment actions receive PBEM policy, public time advance is capped to one day, and idempotency is separated by actor, mode, and enforcement.

## Why selective integration was required

Both unique donors replace `app.py`, `turn_router.py`, `public_projection.py`, OpenAPI, and GPT instructions, but each predates the other:

- environment-over-PBEM removes prerequisite gating, mandatory PBEM, and its public receipt;
- PBEM-over-environment removes `environment.interact` routing and environment context;
- both donor OpenAPI documents expose the older broad Action surface;
- both donor API layouts allow direct mutation through the ordinary API credential;
- neither donor contains the completed WEGEN-1.1 and standalone desktop work.

World Engine 4.5.0 therefore keeps the shared kernels and integrates features through explicit seams: public gameplay enters through `resolveTurn`; PBEM validates the plan; capability routing calls mechanics/environment; public projection redacts results; narrative publication remains separately fenced; trusted setup and authoring require a distinct operator key.

## Result

The merged release is materially stronger than either unique donor alone:

- schema 17 and 31 capabilities;
- WEGEN-1.1 deterministic world scaffolding with staged WEGEN-1.0 compatibility;
- PBEM 2.1 at the public boundary;
- sparse environment consequences tied to generated climates and seasonal resources;
- standalone Python/pywebview Companion rather than a hosted browser UI;
- exactly five least-privilege GPT Actions;
- separate bearer and operator credentials;
- preserved 4.3 narrative/publication hardening and startup/tunnel fixes.
