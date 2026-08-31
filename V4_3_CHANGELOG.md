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
- Regression tests for PowerShell clipboard timeouts, bounded Tk fallback, host retry backoff, deferred launcher error callbacks, and typed FTS degradation.

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
- Permanent endpoint repair preserves the configured provider identity; missing, unknown, Cloudflare, and Tailscale identities never fall through to ngrok.
- Cloudflare installer and recovery share one pinned binary definition and use a trusted absolute System32 `sc.exe`.
- Windows server and companion launchers are anchored to the package and use the private `.venv` interpreter.
- Static Action review now consumes the same positive allowlist as runtime export.

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
- uncaught Windows `Get-Clipboard` and `Set-Clipboard` timeouts during automatic startup.
- stale 4.2 failure and launcher-configuration labels in the 4.3 startup path.
- repeated retries of a failed clipboard host and an unbounded in-process Tk fallback.
- broad FTS exception masking that hid unexpected programming defects.
- broken Cloudflare installer reference to the absent V398 entrypoint.
- cloudflared 2026.8.1/2026.8.2 pin drift between launcher and endpoint installer.
- missing automatic restart path for an installed Cloudflare Windows service.
- cross-provider hostname takeover during failed permanent-endpoint recovery.
- ambient-Python companion execution and working-directory-fragile Windows server launch.
- batch parse-time `%errorlevel%` expansion that broke the no-`py` Python fallback.
- current-directory executable search for `sc.exe` in Cloudflare install and recovery paths.
- stale V399/V400/4.0 active labels and the V420 instruction regression target.
- release verification that measured stale V420 instructions instead of the active V430 artifact.
- package handoff race between initial clean-source validation and final critical-file hashing.

## Compatibility

- WETP remains 1.0.
- Existing campaigns and v4.1 narrative rows migrate forward without deleting source rows.
- Database schema advances to 16.
- New campaigns still default narrative mode to `off`.
- Browser UI authentication/projection sequencing is deferred to 4.3.1.
