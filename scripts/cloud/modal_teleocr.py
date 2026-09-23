"""Modal: TeleOCR via il plugin vLLM ufficiale out-of-tree.

Il plugin del repository TeleOCR registra l'architettura personalizzata del
checkpoint e pinna vLLM 0.11.0 + Transformers 4.57.1. La recipe remota Vast e
questa immagine installano lo stesso plugin; non basta installare vLLM e
abilitare `trust_remote_code`.

Deploy:
  modal deploy scripts/cloud/modal_teleocr.py
"""

import os
import subprocess
import time
import urllib.request

import modal

APP_NAME = "tabularium-teleocr"
MODEL_ID = os.environ.get("TABULARIUM_MODAL_MODEL", "StarDoc-AI/TeleOCR")
GPU = os.environ.get("TABULARIUM_MODAL_GPU", "L4")
PORT = 8888
VLLM_VERSION = os.environ.get("TABULARIUM_VLLM_VERSION", "0.11.0")
MIN_CONTAINERS = int(os.environ.get("TABULARIUM_MODAL_MIN_CONTAINERS", "0"))
MAX_CONTAINERS = int(os.environ.get("TABULARIUM_MODAL_MAX_CONTAINERS", "2"))
MAX_INPUTS = int(os.environ.get("TABULARIUM_MODAL_MAX_INPUTS", os.environ.get("TABULARIUM_SERVE_MAX_NUM_SEQS", "1")))
SERVING_OVERRIDES = {
    "--gpu-memory-utilization": os.environ.get("TABULARIUM_SERVE_GPU_MEMORY_UTILIZATION"),
    "--max-model-len": os.environ.get("TABULARIUM_SERVE_MAX_MODEL_LEN"),
    "--max-num-seqs": os.environ.get("TABULARIUM_SERVE_MAX_NUM_SEQS"),
    "--max-num-batched-tokens": os.environ.get("TABULARIUM_SERVE_MAX_NUM_BATCHED_TOKENS"),
}

def apply_serving_overrides(argv):
    """Apply per-model settings captured by the local deploy command."""
    argv = list(argv)
    for flag, value in SERVING_OVERRIDES.items():
        if value is None:
            continue
        if flag in argv:
            argv[argv.index(flag) + 1] = value
        else:
            # Wrapper commands may use `--` to separate their own flags from
            # vLLM flags; insert before that boundary when one is present.
            boundary = argv.index("--") if "--" in argv else len(argv)
            argv[boundary:boundary] = [flag, value]
    return argv

TELEOCR_PACKAGE = (
    "git+https://github.com/caipeng328/TeleOCR.git@main"
)

weights = modal.Volume.from_name("teleocr-weights", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "gcc", "g++")
    .pip_install(
        f"vllm=={VLLM_VERSION}",
        "huggingface_hub[hf_transfer]",
        TELEOCR_PACKAGE,
    )
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
    """Avvia vLLM dopo il caricamento del plugin architetturale TeleOCR."""
    argv = [
        "vllm", "serve", MODEL_ID,
        "--host", "0.0.0.0",
        "--port", str(PORT),
        "--trust-remote-code",
        "--logits-processors",
        "TeleOCR.vlm_utils.vlm_client.vllm_v1_no_repeat_ngram:VllmV1NoRepeatNGramLogitsProcessor",
        "--dtype", "bfloat16",
        "--gpu-memory-utilization", "0.95",
        "--max-model-len", "16384",
        "--served-model-name", "StarDoc-AI/TeleOCR",
    ]
    api_key = os.environ.get("TABULARIUM_VLLM_API_KEY", "").strip()
    if api_key:
        argv.extend(["--api-key", api_key])

    print(">> Avvio:", " ".join(argv))
    argv = apply_serving_overrides(argv)
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

    if proc.poll() is not None:
        raise RuntimeError(f"vllm serve uscito con codice {proc.returncode} durante l'avvio")
