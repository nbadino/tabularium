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

# Alcune immagini vastai/pytorch includono `cuda-compat` (una libcuda userspace
# più nuova del driver dell'host) e la registrano in ldconfig **prima** del
# driver reale montato da Vast: torch carica quella e ogni query CUDA fallisce
# con `Error 803: unsupported display driver / cuda driver combination`, anche
# scegliendo un torch della versione giusta. Se c'è un driver reale, la compat
# va tolta di mezzo: si disabilita la sua directory e la sua voce ldconfig.
if [ -x "$(command -v nvidia-smi)" ] && nvidia-smi -L >/dev/null 2>&1; then
  for d in /usr/local/cuda*/compat; do
    [ -d "$d" ] || continue
    echo ">> disabilito cuda-compat: $d (driver reale $(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1))"
    mv "$d" "$d.disabled" 2>/dev/null || true
  done
  grep -rl "cuda.*/compat" /etc/ld.so.conf.d/ 2>/dev/null | while read -r f; do
    rm -f "$f" 2>/dev/null || true
  done
  ldconfig 2>/dev/null || true
fi

# Dipendenze pinnate dalla ricetta ufficiale. Il torch va scelto **prima** e
# dall'indice giusto: un venv "pulito" non eredita il torch dell'immagine, e
# `pip install transformers` da solo tira l'ultimo torch (cu13x), che su un
# driver più vecchio non inizializza CUDA (`Error 803: unsupported display
# driver / cuda driver combination`). La versione CUDA di torch deve essere
# compatibile col driver dell'host: si legge il driver da nvidia-smi e si
# sceglie l'indice. `TABULARIUM_TORCH_INDEX` forza una scelta.
if [ -n "${TABULARIUM_TORCH_INDEX:-}" ]; then
  TORCH_INDEX="$TABULARIUM_TORCH_INDEX"
else
  DRIVER="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 || true)"
  DRV_MAJOR="${DRIVER%%.*}"
  case "$DRV_MAJOR" in ''|*[!0-9]*) DRV_MAJOR=0 ;; esac
  if [ "$DRV_MAJOR" -ge 570 ]; then
    TORCH_INDEX="https://download.pytorch.org/whl/cu128"
  elif [ "$DRV_MAJOR" -ge 560 ]; then
    TORCH_INDEX="https://download.pytorch.org/whl/cu126"
  elif [ "$DRV_MAJOR" -ge 550 ]; then
    TORCH_INDEX="https://download.pytorch.org/whl/cu124"
  else
    TORCH_INDEX="https://download.pytorch.org/whl/cu121"
  fi
  echo ">> driver NVIDIA $DRIVER → torch index $TORCH_INDEX"
fi

# CUDA attesa dall'indice (cu124 → 12.4): se il venv ha già un torch con quella
# CUDA si salta la (ri)installazione, così un secondo run non riscarica 800 MB.
WANT_CUDA="$(printf '%s' "$TORCH_INDEX" | sed -nE 's#.*/cu([0-9]{2})([0-9])[^0-9]*$#\1.\2#p')"
HAVE_CUDA="$(python -c 'import torch;print(torch.version.cuda)' 2>/dev/null || true)"
HAVE_VISION="$(python -c 'import torchvision;print(torchvision.__version__)' 2>/dev/null || true)"
if [ -n "$HAVE_CUDA" ] && [ "$HAVE_CUDA" = "$WANT_CUDA" ] && [ -n "$HAVE_VISION" ]; then
  echo ">> torch/torchvision già presenti (CUDA $HAVE_CUDA, vision $HAVE_VISION): salto"
else
  echo ">> installo torch/torchvision dall'indice $TORCH_INDEX (CUDA $WANT_CUDA, trovata '$HAVE_CUDA')"
  if [ -n "$HAVE_CUDA" ] && [ "$HAVE_CUDA" = "$WANT_CUDA" ]; then
    # Torch giusto, manca solo torchvision (importato dal modello/preprocessore).
    python -m pip install torchvision --index-url "$TORCH_INDEX"
  else
    # `pip install torch` da solo NON basta: se un torch ha la stessa versione
    # pubblicata sull'indice, pip dice "already satisfied" e non lo sostituisce.
    # E i pacchetti `nvidia-*` di un tentativo precedente restano accanto a quelli
    # nuovi (cu13 accanto a cu12), torch li precarica tutti → mismatch. Si purga
    # torch/triton/nvidia e si reinstalla pulito.
    python -m pip freeze 2>/dev/null | grep -iE "^(nvidia-|triton==|torch==|torchvision==)" | cut -d= -f1 \
      | xargs -r python -m pip uninstall -y >/dev/null 2>&1 || true
    rm -rf "$ROOT/venv/lib"/python*/site-packages/nvidia
    python -m pip install torch torchvision --index-url "$TORCH_INDEX"
  fi
fi

echo ">> installo le dipendenze della ricetta (transformers/accelerate/qwen_vl_utils)"
python -m pip install "transformers==4.57.1" "accelerate==1.11.0" "qwen_vl_utils==0.0.14"
# Lo script ufficiale lora.sh passa `--deepspeed zero1`: senza il pacchetto
# `swift sft` muore subito con PackageNotFoundError, prima di leggere i dati.
python -m pip install "deepspeed==0.17.1"

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
  # Il fork non versiona `swift/llm/dataset/data/dataset_info.json`, ma
  # `swift.llm.dataset.__init__` chiama `register_dataset_info()` all'import:
  # senza il file, `json.loads(<path>)` esplode e `swift sft` non parte
  # (`JSONDecodeError`). I dataset di Tabularium sono path locali, quindi la
  # lista vuota è la definizione valida: non registra dataset remoti e non
  # sovrascrive nulla. Il file è generato, non modificato a mano.
  DATASET_INFO="$TRAIN_DIR/ms-swift/swift/llm/dataset/data/dataset_info.json"
  if [ ! -f "$DATASET_INFO" ]; then
    mkdir -p "$(dirname "$DATASET_INFO")"
    printf '[]\n' > "$DATASET_INFO"
    echo ">> creato dataset_info.json vuoto (assente nel fork; dataset da path locali)"
  fi
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
