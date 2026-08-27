"""Profili di dominio caricabili senza legare il core a Lloyd's List."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_PROFILE_DIR = _ROOT / "conf" / "profiles"


def list_profiles() -> list[dict]:
    items = []
    for path in sorted(_PROFILE_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("id"):
            items.append(data)
    return items


def get_profile(profile_id: str) -> dict:
    for profile in list_profiles():
        if profile.get("id") == profile_id:
            return profile
    raise ValueError(f"profilo dominio sconosciuto: {profile_id}")
