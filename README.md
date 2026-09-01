# World Engine 5.0.0 — Runtime Convergence

World Engine is a persistent deterministic tabletop-RPG backend with a standalone Windows companion and an optional five-operation ChatGPT GPT Actions bridge. The backend owns canon, rules, random outcomes, player knowledge, progression, environment, economy, population, politics, actor agency, incidents, executable quests, and consequences. ChatGPT interprets intent and renders only authorized results.

Version 5.0.0 completes the dependency-gated runtime planned across the two post-Phase-2 roadmaps. It adds an event/incident spine, commitment-backed politics, actor agency, executable quest graphs, WEGEN-2.0 runtime seeding, and the adaptive native companion while preserving the hardened PBEM, output confidentiality, environment, economy, population, startup, and five-Action boundaries.

## Release contract

| Component | Contract |
| --- | --- |
| Release | **5.0.0** |
| SQLite schema | **24** |
| Procedural generator | **WEGEN-2.0**; staged WEGEN-1.0/1.1/1.2 remain validatable |
| PBEM boundary | **PBEM-2.2**, enforced on public turns |
| Narrative packet/receipt | **NRP-1.2 / NQR-1.2** |
| Mechanism contract | **MOP-1.0**, transaction-aware trusted execution |
| Desktop projection | **WE-DESKTOP-5.0.0** |
| Environment projection | **WE-ENV-PUBLIC-1.0** |
| Capability manifests | **33** |
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

Use `CUSTOM_GPT_INSTRUCTIONS_V500.txt` and the generated `openapi_actions_PERMANENT.json` in the GPT Builder.

### What ngrok is

Ngrok is an optional HTTPS tunnel: it gives ChatGPT a secure public URL that forwards requests to World Engine running on your PC. It is not the game engine, database, or Companion UI.

World Engine prefers the Microsoft Store/WinGet ngrok package and does not download a portable `ngrok.exe`. Existing ngrok configuration is reused. A first-time account token may still be required by ngrok; startup opens the official page and captures a copied token without displaying it. If tunnel setup fails, local desktop play still works.

## Standalone Companion UI

The Companion is a Python/`pywebview` desktop application over loopback—not a hosted browser companion. It keeps one primary play stage and provides Story, Dialogue, Explore, Combat, Character, World Map, and Investigation views alongside Forge and connection diagnostics.

The UI consumes safe local projections. Browser-visible HTML/JavaScript never receives the GPT bearer key or operator key. The desktop is presentation and operator tooling; the engine remains authoritative.

Complete backups use SQLite's online backup path. The legacy JSON snapshot is a core-domain diagnostic and intentionally is not advertised as a complete mechanism/economy/population backup.

Launch it independently with:

```text
START_COMPANION_UI.bat
```

## Procedural world generation

WEGEN-2.0 deterministically creates a connected campaign runtime from seed + namespace:

- settlements, routes, coherent neighboring biomes, regions, and location climates;
- factions, NPC archetypes and NPCs;
- a starting character;
- items, resource nodes, quests, faction relations, recipes, mechanism rules, and operators;
- public markets, finite market stock, inventories, balances, producers, extractors, routes, and supply links;
- settlement profiles and aggregate population cohorts;
- MOP-backed executable quest DAGs;
- actor affordances, goals, and bounded personality values;
- territorial control, public claims, and grievances;
- pressure-driven incidents bound to existing entities and canonical operators;
- a bootstrap World Bible.

Biomes select authoritative location climates, and climate seasons drive weather and seasonal resource growth. Settlements default to sheltered actor exposure; wilderness can opt actors into weather exposure.

Generated content is never written directly to canon. The operator flow is:

```text
generate → stage → validate → dry-run (max one simulated year) → promote atomically
```

Expansion batches are additive, revision-bound, namespace-isolated, and connected to an existing anchor. Runtime metadata is signed and validated; promotion installs base and executable runtime rows in one transaction and one revision. This is procedural world generation at the engine's actual abstraction: deterministic, connected, stateful campaign systems. It is not centimeter-scale terrain synthesis or a physical planet simulator.

## Canonical mechanism contract

MOP-1.0 provides one validated representation for deterministic operators and predicates. Bindings are typed and reference-checked; execution is revision-bound, preflighted, atomic, and tamper-evident. Mechanism effects reuse canonical engine writers rather than maintaining a second source of truth. This is a trusted authoring/runtime surface, not a public GPT mutation escape hatch.

## Economy + logistics

The schema-19 economy runtime adds finite inventories and balances, markets with visibility rules, bounded quotes and transactions, extractors, producers, routes, shipments, and supply links. Simulation runs on canonical absolute-hour boundaries after environment consequences, so time chunking does not create a different economy. Public/native views include only visible local markets and player-safe ledger data.

## Population + settlements

The schema-20 population runtime models aggregate cohorts, households, labor, service needs, and migration flows. Daily population processing follows economy processing and uses the canonical settlement/location model. Projections expose bounded aggregates for the current location; individual private people, hidden cohorts, and internal migration state are not disclosed.

## Event, incident, politics, agency, and quest runtime

Schema stages 21–24 add the remaining live-world layers in dependency order:

- events carry immutable sensitivity, audience scope, principals, and causal provenance;
- incidents derive bounded pressures from authoritative environment/economy/population state, select deterministically, and execute MOP effects atomically;
- politics reserves real currency, inventory, manpower, labor, and route capacity through a commitment ledger before projects, diplomacy, law, occupation, or war consume them;
- agency appraises authorized events, stores private memories, creates bounded plans, and executes only canonical affordances/operators;
- executable quest DAGs transition from authoritative events with idempotent receipts and public projections.

Anonymous/public projections require both `PUBLIC` sensitivity and `WORLD` scope. Private/secret/ENTITY/GM state remains outside the GPT and desktop world-public views.

## Environment + Consequence runtime

The sparse environment layer adds:

- canonical materials and target binding;
- deterministic six-hour weather and seasonal transitions;
- fire, smoke, water, heat, cold, gas, blight, corrosion, ice, snow, mud, darkness, corruption, disease, electricity, explosion, and drought;
- propagation, terrain damage/collapse, actor exposure, afflictions, resource pressure, NPC considerations, reactions, and optional tiered disasters;
- authoritative location summaries without exposing raw target properties or private state.

Public environment interaction is local and source-backed. `inspect` is read-only. `ignite` requires an owned/local ignition source. `extinguish` and `douse` require an owned/local water or smothering source. Caller-authored material, properties, state, intensity, amount, remote targets, and zones are rejected.

Physics integrates at canonical absolute-hour boundaries, including two-phase consequence merging, so chunked time advancement is deterministic.

## PBEM 2.2 player boundary

Every public `resolveTurn` enforces PBEM:

- the actor must be the actual character;
- actor-scoped generic operations are server-bound;
- direct consequence and legacy caller-damage writers are rejected;
- DCs, modifiers, ownership, locality, and outcomes are server-derived;
- `requires_success_of` gates consequences on a completed successful check;
- remote movement requires an authored route or successful prerequisite;
- public time advance is capped to one day;
- PBEM 2.2 contains actor-bound economy and actor-local population policy gates for trusted/router use; the public GPT allowlist remains closed unless those capabilities are explicitly enabled in a later security-reviewed release;
- idempotency is namespaced by campaign, actor, mode, and enforcement.

Trusted local/admin workflows keep their separate operator surface.

## Narrative and confidentiality

In narrative `enforce` mode, the model receives and renders only the redacted narrative packet. Forbidden literals are re-derived from private validation context for output checking, but private evidence is never returned. Accepted prose is bound atomically to campaign revision, turn, packet, exact narration, and choices.

The defensible claim is bounded non-disclosure for enforced public turns and accepted presentation. It is not strict non-interference, and it does not cover trusted admin access, direct Python calls, storage/log access, or semantic inference.

## Important files

- `CUSTOM_GPT_INSTRUCTIONS_V500.txt` — active GPT behavior contract
- `openapi_actions.json` — five-operation portable schema
- `world_engine_companion.py` and `companion_ui/` — standalone desktop
- `world_engine/procedural.py` — deterministic scaffold generator
- `world_engine/pbem.py` — public player policy
- `world_engine/environment.py` — environment simulation
- `world_engine/mechanisms.py` — canonical mechanism contract and receipts
- `world_engine/economy.py` — finite economy and logistics
- `world_engine/population.py` — aggregate population and settlements
- `world_engine/incidents.py` — pressure-derived causal incidents
- `world_engine/politics.py` — commitments, diplomacy, territory, law, and war
- `world_engine/agency.py` — actor appraisal, memory, goals, and plans
- `world_engine/quests.py` — executable quest graphs and receipts
- `scripts/release_verify_v500.py` — release verifier
- `BUILD_REPORT_V500.md` — verification evidence and remaining boundaries
- `WORLD_ENGINE_5_0_0_CORRECTED_MERGED_PLAN_AND_IMPLEMENTATION_REPORT.md` — corrected roadmap and completion report
- `V5_0_CHANGELOG.md` — merged feature and correction history

## Verification boundaries

Automated gates cover source and clean-extracted packages, schema/integrity, Action surface, deterministic generation/simulation, PBEM, environment, startup logic, API policy, and desktop projections.

Live ngrok/Cloudflare/Tailscale connectivity, Windows Service Control Manager behavior, Foundry relay, and graphical rendering depend on the user’s machine and external accounts; the release report marks those separately instead of claiming them from unit tests.
