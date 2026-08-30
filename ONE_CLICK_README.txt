WORLD ENGINE 4.3.0 — OUTPUT + COMPANION HARDENED

START
1. Extract this complete ZIP.
2. Double-click START_WORLD_ENGINE.bat.
3. Do not paste an ngrok token into a console. If no existing token/config is found, the official ngrok page opens. Sign in and click Copy; World Engine detects the clipboard and configures it automatically.
4. Wait for WORLD ENGINE 4.3 CONNECTION READY.

ONE-TIME GPT BUILDER SETUP
1. Import openapi_actions_PERMANENT.json.
2. Configure Action authentication as Bearer.
3. The generated World Engine API key is placed on the clipboard during first setup; paste it into the private GPT Builder Bearer field once.
4. Use CUSTOM_GPT_INSTRUCTIONS_V430.txt as the GPT instructions.

NORMAL USE
Double-click START_WORLD_ENGINE.bat. It retrieves the existing API key, starts the backend, repairs/starts HTTPS, tests public health and protected authentication, regenerates the 21-operation curated schema, starts the supervisor, and opens the launcher. No routine key/token paste is required.

NORMAL GAMEPLAY
ChatGPT uses resolveTurn. New campaigns default narrative mode to off. In configured enforce mode, public turns must use execute; context-only and narrative downgrades fail before mutation.

PUBLICATION
publishPresentation accepts only campaign, presentation, packet, turn, expected revision, exact narration, and exact choices. Do not display rejected or semantic-review-required candidates. Trusted review uses scripts\publication_review.py.

OPTIONAL FOUNDRY COMPANION
Set WORLD_ENGINE_FOUNDRY_API_KEY and, if needed, WORLD_ENGINE_FOUNDRY_URL to a literal loopback IP origin. Run START_COMPANION_WORKER.bat. Delivery-unknown rows require operator reconciliation.

PERSISTENT DATA
%LOCALAPPDATA%\WorldEngine\
  world_engine.sqlite3
  launcher_config.json
  music_catalog.json
  ngrok.yml
  permanent_endpoint.json
  openapi_actions_PERMANENT.json
  logs\

NO ADMINISTRATOR RIGHTS ARE REQUIRED FOR DEFAULT USER-SESSION STARTUP.
The Windows user must be signed in, and the PC must be powered on and online for remote Actions.
