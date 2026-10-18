#!/usr/bin/env bash
# Prepara un host GPU per il training ms-swift di Tabularium (M18).
#
# Idempotente e ripetibile. Segue la ricetta ufficiale di MonkeyOCRv2-Parsing
# (`parsing/train/README.md`): venv, dipendenze pinnate, e la copia di ms-swift
# bundlata nel repo (`pip install -e ./ms-swift` dalla cartella `parsing/train`).
# Non tocca il sistema e non serve privilegi oltre all'utente.
#
# Uso:
#   TABULARIUM_TRAIN_ROOT=$HOME/tabularium-train bash scripts/cloud/setup_cloud_train.sh
#
# Variabili:
#   TABULARIUM_TRAIN_ROOT   radice dell'ambiente (default: $HOME/tabularium-train)
#   TABULARIUM_TRAIN_PYTHON interprete base (default: python3)
#   TABULARIUM_MONKEY_REPO  URL del repo ufficiale (default: GitHub Yuliang-Liu)
#   TABULARIUM_TRAIN_FLASH_ATTN=1  installa anche flash-attn (build lunga, opzionale)
set -euo pipefail

ROOT="${TABULARIUM_TRAIN_ROOT:-$HOME/tabularium-train}"
PY="${TABULARIUM_TRAIN_PYTHON:-python3}"
MONKEY_REPO="${TABULARIUM_MONKEY_REPO:-https://github.com/Yuliang-Liu/MonkeyOCRv2}"

mkdir -p "$ROOT"
echo ">> ambiente: $ROOT"

# Il repo ufficiale contiene ms-swift come submodule: va inizializzato, altrimenti
# `pip install -e ./ms-swift` fallisce (setup.py legge README.md, assente).
if [ ! -d "$ROOT/MonkeyOCRv2/.git" ]; then
  echo ">> clono il repo ufficiale MonkeyOCRv2 (con submodule)"
  git clone --depth 1 --recurse-submodules --shallow-submodules "$MONKEY_REPO" "$ROOT/MonkeyOCRv2"
fi
git -C "$ROOT/MonkeyOCRv2" submodule update --init --recursive --depth 1

if [ ! -x "$ROOT/venv/bin/python" ]; then
  echo ">> creo il venv"
  "$PY" -m venv "$ROOT/venv"
fi
# shellcheck disable=SC1091
source "$ROOT/venv/bin/activate"
python -m pip install --upgrade pip wheel >/dev/null

# Dipendenze pinnate dalla ricetta ufficiale. Torch lo fornisce l'immagine CUDA.
echo ">> installo le dipendenze della ricetta (transformers/accelerate/qwen_vl_utils)"
python -m pip install "transformers==4.57.1" "accelerate==1.11.0" "qwen_vl_utils==0.0.14"

if [ "${TABULARIUM_TRAIN_FLASH_ATTN:-0}" = "1" ]; then
  echo ">> installo flash-attn (opzionale, build lunga)"
  python -m pip install flash-attn==2.8.3 --no-build-isolation || echo ">> flash-attn non installato: il training userà attn_impl=eager"
fi

# ms-swift bundlato: `pip install -e ./ms-swift` dalla cartella `parsing/train`.
TRAIN_DIR="$ROOT/MonkeyOCRv2/parsing/train"
if [ -d "$TRAIN_DIR/ms-swift" ]; then
  # Il `setup.py` bundlato apre `README.md`, che il repo non include (404):
  # senza, la build editable fallisce con FileNotFoundError.
  [ -f "$TRAIN_DIR/ms-swift/README.md" ] || printf '# ms-swift (bundled with MonkeyOCRv2)\n' > "$TRAIN_DIR/ms-swift/README.md"
  echo ">> installo ms-swift bundlato ($TRAIN_DIR/ms-swift)"
  ( cd "$TRAIN_DIR" && python -m pip install -e ./ms-swift )
else
  echo ">> ms-swift bundlato assente: installo la wheel pip 'ms-swift'"
  python -m pip install "ms-swift"
fi

echo ">> verifica"
# `swift --version` non esiste in questa versione di ms-swift: si verifica
# l'import. E si controlla che torch veda davvero la GPU: un container senza
# `/dev/nvidia*` importa torch ma non può addestrare.
python -c "import swift" || { echo "ERRORE: ms-swift non importabile"; exit 1; }
python -c "import torch; assert torch.cuda.is_available()" \
  || { echo "ERRORE: torch non vede la GPU (container senza device NVIDIA?)"; exit 1; }
# Marcatore di readiness: il backend può dire "preparato" solo quando esiste,
# non quando l'onstart è stato lanciato (l'installazione dura minuti).
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$ROOT/.train_ready"
echo "__TRAIN_ENV_READY__ $ROOT"
