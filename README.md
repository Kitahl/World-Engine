# World Engine 4.5.0 — Procedural Desktop + PBEM + Environment

World Engine is a persistent deterministic tabletop-RPG backend for ChatGPT GPT Actions. The backend owns canon, rules, random outcomes, player knowledge, progression, environmental state, and consequences. ChatGPT interprets intent and renders only authorized results.

Version 4.5.0 merges the procedural/desktop line, PBEM 2.1 player boundary, and the Environment + Consequence runtime into one schema-17 release.

## Release contract

| Component | Contract |
| --- | --- |
| Release | **4.5.0** |
| SQLite schema | **17** |
| Procedural generator | **WEGEN-1.1**; staged WEGEN-1.0 remains validatable |
| PBEM boundary | **PBEM-2.1**, enforced on public turns |
| Narrative packet/receipt | **NRP-1.2 / NQR-1.2** |
| Desktop projection | **WE-DESKTOP-1.0** |
| Environment projection | **WE-ENV-PUBLIC-1.0** |
| Capability manifests | **31** |
| GPT Actions | **5** |
| Normal gameplay gateway | **resolveTurn** |

The five GPT Actions are:

- `resolveTurn`
- `publishPresentation`
- `saveVisualProfile`
- `buildImageCue`
- `recordImageGeneration`

Direct setup, mechanics, simulation, authoring, and admin routes are hidden from the GPT schema and require a separate operator key. This prevents those routes from bypassing PBEM validation.

## Start on Windows

Double-click:

```text
START_WORLD_ENGINE.bat
```

Startup creates or reuses a private `.venv`, installs backend and `pywebview` dependencies when needed, starts the loopback API, opens the standalone Companion desktop, and then attempts the optional GPT HTTPS connection.

The local engine and desktop remain usable if the external tunnel is unavailable. Connection status is reported honestly as ready, auth required, timed out, or failed.

Use `CUSTOM_GPT_INSTRUCTIONS_V450.txt` and the generated `openapi_actions_PERMANENT.json` in the GPT Builder.

### What ngrok is

Ngrok is an optional HTTPS tunnel: it gives ChatGPT a secure public URL that forwards requests to World Engine running on your PC. It is not the game engine, database, or Companion UI.

World Engine prefers the Microsoft Store/WinGet ngrok package and does not download a portable `ngrok.exe`. Existing ngrok configuration is reused. A first-time account token may still be required by ngrok; startup opens the official page and captures a copied token without displaying it. If tunnel setup fails, local desktop play still works.

## Standalone Companion UI

The Companion is a Python/`pywebview` desktop application over loopback—not a hosted browser companion. It provides six operator-facing modes, including campaign play, world forge, map/continuity views, and connection diagnostics.

The UI consumes safe local projections. Browser-visible HTML/JavaScript never receives the GPT bearer key or operator key. The desktop is presentation and operator tooling; the engine remains authoritative.

Launch it independently with:

```text
START_COMPANION_UI.bat
```

## Procedural world scaffold

WEGEN-1.1 deterministically creates a connected campaign scaffold from seed + namespace:

- settlements, routes, coherent neighboring biomes, regions, and location climates;
- factions, NPC archetypes and NPCs;
- a starting character;
- items, resource nodes, quests, and faction relations;
- a bootstrap World Bible.

Biomes select authoritative location climates, and climate seasons drive weather and seasonal resource growth. Settlements default to sheltered actor exposure; wilderness can opt actors into weather exposure.

Generated content is never written directly to canon. The operator flow is:

```text
generate → stage → validate → dry-run (max one simulated year) → promote atomically
```

Expansion batches are additive, revision-bound, namespace-isolated, and connected to an existing anchor. This is a deterministic world scaffold, not full terrain synthesis or a centimeter-scale physical planet generator.

## Environment + Consequence runtime

The sparse environment layer adds:

- canonical materials and target binding;
- deterministic six-hour weather and seasonal transitions;
- fire, smoke, water, heat, cold, gas, blight, corrosion, ice, snow, mud, darkness, corruption, disease, electricity, explosion, and drought;
- propagation, terrain damage/collapse, actor exposure, afflictions, resource pressure, NPC considerations, reactions, and optional tiered disasters;
- authoritative location summaries without exposing raw target properties or private state.

Public environment interaction is local and source-backed. `inspect` is read-only. `ignite` requires an owned/local ignition source. `extinguish` and `douse` require an owned/local water or smothering source. Caller-authored material, properties, state, intensity, amount, remote targets, and zones are rejected.

Physics integrates at canonical absolute-hour boundaries, including two-phase consequence merging, so chunked time advancement is deterministic.

## PBEM 2.1 player boundary

Every public `resolveTurn` enforces PBEM:

- the actor must be the actual character;
- actor-scoped generic operations are server-bound;
- direct consequence and legacy caller-damage writers are rejected;
- DCs, modifiers, ownership, locality, and outcomes are server-derived;
- `requires_success_of` gates consequences on a completed successful check;
- remote movement requires an authored route or successful prerequisite;
- public time advance is capped to one day;
- idempotency is namespaced by campaign, actor, mode, and enforcement.

Trusted local/admin workflows keep their separate operator surface.

## Narrative and confidentiality

In narrative `enforce` mode, the model receives and renders only the redacted narrative packet. Forbidden literals are re-derived from private validation context for output checking, but private evidence is never returned. Accepted prose is bound atomically to campaign revision, turn, packet, exact narration, and choices.

The defensible claim is bounded non-disclosure for enforced public turns and accepted presentation. It is not strict non-interference, and it does not cover trusted admin access, direct Python calls, storage/log access, or semantic inference.

## Important files

- `CUSTOM_GPT_INSTRUCTIONS_V450.txt` — active GPT behavior contract
- `openapi_actions.json` — five-operation portable schema
- `world_engine_companion.py` and `companion_ui/` — standalone desktop
- `world_engine/procedural.py` — deterministic scaffold generator
- `world_engine/pbem.py` — public player policy
- `world_engine/environment.py` — environment simulation
- `scripts/release_verify_v450.py` — release verifier
- `BUILD_REPORT_V450.md` — verification evidence and remaining boundaries
- `MERGE_ANALYSIS_V450.md` — organized analysis of the three supplied archives
- `V4_5_CHANGELOG.md` — merged feature and correction history

## Verification boundaries

Automated gates cover source and clean-extracted packages, schema/integrity, Action surface, deterministic generation/simulation, PBEM, environment, startup logic, API policy, and desktop projections.

Live ngrok/Cloudflare/Tailscale connectivity, Windows Service Control Manager behavior, Foundry relay, and graphical rendering depend on the user’s machine and external accounts; the release report marks those separately instead of claiming them from unit tests.
