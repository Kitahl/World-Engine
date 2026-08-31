# Permanent Endpoint Guide — World Engine 4.3

## Default path

Run `START_WORLD_ENGINE.bat`. On Windows, World Engine accepts only a canonical ngrok App Execution Alias whose running process reports the package family bound to Store product `9MVS1J51GMK6`. If needed, the startup controller authenticates the running Microsoft App Installer/WinGet process, validates the trusted Store source, and installs that exact product non-interactively. Administrator privileges and manual executable verification are not required.

A pre-existing tunnel is reused only when Windows maps the local ngrok API listener to an owning PID and that process reports the same ngrok Store package family. An old standalone ngrok process or an ngrok-shaped loopback service is rejected.

World Engine never falls back to downloading a standalone `ngrok.exe`. If Microsoft Store, WinGet, the source identity, the running package identity, or the App Execution Alias cannot be verified, startup stops with an explanation.

## Upgrade cleanup

Startup attempts to remove only the obsolete World Engine-managed files `%LOCALAPPDATA%\WorldEngine\tools\ngrok.exe` and `ngrok-windows-amd64.zip.download`. Reparse-point directories are refused, locked files are reported, and the old cache is never executed even when removal is blocked. Existing ngrok account configuration remains available to the startup controller.

## No-paste credential setup

The startup controller searches existing local ngrok configurations and environment variables first. If none work, it opens the official ngrok dashboard. Sign in and click **Copy**. World Engine watches the clipboard, validates the copied token with the ngrok CLI, writes its persistent user config, and continues. There is no console or GUI paste field.

## Stable endpoint

World Engine records the assigned HTTPS URL in `%LOCALAPPDATA%\WorldEngine\permanent_endpoint.json`. Repairs require that same URL; a different account/domain is not silently accepted.

## Continuous operation

A no-admin per-user supervisor starts after Windows sign-in and periodically verifies/repairs:

1. local backend;
2. local Bearer authentication;
3. ngrok endpoint process;
4. public health;
5. public protected authentication.

## One-time GPT Builder setup

Import `openapi_actions_PERMANENT.json` and configure its Bearer key once. Local software cannot write into private GPT Builder settings. The World Engine key is placed on the clipboard during initial setup.

## Availability boundary

The PC must be powered on, connected to the internet, and signed into the Windows user account. External ngrok service availability and account quotas apply.
