@echo off
setlocal
cd /d "%~dp0"
title World Engine 5.1.1 Automatic Permanent Endpoint

echo World Engine 5.1.1 automatic no-admin endpoint setup
echo.
echo No tunnel account, token copy, or paste box is required by default.
echo An existing configured ngrok endpoint is preserved and reused.
echo Otherwise World Engine starts an account-free Cloudflare Quick Tunnel,
echo generates the Action schema, and runs authenticated tests automatically.
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 INSTALL_PERMANENT_ENDPOINT_V400.py --provider auto --root "%~dp0."
) else (
  python INSTALL_PERMANENT_ENDPOINT_V400.py --provider auto --root "%~dp0."
)
set RC=%errorlevel%
echo.
if not "%RC%"=="0" pause
exit /b %RC%
