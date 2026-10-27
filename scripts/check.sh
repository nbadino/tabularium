#!/usr/bin/env bash
# One command, one truth. This is the "definition of done" in AGENTS.md.
#
#   scripts/check.sh          everything CI runs (backend, frontend, invariants)
#   scripts/check.sh --fast   invariants only: seconds, run it before every commit
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAST=0
case "${1:-}" in
  --fast) FAST=1 ;;
  "") ;;
  *) echo "usage: $0 [--fast]" >&2; exit 2 ;;
esac

PY="$ROOT/backend/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "backend/.venv not found: run scripts/setup_backend.sh" >&2
  exit 2
fi

step() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }

step "model surfaces"
"$PY" "$ROOT/scripts/surfaces.py" --check

step "surface invariants"
(cd "$ROOT/backend" && "$PY" -m pytest tests/test_model_surface.py tests/test_model_specs.py -q)

if [[ "$FAST" -eq 1 ]]; then
  printf '\nfast check: OK\n'
  exit 0
fi

step "backend tests"
(cd "$ROOT/backend" && "$PY" -m pytest -q)

if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  echo "frontend/node_modules not found: run scripts/setup_frontend.sh" >&2
  exit 2
fi

step "frontend typecheck"
(cd "$ROOT/frontend" && npm run --silent typecheck)

step "frontend tests"
(cd "$ROOT/frontend" && npm test --silent)

step "frontend build"
(cd "$ROOT/frontend" && npm run --silent build)

printf '\ncheck: OK\n'
