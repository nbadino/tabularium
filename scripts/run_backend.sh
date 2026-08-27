#!/usr/bin/env bash
# Avvio backend API su http://127.0.0.1:8787 (override: LLOYDS_HOST/LLOYDS_PORT).
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
cd backend || exit 1
# shellcheck disable=SC1091
BACKEND_ENV="${LLOYDS_BACKEND_ENV:-$ROOT/backend/.venv}"
if [ -z "${LLOYDS_BACKEND_ENV:-}" ] && [ -x "$ROOT/.venv-uvdoc/bin/uvicorn" ]; then
  BACKEND_ENV="$ROOT/.venv-uvdoc"
fi
source "$BACKEND_ENV/bin/activate"
exec uvicorn app.main:app --host "${LLOYDS_HOST:-127.0.0.1}" --port "${LLOYDS_PORT:-8787}" --reload
