"""Ambiente MLX gestito automaticamente da Tabularium.

Su Apple Silicon vLLM non gira (è un progetto CUDA-first) e non esiste un
percorso CUDA: il serving locale nativo è **MLX**, esposto come endpoint
OpenAI-compatibile da `mlx-vlm` (`python -m mlx_vlm.server`). È lo stesso
principio di `local_runtime.py` per vLLM: l'utente non prepara un venv a mano.
Alla prima messa in servizio Tabularium crea `<root>/mlx-runtime` e ci
installa `mlx-vlm`; dopo è un controllo istantaneo.

Perché un ambiente separato: `mlx-vlm` porta `transformers`, `mlx` e i suoi
pesi. Il processo dashboard non deve averli (§4 di AGENTS.md: nessuna
dipendenza PyTorch nel processo che serve la UI); il server MLX è un
sottoprocesso, esattamente come vLLM.

L'ambiente è per *piattaforma*, non per modello: i pesi li scarica `mlx-vlm`
dalla cache Hugging Face alla prima richiesta, quindi non passano dal registro
modelli (`<root>/models`).
"""
from __future__ import annotations

import json
import subprocess
import sys
import venv
from datetime import datetime, timezone
from pathlib import Path

from .. import config

# `mlx-vlm` è l'unico pacchetto necessario: porta con sé `mlx`, `mlx-lm`,
# `transformers` e il server FastAPI.
PACKAGES = ["mlx-vlm"]


def _dir() -> Path:
    return config.ROOT_DIR / "mlx-runtime"


def bin_dir() -> Path:
    return _dir() / ("Scripts" if sys.platform == "win32" else "bin")


def python_bin() -> Path:
    return bin_dir() / ("python.exe" if sys.platform == "win32" else "python")


def is_ready() -> bool:
    """Vero quando l'ambiente esiste e `mlx-vlm` è importabile da lì."""
    if not python_bin().exists():
        return False
    probe = subprocess.run(
        [str(python_bin()), "-c", "import mlx_vlm.server"],
        capture_output=True,
        text=True,
        check=False,
    )
    return probe.returncode == 0


def _state_file() -> Path:
    return _dir() / ".install_state.json"


def _log_file() -> Path:
    return _dir() / ".install.log"


def log_tail(n: int = 4000) -> str:
    log_file = _log_file()
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


def _write_state(**fields) -> None:
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    state: dict = {}
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
    state.update(fields, updated_at=datetime.now(timezone.utc).isoformat())
    path.write_text(json.dumps(state), encoding="utf-8")


def install_state() -> dict:
    stored: dict = {}
    if _state_file().exists():
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
    """Crea il venv MLX e installa `mlx-vlm` se manca. Bloccante.

    La prima volta scarica `mlx-vlm` e le sue dipendenze (un minuto circa su
    banda normale); le volte successive trova `mlx_vlm` importabile e ritorna
    subito.
    """
    if is_ready():
        return
    target = _dir()
    target.mkdir(parents=True, exist_ok=True)
    # `clear=True` cancella il contenuto della cartella: il venv si crea
    # **prima** di scrivere stato e log, altrimenti i file appena scritti
    # spariscono e durante l'installazione la UI legge «absent» con il log
    # vuoto — proprio nei minuti in cui servono.
    try:
        venv.EnvBuilder(with_pip=True, clear=True).create(str(target))
    except Exception as exc:  # noqa: BLE001
        _write_state(state="error", error=str(exc))
        raise RuntimeError(f"creazione dell'ambiente MLX fallita: {exc}") from exc
    _write_state(state="installing", error=None)
    with _log_file().open("ab") as fh:
        stamp = datetime.now(timezone.utc).isoformat()
        fh.write(f"[{stamp}] venv MLX creato in {target}\n".encode())
        fh.flush()
        try:
            subprocess.run(
                [str(python_bin()), "-m", "pip", "install", "--upgrade", "pip"],
                check=True,
                stdout=fh,
                stderr=subprocess.STDOUT,
            )
            subprocess.run(
                [str(python_bin()), "-m", "pip", "install", *PACKAGES],
                check=True,
                stdout=fh,
                stderr=subprocess.STDOUT,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            _write_state(state="error", error=str(exc))
            raise RuntimeError(f"installazione dell'ambiente MLX fallita: {exc}") from exc
    if not is_ready():
        _write_state(state="error", error="mlx_vlm non importabile dopo l'installazione")
        raise RuntimeError("installazione dell'ambiente MLX incompleta")
    _write_state(state="ready", error=None)


def serve_argv(repo: str, *, port: int, host: str = "127.0.0.1") -> list[str]:
    """Comando del server MLX, OpenAI-compatibile.

    `mlx-vlm` non ha `--served-model-name`: il nome che il client deve chiedere
    è l'id del checkpoint (lo stesso che compare in `/v1/models`). Il server
    scarica i pesi da Hugging Face alla prima richiesta.
    """
    return [
        str(python_bin()),
        "-m",
        "mlx_vlm.server",
        "--model",
        repo,
        "--host",
        host,
        "--port",
        str(int(port)),
    ]
