#!/usr/bin/env bash
# Setup ambiente frontend: node_modules (Node >= 20).
set -euo pipefail
cd "$(dirname "$0")/../frontend" || exit 1

npm ci
echo ">> Frontend pronto."
