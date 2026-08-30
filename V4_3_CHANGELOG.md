# World Engine 4.3.0 Changelog

## Added

- Schema-16 narrative publication attempts, semantic attestations, and packet acceptance fence.
- WEPUB-1.0 canonical publication candidate and WEP-1.0 immutable presentation envelope.
- Same-transaction narrative acceptance and companion outbox enqueue.
- Exact semantic-review operator tool: `scripts/publication_review.py`.
- Bounded companion outbox worker: `scripts/companion_worker.py` and `START_COMPANION_WORKER.bat`.
- Safe hidden trusted-backend endpoint: `GET /api/presentation/latest`.
- Static Action-surface audit covering source and checked-in OpenAPI.
- NRP-1.2 private validation contexts and NQR-1.2 receipts.
- Regression tests for packet races, replay/conflict, rollback, rehashed presentation forgery, semantic pending/reject, confidentiality canaries, closed publication inputs, choice bounds, and safe latest reads.

## Changed

- Public `resolveTurn` uses a positive ordinary-player capability allowlist.
- Enforce mode requires execute requests and rejects caller downgrades before mutation.
- Character knowledge uses the believer fact view.
- Non-GM public context excludes global fact search, event history, and archive candidates.
- Public exception responses use stable non-reflective codes.
- GPT Action count is treated as a maximum of 30, not a target; the curated v4.3 surface contains 21 operations.
- `publishPresentation` accepts only campaign, presentation, packet, turn, revision, narration, and choices.
- Exporter and launcher share an exact positive allowlist for all 21 GPT Actions, so future endpoints fail closed.
- Publication choices are bounded to 500 characters per item in both OpenAPI and engine validation.
- Exact accepted replays are idempotent; competing packet candidates conflict.
- Explicit beat realization remains required for legacy narrative-output consumption under the strict default.

## Removed from GPT Actions

- raw world context and entity reads;
- NPC/faction save and mutation operations;
- raw world-state mutation;
- broad simulation configuration;
- authoring operations;
- internal-state reads.

Backend/operator implementations may remain for trusted setup, migration, and diagnostics, but they are not part of the curated GPT schema.

## Fixed

- `WE43-001` canonical-fact and event leakage through the full enforced turn payload.
- enforce-mode context-only bypass without an override.
- public `knowledge.transfer`/future-capability allow-by-default exposure.
- reflected secret-bearing exception strings.
- stale unsafe checked-in OpenAPI export.
- non-atomic narrative/presentation publication.
- incomplete accepted replay validation.
- companion evidence pre-seeding.
- launcher exception-lambda lifetime capture.
- ISO accepted-presentation timestamp decoding.
- latest-presentation rehashed forgery caused by validating only mutable envelope bytes and their mutable stored hash.

## Compatibility

- WETP remains 1.0.
- Existing campaigns and v4.1 narrative rows migrate forward without deleting source rows.
- Database schema advances to 16.
- New campaigns still default narrative mode to `off`.
- Browser UI authentication/projection sequencing is deferred to 4.3.1.
