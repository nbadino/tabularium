"""Modal: deployment serverless di Qwen/Qwen3-VL-8B-Instruct via vLLM.

Stessa architettura di ``modal_vllm.py`` (MonkeyOCRv2): vedi quel file per il
razionale di `subprocess.Popen` dentro `@modal.web_server`.

Requisiti verificati sulla model card Hugging Face e sul repo GitHub
QwenLM/Qwen3-VL (agosto 2026):
  - repo HF: ``Qwen/Qwen3-VL-8B-Instruct`` (~16.3 GB su disco in BF16 — il
    "9B" della card è il conteggio parametri, non la dimensione su disco).
  - architettura nativa in **vLLM >= 0.11.0**: nessun `--trust-remote-code`
    richiesto.
  - VLM generalista, non specializzato documenti: il parser Tabularium
    dedicato non è ancora implementato, questa template lo rende
    raggiungibile/deployabile ma non ancora usabile dal prefill strutturato.
  - checkpoint BF16 non entra in una GPU consumer da 8GB (v. nota locale in
    `model_adapters.py`): su Modal L4 (24 GB) c'è margine comodo, quindi qui
    si usa direttamente BF16 invece della variante FP8 ufficiale
    (``Qwen/Qwen3-VL-8B-Instruct-FP8``, utile solo se si vuole restare sotto
    12 GB).
  - `--limit-mm-per-prompt` limita le immagini per richiesta (sintassi JSON
    valida nelle release vLLM correnti).

Uso:
  modal deploy scripts/cloud/modal_qwen3_vl.py

L'URL dell'endpoint è stampato da `modal deploy` e ha forma:
  https://<WORKSPACE>--tabularium-qwen3-vl-serve.modal.run
"""

import os
import subprocess
import time
import urllib.request

import modal

APP_NAME = "tabularium-qwen3-vl"
MODEL_ID = os.environ.get("TABULARIUM_MODAL_MODEL", "Qwen/Qwen3-VL-8B-Instruct")
GPU = os.environ.get("TABULARIUM_MODAL_GPU", "L4")
PORT = 8888
VLLM_VERSION = os.environ.get("TABULARIUM_VLLM_VERSION", "0.28.0")
MIN_CONTAINERS = int(os.environ.get("TABULARIUM_MODAL_MIN_CONTAINERS", "0"))
MAX_CONTAINERS = int(os.environ.get("TABULARIUM_MODAL_MAX_CONTAINERS", "2"))
MAX_INPUTS = int(os.environ.get("TABULARIUM_MODAL_MAX_INPUTS", "4"))

weights = modal.Volume.from_name("qwen3-vl-weights", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "gcc", "g++")
    .pip_install(f"vllm=={VLLM_VERSION}", "huggingface_hub[hf_transfer]")
    .env({
        "HF_HOME": "/weights/hf-cache",
        "HF_XET_HIGH_PERFORMANCE": "1",
        "VLLM_USE_FLASHINFER_SAMPLER": "0",
    })
)

app = modal.App(APP_NAME)


@app.function(
    image=image,
    gpu=GPU,
    timeout=3600,
    max_containers=MAX_CONTAINERS,
    min_containers=MIN_CONTAINERS,
    scaledown_window=900,
    volumes={"/weights": weights},
)
@modal.concurrent(max_inputs=MAX_INPUTS)
@modal.web_server(PORT, startup_timeout=1800)
def serve():
    """Avvia `vllm serve` con i flag verificati per Qwen3-VL-8B-Instruct."""
    argv = [
        "vllm", "serve", MODEL_ID,
        "--host", "0.0.0.0",
        "--port", str(PORT),
        "--dtype", "bfloat16",
        "--max-model-len", "32768",
        "--limit-mm-per-prompt", '{"image":4,"video":0}',
        "--max-num-seqs", str(MAX_INPUTS),
        "--served-model-name", "qwen3-vl-8b",
    ]
    api_key = os.environ.get("TABULARIUM_VLLM_API_KEY", "").strip()
    if api_key:
        argv.extend(["--api-key", api_key])

    print(">> Avvio:", " ".join(argv))
    proc = subprocess.Popen(argv)

    deadline = time.monotonic() + 10 * 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"vllm serve uscito con codice {proc.returncode} durante l'avvio")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2)
            print(">> vLLM pronto su /health")
            break
        except Exception:  # noqa: BLE001
            time.sleep(2)
