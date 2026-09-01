#!/usr/bin/env bash
# Setup ambiente backend: venv + dipendenze (Python 3.11–3.13).
set -euo pipefail
cd "$(dirname "$0")/../backend" || exit 1

PY=${PYTHON:-python3}
if ! PY_VERSION=$(
  "$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")'
); then
  echo "Python non trovato: $PY (imposta PYTHON=/percorso/python3.13)" >&2
  exit 2
fi
case "$PY_VERSION" in
  3.11|3.12|3.13) ;;
  *)
    echo "Versione Python non supportata: $PY_VERSION (richiesta 3.11–3.13)" >&2
    exit 2
    ;;
esac
if [ ! -d .venv ]; then
  echo ">> Creo virtualenv..."
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip -q
pip install -r requirements.txt -r requirements-dev.txt
if ! python -c 'import cryptography; print("cryptography " + cryptography.__version__)'; then
  echo "Dipendenza cryptography non disponibile: il vault dei segreti non è sicuro." >&2
  exit 2
fi
echo ">> Backend pronto. Attivalo con: source backend/.venv/bin/activate"
