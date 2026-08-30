# GPT Setup — World Engine 4.0

## Files

- GPT instructions: `CUSTOM_GPT_INSTRUCTIONS_V400.txt`
- Static development schema: `openapi_actions.json`
- Generated stable schema: `openapi_actions_PERMANENT.json`

## Initial setup

1. Extract the full release.
2. Run `START_WORLD_ENGINE.bat` normally; do not use Administrator mode.
3. If no valid ngrok authentication is found, the official dashboard opens. Sign in and press **Copy** beside the authtoken. World Engine reads the clipboard, validates/configures the local ngrok agent, and continues automatically. There is no launcher paste field.
4. Wait until the startup receipt says `PASS` and both `health_ok` and `protected_auth_ok` are true.
5. In GPT Builder, enable Image generation if scene images are required.
6. Import `openapi_actions_PERMANENT.json`.
7. Configure Action authentication as Bearer and paste the World Engine API key from the clipboard once.
8. Paste `CUSTOM_GPT_INSTRUCTIONS_V400.txt` into GPT Instructions.
9. Test `ensureCampaign`, then `resolveTurn` in `context_only` mode.

## Normal startup

After first setup, `START_WORLD_ENGINE.bat` automatically:

- loads the persistent API key;
- starts/validates the backend;
- starts/repairs the stable endpoint;
- validates public health and protected auth;
- regenerates the permanent schema;
- starts a single no-admin supervisor;
- opens the launcher.

No routine token or API-key paste is required.

## GPT Builder boundary

World Engine cannot silently alter the user's private GPT Builder Action authentication. The initial schema import and Bearer-field paste remain a one-time user-controlled security step. If the ngrok account's assigned hostname is deliberately changed, the schema must be re-imported.

## Normal turn usage

Use `resolveTurn` for ordinary gameplay. Send:

- `campaign_id`;
- `actor_kind` and `actor_id` when known;
- `expected_revision` for mutations;
- a stable unique `idempotency_key` for the user turn;
- the smallest sufficient ordered intent list;
- explicit `depends_on` links;
- `optional=true` only for truly optional work.

Use:

- `mode=execute` to resolve the turn;
- `mode=plan` to validate/route without mutation;
- `mode=context_only` for bounded context retrieval;
- `mode=capabilities` to inspect contracts.

If the connection fails, stop authoritative play. Run `START_WORLD_ENGINE.bat`; resume only after the startup receipt passes and `ensureCampaign` plus `resolveTurn(context_only)` succeed.
