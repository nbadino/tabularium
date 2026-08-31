"""Modal: deployment serverless del VLM MinerU2.5-Pro usato da MinerU 3.4.x.

Stessa architettura di ``modal_vllm.py`` (MonkeyOCRv2): vedi quel file per il
razionale di `subprocess.Popen` dentro `@modal.web_server` (un `subprocess.run`
bloccante impedisce a Modal di segnare mai il container pronto).

MinerU 3.4.5 è il runtime completo (API/CLI); questo endpoint espone il suo
componente VLM OpenAI-compatible. Il client Tabularium implementa lo stesso
protocollo `MinerUClient.two_step_extract` per immagini singole.

Requisiti verificati su github.com/opendatalab/mineru-vl-utils (agosto 2026):
  - comando: `vllm serve <repo> --logits-processors
    mineru_vl_utils:MinerULogitsProcessor` — il flag è richiesto da vLLM
    ≥0.10.1 (qui pinnato a 0.21.0, quindi sempre necessario) per attivare il
    `no_repeat_ngram_size` che evita generazioni ripetute; senza, l'output
    degrada. Richiede il pacchetto base `mineru-vl-utils` installato
    nell'immagine (il flag è un percorso Python risolto da vLLM a runtime) —
    **non** l'extra `[vllm]`: la matrice di compatibilità pubblicata dichiara
    vLLM <0.22.0; il logits
    processor sta nel pacchetto base, l'extra serve solo al suo engine
    in-process, non usato qui (si lancia `vllm serve` come processo diretto).
  - checkpoint: il repo verificato dalla guida ufficiale è
    `opendatalab/MinerU2.5-2509-1.2B`; qui il default è l'ultimo checkpoint
    "Pro" della stessa famiglia/architettura 1.2B (peso più recente, comando
    di serve identico) — se la risposta del modello peggiora, tornare al
    checkpoint 2509 esplicitamente verificato con `TABULARIUM_MODAL_MODEL`.
  - **NON usare l'endpoint OpenAI-compatibile grezzo**: la guida ufficiale
    prescrive il client dedicato (`mineru_vl_utils.MinerUClient`, metodo
    `two_step_extract`), che orchestra layout+contenuto in più chiamate con
    prompt e post-processing propri — non un singolo prompt testuale.
    `MinerU2_5Adapter.prompt_for` in `model_adapters.py` resta perciò
    `NotImplementedError` di proposito: questa template rende il modello
    *raggiungibile e deployabile* da UI, non ancora *usabile dal prefill*
    generico di Tabularium (che parla `/chat/completions` grezzo). Integrare
    `mineru_vl_utils` lato client è lavoro separato, non ancora fatto.
  - GPU/dtype: BF16 confermato dalla model card; compute capability non
    dichiarata esplicitamente. Default qui: L4 (stesso tetto prudente delle
    altre template), sovrascrivibile.

Uso:
  modal deploy scripts/cloud/modal_mineru.py

L'URL dell'endpoint è stampato da `modal deploy` e ha forma:
  https://<WORKSPACE>--tabularium-mineru-serve.modal.run
"""

import os
import subprocess
import time
import urllib.request

import modal

APP_NAME = "tabularium-mineru"
MODEL_ID = os.environ.get("TABULARIUM_MODAL_MODEL", "opendatalab/MinerU2.5-Pro-2605-1.2B")
GPU = os.environ.get("TABULARIUM_MODAL_GPU", "L4")
PORT = 8888
VLLM_VERSION = os.environ.get("TABULARIUM_VLLM_VERSION", "0.21.0")
MIN_CONTAINERS = int(os.environ.get("TABULARIUM_MODAL_MIN_CONTAINERS", "0"))
MAX_CONTAINERS = int(os.environ.get("TABULARIUM_MODAL_MAX_CONTAINERS", "2"))
MAX_INPUTS = int(os.environ.get("TABULARIUM_MODAL_MAX_INPUTS", "4"))

weights = modal.Volume.from_name("mineru-weights", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "gcc", "g++")
    .pip_install(
        f"vllm=={VLLM_VERSION}",
        "huggingface_hub[hf_transfer]",
        # Solo il pacchetto base: il logits processor che
        # `--logits-processors` risolve a runtime è qui. L'extra `[vllm]`
        # dichiara vLLM <0.22.0, quindi il pin 0.21.0 evita una combinazione
        # non supportata.
        "mineru-vl-utils",
    )
    .env({
        "HF_HOME": "/weights/hf-cache",
        "HF_XET_HIGH_PERFORMANCE": "1",
        # Come in modal_vllm.py / modal_paddleocr_vl.py: FlashInfer compila i
        # kernel di sampling con nvcc a runtime, assente nell'immagine.
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
    """Avvia `vllm serve` con il logits processor richiesto da MinerU2.5."""
    argv = [
        "vllm", "serve", MODEL_ID,
        "--host", "0.0.0.0",
        "--port", str(PORT),
        "--logits-processors", "mineru_vl_utils:MinerULogitsProcessor",
        "--max-num-seqs", str(MAX_INPUTS),
        # Deve coincidere con l'adapter e con il target impostato dalla UI;
        # il nome vLLM è case-sensitive.
        "--served-model-name", "mineru2.5",
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
