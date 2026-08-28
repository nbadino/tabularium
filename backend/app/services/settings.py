"""Impostazioni dell'istanza (self-hosted).

Salvate come JSON nella tabella `meta` (chiave `app_settings`), con default
derivati dall'ambiente al primo avvio. L'admin le cambia dalla pagina
Impostazioni; la UI usa `GET /api/settings` (pubblico per il gating) e
`PUT /api/settings` (solo admin).
"""
from __future__ import annotations

import json

from .. import config
from ..db import connect

_SETTINGS_KEY = "app_settings"

# Default derivati dall'ambiente: l'istanza nasce coerente con le env var.
def _defaults() -> dict:
    return {
        "instance_name": config.APP_NAME,
        "allow_registration": bool(config.REGISTRATION_OPEN),
        "default_new_user_role": "editor",
    }


def get_app_settings() -> dict:
    """Impostazioni correnti (default + override persistiti)."""
    settings = _defaults()
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key=?", (_SETTINGS_KEY,)
        ).fetchone()
    if row:
        try:
            stored = json.loads(row["value"])
            if isinstance(stored, dict):
                settings.update({k: v for k, v in stored.items() if k in settings})
        except (TypeError, ValueError):
            pass
    return settings


def save_app_settings(updates: dict) -> dict:
    """Applica solo le chiavi note; restituisce lo stato aggiornato."""
    settings = get_app_settings()
    known = {"instance_name", "allow_registration", "default_new_user_role"}
    for key in known:
        if key in updates:
            settings[key] = updates[key]
    if not (0 < len(str(settings["instance_name"]).strip()) <= 100):
        raise ValueError("instance_name non valido")
    settings["allow_registration"] = bool(settings["allow_registration"])
    if settings["default_new_user_role"] not in {"editor", "viewer"}:
        settings["default_new_user_role"] = "editor"
    with connect() as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_SETTINGS_KEY, json.dumps(settings, ensure_ascii=False)),
        )
    return settings
