#!/usr/bin/env bash
# ==============================================================================
# Tabularium — Cloud GPU Inference Setup (Vast.ai / RunPod / Cloud VM)
# ==============================================================================
# Questo script si esegue sull'istanza cloud (es. Vast.ai con PyTorch/CUDA) per:
# 1. Configurare l'ambiente Python/CUDA e le dipendenze vLLM
# 2. Scaricare il modello MonkeyOCRv2-B-Parsing da HuggingFace o ModelScope
# 3. Avviare il server vLLM OpenAI-compatibile su porta configurabile
#
# Uso sul server cloud:
#   bash setup_cloud_vllm.sh [--port 8888] [--model zenosai/MonkeyOCRv2-B-Parsing] [--api-key SECRET]
# ==============================================================================
set -euo pipefail

PORT="${PORT:-8888}"
HOST="${HOST:-0.0.0.0}"
MODEL_NAME="${MODEL_NAME:-zenosai/MonkeyOCRv2-B-Parsing}"
MODEL_DIR="${MODEL_DIR:-$HOME/MonkeyOCRv2/model_weight/MonkeyOCRv2-B-Parsing}"
GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.90}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-24576}"
API_KEY="${API_KEY:-}"
VLLM_VERSION="${VLLM_VERSION:-0.25.1}"
TRANSFORMERS_VERSION="${TRANSFORMERS_VERSION:-4.51.3}"
MONKEYOCR_REF="${MONKEYOCR_REF:-}"
MIN_DISK_GB="${MIN_DISK_GB:-10}"

: "${MONKEYOCR_REF:?MONKEYOCR_REF obbligatorio: usare un commit SHA o un tag verificato}"

# Parse flags
while [[ $# -gt 0 ]]; do
  case $1 in
    -p|--port)
      PORT="$2"; shift 2 ;;
    -h|--host)
      HOST="$2"; shift 2 ;;
    -m|--model)
      MODEL_NAME="$2"; shift 2 ;;
    --model-dir)
      MODEL_DIR="$2"; shift 2 ;;
    --api-key)
      API_KEY="$2"; shift 2 ;;
    --gpu-mem)
      GPU_MEM_UTIL="$2"; shift 2 ;;
    --max-len)
      MAX_MODEL_LEN="$2"; shift 2 ;;
    *)
      echo "Argomento sconosciuto: $1" >&2; exit 1 ;;
  esac
done

echo "=========================================================="
echo ">> [Tabularium Cloud Setup] Avvio configurazione vLLM GPU"
echo ">> Host: $HOST | Port: $PORT | GPU Mem Util: $GPU_MEM_UTIL"
echo "=========================================================="

# 1. Check GPU
if command -v nvidia-smi &>/dev/null; then
  echo ">> GPU Rilevata:"
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
  GPU_QUERY=$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader)
else
  echo "!! nvidia-smi non trovato: serve una GPU NVIDIA funzionante." >&2
  exit 1
fi

DISK_AVAILABLE_GB=$(df -Pk "$HOME" | awk 'NR==2 {printf "%d", $4 / 1024 / 1024}')
if [ "$DISK_AVAILABLE_GB" -lt "$MIN_DISK_GB" ]; then
  echo "!! Spazio insufficiente: ${DISK_AVAILABLE_GB} GB liberi, servono almeno ${MIN_DISK_GB} GB." >&2
  exit 1
fi

# 2. Install base system dependencies
echo ">> Installazione dipendenze di sistema..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git git-lfs curl wget build-essential gcc g++ > /dev/null 2>&1

# 3. Clone MonkeyOCRv2 repo if not present
WORK_DIR="$HOME/MonkeyOCRv2"
if [ ! -d "$WORK_DIR" ]; then
  echo ">> Clonazione repository MonkeyOCRv2 in $WORK_DIR..."
  git clone https://github.com/Yuliang-Liu/MonkeyOCRv2.git "$WORK_DIR"
fi
git -C "$WORK_DIR" fetch --depth 1 origin "$MONKEYOCR_REF"
git -C "$WORK_DIR" checkout --detach FETCH_HEAD
cd "$WORK_DIR/parsing"

# 4. Install Python dependencies
echo ">> Installazione dipendenze Python (vLLM, PyTorch, Transformers)..."
python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet \
  "vllm==${VLLM_VERSION}" \
  "transformers==${TRANSFORMERS_VERSION}" \
  "accelerate==1.6.0" \
  "huggingface_hub==0.30.2" \
  "pillow==11.2.1" \
  "pydantic==2.11.3" \
  "requests==2.32.3" \
  "timm==1.0.15" \
  "einops==0.8.1"

# 5. Download model weights
if [ ! -d "$MODEL_DIR" ] || [ ! -f "$MODEL_DIR/config.json" ]; then
  echo ">> Download pesi modello $MODEL_NAME in $MODEL_DIR..."
  mkdir -p "$(dirname "$MODEL_DIR")"
  MODEL_NAME="$MODEL_NAME" MODEL_DIR="$MODEL_DIR" python3 - <<'PY'
from huggingface_hub import snapshot_download
import os

model_id = os.environ["MODEL_NAME"]
target_dir = os.environ["MODEL_DIR"]
print(f"Scaricamento {model_id} da HuggingFace...")
snapshot_download(repo_id=model_id, local_dir=target_dir)
print("Download completato!")
PY
fi

MODEL_NAME="$MODEL_NAME" MODEL_DIR="$MODEL_DIR" VLLM_VERSION="$VLLM_VERSION" \
MONKEYOCR_REF="$MONKEYOCR_REF" GPU_QUERY="$GPU_QUERY" DISK_AVAILABLE_GB="$DISK_AVAILABLE_GB" \
python3 - <<'PY'
import json, os, subprocess
from pathlib import Path

manifest = {
    "model": os.environ["MODEL_NAME"],
    "model_dir": os.environ["MODEL_DIR"],
    "vllm": os.environ["VLLM_VERSION"],
    "monkeyocr_ref": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "requested_ref": os.environ["MONKEYOCR_REF"],
    "gpu": os.environ["GPU_QUERY"],
    "disk_available_gb": int(os.environ["DISK_AVAILABLE_GB"]),
}
Path("cloud-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
PY

# 6. Prepare serving flags
SERVE_ARGS=(
  -m "$MODEL_DIR"
  -p "$PORT"
  --host "$HOST"
  --gpu-memory-utilization "$GPU_MEM_UTIL"
  --max-model-len "$MAX_MODEL_LEN"
  --max-num-batched-tokens "$MAX_MODEL_LEN"
  --max-num-seqs 8
)

if [ -n "$API_KEY" ]; then
  SERVE_ARGS+=(--api-key "$API_KEY")
  echo ">> Autenticazione abilitata con API Key segreta."
fi

echo "=========================================================="
echo ">> [Tabularium Cloud Server] Avvio vLLM su $HOST:$PORT..."
echo ">> Endpoint: http://$HOST:$PORT/v1"
echo "=========================================================="

exec python3 serve.py "${SERVE_ARGS[@]}"
