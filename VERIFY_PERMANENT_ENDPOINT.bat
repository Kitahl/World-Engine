@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3 VERIFY_PERMANENT_ENDPOINT_V400.py & set RC=%errorlevel% & pause & exit /b %RC%)
python VERIFY_PERMANENT_ENDPOINT_V400.py
set RC=%errorlevel%
pause
exit /b %RC%
