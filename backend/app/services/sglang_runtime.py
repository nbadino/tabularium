"""SGLang environment for models whose vendor serves them with SGLang, not vLLM.

One environment per model, under `TABULARIUM_ROOT/sglang-runtime/<adapter_id>`: the
vendor builds pin their own torch (Unlimited-OCR-2's wheel pins torch 2.13), so two
SGLang models cannot share a site-packages. The packages and the interpreter come from
the model's serving recipe (`pip_extra`, `python_version`); nothing here names a model.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .. import config


def _dir(adapter_id: str) -> Path:
    return config.ROOT_DIR / "sglang-runtime" / adapter_id


def python_bin(adapter_id: str) -> Path:
    return _dir(adapter_id) / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _ready_marker(adapter_id: str) -> Path:
    return _dir(adapter_id) / ".ready"


def is_ready(adapter_id: str) -> bool:
    return python_bin(adapter_id).exists() and _ready_marker(adapter_id).exists()


def _log_file(adapter_id: str) -> Path:
    return _dir(adapter_id) / ".install.log"


def ensure_ready(adapter_id: str) -> None:
    """Create the environment and install the recipe's packages. Blocking.

    `uv` is preferred because it fetches the interpreter the vendor asks for (the
    README uses `uv venv --python 3.10`); a matching `python3.X` on PATH works too.
    The ready marker is written last, so an interrupted install is redone, not trusted.
    """
    if is_ready(adapter_id):
        return
    from . import serve_recipes

    recipe = serve_recipes.recipe_for(adapter_id)
    version = recipe.python_version or f"{sys.version_info.major}.{sys.version_info.minor}"
    uv = shutil.which("uv")
    interpreter = shutil.which(f"python{version}")
    if uv is None and interpreter is None:
        raise RuntimeError(
            f"'{adapter_id}' si serve con SGLang su Python {version}: installa `uv` "
            f"(https://docs.astral.sh/uv/) oppure python{version}, poi riprova"
        )
    target = _dir(adapter_id)
    target.mkdir(parents=True, exist_ok=True)
    python = str(python_bin(adapter_id))
    if uv is not None:
        steps = [
            [uv, "venv", "--clear", "--python", version, str(target)],
            [uv, "pip", "install", "--python", python, "huggingface_hub", *recipe.pip_extra],
        ]
    else:
        steps = [
            [interpreter, "-m", "venv", "--clear", str(target)],
            [python, "-m", "pip", "install", "--upgrade", "pip"],
            [python, "-m", "pip", "install", "huggingface_hub", *recipe.pip_extra],
        ]
    with _log_file(adapter_id).open("ab") as log:
        log.write(f"[{datetime.now(timezone.utc).isoformat()}] SGLang runtime in {target}\n".encode())
        log.flush()
        for argv in steps:
            try:
                subprocess.run(argv, check=True, stdout=log, stderr=subprocess.STDOUT)
            except (OSError, subprocess.CalledProcessError) as exc:
                raise RuntimeError(f"installazione del runtime SGLang fallita: {exc}") from exc
    _ready_marker(adapter_id).write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
