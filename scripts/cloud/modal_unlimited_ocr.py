"""Modal: deployment serverless di Unlimited-OCR (baidu) via vLLM.

Stessa architettura di ``modal_vllm.py`` (MonkeyOCRv2): il profilo predefinito
mantiene una GPU calda per eliminare il caricamento dei pesi a ogni richiesta.
Impostando `TABULARIUM_MODAL_MIN_CONTAINERS=0` si abilita lo scale-to-zero.
razionale di `subprocess.Popen` dentro `@modal.web_server`.

Differenza decisiva rispetto alle altre template: questo modello **esige**
l'immagine Docker dedicata ``vllm/vllm-openai:unlimited-ocr`` —
l'architettura (linaggio DeepSeek-OCR con R-SWA) non è ancora nella wheel
stabile di vLLM, quindi non si può ``pip install``. L'immagine si carica con
`modal.Image.from_registry` e si svuota l'ENTRYPOINT (è sintonizzato per
`docker run`, qui il comando lo lanciamo noi).

Requisiti verificati sulla ricetta vLLM ufficiale
(recipes.vllm.ai/baidu/Unlimited-OCR, agosto 2026):
  - repo HF: ``baidu/Unlimited-OCR`` (3B, BF16, licenza MIT);
  - comando: ``vllm serve <repo> --trust-remote-code --logits_processors
    vllm.model_executor.models.unlimited_ocr:NGramPerReqLogitsProcessor
    --no-enable-prefix-caching --mm-processor-cache-gb 0``. Il logits
    processor è OBBLIGATORIO: senza, i documenti lunghi vanno in loop sui
    token ``<|det|>``. Le due disattivazioni di cache sono deliberate
    (l'OCR non riusa l'immagine fra richieste);
  - per richiesta il client deve inviare: prompt che inizia con il prefisso
    letterale ``<image>``, ``skip_special_tokens=False`` e
    ``vllm_xargs {ngram_size: 35, window_size: 128}`` (1024 per input
    multi-pagina). L'adapter di Tabularium li invia nel prefill end2end e
    converte i marker di grounding in bozze di blocchi modificabili.

Uso:
  modal deploy scripts/cloud/modal_unlimited_ocr.py

L'URL dell'endpoint è stampato da `modal deploy` e ha forma:
  https://<WORKSPACE>--tabularium-unlimited-ocr-serve.modal.run

Il profilo performance usato dalla UI imposta anche `max-num-batched-tokens=8192`,
`max-num-seqs=4` e `gpu-memory-utilization=0.95`, seguendo la recipe vLLM.
"""

import os
import subprocess
import time
import urllib.request

import modal

APP_NAME = "tabularium-unlimited-ocr"
MODEL_ID = os.environ.get("TABULARIUM_MODAL_MODEL", "baidu/Unlimited-OCR")
GPU = os.environ.get("TABULARIUM_MODAL_GPU", "L4")
PORT = 8888
SERVED_NAME = "Unlimited-OCR"
MIN_CONTAINERS = int(os.environ.get("TABULARIUM_MODAL_MIN_CONTAINERS", "0"))
MAX_CONTAINERS = int(os.environ.get("TABULARIUM_MODAL_MAX_CONTAINERS", "2"))
MAX_INPUTS = int(os.environ.get("TABULARIUM_MODAL_MAX_INPUTS", "4"))
# Immagine ufficiale della ricetta (CUDA 13.0); per GPU Hopper con driver
# CUDA 12.9 esiste il tag `unlimited-ocr-cu129` (override via env).
VLLM_IMAGE = os.environ.get(
    "TABULARIUM_MODAL_VLLM_IMAGE", "vllm/vllm-openai:unlimited-ocr"
)

weights = modal.Volume.from_name("unlimited-ocr-weights", create_if_missing=True)

image = (
    modal.Image.from_registry(VLLM_IMAGE, add_python="3.12")
    .entrypoint([])
    .env({
        # I pesi (~6 GB) persistono nel volume fra deploy e cold start.
        "HF_HOME": "/weights/hf-cache",
        "HF_XET_HIGH_PERFORMANCE": "1",
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
    """Avvia `vllm serve` con il logits processor richiesto da Unlimited-OCR."""
    argv = [
        "vllm", "serve", MODEL_ID,
        "--host", "0.0.0.0",
        "--port", str(PORT),
        "--trust-remote-code",
        "--logits_processors",
        "vllm.model_executor.models.unlimited_ocr:NGramPerReqLogitsProcessor",
        "--no-enable-prefix-caching",
        "--mm-processor-cache-gb", "0",
        # Budget della recipe vLLM: più spazio al prefill multimodale senza
        # alterare il modello o i parametri di decoding.
        "--max-num-batched-tokens", "8192",
        "--max-model-len", "32768",
        "--max-num-seqs", str(MAX_INPUTS),
        "--gpu-memory-utilization", "0.95",
        "--served-model-name", SERVED_NAME,
    ]
    api_key = os.environ.get("TABULARIUM_VLLM_API_KEY", "").strip()
    if api_key:
        argv.extend(["--api-key", api_key])

    print(">> Avvio:", " ".join(argv))
    proc = subprocess.Popen(argv)

    deadline = time.monotonic() + 10 * 60  # il download dei pesi può allungare l'avvio
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"vllm serve uscito con codice {proc.returncode} durante l'avvio")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2)
            print(">> vLLM pronto su /health")
            break
        except Exception:  # noqa: BLE001
            time.sleep(2)
