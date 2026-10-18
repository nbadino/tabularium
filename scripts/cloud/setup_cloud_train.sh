#!/usr/bin/env bash
# Prepara un host GPU per il training ms-swift di Tabularium (M18).
#
# Idempotente e ripetibile: crea un venv dedicato, installa ms-swift (la copia
# bundlata nel repo ufficiale MonkeyOCRv2 se presente, altrimenti la wheel pip),
# clona il repo di training e verifica che `swift` risponda. Non tocca il
# sistema e non serve privilegi oltre all'utente.
#
# Uso:
#   TABULARIUM_TRAIN_ROOT=$HOME/tabularium-train bash scripts/cloud/setup_cloud_train.sh
#
# Variabili:
#   TABULARIUM_TRAIN_ROOT   radice dell'ambiente (default: $HOME/tabularium-train)
#   TABULARIUM_TRAIN_PYTHON interprete base (default: python3)
#   TABULARIUM_MONKEY_REPO  URL del repo ufficiale (default: GitHub Yuliang-Liu)
set -euo pipefail

ROOT="${TABULARIUM_TRAIN_ROOT:-$HOME/tabularium-train}"
PY="${TABULARIUM_TRAIN_PYTHON:-python3}"
MONKEY_REPO="${TABULARIUM_MONKEY_REPO:-https://github.com/Yuliang-Liu/MonkeyOCRv2}"

mkdir -p "$ROOT"

echo ">> ambiente: $ROOT"
if [ ! -x "$ROOT/venv/bin/python" ]; then
  echo ">> creo il venv"
  "$PY" -m venv "$ROOT/venv"
fi
# shellcheck disable=SC1091
source "$ROOT/venv/bin/activate"
python -m pip install --upgrade pip wheel >/dev/null

if [ ! -d "$ROOT/MonkeyOCRv2/.git" ]; then
  echo ">> clono il repo ufficiale MonkeyOCRv2"
  git clone --depth 1 --recurse-submodules "$MONKEY_REPO" "$ROOT/MonkeyOCRv2"
fi

# ms-swift: la copia bundlata dal repo ufficiale è la ricetta monkeyocrv2-compatibile.
SWIFT_DIR="$ROOT/MonkeyOCRv2/parsing/train/ms-swift"
if [ -d "$SWIFT_DIR" ]; then
  echo ">> installo ms-swift bundlato ($SWIFT_DIR)"
  python -m pip install -e "$SWIFT_DIR"
else
  echo ">> ms-swift bundlato assente: installo la wheel pip 'ms-swift'"
  python -m pip install "ms-swift"
fi

echo ">> verifica"
swift --version || { echo "ERRORE: 'swift' non risponde"; exit 1; }
# Marcatore di readiness: il backend può dire "preparato" solo quando esiste,
# non quando l'onstart è stato lanciato (l'installazione dura minuti).
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$ROOT/.train_ready"
echo "__TRAIN_ENV_READY__ $ROOT"
