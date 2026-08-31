"""Modal: deployment serverless del VLM dots.mocr (dots-studio) via vLLM.

Stessa architettura di ``modal_vllm.py`` (MonkeyOCRv2): vedi quel file per il
razionale di `subprocess.Popen` dentro `@modal.web_server`.

Requisiti verificati su Hugging Face dots-studio/dots.mocr e sul README
ufficiale del repo (originariamente rednote-hilab/dots.ocr, agosto 2026):
  - repo HF: ``dots-studio/dots.mocr`` (rebrand dell'org rednote-hilab, stesso
    checkpoint). Pesi reali ~6.1 GB (due shard safetensors), non i 3.4 GB
    dichiarati in una stima precedente (quella era il solo componente LLM).
  - architettura nativa in **vLLM >= 0.11.0** (merge ufficiale, PR #24645):
    non serve più il pin storico `vllm==0.9.1` delle versioni pre-integrazione.
  - comando: `vllm serve <repo> --trust-remote-code
    --chat-template-content-format string` — quest'ultimo flag è
    **obbligatorio** secondo il README (formato di serializzazione del
    contenuto per il chat template, non interferisce con l'invio di immagini
    in stile OpenAI).
  - GPU: nessuna raccomandazione ufficiale di VRAM. Default qui: L4 (24 GB),
    comodo per un checkpoint da 6+ GB.

Uso:
  modal deploy scripts/cloud/modal_dots_ocr.py

L'URL dell'endpoint è stampato da `modal deploy` e ha forma:
  https://<WORKSPACE>--tabularium-dots-ocr-serve.modal.run
"""

import os
import subprocess
import time
import urllib.request

import modal

APP_NAME = "tabularium-dots-ocr"
MODEL_ID = os.environ.get("TABULARIUM_MODAL_MODEL", "dots-studio/dots.mocr")
GPU = os.environ.get("TABULARIUM_MODAL_GPU", "L4")
PORT = 8888
VLLM_VERSION = os.environ.get("TABULARIUM_VLLM_VERSION", "0.28.0")
MIN_CONTAINERS = int(os.environ.get("TABULARIUM_MODAL_MIN_CONTAINERS", "0"))
MAX_CONTAINERS = int(os.environ.get("TABULARIUM_MODAL_MAX_CONTAINERS", "2"))
MAX_INPUTS = int(os.environ.get("TABULARIUM_MODAL_MAX_INPUTS", "4"))

weights = modal.Volume.from_name("dots-ocr-weights", create_if_missing=True)

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
    """Avvia `vllm serve` con i flag verificati sul README di dots.mocr."""
    argv = [
        "vllm", "serve", MODEL_ID,
        "--host", "0.0.0.0",
        "--port", str(PORT),
        "--trust-remote-code",
        "--chat-template-content-format", "string",
        "--max-num-seqs", str(MAX_INPUTS),
        "--served-model-name", "dots-mocr",
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
