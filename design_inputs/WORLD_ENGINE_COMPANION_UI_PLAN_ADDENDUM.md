# World Engine 4.3 Companion UI Plan Addendum

**Source reviewed:** `WORLD_ENGINE_COMPANION_UI_INTEGRATION_ENGINEERING_DESIGN.md`  
**SHA-256:** `1375962bb36c207cb94e1ac3b136df954438e8baa3dec836c792be94d2cae3aa`  
**Status:** Engineering proposal incorporated with corrections; it is not executable authority and does not override the verified v4.3 confidentiality and output-hardening gates.

## Verdict

The design is a strong basis for a standalone companion UI. Its central boundary is correct: World Engine owns state, ChatGPT supplies accepted presentation prose, and the browser receives a deliberately safe projection. It should not be merged wholesale into v4.3.0. The safest delivery order is:

1. **v4.3.0 — output and companion foundation:** close `WE43-001`; implement NRP/NQR 1.2; add the accepted-presentation receipt; add schema 16 companion persistence; publish the presentation through a constrained action; expose only the latest safe presentation read; deliver presentation data to Foundry through a hardened outbox.
2. **v4.3.1 — minimal standalone UI:** safe snapshot contract, principal-scoped session tokens, Socket.IO recovery contract, React shell, campaign summary, narrative pane, choices, and connection status.
3. **Later UI increments:** relationships, timelines, maps, combat, settings, richer live panels, and Foundry entity synchronization. These expand the confidentiality surface and must pass their own projection tests.

The UI therefore becomes a consumer of the v4.3 safety boundary, not a second path around it.

## Corrected dependency order

```text
WE43-001 confidentiality closure
  -> accepted-output receipt + immutable private validation envelope
  -> publishPresentation proof binding
  -> safe latest-presentation read
  -> allowlisted per-principal UI snapshot
  -> principal-scoped short-lived browser session
  -> projection-sequenced Socket.IO hints and recovery
  -> React companion shell
  -> maps, combat, and Foundry entity sync
```

### Gate UI-0 — source and licence freeze

- Reuse permissively licensed libraries only after pinning their exact versions and licence texts.
- HAIP, Foundry REST API, TanStack Query, Socket.IO, Zustand, and MapLibre are candidates for adaptation or dependency use, not automatic copy authority.
- Marinara, Silly Map, RPG Companion, and other AGPL projects are reference-only unless the resulting distribution intentionally adopts compatible obligations.
- Treat any repository without a verified licence as view-only. Do not copy its code, assets, or distinctive implementation.

### Gate UI-1 — actual publication proof

`publishPresentation` must not accept arbitrary authenticated narration. The request must bind to evidence produced by the accepted narrative path:

- `campaign_id`, `turn_id`, and authoritative `revision`;
- `packet_id` and the canonical public-packet digest;
- accepted `output_hash`, accepted NQR result, and one-time or idempotent output receipt;
- canonical serializer and response-contract version.

The server verifies this evidence against its immutable private validation record, then stores presentation text and choices without mutating canonical world state. Replays with the same idempotency key return the same result; conflicting reuse is rejected.

### Gate UI-2 — safe snapshot and browser session

- `GET /api/ui/snapshot` is a new allowlisted projection, never a serialization of campaign tables or the full turn response.
- Snapshot fields are explicitly typed and versioned. Unknown server fields do not become public by default.
- Every view is scoped to a campaign and principal. A player and GM may have different projections at the same campaign revision.
- The browser receives a short-lived, least-privilege campaign/player token for read and socket access. It never receives or stores the GPT action bearer token.
- Whole-response differential tests assert that hidden facts, hidden events, private validation literals, provider secrets, and outbox internals are absent from success, validation-error, and recovery responses.

### Gate UI-3 — real-time recovery

Socket events are invalidation hints; the HTTP snapshot remains authoritative.

Each event carries both:

- `campaign_revision`, which may legitimately skip for a player when intervening changes were invisible; and
- a contiguous per-principal `projection_sequence`, whose gap forces a snapshot refresh.

Rooms are principal/projection scoped, not merely campaign scoped. On reconnect, unrecovered connection, sequence gap, schema mismatch, or unknown event type, the client invalidates relevant TanStack Query keys and fetches a fresh snapshot. Socket.IO connection-state recovery is an optimization, not a replacement for World Engine reconciliation.

### Gate UI-4 — minimal companion shell

- React/Vite application with TanStack Query owning server state.
- Zustand limited to ephemeral UI state such as panel layout, selections, and local preferences.
- One bootstrap snapshot followed by revision hints; no client-side reconstruction of hidden authoritative state.
- Raw HTML disabled. Render narration as text or through a tightly allowlisted Markdown pipeline.
- Initial panels: connection state, campaign summary, accepted narrative, choices, and compact recent public events.
- Accessibility, keyboard operation, empty/loading/error states, and reconnect behavior are release criteria.

### Gate UI-5 — maps, combat, and extended panels

- MapLibre consumes only already-safe GeoJSON projections. Clustering and visualization never determine visibility.
- Combat, relationship, timeline, and entity panels require field-by-field projection review.
- Foundry entity synchronization follows the proven presentation-only bridge. It must not be implemented by expanding the presentation action into an unconstrained state mutation API.

## Contracts that supersede the proposal where they differ

| Area | Corrected contract |
|---|---|
| Publication | Accepted output receipt and digest chain are required; authentication alone is insufficient. |
| Revisions | Use authoritative `campaign_revision` plus per-principal `projection_sequence`; do not require campaign revisions to be contiguous for every viewer. |
| Realtime rooms | Principal/projection scoped; a bare campaign room can leak another viewer's fields. |
| Snapshot | Closed allowlist DTO generated from an explicit believer/principal view. |
| Tokens | Short-lived UI token with campaign, principal, scopes, expiry, audience, and revocation/version claims. |
| Persistence | Schema 16 stores presentations, provider profiles/bindings, outbox, and delivery attempts. Presence and transient socket membership remain in memory. UI preferences/sessions are deferred until the UI contract is proven. |
| Foundry | Presentation-only delivery through an idempotent leased outbox; ambiguous delivery becomes `delivery_unknown` and requires reconciliation. |
| Performance | Latency and rendering numbers in the design are targets to measure, not verified properties. |

## Reuse and adaptation decisions

| Candidate | Use | Boundary |
|---|---|---|
| HAIP | Architectural reference for companion separation | Adapt concepts; do not inherit its trust assumptions without proof binding. |
| Socket.IO / python-socketio | Realtime transport | Authenticated principal rooms, bounded payloads, HTTP snapshot recovery. |
| TanStack Query | Browser server-state cache | Query invalidation and snapshot refresh; no hidden-state inference. |
| Zustand | Ephemeral UI state | No authoritative campaign data. |
| MapLibre GL JS | Later map rendering | Only safe projected GeoJSON. |
| Foundry REST API | Downstream presentation transport | Allowlisted endpoints, redirect/proxy/DNS/IP defenses, bounded responses, explicit ambiguous-delivery handling. |
| AGPL or unlicensed projects | UX and architecture reference | No copied implementation or assets in the distributable build. |

## Verification gates

The UI work is accepted only after all of the following are independently demonstrated:

1. A secret inserted into canonical facts or events is absent from the entire player HTTP response and all socket payloads.
2. A valid accepted narration can be published once; forged, stale, cross-campaign, altered, and replay-conflict requests fail closed.
3. A player and GM at the same campaign revision receive different correct projections without room crossover.
4. Disconnect, replay-window loss, sequence gap, and server restart converge to the authoritative snapshot.
5. Provider SSRF, redirect, proxy-environment, credential, payload-size, and response-size tests fail safely.
6. Outbox crash points cannot silently mark an ambiguous downstream request as unsent or safely retryable.
7. The bundled OpenAPI action count and GPT-facing surface remain within the chosen compatibility budget.
8. Fresh-build and upgrade migrations produce schema 16 with no partial state after rollback tests.

## Plan impact

This addendum does not change the v4.3.0 critical path. It narrows the companion foundation to the contracts the later UI needs and prevents the UI proposal from widening the current security fix. The standalone UI is now an explicit v4.3.1 deliverable after the v4.3.0 runtime and bounded non-disclosure gates pass.

## Claim boundary: non-disclosure, not strict non-interference

Strict differential non-interference would require the public response to remain identical when any private director state changes. That is incompatible with a director that is intentionally allowed to use private state to select an otherwise safe story beat. v4.3 therefore claims and tests a narrower property:

- closed public response schemas;
- no private literals, private identifiers, sealed validation context, unscoped events, or canonical-only facts in player responses;
- whole-response canary and differential disclosure tests across success and error paths; and
- player-visible facts selected only through the believer/principal projection.

If strict non-interference is later required, private state must be prevented from influencing every public selection as well as being redacted from the result.
