"""Modal: deployment serverless del server vLLM di MonkeyOCRv2-Parsing.

Template a chiamata per la dashboard Tabularium: la GPU si accende quando
arriva una richiesta e si spegne dopo il periodo di "caldo" (`scaledown_window`).
Il costo è solo per secondo di GPU effettivamente usato.

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
  TABULARIUM_VLLM_API_KEY    se impostata, il server esige questa Bearer key
"""

import os
import subprocess

import modal

APP_NAME = "tabularium-vllm"
MODEL_ID = os.environ.get("TABULARIUM_MODAL_MODEL", "zenosai/MonkeyOCRv2-B-Parsing")
GPU = os.environ.get("TABULARIUM_MODAL_GPU", "L4")
PORT = 8888
VLLM_VERSION = "0.28.0"  # riga >= 0.25 richiesta da parsing/serve.py

# Volume persistente: il checkpoint (~3 GB) si scarica una sola volta.
weights = modal.Volume.from_name("monkeyocrv2-weights", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "gcc", "g++")
    # vLLM con dipendenze CUDA; hf_transfer accelera il download dei pesi.
    .pip_install(
        f"vllm=={VLLM_VERSION}",
        "huggingface_hub[hf_transfer]",
    )
    # Repo ufficiale: NON si modifica, si installa così com'è (serve.py + modeling).
    .run_commands(
        "git clone --depth 1 https://github.com/Yuliang-Liu/MonkeyOCRv2 /opt/MonkeyOCRv2",
        "cd /opt/MonkeyOCRv2 && pip install -e .",
    )
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "MAX_JOBS": "2"})
)

app = modal.App(APP_NAME)


@app.function(
    image=image,
    gpu=GPU,
    timeout=3600,
    # Il container resta caldo 15 minuti dopo l'ultima richiesta: i cold start
    # (caricamento del modello, alcuni minuti) non si ripetono a ogni chiamata.
    scaledown_window=900,
    volumes={"/weights": weights},
)
@modal.web_server(PORT, startup_timeout=1800)
def serve():
    """Avvia parsing/serve.py del repo ufficiale su 0.0.0.0:8888."""
    from huggingface_hub import snapshot_download

    model_dir = "/weights/MonkeyOCRv2-B-Parsing"
    if not os.path.isdir(model_dir) or not os.listdir(model_dir):
        print(f">> Scarico il checkpoint {MODEL_ID} in {model_dir}")
        snapshot_download(repo_id=MODEL_ID, local_dir=model_dir)
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
        "8",
        # serve.py inoltra il resto degli argomenti a `vllm serve`.
    ]
    api_key = os.environ.get("TABULARIUM_VLLM_API_KEY", "").strip()
    if api_key:
        argv.extend(["--", "--api-key", api_key])

    print(">> Avvio:", " ".join(argv))
    subprocess.run(argv, check=True)
