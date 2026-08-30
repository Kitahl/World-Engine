# World Engine v3.4.0 Changelog

## WORLD + SCENE
- Persistent WORLD location graph remains abstract: coordinates, realm, region, weighted roads.
- Added disposable SCENE records with maximum 12 concrete entities.
- Scene entities track x/y/z, zone, stance and scene-local state.
- Scene features track spatial placement, LOS blocking, difficult terrain and persistent foldback.
- Starting combat can materialize the tactical grid from SCENE automatically.
- Ending combat folds participant positions back into the active scene and deletes combat-grid rows.
- Ending a scene folds only declared persistent consequences back into WORLD and deletes the scene.

## Hierarchical directors
- Added deterministic `directors` table.
- Director kinds: civic, faction, realm, divine, power.
- Scope: location, region, realm, scene or global.
- Sources: NPC, faction, current faction leader, deity or named power.
- Overlapping directors form an ordered authority stack.
- Policies resolve by priority; event weights compose deterministically.
- CHANCE simulation now consumes the active director multiplier for the event's role/location.
- `faction_leader` directors automatically follow succession.
- Director qualitative context feeds scene/battle/choice image prompts without exposing numerical weights.

## Lifecycle / succession
- NPCs have alive/dead/missing status and death time.
- Lifecycle persists birth year, parents, spouse, mortality profile, fertility profile and declared heir.
- Optional seeded birth simulation creates persistent children with parent links.
- Death removes NPCs from ordinary scene/context selection.
- Ownership and faction leadership transfer deterministically on death.
- Current faction-leader directors automatically resolve the successor.

## World / context tracking
- `getWorldContext` now uses one SQLite connection and caps returned living entities at 40.
- Response reports limit, total count, returned count and truncation.
- Added basic world tracking: locations, living/dead NPC totals, active combat count, active scene and local population counts.
- Hidden internal numerical state has the same cap/truncation discipline.

## Determinism and safety corrections
- Authoritative gameplay checks/attacks now consume the campaign-seeded RNG stream.
- Same seed + same call sequence replays across DB reopen.
- Removed `SystemRandom` from engine sources; standalone non-campaign dice use fixed seeded test RNG.
- Critical damage above the 100-dice parser cap is clamped and explicitly flagged instead of crashing.
- Removed remaining nested public DB reads from write transactions.
- One-click cloudflared helper pinned to 2026.8.1 with SHA-256 verification.
- Launcher remains importable on Python installs without Tkinter so helper/CI tests can run headlessly.

## Images
- Scene staging and persistent features feed automatic scene images.
- Local mayor/king/faction/deity/power director context feeds scene imagery qualitatively.
- Raw authority values and event multipliers are not rendered into image prompts.
- No standalone portrait trigger was introduced.
