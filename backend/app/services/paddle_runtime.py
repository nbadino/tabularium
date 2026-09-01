"""Runtime ufficiale PaddleOCR per il document parser PaddleOCR-VL.

I pesi VLM e il pipeline Paddle hanno dipendenze diverse: il primo vive nel
runtime vLLM condiviso, il secondo in un ambiente isolato. L'installazione è
avviata dal monitor del download e resta osservabile su file, come il download
dei pesi.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import venv
from datetime import datetime, timezone
from pathlib import Path

from .. import config


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


def ready() -> bool:
    try:
        check = subprocess.run(
            [str(python_bin()), "-c", "from paddleocr import PaddleOCRVL"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return check.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def status() -> dict:
    stored = {}
    if _state_file().exists():
        try:
            stored = json.loads(_state_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    is_ready = ready()
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
    root = _dir()
    root.mkdir(parents=True, exist_ok=True)
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
                # PaddleOCR-VL requires PaddlePaddle 3.x.  The default PyPI
                # resolver can otherwise select the old 2.6 CPU/GPU build,
                # which fails inside PaddleX's document parser.
                subprocess.run(
                    [str(python_bin()), "-m", "pip", "install",
                     "paddlepaddle-gpu==3.2.0",
                     "-i", "https://www.paddlepaddle.org.cn/packages/stable/cu118/"],
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
    _write_state(state="ready", error=None)
