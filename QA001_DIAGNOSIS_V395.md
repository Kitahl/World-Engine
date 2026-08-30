# QA-001 — Campaign Bootstrap Diagnosis

## Finding

The v3.9.4/v3.9.5 campaign bootstrap implementation is not reproducibly failing in the backend. Real HTTP testing against v3.9.5 produced:

- `GET /health` with no auth: HTTP 200.
- `POST /api/campaign` minimal payload + correct Bearer: HTTP 200, campaign revision 0 returned.
- `POST /api/campaign` explicit `world_time` + correct Bearer: HTTP 200.
- same endpoint + wrong Bearer: HTTP 401.
- same endpoint + missing Bearer: HTTP 401.

Therefore the reported GPT-side `aiohttp.client_exceptions.ClientResponseError` is most consistent with the deployment binding rather than the SQLite bootstrap algorithm. Leading causes are a stale Quick Tunnel URL, stale GPT Bearer token after a new launcher key was generated, or an older World Engine process still occupying port 8000 under a different key.

## v3.9.5 fix

The launcher now checks the protected local endpoint before accepting an already-running service, checks the protected public endpoint before declaring HTTPS ready, records a non-secret API-key fingerprint, detects schema/public-URL mismatch, and provides **Test Action Connection** to classify the failure.

The campaign database remains authoritative; no outage-time state should be invented or backfilled until bootstrap/context reads succeed.
