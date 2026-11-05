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
# Upstream has no tags: pin a commit, so every install runs the same `serve.py`.
# Bump it on purpose, after re-checking docs/spec/10-official-conventions.md (R3).
MONKEYOCRV2_COMMIT = "6cc0c0bb4864f2541fa6bc3a8f976cffaac8f359"


def monkeyocrv2_repo_dir() -> Path:
    return config.ROOT_DIR / "vendor" / "MonkeyOCRv2"


def _repin(target: Path) -> None:
    """Move a clone made before the pin, or at an older one, to MONKEYOCRV2_COMMIT.

    A folder that is not its own git checkout, or one with local changes, belongs to
    the user and stays as it is. Offline, the clone that is there keeps serving.
    """
    if not (target / ".git").exists():
        return  # without its own .git, `git -C` would act on an enclosing repository

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(target), *args], capture_output=True, text=True, timeout=300,
        )

    try:
        head = git("rev-parse", "HEAD")
        if head.returncode != 0 or head.stdout.strip() == MONKEYOCRV2_COMMIT:
            return
        if git("status", "--porcelain", "--untracked-files=no").stdout.strip():
            return
        if git("cat-file", "-e", f"{MONKEYOCRV2_COMMIT}^{{commit}}").returncode != 0:
            git("fetch", "--filter=blob:none", "origin", MONKEYOCRV2_COMMIT)
        git("checkout", "--detach", MONKEYOCRV2_COMMIT)
    except (OSError, subprocess.TimeoutExpired):
        pass


def ensure_monkeyocrv2_repo() -> Path:
    """Clona (shallow, una volta sola) il repo ufficiale se non è già presente."""
    target = monkeyocrv2_repo_dir()
    if (target / "parsing" / "serve.py").exists():
        _repin(target)
        return target
    if target.exists():
        # Cartella parziale/corrotta da un tentativo precedente interrotto.
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        for argv in (
            ["git", "clone", "--filter=blob:none", "--no-checkout", MONKEYOCRV2_REPO_URL, str(target)],
            ["git", "-C", str(target), "checkout", "--detach", MONKEYOCRV2_COMMIT],
        ):
            subprocess.run(argv, check=True, capture_output=True, text=True, timeout=300)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"clone automatico del repo MonkeyOCRv2 fallito: {exc.stderr.strip()}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "clone automatico del repo MonkeyOCRv2 troppo lento (timeout 5 minuti)"
        ) from exc
    return target
