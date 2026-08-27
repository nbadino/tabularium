#!/usr/bin/env bash
# Avvio automatico di Lloyds Lab al login dell'utente.
# Avvia backend + frontend buildato una sola volta e apre la dashboard quando pronta.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${LLOYDS_HOST:-127.0.0.1}"
PORT="${LLOYDS_PORT:-8787}"
URL="http://${HOST}:${PORT}"
LOG_FILE="${LLOYDS_AUTOSTART_LOG:-/tmp/lloyds-lab-autostart.log}"
LOCK_FILE="${LLOYDS_AUTOSTART_LOCK:-/tmp/lloyds-lab-autostart.lock}"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  exit 0
fi

cd "$ROOT"

# Carica la configurazione locale, se presente.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
  HOST="${LLOYDS_HOST:-127.0.0.1}"
  PORT="${LLOYDS_PORT:-8787}"
  URL="http://${HOST}:${PORT}"
fi

if ! curl -fsS --max-time 2 "${URL}/api/health" >/dev/null 2>&1; then
  nohup "$ROOT/scripts/run.sh" >>"$LOG_FILE" 2>&1 &
  SERVER_PID=$!

  ready=0
  for _ in $(seq 1 60); do
    if curl -fsS --max-time 2 "${URL}/api/health" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 0.5
  done

  if [ "$ready" -ne 1 ]; then
    echo "[$(date -Is)] Lloyds Lab non pronto dopo 30 secondi (PID ${SERVER_PID})." >>"$LOG_FILE"
    exit 1
  fi
fi

# xdg-open ritorna subito e non mantiene aperta una shell del desktop.
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 || true
fi
