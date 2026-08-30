@echo off
setlocal
cd /d "%~dp0"
title World Engine 4.3.0 Automatic Startup

echo.
echo ============================================================
echo   WORLD ENGINE 4.3.0 - AUTOMATIC STARTUP
echo ============================================================
echo   Backend, persistent HTTPS, schema, and connection tests
echo   will be started automatically.
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 world_engine_startup.py --root "%~dp0."
    set RC=%errorlevel%
    if not "%RC%"=="0" pause
    exit /b %RC%
)

where python >nul 2>nul
if %errorlevel%==0 (
    python world_engine_startup.py --root "%~dp0."
    set RC=%errorlevel%
    if not "%RC%"=="0" pause
    exit /b %RC%
)

echo World Engine needs Python 3.11 or newer.
echo Install Python and enable Add Python to PATH, then run this again.
pause
exit /b 1
