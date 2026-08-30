# World Engine v3.5.0 Changelog

## Added
- safe authoring pipeline: stage -> static validation -> scratch dry-run -> promote
- `world_bible` stable canon table
- NPC archetypes + thin materialized instances
- sim-rule templates and shared recipes
- authoring batches with statuses/results
- canon locks
- reactive content gaps
- bootstrap / lazy / reactive authoring modes
- world digest for automated tuning
- lazy dematerialization of untouched generated NPCs
- automatic canonization of materialized NPCs when gameplay mutates them
- world-bible constraints injected qualitatively into image prompts
- one high-level `authorWorldContent` GPT Action

## Safety
- generated content never directly becomes runtime truth without the gate
- scratch validation does not modify live campaign DB
- malformed/high-probability/self-loop/invalid-reference content is rejected
- canon-locked rows cannot be silently overwritten
- runtime missing references create content gaps instead of LLM improvisation

## Compatibility
All v3.4 WORLD/SCENE, directors, lifecycle, deterministic dice, concurrency/security and visual-continuity systems remain. GPT-visible operation count remains exactly 30.
