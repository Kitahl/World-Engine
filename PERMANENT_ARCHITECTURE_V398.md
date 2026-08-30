# Permanent connection architecture

```text
ChatGPT GPT Action
        |
        | one fixed HTTPS server URL
        v
https://<device>.<tailnet>.ts.net
        |
        | Tailscale Funnel --bg
        | stable DNS + auto resume
        v
127.0.0.1:8000
        |
        v
World Engine FastAPI
        |
        +-- persistent API key
        +-- persistent SQLite campaign
        +-- %LOCALAPPDATA%\WorldEngine
```

The public hostname, API key identity, and campaign DB are all decoupled from the extracted World Engine version folder.

## Failure behavior

The installer and verifier require two independent checks:

1. `GET <permanent-url>/health` → HTTP 200.
2. protected `GET <permanent-url>/api/context?...` with the current Bearer key → HTTP 200.

A public 200 with protected 401 is authentication failure, not a campaign failure. A local 200 with public failure is endpoint/provider connectivity, not SQLite loss.
