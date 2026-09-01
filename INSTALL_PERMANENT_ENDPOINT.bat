@echo off
setlocal
cd /d "%~dp0"
title World Engine 5.1.0 Automatic Permanent Endpoint

echo World Engine 5.1.0 automatic no-admin endpoint setup
echo.
echo No paste box is used.
echo If ngrok is not already configured, the official dashboard opens.
echo Sign in and click its Copy button once; World Engine captures the
echo copied authtoken, configures ngrok, starts HTTPS, generates the
echo permanent Action schema, and runs authenticated tests automatically.
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 INSTALL_PERMANENT_ENDPOINT_V400.py --provider ngrok --root "%~dp0."
) else (
  python INSTALL_PERMANENT_ENDPOINT_V400.py --provider ngrok --root "%~dp0."
)
set RC=%errorlevel%
echo.
if not "%RC%"=="0" pause
exit /b %RC%
