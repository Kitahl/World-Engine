# World Engine 4.3.0 — Output + Companion Hardened

World Engine is a persistent, deterministic world-simulation and tabletop-RPG backend for ChatGPT GPT Actions. The backend owns canon, rules, random outcomes, state, knowledge, progression, and consequences. The model interprets player intent and renders only authorized results.

## What 4.3 adds

- closes the `WE43-001` canonical-fact and event leak in enforced public turns;
- binds character knowledge reads to that character’s believer view;
- replaces the public turn denylist with a positive player-capability allowlist;
- rejects enforce-mode context-only/downgrade requests before state mutation;
- upgrades the narrative packet and receipt contracts to NRP-1.2/NQR-1.2;
- validates prose against private server-side evidence that is never returned to the renderer;
- gives each packet one authoritative publication decision;
- atomically commits receipt, accepted output, director progression, presentation, outbox, and acceptance;
- supports exact semantic review bound to the stored candidate digest;
- publishes a closed, immutable presentation envelope and loopback-only Foundry outbox;
- exposes no raw context, entity, NPC, faction, world-state, simulation, or authoring operations as GPT Actions;
- provides a safe trusted-backend reader for the latest accepted presentation;
- adds operator CLIs for semantic review and Foundry delivery.

This release preserves the 4.2 context-compiler/narrative merge and compatible 4.1 narrative import.

## Measured release status

| Measure | Value |
|---|---:|
| Release | **4.3.0** |
| Database schema | **16** |
| Turn protocol | **WETP-1.0** |
| Narrative packet / receipt | **NRP-1.2 / NQR-1.2** |
| Publication candidate | **WEPUB-1.0** |
| Presentation envelope | **WEP-1.0** |
| Registered capability manifests | **30** |
| Source API operations with operation IDs | **28** |
| Curated GPT Actions | **21** (maximum 30) |
| Duplicate GPT operation IDs | **0** |
| Default narrative mode | **`off`** |
| Normal gameplay entry point | **`resolveTurn`** |

The fresh source-tree test count and verification limits are recorded in `BUILD_REPORT_V430.md`.

## Quick start on Windows

1. Extract the complete ZIP.
2. Double-click `START_WORLD_ENGINE.bat`.
3. Wait for `WORLD ENGINE 4.3 CONNECTION READY`.
4. Import the generated `openapi_actions_PERMANENT.json` into GPT Actions.
5. Configure Bearer authentication with the private key copied by the launcher.
6. Use `CUSTOM_GPT_INSTRUCTIONS_V430.txt` as the GPT instructions.

Persistent data remains under `%LOCALAPPDATA%\WorldEngine\`. Default startup does not require administrator rights.

## Authority and public boundary

```text
player message
  → model intent normalization
  → resolveTurn / WETP-1.0
  → positive public capability allowlist
  → believer-scoped bounded context
  → deterministic kernels + atomic state commits
  → closed public result / NRP-1.2
  → exact prose publication gate
  → immutable accepted presentation + outbox
```

Public `resolveTurn` accepts character actors only. Unknown, private, administrative, and future capabilities fail closed. Ordinary allowed capabilities cover movement/routing, rules checks/attacks, conditions/resources, relationships, dialogue context, quests, time advance, combat, progression, and visual cues.

In configured `enforce` mode, the public turn must be `execute`; callers cannot obtain the unfiltered context-only payload or downgrade narrative mode. Non-GM turns do not compile global facts, event history, or archive candidates. Public error payloads use endpoint-owned codes and never reflect exception text.

The defensible claim is bounded non-disclosure for enforced public `resolveTurn` and accepted presentation paths. This is not strict non-interference: private director state may intentionally influence which safe story option is selected. Admin/GM access, direct Python calls, storage/log access, off/shadow/compare behavior, semantic inference, and multi-character ownership authorization are outside that claim.

## Narrative runtime and publication

New campaigns default to narrative mode `off`.

- `shadow`: compile an internal comparison packet; baseline presentation remains authoritative.
- `compare`: retain baseline output and internal candidate evidence for evaluation.
- `enforce`: render only from `_narrative_render_packet`; do not read omitted context/events.

For an enabled packet, the model calls `publishPresentation` with only:

- `campaign_id`
- `presentation_id`
- `packet_id`
- `turn_id`
- `expected_revision`
- exact `narration`
- exact displayed `choices`

Extra fields are rejected. The server computes the canonical candidate and digest. One `(campaign_id, packet_id)` decision fence prevents competing accepted outputs. Exact accepted replay is idempotent; a different candidate conflicts.

When semantic authority review is required, publication returns `semantic_review_required` and writes only an audit attempt. Nothing is presented or queued until a human or trusted server approves the exact stored digest.

## Trusted semantic review

Inspect the exact candidate:

```powershell
python scripts\publication_review.py inspect --campaign default --attempt-id ATTEMPT_ID
```

Approve or reject only after copying the displayed digest:

```powershell
python scripts\publication_review.py decide --campaign default --attempt-id ATTEMPT_ID --candidate-digest DIGEST --reviewer-id OPERATOR --decision approve
```

This tool is local/operator-only and is not a GPT Action.

## Companion / Foundry delivery

Accepted presentations are immutable, digest-checked, and written with a transactional outbox. Network delivery happens only after commit. The relay origin must be a literal loopback IP; redirects, proxies, hostnames, credential-bearing URLs, and non-loopback destinations are rejected.

Set `WORLD_ENGINE_FOUNDRY_API_KEY` and optionally `WORLD_ENGINE_FOUNDRY_URL`, then run:

```powershell
START_COMPANION_WORKER.bat
```

The current Foundry relay has no proven remote idempotency/fencing contract. Post-send uncertainty becomes `delivery_unknown` and requires operator reconciliation; this release makes no remote exactly-once claim.

`GET /api/presentation/latest` is hidden from GPT Actions and returns only a validated accepted public envelope to a trusted backend. Browser-safe per-principal snapshots, short-lived UI tokens, projection-sequenced Socket.IO, and the React companion shell are planned for 4.3.1; the browser must never receive the World Engine/GPT bearer token.

## Important files

- `BUILD_REPORT_V430.md` — verification evidence and limits
- `V4_3_CHANGELOG.md` — release changes
- `GPT_INSTRUCTIONS.md` / `CUSTOM_GPT_INSTRUCTIONS_V430.txt` — corrected GPT contract
- `openapi_actions.json` — regenerated curated schema
- `scripts/static_openapi_surface_audit.py` — source + checked-in schema gate
- `scripts/publication_review.py` — trusted semantic review
- `scripts/companion_worker.py` — bounded outbox worker
- `WETP_PROTOCOL.md` — unified turn protocol
- `V4_2_CHANGELOG.md` — inherited merge history

## License and integration note

External Foundry/companion projects examined during design are references only unless their licenses permit reuse. No unlicensed or AGPL implementation was copied into this package.
