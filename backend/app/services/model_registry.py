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
from .model_adapters import get_adapter, list_adapters

_ACTIVE: dict[str, dict] = {}  # adapter_id -> {"proc": Popen, "log_file": Path}

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
    return {
        "adapter_id": adapter_id,
        "installed": installed,
        "downloading": downloading,
        "path": str(d) if d.is_dir() else None,
        "size_bytes": _dir_size(d),
        "state": state_label,
        "error": None if installed or downloading else stored.get("error"),
        "updated_at": stored.get("updated_at"),
    }


def list_models() -> list[dict]:
    return [{**cap, **install_state(cap["adapter_id"])} for cap in list_adapters()]


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
    return install_state(adapter_id)
