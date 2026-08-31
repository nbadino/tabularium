"""Modal: deployment serverless del VLM zai-org/GLM-OCR via vLLM.

Stessa architettura di ``modal_vllm.py`` (MonkeyOCRv2): vedi quel file per il
razionale di `subprocess.Popen` dentro `@modal.web_server`.

Requisiti verificati sul README ufficiale GitHub zai-org/GLM-OCR e sulla
recipe vLLM (docs.vllm.ai/projects/recipes/en/stable/GLM/GLM-OCR.html,
agosto 2026):
  - repo HF: ``zai-org/GLM-OCR`` (0.9B dichiarati, checkpoint reale ~2.7 GB
    bf16, architettura nativa `glm_ocr` su Transformers >= 5.0.0).
  - richiede **vLLM >= 0.19.0**. `--trust-remote-code` non compare nel
    comando ufficiale (architettura nativa) — se il deploy fallisce con
    "unsupported architecture" su una wheel meno recente, è il primo flag da
    aggiungere.
  - comando ufficiale usa `--speculative-config` per il draft MTP
    (accelera la decodifica, non cambia l'output): mantenuto qui.
  - problema noto in community (non nel README): la cache dell'encoder
    immagini di default (4000 token) non basta per immagini ad alta
    risoluzione ("exceeds pre-allocated encoder cache size") — fix riportato:
    `--max-num-batched-tokens 32768`.
  - context length nativo 131072, ma il repo non raccomanda un valore per
    hardware specifico ("in base alla propria macchina").

Uso:
  modal deploy scripts/cloud/modal_glm_ocr.py

L'URL dell'endpoint è stampato da `modal deploy` e ha forma:
  https://<WORKSPACE>--tabularium-glm-ocr-serve.modal.run
"""

import os
import subprocess
import time
import urllib.request

import modal

APP_NAME = "tabularium-glm-ocr"
MODEL_ID = os.environ.get("TABULARIUM_MODAL_MODEL", "zai-org/GLM-OCR")
GPU = os.environ.get("TABULARIUM_MODAL_GPU", "L4")
PORT = 8888
VLLM_VERSION = os.environ.get("TABULARIUM_VLLM_VERSION", "0.19.0")
MIN_CONTAINERS = int(os.environ.get("TABULARIUM_MODAL_MIN_CONTAINERS", "0"))
MAX_CONTAINERS = int(os.environ.get("TABULARIUM_MODAL_MAX_CONTAINERS", "2"))
MAX_INPUTS = int(os.environ.get("TABULARIUM_MODAL_MAX_INPUTS", "4"))

weights = modal.Volume.from_name("glm-ocr-weights", create_if_missing=True)

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
    """Avvia `vllm serve` con i flag verificati sul README di GLM-OCR."""
    argv = [
        "vllm", "serve", MODEL_ID,
        "--host", "0.0.0.0",
        "--port", str(PORT),
        "--speculative-config", '{"method": "mtp", "num_speculative_tokens": 3}',
        "--max-num-batched-tokens", "32768",
        "--max-num-seqs", str(MAX_INPUTS),
        "--served-model-name", "glm-ocr",
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
