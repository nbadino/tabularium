"""Modal: deployment serverless del server vLLM di PaddleOCR-VL-1.6.

Stessa architettura di ``modal_vllm.py`` (MonkeyOCRv2): la GPU si accende alla
prima richiesta e si spegne dopo il periodo di "caldo". Vedi quel file per il
razionale di `subprocess.Popen` (non `subprocess.run`) dentro la funzione
`@modal.web_server`: un `.run()` bloccante impedisce a Modal di segnare mai il
container pronto, anche quando il server è realmente su (bug riprodotto e
confermato su MonkeyOCRv2, stessa causa qui).

Requisiti verificati sulla guida ufficiale vLLM
(docs.vllm.ai/projects/recipes/en/stable/PaddlePaddle/PaddleOCR-VL.html e
github.com/vllm-project/recipes, agosto 2026):
  - repo HF: `PaddlePaddle/PaddleOCR-VL-1.6` (upgrade della card base
    `PaddleOCR-VL` — repo distinto, non solo una revisione).
  - comando: `vllm serve <repo> --trust-remote-code --max-num-batched-tokens
    16384 --no-enable-prefix-caching --mm-processor-cache-gb 0` — i due flag
    di cache sono disattivati deliberatamente: i task OCR non riusano
    l'immagine fra richieste, la cache costa memoria senza guadagno.
  - il VLM standalone riceve task di recognition su crop (prefissi "OCR:",
    "Table Recognition:", "Formula Recognition:", "Chart Recognition:").
    La pipeline ufficiale completa aggiunge PP-DocLayoutV3/PP-DocLayoutV2 per
    layout/bbox; questo endpoint serve il VLM e Tabularium usa il detector OCR
    locale come primo stadio del prefill, poi il VLM Paddle per i crop.
  - GPU/dtype: NON documentati esplicitamente dalla guida ufficiale. Default
    qui: L4 (stesso tetto prudente di MonkeyOCRv2), sovrascrivibile.
  - La pipeline Paddle documenta tabelle HTML; l'adapter Tabularium le converte
    in OTSL/griglia interna per l'editor. Il formato va verificato sul primo
    endpoint reale dopo il redeploy.

Uso:
  modal deploy scripts/cloud/modal_paddleocr_vl.py

L'URL dell'endpoint è stampato da `modal deploy` e ha forma:
  https://<WORKSPACE>--tabularium-paddleocr-vl-serve.modal.run
"""

import os
import subprocess
import time
import urllib.request

import modal

APP_NAME = "tabularium-paddleocr-vl"
MODEL_ID = os.environ.get("TABULARIUM_MODAL_MODEL", "PaddlePaddle/PaddleOCR-VL-1.6")
GPU = os.environ.get("TABULARIUM_MODAL_GPU", "L4")
PORT = 8888
VLLM_VERSION = os.environ.get("TABULARIUM_VLLM_VERSION", "0.28.0")
MIN_CONTAINERS = int(os.environ.get("TABULARIUM_MODAL_MIN_CONTAINERS", "0"))
MAX_CONTAINERS = int(os.environ.get("TABULARIUM_MODAL_MAX_CONTAINERS", "2"))
MAX_INPUTS = int(os.environ.get("TABULARIUM_MODAL_MAX_INPUTS", "4"))

weights = modal.Volume.from_name("paddleocr-vl-weights", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "gcc", "g++")
    .pip_install(f"vllm=={VLLM_VERSION}", "huggingface_hub[hf_transfer]")
    .env({
        "HF_HOME": "/weights/hf-cache",  # persiste i pesi (~1.8 GB) fra deploy
        "HF_XET_HIGH_PERFORMANCE": "1",
        # Come in modal_vllm.py: FlashInfer compila i kernel di sampling con
        # nvcc a runtime, ma l'immagine non ha il CUDA toolkit — il warmup
        # crashava (riprodotto: "Could not find nvcc and default
        # cuda_home=... doesn't exist"). Il sampler nativo di vLLM basta qui.
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
    """Avvia `vllm serve` con i flag verificati per PaddleOCR-VL-1.6."""
    argv = [
        "vllm", "serve", MODEL_ID,
        "--host", "0.0.0.0",
        "--port", str(PORT),
        "--trust-remote-code",
        "--max-num-batched-tokens", "16384",
        "--no-enable-prefix-caching",
        "--mm-processor-cache-gb", "0",
        "--max-num-seqs", str(MAX_INPUTS),
        "--served-model-name", "PaddleOCR-VL-1.6",
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
