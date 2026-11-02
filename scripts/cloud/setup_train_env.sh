#!/usr/bin/env bash
# Ambiente di training "giusto per il modello", senza passi manuali.
#
# Una sola immagine PyTorch (o l'immagine Paddle per PaddleOCR-VL) e questo
# script: sceglie il profilo dalla ricetta, crea un venv isolato per profilo e
# installa il toolchain ufficiale con i pin misurati in docs/guide/training.md.
# Scrive `ready.json` (python + repo) e `.train_ready`: il backend aspetta il
# file, non presume che l'onstart sia finito.
#
# Uso (onstart Vast):
#   TABULARIUM_TRAIN_PROFILE=qwen TABULARIUM_TRAIN_ADAPTER=qwen3-vl-8b \
#     bash setup_train_env.sh
set -euo pipefail

PROFILE="${TABULARIUM_TRAIN_PROFILE:?profilo mancante}"
ADAPTER="${TABULARIUM_TRAIN_ADAPTER:-}"
ROOT="${TABULARIUM_TRAIN_ROOT:-$HOME/tabularium-train}/$PROFILE"
PY="${TABULARIUM_TRAIN_PYTHON:-python3}"
LOG="$ROOT/setup.log"
mkdir -p "$ROOT"
exec >>"$LOG" 2>&1

log() { printf '>> %s\n' "$*"; }
fail() {
  printf '__TRAIN_ENV_FAILED__ %s\n' "$*"
  printf '%s\n' "$*" >"$ROOT/.failed"
  exit 1
}
trap 'fail "comando fallito alla riga $LINENO"' ERR

log "$(date -u +%FT%TZ) profilo=$PROFILE adapter=$ADAPTER"
log "root=$ROOT"
export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq || true
  apt-get install -y -qq git curl python3-venv >/dev/null || true
fi

# Il venv va creato *prima* di installare torch: su alcune immagini il pip
# di sistema è quello di Debian e non può disinstallare i pacchetti di sistema
# (wheel installato da apt). Dentro il venv pip è libero di lavorare.
if [ ! -x "$ROOT/venv/bin/python" ]; then
  log "creo il venv (system-site-packages: eredita torch/paddle dell'immagine)"
  if ! "$PY" -m venv --system-site-packages "$ROOT/venv"; then
    log "python3-venv assente: lo installo e riprovo"
    apt-get update -qq || true
    apt-get install -y -qq python3-venv >/dev/null || true
    "$PY" -m venv --system-site-packages "$ROOT/venv"
  fi
fi
# shellcheck disable=SC1091
source "$ROOT/venv/bin/activate"
python -m pip install --upgrade pip >/dev/null

# Immagini PyTorch: il torch dell'immagine è già compatibile col driver scelto
# da Vast. MonkeyOCRv2 ha una combinazione misurata (torch 2.6 + transformers
# 4.57 + DeepSpeed zero1): con torch 2.11 il primo passo muore nello scheduler
# (`zip() argument 2 is longer than argument 1`). Per quel profilo si pinna.
if [ "$PROFILE" = "monkey" ]; then
  if ! python -c 'import torch; assert torch.__version__.startswith("2.6."); assert torch.cuda.is_available()' >/dev/null 2>&1; then
    log "installo torch 2.6.0/torchvision 0.21.0 (cu124) per la ricetta Monkey"
    python -m pip install --force-reinstall "torch==2.6.0" "torchvision==0.21.0" \
      --index-url https://download.pytorch.org/whl/cu124
  else
    log "torch 2.6 CUDA già presente"
  fi
elif [ "$PROFILE" != "paddle" ]; then
  if ! python -c 'import torch; assert torch.cuda.is_available()' >/dev/null 2>&1; then
    log "torch CUDA assente: installo torch/torchvision (cu128)"
    python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
  else
    log "torch CUDA già presente nell'immagine"
  fi
  if ! python -c 'import torchvision' >/dev/null 2>&1; then
    log "torchvision assente: lo installo"
    python -m pip install torchvision --index-url https://download.pytorch.org/whl/cu128
  fi
fi

install_common() {
  python -m pip install accelerate "qwen_vl_utils==0.0.14"
}

case "$PROFILE" in
  monkey)
    REPO="$ROOT/MonkeyOCRv2/parsing/train"
    if [ ! -d "$ROOT/MonkeyOCRv2/.git" ]; then
      log "clono MonkeyOCRv2 (fork ms-swift incluso)"
      git clone --depth 1 --recurse-submodules --shallow-submodules \
        "${TABULARIUM_MONKEY_REPO:-https://github.com/Yuliang-Liu/MonkeyOCRv2}" \
        "$ROOT/MonkeyOCRv2"
    fi
    git -C "$ROOT/MonkeyOCRv2" submodule update --init --recursive --depth 1
    [ -f "$REPO/ms-swift/README.md" ] || printf '# ms-swift (bundled with MonkeyOCRv2)\n' >"$REPO/ms-swift/README.md"
    mkdir -p "$REPO/ms-swift/swift/llm/dataset/data"
    [ -f "$REPO/ms-swift/swift/llm/dataset/data/dataset_info.json" ] || printf '[]\n' >"$REPO/ms-swift/swift/llm/dataset/data/dataset_info.json"
    ( cd "$REPO" && python -m pip install -e ./ms-swift )
    python -m pip install "transformers==4.57.1" "deepspeed==0.17.1"
    install_common
    # Compatibilità: il preflight storico cerca il marcatore globale.
    date -u +"%Y-%m-%dT%H:%M:%SZ" >"$HOME/tabularium-train/.train_ready"
    ;;
  qwen)
    REPO="$ROOT"
    # ms-swift 4.6 + transformers 5: la ricetta Qwen3-VL è stata validata su
    # questa combinazione (4.7-dev si comporta allo stesso modo).
    python -m pip install "ms-swift==4.6.0" "transformers==5.20.2"
    install_common
    # La ricetta Qwen dichiara `deepspeed zero2`: senza pacchetto `swift sft`
    # muore prima di leggere il dataset.
    python -m pip install "deepspeed==0.17.1"
    ;;
  legacy)
    # Unlimited-OCR / DeepSeek-OCR-2: il remote code usa API rimosse in
    # transformers 5; 4.46.3 è l'ultima che importa entrambi.
    REPO="$ROOT"
    python -m pip install "ms-swift==4.6.0" "transformers==4.46.3"
    install_common
    ;;
  uocr2)
    # Unlimited-OCR-2: ms-swift supports `unlimited-ocr` only on main (d52d0c4);
    # the pins are those of its `examples/models/unlimited_ocr2/train.sh`.
    REPO="$ROOT"
    python -m pip install \
      "ms-swift @ git+https://github.com/modelscope/ms-swift@d52d0c45000b875683e23dcf85d9a6e2ef346e52" \
      "transformers==4.52.1" easydict addict
    install_common
    ;;
  dots)
    REPO="$ROOT"
    python -m pip install "ms-swift==4.6.0" "transformers==4.57.1"
    install_common
    log "installo flash-attn (build lunga: 10-30 min)"
    MAX_JOBS="${MAX_JOBS:-8}" python -m pip install flash-attn==2.8.3 --no-build-isolation \
      || log "flash-attn non installato: dots userà attn_impl=eager"
    ;;
  glm)
    REPO="$ROOT/LLaMA-Factory"
    if [ ! -d "$REPO/.git" ]; then
      log "clono LLaMA-Factory"
      git clone --depth 1 https://github.com/hiyouga/LLaMA-Factory.git "$REPO"
    fi
    python -m pip install -e "$REPO"
    python -m pip install -r "$REPO/requirements/metrics.txt" || true
    # `warmup_ratio` è stato rimosso in transformers 5.20: la ricetta GLM
    # ufficiale è ferma a 5.8.
    python -m pip install "transformers==5.8.0"
    ;;
  paddle)
    REPO="$ROOT/PaddleFormers"
    if [ ! -d "$REPO/.git" ]; then
      log "clono PaddleFormers"
      git clone --depth 1 https://github.com/PaddlePaddle/PaddleFormers.git "$REPO"
    fi
    # L'immagine ufficiale Paddle porta paddlepaddle-gpu e il compilatore CUDA.
    python -m pip install -e "$REPO"
    python -m pip install "triton>=3.1"
    ;;
  *)
    fail "profilo sconosciuto: $PROFILE"
    ;;
esac

log "verifico i comandi del toolchain"
case "$PROFILE" in
  monkey|qwen|legacy|uocr2|dots)
    python -c 'import swift' || fail "ms-swift non importabile"
    python -c 'import torch; assert torch.cuda.is_available()' || fail "torch non vede la GPU"
    ;;
  glm)
    command -v llamafactory-cli >/dev/null || fail "llamafactory-cli assente"
    python -c 'import torch; assert torch.cuda.is_available()' || fail "torch non vede la GPU"
    ;;
  paddle)
    command -v paddleformers-cli >/dev/null || fail "paddleformers-cli assente"
    python -c 'import paddle; assert paddle.device.is_compiled_with_cuda()' || fail "Paddle non vede la GPU"
    ;;
esac

PYTHON_PATH="$ROOT/venv/bin/python"
python - "$ROOT" "$PROFILE" "$ADAPTER" "$PYTHON_PATH" "$REPO" <<'PY'
import json
import pathlib
import sys

root, profile, adapter, python, repo = sys.argv[1:]
path = pathlib.Path(root)
payload = {
    "profile": profile,
    "adapter_id": adapter,
    "python": python,
    "repo": repo,
    "ready_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
}
(path / "ready.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY
date -u +"%Y-%m-%dT%H:%M:%SZ" >"$ROOT/.train_ready"
log "READY $ROOT"
printf '__TRAIN_ENV_READY__ %s\n' "$ROOT"
