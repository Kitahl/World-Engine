# World Engine 5.0.0 — Corrected Merged Plan and Implementation Report

Date: 2026-08-31
Inputs reviewed: `WORLD_ENGINE_REMAINING_WORK_AFTER_PHASE_2.md` and `WORLD_ENGINE_REMAINING_PLAN_4_6_1_TO_4_9.md`
Authority: the two documents were treated as design evidence, not executable instructions. The live repository, database contracts, security boundaries, and tests were authoritative.

## Executive decision

The two plans were directionally compatible but no longer executable as written. They assumed a 4.6/schema-19 baseline, while the repository already contained World Engine 4.7/schema 20 with MOP-1.0, finite economy/logistics, population/settlements, PBEM-2.2, narrative confidentiality, the safe endpoint system, and a local pywebview companion.

The corrected plan therefore became one dependency-gated major release: **World Engine 5.0.0, schema 24**. A major version is appropriate because the work completes a new runtime architecture rather than adding one isolated subsystem.

## Corrections applied to both plans

1. **Add an event/incident spine before downstream simulation.** Politics, agency, and quests need scoped, causal events. Building them first would preserve the old secret/event leak and create incompatible histories.
2. **Add commitments before politics and war.** A project or army cannot merely claim currency, goods, labor, manpower, or route capacity. Reservations must be atomic and visible to economy/population availability calculations.
3. **Use the existing belief system for political knowledge.** A second claims-as-beliefs database would create contradictory knowledge authority.
4. **Treat existing quests as storage, not a complete runtime.** The `quests` table existed, but graph validation, event-driven transitions, receipts, template bindings, and scheduler execution did not.
5. **Keep public GPT Actions at exactly five.** Economy, population, politics, authoring, simulation, agency, incidents, and quest mutation remain trusted local/internal workflows.
6. **Do not build a hosted browser companion.** The product already uses a bundled local HTML/CSS/JavaScript shell inside Python/pywebview over loopback. The UI plan was applied to that native shell.
7. **Expand procedural generation at World Engine’s abstraction.** The engine already generated deterministic connected scaffolds. WEGEN-2.0 extends those scaffolds with executable runtime seeds; it does not become a centimeter-scale terrain or planet simulator.
8. **Make MOP transaction-aware before runtime reuse.** Scheduler domains must execute canonical operators inside the caller’s transaction and revision, with scoped idempotency and stable boundary identities.
9. **Use dependency gates, not calendar-only versions.** Each stage was integrated only after its prerequisites and privacy boundaries existed.

## Corrected dependency plan and completion

| Stage | Schema | Deliverable | Completion evidence |
| --- | ---: | --- | --- |
| Foundation | 20 | Preserve 4.7 MOP, economy, population, environment, PBEM, narrative, startup | Existing suites retained |
| Event/incident spine | 21 | Visibility/audience/principals, causal roots, bounded derived pressures, deterministic incident selection, MOP execution | Implemented in `engine.py`, `mechanisms.py`, `incidents.py`, `simulation.py` |
| Commitments/politics | 22 | Resource reservations, projects, diplomacy, treaties, claims, grievances, territorial control, forces, war, occupation, law | Implemented in `politics.py`; economy/population subtract reservations |
| Actor agency | 23 | Affordances, values, goals, appraisal, private memory, plans, canonical execution | Implemented in `agency.py` |
| Executable quests | 24 | Validated DAGs, bindings, event cursor, transitions, receipts, public projections | Implemented in `quests.py` |
| Generation/authoring | — | WEGEN-2.0 runtime seeds, validation, dry-run, one-transaction promotion | Implemented in `procedural.py` and `authoring.py` |
| Native companion | — | Adaptive one-stage shell and safe runtime projections | Implemented in `desktop.py` and `companion_ui/` |
| Release | 24 | Version 5.0.0 identity, instructions, verifier, package, reports | Implemented; packaging follows final green full-suite/commit gate |

## Runtime architecture

The canonical daily order is:

```text
environment (-80)
  → economy (-70)
  → population (-60)
  → politics (-50)
  → agency (-40)
  → incidents (-30)
  → quests (-20)
```

Environment and economy retain canonical absolute-hour integration. Population, politics, agency, incidents, and quests use canonical day boundaries. Runtime identities derive from stable boundary keys rather than the number of API calls used to reach the boundary.

### Event and incident safety

- Events now store `sensitivity`, audience `scope_type`, optional principal, causal parent, and causal root.
- Anonymous/public world views require both `sensitivity='PUBLIC'` and `scope_type='WORLD'`.
- Incident instances snapshot visibility immutably, so editing a definition cannot retroactively declassify history.
- Secret incident visibility propagates to causal MOP child events.
- Incident definition JSON, condition depth/nodes, bindings, weights, locations, definitions, and candidate materialization are bounded.
- Removed or stale locations cannot continue contributing old pressure rows.
- Public and trusted incident dispatchers are separate.

### Politics and resource truth

- Commitments reserve real currency, inventory, manpower, labor, or route capacity atomically.
- Population labor availability subtracts active political labor commitments.
- Economy route shipment capacity subtracts active route-capacity commitments.
- Actor-scoped idempotency prevents duplicate political mutations.
- Private strategy, claims, proposals, treaty/legal details, force state, and project events use ENTITY/GM projections; public world consequences remain WORLD/public.

### Agency and quests

- Agency planning uses canonical MOP affordances, not a second effect system.
- Appraisals and memories are private and principal-scoped.
- Quest graphs validate structure and bindings, consume authoritative events through a cursor, and persist idempotent transition receipts.
- Desktop/player projections contain only explicitly public quest state.

## Procedural generation outcome

WEGEN-2.0 preserves deterministic seed + namespace generation and backward validation for WEGEN-1.0, 1.1, and 1.2. New signed `_generation.runtime` metadata contains:

- MOP-backed quest templates and executable DAGs;
- agency affordances, goals, and bounded personality values;
- political territorial control, claims, and grievances;
- incident definitions bound to generated entities and canonical operators.

Static authoring validation rejects malformed shapes, non-finite numbers, oversized inputs, bad IDs/references, and canon locks. Scratch promotion validates the installed runtime. Live promotion writes base content and runtime content in one transaction and increments the campaign revision once.

## Companion decision

The companion remains a **standalone local Python/pywebview application**, not a remote browser product. The bundled web assets are the native window’s presentation layer. WE-DESKTOP-5.0.0 retains one `stage-content` surface and adds Story, Dialogue, Explore, Combat, Character, World Map, and Investigation modes.

Secondary allowlists sanitize incident journal entries, player agency state, public politics, and executable quests. Private/secret/raw database rows, bearer credentials, and operator keys are not sent to the UI.

## Ngrok clarification and automation

Ngrok is only the optional HTTPS tunnel that lets remote GPT Actions reach the engine on the user’s PC. Local engine and companion use do not require it.

World Engine uses the pinned Microsoft Store package through authenticated WinGet and never downloads a portable `ngrok.exe`. It automatically reuses an existing configuration or `NGROK_AUTHTOKEN`. A first-time ngrok account credential cannot be invented or retrieved without the account owner’s authorization; the remaining one-time action is signing in and pressing Copy. Clipboard reads are bounded, use STA PowerShell then Tk fallbacks, and no longer make a PowerShell timeout fatal. The captured token is validated, persisted, and then reused automatically.

## Verification policy

Green tests are necessary but not sufficient. This release uses:

- fresh and schema-13 migration databases;
- SQLite integrity and foreign-key checks;
- exact five-Action/OpenAPI audits;
- public WORLD confidentiality probes;
- actual one-call versus chunked scheduler identity comparison;
- long valid incident-ID execution;
- immutable incident visibility tests;
- cross-kernel commitment tests;
- procedural validation, dry-run, rollback, and atomic promotion tests;
- native UI structure and projection tests;
- clean-extracted package compilation and full-suite verification.

The final build report and handoff JSON record exact counts, hashes, and unverified Windows/external boundaries. No live network, Windows service, Foundry relay, or graphical pywebview result is claimed from unit tests.
