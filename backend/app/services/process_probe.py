"""Ispezione dei processi indipendente dal sistema operativo.

Linux espone `/proc`; macOS no. Le funzioni che leggevano direttamente
`/proc/<pid>/stat` e `/proc/<pid>/cmdline` degradavano quindi in silenzio su
macOS: l'`except OSError` restituiva sempre il valore di fallback, e con esso
un processo non attribuibile o uno zombie scambiato per vivo.

Qui il percorso Linux resta identico a prima; su macOS si interroga `ps`, che
è nel sistema base e non aggiunge dipendenze.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_PS_TIMEOUT = 5.0


def _ps_field(pid: int, field: str) -> str | None:
    """Un campo di `ps` per il PID, senza intestazione. None se non leggibile.

    `-ww` disattiva il troncamento alla larghezza del terminale: senza, una
    riga di comando lunga (i serve reali lo sono) arriverebbe tagliata e
    l'attribuzione fallirebbe proprio sui processi che deve riconoscere.
    """
    try:
        done = subprocess.run(
            ["ps", "-ww", "-o", f"{field}=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    value = done.stdout.strip()
    return value or None


def process_state(pid: int) -> str | None:
    """Codice di stato del processo (`Z` = zombie). None se non ispezionabile.

    Su Linux il campo è il terzo di `/proc/<pid>/stat`, ma va cercato **dopo**
    l'ultima `)`: il secondo campo è il nome del comando fra parentesi e può
    contenere spazi e parentesi a sua volta.
    """
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
            return stat.rsplit(")", 1)[-1].split()[0]
        except (OSError, IndexError):
            return None
    if sys.platform == "darwin":
        state = _ps_field(pid, "state")
        # `ps` aggiunge flag posizionali allo stato (`S+`, `Ss`): conta la prima
        # lettera, che è il codice vero e proprio.
        return state[0] if state else None
    return None


def is_zombie(pid: int) -> bool:
    """True solo se si è potuto stabilire che il processo è uno zombie."""
    return process_state(pid) == "Z"


def process_cmdline(pid: int) -> str | None:
    """Riga di comando del processo, argomenti separati da spazio.

    None quando non è leggibile: il chiamante decide se ciò significhi "non è
    nostro" (attribuzione) o "non verificabile".
    """
    if sys.platform.startswith("linux"):
        try:
            return (
                Path(f"/proc/{pid}/cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode("utf-8", errors="replace")
            )
        except OSError:
            return None
    if sys.platform == "darwin":
        return _ps_field(pid, "command")
    return None
