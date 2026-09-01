@echo off
setlocal
cd /d "%~dp0"
title World Engine 5.1.1 Automatic Startup

echo.
echo ============================================================
echo   WORLD ENGINE 5.1.1 - AUTOMATIC STARTUP
echo ============================================================
echo   Backend, persistent HTTPS, schema, and connection tests
echo   will be started automatically.
echo   Existing configured ngrok is reused. Otherwise an account-free
echo   Cloudflare Quick Tunnel starts automatically with no token copy.
echo   The local engine still starts if the optional public link is unavailable.
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
