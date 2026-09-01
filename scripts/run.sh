#!/usr/bin/env bash
# Avvio MONOPROCESSO: backend FastAPI + frontend built servito su http://<host>:8787
# Ricostruisce il frontend quando i sorgenti sono più recenti della build.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Configurazioni locali (TABULARIUM_TRAIN_REPO, TABULARIUM_TRAIN_ENV, ...) opzionali.
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi

DIST_INDEX="$ROOT/frontend/dist/index.html"
needs_build=0
if [ ! -f "$DIST_INDEX" ]; then
  needs_build=1
elif find "$ROOT/frontend/src" "$ROOT/frontend/index.html" "$ROOT/frontend/package.json" "$ROOT/frontend/package-lock.json" \
    -type f -newer "$DIST_INDEX" -print -quit 2>/dev/null | grep -q .; then
  needs_build=1
fi
if [ "${TABULARIUM_BUILD_FRONTEND:-0}" = "1" ]; then
  needs_build=1
fi
if [ "$needs_build" -eq 1 ]; then
  echo ">> frontend/dist assente o non aggiornato: eseguo la build..."
  "$ROOT/scripts/build_frontend.sh"
fi

cd "$ROOT/backend" || exit 1
# shellcheck disable=SC1091
BACKEND_ENV="${TABULARIUM_BACKEND_ENV:-$ROOT/backend/.venv}"
# Se è stato installato il runtime UVDoc dedicato, usalo automaticamente.
# L'ambiente base Python 3.14 non ha una wheel PaddlePaddle compatibile.
if [ -z "${TABULARIUM_BACKEND_ENV:-}" ] && [ -x "$ROOT/.venv-uvdoc/bin/uvicorn" ]; then
  BACKEND_ENV="$ROOT/.venv-uvdoc"
fi
source "$BACKEND_ENV/bin/activate"
exec uvicorn app.main:app --host "${TABULARIUM_HOST:-127.0.0.1}" --port "${TABULARIUM_PORT:-8787}"
