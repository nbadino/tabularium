"""Domain profiles: per-corpus defaults (labels, page genres, conventions) kept out of the core."""
from __future__ import annotations

import json

from .. import config

_PROFILE_DIR = config.RESOURCE_DIR / "conf" / "profiles"


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


def canonical_id(profile_id: str) -> str:
    """Map a renamed profile's old id to its current one; unknown ids pass through."""
    for profile in list_profiles():
        if profile_id == profile["id"] or profile_id in profile.get("legacy_ids", []):
            return profile["id"]
    return profile_id


def get_profile(profile_id: str) -> dict:
    wanted = canonical_id(profile_id)
    for profile in list_profiles():
        if profile["id"] == wanted:
            return profile
    raise ValueError(f"unknown domain profile: {profile_id}")
