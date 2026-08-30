# Permanent Endpoint Guide — World Engine 4.0

## Default path

Run `START_WORLD_ENGINE.bat`. World Engine uses the standalone ngrok agent in the current Windows user session. Administrator privileges are not required.

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
