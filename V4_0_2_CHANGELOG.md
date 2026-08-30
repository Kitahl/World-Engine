# World Engine 4.0.2 — Narrative Director Update

## Numerical summary

| Measure | Value |
|---|---:|
| Release | 4.0.2 |
| Database schema | 14 |
| Turn protocol | WETP-1.0 |
| Capability manifests | 30 |
| GPT-visible OpenAPI operations | 30 |
| Narrative migration modes | 4 |
| New persistent narrative tables | 9 |
| Narrative packet version | NRP-1.0 |
| Narrative quality receipt version | NQR-1.0 |
| Default narrative mode | shadow |
| Imported 1.63 source SHA-256 | `0748cf20e6fc870055d1d96ac329b83561c71162922bbb2220278ccb1f2feee5` |

## Added

1. `world_engine/narrative.py`: native narrative director, storylet selector, semantic dialogue planner, voice-profile compiler, motif manager, render-packet compiler, quality gate and accepted-output recorder.
2. Schema 14 with persistent configuration, voice profiles, beats, motifs, semantic dialogue state, render packets, outputs, receipts and director state.
3. `narrative.manage` as the 30th routed capability. No new GPT Action endpoint was added.
4. Automatic narrative packet compilation after an authoritative `resolveTurn` execution.
5. `off`, `shadow`, `compare` and `enforce` migration modes; default is `shadow`.
6. Typed `NRP-1.0` packets and deterministic `NQR-1.0` quality receipts.
7. Hard checks for visible internal tags, player-authorship violations, withheld-string leakage, gross length violations and second-person POV drift.
8. Soft diagnostics for recent-output similarity, repeated four-grams/openings, clichés, repeated “you see/notice,” catchphrases and motif overuse.
9. Dynamic length by scene function, replacing the 1.63 fixed 600–750-token default.
10. 30-scenario narrative benchmark specification and release-audit tooling.

## Corrected from World Engine 1.63

- Player-character introspection is forbidden unless explicitly supplied by the player.
- Literal generated dialogue is not cached; semantic intent/facts/subtext/voice state are cached.
- NPC state uses existing beliefs, goals, memories, relationships, needs and cognition without Hilbert-vector authority.
- Motifs use explicit eligibility, cooldown, recurrence and transformation state rather than symbolic-vector mathematics.
- Coarse tone labels are replaced by a hybrid multidimensional style profile.
- Cutscene command concepts are represented by typed render data rather than exposed `::cam`/`::d`/`::fx` syntax.
- Marketing claims in 1.63 such as single-author quality, lossless prose fidelity and fixed percentage gains are not treated as verified.

## Preserved

- 4.0.1 Windows root-quoting fix.
- WETP-1.0 and the 30-operation Custom GPT Action ceiling.
- Existing deterministic mechanics, world simulation, context compiler, images, music, startup, endpoint repair and persistent data migration.
- Current `turn_policy.py` as the player-facing baseline until promotion through shadow/compare/enforce.

## Evidence boundary

The implementation and deterministic tests establish schema, routing, authority boundaries, packet construction and local checks. They do not establish superior literary quality. That requires the blinded human evaluation defined in `NARRATIVE_BENCHMARK_V402.md`.
