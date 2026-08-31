# World Engine 4.5.0 Changelog

## Added

- PBEM 2.1 public player-intent enforcement with actor binding, server-derived checks, prerequisite gating, and actor/mode/enforcement idempotency.
- Schema-17 Environment + Consequence runtime with six environment tables, 11 seeded materials, 17 effect types, sparse hourly integration, six-hour weather, seasons, exposure, propagation, terrain damage, afflictions, resource/social pressure, and opt-in disasters.
- `environment.interact` public capability for local inspect, ignite, extinguish, and douse attempts.
- WEGEN-1.1 deterministic campaign scaffolding with coherent neighboring biomes, authoritative location climates, biome regions, and seasonal resource integration.
- Backward validation support for staged WEGEN-1.0 batches.
- Standalone local Python/pywebview Companion UI and safe `WE-DESKTOP-1.0` projections.
- Five-Action least-privilege GPT surface: `resolveTurn`, `publishPresentation`, `saveVisualProfile`, `buildImageCue`, and `recordImageGeneration`.
- 4.5 release verifier and exact-inventory clean-extraction packager.

## Changed

- Public GPT turns always enforce PBEM; trusted setup, direct mechanics, simulation, authoring, and world-event routes are hidden from OpenAPI and require a separate operator key.
- Public world advance is capped at 1,440 minutes; generated authoring dry-runs are capped at 365 days.
- Generated climates activate weather and regional seasons drive resource growth.
- The Companion is described and shipped as a standalone desktop application, not a hosted browser companion.
- Startup creates/reuses distinct public and operator credentials, probes `pywebview` in retained virtual environments, and keeps local play available when the optional tunnel is degraded.
- Ngrok installation remains Microsoft Store/WinGet-first with no portable executable download.

## Fixed

- Environment public callers can no longer supply material IDs, raw properties/state, effect intensity, or amount.
- Remote environment targets, public zones, source-less extinguishing, and caller-invented ignition sources are rejected.
- Ambient weather now applies once per target instead of once per active effect; actors with effects still receive exposure/lightning.
- Explicit custom weather tables no longer gain unrequested seasonal conditions.
- Direct climate writes reject unknown, non-finite, boolean, string, zero-only, overflow, and over-limit weight sets atomically.
- New effects are deferred until old effects complete the current boundary, preventing order-dependent overwrite and same-boundary double processing.
- Environment targets follow actor movement; earthquakes stay inside the selected location; terrain destruction and support collapse emit once.
- Canonical tile movement cost is preserved when temporary environment modifiers clear.
- PBEM rejects malformed direct consequences, cross-actor rules payloads, caller-opt-in FPC behavior, and oversized public time advancement.
- The GPT-visible surface can no longer bypass PBEM through direct setup/mechanics/authoring operations.
- Startup clipboard timeout failures are bounded and fall back without aborting the local engine.

## Compatibility

- Existing databases migrate to schema 17 without deleting prior campaigns.
- Historical narrative/output receipts remain versioned by their original component releases.
- Staged WEGEN-1.0 generation payloads remain validatable; new generation emits WEGEN-1.1.
- Live ngrok/Cloudflare/Tailscale connectivity, Windows services, Foundry relay delivery, graphical pywebview rendering, and OS clipboard integration remain machine-dependent verification boundaries.
