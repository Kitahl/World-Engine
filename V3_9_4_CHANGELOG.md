# World Engine v3.9.4 — Gauntlet Merge / Runtime Hardening

## Baseline

- Parent release: v3.9.3, schema 12.
- Supplied patch specification: `WORLD_ENGINE_GAUNTLET_DELIVERY.zip` runner targeting v3.9.1.
- Important provenance: the supplied delivery manifest reported `FAIL_OR_NOT_VERIFIED`, runner exit code `MISSING`, and no emitted release/source ZIP. The runner was therefore treated as a patch specification, not as verified release evidence.

## Merged fixes

1. **Strict YouTube identity parser** — accepts raw 11-character IDs and exact supported YouTube URL forms; rejects unsupported schemes, look-alike hosts, URL credentials, fragments, duplicate `v` parameters, and extra path segments.
2. **Music catalog normalization** — canonical video ID, source URL, binding tags, numeric priority/volume, validation status, and last validation result. Malformed tracks are ignored rather than sent to the player.
3. **Bounded failed-track memory** — playback/content errors 2, 5, 100, 101 and 150 place the candidate on a 900-second cooldown and persist its latest validation failure. Error 153 remains a player-origin/referrer fault and does not blacklist a track.
4. **Immediate soundtrack fallback** — the pywebview error callback returns and applies the next deterministic resolver decision immediately instead of waiting only for the normal polling interval.
5. **Mandatory player death saves** — `RulesKernel._death_save_db()` resolves inside the active combat transaction. `WorldEngine.next_turn()` automatically runs it when an unstabilized player character at 0 HP becomes the active actor. GPT/API callers cannot skip the save by omission.
6. **Current diagnostics** — stale music/player/launcher version guidance is updated to v3.9.4.

## Preserved v3.9.3 systems

NPC cognition/DECIDE/GOAP, canonical character and major-NPC visual references, automatic image directives, book-like narrative policy, XP/milestone/reward ledgers, pending level-up authority, sparse persistent 3D mapping, world systems, music Error-153 origin handling, reasoning directives, and the 30-operation GPT Actions contract remain in place.

## Non-claims

- Real YouTube network availability is not inferred from an 11-character ID.
- Physical Windows WebView2 playback still requires a real Windows/browser acceptance test.
- A temporary Cloudflare quick-tunnel URL is not a permanent backend address; if it changes, the GPT Action server URL must be updated.
