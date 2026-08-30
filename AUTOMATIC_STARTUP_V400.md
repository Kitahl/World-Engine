# World Engine 4.0 Automatic Startup

## Objective

Provide one user action—double-click `START_WORLD_ENGINE.bat`—that brings the local authoritative backend and its stable HTTPS endpoint into a verified usable state without Administrator privileges or a console paste prompt.

## Credential discovery order

World Engine searches in this order:

1. persistent World Engine ngrok config;
2. standard user ngrok config locations;
3. `WORLD_ENGINE_NGROK_AUTHTOKEN`;
4. `NGROK_AUTHTOKEN`;
5. interactive browser/clipboard capture.

The clipboard path opens the official ngrok authtoken page and waits for the account owner to press its **Copy** button. Token-shaped clipboard values are validated through `ngrok config check`. Rejected values are fingerprinted and skipped rather than repeatedly recaptured.

The token itself is never written to startup logs or status receipts.

## World Engine API key

The API key is loaded from persistent `%LOCALAPPDATA%\WorldEngine\launcher_config.json`. If it does not exist, a cryptographically random key is generated once. The backend, endpoint verifier, launcher, and generated schema all use the same persistent identity.

A non-secret 12-character SHA-256 fingerprint is used for diagnosis without exposing the key.

## Ordered startup transaction

```text
persistent-data migration
  → private Python runtime
  → backend start/protected-auth test
  → endpoint auth discovery
  → stable endpoint start/repair
  → public health test
  → public protected-auth test
  → permanent schema generation
  → no-admin user startup install
  → supervisor launch
  → final verification receipt
  → launcher UI
```

The startup controller fails closed if public health or protected authentication does not pass.

## Supervisor

One hidden per-user supervisor starts after Windows sign-in. It uses a process-lifetime lock to prevent duplicates and rechecks the backend/endpoint every 30 seconds by default. A failed process or temporary network interruption is retried without changing the configured hostname.

Supervisor state:

- `%LOCALAPPDATA%\WorldEngine\supervisor_status.json`
- `%LOCALAPPDATA%\WorldEngine\logs\world_engine_supervisor.log`

## Stable-host invariant

Once `permanent_endpoint.json` exists, repair requires the same expected hostname. A different ngrok account/domain is not silently accepted because that would leave the GPT schema pointed at the old endpoint.

## First-use boundary

Automation cannot perform two account-owner actions:

1. private ngrok login/Copy when no local credential exists;
2. private GPT Builder schema import and Bearer authentication configuration.

The startup controller prepares and reveals the exact files/clipboard value for those one-time actions. Later local startup is automatic.

## Failure receipts

- `last_startup_result.json`
- `GPT_ACTION_SETUP_READY.txt`
- `supervisor_status.json`

These contain status, URLs, fingerprints, process results, and health/auth checks, but no raw secrets.
