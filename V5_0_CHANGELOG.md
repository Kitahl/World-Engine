# World Engine 5.0.0 Changelog

## Added

- Schema 21 event visibility, audience scope, principals, and causal provenance.
- Deterministic bounded incident pressure/selection/execution runtime.
- Schema 22 political commitment ledger, projects, diplomacy, treaties, territory, forces, war, occupation, and legal hooks.
- Schema 23 actor agency with affordances, values, private appraisal/memory, goals, plans, and MOP execution.
- Schema 24 executable quest graphs, bindings, event cursors, transitions, receipts, and projections.
- WEGEN-2.0 executable runtime seeding with atomic authoring promotion.
- WE-DESKTOP-5.0.0 adaptive local companion with Dialogue and safe runtime views.
- World Engine 5.0 release verifier and clean-package gates.

## Changed

- MOP-1.0 can execute inside a scheduler-owned transaction and revision.
- Scoped idempotency keys are bounded and digest-backed while retaining readable prefixes.
- Scheduler-owned identities use stable step boundaries and are invariant to time-advance chunking.
- Population labor and economy route capacity subtract active political commitments.
- Anonymous context requires PUBLIC sensitivity and WORLD scope.
- Active engine/startup/OpenAPI/instruction identity is 5.0.0; historical component receipts retain their original versions unless their contract changed in 5.0.

## Fixed

- SECRET incident effects no longer leak through PUBLIC mechanism child events.
- Incident definition edits cannot retroactively declassify historical instances.
- ENTITY-scoped events no longer appear in anonymous world context.
- Long valid incident IDs no longer overflow scoped idempotency keys.
- Incident selection and definition evaluation are explicitly bounded.
- Deleted/stale locations no longer influence world pressure aggregation.
- Incident and MOP persisted identities are chunk-invariant.
- Public incident dispatch cannot request privileged history or mutate definitions.
- Existing-database event indexes are created only after additive visibility columns exist.
- PowerShell clipboard timeouts fall through to bounded fallback readers instead of aborting startup.

## Preserved boundaries

- Exactly five public GPT Actions.
- PBEM-2.2 public-player enforcement.
- NRP-1.2/NQR-1.2 narrative confidentiality and publication.
- Microsoft Store/WinGet-only ngrok installation; no direct executable download.
- Standalone local pywebview companion; no hosted browser UI.
