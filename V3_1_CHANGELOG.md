# v3.1 Changelog

## Added
- `getInternalStateBlock`
- `saveVisualProfile`
- `getVisualProfile`
- `saveVisualState`
- `getVisualState`
- `getRecentImageContext`
- `visual_profiles` persistence
- `visual_states` persistence
- `visual_context_json` and `source_revision` on image records

## Changed
- image prompts now reuse entity appearance and location/scene/combat continuity
- hidden numeric state can drive qualitative visual effects
- world time is rendered to image prompts as a time-of-day label rather than raw campaign timestamp
- image-generation records no longer advance gameplay revision

## Explicitly not added
- automatic standalone character/NPC portrait generation
- player-visible numerical state block
