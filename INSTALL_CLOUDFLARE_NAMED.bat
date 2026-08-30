@echo off
setlocal
cd /d "%~dp0"
echo Cloudflare Named Tunnel requires a domain already managed by Cloudflare.
set /p WE_URL=Stable HTTPS URL ^(example https://worldengine.example.com^): 
net session >nul 2>&1
if not %errorlevel%==0 (
  echo Please rerun this file as Administrator.
  pause
  exit /b 1
)
where py >nul 2>nul
if %errorlevel%==0 (py -3 INSTALL_PERMANENT_ENDPOINT_V398.py --root "%~dp0." --provider cloudflare --url "%WE_URL%" & pause & exit /b)
python INSTALL_PERMANENT_ENDPOINT_V398.py --root "%~dp0." --provider cloudflare --url "%WE_URL%"
pause
