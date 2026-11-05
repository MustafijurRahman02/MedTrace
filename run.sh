#!/usr/bin/env bash
# One-command local start (Linux/macOS). Settings come from the .env file in this folder (read by the app itself).
set -e
cd "$(dirname "$0")/backend"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
