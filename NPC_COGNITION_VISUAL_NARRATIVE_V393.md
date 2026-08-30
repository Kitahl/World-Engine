# World Engine v3.9.3 — NPC Cognition, Visual Identity and Narrative Contract

## How NPC action is produced
Off-screen/runtime NPC action remains deterministic simulation. Needs, resources, relationships, world facts, beliefs, goals, requirements, distance/proximity, commitment and action considerations feed DECIDE. Meaningful belief/goal/memory changes are committed with `updateNpcState` and explicit reasons. Bounded GOAP is available when a goal requires a short dependent action sequence. Jobs/reservations provide shared-world work opportunities without allowing impossible double claims.

When DECIDE changes an NPC's chosen action, v3.9.3 records a bounded causal decision-thought row. A cognition snapshot exposes game-state reasons such as need pressure, active goals, beliefs, job pressure, causal mood thoughts and the last chosen action. These are simulation facts, not language-model private chain-of-thought.

## 1.63 behavior retained
The legacy 1.63 design wanted beliefs, emotions, goals and memories to alter NPC speech/action; faction, mood, routine, weather and memory to affect prose; and mechanics to remain hidden beneath player-facing narrative. v3.9.3 preserves those semantics through database-backed state and causal reason records rather than reviving the old Hilbert-vector/TMAF prompt machinery.

## Canonical visual identity
After character appearance and initial equipment are final, `saveVisualProfile` returns a required character-reference cue. Major NPCs receive the same treatment when their profile is finalized. A successful generated reference is recorded in `entity_visual_references` with an optional image handle plus exact prompt/fingerprint. Later scene cues carry reference metadata and current authoritative gear.

A native ChatGPT-generated bitmap is not assumed to be directly uploadable to a GPT Action. If the platform makes a usable image reference available, it is used. Otherwise the persistent exact identity prompt/fingerprint is the cross-session fallback. Within a conversation, the actual prior generated image should be used as a visual reference whenever the image tool can access it.

## Narrative contract
The GPT follows response-type word budgets instead of emitting a 600–750-token block for every minor turn. Full openings/cinematic scenes remain long enough for novel-like narration; routine mechanics, dialogue and combat beats remain shorter. Dialogue is driven by the NPC's culture/role/status/faction/relationship/mood/beliefs/goals/memories/motives and normally remains 1–4 spoken sentences per conversational turn.
