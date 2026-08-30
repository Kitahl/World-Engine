# World Engine Corrected Integrated Build Plan

## Target: v4.3.0 Output + Companion Hardening

Date: 2026-08-30  
Release base: verified `world_engine_v4_2_0_CONTEXT_NARRATIVE_MERGED_WINDOWS_FULL.zip`  
Base SHA-256: `4841f5aeb7305100ea1c7c09444c9bf036a33ec707dde6b67489c74e8c6f096a`

## Outcome

Build v4.3 from the verified v4.2 source, not from either v4.1-derived patch. The release order is fixed by one security dependency:

`WE43-001 confidentiality -> private validation/output hardening -> presentation publication -> Foundry delivery -> optional snapshot/provider work`

Snapshot sync cannot start before confidentiality closure because an external projection magnifies any internal disclosure defect.

## Source decision matrix

| Source | Adopt | Adapt | Reject/defer |
|---|---|---|---|
| Verified v4.2 | Entire authority/runtime baseline | Version/schema/docs | Nothing wholesale |
| Corrected guide v1.1 | Defect list, authority split, runtime/offline split | Tests and line references to v4.2 | Old schema/test totals |
| Claude/WE-OUTPUT-003 | Shingles, agency/tense ideas, realization and timing tests | Rewrite against v4.2 APIs | Patch/full replacement; impure/mode-mixing ledger |
| Research correction | Minimal NarrationPacket, hard/soft split, offline ablations | Explicit fail-closed loop | Runtime heavy NLP; second story authority |
| WE43 Addendum A | Leak diagnosis and A+B direction | Whole-response authorized DTO | Exact older-build counts as release proof |
| Companion handoff | Presentation envelope, Foundry projection, relay client shape | Schema/outbox/security/API integration | Snapshot placeholder; stale migration instructions |
| Space/Scout prior art | Lease, fence, delivery-unknown concepts | Implement locally in existing DB | Young dependency adoption without run proof |

## Version and migration contract

- Engine/app: `4.3.0`.
- SQLite `user_version`: 16.
- Public narrative packet: `NRP-1.2`.
- Quality receipt: `NQR-1.2`.
- Turn protocol remains `WETP-1.0` unless the top-level response schema is explicitly versioned as an additive WETP response projection.
- Migration 15 -> 16 is additive, atomic, idempotent, and bumps `user_version` last.
- v4.2 binaries must not reopen a schema-16 store; rollback is restore-from-pre-upgrade backup or roll forward.

Schema 16 contains:

- immutable private narrative validation context bound to public packet digest;
- companion presentations;
- companion bindings;
- companion outbox with status, attempts, next attempt, claim owner, lease, fencing token, and delivery-unknown detail.

Do not add an event-visibility column in this release. First close model/companion disclosure at read/projection time. A first-class per-event audience model is a later migration if product requirements demand it.

## Phase 0 — provenance and baseline

1. Verify base package hash and archive-path safety.
2. Copy into an isolated v4.3 tree.
3. Record current schema, packet/receipt versions, action count, and critical file hashes.
4. Run the v4.2 suite before editing when execution is authorized.
5. Preserve the original package unchanged.

Gate: reproducible base identity and no unaccounted working-tree edits.

## Phase 1 — WE43-001 confidentiality closure

### 1.1 Explicit believer view

Preserve canonical `knowledge_snapshot()` default behavior. Add `view='canonical'|'believer'` or an explicit `belief_view()` method. Context Compiler uses the believer view. Facts in that view are limited to facts represented by the believer's authorized beliefs/transfers/public claims.

### 1.2 Closed enforce response

Replace copy-and-delete with a newly constructed `NarrationSafeTurnResponse`. Only allowlisted fields are serialized. Omit:

- raw `context_packet`;
- internal capability plan and debug fields;
- raw events/knowledge;
- private validation context;
- internal exception details.

The render packet must be compiled from the same authorized projection. Error paths use the same closed schema.

### 1.3 Differential non-interference gate

Create twin campaign fixtures that differ only in hidden canonical fact and event canaries. With time/RNG controlled, the complete enforce responses must be deeply equal and contain neither canary. Repeat against a forced-error path and the companion presentation payload.

Gate: whole-response secrecy, not merely absence from one packet.

## Phase 2 — NRP-1.2 private validation context

1. Build a public packet containing IDs/digests/authorized facts only; no forbidden literal.
2. Build a private validation envelope atomically with:
   - campaign and packet ID;
   - exact public packet digest;
   - packet/policy version;
   - packet-time source revision;
   - normalized forbidden literals and/or fact IDs;
   - integrity digest or server-held MAC if a suitable secret-management contract exists.
3. Expose the public packet through normal APIs; load private context only through an internal repository.
4. `quality_check(packet_id=...)` validates against stored packet-time context and rejects caller packet/body swaps.
5. Never fall back to mutable current-state re-derivation for an old packet.

Gate: packet-swap, source-drift, public-serialization, and atomic-rollback tests.

## Phase 3 — selective Output Director hardening

Port only missing behavior:

- shingle similarity for long output while keeping the fast short-text path;
- measured validator duration and baseline drift metrics;
- additional high-value agency patterns with positive/negative fixtures;
- soft tense drift check;
- pure validation separated from observation recording;
- rollout-mode-aware repetition history;
- explicit `realized_beat_ids` before beat consumption;
- tense-aware cliché normalization only if false-positive tests justify it.

Do not replace `narrative.py`, add runtime embeddings, introduce a second storylet authority, or claim semantic correctness from regexes.

Gate: existing v4.2 narrative APIs remain present; detector fixtures and performance ceilings pass.

## Phase 4 — companion presentation core

1. Integrate presentation/outbox tables into schema 16 and World Engine's existing DB helpers.
2. Add immutable `PresentationEnvelope` with bounded narration, choices, presentation metadata, revision, turn/packet/output binding, and content hash.
3. Add `publishPresentation` to the GPT action schema.
4. Retain `setVisualPreferences` as a backend route but omit it from the GPT export so the action total remains 30.
5. Publishing inserts presentation + outbox in one DB transaction.
6. Return a publication receipt; do not claim it proves ChatGPT display equality.

Gate: immutable-ID, idempotency, stale-revision, metadata-bound, and action-count tests.

## Phase 5 — crash-safe Foundry delivery

### Outbox state machine

States: `pending`, `sending`, `sent`, `dead`, `delivery_unknown`.

- claim atomically assigns owner, lease, incremented fence, and attempt;
- every sending mutation compare-and-sets owner + fence;
- definite non-delivery can retry with bounded backoff;
- permanent/configuration error becomes dead;
- ambiguous post-send failure or expired sending lease becomes delivery unknown;
- only explicit reconciliation/duplicate-tolerant policy retries unknown delivery.

### Transport boundary

- credential-bound allowlist of relay origins;
- HTTP only for approved loopback; remote requires HTTPS;
- disable ambient proxy and automatic redirects;
- validate resolved IPs and every manually approved redirect hop;
- never forward key across origin;
- request/response byte caps and connect/read/total timeouts;
- escape HTML and redact secrets from errors;
- pin a relay/module release and least-privilege scopes.

Gate: crash-after-acceptance, stale-fence, redirect/key exfiltration, SSRF, body-cap, retry classification, and HTML-escaping tests.

## Phase 6 — Foundry presentation proof

1. Use a fake relay for deterministic CI contract tests.
2. Add an opt-in live smoke test requiring operator-provided URL/key/client ID.
3. Send presentation chat only.
4. Record remote receipt/status without treating Foundry as authority.
5. Confirm reconnect/restart behavior and delivery-unknown reconciliation.

Gate: one live presentation visible in the pinned Foundry version, with no secret canary and no duplicate automatic retry.

## Phase 7 — deferred snapshot/provider work

Not part of v4.3.0 release:

- D&D5e/generic Actor or Scene mapping;
- map/token/encounter synchronization beyond proven relay contracts;
- per-event audience schema;
- full companion UI;
- runtime embeddings/stylometry;
- multi-candidate generation by default.

Begin only after querying the actual Foundry world/system structure and writing a provider-specific contract.

## Phase 8 — offline evidence

Use separate panels:

- disclosure/fidelity/chronology hard failures;
- player-agency precision/recall;
- repetition and cliché incidence;
- state-fidelity extraction;
- NPC voice attribution;
- human preference;
- latency and storage growth.

Run 50-turn CI, 100-turn release, and optional 250-turn evaluation. Compare baseline, hardening-only, local-repair, and regenerate-once conditions. Do not blend all measures into one score.

## Release verification

Required before a “works” claim:

1. full v4.3 suite from a clean extracted package;
2. high-risk confidentiality, migration, and outbox tests repeated three times;
3. syntax/static checks;
4. OpenAPI integrity, no more than 30 exported operations, required core actions present, no unresolved refs;
5. clean-database and migrated-schema-15 database audits;
6. Windows launcher/smoke check;
7. fake-relay integration and optional live Foundry smoke;
8. archive path, manifest, critical hashes, and clean re-extraction verification.

If runtime execution remains unauthorized, the package must be labeled **statically built, runtime verification pending**—never “working.”

## Gauntlet and Council gates

The bounded local Council attacked five concrete artifacts. Unique load-bearing amendments were:

- closed-schema differential disclosure testing across whole responses, not field deletion;
- immutable packet-time validation binding;
- expired send -> delivery unknown;
- credential-bound destination allowlist with no automatic redirect;
- atomic idempotent migration and explicit unsafe-rollback refusal.

The Gauntlet must still:

- `self`: challenge the precommitted “v4.3 + schema16 + presentation-only” answer;
- `boundary`: verify the exact public/private packet, response, and delivery interfaces;
- `oob`: test live relay/version/launcher assumptions beyond unit tests;
- `explain`: require a plain-language trace from player action to authorized narration and Foundry display;
- audit every all-green release claim against raw outputs and clean-package reruns.

## Definition of done

v4.3.0 is done only when the critical confidentiality gate passes on the complete response, the private validation context cannot escape, output hardening preserves v4.2 APIs, presentation publication is durable and bounded, ambiguous delivery is honest, the GPT schema remains within its intended action ceiling, and a clean package produces fresh release evidence.

## Companion UI integration amendment

The supplied companion UI engineering design is incorporated through `WORLD_ENGINE_COMPANION_UI_PLAN_ADDENDUM.md`. Its architecture is accepted only as a proposal and is sequenced after the confidentiality boundary:

- **v4.3.0:** `WE43-001`, NRP/NQR 1.2, accepted-output proof binding, schema 16 presentation/outbox foundation, safe latest-presentation read, and presentation-only Foundry delivery.
- **v4.3.1:** allowlisted per-principal snapshot, short-lived browser session, projection-sequenced Socket.IO recovery, and the minimal React companion shell.
- **Later:** maps, combat, relationship/timeline panels, and Foundry entity synchronization after separate projection and bounded non-disclosure gates.

The addendum also corrects a subtle revision problem in the proposal: campaign revisions may skip for a player when intervening changes were invisible. Realtime events therefore carry an authoritative `campaign_revision` plus a contiguous per-principal `projection_sequence`; only a projection-sequence gap is itself a recovery fault.

The release does not claim strict non-interference while authorized private director state can influence safe story selection. Its claim is bounded non-disclosure: closed whole-response contracts exclude private literals, identifiers, validation context, canonical-only facts, and unscoped events. The UI addendum defines the corresponding tests and the stronger condition that would be required for a future strict claim.
