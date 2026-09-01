# World Engine 4.7.0 Build and Verification Report

Date: 2026-08-31

Target: full Windows package, schema 20

Merge strategy: selective integration; wholesale donor overlays rejected

## Outcome

World Engine 4.7.0 integrates the hardened canonical mechanism contract, finite economy/logistics runtime, aggregate population/settlement runtime, WEGEN-1.2 generation, and WE-DESKTOP-1.1 native companion projections on the existing output/PBEM/environment foundation.

The system remains a native Python/PyWebView companion, not a hosted browser application. Procedural generation expands the engine's stateful campaign abstraction rather than attempting centimeter-scale terrain or planetary physics.

## Source gates

| Gate | Result |
|---|---:|
| Full regression suite | 568 passed |
| Full-suite subtests | 34 passed |
| Focused repaired/legacy suite | 167 passed |
| Focused subtests | 9 passed |
| Sol final integration sample | 103 passed + 19 subtests |
| Python compilation | PASS |
| Companion JavaScript syntax | PASS |
| Static GPT Action inventory | PASS — exact 5 |
| 4.7 OpenAPI audit | PASS |
| 4.7 fresh/migrated SQLite audit | PASS |
| 4.7 HTTP/security audit | PASS |
| 4.7 source/instruction audit | PASS |
| 4.7 feature audit | PASS |

The release verifier confirms schema 20, SQLite integrity, zero foreign-key violations, required mechanism/economy/population tables, 33 capability manifests, exact five public GPT operations, resolvable OpenAPI references, WEGEN-1.2 with backward validation, PBEM-2.2, WE-DESKTOP-1.1, environment behavior, real mechanism execution/receipt creation, generated markets/population, and instruction mirroring under the 8,000-byte limit.

## Security and authority decisions

- Public GPT operations remain exactly resolveTurn, publishPresentation, saveVisualProfile, buildImageCue, and recordImageGeneration.
- The new economy/population manifests and PBEM policies are integrated inside the TurnRouter, but the external app capability allowlist is deliberately not broadened without a separate public-surface security decision.
- Native companion projections expose public/local markets, aggregate current-location population, player balances, and canonical inventory only.
- MOP-1.0 is trusted/internal; it is not a public arbitrary-effect Action.
- Optional MCP exposes operator functions only on loopback and rejects non-loopback peers even if Uvicorn is started with an unsafe bind address.
- Complete backups remain SQLite-native; the legacy JSON snapshot is not represented as a complete private-state backup.

## Lint status

Correctness-only Ruff checks are required on the new 4.7 files. Repo-wide Ruff is not yet a release gate: the historical tree reports 292 style/debt findings under the current broad rule set. Those findings are recorded rather than misrepresented as introduced or cleared by this integration.

## Packaging gate

scripts/package_v470.py refuses a dirty tree, packages only tracked files, hashes critical inputs, verifies exact ZIP inventory/integrity, extracts into a clean temporary directory, and reruns compilation, the full suite, static Action audit, narrative audit, and the 4.7 release verifier. Final archive size, SHA-256, clean-extracted counts, and unverified boundaries are recorded in WORLD_ENGINE_V470_HANDOFF.json beside the ZIP.

## Remaining machine-dependent boundaries

- Actual Windows double-click startup and Service Control Manager execution.
- Live ngrok, Cloudflare, and Tailscale connectivity and account state.
- Live Foundry relay delivery.
- Graphical PyWebView/Edge rendering and OS clipboard integration.

These boundaries are not counted as passed by source or clean-extraction tests.
