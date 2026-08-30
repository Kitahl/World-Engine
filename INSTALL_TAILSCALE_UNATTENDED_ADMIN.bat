@echo off
setlocal
cd /d "%~dp0"
net session >nul 2>&1
if not %errorlevel%==0 (
  echo Requesting Administrator privileges ONLY for optional Tailscale unattended mode...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
where py >nul 2>nul
if %errorlevel%==0 (py -3 INSTALL_PERMANENT_ENDPOINT_V399.py --root "%~dp0." --provider tailscale-admin) else (python INSTALL_PERMANENT_ENDPOINT_V399.py --root "%~dp0." --provider tailscale-admin)
pause
