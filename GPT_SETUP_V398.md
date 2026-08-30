# World Engine v3.9.8 — Permanent GPT Setup

## One-time setup

1. Run `START_WORLD_ENGINE.bat`. The launcher automatically uses `%LOCALAPPDATA%\WorldEngine` for the database, API key, music catalog and endpoint state.
2. Click **Permanent Endpoint Setup**. Default provider is Tailscale Funnel; Cloudflare Named Tunnel is available through `INSTALL_CLOUDFLARE_NAMED.bat` when you own a Cloudflare-managed domain.
3. Complete the one-time provider login/approval.
4. The installer will not report PASS until the stable public `/health` endpoint and a protected Bearer-auth API request both pass.
5. Import `openapi_actions_PERMANENT.json` into GPT Actions once.
6. Set Action authentication to Bearer and paste the value from **Copy API Key**.
7. Click **Test Action Connection**.

Normal launcher restarts and Windows restarts keep the same public hostname, database and API key. The backend is registered for hidden Windows logon autostart. Quick Tunnel is disabled as an automatic fallback.

If you install a later full World Engine release, launch it once; `runtime_install.json` is updated so the persistent autostart bootstrap follows the new installation.
