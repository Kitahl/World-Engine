@echo off
setlocal
cd /d "%~dp0"

set "WORLD_ENGINE_PRIVATE_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%WORLD_ENGINE_PRIVATE_PYTHON%" (
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 -m venv .venv
  ) else (
    where python >nul 2>nul
    if errorlevel 1 (
      echo ERROR: Python 3.11 or newer is required.
      exit /b 1
    )
    python -m venv .venv
  )
  if errorlevel 1 exit /b 1
)

"%WORLD_ENGINE_PRIVATE_PYTHON%" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 exit /b 1

if "%WORLD_ENGINE_API_KEY%"=="" (
  echo ERROR: WORLD_ENGINE_API_KEY is not set.
  echo Set it to a strong random secret, or use START_WORLD_ENGINE.bat which generates one automatically.
  exit /b 1
)
if "%WORLD_ENGINE_HOST%"=="" set WORLD_ENGINE_HOST=127.0.0.1
"%WORLD_ENGINE_PRIVATE_PYTHON%" app.py
exit /b %errorlevel%
