"""Gateway OpenCode (Zen / Go) per i modelli chiusi come Gemini 4 Argon.

Argon non pubblica pesi: si raggiunge solo via API. Il gateway OpenCode
espone i modelli Gemini sul protocollo Google `generateContent`
(`.../inference/<scope>/google/v1beta`) e autentica con la chiave dell'account.

La chiave non va reinserita a mano: opencode la conserva già in chiaro in
`~/.local/share/opencode/auth.json` (`opencode-go` o `opencode`). Se l'utente
non ha incollato una chiave in Impostazioni, la leggiamo da lì; l'override per
test e per archivi non standard passa da `TABULARIUM_OPENCODE_KEY` (chiave) e
`TABULARIUM_OPENCODE_AUTH` (percorso dell'archivio).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# Percorso della variante `gemini-4-argon-contributor` sul gateway Go. Il
# modello vive nel campo `served_model_name` dell'adapter; qui si dichiara solo
# dove si trova, così la UI può precompilare l'endpoint senza indovinare.
DEFAULT_BASE_URL = "https://opencode.ai/inference/go/google/v1beta"
DEFAULT_MODEL = "gemini-4-argon-contributor"

_AUTH_PATH = Path.home() / ".local" / "share" / "opencode" / "auth.json"
# Provider in ordine di preferenza: la sottoscrizione Go (argon-contributor) e
# il pay-as-you-go Zen (argon). Entrambi espongono la chiave come `key`.
_PROVIDER_ORDER = ("opencode-go", "opencode")


def read_api_key() -> str:
    """Chiave OpenCode dall'ambiente o dall'archivio credenziali di opencode."""
    override = os.environ.get("TABULARIUM_OPENCODE_KEY")
    if override and override.strip():
        return override.strip()
    paths = []
    custom = os.environ.get("TABULARIUM_OPENCODE_AUTH")
    if custom:
        paths.append(Path(custom))
    paths.append(_AUTH_PATH)
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for provider in _PROVIDER_ORDER:
            entry = data.get(provider)
            if isinstance(entry, dict):
                key = str(entry.get("key") or "").strip()
                if key:
                    return key
    return ""


def status() -> dict:
    """Stato leggibile dalla UI: endpoint, modello e presenza della chiave."""
    return {
        "base_url": DEFAULT_BASE_URL,
        "model": DEFAULT_MODEL,
        "key_available": bool(read_api_key()),
    }
