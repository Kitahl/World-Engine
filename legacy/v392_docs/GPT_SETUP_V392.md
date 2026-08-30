# World Engine v3.9.2 — GPT Setup

1. Use `GPT_INSTRUCTIONS.md` as the GPT's main instructions. It is kept below the 8,000-character limit.
2. Enable the GPT **Image generation** capability. Automatic World Engine cues cannot invoke native ChatGPT image generation if this capability is disabled.
3. Use a model that supports Actions. Do not use Pro mode for this GPT when Actions are required.
4. In ChatGPT, enable **Settings → General → Higher intelligence** while using Instant. GPT-5.6 can then automatically apply more reasoning to complex turns. World Engine returns a deterministic `_turn_directives.reasoning` recommendation, but an Action cannot itself move the reasoning slider.
5. Import `openapi_actions.json` (or the launcher-generated `openapi_actions_live.json`) into GPT Actions and configure Bearer authentication with the World Engine API key.
6. The GPT-visible actions are marked `x-openai-isConsequential: false`. When ChatGPT offers **Always allow**, choose it if you want routine game-state calls to proceed without repeated approval prompts.
7. For a public/shared GPT, configure a valid public HTTPS privacy-policy URL in the GPT Action settings.
8. Start the World Engine launcher before the GPT session. Start the music player if background music is wanted.

## Automatic image smoke test

Start a campaign, create a location and character, then start a SCENE through World Engine. The Action response should contain:

```json
{
  "_turn_directives": {
    "image": {
      "required": true,
      "tool": "native_chatgpt_image_generation",
      "order": "before_narration"
    }
  }
}
```

The GPT must generate the native image before continuing the scene, then call `recordImageGeneration`. Re-reading the same scene should not create a duplicate image after it is recorded.

## Decision image smoke test

For a meaningful choice, the GPT calls `buildImageCue` with `trigger_type="event_choice"` and `decision_phase="before"`; if `should_generate=true`, it generates the image before showing the choices. After a committed material decision visibly changes the scene, it may call the same operation with `decision_phase="after"` and a unique scene key, then generate the consequence image.

## Simulation receipt / developer HUD

Normal players should not see raw receipts. For development, tell the GPT:

`SYSTEM DEBUG: ON`

It should then expose compact receipt information such as engine operation, campaign revision delta, simulation tally, rule/cascade signals and elapsed API time. Turn it off with:

`SYSTEM DEBUG: OFF`

World Engine cannot expose ChatGPT's private chain-of-thought or exact hidden reasoning tokens from a custom-GPT Action. Receipts instead verify which deterministic backend work actually ran.
