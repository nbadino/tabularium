"""Modal: deployment serverless del server vLLM di MonkeyOCRv2-Parsing.

Template per la dashboard Tabularium: il profilo predefinito mantiene una GPU
calda (`min_containers=1`) per privilegiare la latenza; il costo è a consumo.
Impostando `TABULARIUM_MODAL_MIN_CONTAINERS=0` si abilita lo scale-to-zero.

Requisiti verificati sul repo ufficiale `Yuliang-Liu/MonkeyOCRv2`:
  - `parsing/serve.py` esige vLLM >= 0.25 (registrazione del modello tramite
    `modeling.modeling_monkeyocrv2_vllm`) oppure vLLM == 0.11 (legacy).
  - Il checkpoint è `zenosai/MonkeyOCRv2-B-Parsing` (o `-S-Parsing`) da HF.
  - Il modello gira in bfloat16: serve una GPU Ampere o successiva
    (compute capability >= 8.0). La T4 NON è utilizzabile: vLLM rifiuta il
    bfloat16 su compute capability < 8.0. Default qui: L4.

Uso:
  pip install modal
  modal setup                                   # una sola volta (autenticazione)
  modal deploy scripts/cloud/modal_vllm.py      # crea/aggiorna l'endpoint

L'URL dell'endpoint è stampato da `modal deploy` e ha forma:
  https://<WORKSPACE>--tabularium-vllm-serve.modal.run
Nella card "Inferenza Modello & Cloud Offloading" di Tabularium usa:
  https://<WORKSPACE>--tabularium-vllm-serve.modal.run/v1

Costi di riferimento (verificati 2026-08, modal.com/pricing):
  L4  ≈ 0,80 $/h attiva  ·  A10 ≈ 1,10 $/h  ·  0 $ a riposo.
  Piano Starter: 30 $ di crediti gratuiti al mese.

Configurazione opzionale (env, letti al deploy):
  TABULARIUM_MODAL_GPU       GPU Modal (default "L4"; es. "A10G")
  TABULARIUM_MODAL_MODEL     checkpoint HF (default MonkeyOCRv2-B-Parsing)
  TABULARIUM_MODAL_DFLASH    1 (default): abilita il draft ufficiale 2x
  TABULARIUM_MODAL_MIN_CONTAINERS 1 (default): elimina il cold start
  TABULARIUM_MODAL_MAX_INPUTS 4 (default): concorrenza per container
  TABULARIUM_VLLM_API_KEY    se impostata, il server esige questa Bearer key
"""

import os
import subprocess
from pathlib import Path

import modal

APP_NAME = "tabularium-vllm"
MODEL_ID = os.environ.get("TABULARIUM_MODAL_MODEL", "zenosai/MonkeyOCRv2-B-Parsing")
DFLASH_MODEL_ID = os.environ.get(
    "TABULARIUM_MODAL_DFLASH_MODEL", "zenosai/MonkeyOCRv2-B-Parsing-DFlash"
)
GPU = os.environ.get("TABULARIUM_MODAL_GPU", "L4")
PORT = 8888
USE_DFLASH = os.environ.get("TABULARIUM_MODAL_DFLASH", "1").strip() != "0"
# DFlash è documentato dal progetto MonkeyOCRv2 con vLLM 0.25.1 + CUDA 12.9.
# Il pin resta sovrascrivibile per esperimenti, ma non si usa per errore una
# wheel più recente con una ricetta DFlash non compatibile.
VLLM_VERSION = os.environ.get("TABULARIUM_VLLM_VERSION", "0.25.1")
MIN_CONTAINERS = int(os.environ.get("TABULARIUM_MODAL_MIN_CONTAINERS", "0"))
MAX_CONTAINERS = int(os.environ.get("TABULARIUM_MODAL_MAX_CONTAINERS", "2"))
MAX_INPUTS = int(os.environ.get("TABULARIUM_MODAL_MAX_INPUTS", "4"))
DFLASH_TOKENS = int(os.environ.get("TABULARIUM_MODAL_DFLASH_TOKENS", "16"))

# Volume persistente: il checkpoint (~3 GB) si scarica una sola volta.
weights = modal.Volume.from_name("monkeyocrv2-weights", create_if_missing=True)
vllm_cache = modal.Volume.from_name("monkeyocrv2-vllm-cache", create_if_missing=True)

image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.11"
    )
    .apt_install("git", "gcc", "g++")
    # vLLM con dipendenze CUDA; hf_transfer accelera il download dei pesi.
    .pip_install(
        f"vllm=={VLLM_VERSION}",
        "huggingface_hub[hf_transfer]",
        extra_index_url="https://wheels.vllm.ai/0.25.1/cu129",
    )
    # Repo ufficiale: NON si modifica e NON è un pacchetto pip (nessun
    # setup.py/pyproject.toml alla radice). serve.py risolve i propri import
    # dalla sua cartella: basta clonarlo.
    .run_commands(
        "git clone --depth 1 https://github.com/Yuliang-Liu/MonkeyOCRv2 /opt/MonkeyOCRv2",
    )
    .env({
        # Questa immagine contiene il CUDA toolkit: abilitiamo il sampler
        # FlashInfer, evitando il collo di bottiglia del sampler Python/nativo.
        "VLLM_USE_FLASHINFER_SAMPLER": "1",
        "HF_XET_HIGH_PERFORMANCE": "1",
        "MAX_JOBS": "2",
    })
)

app = modal.App(APP_NAME)


@app.function(
    image=image,
    gpu=GPU,
    timeout=3600,
    # Due container consentono rolling deploy e assorbono una seconda
    # richiesta durante un cold start; il limite resta configurabile per chi
    # vuole una sola GPU (`TABULARIUM_MODAL_MAX_CONTAINERS=1`).
    max_containers=MAX_CONTAINERS,
    min_containers=MIN_CONTAINERS,
    # Il container resta caldo 15 minuti dopo l'ultima richiesta: i cold start
    # (caricamento del modello, alcuni minuti) non si ripetono a ogni chiamata.
    scaledown_window=900,
    volumes={
        "/weights": weights,
        "/root/.cache/vllm": vllm_cache,
    },
)
@modal.concurrent(max_inputs=MAX_INPUTS)
@modal.web_server(PORT, startup_timeout=1800)
def serve():
    """Avvia parsing/serve.py del repo ufficiale su 0.0.0.0:8888.

    `web_server` (v. modal.com/docs/sdk/py/latest/web_server) sonda la porta
    dall'esterno per capire quando il container è pronto: la funzione deve
    solo AVVIARE il processo e tornare, come da esempio ufficiale
    (modal.com/docs/guide/webhooks) e come fanno tutti gli esempi `@app.server`
    con `@modal.enter()`. Prima usava `subprocess.run(check=True)`, che
    blocca finché vLLM non esce — cioè mai, finché serve traffico: la
    funzione non tornava mai e Modal non segnava il container pronto (la
    dashboard restava su "starting", ogni richiesta restava in coda o
    otteneva un redirect, pur con vLLM realmente su e funzionante nei log —
    riprodotto e confermato: stesso comportamento a ogni deploy). Con
    `Popen` la funzione avvia il processo e torna, lasciandolo vivo in
    background: esattamente il pattern degli esempi ufficiali.
    """
    from huggingface_hub import snapshot_download

    # MODEL_ID può puntare anche a -S-Parsing o a un checkpoint fine-tuned:
    # non riusare una cartella B hard-coded, altrimenti un cambio env continua
    # silenziosamente a servire i pesi precedenti.
    model_dir = f"/weights/{Path(MODEL_ID).name}"
    if not os.path.isdir(model_dir) or not os.listdir(model_dir):
        print(f">> Scarico il checkpoint {MODEL_ID} in {model_dir}")
        snapshot_download(repo_id=MODEL_ID, local_dir=model_dir)
        weights.commit()

    draft_dir = f"/weights/{Path(DFLASH_MODEL_ID).name}"
    if USE_DFLASH:
        if MODEL_ID != "zenosai/MonkeyOCRv2-B-Parsing":
            raise RuntimeError(
                "DFlash è supportato dalla ricetta ufficiale solo con "
                "MonkeyOCRv2-B-Parsing; imposta TABULARIUM_MODAL_DFLASH=0 "
                "per un altro checkpoint"
            )
        if not os.path.isdir(draft_dir) or not os.listdir(draft_dir):
            print(f">> Scarico il draft DFlash {DFLASH_MODEL_ID} in {draft_dir}")
            snapshot_download(repo_id=DFLASH_MODEL_ID, local_dir=draft_dir)
            weights.commit()

    argv = [
        "python",
        "/opt/MonkeyOCRv2/parsing/serve.py",
        "--model-path",
        model_dir,
        "--host",
        "0.0.0.0",
        "--port",
        str(PORT),
        # Stessi flag del serve_model.sh locale: contesto ampio per END2END.
        "--gpu-memory-utilization",
        "0.9",
        "--max-model-len",
        "24576",
        "--max-num-batched-tokens",
        "24576",
        "--max-num-seqs",
        str(MAX_INPUTS),
        # serve.py inoltra il resto degli argomenti a `vllm serve`.
    ]
    if USE_DFLASH:
        # serve.py ufficiale espone il draft con l'opzione breve -d.
        argv.extend(["-d", draft_dir, "--dflash-num-speculative-tokens", str(DFLASH_TOKENS)])
    api_key = os.environ.get("TABULARIUM_VLLM_API_KEY", "").strip()
    if api_key:
        argv.extend(["--", "--api-key", api_key])

    print(">> Avvio:", " ".join(argv))
    proc = subprocess.Popen(argv)

    # Non blocchiamo sulla readiness (ci pensa `startup_timeout` sondando la
    # porta), ma un crash immediato (es. checkpoint corrotto, flag rifiutato
    # da vLLM) va segnalato subito invece di far scadere 30 minuti di attesa.
    import time
    import urllib.request

    deadline = time.monotonic() + 5 * 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"parsing/serve.py uscito con codice {proc.returncode} durante l'avvio")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2)
            print(">> vLLM pronto su /health")
            break
        except Exception:  # noqa: BLE001
            time.sleep(2)
