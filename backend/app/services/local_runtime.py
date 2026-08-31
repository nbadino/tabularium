"""Ambiente vLLM gestito automaticamente da Tabularium.

Il serving locale generico (`vllm serve ...`, v. `model_adapters.py`)
richiede vLLM installato da qualche parte raggiungibile dal backend. Prima
l'utente doveva creare un venv a mano ed esportare `TABULARIUM_SERVE_PYTHON`:
ora, se non c'è un override esplicito e `vllm` non è già sul `PATH`,
`serve_manager.start()` chiama `ensure_ready()` che crea da sé un venv
dedicato sotto `TABULARIUM_ROOT/vllm-runtime` e ci installa quel che serve —
nessun passaggio manuale, nessuna variabile d'ambiente da impostare.

Un solo venv condiviso da tutti gli adapter generici (non solo quello attivo
al momento): evita di installare/reinstallare pacchetti ogni volta che si
cambia modello. I pacchetti extra sono quelli richiesti dai `serve_command`
verificati in `model_adapters.py` (MinerU2.5, MonkeyOCRv2) — un modello
custom che richieda un pacchetto ulteriore lo passa via `extra_args`
lato flag vLLM, non via dipendenze Python aggiuntive.
"""
from __future__ import annotations

import json
import subprocess
import sys
import venv
from datetime import datetime, timezone
from pathlib import Path

from .. import config

PACKAGES = [
    "vllm",
    "huggingface_hub[hf_transfer]",
    # MinerU2.5 (v. MinerU2_5Adapter.serve_command): NON l'extra `[vllm]`,
    # la matrice di compatibilità pubblicata dichiara vLLM <0.22.0.
    "mineru-vl-utils",
    # `parsing/serve.py` del repo ufficiale MonkeyOCRv2: nessuna delle tre è
    # una dipendenza di vLLM.
    "timm",
    "gradio",
    "pypdfium2",
]


def _dir() -> Path:
    return config.ROOT_DIR / "vllm-runtime"


def bin_dir() -> Path:
    return _dir() / ("Scripts" if sys.platform == "win32" else "bin")


def python_bin() -> Path:
    return bin_dir() / ("python.exe" if sys.platform == "win32" else "python")


def _vllm_bin() -> Path:
    return bin_dir() / ("vllm.exe" if sys.platform == "win32" else "vllm")


def is_ready() -> bool:
    return _vllm_bin().exists()


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
    """Crea il venv dedicato e installa vLLM (+ dipendenze extra) se manca.

    Bloccante: la primissima chiamata può richiedere qualche minuto (download
    di vLLM e delle ruote CUDA); le volte successive è un controllo
    istantaneo (`is_ready()` trova già l'eseguibile e ritorna subito).
    """
    if is_ready():
        return
    target = _dir()
    target.mkdir(parents=True, exist_ok=True)
    _write_state(state="installing", error=None)
    log_file = _log_file()
    with log_file.open("ab") as fh:
        stamp = datetime.now(timezone.utc).isoformat()
        fh.write(f"[{stamp}] creazione venv in {target}\n".encode())
        fh.flush()
        try:
            # `clear=True`: un tentativo precedente fallito può aver lasciato
            # un venv incompleto senza `vllm` installato — ripartire da zero
            # è più affidabile che provare a riparare in-place.
            venv.EnvBuilder(with_pip=True, clear=True).create(str(target))
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
        except Exception as exc:  # noqa: BLE001
            _write_state(state="failed", error=str(exc))
            raise RuntimeError(
                "installazione automatica dell'ambiente vLLM fallita "
                f"(v. log in {log_file}): {exc}"
            ) from exc
    _write_state(state="ready", error=None)
