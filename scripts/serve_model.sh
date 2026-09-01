#!/usr/bin/env bash
# Avvia il server di inferenza vLLM con il checkpoint MonkeyOCRv2-Parsing scaricato.
# Uso:  ./scripts/serve_model.sh [model] [port] [draft]
#       model = checkpoint (default: model_weight/MonkeyOCRv2-B-Parsing del repo)
#       port  = default 8888
#       draft = checkpoint DFlash; in alternativa la env
#               TABULARIUM_MONKEY_DFLASH_DRAFT. Il budget VRAM resta quello di
#               --gpu-memory-utilization: con il draft attivo vLLM ci alloca
#               dentro anche i pesi del draft, quindi la cache KV si riduce.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="${TABULARIUM_TRAIN_REPO:-/home/cappannonno/MonkeyOCRv2}"
ENV_DIR="${TABULARIUM_TRAIN_PYTHON:+$(dirname "$(dirname "$TABULARIUM_TRAIN_PYTHON")")}"
ENV_DIR="${ENV_DIR:-/home/cappannonno/anaconda3/envs/MonkeyOCRv2Parsing}"
MODEL="${1:-$REPO/model_weight/MonkeyOCRv2-B-Parsing}"
PORT="${2:-8888}"

if [ ! -d "$ENV_DIR" ] || [ ! -d "$MODEL" ]; then
  echo ">> env o checkpoint non trovati: ENV=$ENV_DIR MODEL=$MODEL"
  exit 1
fi

cd "$REPO/parsing"
export PATH="$ENV_DIR/bin:$PATH"
# gcc-13 compatibile CUDA + tol a flashinfer di bloccare il JIT.
# NOTA (v. docs/LOCAL_INFERENCE_GUIDE.md §2): queste tre variabili sono
# workaround EMPIRICI di compilatore/CUDA JIT su questa installazione, non
# requisiti documentati dal repo ufficiale MonkeyOCRv2 — il serving via vLLM
# non richiede di per sé una compilazione manuale di flash-attn. Su una
# macchina diversa potrebbero non servire, o servirne di diverse: se il
# server si avvia senza, non aggiungerle "per sicurezza".
export CC="${CC:-gcc-13}" CXX="${CXX:-g++-13}"
export NVCC_PREPEND_FLAGS="-allow-unsupported-compiler"
export MAX_JOBS=2

# DFlash (speculative decoding, fino a ~2x secondo il README ufficiale): attivo
# solo se il draft è già scaricato. `serve.py` aggiunge --speculative-config
# unicamente quando riceve -d, quindi senza draft il comando resta identico a
# prima. Richiede vLLM >= 0.25; con la 0.11 legacy serve.py esce con errore, per
# questo il draft va passato solo quando l'ambiente lo supporta (v. serve_manager).
DRAFT="${3:-${TABULARIUM_MONKEY_DFLASH_DRAFT:-}}"
DFLASH_ARGS=()
if [ -n "$DRAFT" ]; then
  if [ ! -d "$DRAFT" ]; then
    echo ">> DFlash richiesto ma draft non trovato ($DRAFT): avvio senza DFlash"
  elif ! "$ENV_DIR/bin/python" - <<'PYEOF'
import sys
from importlib.metadata import version
try:
    parts = tuple(int(x) for x in version("vllm").split(".")[:2])
except Exception:
    sys.exit(1)
sys.exit(0 if parts >= (0, 12) else 1)
PYEOF
  then
    # serve.py fa `parser.error` e non parte affatto se riceve -d su vLLM < 0.12
    # (v. parsing/serve.py). Meglio servire senza accelerazione che non servire.
    echo ">> DFlash non supportato da questa vLLM (<0.12): avvio senza DFlash"
  else
    echo ">> DFlash attivo: draft $DRAFT"
    DFLASH_ARGS=(-d "$DRAFT")
  fi
fi

# NOTA: le versioni recenti di serve.py non accettano --generation-config; il
# file generation_config.json del checkpoint viene letto da vllm comunque.
echo ">> Servo $MODEL su :$PORT"
exec "$ENV_DIR/bin/python" serve.py -m "$MODEL" -p "$PORT" \
  "${DFLASH_ARGS[@]}" \
  --host 127.0.0.1 --gpu-memory-utilization 0.9 \
  --max-model-len 24576 --max-num-batched-tokens 24576 --max-num-seqs 8
