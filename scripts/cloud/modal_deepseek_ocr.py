"""Modal: deployment serverless del VLM deepseek-ai/DeepSeek-OCR-2 via vLLM.

Stessa architettura di ``modal_vllm.py`` (MonkeyOCRv2): vedi quel file per il
razionale di `subprocess.Popen` dentro `@modal.web_server`.

Requisiti verificati su recipes.vllm.ai/deepseek-ai/DeepSeek-OCR-2 e sulla
card Hugging Face (agosto 2026):
  - repo HF: ``deepseek-ai/DeepSeek-OCR-2`` (3B, ~6 GB su disco in BF16,
    licenza Apache-2.0 — non MIT come indicato in una stima precedente).
  - richiede **vLLM >= 0.12.0**: l'architettura ha una storia di breaking
    change fra versioni vLLM (issue vllm-project/vllm#33252, GH DeepSeek-OCR
    #231 su una versione precedente) — verificare con `vllm serve --help`
    prima di un deploy reale, non fidarsi ciecamente del pin qui sotto.
  - `--trust-remote-code` richiesto (confermato su README/model card).
  - logits processor dedicato per evitare loop di generazione su documenti
    lunghi, stesso principio di MinerU2.5/Unlimited-OCR.
  - la "Gundam mode" (compressione ottica/crop tiling) è hardcoded
    nell'integrazione vLLM: nessun flag la controlla.
  - card ufficiale: "una singola GPU >= 8GB è tipicamente sufficiente per
    l'inferenza BF16" — margine comodo su L4 (24 GB).

Uso:
  modal deploy scripts/cloud/modal_deepseek_ocr.py

L'URL dell'endpoint è stampato da `modal deploy` e ha forma:
  https://<WORKSPACE>--tabularium-deepseek-ocr-serve.modal.run
"""

import os
import subprocess
import time
import urllib.request

import modal

APP_NAME = "tabularium-deepseek-ocr"
MODEL_ID = os.environ.get("TABULARIUM_MODAL_MODEL", "deepseek-ai/DeepSeek-OCR-2")
GPU = os.environ.get("TABULARIUM_MODAL_GPU", "L4")
PORT = 8888
VLLM_VERSION = os.environ.get("TABULARIUM_VLLM_VERSION", "0.12.0")
MIN_CONTAINERS = int(os.environ.get("TABULARIUM_MODAL_MIN_CONTAINERS", "0"))
MAX_CONTAINERS = int(os.environ.get("TABULARIUM_MODAL_MAX_CONTAINERS", "2"))
MAX_INPUTS = int(os.environ.get("TABULARIUM_MODAL_MAX_INPUTS", "4"))

weights = modal.Volume.from_name("deepseek-ocr-weights", create_if_missing=True)

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
    """Avvia `vllm serve` con i flag verificati per DeepSeek-OCR-2."""
    argv = [
        "vllm", "serve", MODEL_ID,
        "--host", "0.0.0.0",
        "--port", str(PORT),
        "--trust-remote-code",
        "--logits-processors", "vllm.model_executor.models.deepseek_ocr:NGramPerReqLogitsProcessor",
        "--no-enable-prefix-caching",
        "--mm-processor-cache-gb", "0",
        "--max-num-seqs", str(MAX_INPUTS),
        "--served-model-name", "deepseek-ocr-2",
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
