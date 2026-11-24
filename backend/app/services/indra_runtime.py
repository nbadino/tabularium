"""The ONNX Runtime environment that serves Indra OCR on this machine.

Indra needs neither vLLM nor PyTorch: `indra-ocr` (ONNX Runtime, OpenCV, pypdfium2)
plus FastAPI and Uvicorn for Tabularium's gateway. It gets its own small venv under
`TABULARIUM_ROOT/indra-runtime`, created the first time the model is served, like the
vLLM one (`local_runtime`). On a Linux machine with an NVIDIA GPU the CPU build of ONNX
Runtime is replaced by onnxruntime-gpu (the two install the same `onnxruntime` module
and cannot live side by side); everywhere else the CPU build stays.
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
import venv
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from ..models import indra_lloyds as lloyds_spec
from ..models import indra_ocr as spec

READY_CHECK = "import indra_ocr, fastapi, uvicorn, onnxruntime, PIL, numpy"


def _dir() -> Path:
    return config.ROOT_DIR / "indra-runtime"


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
            fh.seek(max(0, fh.tell() - n))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _read_state() -> dict:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(**fields) -> None:
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    state = _read_state()
    state.update(fields, updated_at=datetime.now(timezone.utc).isoformat())
    path.write_text(json.dumps(state), encoding="utf-8")


def wants_gpu(*, system: str | None = None, gpus: list | None = None) -> bool:
    """True when this machine should get onnxruntime-gpu: Linux with an NVIDIA GPU.

    The onnxruntime-gpu wheels on PyPI are CUDA builds for Linux and Windows; macOS
    has none, and Apple silicon runs Indra on the CPU, as the vendor tests it.
    """
    system = system or platform.system()
    if system != "Linux":
        return False
    if gpus is None:
        from . import hardware

        gpus = hardware.cuda_devices()
    return bool(gpus)


def packages(*, gpu: bool) -> list[list[str]]:
    """The pip steps, in order. `indra-ocr` depends on the CPU `onnxruntime`, and the
    GPU build installs the same module: installing both in one step leaves one half
    overwritten by the other. So the CPU build goes first, is removed, and the GPU
    build takes its place."""
    # One environment serves every model of the gateway: the line reader's own
    # imports are installed with it.
    steps = [[spec.INDRA_PACKAGE, *spec.GATEWAY_PACKAGES, *lloyds_spec.RUNTIME_PACKAGES]]
    if gpu:
        steps.append(["--uninstall", "onnxruntime"])
        steps.append([spec.ONNXRUNTIME_GPU_PACKAGE])
    return steps


def is_ready() -> bool:
    if not python_bin().exists():
        return False
    state = _read_state()
    gpu = wants_gpu()
    # A runtime built from an older package list (one without Pillow, say) is not
    # ready: it is rebuilt instead of failing at the gateway's first import.
    return (
        state.get("state") == "ready"
        and state.get("gpu") == gpu
        and state.get("packages") == packages(gpu=gpu)
    )


def install_state() -> dict:
    ready = is_ready()
    stored = _read_state()
    return {
        "ready": ready,
        "state": "ready" if ready else stored.get("state", "absent"),
        "error": None if ready else stored.get("error"),
    }


def ensure_ready() -> None:
    """Create the environment and install Indra if it is missing (blocking).

    A few hundred MB of wheels the first time (more with onnxruntime-gpu); later calls
    return at once. A machine that gains or loses a GPU is reinstalled, so the build
    always matches the hardware.
    """
    if is_ready():
        return
    target = _dir()
    target.mkdir(parents=True, exist_ok=True)
    gpu = wants_gpu()
    try:
        venv.EnvBuilder(with_pip=True, clear=True).create(str(target))
    except Exception as exc:  # noqa: BLE001
        _write_state(state="failed", error=str(exc), gpu=gpu)
        raise RuntimeError(f"creazione dell'ambiente Indra OCR fallita: {exc}") from exc
    _write_state(state="installing", error=None, gpu=gpu)
    python = str(python_bin())
    with _log_file().open("ab") as fh:
        stamp = datetime.now(timezone.utc).isoformat()
        fh.write(f"[{stamp}] venv creato in {target} (gpu={gpu})\n".encode())
        fh.flush()
        try:
            subprocess.run(
                [python, "-m", "pip", "install", "--upgrade", "pip"],
                check=True, stdout=fh, stderr=subprocess.STDOUT,
            )
            for step in packages(gpu=gpu):
                if step[0] == "--uninstall":
                    command = [python, "-m", "pip", "uninstall", "-y", *step[1:]]
                else:
                    command = [python, "-m", "pip", "install", *step]
                subprocess.run(command, check=True, stdout=fh, stderr=subprocess.STDOUT)
            subprocess.run(
                [python, "-c", READY_CHECK], check=True, stdout=fh, stderr=subprocess.STDOUT,
            )
        except Exception as exc:  # noqa: BLE001
            _write_state(state="failed", error=str(exc), gpu=gpu)
            raise RuntimeError(
                f"installazione dell'ambiente Indra OCR fallita (v. log in {_log_file()}): {exc}"
            ) from exc
    _write_state(state="ready", error=None, gpu=gpu, packages=packages(gpu=gpu))
