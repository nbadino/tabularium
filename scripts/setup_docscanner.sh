#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${TABULARIUM_DOCSCANNER_ROOT:-$ROOT/vendor/DocScanner}"

if [ ! -d "$TARGET/.git" ]; then
  git clone --depth 1 https://github.com/fh2019ustc/DocScanner.git "$TARGET"
fi

echo "Repository DocScanner: $TARGET"
echo "Scarica i pesi indicati nel README ufficiale e copiali in:"
echo "$TARGET/model_pretrained/{seg.pth,DocScanner-L.pth}"
echo "Poi imposta: export TABULARIUM_DOCSCANNER_ROOT=$TARGET"
