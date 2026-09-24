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
REMOTE_LOG_PATH="${REMOTE_LOG_PATH:-/var/log/tabularium_setup.log}"
REPLACE_RUNNING_SERVER=0
# La versione di Transformers resta non fissata globalmente: ogni release
# vLLM dichiara il proprio intervallo compatibile e i plugin (TeleOCR) possono
# imporre un pin più stretto nella propria ricetta.
VLLM_VERSION="${VLLM_VERSION:-0.28.0}"
TRANSFORMERS_VERSION="${TRANSFORMERS_VERSION:-}"
# PyTorch viene risolto dal vLLM della ricetta. CUDA 12.8 è il minimo
# supportato da Blackwell; non imporre CUDA 13 a versioni vLLM più vecchie,
# le cui versioni Torch pinnate non hanno wheel cu130.
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cu128}"
CUDA_TOOLKIT_VERSION="${CUDA_TOOLKIT_VERSION:-13.0}"
CUDA_TOOLKIT_PKG="${CUDA_TOOLKIT_PKG:-13-0}"
MONKEYOCR_REF="${MONKEYOCR_REF:-}"
MIN_DISK_GB_WAS_SET=0
if [ -n "${MIN_DISK_GB+x}" ]; then MIN_DISK_GB_WAS_SET=1; fi
MIN_DISK_GB_REQUESTED="${MIN_DISK_GB:-20}"
MIN_DISK_GB="$MIN_DISK_GB_REQUESTED"
MIN_COMPUTE_CAP="${MIN_COMPUTE_CAP:-7.5}"
# Il driver deve saper eseguire la CUDA con cui e' compilato il PyTorch che
# installiamo (indice cu128): un driver piu' vecchio fa fallire vLLM molto
# dopo, con "The NVIDIA driver on your system is too old".
MIN_CUDA_DRIVER="${MIN_CUDA_DRIVER:-12.9}"
# Ambiente Python isolato: le immagini recenti (Ubuntu 24.04) hanno pip gestito
# dalla distro, che rifiuta sia l'auto-aggiornamento sia gli install di sistema
# (PEP 668). Un venv rende il setup indipendente dall'immagine scelta.
VENV_DIR="${VENV_DIR:-$HOME/tabularium-venv}"
# Vast/shared hosts can have brief PyPI stalls; pip's short default timeout
# turned a transient slow wheel into a false "no matching distribution" error.
export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-120}"
export PIP_RETRIES="${PIP_RETRIES:-10}"

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
    --replace-running-server)
      REPLACE_RUNNING_SERVER=1; shift ;;
    --max-len)
      MAX_MODEL_LEN="$2"; shift 2 ;;
    --ref|--monkeyocr-ref)
      MONKEYOCR_REF="$2"; shift 2 ;;
    *)
      echo "Argomento sconosciuto: $1" >&2; exit 1 ;;
  esac
done

if ! [[ "$MIN_DISK_GB" =~ ^[0-9]+$ ]] || [ "$MIN_DISK_GB" -lt 1 ]; then
  echo "!! MIN_DISK_GB deve essere un intero positivo (ricevuto: $MIN_DISK_GB)." >&2
  exit 2
fi

# Ricetta di serving generata dal backend (`serve_recipes.py`): versione di
# vLLM, dipendenze extra e flag ufficiali del modello. Senza, si resta sul
# percorso storico MonkeyOCRv2.
RECIPE_B64="${RECIPE_B64:-}"
RECIPE_RUNTIME="monkeyocr"
RECIPE_ADAPTER="monkeyocrv2-parsing"
RECIPE_SERVED_MODEL_NAME="MonkeyOCRv2"
RECIPE_PIP_EXTRA=""
RECIPE_INSTALL_VLLM="1"
RECIPE_MIN_DISK_GB=20
RECIPE_MIN_VRAM_GB=10
RECIPE_MIN_RAM_GB=12
RECIPE_NATIVE_GATEWAY_B64=""
RECIPE_NATIVE_REMOTE_PORT=""
RECIPE_DRAFT_HF_REPO=""
RECIPE_DRAFT_MODEL_DIR=""
RECIPE_TELEOCR_SETTINGS="{}"
RECIPE_USER_DTYPE=""
RECIPE_MINERU_SETTINGS="{}"
RECIPE_CONFIG_SIGNATURE=""
SERVE_ARGV=()
if [ -n "$RECIPE_B64" ]; then
  RECIPE_JSON=$(printf '%s' "$RECIPE_B64" | base64 -d)
  read -r RECIPE_RUNTIME MODEL_NAME MODEL_DIR VLLM_VERSION <<EOF
$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; r=json.load(sys.stdin); print(r['runtime'], r['hf_repo'], r['model_dir'], r['vllm_version'] or '$VLLM_VERSION')")
EOF
  RECIPE_PIP_EXTRA=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(' '.join(json.load(sys.stdin)['pip_extra']))")
  RECIPE_INSTALL_VLLM=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print('1' if json.load(sys.stdin).get('install_vllm', True) else '0')")
  RECIPE_ADAPTER=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['adapter_id'])")
  RECIPE_SERVED_MODEL_NAME=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('served_model_name') or '')")
  RECIPE_MIN_DISK_GB=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('min_free_disk_gb', 20)))")
  RECIPE_MIN_DISK_GB_REUSE=$(printf '%s' "$RECIPE_B64" | base64 -d | python3 -c "import json,sys; r=json.load(sys.stdin); print(int(r.get('min_free_disk_gb_reuse',r.get('min_free_disk_gb',20))))")
  RECIPE_MIN_DISK_GB_CACHED=$(printf '%s' "$RECIPE_B64" | base64 -d | python3 -c "import json,sys; r=json.load(sys.stdin); print(int(r.get('min_free_disk_gb_cached',r.get('min_free_disk_gb_reuse',r.get('min_free_disk_gb',20)))))")
  RECIPE_MIN_VRAM_GB=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('min_free_vram_gb', 10)))")
  RECIPE_MIN_RAM_GB=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('min_free_ram_gb', 12)))")
  MIN_COMPUTE_CAP=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(float(json.load(sys.stdin).get('min_compute_capability', 7.5)))")
  MIN_CUDA_DRIVER=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(float(json.load(sys.stdin).get('min_cuda_driver', 12.8)))")
  RECIPE_NATIVE_GATEWAY_B64=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('native_gateway_b64') or '')")
  RECIPE_NATIVE_REMOTE_PORT=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('native_remote_port') or '')")
  RECIPE_DRAFT_HF_REPO=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('draft_hf_repo') or '')")
  RECIPE_DRAFT_MODEL_DIR=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('draft_model_dir') or '')")
  RECIPE_CONFIG_SIGNATURE=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('configuration_signature') or '')")
  RECIPE_TELEOCR_SETTINGS=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin).get('settings') or {},separators=(',',':'))) ")
  RECIPE_USER_DTYPE=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print((json.load(sys.stdin).get('serving_overrides') or {}).get('dtype') or '')")
  RECIPE_MINERU_SETTINGS=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin).get('settings') or {},separators=(',',':'))) ")
  RECIPE_TRANSFORMERS_VERSION=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('transformers_version') or '')")
  if [ -n "$RECIPE_TRANSFORMERS_VERSION" ]; then TRANSFORMERS_VERSION="$RECIPE_TRANSFORMERS_VERSION"; fi
  # Un ambiente per combinazione compatibile di framework/pin/extra: i modelli
  # senza extra e con la stessa vLLM riusano gli stessi site-packages.
  VENV_DIR=$(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('venv_dir') or '$VENV_DIR')")
  # A fresh environment needs room for framework wheels as well as model files.
  # A previously installed exact environment only needs the model headroom.
  if [ "$RECIPE_INSTALL_VLLM" = "0" ] || [ -x "$VENV_DIR/bin/python" ]; then
    if [ -f "$MODEL_DIR/config.json" ]; then
      RECIPE_MIN_DISK_GB="$RECIPE_MIN_DISK_GB_CACHED"
    else
      RECIPE_MIN_DISK_GB="$RECIPE_MIN_DISK_GB_REUSE"
    fi
  fi
  # An explicit operator threshold can make the recipe stricter, never looser.
  if [ "$MIN_DISK_GB_WAS_SET" -eq 1 ] && [ "$MIN_DISK_GB_REQUESTED" -gt "$RECIPE_MIN_DISK_GB" ]; then
    MIN_DISK_GB="$MIN_DISK_GB_REQUESTED"
  else
    MIN_DISK_GB="$RECIPE_MIN_DISK_GB"
  fi
  mapfile -t SERVE_ARGV < <(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; [print(a) for a in json.load(sys.stdin)['argv']]")
  echo ">> Ricetta ufficiale: $(printf '%s' "$RECIPE_JSON" | python3 -c "import json,sys; r=json.load(sys.stdin); print(r['adapter_id'], '· vLLM', r['vllm_version'], '·', r['runtime'])")"
fi

if [ "$RECIPE_RUNTIME" = "monkeyocr" ]; then
  : "${MONKEYOCR_REF:?MONKEYOCR_REF obbligatorio per MonkeyOCRv2: usare un commit SHA o un tag verificato}"
fi

# La cartella dei pesi segue il modello scelto. Con un percorso fisso, cambiare
# checkpoint avrebbe scaricato altrove — o peggio, riusato i pesi già presenti
# servendo un modello diverso da quello richiesto.
if [ -z "$MODEL_DIR" ]; then
  MODEL_DIR="$HOME/MonkeyOCRv2/model_weight/$(basename "$MODEL_NAME")"
fi

# Disk is the first resource check: the Vast host's physical SSD size is not
# the container quota. Nothing is cleaned automatically to make this pass.
DISK_PROBE="$MODEL_DIR"
while [ ! -e "$DISK_PROBE" ] && [ "$DISK_PROBE" != "/" ]; do DISK_PROBE=$(dirname "$DISK_PROBE"); done
DISK_AVAILABLE_GB=$(df -Pk "$DISK_PROBE" | awk 'NR==2 {printf "%d", $4 / 1024 / 1024}')
if [ -z "$DISK_AVAILABLE_GB" ] || [ "$DISK_AVAILABLE_GB" -lt "$MIN_DISK_GB" ]; then
  echo "!! Preflight disco fallito per ${RECIPE_ADAPTER}: ${DISK_AVAILABLE_GB:-sconosciuto} GB liberi, ne servono almeno ${MIN_DISK_GB} GB." >&2
  echo "!! Nessun pacchetto o peso è stato scaricato. Il disco visibile al container può essere più piccolo dell'SSD fisico dell'host." >&2
  echo "!! Modelli, runtime e cache già presenti non verranno cancellati automaticamente." >&2
  df -h "${DISK_PROBE:-$MODEL_DIR}" 2>&1 | tail -2 >&2 || true
  exit 2
fi
echo ">> Preflight disco: ${DISK_AVAILABLE_GB} GB liberi; budget ricetta ${MIN_DISK_GB} GB — OK."

# Basic host/toolchain checks are read-only and happen before apt/pip. This
# script intentionally targets the Debian/Ubuntu CUDA images used by Vast and
# RunPod; guessing at other package managers would leave half-configured hosts.
if [ ! -x "$(command -v python3 2>/dev/null || true)" ]; then
  echo "!! Preflight fallito: python3 non trovato. Scegli un'immagine cloud Ubuntu/Debian con Python 3." >&2
  exit 2
fi
PYTHON_VERSION=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
PYTHON_MAJOR=${PYTHON_VERSION%%.*}
PYTHON_MINOR=${PYTHON_VERSION#*.}
if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]; }; then
  echo "!! Preflight fallito: Python $PYTHON_VERSION; vLLM richiede Python 3.10 o superiore." >&2
  exit 2
fi
if [ "$RECIPE_ADAPTER" = "teleocr" ] && { [ "$PYTHON_MAJOR" -gt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -ge 13 ]; }; }; then
  echo "!! Preflight fallito: TeleOCR upstream richiede Python >=3.10,<3.13 (qui $PYTHON_VERSION)." >&2
  exit 2
fi
if [ ! -x "$(command -v apt-get 2>/dev/null || true)" ]; then
  echo "!! Preflight fallito: apt-get non trovato. Usa un'immagine Ubuntu/Debian supportata." >&2
  exit 2
fi
if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "!! Preflight fallito: nvidia-smi non trovato, GPU NVIDIA non visibile al container." >&2
  exit 2
fi
if [ "$RECIPE_INSTALL_VLLM" = "0" ] && ! command -v vllm >/dev/null 2>&1; then
  echo "!! Preflight fallito: la ricetta richiede un'immagine con vLLM già installato, ma il comando vllm non esiste." >&2
  exit 2
fi

# Test egress before large installs. Reaching HF's model API and PyPI is
# required for every pip based recipe; GitHub is needed for plugin extras.
NETWORK_HOSTS=(pypi.org huggingface.co)
if [[ "$RECIPE_PIP_EXTRA" == *"github.com"* ]]; then NETWORK_HOSTS+=(github.com); fi
for network_host in "${NETWORK_HOSTS[@]}"; do
  if ! NETWORK_HOST="$network_host" python3 - <<'PY'
import os, socket, ssl
host = os.environ["NETWORK_HOST"]
with socket.create_connection((host, 443), timeout=8) as sock:
    with ssl.create_default_context().wrap_socket(sock, server_hostname=host):
        pass
PY
  then
    echo "!! Preflight rete fallito: impossibile raggiungere https://${network_host}:443; nessun pacchetto o peso è stato scaricato." >&2
    exit 2
  fi
done

# Quando il backend ha verificato via /v1/models che sulla porta configurata
# gira già un altro modello, può autorizzare la sostituzione. Fermiamo solo il
# processo vLLM/serve.py che ascolta *questa porta*, prima di misurare la VRAM;
# non tocchiamo processi GPU estranei né usiamo pkill globale.
if [ "$REPLACE_RUNNING_SERVER" = "1" ]; then
  if ! command -v ss >/dev/null 2>&1; then
    echo "!! Sostituzione server impossibile: ss non è disponibile per identificare in sicurezza il listener della porta $PORT." >&2
    exit 2
  fi
  LISTENER_INFO=$(ss -H -ltnp "sport = :$PORT" 2>/dev/null || true)
  if [ -n "$LISTENER_INFO" ]; then
    SERVER_PIDS=$(printf '%s\n' "$LISTENER_INFO" | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true)
    if [ -z "$SERVER_PIDS" ]; then
      echo "!! Sostituzione server impossibile: il listener sulla porta $PORT non espone un PID verificabile; nessun processo è stato fermato." >&2
      exit 2
    fi
    for server_pid in $SERVER_PIDS; do
      if [ ! -r "/proc/$server_pid/cmdline" ]; then continue; fi
      SERVER_CMD=$(tr '\0' ' ' < "/proc/$server_pid/cmdline")
      if [[ "$SERVER_CMD" != *"serve.py"* && "$SERVER_CMD" != *"vllm.entrypoints"* ]]; then
        echo "!! Sostituzione server impossibile: PID $server_pid sulla porta $PORT non è un server vLLM riconosciuto; nessun processo è stato fermato." >&2
        exit 2
      fi
    done
    echo ">> Arresto controllato del server Tabularium sulla porta $PORT prima del preflight VRAM..."
    for server_pid in $SERVER_PIDS; do kill -TERM "$server_pid" 2>/dev/null || true; done
    for _ in $(seq 1 15); do
      alive=0
      for server_pid in $SERVER_PIDS; do kill -0 "$server_pid" 2>/dev/null && alive=1 || true; done
      [ "$alive" = "0" ] && break
      sleep 1
    done
    for server_pid in $SERVER_PIDS; do kill -KILL "$server_pid" 2>/dev/null || true; done
    sleep 2
  fi
fi

# Check free VRAM, not just the GPU's advertised total: another process may
# already occupy it. This prevents a costly pip/model install that cannot fit.
GPU_FREE_VRAM_MB=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -n1 | tr -d ' ')
if ! [[ "$GPU_FREE_VRAM_MB" =~ ^[0-9]+$ ]]; then
  echo "!! Preflight VRAM fallito: nvidia-smi non ha restituito memoria libera leggibile." >&2
  exit 2
fi
GPU_FREE_VRAM_GB=$((GPU_FREE_VRAM_MB / 1024))
if [ "$GPU_FREE_VRAM_GB" -lt "$RECIPE_MIN_VRAM_GB" ] && [ "$REPLACE_RUNNING_SERVER" = "1" ]; then
  echo ">> Attendo il rilascio della memoria GPU da parte del server sostituito..."
  for _ in $(seq 1 10); do
    sleep 2
    GPU_FREE_VRAM_MB=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -n1 | tr -d ' ')
    [[ "$GPU_FREE_VRAM_MB" =~ ^[0-9]+$ ]] || continue
    GPU_FREE_VRAM_GB=$((GPU_FREE_VRAM_MB / 1024))
    [ "$GPU_FREE_VRAM_GB" -ge "$RECIPE_MIN_VRAM_GB" ] && break
  done
fi
if [ "$GPU_FREE_VRAM_GB" -lt "$RECIPE_MIN_VRAM_GB" ]; then
  echo "!! Preflight VRAM fallito per ${RECIPE_ADAPTER}: ${GPU_FREE_VRAM_GB} GB liberi, ne servono almeno ${RECIPE_MIN_VRAM_GB} GB." >&2
  echo "!! Nessun pacchetto o peso è stato scaricato. Libera la GPU o scegli una macchina con più VRAM." >&2
  exit 2
fi
echo ">> Preflight VRAM: ${GPU_FREE_VRAM_GB} GB liberi; budget ricetta ${RECIPE_MIN_VRAM_GB} GB — OK."

# Read MemAvailable (not total RAM) so concurrent processes are accounted for.
RAM_AVAILABLE_KB=$(awk '/^MemAvailable:/ {print $2; exit}' /proc/meminfo 2>/dev/null || true)
if ! [[ "$RAM_AVAILABLE_KB" =~ ^[0-9]+$ ]]; then
  echo "!! Preflight RAM fallito: MemAvailable non leggibile da /proc/meminfo." >&2
  exit 2
fi
RAM_AVAILABLE_GB=$((RAM_AVAILABLE_KB / 1024 / 1024))
if [ "$RAM_AVAILABLE_GB" -lt "$RECIPE_MIN_RAM_GB" ]; then
  echo "!! Preflight RAM fallito per ${RECIPE_ADAPTER}: ${RAM_AVAILABLE_GB} GB disponibili, ne servono almeno ${RECIPE_MIN_RAM_GB} GB." >&2
  echo "!! Nessun pacchetto o peso è stato scaricato. Libera memoria o scegli una macchina con più RAM." >&2
  exit 2
fi
echo ">> Preflight RAM: ${RAM_AVAILABLE_GB} GB disponibili; budget ricetta ${RECIPE_MIN_RAM_GB} GB — OK."

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
    echo "!! GPU con compute capability ${COMPUTE_CAP:-sconosciuta}: la ricetta richiede almeno ${MIN_COMPUTE_CAP}." >&2
    exit 1
  fi
  echo ">> Compute capability verificata: $COMPUTE_CAP (minima $MIN_COMPUTE_CAP)"
  # vLLM supports Turing (sm_75), but those cards do not execute BF16. Keep
  # producer-recommended BF16 on Ampere+ and select the equivalent supported
  # FP16 engine dtype on older cards instead of excluding them from search.
  if awk -v cap="$COMPUTE_CAP" 'BEGIN { exit !(cap + 0 < 8.0) }'; then
    if [ "$RECIPE_USER_DTYPE" = "bfloat16" ]; then
      echo "!! serving.dtype=bfloat16 non è supportato dalla GPU sm_${COMPUTE_CAP}; scegli half/float16 o ripristina Automatico." >&2
      exit 2
    fi
    if [ -n "$RECIPE_USER_DTYPE" ]; then
      echo ">> Precisione manuale rispettata: $RECIPE_USER_DTYPE."
    else
    if [ "$RECIPE_RUNTIME" = "teleocr-native" ]; then
      RECIPE_TELEOCR_SETTINGS=$(printf '%s' "$RECIPE_TELEOCR_SETTINGS" | python3 -c 'import json,sys; x=json.load(sys.stdin); x.setdefault("serving", {})["dtype"]="half"; print(json.dumps(x,separators=(",",":")))')
    elif [ "$RECIPE_INSTALL_VLLM" = "1" ]; then
      has_dtype=0
      for ((i=0; i<${#SERVE_ARGV[@]}; i++)); do
        if [ "${SERVE_ARGV[$i]}" = "--dtype" ] && [ $((i+1)) -lt ${#SERVE_ARGV[@]} ]; then
          SERVE_ARGV[$((i+1))]="half"
          has_dtype=1
          break
        fi
      done
      if [ "$has_dtype" = "0" ]; then SERVE_ARGV+=(--dtype half); fi
    fi
    echo ">> Precisione adattata alla GPU sm_${COMPUTE_CAP}: FP16 (BF16 richiede sm_80+)."
    fi
  fi

  # La compute capability dice cosa sa fare la GPU, non cosa sa eseguire il
  # driver. Sono due cose diverse: una 3060 ha capability 8.6 e passa il
  # controllo sopra, ma con un driver fermo sotto la 12.8 il PyTorch cu128 muore
  # con "The NVIDIA driver on your system is too old (found version 12060)" —
  # dopo aver scaricato qualche gigabyte di wheel e diversi minuti di
  # noleggio. Meglio saperlo adesso.
  DRIVER_CUDA=$(nvidia-smi 2>/dev/null | sed -n 's/.*CUDA Version: *\([0-9][0-9.]*\).*/\1/p' | head -n1)
  if [ -z "$DRIVER_CUDA" ]; then
    echo ">> CUDA del driver non leggibile: proseguo senza il controllo." >&2
  elif ! awk -v actual="$DRIVER_CUDA" -v minimum="$MIN_CUDA_DRIVER" \
      'BEGIN { exit !(actual + 0 >= minimum + 0) }'; then
    echo "!! Il driver di questa macchina arriva a CUDA $DRIVER_CUDA, ma servono almeno $MIN_CUDA_DRIVER." >&2
    echo "!! Noleggia un'istanza che dichiari \"Max CUDA\" $MIN_CUDA_DRIVER o superiore: qui vLLM non partirebbe." >&2
    exit 1
  else
    echo ">> CUDA del driver verificata: $DRIVER_CUDA (minima $MIN_CUDA_DRIVER)"
  fi
else
  echo "!! nvidia-smi non trovato: serve una GPU NVIDIA funzionante." >&2
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
"$PY_BIN" -m pip install --quiet --upgrade pip 'setuptools>=77,<80' wheel
PYTHON_PACKAGES=(
  "vllm==${VLLM_VERSION}" \
  "huggingface_hub"
)
if [ "$RECIPE_ADAPTER" = "teleocr" ]; then
  # The official root package (not just its vLLM model plugin) provides the
  # OCR pipeline, PDF/image preparation and no-repeat-ngram processor. Its
  # pyproject pins Pillow <12 and constrains Python <3.13, so do not apply the
  # unrelated generic runtime pins to this environment.
  PYTHON_PACKAGES+=("$RECIPE_PIP_EXTRA")
  RECIPE_PIP_EXTRA=""
else
  PYTHON_PACKAGES+=("pillow==12.3.0" "pydantic==2.13.5" "timm==1.0.29" "einops==0.8.2")
fi
if [ -n "$TRANSFORMERS_VERSION" ]; then
  PYTHON_PACKAGES+=("transformers==${TRANSFORMERS_VERSION}")
else
  echo ">> Transformers non pinnato dalla ricetta: vLLM risolverà la versione compatibile."
fi
"$PY_BIN" -m pip install --quiet "${PYTHON_PACKAGES[@]}"

fi

# La build CUDA di PyTorch deve conoscere la GPU: il wheel PyPI di default non
# vede le sm_120 (Blackwell) e vLLM muore su "FlashInfer requires sm75 or
# higher". Si sostituisce la sola build, mantenendo *le versioni che la ricetta
# di vLLM ha appena risolto* — imporne altre prima dell'install le contraddice
# (vLLM 0.21 vuole torchvision 0.26, non 0.28).
if [ "$RECIPE_INSTALL_VLLM" = "1" ]; then
  TORCH_CUDA_RUNTIME=$("$PY_BIN" -c "import torch; print(torch.version.cuda or '0')" 2>/dev/null || echo 0)
  if awk -v cap="$COMPUTE_CAP" -v cuda="$TORCH_CUDA_RUNTIME" 'BEGIN { split(cuda, v, "."); exit !(cap + 0 >= 12.0 && (v[1] + 0 < 12 || (v[1] + 0 == 12 && v[2] + 0 < 8))) }'; then
    TORCH_PINNED=$("$PY_BIN" -c "import torch; print(torch.__version__.split('+')[0])")
    TORCH_TRIO=("torch==${TORCH_PINNED}")
    for extra in torchvision torchaudio; do
      version=$("$PY_BIN" -c "import ${extra}; print(${extra}.__version__.split('+')[0])" 2>/dev/null || true)
      [ -n "$version" ] && TORCH_TRIO+=("${extra}==${version}")
    done
    echo ">> PyTorch ${TORCH_PINNED} reinstallato con wheel CUDA 12.8+ per Blackwell: ${TORCH_TRIO[*]}"
    # Un pacchetto alla volta: se torchaudio non esiste sull'indice cu128 (l'
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

# Optional official MonkeyOCRv2 DFlash draft. The recipe only supplies these
# fields when the compatible base checkpoint is selected and DFlash is enabled
# in Settings; no draft weights are downloaded for custom/LoRA checkpoints.
if [ -n "$RECIPE_DRAFT_HF_REPO" ]; then
  if [ -z "$RECIPE_DRAFT_MODEL_DIR" ]; then
    echo "!! Ricetta DFlash incompleta: percorso draft mancante." >&2
    exit 2
  fi
  if [ ! -d "$RECIPE_DRAFT_MODEL_DIR" ] || [ ! -f "$RECIPE_DRAFT_MODEL_DIR/config.json" ]; then
    echo ">> Download draft ufficiale $RECIPE_DRAFT_HF_REPO in $RECIPE_DRAFT_MODEL_DIR..."
    mkdir -p "$(dirname "$RECIPE_DRAFT_MODEL_DIR")"
    DRAFT_MODEL_NAME="$RECIPE_DRAFT_HF_REPO" DRAFT_MODEL_DIR="$RECIPE_DRAFT_MODEL_DIR" "$PY_BIN" - <<'PY'
from huggingface_hub import snapshot_download
import os

snapshot_download(repo_id=os.environ["DRAFT_MODEL_NAME"], local_dir=os.environ["DRAFT_MODEL_DIR"])
PY
  fi
fi

MODEL_NAME="$MODEL_NAME" MODEL_DIR="$MODEL_DIR" VLLM_VERSION="$VLLM_VERSION" \
TORCH_INDEX="$TORCH_INDEX" \
RECIPE_RUNTIME="$RECIPE_RUNTIME" RECIPE_ADAPTER="${RECIPE_ADAPTER:-monkeyocrv2-parsing}" \
RECIPE_DRAFT_HF_REPO="$RECIPE_DRAFT_HF_REPO" RECIPE_DRAFT_MODEL_DIR="$RECIPE_DRAFT_MODEL_DIR" \
RECIPE_TRANSFORMERS_VERSION="$TRANSFORMERS_VERSION" MONKEYOCR_REF="$MONKEYOCR_REF" \
GPU_QUERY="$GPU_QUERY" COMPUTE_CAP="$COMPUTE_CAP" DISK_AVAILABLE_GB="$DISK_AVAILABLE_GB" \
MIN_DISK_GB="$MIN_DISK_GB" \
GPU_MEM_UTIL="$GPU_MEM_UTIL" MAX_MODEL_LEN="$MAX_MODEL_LEN" \
RECIPE_B64="$RECIPE_B64" RECIPE_CONFIG_SIGNATURE="$RECIPE_CONFIG_SIGNATURE" \
"$PY_BIN" - <<'PY'
import json, os, platform, subprocess, sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

def installed(package):
    try:
        return version(package)
    except PackageNotFoundError:
        return ""

def torch_cuda_runtime():
    try:
        import torch
        return str(torch.version.cuda or "")
    except Exception:
        return ""

try:
    recipe = json.loads(__import__("base64").b64decode(os.environ.get("RECIPE_B64", "")).decode())
except Exception:
    recipe = {}
argv = list(recipe.get("argv") or [])

def flag_value(flag):
    try:
        return argv[argv.index(flag) + 1]
    except (ValueError, IndexError):
        return None

safe_argv = list(argv)
for index, value in enumerate(safe_argv[:-1]):
    if value == "--api-key":
        safe_argv[index + 1] = "<redacted>"

gpu_memory_utilization = flag_value("--gpu-memory-utilization")
max_model_len = flag_value("--max-model-len")
if not recipe:
    gpu_memory_utilization = os.environ["GPU_MEM_UTIL"]
    max_model_len = os.environ["MAX_MODEL_LEN"]

manifest = {
    "model": os.environ["MODEL_NAME"],
    "model_dir": os.environ["MODEL_DIR"],
    "vllm": os.environ["VLLM_VERSION"],
    "torch": installed("torch"),
    "torch_cuda_runtime": torch_cuda_runtime(),
    "transformers": installed("transformers"),
    "requested_transformers": os.environ.get("RECIPE_TRANSFORMERS_VERSION", ""),
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
    "draft_model": os.environ.get("RECIPE_DRAFT_HF_REPO", ""),
    "draft_model_dir": os.environ.get("RECIPE_DRAFT_MODEL_DIR", ""),
    "adapter_id": os.environ.get("RECIPE_ADAPTER", ""),
    "runtime": os.environ.get("RECIPE_RUNTIME", ""),
    "gpu": os.environ["GPU_QUERY"],
    "compute_capability": os.environ.get("COMPUTE_CAP", ""),
    "dtype": "bfloat16",
    "gpu_memory_utilization": float(gpu_memory_utilization) if gpu_memory_utilization is not None else None,
    "max_model_len": int(max_model_len) if max_model_len is not None else None,
    "serve_argv": safe_argv,
    "configuration_signature": os.environ.get("RECIPE_CONFIG_SIGNATURE", ""),
    "python": platform.python_version(),
    "recipe": "tabularium-vast-3",
    "disk_available_gb": int(os.environ["DISK_AVAILABLE_GB"]),
    "min_free_disk_gb": int(os.environ["MIN_DISK_GB"]),
}
target = Path(os.environ.get("MODEL_DIR", ".")).parent / "cloud-manifest.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f">> Manifest scritto in {target}")
PY

# 6. Prepare serving flags
if [ ${#SERVE_ARGV[@]} -eq 0 ] && [ "$RECIPE_RUNTIME" != "teleocr-native" ]; then
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

# Il vecchio server, se verificato e sostituibile, è già stato arrestato prima
# del controllo VRAM. Non uccidere processi GPU o listener estranei qui.

# La cache di compilazione appartiene alla coppia torch/CUDA che l'ha prodotta:
# riusarla dopo un cambio di ambiente produce errori di cubin mancanti.
rm -rf "$HOME/.cache/vllm/torch_compile_cache" 2>/dev/null || true

echo "=========================================================="
if [ "$RECIPE_RUNTIME" = "teleocr-native" ]; then
  echo ">> [Tabularium Cloud Runner] Preparazione runner TeleOCR nativo..."
else
  echo ">> [Tabularium Cloud Server] Avvio vLLM su $HOST:$PORT..."
  echo ">> Endpoint: http://$HOST:$PORT/v1"
fi
echo "=========================================================="

if [ "$RECIPE_ADAPTER" = "teleocr" ]; then
  if [ -z "$RECIPE_NATIVE_GATEWAY_B64" ] || [ -z "$RECIPE_NATIVE_REMOTE_PORT" ]; then
    echo "!! Runner nativo non incluso nella ricetta TeleOCR; rifiuto un avvio parziale." >&2
    exit 2
  fi
  NATIVE_GATEWAY="$VENV_DIR/tabularium_teleocr_gateway.py"
  printf '%s' "$RECIPE_NATIVE_GATEWAY_B64" | base64 -d > "$NATIVE_GATEWAY"
  export TABULARIUM_TELEOCR_MODEL="$MODEL_NAME"
  export TABULARIUM_TELEOCR_MODEL_PATH="$MODEL_DIR"
  export TABULARIUM_TELEOCR_SETTINGS="$RECIPE_TELEOCR_SETTINGS"
  echo ">> Avvio runner nativo TeleOCR (TeleOCRClient + vllm-async-engine) su 127.0.0.1:$RECIPE_NATIVE_REMOTE_PORT..."
  "$PY_BIN" -m uvicorn tabularium_teleocr_gateway:app \
    --app-dir "$VENV_DIR" --host 127.0.0.1 \
    --port "$RECIPE_NATIVE_REMOTE_PORT" --no-access-log \
    >> "$REMOTE_LOG_PATH" 2>&1 < /dev/null &
  GATEWAY_PID=$!
  trap 'kill "$GATEWAY_PID" 2>/dev/null || true; wait 2>/dev/null || true' EXIT TERM INT
  wait "$GATEWAY_PID"
  exit "$?"
fi

if [ "$RECIPE_ADAPTER" = "glm-ocr" ]; then
  if [ -z "$RECIPE_NATIVE_GATEWAY_B64" ] || [ -z "$RECIPE_NATIVE_REMOTE_PORT" ]; then
    echo "!! Gateway del pipeline nativo non incluso nella ricetta ${RECIPE_ADAPTER}; rifiuto un avvio parziale." >&2
    exit 2
  fi
  NATIVE_GATEWAY="$VENV_DIR/tabularium_glmocr_gateway.py"
  NATIVE_MODULE="tabularium_glmocr_gateway"
  printf '%s' "$RECIPE_NATIVE_GATEWAY_B64" | base64 -d > "$NATIVE_GATEWAY"
  export TABULARIUM_SERVER_API_KEY="$API_KEY"
  export TABULARIUM_GLMOCR_VLLM_PORT="$PORT"
  "$PY_BIN" -m uvicorn "$NATIVE_MODULE:app" \
    --app-dir "$VENV_DIR" --host 127.0.0.1 \
    --port "$RECIPE_NATIVE_REMOTE_PORT" --no-access-log \
    >> "$REMOTE_LOG_PATH" 2>&1 < /dev/null &
  GATEWAY_PID=$!
  "$PY_BIN" "${SERVE_ARGV[@]}" >> "$REMOTE_LOG_PATH" 2>&1 < /dev/null &
  MODEL_PID=$!
  trap 'kill "$GATEWAY_PID" "$MODEL_PID" 2>/dev/null || true; wait 2>/dev/null || true' EXIT TERM INT
  set +e
  wait -n "$GATEWAY_PID" "$MODEL_PID"
  RESULT=$?
  set -e
  exit "$RESULT"
fi

if [ "$RECIPE_ADAPTER" = "mineru2.5" ]; then
  if [ -z "$RECIPE_NATIVE_GATEWAY_B64" ] || [ -z "$RECIPE_NATIVE_REMOTE_PORT" ]; then
    echo "!! Gateway MinerUClient non incluso nella ricetta MinerU2.5; rifiuto un avvio parziale." >&2
    exit 2
  fi
  NATIVE_GATEWAY="$VENV_DIR/tabularium_mineru_gateway.py"
  printf '%s' "$RECIPE_NATIVE_GATEWAY_B64" | base64 -d > "$NATIVE_GATEWAY"
  export TABULARIUM_SERVER_API_KEY="$API_KEY"
  # The native MinerUClient selects the server's exposed model id, which can
  # differ from the Hugging Face repo passed to vLLM (here `mineru2.5`).
  export TABULARIUM_MINERU_MODEL="$RECIPE_SERVED_MODEL_NAME"
  export TABULARIUM_MINERU_SETTINGS="$RECIPE_MINERU_SETTINGS"
  export TABULARIUM_MINERU_VLLM_URL="http://127.0.0.1:$PORT"
  # MinerUClient checks /v1/models in its constructor. Start vLLM first and
  # wait for the expected served model before launching the official bridge;
  # starting both together races and makes the bridge exit on connection-refused.
  export TABULARIUM_EXPECTED_VLLM_MODEL="$RECIPE_SERVED_MODEL_NAME"
  "$PY_BIN" "${SERVE_ARGV[@]}" >> "$REMOTE_LOG_PATH" 2>&1 < /dev/null &
  MODEL_PID=$!
  model_ready=0
  for _ in $(seq 1 180); do
    if ! kill -0 "$MODEL_PID" 2>/dev/null; then
      echo "!! Il server vLLM MinerU è terminato prima di esporre /v1/models." >&2
      tail -n 60 "$REMOTE_LOG_PATH" >&2 || true
      wait "$MODEL_PID" || true
      exit 1
    fi
    if "$PY_BIN" - <<'PY' >/dev/null 2>&1
import json, os, urllib.request
url = os.environ["TABULARIUM_MINERU_VLLM_URL"].rstrip("/") + "/v1/models"
headers = {}
key = os.environ.get("TABULARIUM_SERVER_API_KEY", "")
if key:
    headers["Authorization"] = f"Bearer {key}"
request = urllib.request.Request(url, headers=headers)
with urllib.request.urlopen(request, timeout=3) as response:
    models = json.load(response).get("data", [])
if not any(item.get("id") == os.environ["TABULARIUM_EXPECTED_VLLM_MODEL"] for item in models):
    raise SystemExit(1)
PY
    then
      model_ready=1
      break
    fi
    sleep 2
  done
  if [ "$model_ready" != "1" ]; then
    echo "!! Timeout: vLLM non ha esposto ${RECIPE_SERVED_MODEL_NAME} su /v1/models entro 6 minuti; il bridge MinerU non è stato avviato." >&2
    kill -TERM "$MODEL_PID" 2>/dev/null || true
    wait "$MODEL_PID" 2>/dev/null || true
    exit 1
  fi
  "$PY_BIN" -m uvicorn tabularium_mineru_gateway:app \
    --app-dir "$VENV_DIR" --host 127.0.0.1 \
    --port "$RECIPE_NATIVE_REMOTE_PORT" --no-access-log \
    >> "$REMOTE_LOG_PATH" 2>&1 < /dev/null &
  GATEWAY_PID=$!
  trap 'kill "$GATEWAY_PID" "$MODEL_PID" 2>/dev/null || true; wait 2>/dev/null || true' EXIT TERM INT
  set +e
  wait -n "$GATEWAY_PID" "$MODEL_PID"
  RESULT=$?
  set -e
  exit "$RESULT"
fi

exec "$PY_BIN" "${SERVE_ARGV[@]}"
