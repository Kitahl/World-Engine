@echo off
setlocal
cd /d "%~dp0"
title World Engine 5.1.0 Desktop Companion
if not exist ".venv\Scripts\python.exe" (
  echo World Engine's private Python runtime is not ready.
  echo Run START_WORLD_ENGINE.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "world_engine_companion.py"
if errorlevel 1 (
  echo.
  echo WORLD ENGINE DESKTOP COMPANION FAILED
  echo The local engine was not modified.
  pause
)
