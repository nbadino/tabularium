"""Cosa può fare questa macchina, in locale.

Un solo posto decide quali runtime di serving locale esistono per la macchina
su cui gira Tabularium. Il resto dell'app — registro modelli, hub, pagina di
riconoscimento — legge di qui invece di dedurre le capacità dall'OS sparso in
controlli `platform.system() == ...`.

Perché serve: lo stesso modello del registro non è locale ovunque. vLLM è un
progetto CUDA-first (su Windows lo si usa via WSL2) e non gira nativamente su
Apple Silicon; lì il percorso nativo è MLX, che copre le architetture OCR
pubblicate per Metal ma non tutte. Dire «modello locale» senza dire *dove*
produce un pulsante che fallisce; dire «solo Linux» nasconde i modelli che su
un Mac girano benissimo.

I codici restituiti sono stabili e la UI li traduce (§13.11): qui non c'è
prosa destinata allo schermo, solo fatti verificabili sulla macchina.
"""
from __future__ import annotations

import os
import platform
import sys
from typing import Iterable

RUNTIME_VLLM = "vllm"
RUNTIME_MLX = "mlx-vlm"
LOCAL_RUNTIMES = (RUNTIME_VLLM, RUNTIME_MLX)

# Codici stabili delle cause.
REASON_CUDA_REQUIRED = "cuda_required"
REASON_NO_NVIDIA_GPU = "no_nvidia_gpu"
REASON_APPLE_SILICON_REQUIRED = "apple_silicon_required"
REASON_NO_LOCAL_RUNTIME = "no_local_runtime"
REASON_MODEL_UNSUPPORTED = "model_unsupported_locally"
REASON_INSUFFICIENT_MEMORY = "insufficient_memory"

# Frazione della memoria che si lascia al sistema, ai pesi caricati e al KV
# cache: la stima qui sotto è deliberatamente prudente e serve a dire «non ci
# sta» con sicurezza, non «ci sta» con sicurezza.
_MEMORY_HEADROOM = 0.6


def is_apple_silicon() -> bool:
    """Vero su macOS con CPU arm64 (M1 e successivi)."""
    return sys.platform == "darwin" and platform.machine() == "arm64"


def total_memory_gb() -> float | None:
    """Memoria fisica della macchina in GB, o None se non deducibile.

    Su Apple Silicon è la memoria *unificata*: la stessa che la GPU vede come
    VRAM. Non serve `sysctl`: `sysconf` è portabile su macOS e Linux.
    """
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):
        return None
    if not isinstance(pages, int) or not isinstance(page_size, int) or pages <= 0:
        return None
    return round(pages * page_size / (1024 ** 3), 1)


def cuda_devices() -> list[dict]:
    """GPU NVIDIA visibili, via `nvidia-smi` (lista vuota se assente).

    Riusa la telemetria del training: è la stessa misura, non una seconda
    verità da tenere allineata.
    """
    from . import trainer_metrics

    return trainer_metrics.gpu_snapshot()


def runtime_support() -> dict[str, dict]:
    """Quali runtime questa macchina può ospitare, e perché no quando no.

    «Supportato» non vuol dire «già installato»: i runtime locali sono
    provisionati da Tabularium al primo avvio (`local_runtime`,
    `mlx_runtime`), quindi qui si risponde alla domanda che l'utente si pone
    davvero — *questo* computer può, sì o no.
    """
    system = platform.system()
    devices = cuda_devices()
    cuda_ok = bool(devices)

    if system == "Linux":
        vllm = {
            "supported": cuda_ok,
            "reason": None if cuda_ok else REASON_NO_NVIDIA_GPU,
        }
    else:
        # Windows usa WSL2, macOS non ha CUDA: fuori da Linux il percorso vLLM
        # non è quello nativo.
        vllm = {
            "supported": False,
            "reason": REASON_CUDA_REQUIRED,
            "wsl": system == "Windows",
        }

    mlx = {
        "supported": is_apple_silicon(),
        "reason": None if is_apple_silicon() else REASON_APPLE_SILICON_REQUIRED,
    }
    return {RUNTIME_VLLM: vllm, RUNTIME_MLX: mlx}


def summary() -> dict:
    """Fotografia della macchina, senza decisioni sui modelli."""
    support = runtime_support()
    return {
        "platform": platform.system(),
        "os_version": platform.mac_ver()[0] or platform.release(),
        "arch": platform.machine(),
        "apple_silicon": is_apple_silicon(),
        "memory_gb": total_memory_gb(),
        "cuda": {
            "available": support[RUNTIME_VLLM]["supported"],
            "devices": cuda_devices(),
        },
        "runtimes": support,
        "usable_runtimes": [name for name in LOCAL_RUNTIMES if support[name]["supported"]],
    }


def fits_in_memory(
    approx_size_gb: float | None,
    machine: dict,
    *,
    runtime: str | None = None,
    mlx_weight_factor: float | None = None,
) -> bool:
    """Stima prudente: i pesi devono starci con margine.

    `approx_size_gb` è la dimensione dei pesi sorgente dichiarata dall'adapter.
    I checkpoint MLX pubblicati quantizzati occupano una frazione; un backend
    MLX nativo che carica i pesi sorgente può sovrascrivere quel fattore per
    evitare che la stima lo dia per più piccolo del vero. Confrontare
    sempre il valore pieno contro la memoria della macchina produrrebbe un
    falso «non ci sta» proprio sui modelli che su un Mac girano meglio — e
    l'utente ha chiesto di sapere cosa è possibile, non una risposta
    prudente ma sbagliata.

    Resta una stima, non una misura: il KV cache e il picco di prefill
    dipendono dall'immagine. Serve a dire «non ci sta» con sicurezza, non
    «ci sta» con sicurezza.
    """
    if not approx_size_gb:
        return True
    device_vram = [
        gpu.get("memory_total") for gpu in machine.get("cuda", {}).get("devices", []) if gpu.get("memory_total")
    ]
    if device_vram:
        # nvidia-smi riporta MiB.
        available_gb = max(device_vram) / 1024 * _MEMORY_HEADROOM
    else:
        memory = machine.get("memory_gb")
        if not memory:
            return True
        available_gb = memory * _MEMORY_HEADROOM
    return approx_size_gb * weight_factor(runtime, mlx_weight_factor) <= available_gb


def weight_factor(runtime: str | None, mlx_weight_factor: float | None = None) -> float:
    """Quanto occupano i pesi serviti rispetto ai pesi pieni del repo.

    `mlx-vlm` serve i checkpoint `mlx-community` quantizzati a 4 bit
    (≈0,3× il bf16); vLLM serve i pesi pieni, con un margine per i buffer.
    """
    if runtime == RUNTIME_MLX:
        return mlx_weight_factor if mlx_weight_factor is not None else 0.35
    return 1.2


def pick_serve_runtime(capabilities, machine: dict | None = None) -> str:
    """Il runtime locale da usare per servire questo modello su questa macchina.

    Non è un verdetto — quello è `plan_local`, che può dire «qui non gira» — è
    la scelta operativa di chi avvia il server. Su Apple Silicon, dove vLLM non
    gira, si usa MLX se il modello ha un checkpoint MLX; altrove resta il
    percorso vLLM, storico e verificato. Così l'aggiunta del percorso Apple non
    tocca Linux e Windows, dove il comportamento resta quello di prima.
    """
    machine = machine or summary()
    mlx_repo = getattr(capabilities, "local_mlx_repo", "") or ""
    declares = tuple(getattr(capabilities, "local_runtimes", ()) or ())
    if machine["apple_silicon"] and RUNTIME_MLX in declares and mlx_repo:
        return RUNTIME_MLX
    return RUNTIME_VLLM


def plan_local(
    local_runtimes: Iterable[str],
    *,
    machine: dict | None = None,
    approx_size_gb: float | None = None,
    mlx_weight_factor: float | None = None,
) -> dict:
    """Il verdetto locale per un modello su questa macchina.

    `runnable` dice se il modello può girare qui. `runtime` è quello che si
    userà. `reason` è il codice stabile del perché no: un modello può essere
    non eseguibile perché *questa macchina* non ha il runtime (CUDA assente,
    niente Apple Silicon), perché il modello non ha un percorso locale per
    alcun runtime che la macchina abbia, o perché i pesi non ci stanno.
    """
    machine = machine or summary()
    wanted = tuple(local_runtimes)
    if not wanted:
        return {"runnable": False, "runtime": None, "reason": REASON_MODEL_UNSUPPORTED}
    usable = [name for name in wanted if machine["runtimes"].get(name, {}).get("supported")]
    if not usable:
        # Nessun runtime del modello è ospitabile qui: se la macchina non ne
        # supporta nemmeno uno il problema è la macchina, altrimenti è il
        # modello a non avere un percorso locale su questa piattaforma.
        if not machine["usable_runtimes"]:
            return {"runnable": False, "runtime": None, "reason": REASON_NO_LOCAL_RUNTIME}
        return {"runnable": False, "runtime": None, "reason": REASON_MODEL_UNSUPPORTED}
    runtime = usable[0]
    if not fits_in_memory(
        approx_size_gb, machine, runtime=runtime, mlx_weight_factor=mlx_weight_factor,
    ):
        return {"runnable": False, "runtime": None, "reason": REASON_INSUFFICIENT_MEMORY}
    return {"runnable": True, "runtime": runtime, "reason": None}
