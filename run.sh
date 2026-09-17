#!/usr/bin/env bash
# AgroSuite — inicializador para Linux e macOS.
# Cria o ambiente virtual na primeira execução e sobe o app nas seguintes.
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv"
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
  echo
  echo "  Primeira execução: preparando o ambiente."
  echo
  python3 -m venv "$VENV"
  "$PY" -m pip install --upgrade pip --quiet
  "$PY" -m pip install -r requirements.txt
fi

exec "$PY" -m agrosuite "$@"
