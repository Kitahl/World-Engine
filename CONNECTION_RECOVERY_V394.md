# World Engine v3.9.4 — Connection Recovery

A ChatGPT `ClientResponseError` does not by itself prove campaign data loss. It means the Action client did not obtain a usable HTTP response. World Engine stores campaign state in SQLite locally, so availability of the GPT Action endpoint and persistence of the database are separate questions.

## v3.9.4 launcher checks

1. The local API must answer `http://127.0.0.1:8000/health` with HTTP 200.
2. After Cloudflare Quick Tunnel creates an HTTPS address, the launcher now checks the **public** `<tunnel>/health` endpoint before declaring `RUNNING + HTTPS`.
3. Only after that public health check succeeds does it generate `openapi_actions_live.json`.
4. Keep the launcher and HTTPS tunnel running while ChatGPT uses Actions.

## Important temporary-tunnel limitation

A Cloudflare Quick Tunnel address is temporary. If the tunnel stops and a later restart produces a different public URL, an existing GPT Actions schema still points at the old dead address. Re-import the newly generated `openapi_actions_live.json` into the GPT before resuming play. This cannot be repaired solely by the local engine because the GPT's configured Action server URL lives on the ChatGPT side.

## Safe campaign recovery

After connectivity returns:

1. call `ensureCampaign` for the same campaign ID;
2. call `getWorldContext`;
3. compare authoritative backend state with the last known checkpoint;
4. do not retroactively award time, XP, items, money, relationship changes, quest completion, or other consequences that were narrated while the backend was unreachable;
5. resume from the last state confirmed by the backend.
