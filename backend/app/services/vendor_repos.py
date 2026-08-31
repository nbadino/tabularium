"""Repository di codice ufficiali che Tabularium clona da sé quando servono
per il serving locale — distinti dai *pesi* dei modelli, gestiti da
`model_registry.py`.

MonkeyOCRv2-Parsing è l'unico adapter che ne ha bisogno: `parsing/serve.py`
non è distribuito come pacchetto pip, va eseguito dal checkout del repo. Prima
l'utente doveva clonarlo a mano e impostare `TABULARIUM_TRAIN_REPO`: ora
`serve_manager.start()` lo clona da sé la prima volta, se l'utente non ha già
un checkout proprio configurato.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .. import config

MONKEYOCRV2_REPO_URL = "https://github.com/Yuliang-Liu/MonkeyOCRv2"


def monkeyocrv2_repo_dir() -> Path:
    return config.ROOT_DIR / "vendor" / "MonkeyOCRv2"


def ensure_monkeyocrv2_repo() -> Path:
    """Clona (shallow, una volta sola) il repo ufficiale se non è già presente."""
    target = monkeyocrv2_repo_dir()
    if (target / "parsing" / "serve.py").exists():
        return target
    if target.exists():
        # Cartella parziale/corrotta da un tentativo precedente interrotto.
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", MONKEYOCRV2_REPO_URL, str(target)],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"clone automatico del repo MonkeyOCRv2 fallito: {exc.stderr.strip()}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "clone automatico del repo MonkeyOCRv2 troppo lento (timeout 5 minuti)"
        ) from exc
    return target
