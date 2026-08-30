# World Engine v3.9.5 — Action Bootstrap Diagnostics

- Keeps schema version 12.
- Adds authenticated launcher self-tests for both local and public HTTPS API paths.
- Adds `Test Action Connection` UI control.
- Classifies local API, local auth, public tunnel, public auth, stale-schema URL, and likely stale GPT Bearer-token failures.
- Records a non-secret 12-hex SHA-256 API-key fingerprint for configuration comparison.
- Temporary HTTPS is not declared ready until both `/health` and an authenticated protected endpoint succeed through the public URL.
- Warns explicitly when a Quick Tunnel URL changes and requires Action schema re-import.
- No campaign-state schema migration is required.
