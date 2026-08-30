# Cloudflare Named Tunnel option

Use this only if you prefer a custom domain.

One-time Cloudflare dashboard work:

1. Networking → Tunnels → Create tunnel.
2. Create a remotely managed tunnel, e.g. `world-engine`.
3. Add a Published Application route such as `worldengine.example.com`.
4. Service URL: `http://127.0.0.1:8000`.
5. Copy the tunnel token from the connector installation command.
6. Run `INSTALL_CLOUDFLARE_NAMED_V398.bat` as Administrator.
7. Enter `https://worldengine.example.com` and the token when prompted.

The installer uses the pinned Cloudflare `cloudflared` 2026.8.2 Windows AMD64 binary and verifies SHA-256 `c29eee2b121f5436a642eed69fd9767da7e7b8c510fa50aaa130337f931357b5` before service installation.

The named tunnel runs as a Windows service and the hostname stays fixed. The GPT imports `openapi_actions_PERMANENT.json` once.
