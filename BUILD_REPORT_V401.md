# World Engine 4.0.1 Startup Hotfix Build Report

## Scope

This is a compatibility hotfix over the exact shipped 4.0.0 release. It fixes the Windows installation-root quoting defect observed as:

`StartupError: World Engine app.py not found under ...\world_engine_chatgpt_v4_0_0"`

## Root cause

`%~dp0` expands to a path ending in `\`. Passing it as `--root "%~dp0"` can cause Windows command-line parsing to preserve the closing quote as part of the Python argument. The resulting path literally ends in `"`, so `root / "app.py"` points at a nonexistent directory.

## Fix

- All batch files pass `--root "%~dp0."`.
- `normalize_install_root()` strips accidental leading/trailing quote characters before resolving a path.
- Startup, supervisor, autostart, migration, and permanent-endpoint entry points use the centralized sanitizer.
- The permanent-endpoint installer passes the raw argument into the sanitizer instead of resolving a broken `Path` first.
- Engine version is 4.0.1. Database schema remains 13; WETP remains 1.0.

## Focused regression

- broken root with literal trailing `"` normalizes to the correct install directory;
- `automatic_startup()` accepts the exact broken root shape;
- no batch launcher contains `--root "%~dp0"`;
- startup and permanent setup contain `--root "%~dp0."`.
