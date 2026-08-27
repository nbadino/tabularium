#!/usr/bin/env bash
# ==============================================================================
# Lloyds Lab — Cloud GPU Inference Setup (Vast.ai / RunPod / Cloud VM)
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
echo ">> [Lloyds Lab Cloud Setup] Avvio configurazione vLLM GPU"
echo ">> Host: $HOST | Port: $PORT | GPU Mem Util: $GPU_MEM_UTIL"
echo "=========================================================="

# 1. Check GPU
if command -v nvidia-smi &>/dev/null; then
  echo ">> GPU Rilevata:"
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
else
  echo "!! ATTENZIONE: nvidia-smi non trovato! Assicurati di essere su un'istanza con GPU NVIDIA." >&2
fi

# 2. Install base system dependencies
echo ">> Installazione dipendenze di sistema..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq git git-lfs curl wget build-essential gcc g++ > /dev/null 2>&1 || true

# 3. Clone MonkeyOCRv2 repo if not present
WORK_DIR="$HOME/MonkeyOCRv2"
if [ ! -d "$WORK_DIR" ]; then
  echo ">> Clonazione repository MonkeyOCRv2 in $WORK_DIR..."
  git clone https://github.com/Yuliang-Liu/MonkeyOCRv2.git "$WORK_DIR"
fi
cd "$WORK_DIR/parsing"

# 4. Install Python dependencies
echo ">> Installazione dipendenze Python (vLLM, PyTorch, Transformers)..."
python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet \
  "vllm>=0.6.0" \
  "transformers>=4.45.0" \
  "accelerate" \
  "huggingface_hub" \
  "pillow" \
  "pydantic" \
  "requests" \
  "timm" \
  "einops" \
  "flashinfer" || true

# 5. Download model weights
if [ ! -d "$MODEL_DIR" ] || [ ! -f "$MODEL_DIR/config.json" ]; then
  echo ">> Download pesi modello $MODEL_NAME in $MODEL_DIR..."
  mkdir -p "$(dirname "$MODEL_DIR")"
  python3 -c "
from huggingface_hub import snapshot_download
import os

model_id = '$MODEL_NAME'
target_dir = '$MODEL_DIR'
print(f'Scaricamento {model_id} da HuggingFace...')
try:
    snapshot_download(repo_id=model_id, local_dir=target_dir, local_dir_use_symlinks=False)
    print('Download completato!')
except Exception as e:
    print(f'HuggingFace download failed: {e}. Tentativo con ModelScope...')
    from modelscope import snapshot_download as ms_download
    ms_download(model_id, local_dir=target_dir)
"
fi

# 6. Prepare serving flags
SERVE_ARGS=(
  -m "$MODEL_DIR"
  -p "$PORT"
  --host "$HOST"
  --gpu-memory-utilization "$GPU_MEM_UTIL"
  --max-model-len "$MAX_MODEL_LEN"
  --max-num-batched-tokens "$MAX_MODEL_LEN"
  --max-num-seqs 8
  --generation-config vllm
)

if [ -n "$API_KEY" ]; then
  SERVE_ARGS+=(--api-key "$API_KEY")
  echo ">> Autenticazione abilitata con API Key segreta."
fi

echo "=========================================================="
echo ">> [Lloyds Lab Cloud Server] Avvio vLLM su $HOST:$PORT..."
echo ">> Endpoint: http://$HOST:$PORT/v1"
echo "=========================================================="

exec python3 serve.py "${SERVE_ARGS[@]}"
