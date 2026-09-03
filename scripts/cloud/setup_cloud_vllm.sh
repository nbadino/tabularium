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
#   bash setup_cloud_vllm.sh [--port 8888] [--model zenosai/MonkeyOCRv2-B-Parsing] [--ref COMMIT_OR_TAG] [--api-key SECRET]
# ==============================================================================
set -euo pipefail

PORT="${PORT:-8888}"
HOST="${HOST:-0.0.0.0}"
MODEL_NAME="${MODEL_NAME:-zenosai/MonkeyOCRv2-B-Parsing}"
MODEL_DIR="${MODEL_DIR:-}"
GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.90}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-24576}"
API_KEY="${API_KEY:-${TABULARIUM_SERVER_API_KEY:-}}"
# Versioni allineate all'ambiente di serving locale verificato
# (`data/vllm-runtime`): vLLM 0.25.1 con transformers 4.51.3 non risolve più —
# pip le dichiara in conflitto — e la coppia qui sotto è quella che serve
# davvero MonkeyOCRv2 su questa installazione.
VLLM_VERSION="${VLLM_VERSION:-0.28.0}"
TRANSFORMERS_VERSION="${TRANSFORMERS_VERSION:-5.16.1}"
# PyTorch va installato *prima* di vLLM e dall'indice CUDA 13.0: il wheel
# PyPI di default non conosce le GPU sm_120 (Blackwell) e vLLM muore con
# "SM 12.x requires CUDA >= 12.9" seguito da "FlashInfer requires GPUs with
# sm75 or higher" — la capability non viene proprio letta. Le versioni sono
# quelle pinnate da vLLM 0.28.0, quindi il suo install le lascia intatte.
TORCH_VERSION="${TORCH_VERSION:-2.13.0}"
TORCHVISION_VERSION="${TORCHVISION_VERSION:-0.28.0}"
TORCHAUDIO_VERSION="${TORCHAUDIO_VERSION:-2.11.0}"
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cu130}"
CUDA_TOOLKIT_VERSION="${CUDA_TOOLKIT_VERSION:-13.0}"
CUDA_TOOLKIT_PKG="${CUDA_TOOLKIT_PKG:-13-0}"
MONKEYOCR_REF="${MONKEYOCR_REF:-}"
MIN_DISK_GB="${MIN_DISK_GB:-10}"
MIN_COMPUTE_CAP="${MIN_COMPUTE_CAP:-8.0}"
# Ambiente Python isolato: le immagini recenti (Ubuntu 24.04) hanno pip gestito
# dalla distro, che rifiuta sia l'auto-aggiornamento sia gli install di sistema
# (PEP 668). Un venv rende il setup indipendente dall'immagine scelta.
VENV_DIR="${VENV_DIR:-$HOME/tabularium-venv}"

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
    --ref|--monkeyocr-ref)
      MONKEYOCR_REF="$2"; shift 2 ;;
    *)
      echo "Argomento sconosciuto: $1" >&2; exit 1 ;;
  esac
done

: "${MONKEYOCR_REF:?MONKEYOCR_REF obbligatorio: usare un commit SHA o un tag verificato}"

# Ricetta di serving generata dal backend (`serve_recipes.py`): versione di
# vLLM, dipendenze extra e flag ufficiali del modello. Senza, si resta sul
# percorso storico MonkeyOCRv2.
RECIPE_B64="${RECIPE_B64:-}"
RECIPE_RUNTIME="monkeyocr"
RECIPE_ADAPTER="monkeyocrv2-parsing"
RECIPE_PIP_EXTRA=""
RECIPE_INSTALL_VLLM="1"
SERVE_ARGV=()
if [ -n "$RECIPE_B64" ]; then
  RECIPE_JSON=$(printf '%s' "$RECIPE_B64" | base64 -d)
  read -r RECIPE_RUNTIME MODEL_NAME MODEL_DIR VLLM_VERSION <<EOF
$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; r=json.load(sys.stdin); print(r['runtime'], r['hf_repo'], r['model_dir'], r['vllm_version'] or '$VLLM_VERSION')")
EOF
  RECIPE_PIP_EXTRA=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(' '.join(json.load(sys.stdin)['pip_extra']))")
  RECIPE_INSTALL_VLLM=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print('1' if json.load(sys.stdin).get('install_vllm', True) else '0')")
  RECIPE_ADAPTER=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['adapter_id'])")
  # Un ambiente per modello: le versioni di vLLM delle ricette non convivono
  # nello stesso site-packages, ma convivono benissimo sullo stesso disco.
  VENV_DIR=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('venv_dir') or '$VENV_DIR')")
  mapfile -t SERVE_ARGV < <(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; [print(a) for a in json.load(sys.stdin)['argv']]")
  echo ">> Ricetta ufficiale: $(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; r=json.load(sys.stdin); print(r['adapter_id'], '· vLLM', r['vllm_version'], '·', r['runtime'])")"
fi

# La cartella dei pesi segue il modello scelto. Con un percorso fisso, cambiare
# checkpoint avrebbe scaricato altrove — o peggio, riusato i pesi già presenti
# servendo un modello diverso da quello richiesto.
if [ -z "$MODEL_DIR" ]; then
  MODEL_DIR="$HOME/MonkeyOCRv2/model_weight/$(basename "$MODEL_NAME")"
fi

echo "=========================================================="
echo ">> [Tabularium Cloud Setup] Avvio configurazione vLLM GPU"
echo ">> Host: $HOST | Port: $PORT | GPU Mem Util: $GPU_MEM_UTIL"
echo "=========================================================="

# 1. Check GPU
if command -v nvidia-smi &>/dev/null; then
  echo ">> GPU Rilevata:"
  nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv,noheader
  GPU_QUERY=$(nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv,noheader)
  COMPUTE_CAP=$(printf '%s\n' "$GPU_QUERY" | awk -F',' 'NR==1 {gsub(/[[:space:]]/, "", $4); print $4}')
  if ! awk -v actual="$COMPUTE_CAP" -v minimum="$MIN_COMPUTE_CAP" 'BEGIN { exit !(actual + 0 >= minimum + 0) }'; then
    echo "!! GPU con compute capability ${COMPUTE_CAP:-sconosciuta}: servono almeno ${MIN_COMPUTE_CAP} per la recipe bf16." >&2
    exit 1
  fi
  echo ">> Compute capability verificata: $COMPUTE_CAP (minima $MIN_COMPUTE_CAP)"
else
  echo "!! nvidia-smi non trovato: serve una GPU NVIDIA funzionante." >&2
  exit 1
fi

disk_free_gb() { df -Pk "$HOME" | awk 'NR==2 {printf "%d", $4 / 1024 / 1024}'; }
DISK_AVAILABLE_GB=$(disk_free_gb)
if [ "$DISK_AVAILABLE_GB" -lt "$MIN_DISK_GB" ]; then
  # Prima di arrendersi si buttano le cache: sono ricostruibili scaricando di
  # nuovo, mentre ambienti e pesi no. Ogni modello costa ~8 GB di ambiente più
  # i suoi pesi, quindi su un disco piccolo la cache è la prima a dover cedere.
  echo ">> Spazio sotto la soglia (${DISK_AVAILABLE_GB} GB): svuoto le cache ricostruibili..."
  python3 -m pip cache purge > /dev/null 2>&1 || true
  rm -rf "$HOME/.cache/vllm" "$HOME/.cache/flashinfer" "$HOME/.cache/pip" 2>/dev/null || true
  DISK_AVAILABLE_GB=$(disk_free_gb)
  echo ">> Dopo la pulizia: ${DISK_AVAILABLE_GB} GB liberi."
fi
if [ "$DISK_AVAILABLE_GB" -lt "$MIN_DISK_GB" ]; then
  echo "!! Spazio insufficiente: ${DISK_AVAILABLE_GB} GB liberi, servono almeno ${MIN_DISK_GB} GB." >&2
  echo "!! Occupazione maggiore (ogni modello preparato costa ~8 GB di ambiente più i pesi):" >&2
  du -sh "$HOME"/tabularium/envs/* "$HOME"/models/* 2>/dev/null | sort -hr | head -6 >&2
  echo "!! Rimuovi un modello che non usi, oppure noleggia con più disco." >&2
  exit 1
fi

# 2. Install base system dependencies
echo ">> Installazione dipendenze di sistema..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git git-lfs curl wget build-essential gcc g++ python3-venv > /dev/null 2>&1

# Toolkit CUDA adeguato alla GPU. FlashInfer compila i kernel via JIT con nvcc:
# su Blackwell (sm_120) un nvcc < 12.9 non riconosce la capability, la legge
# come sconosciuta e vLLM muore con "FlashInfer requires GPUs with sm75 or
# higher" — messaggio che indica il contrario del problema reale.
NVCC_VERSION=$(nvcc --version 2>/dev/null | grep -oE 'release [0-9]+\.[0-9]+' | awk '{print $2}')
NVCC_VERSION="${NVCC_VERSION:-0}"
if awk -v cap="$COMPUTE_CAP" -v nv="$NVCC_VERSION" 'BEGIN { exit !(cap + 0 >= 12.0 && nv + 0 < 12.9) }'; then
  echo ">> GPU sm_${COMPUTE_CAP} con nvcc ${NVCC_VERSION}: installo il toolkit CUDA ${CUDA_TOOLKIT_VERSION}..."
  # Serve il toolkit completo, non il solo nvcc: FlashInfer compila contro
  # curand/cublas e con i soli pacchetti minimi si ferma su "curand.h: No such
  # file or directory".
  if apt-get install -y -qq "cuda-toolkit-${CUDA_TOOLKIT_PKG}" > /dev/null 2>&1; then
    export CUDA_HOME="/usr/local/cuda-${CUDA_TOOLKIT_VERSION}"
    export PATH="$CUDA_HOME/bin:$PATH"
    # Molti consumatori (FlashInfer incluso) risolvono `/usr/local/cuda` senza
    # guardare CUDA_HOME: va fatto puntare al toolkit appena installato.
    ln -sfn "$CUDA_HOME" /usr/local/cuda
    echo ">> Toolkit attivo: $("$CUDA_HOME/bin/nvcc" --version | tail -1)"
  else
    echo "!! Toolkit CUDA ${CUDA_TOOLKIT_VERSION} non installabile: il server potrebbe non partire su questa GPU." >&2
  fi
fi

# 3. Clone MonkeyOCRv2 repo if not present (solo per il wrapper ufficiale)
WORK_DIR="$HOME/MonkeyOCRv2"
if [ "$RECIPE_RUNTIME" = "monkeyocr" ] && [ ! -d "$WORK_DIR" ]; then
  echo ">> Clonazione repository MonkeyOCRv2 in $WORK_DIR..."
  git clone https://github.com/Yuliang-Liu/MonkeyOCRv2.git "$WORK_DIR"
fi
if [ "$RECIPE_RUNTIME" = "monkeyocr" ]; then
  git -C "$WORK_DIR" fetch --depth 1 origin "$MONKEYOCR_REF"
  git -C "$WORK_DIR" checkout --detach FETCH_HEAD
  cd "$WORK_DIR/parsing"
fi

# 4. Install Python dependencies
if [ "$RECIPE_INSTALL_VLLM" = "0" ]; then
  # Immagine dedicata (es. Unlimited-OCR): vLLM e la sua architettura sono già
  # dentro. Un venv isolato li nasconderebbe e una wheel pip li sostituirebbe
  # con una build che quel modello non ha.
  PY_BIN="$(command -v python3)"
  echo ">> vLLM fornito dall'immagine del container: nessuna installazione."
  "$PY_BIN" -m pip install --quiet --upgrade "huggingface_hub" > /dev/null 2>&1 || true
else
if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo ">> Creazione ambiente Python dedicato in $VENV_DIR..."
  mkdir -p "$(dirname "$VENV_DIR")"
  python3 -m venv "$VENV_DIR"
else
  echo ">> Ambiente già presente in $VENV_DIR: riuso."
fi
PY_BIN="$VENV_DIR/bin/python"
echo ">> Installazione dipendenze Python (vLLM, PyTorch, Transformers)..."
"$PY_BIN" -m pip install --quiet --upgrade pip setuptools wheel
"$PY_BIN" -m pip install --quiet \
  "vllm==${VLLM_VERSION}" \
  "transformers==${TRANSFORMERS_VERSION}" \
  "huggingface_hub==1.29.0" \
  "pillow==12.3.0" \
  "pydantic==2.13.5" \
  "timm==1.0.29" \
  "einops==0.8.2"

fi

# La build CUDA di PyTorch deve conoscere la GPU: il wheel PyPI di default non
# vede le sm_120 (Blackwell) e vLLM muore su "FlashInfer requires sm75 or
# higher". Si sostituisce la sola build, mantenendo *le versioni che la ricetta
# di vLLM ha appena risolto* — imporne altre prima dell'install le contraddice
# (vLLM 0.21 vuole torchvision 0.26, non 0.28).
if [ "$RECIPE_INSTALL_VLLM" = "1" ]; then
  TORCH_CUDA_MAJOR=$("$PY_BIN" -c "import torch; print((torch.version.cuda or '0').split('.')[0])" 2>/dev/null || echo 0)
  if awk -v cap="$COMPUTE_CAP" -v cuda="$TORCH_CUDA_MAJOR" 'BEGIN { exit !(cap + 0 >= 12.0 && cuda + 0 < 13) }'; then
    TORCH_PINNED=$("$PY_BIN" -c "import torch; print(torch.__version__.split('+')[0])")
    TORCH_TRIO=("torch==${TORCH_PINNED}")
    for extra in torchvision torchaudio; do
      version=$("$PY_BIN" -c "import ${extra}; print(${extra}.__version__.split('+')[0])" 2>/dev/null || true)
      [ -n "$version" ] && TORCH_TRIO+=("${extra}==${version}")
    done
    echo ">> PyTorch ${TORCH_PINNED} ricompilato per CUDA 13 (sm_120): ${TORCH_TRIO[*]}"
    # Un pacchetto alla volta: se torchaudio non esiste sull'indice cu130 (l'
    # indice parte da 2.9.0), torch — l'unico davvero critico per sm_120 — deve
    # comunque essere ricompilato. Un install unico fallirebbe in blocco.
    "$PY_BIN" -m pip install --quiet --no-deps --force-reinstall \
      "torch==${TORCH_PINNED}" --index-url "$TORCH_INDEX" || \
      echo "!! Build CUDA 13 non disponibile per torch ${TORCH_PINNED}: il server potrebbe non partire su questa GPU." >&2
    for extra in torchvision torchaudio; do
      version=$("$PY_BIN" -c "import ${extra}; print(${extra}.__version__.split('+')[0])" 2>/dev/null || true)
      [ -n "$version" ] || continue
      "$PY_BIN" -m pip install --quiet --no-deps --force-reinstall \
        "${extra}==${version}" --index-url "$TORCH_INDEX" > /dev/null 2>&1 || \
        echo ">> ${extra} ${version} non disponibile per CUDA 13: mantengo la build installata."
    done
  fi
fi

# Dipendenze richieste dalla ricetta del modello (es. il logits processor di
# MinerU, che `--logits-processors` risolve a runtime).
if [ -n "$RECIPE_PIP_EXTRA" ]; then
  echo ">> Dipendenze della ricetta: $RECIPE_PIP_EXTRA"
  # shellcheck disable=SC2086
  "$PY_BIN" -m pip install --quiet $RECIPE_PIP_EXTRA
fi

# 5. Download model weights
if [ ! -d "$MODEL_DIR" ] || [ ! -f "$MODEL_DIR/config.json" ]; then
  echo ">> Download pesi modello $MODEL_NAME in $MODEL_DIR..."
  mkdir -p "$(dirname "$MODEL_DIR")"
  MODEL_NAME="$MODEL_NAME" MODEL_DIR="$MODEL_DIR" "$PY_BIN" - <<'PY'
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
TORCH_VERSION="$TORCH_VERSION" TORCH_INDEX="$TORCH_INDEX" \
RECIPE_RUNTIME="$RECIPE_RUNTIME" RECIPE_ADAPTER="${RECIPE_ADAPTER:-monkeyocrv2-parsing}" \
TRANSFORMERS_VERSION="$TRANSFORMERS_VERSION" MONKEYOCR_REF="$MONKEYOCR_REF" \
GPU_QUERY="$GPU_QUERY" COMPUTE_CAP="$COMPUTE_CAP" DISK_AVAILABLE_GB="$DISK_AVAILABLE_GB" \
GPU_MEM_UTIL="$GPU_MEM_UTIL" MAX_MODEL_LEN="$MAX_MODEL_LEN" \
"$PY_BIN" - <<'PY'
import json, os, platform, subprocess, sys
from pathlib import Path

manifest = {
    "model": os.environ["MODEL_NAME"],
    "model_dir": os.environ["MODEL_DIR"],
    "vllm": os.environ["VLLM_VERSION"],
    "torch": os.environ.get("TORCH_VERSION", ""),
    "torch_index": os.environ.get("TORCH_INDEX", ""),
    "transformers": os.environ["TRANSFORMERS_VERSION"],
    # Il commit del runner esiste solo quando si serve col wrapper ufficiale:
    # gli altri modelli non hanno un checkout da interrogare, e `git rev-parse`
    # fuori da un repo esce 128 e con `set -e` porta giù tutto il setup.
    "monkeyocr_ref": (
        subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False,
        ).stdout.strip()
        if os.environ.get("RECIPE_RUNTIME", "monkeyocr") == "monkeyocr"
        else ""
    ),
    "requested_ref": os.environ["MONKEYOCR_REF"],
    "adapter_id": os.environ.get("RECIPE_ADAPTER", ""),
    "runtime": os.environ.get("RECIPE_RUNTIME", ""),
    "gpu": os.environ["GPU_QUERY"],
    "compute_capability": os.environ.get("COMPUTE_CAP", ""),
    "dtype": "bfloat16",
    "gpu_memory_utilization": float(os.environ["GPU_MEM_UTIL"]),
    "max_model_len": int(os.environ["MAX_MODEL_LEN"]),
    "python": platform.python_version(),
    "recipe": "tabularium-vast-2",
    "disk_available_gb": int(os.environ["DISK_AVAILABLE_GB"]),
}
target = Path(os.environ.get("MODEL_DIR", ".")).parent / "cloud-manifest.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f">> Manifest scritto in {target}")
PY

# 6. Prepare serving flags
if [ ${#SERVE_ARGV[@]} -eq 0 ]; then
  # Percorso storico senza ricetta: wrapper MonkeyOCRv2 con i flag verificati.
  SERVE_ARGV=(
    serve.py
    --model-path "$MODEL_DIR"
    --host "$HOST"
    --port "$PORT"
    --gpu-memory-utilization "$GPU_MEM_UTIL"
    --max-model-len "$MAX_MODEL_LEN"
    --max-num-batched-tokens "$MAX_MODEL_LEN"
    --max-num-seqs 8
  )
  if [ -n "$API_KEY" ]; then
    SERVE_ARGV+=(--api-key "$API_KEY")
    echo ">> Autenticazione abilitata con API Key segreta."
  fi
fi

# Un solo server per volta sulla porta: cambiare modello significa sostituire
# quello attivo, non affiancarlo. I pesi e l'ambiente del precedente restano
# sul disco, quindi tornare indietro è questione di secondi.
if pgrep -f "[s]erve[.]py|[v]llm.entrypoints" > /dev/null 2>&1; then
  echo ">> Fermo il server attualmente in ascolto..."
  pkill -f "[s]erve[.]py|[v]llm.entrypoints" || true
  sleep 5
fi

# La cache di compilazione appartiene alla coppia torch/CUDA che l'ha prodotta:
# riusarla dopo un cambio di ambiente produce errori di cubin mancanti.
rm -rf "$HOME/.cache/vllm/torch_compile_cache" 2>/dev/null || true

echo "=========================================================="
echo ">> [Tabularium Cloud Server] Avvio vLLM su $HOST:$PORT..."
echo ">> Endpoint: http://$HOST:$PORT/v1"
echo "=========================================================="

exec "$PY_BIN" "${SERVE_ARGV[@]}"
