@echo off
setlocal
cd /d "%~dp0"
if not defined WORLD_ENGINE_FOUNDRY_API_KEY (
  echo WORLD_ENGINE_FOUNDRY_API_KEY is not set. The relay may reject delivery.
)
set "WORLD_ENGINE_PRIVATE_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%WORLD_ENGINE_PRIVATE_PYTHON%" (
  echo ERROR: World Engine private Python runtime is missing.
  echo Run START_WORLD_ENGINE.bat once, then start the companion worker again.
  exit /b 1
)
"%WORLD_ENGINE_PRIVATE_PYTHON%" scripts\companion_worker.py --max-items 100
if errorlevel 1 pause
endlocal
