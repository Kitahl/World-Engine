# World Engine 4.7.0 Changelog

## Added

- Schema-18 MOP-1.0 canonical mechanism contract with typed bindings, predicates, costs/effects, exact paired migrations, deterministic digests, atomic execution, tamper-evident receipts, and canonical effect adapters.
- Schema-19 finite economy and logistics runtime: markets, item listings, inventories, balances, extractors, producers, routes, shipments, supply links, transactions, visibility, actor-scoped idempotency, and canonical-hour simulation.
- Schema-20 aggregate population and settlement runtime: profiles, cohorts, households, labor, service needs, migration flows, daily lifecycle processing, and economy labor integration.
- WEGEN-1.2 procedural campaign generation for markets, stock, balances, extraction/production/logistics, settlement profiles, and population cohorts.
- WE-DESKTOP-1.1 native companion projections for local public markets, aggregate settlement/population state, canonical quantified inventory, and balances.
- 4.7 release verifier, exact-inventory packager, authoring-schema coverage test, and trusted-local MCP boundary tests.

## Changed

- Database release schema advances from 17 to 20 through reserved mechanism, economy, and population stages.
- PBEM integration advances to 2.2 with actor-bound economy and actor-local population policy gates inside the TurnRouter.
- Public GPT operation inventory remains exactly five. Economy/population are not silently added to the external app capability allowlist in this release.
- Environment runs before canonical-hour economy work; population runs at canonical daily boundaries after same-time environment/economy work.
- Procedural promotion reuses the existing stage, validate, dry-run, and atomic promotion workflow.
- Active engine/startup/OpenAPI identity advances to 4.7.0. Historical component receipts keep their original versions.
- Optional MCP is now a loopback-only trusted operator surface with DNS-rebinding protection and peer-address enforcement.

## Fixed

- Rebased donor schema collisions onto an explicit 18 → 19 → 20 sequence.
- Removed campaign-global economy replay identity in favor of actor/server-scoped transaction keys.
- Rejected non-finite and boolean numeric values across new subsystem authoring/runtime paths.
- Made economy scheduling invariant to arbitrary request tails and corrected same-boundary shipment ordering.
- Prevented global/actorless population disclosure and stale service/labor derived rows.
- Prevented mechanism receipts/events from serializing private bound entity rows and added digest verification.
- Routed mechanism effects through canonical tables, histories, claims, locks, events, one transaction, and one campaign revision.
- Updated the shipped authoring JSON schema so it covers every WEGEN-1.2 generated section.
- Corrected stale 4.5 release labels, schema assertions, capability counts, instructions, and startup metadata.

## Compatibility

- Existing schema-13 through schema-17 databases migrate forward without deleting campaigns.
- WEGEN-1.0 and WEGEN-1.1 staged payloads remain validatable; new generation emits WEGEN-1.2.
- Narrative NRP-1.2/NQR-1.2, Environment 4.5, output-hardening 4.3, and the five GPT Action operation IDs remain compatible.
- Complete backups use SQLite's online backup. The legacy JSON snapshot remains a core-domain diagnostic, not a complete subsystem export.
