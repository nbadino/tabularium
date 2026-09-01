"""Registro modelli: stato di installazione e download in background da Hugging Face.

Ogni adapter dichiara una ricetta di download (`capabilities.hf_repo` in
`model_adapters.py`). Il download gira in un sottoprocesso — stesso pattern già
collaudato in `trainer.start_run()` (Popen + thread che segue il log, stato
persistito su disco) — non un thread che chiama `huggingface_hub` in-process:
un processo separato si può interrompere in modo pulito e non blocca l'event
loop del backend durante un download da diversi GB.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from .model_adapters import get_adapter, list_adapters, supported_prefill_modes, supports_export
from . import paddle_runtime

_ACTIVE: dict[str, dict] = {}  # adapter_id -> {"proc": Popen, "log_file": Path}
_RUNTIME_ACTIVE: set[str] = set()

# Template Modal gestite dall'interfaccia CloudControlModal. È distinto da
# `local_serve_ready`: Unlimited-OCR, per esempio, è cloud-ready ma non si
# avvia con il server locale standard.
_CLOUD_TEMPLATES = {
    "monkeyocrv2-parsing": "monkeyocrv2",
    "mineru2.5": "mineru",
    "paddleocr-vl": "paddleocr-vl",
    "unlimited-ocr": "unlimited-ocr",
    "dots-ocr": "dots-ocr",
    "glm-ocr": "glm-ocr",
    "deepseek-ocr": "deepseek-ocr",
    "qwen3-vl-8b": "qwen3-vl",
}

_DOWNLOAD_SCRIPT = (
    "import sys\n"
    "from huggingface_hub import snapshot_download\n"
    "snapshot_download(\n"
    "    repo_id=sys.argv[1],\n"
    "    revision=(sys.argv[2] or None),\n"
    "    local_dir=sys.argv[3],\n"
    "    local_dir_use_symlinks=False,\n"
    ")\n"
    "print('__DOWNLOAD_DONE__')\n"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def models_dir(adapter_id: str) -> Path:
    """Cartella dei pesi di un adapter. Valida `adapter_id` contro il registro
    (`get_adapter` alza `ValueError` per id sconosciuti) prima di costruire il
    path: evita che un id malformato in una URL esca da `MODELS_DIR`."""
    get_adapter(adapter_id)
    return config.MODELS_DIR / adapter_id


def draft_dir(adapter_id: str) -> Path:
    """Cartella del draft per speculative decoding (DFlash su MonkeyOCRv2).

    Sta accanto ai pesi principali ma in una directory separata: `serve.py`
    ufficiale vuole due `-m`/`-d` distinti, e `is_installed()` non deve
    scambiare i file del draft per quelli del checkpoint.
    """
    get_adapter(adapter_id)
    return config.MODELS_DIR / f"{adapter_id}-draft"


def draft_installed(adapter_id: str) -> bool:
    d = draft_dir(adapter_id)
    return (d / "config.json").exists() and bool(
        list(d.glob("*.safetensors")) or list(d.glob("*.bin"))
    )


def _draft_unusable_marker(adapter_id: str) -> Path:
    return draft_dir(adapter_id) / ".unusable"


def mark_draft_unusable(adapter_id: str, reason: str) -> None:
    """Ricorda che su questa macchina il draft non è utilizzabile.

    Senza memoria, ogni avvio ripaga il tentativo fallito (~90 s su una GPU
    dove il draft non entra nel budget VRAM) prima di ripiegare. Il marcatore
    sta nella cartella del draft: cancellare o riscaricare il modello lo
    azzera, ed è il gesto giusto dopo un cambio di GPU.
    """
    marker = _draft_unusable_marker(adapter_id)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{_now()}\n{reason}\n", encoding="utf-8")
    except OSError:
        pass


def draft_unusable_reason(adapter_id: str) -> str | None:
    marker = _draft_unusable_marker(adapter_id)
    if not marker.exists():
        return None
    try:
        lines = marker.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "draft marcato come non utilizzabile"
    return lines[1] if len(lines) > 1 else "draft marcato come non utilizzabile"


def ensure_draft(adapter_id: str) -> Path | None:
    """Scarica il draft se manca e ritorna la sua cartella, o `None`.

    Sincrona di proposito: è ~170 MB e viene chiamata dalla fase di
    preparazione di `serve_manager.start()`, che gira già in background con
    stato osservabile. `None` quando l'adapter non ha un draft o il download
    fallisce: in quel caso il server parte semplicemente senza DFlash.
    """
    repo = get_adapter(adapter_id).capabilities.draft_hf_repo
    if not repo:
        return None
    if draft_unusable_reason(adapter_id) is not None:
        return None
    target = draft_dir(adapter_id)
    if draft_installed(adapter_id):
        return target
    target.mkdir(parents=True, exist_ok=True)
    log_file = target / ".download.log"
    proc = subprocess.run(
        [sys.executable, "-u", "-c", _DOWNLOAD_SCRIPT, repo, "", str(target)],
        stdout=log_file.open("wb"),
        stderr=subprocess.STDOUT,
    )
    if proc.returncode != 0 or not draft_installed(adapter_id):
        return None
    return target


def _state_file(adapter_id: str) -> Path:
    return models_dir(adapter_id) / ".download_state.json"


def _log_file(adapter_id: str) -> Path:
    return models_dir(adapter_id) / ".download.log"


def _write_state(adapter_id: str, **fields) -> None:
    path = _state_file(adapter_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    state: dict = {}
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
    state.update(fields)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_state(adapter_id: str) -> dict:
    path = _state_file(adapter_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def is_installed(adapter_id: str) -> bool:
    """Vero solo se il checkpoint ha sia `config.json` sia i pesi: un download
    interrotto a metà non deve sembrare 'installato' (config.json arriva
    presto, i file `.safetensors`/`.bin` grandi arrivano per ultimi)."""
    d = models_dir(adapter_id)
    if not d.is_dir():
        return False
    if not (d / "config.json").exists():
        return False
    return bool(list(d.glob("*.safetensors")) or list(d.glob("*.bin")))


def install_state(adapter_id: str) -> dict:
    d = models_dir(adapter_id)
    stored = _read_state(adapter_id)
    active = _ACTIVE.get(adapter_id)
    downloading = active is not None and active["proc"].poll() is None
    installed = is_installed(adapter_id)
    if installed:
        state_label = "installed"
    elif downloading:
        state_label = "downloading"
    else:
        state_label = stored.get("state", "absent")
    state = {
        "adapter_id": adapter_id,
        "installed": installed,
        "downloading": downloading,
        "path": str(d) if d.is_dir() else None,
        "size_bytes": _dir_size(d),
        # Quanto ci si aspetta in totale: è la stima dichiarata dall'adapter,
        # non una misura del repo remoto. Serve alla barra di avanzamento del
        # download, che va quindi mostrata come approssimata.
        "expected_bytes": _expected_bytes(adapter_id),
        "state": state_label,
        "error": None if installed or downloading else stored.get("error"),
        "updated_at": stored.get("updated_at"),
    }
    if adapter_id == "paddleocr-vl":
        runtime = paddle_runtime.status()
        state["runtime_ready"] = runtime["ready"]
        state["runtime_state"] = runtime["state"]
        state["runtime_error"] = runtime["error"]
        state["runtime_log_tail"] = runtime["log_tail"]
        if state["state"] == "installed" and not runtime["ready"]:
            state["state"] = "runtime_" + runtime["state"]
    return state


def ensure_runtime_async(adapter_id: str) -> None:
    """Configura in background il runtime ufficiale richiesto dall'adapter."""
    if adapter_id != "paddleocr-vl" or not is_installed(adapter_id):
        return
    runtime = paddle_runtime.status()
    if runtime["ready"] or adapter_id in _RUNTIME_ACTIVE:
        return
    _RUNTIME_ACTIVE.add(adapter_id)

    def worker() -> None:
        try:
            _write_state(adapter_id, state="runtime_installing", error=None)
            paddle_runtime.ensure_ready()
            _write_state(adapter_id, state="installed", error=None)
        except Exception as exc:  # noqa: BLE001
            _write_state(adapter_id, state="runtime_failed", error=str(exc))
        finally:
            _RUNTIME_ACTIVE.discard(adapter_id)

    threading.Thread(target=worker, name=f"runtime-{adapter_id}", daemon=True).start()


def _expected_bytes(adapter_id: str) -> int | None:
    try:
        approx = get_adapter(adapter_id).capabilities.approx_size_gb
    except (ValueError, AttributeError):
        return None
    return int(approx * 1024 ** 3) if approx else None


def vram_warning(adapter, size_bytes: int | None = None) -> str | None:
    """Avviso soft sulla dimensione del checkpoint vs. VRAM libera rilevata.

    Non è un preflight bloccante come `services/vram.py` per il training: qui
    l'utente può servire in locale qualunque modello scelga (compreso un
    modello custom di dimensione ignota a priori), esattamente come un
    'potrebbe non entrare' di LM Studio — un avviso informativo, mai un
    blocco. Usa la dimensione reale su disco se già scaricato (più accurata),
    altrimenti la stima dichiarata dall'adapter.
    """
    size_gb = (size_bytes / (1024 ** 3)) if size_bytes else adapter.capabilities.approx_size_gb
    if not size_gb:
        return None
    try:
        from . import trainer_metrics
        gpus = trainer_metrics.gpu_snapshot()
    except Exception:  # noqa: BLE001
        gpus = []
    if not gpus:
        return None
    # Capacità della scheda, non memoria libera adesso: avviare un modello ferma
    # sempre quello in servizio, quindi il libero del momento direbbe "non ci
    # sta" per un modello che invece ci starebbe benissimo da solo.
    total_mib = max(g["memory_total"] for g in gpus)
    # Margine di stima: pesi + KV cache/attivazioni, non un limite esatto (v.
    # `services/vram.py` per la stima puntuale usata nel training).
    needed_mib = size_gb * 1024 * 1.35
    if needed_mib <= total_mib:
        return None
    # Un'unica fonte di verità su "ci sta o no": la stessa funzione che decide
    # quanti GiB spostare in RAM al momento del serving. Duplicare qui una
    # soglia separata garantirebbe solo che prima o poi le due divergano.
    from . import serve_manager

    if serve_manager.cpu_offload_gib(size_bytes or int(size_gb * 1024 ** 3)) > 0:
        # I soli pesi si mangiano il budget. Non è più un blocco: dalla versione
        # con `--cpu-offload-gb` automatico (v. `serve_manager.cpu_offload_gib`)
        # il modello parte comunque, tenendo in RAM la parte eccedente. Il costo
        # è di banda PCIe, misurato ~10x più lento di un modello residente
        # (DeepSeek-OCR-2 16.7 tok/s e Unlimited-OCR 13.3 contro i 152.6 di
        # MonkeyOCRv2). Va detto, non nascosto — ma va detto come un costo, non
        # come un impedimento.
        return (
            f"il checkpoint pesa circa {size_gb:.1f} GB e la GPU ne ha "
            f"{total_mib / 1024:.1f}: i pesi non ci stanno tutti. Verrà servito "
            "spostando la parte eccedente in RAM, quindi parte ma è circa 10 "
            "volte più lento di un modello che entra interamente in VRAM. Per "
            "lavorare a piena velocità usalo su GPU remota (Cloud)."
        )
    return (
        f"il checkpoint pesa circa {size_gb:.1f} GB, la GPU ne ha "
        f"{total_mib / 1024:.1f}: margine stretto per il contesto. Puoi provarlo "
        "comunque; se va in out-of-memory, riduci max-model-len (la cache KV per "
        "richiesta) — non gpu-memory-utilization, che ridurrebbe ancora il "
        "budget disponibile."
    )


def _docker_gpu_blocker() -> str | None:
    from . import serve_manager

    return serve_manager.docker_gpu_blocker()


def list_models() -> list[dict]:
    items = []
    for cap in list_adapters():
        adapter = get_adapter(cap["adapter_id"])
        ensure_runtime_async(cap["adapter_id"])
        modes = supported_prefill_modes(adapter)
        try:
            local_serve_ready = adapter.serve_command("", 8888) is not None
        except (NotImplementedError, TypeError):
            local_serve_ready = False
        state = install_state(cap["adapter_id"])
        items.append({
            **cap,
            **state,
            **modes,
            "export_ready": supports_export(adapter),
            "local_serve_ready": local_serve_ready,
            "cloud_serve_ready": cap["adapter_id"] in _CLOUD_TEMPLATES,
            "cloud_template": _CLOUD_TEMPLATES.get(cap["adapter_id"]),
            "download_only": (
                not local_serve_ready
                and cap["adapter_id"] not in _CLOUD_TEMPLATES
                and not modes["supports_native"]
            ),
            "vram_warning": vram_warning(adapter, state.get("size_bytes") or None),
            "draft_repo": cap.get("draft_hf_repo") or None,
            "draft_installed": bool(cap.get("draft_hf_repo")) and draft_installed(cap["adapter_id"]),
            "draft_unusable": draft_unusable_reason(cap["adapter_id"]) if cap.get("draft_hf_repo") else None,
            # Un adapter servito via Docker dipende da prerequisiti di sistema
            # che non possiamo installare noi: dichiararli prima del click è
            # meglio che un `exit code 125` dopo.
            "local_serve_blocker": (
                _docker_gpu_blocker() if cap.get("serve_backend") == "docker-vllm-openai" else None
            ),
        })
    return items


def start_download(adapter_id: str) -> dict:
    adapter = get_adapter(adapter_id)
    repo = adapter.capabilities.hf_repo
    if not repo:
        raise ValueError(f"adapter '{adapter_id}' non ha un repo Hugging Face configurato")

    existing = _ACTIVE.get(adapter_id)
    if existing is not None and existing["proc"].poll() is None:
        return install_state(adapter_id)  # download già in corso, idempotente

    target = models_dir(adapter_id)
    target.mkdir(parents=True, exist_ok=True)
    log_file = _log_file(adapter_id)
    revision = adapter.capabilities.hf_revision or ""

    _write_state(adapter_id, state="downloading", error=None, updated_at=_now(), hf_repo=repo)
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", _DOWNLOAD_SCRIPT, repo, revision, str(target)],
        stdout=log_file.open("wb"),
        stderr=subprocess.STDOUT,
    )
    _ACTIVE[adapter_id] = {"proc": proc, "log_file": log_file}
    threading.Thread(target=_monitor, args=(adapter_id,), daemon=True).start()
    return install_state(adapter_id)


def _monitor(adapter_id: str) -> None:
    handle = _ACTIVE.get(adapter_id)
    if not handle:
        return
    code = handle["proc"].wait()
    ok = code == 0 and is_installed(adapter_id)
    _write_state(
        adapter_id,
        state="installed" if ok else "failed",
        error=None if ok else f"download terminato con codice di uscita {code}",
        updated_at=_now(),
    )
    if ok and adapter_id == "paddleocr-vl":
        try:
            _write_state(adapter_id, state="runtime_installing", error=None)
            paddle_runtime.ensure_ready()
            _write_state(adapter_id, state="installed", error=None)
        except Exception as exc:  # noqa: BLE001
            _write_state(adapter_id, state="runtime_failed", error=str(exc))
    _ACTIVE.pop(adapter_id, None)


def download_log_tail(adapter_id: str, n: int = 4000) -> str:
    log_file = _log_file(adapter_id)
    if not log_file.exists():
        return ""
    try:
        with log_file.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - n))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def cancel_download(adapter_id: str) -> dict:
    get_adapter(adapter_id)
    handle = _ACTIVE.get(adapter_id)
    if handle and handle["proc"].poll() is None:
        handle["proc"].terminate()
        try:
            handle["proc"].wait(timeout=10)
        except subprocess.TimeoutExpired:
            handle["proc"].kill()
    _ACTIVE.pop(adapter_id, None)
    _write_state(adapter_id, state="cancelled", updated_at=_now())
    return install_state(adapter_id)


def delete_model(adapter_id: str) -> dict:
    get_adapter(adapter_id)
    if _ACTIVE.get(adapter_id) and _ACTIVE[adapter_id]["proc"].poll() is None:
        raise ValueError("download in corso: fermalo prima di cancellare")
    d = models_dir(adapter_id)
    if d.exists():
        shutil.rmtree(d)
    # Il draft segue il checkpoint: lasciarlo indietro terrebbe in vita anche il
    # marcatore `.unusable`, che deve poter essere azzerato riscaricando.
    draft = draft_dir(adapter_id)
    if draft.exists():
        shutil.rmtree(draft)
    return install_state(adapter_id)
