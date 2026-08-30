# World Engine v3.9.2

Bug-fix/automation release based on verified v3.9.1.

## Automatic scene imagery

- SCENE start responses now carry a required native-image directive when automatic images are enabled.
- Player-character movement to a new location carries a new-location image directive.
- Combat start carries a battle-opening image directive.
- `getWorldContext` surfaces a pending scene/new-location cue when one remains unrecorded.
- Meaningful choice menus are required by GPT instructions to call `buildImageCue(event_choice)` before presenting choices.
- Image cues are now setting-neutral rather than hardcoded to fantasy or anti-modern prompts.
- Successful generation remains deduplicated through `recordImageGeneration`; failed/unavailable image attempts remain retryable.
- `event_choice` image cues distinguish `decision_phase=before` from `decision_phase=after` so decision-consequence images are not mislabeled as pre-choice art.

## Automatic reasoning policy

- Added deterministic `fast` / `standard` / `deep` turn classification based on task, choice count, active authorities, quests, combat density, and consequence scope.
- Routine mechanics prefer fast/Instant behavior because the backend resolves the simulation.
- World synthesis, complex authoring, and major multi-system consequences recommend High; new scene/location synthesis recommends Medium; routine backend-resolved mechanics recommend Instant.
- This is a recommendation only; Actions cannot change ChatGPT's model picker. Use ChatGPT's Higher intelligence setting with Instant for actual automatic reasoning escalation.

## Simulation receipts

- Key runtime Action responses now include `_engine_receipt` with engine/schema version, operation, revision before/after, world-time before/after, elapsed API time, and detected simulation/rule signals.
- Receipts are hidden from normal narration and can power `SYSTEM DEBUG: ON` developer HUD output.

## Action approvals

- Every GPT-visible operation is exported with `x-openai-isConsequential: false` so the client can offer an Always Allow path for ordinary game-only Actions.
- The GPT Action ceiling remains 30 operations.

## Music reliability

- Runtime YouTube errors 2/5/100/101/150 blacklist the failing video for the player session and deterministically fall back to the next matching catalog candidate.
- Error 153 remains a client-origin/referrer failure and does not blacklist an otherwise valid track.
- Existing URL/video-ID syntax validation remains mandatory before catalog use.

## Compatibility

- Database schema remains version 11; no migration is required from v3.9.1.
