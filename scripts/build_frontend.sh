#!/usr/bin/env bash
# Build del frontend in frontend/dist (requisito: npm install già eseguito).
set -euo pipefail
cd "$(dirname "$0")/../frontend" || exit 1
npm run build
echo ">> Frontend build in frontend/dist."