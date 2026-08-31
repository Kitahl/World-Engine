WORLD ENGINE 4.5.0 — PROCEDURAL DESKTOP + PBEM + ENVIRONMENT

1. Extract the complete ZIP to a normal writable folder.
2. Double-click START_WORLD_ENGINE.bat.
3. The local engine and standalone Companion desktop start first.
4. External GPT connectivity is optional; follow the connection window only if you want GPT Actions.
5. In GPT Builder, use CUSTOM_GPT_INSTRUCTIONS_V450.txt and the generated openapi_actions_PERMANENT.json.
6. The public schema has five Actions. Normal gameplay uses resolveTurn.
7. World generation is performed in the desktop Forge and must pass stage, validation, dry-run, and promotion.
8. Ngrok is only the optional HTTPS bridge from ChatGPT to this PC. It is not required for local desktop use.

World Engine automatically creates a distinct private operator key. It is not displayed or copied to the GPT/browser surface.

If startup reports DEGRADED, the local engine and desktop may still be ready while the external tunnel needs account configuration or recovery. See PERMANENT_ENDPOINT_GUIDE.md and BUILD_REPORT_V450.md.
