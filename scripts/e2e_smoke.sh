#!/usr/bin/env bash
# Smoke test browser multipiattaforma: verifica che la build servita dalla SPA
# si carichi davvero in Chromium e che i deep-link non rompano il fallback.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE_URL="${TABULARIUM_E2E_URL:-http://127.0.0.1:8787}"
TMP_HTML="$(mktemp /tmp/tabularium-e2e.XXXXXX.html)"
SERVER_PID=""
cleanup() {
  rm -f "$TMP_HTML"
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if ! curl -fsS "$BASE_URL/api/health" >/dev/null 2>&1; then
  "$ROOT/scripts/run.sh" >/tmp/tabularium-e2e-server.log 2>&1 &
  SERVER_PID=$!
  for _ in {1..30}; do
    curl -fsS "$BASE_URL/api/health" >/dev/null 2>&1 && break
    sleep 0.2
  done
fi
curl -fsS "$BASE_URL/api/health" >/dev/null

chromium --headless --no-sandbox --disable-gpu --virtual-time-budget=3000 \
  --dump-dom "$BASE_URL/" >"$TMP_HTML" 2>/dev/null
grep -q '<title>Tabularium' "$TMP_HTML"
grep -q 'id="root"' "$TMP_HTML"

chromium --headless --no-sandbox --disable-gpu --virtual-time-budget=3000 \
  --dump-dom "$BASE_URL/dataset" >"$TMP_HTML" 2>/dev/null
grep -q '<title>Tabularium' "$TMP_HTML"

echo "e2e smoke OK: SPA root, dataset deep-link e health endpoint"
