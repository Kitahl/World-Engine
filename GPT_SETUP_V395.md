# GPT Setup — World Engine v3.9.5

1. Start the launcher and wait for `RUNNING`.
2. Click **Start Temporary HTTPS** and keep it running during play.
3. Click **Test Action Connection**. Do not configure the GPT until public health and protected auth both pass.
4. Import the newly generated `openapi_actions_live.json` into GPT Actions.
5. Set Action authentication to **Bearer** and paste **Copy API Key**.
6. If the tunnel URL changes after restart, re-import the new schema.
7. If a new install uses a different API key, restore the old `data/launcher_config.json` or replace the GPT Bearer token with the new key.
8. Preserve `data/world_engine.sqlite3`, `data/launcher_config.json`, and configured `data/music_catalog.json` during upgrades.

## QA-001 / ClientResponseError

Run **Test Action Connection**. It distinguishes local API down, local API-key mismatch/stale process on port 8000, missing public tunnel, unreachable tunnel, public auth failure, stale schema URL, and launcher-side PASS. If launcher-side PASS but the GPT still fails, re-import the current live schema and replace the GPT Bearer token with the current **Copy API Key** value.

Do not create or advance authoritative campaign state while Actions are unavailable.
