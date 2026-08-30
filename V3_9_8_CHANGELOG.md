# World Engine v3.9.8 — Full Permanent Endpoint Release

## Integration
- Full source package based on v3.9.5; no overlay installation required.
- Preserves schema 12 and all v3.9.5 gameplay, cognition, visual, progression, prose, music and gauntlet hardening.
- Integrates v3.9.6/v3.9.7 connection guard and permanent endpoint behavior directly into the launcher/runtime.

## Persistent state
- Windows default data root is `%LOCALAPPDATA%\WorldEngine`.
- DB, launcher API key/config, music catalog, permanent endpoint state and runtime-install pointer survive release-folder changes.
- Current release `data/` is migrated automatically; if persistent state is empty, the newest sibling World Engine install can be migrated once.
- Persistent state always wins; conflicts are preserved instead of overwritten.
- `app.py` and MCP use the persistent DB by default even outside the GUI launcher. Protected API auth can read the persistent launcher key when the environment key is absent.

## Permanent public endpoint
- Normal launcher HTTPS path requires/reuses a configured permanent endpoint.
- Random Cloudflare Quick Tunnel is disabled as an automatic fallback; it remains code-only for explicit development testing.
- Default permanent provider: Tailscale Funnel with `--bg` and Windows unattended mode.
- Alternative: Cloudflare Named Tunnel/service for users with a Cloudflare-managed domain.
- Stable endpoint must pass public `/health` and protected Bearer-auth probes before PASS/schema generation.
- Generates `openapi_actions_PERMANENT.json` with the fixed hostname.

## Reboot/update continuity
- Permanent installer registers hidden Windows-logon backend autostart.
- Version-independent bootstrap reads `%LOCALAPPDATA%\WorldEngine\runtime_install.json`.
- Launching a later full World Engine version updates that pointer, so autostart follows the newest release.

## GPT contract
- 30 GPT-visible Actions remain.
- All are `x-openai-isConsequential:false` for Always-Allow eligibility.
- Compact GPT instructions remain under the 8,000-character/byte constraint.
