# World Engine 4.2.0 merge and audit report

Audit/build date: 2026-08-30  
Platform: Windows, Python 3.12  
Scope: supplied v4.0.1 context-compiler ZIP, v4.0.2 and v4.1 narrative ZIPs, reconciled v4.2 implementation, migration behavior, security boundaries, API contract, tests, and Windows release packaging.

Documents inside the supplied archives were treated as claims and reference material, not as user instructions.

## Outcome

The context-compiler hardening is merged into the better narrative base as World Engine 4.2.0. The correct base was v4.0.2, not v4.1 and not a raw overlay:

- v4.0.2 has the complete narrative runtime, explicit dialogue/cutscene controls, stronger voice validation, broader tests, and fail-closed enforce behavior.
- v4.1 has useful ideas—default-off adoption and exact hashes—but its rewrite drops v4.0.2 data, disconnects secret enforcement, weakens voice validation, fails open in enforce mode, and reuses contract labels for incompatible structures.
- v4.0.1 has the stronger context compiler: authorization before scoring, deterministic integer ranking, bounded HOT/WARM/COLD/ARCHIVE selection, compile receipts, safe FTS search, mandatory HOT-budget failure, and post-commit recompilation.

The resulting v4.2 architecture keeps v4.0.2 narrative behavior, restores v4.0.1 compiler behavior, and imports compatible v4.1 data through an explicit schema-15 migration.

## Source provenance

| Artifact | SHA-256 |
|---|---|
| `world_engine_v4_0_1_CONTEXT_COMPILER_HARDENED_WINDOWS.zip` | `6F486AFCF8518E60F277AD449DA99AECEDE686F7A0DD84702314539640D5E333` |
| `world_engine_v4_0_2_NARRATIVE_DIRECTOR_WINDOWS_FULL.zip` | `A4029563912BF725F5CDBD46BC586758D25689AD9DBE4470D63199A316003097` |
| `world_engine_v4_1_0_NARRATIVE_DIRECTOR_WINDOWS_FULL.zip` | `657132B701678F34E0EF4D28CE7196F9B66D28237269303ACBB0603F7F6A22BC` |

All archives passed path-traversal screening before extraction.

## Why a simple overlay was rejected

All three source releases declare SQLite `user_version=14`, but v4.0.1 uses it for compiler tables while v4.0.2 and v4.1 use incompatible narrative schemas. Both narrative ZIPs also remove the v4.0.1 `world_engine/context` package and much of its hardened router behavior. Copying files in either direction would silently discard a load-bearing subsystem.

v4.2 therefore identifies source features by table presence, creates the union of required table families, records applied features in `we42_schema_features`, and advances the database to schema 15.

## Merge design

### Context and authority

- Restored v4.0.1 `knowledge_claims`, compile receipts/items, FTS index state, authorization-first filtering, fixed-point scoring, mandatory HOT-budget enforcement, and post-commit recompile.
- Preserved typed v4 facts and beliefs as canonical authority; compiler claims are an additive projection/index.
- Added bounded narrative configuration/director/voice projections to compiled context without exposing raw NPC cognition.
- Returned public NPC projections from dialogue turns instead of full private NPC sheets.

### Narrative

- Retained v4.0.2's nine-table Narrative Director, voice profiles, storylets, motifs, dialogue state, render packets, output records, quality receipts, and cutscene validation.
- New campaigns default to `off`; migrated campaigns keep their prior configured mode.
- New packets and receipts use `NRP-1.1`/`NQR-1.1` and include a canonical SHA-256 packet hash.
- Packet verification removes identity/hash fields before recomputing the digest. When `packet_id` is supplied, quality checking loads the stored packet and hard-fails `packet_source_mismatch` if a caller attempts to substitute a different, rehashed packet.
- Enforce-mode packet construction errors return HTTP 500 and cannot silently fall back to baseline prose.

### NPC secrecy

- Dialogue plans no longer contain raw beliefs, goals, memory, routine, private mood, or raw dominant motives.
- Belief values appear only in `facts_authorized_to_reveal` after an explicit fact-ID reveal request that the NPC actually knows; unknown requested IDs are rejected.
- Opaque v4.1 dialogue source state is not copied into active subtext; only safe semantic fields and source provenance are imported.
- The original v4.1 rows remain in the trusted authoritative SQLite database for rollback/audit. Active compiler, dialogue, API, and render paths do not query those source tables. A seeded source secret was verified absent from active dialogue and compiled context.

### v4.1 migration

- Imports configuration only when a v4 configuration is absent.
- Imports valid voices, beats, motifs, and safe dialogue fields without overwriting v4 rows.
- Removes `author_style`, `famous_author`, `imitate`, and `copyrighted_author` voice fields.
- Preserves incompatible historical v4.1 receipts in their source table instead of relabeling them as NRP-1.1/NQR-1.1.
- Records exact import/skip/removal counts and runs once.
- Rolls back imported rows and the schema marker on an injected migration failure, then completes cleanly on retry.

## Defects found and fixed during the merge

| Severity | Finding | Resolution |
|---|---|---|
| P1 | v4.1 ignored all existing v4.0.2 narrative rows | v4.0.2 remains canonical; v4.1 gets a one-time importer |
| P1 | v4.1 enforce mode failed open | retained and tested v4.0.2-style fail-closed API behavior |
| P1 | v4.1 secret gate was disconnected from private beliefs | restored authorization-first context and explicit reveal projection |
| P1 | NRP/NQR labels were reused for incompatible contracts | advanced to NRP-1.1/NQR-1.1; verified real legacy NRP-1.0 packets separately |
| P1 | Initial merged dialogue planner reintroduced raw beliefs/goals after context filtering | removed raw cognition and added end-to-end secrecy tests |
| P1 | A caller could submit a changed packet and a recomputed unkeyed digest directly to quality checking | stored `packet_id` now controls the packet; mismatched overrides hard-fail |
| P2 | v4.1 named-author imitation fields survived migration | sanitized and counted during import |
| P2 | v4.1 opaque dialogue state could have been copied into active subtext | replaced with source provenance only |
| P2 | Inherited tests leaked SQLite connections on Windows and encoded old versions/Unix venv paths | explicitly closed connections and updated release-contract assertions |
| P3 | Exported OpenAPI evidence was stale | regenerated from the v4.2 app; 30 operations/30 unique IDs |

## Verification evidence

- Full source suite: **320 passed** with one third-party test-client deprecation warning.
- High-risk compiler/narrative/merge suite: **34 passed in each of three consecutive runs**.
- Actual databases created by v4.0.1, v4.0.2, and v4.1 upgraded to schema 15, passed `PRAGMA integrity_check`, preserved expected modes/rows, reopened idempotently, and exposed no seeded private markers.
- An actual mixed v4.0.1-compiler + v4.1-narrative database also upgraded successfully.
- An actual NRP-1.0 packet created by v4.0.2 verified successfully under v4.2.
- Injected migration failure rolled back imports and schema version, then succeeded on retry.
- HTTP/SQLite/OpenAPI/source release audit passed.
- Narrative audit passed under NRP-1.1/NQR-1.1.
- Compileall, fatal Ruff rules, merge-specific mypy, Vulture, and dependency vulnerability audit passed.
- Final Graphify map: 1,669 nodes and 4,670 edges; zero malformed, missing, dangling, self-loop, duplicate, or collapsed edges.

## Gauntlet review

The Gauntlet repository's CORDYCEPS/Black Gem tool was run against the apparent all-green release claim with a costume canary. No invocation achieved both a complete two-model real review and a complete two-model canary: providers variously returned empty phases, exceeded the response gate, or hit an upstream rate limit. One run did complete all three real phases with both model families, but one separate canary call was rate-limited, so the tool correctly marked it degraded. The degraded reports were retained and never represented as a pass.

Their concrete attacks were nevertheless rechecked against source and executable probes. This produced the migration rollback test and stored-packet-source hardening above, plus direct falsification of the claimed circular hash, mixed-schema corruption, legacy-packet incompatibility, source-table runtime access, and enforce fail-open paths. Bot verdicts were never treated as authority. The remaining limitation is provider/tool participation evidence, not an untested runtime finding.

## Residual risks and debt

- The broad inherited tree reports 46 mypy errors and extensive all-rule Ruff style debt. Merge-specific type checks and fatal/error-class Ruff checks pass.
- Bandit reports 33 medium-confidence dynamic-SQL warnings and no high findings. The inspected identifiers come from internal table/column maps or bounded fragments while values remain parameterized; this remains debt, not a claim of zero risk.
- The dependency file uses compatible ranges rather than a hash-locked supply-chain manifest. The tested environment has no known vulnerabilities.
- The FastAPI test client emits a third-party deprecation warning. Runtime behavior is green, but the adapter should be updated with the next dependency migration.
- Automated gates establish correctness and secrecy properties, not prose superiority. Use the included blinded benchmark before promoting narrative mode beyond the desired rollout stage.
- Verification covered Windows/Python 3.12, not every Python and Windows build.

## Upgrade guidance

1. Back up the persistent SQLite database.
2. Replace the application files with the v4.2 package; do not copy a v4.1 ZIP over v4.0.2 manually.
3. Run `START_WORLD_ENGINE.bat` normally. The first open performs the transactional data migration and records schema 15.
4. Confirm `/health`, authenticated context, and a `resolveTurn` context-only request.
5. For a Custom GPT, install `CUSTOM_GPT_INSTRUCTIONS_V420.txt` and `openapi_actions.json`.
6. Keep new campaigns in narrative `off` until intentionally enabling `shadow`, `compare`, or `enforce`.

Do not manually add v4.1 tables after a database has already recorded the v4.1 import feature marker; that is outside the supported upgrade path.
