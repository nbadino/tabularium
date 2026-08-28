#!/usr/bin/env bash
# Avvio backend API su http://127.0.0.1:8787 (override: TABULARIUM_HOST/TABULARIUM_PORT).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
cd backend || exit 1
# shellcheck disable=SC1091
BACKEND_ENV="${TABULARIUM_BACKEND_ENV:-$ROOT/backend/.venv}"
if [ -z "${TABULARIUM_BACKEND_ENV:-}" ] && [ -x "$ROOT/.venv-uvdoc/bin/uvicorn" ]; then
  BACKEND_ENV="$ROOT/.venv-uvdoc"
fi
source "$BACKEND_ENV/bin/activate"
exec uvicorn app.main:app --host "${TABULARIUM_HOST:-127.0.0.1}" --port "${TABULARIUM_PORT:-8787}" --reload
