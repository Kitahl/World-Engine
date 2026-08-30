# World Engine v3.9.3 — GPT Setup

1. Enable the GPT **Image generation** capability.
2. Import `openapi_actions.json` or the launcher-generated `openapi_actions_live.json`.
3. Configure Bearer authentication using the launcher's generated API key.
4. Paste `CUSTOM_GPT_INSTRUCTIONS_V393.txt` into GPT Instructions. It stays below the 8,000-character limit.
5. For routine World Engine calls, the schema explicitly marks all 30 exported operations `x-openai-isConsequential:false`. This makes them eligible for ChatGPT's **Always allow** behavior; actual approval UI remains controlled by the user/platform/workspace.
6. Character creation should finalize appearance/gear through `saveVisualProfile`, generate the returned canonical character-reference image, then record it. Repeat for `importance=major` NPCs when introduced.
7. Use `_turn_directives.narrative`, `_turn_directives.image`, `_turn_directives.reasoning` and `_engine_receipt` exactly as described in the supplied instructions.
8. Do **not** upload `legacy/World_Engine_1.63.txt` as active GPT Knowledge. It is retained only as a historical requirements reference; v3.9.3 backend state, OpenAPI and `CUSTOM_GPT_INSTRUCTIONS_V393.txt` are authoritative.

### Visual-reference boundary
World Engine persists an image reference handle when one is available, plus the exact canonical reference prompt/fingerprint and current gear. Native ChatGPT image generation does not guarantee that its pixel bytes are directly uploadable to an Action. Within a conversation, use the actual prior reference image when available. Across sessions, use a stable accessible image reference if the platform provides one; otherwise the stored canonical prompt/fingerprint is the deterministic continuity fallback.
