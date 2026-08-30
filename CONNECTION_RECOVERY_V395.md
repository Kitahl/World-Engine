# World Engine v3.9.5 — QA-001 Connection Recovery

`ClientResponseError` at `ensureCampaign` means the GPT did not receive a successful protected HTTP response. v3.9.5 independently verifies the backend route itself: valid Bearer auth returns HTTP 200 for both minimal bootstrap and explicit `world_time`; missing/wrong Bearer returns HTTP 401.

## Recovery order

1. Close older World Engine launchers/processes.
2. Preserve/copy the campaign `data/world_engine.sqlite3` and its matching `data/launcher_config.json`.
3. Start v3.9.5. If port 8000 is occupied by an engine using another key, the launcher now reports **PORT 8000 AUTH MISMATCH** instead of declaring success.
4. Start Temporary HTTPS. The launcher now requires both public `/health` and an authenticated protected request to succeed.
5. Click **Test Action Connection**.
6. If it reports stale schema URL, re-import the current `openapi_actions_live.json`.
7. In GPT Actions authentication, choose Bearer and paste the current **Copy API Key** value.
8. Retry `ensureCampaign`, then `getWorldContext`. Only after both succeed should gameplay resume.

A new Quick Tunnel URL after restart requires schema re-import. A new `launcher_config.json` creates a new API key and therefore requires updating the GPT Bearer token. Neither condition implies that the campaign SQLite database was deleted.
