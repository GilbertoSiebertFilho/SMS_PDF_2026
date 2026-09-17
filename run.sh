#!/usr/bin/env bash
# AgroSuite - launcher for Linux and macOS.
# Creates the virtual environment on the first run, then starts the app.
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv"
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
  echo
  echo "  First run: setting up the environment."
  echo
  python3 -m venv "$VENV"
  "$PY" -m pip install --upgrade pip --quiet
  "$PY" -m pip install -r requirements.txt
fi

exec "$PY" -m agrosuite "$@"
