# World Engine v3.9.3

## NPC cognition
- Adds bounded `cognition_snapshot` state: beliefs, goals, recent memories, needs, causal mood/thoughts, job, last DECIDE result and ranked motive reasons.
- DECIDE writes causal decision thoughts when an NPC changes action; the backend stores reasons without exposing language-model chain-of-thought.
- `belief` and `goal` are first-class DECIDE considerations/requirements, so authored cognition can change action selection instead of only dialogue.
- `getWorldContext` includes bounded cognition for important local NPCs.

## Canonical visual identity
- Schema 12 adds NPC importance and persistent `entity_visual_references`.
- Character visual-profile finalization automatically requests a canonical 3:4 reference image.
- Major NPC visual-profile finalization does the same; minor NPCs do not create portrait spam.
- Later image cues carry canonical reference metadata plus authoritative current gear. Failed reference generation stays retryable.
- Native ChatGPT image bytes are not assumed to be backend-accessible: a usable reference handle is stored when available; exact reference prompt/fingerprint remains the persistent fallback.

## Narrative policy
- Adds response-type word budgets and novel-like narrative-adventure style directives.
- Dialogue uses role/status/culture/faction/relationship/mood/beliefs/goals/memory/motives without exposing hidden cognition.
- Adapts the legacy 1.63 full-scene 600–750-token target into shorter budgets for routine turns and fuller budgets for scene openings/cinematic scenes.

## Progression and rewards
- Adds `character_progression` and `owner_balances`.
- Supports XP and milestone modes, cumulative XP thresholds, pending level-up state and level-up reporting in world context.
- `grant_reward` now actually applies XP, currency, items and faction reputation atomically.
- Crossing a threshold does not silently apply class choices. It sets `pending_level`; the rules advancement kernel applies the level after required choices are resolved.
- Advancement synchronizes and clears pending progression state.
- Character advancement now requires an authoritative pending entitlement and exactly the next level; unearned or skipped levels fail closed.
- Character readback now returns normalized reward inventory and currency balances; NPC readback returns cognition and visual-reference status for GPT context.

## GPT Actions / compatibility
- Schema version: 12.
- `saveVisualProfile` is exposed to GPT Actions; development-only `getInternalStateBlock` is hidden from the 30-operation export.
- Export remains exactly 30 unique operations.
- All 30 exported operations are marked `x-openai-isConsequential:false` for Always-allow eligibility.
- OpenAPI object-schema compatibility normalization remains enabled.
