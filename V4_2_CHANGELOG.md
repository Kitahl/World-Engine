# World Engine 4.2.0 changelog

World Engine 4.2.0 is a reconciled release, not a file overlay. It combines the security and determinism work from 4.0.1 with the more complete 4.0.2 narrative runtime, then imports compatible 4.1 data through an explicit migration boundary.

## Merge decisions

- Restored the 4.0.1 authorization-first context compiler, deterministic integer scoring, HOT-budget enforcement, FTS-backed claim search, compilation receipts, and post-commit recompilation.
- Retained the 4.0.2 Narrative Director, storylets, dialogue planning, voice profiles, motifs, render packets, quality receipts, and fail-closed enforce-mode API behavior.
- Adopted 4.1's safer opt-in posture: new campaigns default narrative mode to `off`.
- Added a one-time 4.1 importer. Compatible configuration, voice, beat, motif, and dialogue state is copied into the 4.2 tables; original `we41_*` rows and historical receipts remain untouched.
- Did not reuse the 4.1 packet/receipt payloads as 4.0.2 contracts. New output contracts are `NRP-1.1` and `NQR-1.1`; legacy `NRP-1.0` packets can still be verified when their original digest is valid.

## Security and correctness

- Schema version is now 15. Schema-14 identity is determined by actual table presence because 4.0.1, 4.0.2, and 4.1 used the same SQLite `user_version` for incompatible additions.
- Narrative packets contain a canonical SHA-256 `packet_hash`. Quality checking fails hard with `packet_integrity` if the packet is modified.
- Narrative and dialogue projections no longer expose raw NPC beliefs, goals, mood, memories, or routines. Authorized fact IDs are the only path for explicitly revealed fact values.
- 4.1 dialogue state is normalized during import; opaque/private source state remains only in the preserved `we41_dialogue_memory` rows.
- 4.1 voice fields requesting named-author imitation are removed during import and counted in the migration receipt.
- New narrative configuration defaults to `off`; existing 4.0.2 and 4.1 campaign modes are preserved.

## Compatibility

- Direct upgrades from 4.0.1, 4.0.2, and 4.1 are supported.
- Existing campaigns, revisions, facts, beliefs, narrative rows, and source migration rows are not deleted.
- WETP-1.0 and the existing GPT Action endpoints remain stable.
- `narrative.manage` is registered as the unified-turn capability for narrative configuration, packet compilation, quality checks, and output recording.

## Operator notes

Back up the persistent SQLite database before replacing an existing installation. Start 4.2 normally; migration runs transactionally on first open and is idempotent on later opens. Do not manually copy 4.1 tables into a database after it has already completed the 4.2 migration marker.

For a new Custom GPT, use `CUSTOM_GPT_INSTRUCTIONS_V420.txt` and `openapi_actions.json` from this package.
