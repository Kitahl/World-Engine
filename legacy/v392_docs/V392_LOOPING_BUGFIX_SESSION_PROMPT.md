# WORLD ENGINE v3.9.2 — LOOPING WHOLE-ENGINE BUG-FIX SESSION

Use the supplied **World Engine v3.9.2 AUTOMATIC VISUALS / REASONING / RECEIPTS / MUSIC HOTFIX** as the only implementation baseline. Do not regress to v3.9.1, the historical v3.8 artifact, v3.7.1, or `World_Engine_1.63.txt`. The 1.63 document may be consulted only as a legacy requirements ledger; current executable behavior and current documentation outrank it.

Act as a skeptical senior reliability engineer. Perform **at least 3 materially different bug-fix loops and at most 5**, stopping early only after 3 loops if no reproducible defect remains. Each loop must be: inspect → adversarial reproduction → root cause → minimal patch → focused regression → full regression → clean evidence. Do not call a rerun of the same tests a new loop.

## Immutable release invariants

- WORLD → LOCATION → SCENE → COMBAT layering remains intact.
- Backend/database state is authoritative; narration cannot override resolved state.
- No protagonist immunity.
- SQLite schema stays compatible unless a migration is truly necessary.
- GPT-visible OpenAPI operations remain exactly **30**, with **0 duplicate operationIds**.
- Every OpenAPI `type: object` node has explicit `properties` (empty `{}` is valid when appropriate).
- Every routine exposed gameplay operation remains `x-openai-isConsequential: false`; do not expose destructive account/data administration through the routine surface.
- Do not package runtime DBs, secrets, launcher configs, user music catalogs, `__pycache__`, or `.pyc` files.

## Loop 1 — structural/persistence/API audit

Attack imports, migrations, SQLite integrity/foreign keys, transactions/rollback, deterministic RNG, revision conflicts, route/state consistency, Windows paths, launcher behavior, OpenAPI generation through `/openapi.json`, static exporter, and launcher exporter. Verify the three schemas agree on the compatibility invariants above.

## Loop 2 — simulation/rules/world audit

Attack campaign persistence, WORLD time/chunk invariance, location graph and sparse 3D x/y/z maps, portals/zones/persistent damage, SCENE materialization/foldback, combat, death saves/death, rules/resources/reactions/rests, NPC needs/mood/jobs/reservations/DECIDE/GOAP, factions, plots/quests, crime, rumors, production, ecology/migration, divine/affliction systems, and safe model authoring. Add negative, rollback, concurrency, restart, migration, and long-run tests where existing happy paths could hide bugs.

## Loop 3 — orchestration/UI integration audit

Treat automatic visuals, reasoning routing, receipts, and music as release blockers.

### Automatic native image generation

Verify all default visual preferences are on unless the user disables them:
- `scene_start`
- `new_location`
- `battle_start`
- `event_choice`

For scene start, character arrival at a new location, and combat start, returned Action responses must carry `_turn_directives.image.required=true` when a cue is due. The cue must say to use **native ChatGPT Image generation before narration**, not merely return a prose prompt. On successful image creation the GPT must call `recordImageGeneration`; failed image records must not permanently suppress retry.

At a meaningful decision point (two or more real options or a major irreversible choice), the GPT must call `buildImageCue(trigger_type="event_choice")`, generate the image before presenting the choices when `should_generate=true`, then present the options. Test `decision_phase=before` and `decision_phase=after`.

Prompts must be setting-neutral and respect the World Bible. Fail any hardcoded `fantasy`, `medieval`, or `no modern objects` assumption that corrupts cyberpunk, modern, science-fiction, historical, or custom settings.

Important platform boundary: a backend Action cannot itself press ChatGPT's native Image generation tool. The release must make the cue mandatory and the GPT instructions must require the native tool, but never claim the backend directly generated the image. If the GPT Image generation capability is disabled, report that prerequisite clearly.

### Automatic reasoning policy

Verify deterministic routing:
- routine deterministic mechanics → `Instant`
- ordinary scene/new-location/battle staging → usually `Medium`
- world generation/custom setting/major multi-system or irreversible branches → `High`

Test threshold boundaries and reasons. Do not use Extra High by default. The backend may recommend reasoning but cannot move ChatGPT's model/reasoning selector. The GPT setup must tell eligible users to enable **Settings → General → Higher intelligence** for product-controlled automatic escalation.

### Simulation receipts / developer HUD

Verify authoritative runtime calls return `_engine_receipt` with operation, engine/schema version, campaign/revision before/after, world time before/after when available, elapsed time, and useful simulation/result signals. Receipts must prove backend work without exposing chain-of-thought. Normal play hides raw receipts; `SYSTEM DEBUG: ON` may show a compact footer.

### YouTube music

Audit every parser and resolver path. Error **2** is invalid parameter/video ID, not an autoplay error. Reject malformed IDs/URLs before `YT.Player`. For player errors **2, 5, 100, 101, 150**, blacklist that candidate for the current player session and deterministically select the next matching track. For **153**, do not blacklist the track; retain the loopback HTTP origin/Referer/client-identity mitigation. Never use yt-dlp/audio extraction, hidden-player tricks, Referer impersonation, or any bypass of embedding/autoplay restrictions.

Test raw IDs plus watch/youtu.be/embed/shorts/music.youtube URLs, malformed IDs, playlist-only URLs, multiple candidates, all-candidates-fail, location ambience → combat override → ambience restore, and restart persistence. If Windows Edge WebView2 is unavailable, state **LOGIC VERIFIED / REAL WINDOWS WEBVIEW2 PLAYBACK NOT VERIFIED**.

## Cross-system scenarios

Run at minimum:
1. New campaign → scene opens → mandatory image directive → image record → move to a new location → new-location image.
2. Tavern → battle starts → combat image → player reaches 0 HP → authoritative death processing → scene/world consequences.
3. Meaningful branching decision → event-choice cue → High reasoning recommendation when consequence warrants it → quest/faction mutation.
4. Mine with z-levels → collapse passage → leave → advance WORLD → return → route remains changed.
5. Music context A → bad candidate Error 2/100 → fallback candidate → combat override → post-combat restoration.
6. World advance → receipt proves ticks/DECIDE/CASCADE work and committed revision change.

## Release gates

Before claiming success:
- full source suite PASS;
- generate canonical OpenAPI and recursively audit it;
- SQLite `PRAGMA integrity_check` = `ok`;
- `PRAGMA foreign_key_check` = empty;
- package cleanup after any schema/test generation;
- build one-click Windows ZIP;
- extract ZIP to a completely fresh directory;
- run the entire suite again from that extraction;
- repeat OpenAPI, SQLite, package-hygiene, image-orchestration, reasoning-policy, receipt, and music-fallback audits from the extracted artifact;
- compute SHA-256.

Maintain `BUG_LEDGER.md` with severity, reproduction, root cause, patch, regression test, and status for every discovered bug. Produce a final numerical report listing loops, bugs found/fixed/unresolved, source tests, clean-ZIP tests, image tests, music tests, OpenAPI operations/duplicates/schema errors, SQLite integrity/FK violations, and Windows WebView2 physical-playback status.

Do not make feature-expansion work the priority. This session's job is to attack and stabilize **v3.9.2 as one coherent release**.
