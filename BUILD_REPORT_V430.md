# World Engine 4.3.0 — Build and Verification Report

## Verdict

World Engine 4.3.0 integrates the hardened 4.0.1 context compiler, the stronger 4.0.2/4.1 narrative line, NRP/NQR 1.2 output validation, atomic accepted publication, and a presentation-only companion outbox. The source build and a freshly extracted release ZIP both passed the full verification gates.

## Provenance and review lanes

The build was derived from the supplied 4.0.1, 4.0.2, 4.1.0, companion handoff, corrected update guides, research correction, WE-OUTPUT-003 directive/addendum, and companion UI engineering design. Attached documents were treated as evidence/design inputs, not as user authority.

Review used:

- static repository and artifact comparison;
- Graphify architecture inventory;
- Scout/Space prior-art checks for Foundry REST, Socket.IO, and browser map integration;
- Council-style design review using local Terra/Sol lanes when external model export was unavailable;
- FOIL evidence calibration;
- Infinity Gauntlet frame, boundary, out-of-band, and explain-back checks;
- independent Sol atomic-publication breaker review;
- independent Sol confidentiality review;
- Terra public-boundary implementation;
- fresh runtime tests after explicit authorization.

Repository Librarian returned `LIB-CATALOG-EMPTY` for the sparse public repository at commit `1b1e5221be81d9e48d65208cd2c027e7a7a503d2`; therefore no repository-wide ownership/authority claim is made from that catalog.

## Principal defects found and fixed

1. Canonical facts ignored `believer_key`; character reads could receive world truth. The believer view is now forwarded and canonical unknown facts are omitted.
2. Event history had no per-event visibility and was shipped in WARM context. Non-GM public turns no longer generate event/archive candidates.
3. Enforce mode could be called as `context_only` without an override. Enforce now requires public execute mode before receipt/state work.
4. The public capability denylist allowed unknown/future capabilities and `knowledge.transfer`. It is now a positive ordinary-player allowlist with exact aliases.
5. Public error handlers reflected exception details. They now emit stable endpoint-owned codes.
6. Raw/admin routes remained in the GPT surface. Context, entity, NPC, faction, world-state, simulation, and authoring operations are excluded at source, exporter, launcher, static audit, and checked-in schema layers.
7. The inherited checked-in OpenAPI artifact was stale and unsafe. It was regenerated from the authorized runtime and re-audited.
8. Output acceptance was not one atomic publication decision. Schema 16 adds attempts, exact semantic attestations, and a packet acceptance fence.
9. Presentation/outbox writes could be separated from narrative acceptance. Receipt, output, director progression, presentation, outbox, and acceptance now share one `BEGIN IMMEDIATE` transaction.
10. Accepted replay validation was incomplete. Replay reconstructs and revalidates the canonical envelope, digest, evidence, packet, output, and receipt.
11. Companion callers could preseed self-attested evidence. Full-engine publication now requires a matching committed acceptance.
12. The public publication request accepted model-owned extension trees that were ignored. The request is now closed and rejects extra fields.
13. The safe latest-presentation reader initially treated ISO `accepted_at` as an integer; new runtime coverage caught and corrected it.
14. Launcher/export tests treated 30 Actions as an exact target. It is now a security ceiling; the curated surface has 21 operations.
15. The latest-presentation reader trusted a mutable presentation plus its equally mutable stored hash. It now reuses the full packet/candidate/receipt/output/evidence acceptance-chain validator in one database read scope; a rehashed-forgery regression proves fail-closed behavior.
16. Action curation still relied on a forbidden-operation denylist, and choice item length was absent from OpenAPI. Exporter and launcher now share an exact positive 21-operation allowlist, and each choice is schema-bounded to 500 characters.
17. Windows clipboard credential capture let a five-second `Get-Clipboard` timeout escape and abort startup after the safe Store install. PowerShell hosts now run in STA mode, failures use a 30-second retry backoff, and the Tk fallback runs in a killable five-second helper process. Clipboard writes use the same bounded pattern, so a copy failure cannot turn a verified startup into a false failure.
18. Safe knowledge FTS fallback swallowed every exception. Expected SQLite operational failures now emit a warning and degrade to no candidates; unexpected programming errors propagate to tests and operators.

## Implemented contracts

- Database schema: 16
- Turn protocol: WETP-1.0
- Narrative packet / quality receipt: NRP-1.2 / NQR-1.2
- Canonical publication candidate: WEPUB-1.0
- Presentation envelope: WEP-1.0
- Narrative evidence: NOV-1.0
- Acceptance decision key: `(campaign_id, packet_id)`
- Public publication statuses: `accepted`, `semantic_review_required`, `rejected`
- Foundry outbox statuses: `pending`, `sending`, `sent`, `dead`, `delivery_unknown`

## Verification evidence

Environment used for the fresh pass:

- Windows
- Python 3.12.10
- pytest 9.1.1
- FastAPI 0.141.1
- isolated external verification environment (excluded from the release)

Commands:

```powershell
python scripts\export_openapi.py
python scripts\static_openapi_surface_audit.py
ruff check --select E9,F63,F7,F82 app.py world_engine scripts tests
python -m py_compile app.py world_engine\*.py scripts\*.py
.venv-test\Scripts\python.exe -m pytest -q
```

Measured results at this report revision:

- current source-tree test suite after the SAFE startup hotfix: **419 passed, plus 6 subtests**;
- current startup/endpoint/Store regression gate: **63 passed, plus 6 subtests**;
- focused clipboard/SAFE Store gate: **38 passed, plus 6 subtests**;
- prior freshly extracted 4.3.0 ZIP test suite: **388/388 passed**;
- v4.3 atomic publication suite: **19/19 passed**;
- static source/OpenAPI audit: **pass**;
- source operations with operation IDs: **28**;
- curated GPT Actions: **21 / maximum 30**;
- duplicate operation IDs: **0**;
- forbidden operation IDs in source or checked-in curated schema: **0**;
- Ruff E9/F63/F7/F82: **pass**;
- Python compilation: **pass**;
- warning: one Starlette notice that `TestClient`'s current httpx integration is deprecated in favor of `httpx2`; it is non-failing dependency drift to monitor.

The current source hotfix also passed a live synthetic watchdog probe: a child that slept for ten seconds was terminated by the configured 0.2-second test timeout and returned control in 0.24 seconds. Clipboard contents were never printed. A new packaged artifact must be built from this source revision before claiming the extracted-ZIP numbers apply to the hotfix.

## Security/claim boundary

Supported claim: enforced public `resolveTurn` and accepted presentation paths provide bounded non-disclosure under their explicit actor/capability/schema boundaries.

Not claimed:

- strict non-interference;
- confidentiality of admin/GM routes, direct Python calls, local storage, logs, or operator tools;
- protection in narrative off/shadow/compare modes equivalent to enforce;
- semantic/inferential secrecy beyond literal/structured gates;
- multi-character ownership authorization;
- remote exactly-once Foundry delivery;
- a complete browser companion UI in 4.3.0;
- complete D&D/SRD content coverage.

Private director state can intentionally affect selection among safe story options, so strict non-interference would be an inaccurate design claim.

## Companion UI disposition

The companion UI engineering design is incorporated as a staged plan:

- 4.3.0: safe accepted presentation projection and presentation-only Foundry outbox;
- 4.3.1: per-principal snapshots, short-lived UI tokens, contiguous projection sequence, Socket.IO hints, HTTP snapshot authority, and React shell;
- later: maps, combat, journal, and richer presentation modules.

Campaign revision may skip. UI consumers must use a contiguous projection sequence and refetch the authoritative HTTP snapshot on gaps. The browser must never receive the GPT/World Engine bearer token.

## Remaining operational work

- configure and exercise an actual local Foundry relay if Foundry delivery is desired;
- reconcile `delivery_unknown` rows manually because the reference relay has no proven remote idempotency/fencing contract;
- add principal ownership and short-lived UI-token issuance before exposing browser snapshots;
- monitor the Starlette/httpx2 test-client migration.
- rerun `START_WORLD_ENGINE.bat` on the affected Windows host to confirm the local clipboard owner and desktop session behave normally with the bounded fallback.
