# WORLD ENGINE v3.9.3 — LOOPING WHOLE-ENGINE BUG-FIX SESSION

Use the supplied **World Engine v3.9.3 NPC COGNITION / CANONICAL VISUAL IDENTITY / NARRATIVE / PROGRESSION** release as the only implementation baseline. Do not regress to 3.9.2/3.9.1/3.8/3.7.1. `World_Engine_1.63.txt` is a legacy requirements reference only; preserve useful behavior, not obsolete prompt-era machinery.

Act as an adversarial reliability engineer. Perform at least 3 substantially different bug-fix loops and up to 5 while reproducible defects remain. Each loop: inspect → attack → reproduce → root cause → smallest correct patch → focused regression → full regression → record evidence. Never call a defect fixed because code looks correct.

Release blockers include: failed tests; DB integrity/FK failure; migration loss; partial transactions; NPC action/motive state without causal evidence; XP/reward state inconsistent with character level; skipped pending advancement; reference-image success recorded after failure; major-NPC/character visual identity drift; generated scene images ignoring available canonical references/current gear; invalid OpenAPI; exported operation count !=30; duplicate operationId; any exported operation lacking `x-openai-isConsequential:false`; object schema missing properties; runtime DB/secrets/cache in ZIP; broken YouTube error fallback; or Windows launcher failure.

Specifically attack:
1. NPC DECIDE/GOAP/jobs/needs/mood/beliefs/goals/memories/cognition snapshots and reason persistence.
2. Schema 11→12 and older supported migrations; restart persistence.
3. XP and milestone modes, threshold crossings, multi-level awards, reward rollback, currency/items/reputation, level-up pending state, rules advancement synchronization.
4. Canonical character reference at creation and major-NPC reference at introduction. Failed image attempts must retry. Later cues must include references and current gear. Test native GPT image invocation in GPT Preview if available; do not claim pixel-level cross-session reuse without an accessible stable image reference.
5. Narrative directive word budgets, natural dialogue/cognition conditioning, hidden mechanics, player-agency constraints.
6. WORLD/LOCATION/SCENE/COMBAT, sparse 3D spatial persistence, factions/plots/crime/rumors/production/population/divine/affliction systems and rules kernel.
7. Music with actual supported browser/WebView2 when available: valid control video, real validated track, errors 2/5/100/101/150 fallback, 153 origin/referrer, autoplay-blocked handling. No circumvention.
8. Raw `/openapi.json`, static exporter and launcher exporter. Export exactly 30 unique GPT actions, all non-consequential false, no missing object properties/unresolved refs; `saveVisualProfile` exposed and dev-only internal state hidden from GPT export.
9. Package hygiene and fresh-ZIP testing.

Maintain BUG_LEDGER.md. Final outputs: corrected ZIP, SHA-256, source and clean-ZIP test output, OpenAPI audit, SQLite/migration audit, image-reference audit, progression audit, music validation, and final numerical report. If physical GPT image invocation or Windows WebView2 playback cannot be exercised, mark it NOT PHYSICALLY VERIFIED rather than inferring success from mocks.
