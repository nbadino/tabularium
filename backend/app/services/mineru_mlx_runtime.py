"""Pinned native MinerU MLX runtime for Apple Silicon.

Keep the producer's runtime isolated so preparing MinerU cannot downgrade or
otherwise change the shared Paddle/Qwen MLX environment. The official 2.0
extra carries its compatible mlx-vlm and Transformers requirements.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import venv
from datetime import datetime, timezone
from pathlib import Path

from .. import config

PACKAGES = ["fastapi", "uvicorn", "mineru-vl-utils[mlx]==2.0.5"]
_GIB = 1024 ** 3


def _dir() -> Path:
    return config.ROOT_DIR / "mineru-mlx-runtime"


def bin_dir() -> Path:
    return _dir() / ("Scripts" if sys.platform == "win32" else "bin")


def python_bin() -> Path:
    return bin_dir() / ("python.exe" if sys.platform == "win32" else "python")


def _state_file() -> Path:
    return _dir() / ".install_state.json"


def _log_file() -> Path:
    return _dir() / ".install.log"


def log_tail(n: int = 4000) -> str:
    path = _log_file()
    if not path.exists():
        return ""
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - n))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _write_state(**fields) -> None:
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state.update(fields, updated_at=datetime.now(timezone.utc).isoformat())
    path.write_text(json.dumps(state), encoding="utf-8")


def is_ready() -> bool:
    if not python_bin().exists():
        return False
    try:
        probe = subprocess.run(
            [
                str(python_bin()), "-c",
                "import importlib.metadata as m,importlib.util,sys; "
                "names=('mlx_vlm','mineru_vl_utils','fastapi','uvicorn'); "
                "ok=all(importlib.util.find_spec(n) for n in names); "
                "ok=ok and m.version('mineru-vl-utils')=='2.0.5'; "
                "ok=ok and m.version('mlx-vlm').split('.')[0]=='0' and "
                "m.version('mlx-vlm').split('.')[1]=='7'; sys.exit(0 if ok else 1)",
            ],
            capture_output=True, text=True, check=False, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def install_state() -> dict:
    try:
        stored = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    ready = is_ready()
    return {
        "ready": ready,
        "state": "ready" if ready else stored.get("state", "absent"),
        "error": None if ready else stored.get("error"),
    }


def ensure_ready() -> None:
    """Install the versioned producer MLX extra into an isolated venv."""
    if is_ready():
        return
    from .model_adapters import get_adapter

    weight_size = get_adapter("mineru2.5").capabilities.approx_size_gb or 2.5
    # Estimate source weights + download/extraction staging + the isolated MLX
    # Python wheels. This is an application preflight, not an upstream limit.
    min_free_gb = math.ceil(weight_size * 1.5 + 6)
    free_gb = shutil.disk_usage(config.ROOT_DIR).free / _GIB
    if free_gb < min_free_gb:
        raise RuntimeError(
            f"Preflight disco fallito per MinerU MLX: {free_gb:.1f} GB liberi; "
            f"ne servono almeno {min_free_gb} GB. Nessun pacchetto o peso è stato scaricato."
        )
    target = _dir()
    target.mkdir(parents=True, exist_ok=True)
    try:
        venv.EnvBuilder(with_pip=True, clear=True).create(str(target))
    except Exception as exc:  # noqa: BLE001
        _write_state(state="error", error=str(exc))
        raise RuntimeError(f"creazione dell'ambiente MLX MinerU fallita: {exc}") from exc
    _write_state(state="installing", error=None)
    with _log_file().open("ab") as fh:
        fh.write(f"[{datetime.now(timezone.utc).isoformat()}] venv MinerU MLX creato in {target}\n".encode())
        fh.flush()
        try:
            subprocess.run(
                [str(python_bin()), "-m", "pip", "install", "--upgrade", "pip"],
                check=True, stdout=fh, stderr=subprocess.STDOUT,
            )
            subprocess.run(
                [str(python_bin()), "-m", "pip", "install", *PACKAGES],
                check=True, stdout=fh, stderr=subprocess.STDOUT,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            _write_state(state="error", error=str(exc))
            raise RuntimeError(f"installazione dell'ambiente MLX MinerU fallita: {exc}") from exc
    if not is_ready():
        _write_state(state="error", error="mineru_vl_utils/MLX non importabili dopo l'installazione")
        raise RuntimeError("installazione dell'ambiente MLX MinerU incompleta")
    _write_state(state="ready", error=None)


def serve_argv(model_path: str, *, port: int, settings: dict | None = None) -> list[str]:
    settings = settings or {}
    return [
        str(python_bin()), str(Path(__file__).resolve().parents[3] / "scripts" / "local" / "mineru_mlx_gateway.py"),
        "--model", model_path, "--port", str(int(port)), "--settings", json.dumps(settings, separators=(",", ":")),
    ]
