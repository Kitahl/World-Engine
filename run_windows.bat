@echo off
if not exist .venv py -m venv .venv
call .venv\Scripts\activate.bat
pip install -r requirements.txt
if "%WORLD_ENGINE_API_KEY%"=="" (
  echo ERROR: WORLD_ENGINE_API_KEY is not set.
  echo Set it to a strong random secret, or use START_WORLD_ENGINE.bat which generates one automatically.
  exit /b 1
)
if "%WORLD_ENGINE_HOST%"=="" set WORLD_ENGINE_HOST=127.0.0.1
python app.py
