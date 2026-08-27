#!/usr/bin/env bash
# Avvia il server di inferenza vLLM con il checkpoint MonkeyOCRv2-Parsing scaricato.
# Uso:  ./scripts/serve_model.sh [port]  (default 8888)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="${LLOYDS_TRAIN_REPO:-/home/cappannonno/MonkeyOCRv2}"
ENV_DIR="${LLOYDS_TRAIN_PYTHON:+$(dirname "$(dirname "$LLOYDS_TRAIN_PYTHON")")}"
ENV_DIR="${ENV_DIR:-/home/cappannonno/anaconda3/envs/MonkeyOCRv2Parsing}"
MODEL="${1:-$REPO/model_weight/MonkeyOCRv2-B-Parsing}"
PORT="${2:-8888}"

if [ ! -d "$ENV_DIR" ] || [ ! -d "$MODEL" ]; then
  echo ">> env o checkpoint non trovati: ENV=$ENV_DIR MODEL=$MODEL"
  exit 1
fi

cd "$REPO/parsing"
export PATH="$ENV_DIR/bin:$PATH"
# gcc-13 compatibile CUDA + tol a flashinfer di bloccare il JIT
export CC="${CC:-gcc-13}" CXX="${CXX:-g++-13}"
export NVCC_PREPEND_FLAGS="-allow-unsupported-compiler"
export MAX_JOBS=2

echo ">> Servo $MODEL su :$PORT"
exec "$ENV_DIR/bin/python" serve.py -m "$MODEL" -p "$PORT" \
  --host 127.0.0.1 --gpu-memory-utilization 0.9 \
  --max-model-len 24576 --max-num-batched-tokens 24576 --max-num-seqs 8 \
  --generation-config vllm
