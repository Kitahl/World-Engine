# v3.9.2 Image Automation Boundary

## What World Engine can enforce

World Engine can deterministically decide when a scene image is due and can return a `_turn_directives.image` object containing `required`, `order`, and an authoritative continuity-aware prompt.

Automatic triggers are enabled by default for:

- SCENE start;
- player arrival at a new location;
- battle start;
- major choice/decision moments;
- post-decision visible consequences when the GPT requests an `event_choice` cue with `decision_phase="after"`.

A successful `recordImageGeneration` suppresses duplicate generation for the same trigger/scene key. A failed image record no longer suppresses retry.

## What World Engine cannot do

A GPT Action is an HTTP/text interface. It cannot itself invoke ChatGPT's native Image generation tool or return generated image bytes. The GPT must have the built-in **Image generation** capability enabled and must obey `_turn_directives.image.required` by invoking that capability.

Therefore the release has two verification levels:

- **Backend orchestration verified:** trigger, prompt, deduplication/retry, continuity, API directive and tests.
- **Real GPT tool invocation:** must be confirmed in the GPT Builder Preview after enabling Image generation.

## Preview acceptance test

1. Enable **Image generation** in the GPT's Capabilities.
2. Use `CUSTOM_GPT_INSTRUCTIONS_V392.txt` as the main instructions.
3. Start World Engine and import `openapi_actions.json`.
4. Start a SCENE. Confirm the Action result contains `_turn_directives.image.required=true` and the GPT immediately generates one image before continuing narration.
5. Re-read the same unchanged scene. Confirm no duplicate image is generated after the successful record.
6. Move the player to a new location. Confirm a new establishing image is generated.
7. Start combat. Confirm a battle-opening image is generated.
8. Present a major choice. Confirm the pre-choice image is generated.
9. Make a material choice that changes the visible scene. Confirm a post-decision cue can generate the resolved consequence image.

Do not report native image generation as physically verified unless these steps have been observed in GPT Preview.
