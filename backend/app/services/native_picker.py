"""Finestra nativa di scelta cartella (tkinter) sulla macchina del backend.

Il progetto naviga il filesystem del server da una finestra web
(`api/system.py::browse_folders`), che è la scelta giusta quando il browser è su
un'altra macchina. Qui si aggiunge la comodità opposta: quando il backend gira
sul computer dell'utente, la cartella si sceglie dalla finestra di sistema che
l'utente già conosce.

Il dialogo deve nascere nel thread principale del processo che possiede il
messaggio della GUI: dentro un worker di uvicorn non è affidabile. Si lancia
quindi un interprete figlio che mostra la finestra e restituisce il percorso su
stdout, senza toccare il processo del server. `tkinter` è nella libreria
standard, quindi non si aggiunge alcuna dipendenza.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Tetto di sicurezza: la finestra resta aperta quanto serve, ma un processo
# orfano non deve restare appeso per sempre.
_TIMEOUT_S = 1800

_PICKER_SCRIPT = r"""
import sys


def _fail(message):
    sys.stderr.write(message)
    raise SystemExit(3)


try:
    import tkinter as tk
    from tkinter import filedialog
except Exception as exc:  # tkinter assente (build senza Tk): GUI non disponibile
    _fail("tkinter non disponibile: " + str(exc))

start = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    kwargs = {"initialdir": start} if start else {}
    try:
        path = filedialog.askdirectory(title="Choose the archive folder", **kwargs)
    finally:
        root.destroy()
except Exception as exc:  # nessun display / sessione non interattiva
    _fail(str(exc))

sys.stdout.write(path or "")
"""


class PickerUnavailable(RuntimeError):
    """GUI non disponibile: server headless, tkinter assente, nessun display."""


def _initial_dir(start: str | None) -> str | None:
    """Cartella di partenza del dialogo: la richiesta se è una cartella, la sua
    genitrice se è un file, altrimenti nulla (si parte dal default di sistema)."""
    if not start:
        return None
    try:
        path = Path(start).expanduser()
    except (OSError, ValueError):
        return None
    if path.is_dir():
        return str(path)
    if path.exists():
        return str(path.parent)
    return None


def pick_folder(start: str | None = None) -> str | None:
    """Mostra la finestra di sistema e restituisce il percorso scelto.

    `None` se l'utente annulla. Solleva `PickerUnavailable` quando la finestra
    non può proprio comparire, così l'API risponde 501 e la UI può ripiegare
    sulla navigazione web.
    """
    cmd = [sys.executable, "-X", "utf8", "-c", _PICKER_SCRIPT]
    initial = _initial_dir(start)
    if initial:
        cmd.append(initial)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_TIMEOUT_S,
            env=env,
        )
    except FileNotFoundError as exc:
        raise PickerUnavailable("interprete Python non trovato") from exc
    except subprocess.TimeoutExpired as exc:
        raise PickerUnavailable("la finestra di scelta è rimasta aperta troppo a lungo") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or "GUI non disponibile"
        raise PickerUnavailable(detail)
    return (proc.stdout or "").strip() or None
