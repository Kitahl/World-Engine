# World Engine 5.1.0 — companion UI adaptation report

## What was adapted from the 4.5 candidate

| Candidate quality | How it was adapted |
|---|---|
| Monochrome base, one state-derived accent | `--accent` is computed in `app.js` from the **projected** location id, world hour, and weather. Severity keeps fixed semantic colours so an alert never changes hue with the weather. |
| Central stage dominated by scene art | `.scene-art` canvas sits above the stage heading; narration and modes render beneath it. |
| Collapsible navigation rail | `#rail-toggle`, `cockpit[data-rail]`. Icon-only when collapsed, accessible name and `aria-expanded` retained. |
| Collapsible contextual drawer | `#drawer-toggle` / `#drawer-restore`, `cockpit[data-drawer]`. A hidden drawer keeps a labelled way back. |
| No persistent bottom bar | The existing player dock is retained as status, not navigation. |
| Thin scrollbars | `scrollbar-width: thin` plus a borderless `::-webkit-scrollbar` treatment. |
| Procedural canvas artwork | Deterministic, seeded by the projection's `terrain_seed`; layered ridges with aerial perspective, ground plane, weather veils, day/night sky. |
| Visual-novel scene opening | `maybeOpenScene()` fires only on a genuine public-location change. |
| Server-derived alert emphasis | `notification_summary.tier` is computed in Python and only styled by the client. |
| Responsive behaviour | Breakpoints at 1180/980/720 px; the stage never collapses into the rail track. |
| Reduced motion | `prefers-reduced-motion` suppresses non-essential transitions. |

## What was deliberately rejected

- Candidate `app.py`, `world_engine/ui_projection.py`, `/ui` and `/api/ui/*` routes.
- Browser `fetch`/XHR/WebSocket, bearer-key entry, base-URL field, Connect screen.
- Arbitrary entity lookup; all-location and all-faction enumeration.
- Raw events, context packets, direct `/api/turn` execution from the UI.
- Remote image loading and dereferencing stored image references.
- `innerHTML` and every other executable DOM sink.

The candidate's free-text composer was rejected outright: World Engine is not an
LLM, and a chat-shaped input invites the user to believe otherwise. The
companion presents accepted narration and choices only.

## Boundary retained

Native Python/pywebview, loopback ephemeral asset server, `connect-src 'none'`,
`DesktopProjectionKernel` visibility rules, the procedural world forge, all
existing runtime systems, and exactly five public GPT Actions.

## Completed integration

- Added player-known map pan/zoom and pointer/keyboard selection. Selection is
  local presentation state and never invokes movement or a generic engine call.
- Added a bounded Chronicle sourced only from acceptance-chain-validated public
  presentations inside the same SQLite snapshot transaction.
- Corrected compact layout behavior: the seven-mode rail remains usable at
  760 px and the fixed drawer defaults closed below 980 px.
- Enforced monotonic projection application with an executable JavaScript
  regression for duplicates and lower sequences.
- Closed the Python bridge to exactly eight explicit functions, generic bounded
  errors, count-only authoring stage receipts, serialized endpoint operations,
  and allowlisted endpoint values.
- Kept the Companion as a standalone Python/pywebview desktop application. It
  uses bundled HTML/CSS/JavaScript internally; it is not a hosted browser UI.

## Verification record

| Item | Result |
|---|---|
| Branch | `claude/v5.1.0-pywebview-ui-adaptation` |
| Base before final corrections | `006bb725b732` |
| Integrated focused security/UI/lifecycle gate | **187 passed + 2 subtests** |
| Fatal Python lint + JavaScript syntax | **PASS** |
| Public Action audit | **PASS — exactly 5** |
| Release audits | **PASS — OpenAPI/SQLite/HTTP/source/runtime** |
| Narrative audit | **PASS — NRP-1.2 / NQR-1.2** |
| JavaScript syntax and executed ordering regression | **PASS** |

The authoritative final source commit, complete source and clean-extracted test
