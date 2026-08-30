# World Engine v3.9.8 Build Report

Source baseline: user-supplied `world_engine_v3_9_5_QA001_BOOTSTRAP_CONNECTION_HOTFIX_ONE_CLICK_WINDOWS(1).zip`.

## Integrated
- complete v3.9.5 gameplay/rules/world/NPC/image/progression/music stack;
- version-independent persistent DB/API key/music state under `%LOCALAPPDATA%\WorldEngine`;
- automatic safe migration from release-bound data and newest sibling install when persistent state is empty;
- fixed permanent GPT Action hostname;
- Tailscale Funnel `--bg` + Windows unattended default;
- Cloudflare Named Tunnel service alternative;
- permanent public health + protected-auth gating;
- `openapi_actions_PERMANENT.json`;
- fail-closed policy instead of automatic random Quick Tunnel fallback;
- hidden Windows-logon backend autostart following `runtime_install.json`;
- direct app/MCP persistent DB defaults and persistent launcher-key auth fallback.

## Verification
- baseline before merge: 229/229 PASS;
- permanent integration tests: 8/8 PASS;
- combined source tests: 237/237 PASS;
- GPT Actions: 30/30 unique and non-consequential;
- OpenAPI compatibility errors: 0;
- schema remains 12;
- compact GPT instructions: 7,621 characters / 7,688 bytes.
