WORLD ENGINE 5.1.1 — ONE-CLICK COMPANION

1. Extract the complete ZIP to a normal writable folder.
2. Double-click START_WORLD_ENGINE.vbs.
3. World Engine opens one Companion window. The backend, HTTPS endpoint helper, and supervisor run hidden.
4. Local play needs no external tunnel. The Companion remains usable if external GPT access is unavailable.
5. If you want ChatGPT GPT Actions, the default account-free Cloudflare Quick connection is created automatically. Its URL is temporary/random. When it changes, re-import the generated GPT Actions schema in GPT Builder.
6. Use CUSTOM_GPT_INSTRUCTIONS_V510.txt and openapi_actions.json in GPT Builder. The public schema has five Actions; normal gameplay uses resolveTurn.
7. Music controls are in the Companion. The bundled procedural soundtrack is offline and starts only after you press Play.
8. WEGEN-2.0 world generation is performed in the Companion Forge and must pass stage, validation, dry-run, and atomic promotion.

START_WORLD_ENGINE.bat is retained as a visible diagnostic/fallback launcher. Advanced users with an existing ngrok, named Cloudflare, or Tailscale setup can configure that stable provider separately; no copied ngrok key is required for the default route.

World Engine creates a distinct private operator key. It is not displayed or copied to the GPT/browser surface. See PERMANENT_ENDPOINT_GUIDE.md and MUSIC_GUIDE.md for details.