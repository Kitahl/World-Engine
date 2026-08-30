@echo off
setlocal
cd /d "%~dp0"
if not defined WORLD_ENGINE_FOUNDRY_API_KEY (
  echo WORLD_ENGINE_FOUNDRY_API_KEY is not set. The relay may reject delivery.
)
python scripts\companion_worker.py --max-items 100
if errorlevel 1 pause
endlocal
