#!/usr/bin/env sh
set -eu
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
: "${WORLD_ENGINE_API_KEY:?Set WORLD_ENGINE_API_KEY to a strong random secret before starting}"
export WORLD_ENGINE_API_KEY
export WORLD_ENGINE_HOST="${WORLD_ENGINE_HOST:-127.0.0.1}"
python app.py
