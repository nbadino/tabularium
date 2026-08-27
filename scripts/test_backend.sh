#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../backend"
if [[ ! -x .venv/bin/python ]]; then
  echo "backend/.venv non trovato: esegui scripts/setup_backend.sh" >&2
  exit 2
fi
.venv/bin/python -m pytest "$@"
