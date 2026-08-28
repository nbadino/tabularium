#!/usr/bin/env bash
# Avvio automatico di Tabularium al login dell'utente.
# Avvia backend + frontend buildato una sola volta e apre la dashboard quando pronta.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${TABULARIUM_HOST:-127.0.0.1}"
PORT="${TABULARIUM_PORT:-8787}"
URL="http://${HOST}:${PORT}"
LOG_FILE="${TABULARIUM_AUTOSTART_LOG:-/tmp/tabularium-autostart.log}"
LOCK_FILE="${TABULARIUM_AUTOSTART_LOCK:-/tmp/tabularium-autostart.lock}"

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
  HOST="${TABULARIUM_HOST:-127.0.0.1}"
  PORT="${TABULARIUM_PORT:-8787}"
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
    echo "[$(date -Is)] Tabularium non pronto dopo 30 secondi (PID ${SERVER_PID})." >>"$LOG_FILE"
    exit 1
  fi
fi

# xdg-open ritorna subito e non mantiene aperta una shell del desktop.
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 || true
fi
