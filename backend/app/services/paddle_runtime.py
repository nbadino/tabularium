"""Runtime ufficiale PaddleOCR per il document parser PaddleOCR-VL.

I pesi VLM e il pipeline Paddle hanno dipendenze diverse: il primo vive nel
runtime vLLM condiviso, il secondo in un ambiente isolato. L'installazione è
avviata dal monitor del download e resta osservabile su file, come il download
dei pesi.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
import venv
from datetime import datetime, timezone
from pathlib import Path

from .. import config


# `status()` viene letto per ogni adapter mentre si costruisce il registro.
# Importare PaddleOCR in un processo separato è costoso; il risultato vale per
# tutta la risposta e per le aperture ravvicinate del registro.
_READY_CACHE_TTL = 15.0
_ready_cache: tuple[float, bool] | None = None


def _dir() -> Path:
    return config.ROOT_DIR / "paddle-runtime"


def python_bin() -> Path:
    return _dir() / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _state_file() -> Path:
    return _dir() / ".install_state.json"


def log_path() -> Path:
    return _dir() / ".install.log"


def _write_state(**fields) -> None:
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {}
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    state.update(fields, updated_at=datetime.now(timezone.utc).isoformat())
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def log_tail(n: int = 4000) -> str:
    path = log_path()
    if not path.exists():
        return ""
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - n))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def ready(*, force: bool = False) -> bool:
    global _ready_cache
    now = time.monotonic()
    if not force and _ready_cache is not None and now - _ready_cache[0] < _READY_CACHE_TTL:
        return _ready_cache[1]
    try:
        check = subprocess.run(
            [str(python_bin()), "-c", "from paddleocr import PaddleOCRVL"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        result = check.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        result = False
    _ready_cache = (time.monotonic(), result)
    return result


def status(*, probe: bool = True) -> dict:
    """Restituisce lo stato del runtime.

    Il catalogo modelli passa ``probe=False``: importare PaddleOCR in un
    processo separato durante una semplice apertura della pagina è lavoro di
    avvio, non lettura di catalogo. Il worker di installazione e il percorso
    di inferenza usano invece il probe reale.
    """
    stored = {}
    if _state_file().exists():
        try:
            stored = json.loads(_state_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    if probe:
        is_ready = ready()
    else:
        is_ready = stored.get("state") == "ready" or (
            _ready_cache is not None and _ready_cache[1]
        )
    return {
        "ready": is_ready,
        "state": "ready" if is_ready else stored.get("state", "absent"),
        "error": None if is_ready else stored.get("error"),
        "log_tail": log_tail(),
    }


def ensure_ready() -> None:
    """Installa il pipeline ufficiale PaddleOCR document parser."""
    if ready():
        return
    global _ready_cache
    _ready_cache = None
    root = _dir()
    root.mkdir(parents=True, exist_ok=True)
    # L'ambiente si crea **prima** di scrivere stato e log: `clear=True`
    # cancella il contenuto della cartella, quindi qualunque file scritto
    # prima viene rimosso insieme al venv precedente. Effetto osservato:
    # durante l'installazione la UI leggeva `absent` e il log era vuoto,
    # proprio nei minuti in cui servono.
    try:
        venv.EnvBuilder(with_pip=True, clear=True).create(str(root))
    except Exception as exc:  # noqa: BLE001
        _write_state(state="failed", error=str(exc))
        raise RuntimeError(f"installazione PaddleOCR fallita: {exc}") from exc
    _write_state(state="installing", error=None)
    with log_path().open("ab") as log:
        log.write(b"\n== Tabularium: installazione PaddleOCR document parser ==\n")
        log.flush()
        try:
            venv.EnvBuilder(with_pip=True, clear=True).create(str(root))
            subprocess.run(
                [str(python_bin()), "-m", "pip", "install", "--upgrade", "pip"],
                check=True, stdout=log, stderr=subprocess.STDOUT,
            )
            override = os.environ.get("TABULARIUM_PADDLE_PACKAGES")
            if override:
                subprocess.run(
                    [str(python_bin()), "-m", "pip", "install", *override.split()],
                    check=True, stdout=log, stderr=subprocess.STDOUT,
                )
            else:
                # PaddleOCR-VL richiede PaddlePaddle 3.x. Due build diverse a
                # seconda della macchina, e non è un dettaglio di gusto:
                # - su Linux con CUDA la build GPU dall'indice Paddle, perché
                #   il resolver PyPI sceglierebbe la vecchia 2.6, che fallisce
                #   dentro il document parser di PaddleX;
                # - su Apple Silicon la build GPU non esiste affatto (nessuna
                #   wheel macOS nell'indice CUDA): lì si prende la CPU arm64 da
                #   PyPI. Senza questo ramo il percorso ufficiale di
                #   PaddleOCR-VL su un Mac falliva con un errore di
                #   installazione, e l'utente restava in un vicolo cieco.
                if sys.platform == "darwin" and platform.machine() == "arm64":
                    paddle_pin = [
                        "paddlepaddle==3.3.1",
                    ]
                else:
                    paddle_pin = [
                        "paddlepaddle-gpu==3.2.0",
                        "-i", "https://www.paddlepaddle.org.cn/packages/stable/cu118/",
                    ]
                subprocess.run(
                    [str(python_bin()), "-m", "pip", "install", *paddle_pin],
                    check=True, stdout=log, stderr=subprocess.STDOUT,
                )
                subprocess.run(
                    [str(python_bin()), "-m", "pip", "install", "numpy<2.4", "paddleocr[doc-parser]"],
                    check=True, stdout=log, stderr=subprocess.STDOUT,
                )
            subprocess.run(
                [str(python_bin()), "-c", "from paddleocr import PaddleOCRVL"],
                check=True, stdout=log, stderr=subprocess.STDOUT,
            )
        except Exception as exc:  # noqa: BLE001
            _write_state(state="failed", error=str(exc))
            raise RuntimeError(f"installazione PaddleOCR fallita: {exc}") from exc
    _ready_cache = (time.monotonic(), True)
    _write_state(state="ready", error=None)
