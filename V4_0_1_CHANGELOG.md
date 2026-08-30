# World Engine 4.0.1 Startup Hotfix

- Fixes Windows `cmd.exe` root-path quoting when `%~dp0` ends in a backslash.
- All batch launchers now pass `--root "%~dp0."`.
- Python startup, supervisor, autostart, connection-guard, and permanent-endpoint entry points defensively strip accidental quote characters from install roots.
- Fixes the observed `StartupError: World Engine app.py not found under ...\world_engine_chatgpt_v4_0_0"`.
- No database schema change: schema remains 13.
- WETP remains 1.0 and capability manifests remain compatible with 4.0.0.
