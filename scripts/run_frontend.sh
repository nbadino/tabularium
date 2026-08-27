#!/usr/bin/env bash
# Avvio frontend dev (Vite, con proxy /api -> 127.0.0.1:8787).
set -euo pipefail
cd "$(dirname "$0")/../frontend" || exit 1
exec npm run dev