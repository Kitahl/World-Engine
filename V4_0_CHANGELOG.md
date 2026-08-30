# World Engine 4.0.0 Changelog

## Unified Turn Router

- Adds WETP-1.0 and GPT Action `resolveTurn`.
- Adds four operation modes: execute, plan, context_only, capabilities.
- Supports up to 20 ordered intents with aliases, explicit dependencies, optional steps, revision checks and stable idempotency keys.
- Reuses existing deterministic v3.9.x providers instead of replacing them.

## Capability Registry

- Adds 29 default capability manifests.
- Adds authority modes READ, RESOLVED, SIMULATED, NARRATED and AUTHOR.
- Declares provider, domain, version, requirements, writes, context tiers, priority and metadata.

## Entity + Relationship Graph

- Adds universal campaign-scoped entities and typed relations.
- Synchronizes existing typed entities into universal identities.
- Supports relation provenance, strength, direction and validity intervals.

## Knowledge Provenance

- Adds canonical facts separated from per-entity beliefs.
- Adds confidence/status/provenance and information-transfer records.
- Adds sender/receiver/channel/credibility/distortion and transfer lineage.

## Context Compiler

- Adds deterministic HOT/WARM/COLD/ARCHIVE compilation.
- Adds explicit character/token budgets, included/omitted inspection, activation reasons and digest.

## Automatic Connection Startup

- Replaces the failed manual token-paste interaction.
- Reuses existing ngrok config first, then environment credentials, then official dashboard Copy + automatic clipboard capture.
- Automatically retrieves/creates the persistent World Engine API key.
- Automatically starts and authenticates the backend.
- Automatically starts/repairs the stable HTTPS endpoint.
- Automatically verifies public health and protected Bearer access.
- Automatically regenerates the permanent Action schema.
- Adds one no-admin per-user supervisor with duplicate-process protection and periodic self-repair.
- Preserves the configured hostname during repair rather than silently switching domains.

## Preserved

All v3.9.9 rules, WORLD/SCENE/COMBAT, NPC cognition/DECIDE/GOAP, progression, image continuity, music fallback, sparse 3D geography, safe authoring, connection diagnostics and persistent-data behavior remain present.

## Schema

Database schema advances from 12 to 13.
