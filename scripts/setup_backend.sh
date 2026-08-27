#!/usr/bin/env bash
# Setup ambiente backend: venv + dipendenze (Python >= 3.11).
set -euo pipefail
cd "$(dirname "$0")/../backend" || exit 1

PY=${PYTHON:-python3}
if [ ! -d .venv ]; then
  echo ">> Creo virtualenv..."
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip -q
pip install -r requirements.txt -r requirements-dev.txt
echo ">> Backend pronto. Attivalo con: source backend/.venv/bin/activate"