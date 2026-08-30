# World Engine v3.9.9 — No-Admin Permanent Endpoint Repair

- Default Permanent Endpoint Setup no longer elevates to Administrator.
- Replaces Tailscale unattended as the default with portable ngrok user mode.
- Uses the free account-assigned stable ngrok development domain.
- Stores ngrok executable/config under persistent `%LOCALAPPDATA%\WorldEngine` data.
- Installs a current-user Startup bootstrap instead of a Windows service.
- Startup bootstrap starts both World Engine and the configured ngrok endpoint after user login.
- Launcher automatically attempts to restart a configured permanent endpoint before declaring it unreachable.
- `VERIFY_PERMANENT_ENDPOINT.bat` also self-heals before probing.
- Hostname drift fails closed instead of silently rewriting the GPT schema.
- Tailscale unattended remains available only through `INSTALL_TAILSCALE_UNATTENDED_ADMIN.bat`.
- Database schema remains 12; gameplay systems are unchanged.
