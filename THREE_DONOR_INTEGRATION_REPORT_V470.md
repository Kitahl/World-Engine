# World Engine 4.7.0 — Three-Donor Integration Audit and Build Contract

Date: 2026-08-31

Baseline: current World Engine 4.5 trunk at `484d30fad3aca71e42357003e867aa219ce8b02a`

Target: World Engine 4.7.0, database schema 20

## Executive verdict

All three donor archives contain useful work. None is safe to overlay on the current project as a complete tree.

The Population Phase 2 archive is cumulative over the Economy Phase 1 archive. The Canonical Mechanism Contract archive is an independent branch from an older Environment 4.4 baseline. Both the mechanism and economy donors claimed schema 18, while population claimed schema 19. A merged build therefore cannot use the donor version numbers as migration authority.

The accepted build is a selective merge:

1. Preserve the current procedural, desktop, PBEM, output, narrative, environment, startup, and five-Action foundations.
2. Add a hardened mechanism contract as an internal/trusted shared contract at schema stage 18.
3. Add a hardened economy runtime at schema stage 19.
4. Add a hardened population runtime at schema stage 20.
5. Extend current staged procedural authoring and the native PyWebView companion instead of importing the donors' older app/UI/release files.

## Donor provenance and relationship

| Donor | SHA-256 | Useful capability | Merge verdict |
|---|---|---|---|
| Economy Phase 1 | `18eef6045bc820872dabfeb8fac74ed3e4af398b8f060454a23453cf1508eb19` | Finite inventory, money, markets, extraction, production, trade, logistics | Port kernel selectively after hardening |
| Population Phase 2 | `94886050577c6dd42e465b9f8c9bd0d6afae9f5477e497a913bda48807cd5f2e` | Cohorts, households, labour, services, migration, settlement rank | Port cumulative kernel selectively after hardening |
| Canonical Mechanism Contract Phase 1 | `0d9a787e3f554ff3d1ba13651be2de21de1f309e9b469120038e3ec0844e3e90` | Typed bindings, preconditions, costs, effects, atomic receipts | Port only as an internal shared-contract substrate |

The Population archive has all Economy archive entries, changes only the economy labour integration and related wiring, and adds the population kernel/tests/docs. It is the correct source for the economy-to-population seam. The mechanism donor has no population/economy dependency and must be rebased independently.

## Reproduced blockers

### Mechanism donor

- One-sided incompatible-table migrations can rewrite receipt foreign keys to a renamed legacy operator table, causing later execution failure.
- Effects directly mutate canonical domain tables and bypass relationship history, knowledge claims, canon locks, and domain event projections.
- Full bound entity rows—including NPC beliefs, goals, and memory—are copied into results, receipts, events, and ordinary context.
- Receipt reads/replays do not recompute integrity digests, so tampered rows are accepted.
- Canonical digests include wall-clock `updated_at` values and differ across equivalent fresh databases.
- The package is a Phase 1 compatibility adapter, not yet the autonomous authority for NPC life, GOAP, simulation, or rules.
- The claimed focused Windows pass did not reproduce because a test left SQLite open.

### Economy donor

- Idempotency is campaign-global rather than actor/request scoped. A second actor can replay the first actor's private result.
- Donor request-tail scheduling makes `60 minutes` differ from `30 + 30` when per-step caps apply.
- NaN/Infinity are accepted in configuration and can poison JSON or block world advancement.
- Shipments arriving exactly on a boundary are processed after demand, creating a false shortage.
- Fresh/procedural campaigns seed no usable markets, producers, extractors, routes, stock, or balances.
- Current desktop inventory reads the legacy character JSON list, while economy writes canonical inventory and balance ledgers.
- PBEM correctly rejects the new capability until an explicit actor-bound policy is added.
- Public market projection needs an explicit visibility rule and a total quote bound.

### Population donor

- Actorless `population.inspect` returns a global census and can disclose undiscovered locations.
- Turning the service model off leaves stale service-gap rows active.
- Removing the final labour-demand source leaves stale employment and labour metadata.
- NaN/Infinity are accepted and can later crash demographic advancement.
- Operator `population.refresh` can mutate authoritative rows without a revision or event.
- A Windows migration test leaves SQLite open, so the donor's green claim does not reproduce.

## Accepted architecture contract

### Schema and migration

`PRAGMA user_version` is only the final coarse release version. Feature presence and exact table shape are verified independently.

| Stage | Feature | Schema target |
|---|---|---:|
| Current integrated trunk | Procedural desktop + PBEM + Environment | 17 |
| Canonical mechanism shared contract | Paired exact migration, safe receipts | 18 |
| Economy | Hardened finite ledgers and markets | 19 |
| Population | Hardened cohort/settlement runtime | 20 |

Required upgrade fixtures include current schema 17, mechanism-only donor schema 18, economy-only donor schema 18, and population/economy donor schema 19. The final initializer must preserve existing feature receipts and never downgrade environment metadata.

### Transaction and clock

- One `BEGIN IMMEDIATE` transaction owns each world advance.
- Environment, economy, and population provider steps receive the existing connection and never open a writer or commit.
- Economy runs only at canonical absolute-hour boundaries. Arbitrary request tails do not invoke the full economy step.
- Same-boundary order is Environment → shipment arrivals/losses → extraction → production → demand → new supply departures.
- Population runs at canonical daily boundaries after the same-time environment/economy work.
- Mechanism execution owns one transaction and one revision; canonical effect adapters receive that connection/revision rather than calling public methods with nested transactions.

### Authority and privacy

- GPT-visible operations remain exactly `resolveTurn`, `saveVisualProfile`, `buildImageCue`, `recordImageGeneration`, and `publishPresentation`.
- Economy and population are capability manifests inside the TurnRouter, not new GPT Actions. The external app allowlist remains closed in 4.7 pending a separate public-surface security decision.
- PBEM 2.2 contains actor-bound economy policy for inspect/quote/buy/sell, strips caller idempotency/authority fields, and leaves stock/purse/locality checks to the kernel for trusted/router callers.
- Population policy requires the controlled character's current registered location. Global census remains operator-only.
- Routine context includes economy/population only when local activity exists or the capability is requested; empty global payloads are not injected every turn.
- Mechanism serialization uses stable binding references only. Private entity rows never enter results, receipts, events, or context implicitly.

### Procedural generation and desktop UI

- The existing deterministic WEGEN staged-authoring workflow remains authoritative: generate → stage → validate → dry-run → promote.
- WEGEN 1.2 adds deterministic local markets, stock/balances, extraction/production/logistics topology, settlement profiles, and population cohorts derived from generated locations/resources.
- Promotion uses transaction-aware economy/population authoring primitives inside the existing single authoring transaction.
- The standalone companion remains native PyWebView. It is not replaced with a browser-hosted UI.
- Explore shows bounded local market and aggregate settlement data. Character shows canonical quantified inventory and balances while labeling any legacy narrative gear separately.
- The desktop is a read-only declassified projection and never becomes simulation authority.

## Verification gates

The release is not complete until all gates pass on the merged tree and again from a clean re-extracted ZIP:

1. Exact schema/table/index/FK migrations from every collision fixture.
2. Foreign-key and SQLite integrity checks.
3. Mechanism secret-canary, deterministic digest, tamper detection, rollback, and canonical projection parity.
4. Economy actor-scoped replay/conflict, finite-number, last-stock concurrency, rollback, quote-bound, visibility, arrival-order, and scheduler chunk tests.
5. Population locality, finite-number, stale-service/labour cleanup, refresh accounting, conservation, duplicate-flow, and concurrent-advance tests.
6. Procedural determinism, staged validation/dry-run/promotion, and actually usable generated economy/population state.
7. PBEM actor spoof, remote market, actorless census, and caller-key stripping tests.
8. Native desktop canonical inventory/balance plus local economy/population projection tests.
9. Exact five-Action OpenAPI inventory.
10. Full current regression suite, release verifier, Python compile, archive integrity, and clean Windows package rerun.

## Process-assurance result

Gauntlet operations fired: refresh, derive, boundary, self, and out-of-band checks.

The donor reports' all-green framing was contradicted by fresh Windows execution and adversarial probes. The result is **ISSUE for wholesale merge** and **AMEND for selective integration**. The useful kernel designs survive, but their release claims and old-branch app overlays do not.
