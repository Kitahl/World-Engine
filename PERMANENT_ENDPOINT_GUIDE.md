# Endpoint Guide — World Engine 5.1.1

## Normal path: automatic, account-free, temporary

Double-click `START_WORLD_ENGINE.vbs`. It opens one Companion window while the backend, endpoint helper, and supervisor stay hidden. Local Companion play does not need an HTTPS endpoint.

When you choose to connect GPT Actions, World Engine automatically starts an account-free Cloudflare Quick Tunnel. It uses a World-Engine-owned isolated configuration area and does not read, rename, or modify a personal Cloudflare configuration. No ngrok key, clipboard copy, account login, administrator privilege, or standalone executable download is required for this default route.

The Quick Tunnel URL is random and temporary. If it changes after a restart or recovery, World Engine retains a re-import warning. Re-import the generated `openapi_actions.json` schema into GPT Builder and acknowledge the warning only after that update. The local application continues to work while the warning is present.

## Optional stable providers

An already configured ngrok, named Cloudflare Tunnel, or Tailscale route may be reused as an advanced stable endpoint. These providers retain their own account, token, device, hostname, and availability requirements. World Engine does not silently replace a configured stable provider with another provider.

Ngrok authtokens cannot be safely acquired automatically because they belong to the user’s ngrok account. For that reason, the default first-run route is the no-account Quick Tunnel rather than a token-copy workflow. World Engine does not download a portable `ngrok.exe`; existing supported ngrok configuration is only an optional advanced path.

## Lifecycle and safety

World Engine keeps ownership data for a Quick Tunnel child before attempting stop/restart operations. It suppresses replacement while a verified owned child is alive and does not kill unrelated tunnel processes. The automatic child is launched with an isolated World-Engine configuration/home, while provider secrets are excluded from its environment.

## GPT Builder setup

Use `CUSTOM_GPT_INSTRUCTIONS_V510.txt` and `openapi_actions.json`. Local software cannot write your private GPT Builder configuration. The public schema has exactly five Actions. If the temporary Quick URL changes, import the fresh schema again.

## Visible diagnostics

`START_WORLD_ENGINE.bat` remains available when you need a visible diagnostic console. It is not the normal one-click route. `launcher.py` is diagnostic/manual compatibility tooling, not a second normal UI.

## Availability boundary

For GPT Actions, the PC must be on and connected to the internet. Quick Tunnels are for temporary development-style access and do not promise a stable hostname or service level. Named providers and external GPT Builder configuration remain account/machine boundaries.