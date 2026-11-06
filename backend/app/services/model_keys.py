"""Each user's own API key for a saved model.

A saved model (a compute profile used by Recognition and by model comparisons) often
reaches a paid API: OpenCode for Gemini, LlamaCloud for OCRia. The key is personal: each
user adds their own, stored encrypted in `user_model_keys`, and a run uses the key of
the user who started it. A model marked `key_required` cannot be used by someone without
a key; there is no fallback to anybody else's key, nor to the OpenCode key of this
machine. Nobody reads a key back, not even masked: the API only says whether the
current user has one.

Local mode (`TABULARIUM_AUTH=off`) has a single, anonymous user: there the key lives on
the profile (`credential_ref`), as before per-user keys, with the OpenCode gateway key
of the machine as fallback.

The active model (Models page) is not a saved model: it is the instance's shared
configuration, set by an administrator, and keeps its own key.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException

from ..adapters import compute_profiles as profilestore
from ..adapters import model_keys as keystore
from . import auth as authsvc
from .i18n import msg

MAX_KEY_LENGTH = 4096
_VAULT_PREFIX = "consensus_profile_"
# Saved models that reach a paid API need a key whatever was saved with them.
_PAID_ADAPTERS = frozenset({"gemini-argon", "llamaindex-ocria-1"})


class MissingKey(RuntimeError):
    """The user who runs a saved model has no key for it."""

    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


def _local() -> bool:
    return not authsvc.auth_enabled()


def requires_key(adapter_id: str, provider: str | None, had_key: bool) -> bool:
    """Whether a model being saved needs each user's own key."""
    if had_key or adapter_id in _PAID_ADAPTERS or provider in {"llamaindex", "opencode"}:
        return True
    from .model_adapters import get_adapter
    from .model_surface import API_ONLY_BACKENDS

    try:
        backend = getattr(get_adapter(adapter_id).capabilities, "serve_backend", "")
    except ValueError:
        return False
    return backend in API_ONLY_BACKENDS


def _uses_gateway(profile: dict[str, Any]) -> bool:
    from .model_adapters import get_adapter

    if profile.get("provider") == "opencode":
        return True
    try:
        adapter = get_adapter(str(profile["model_adapter_id"]))
    except ValueError:
        return False
    return getattr(adapter.capabilities, "serve_backend", "") == "opencode-gateway"


def _local_key(profile: dict[str, Any]) -> str:
    from .inference import profile_credential

    key = profile_credential(profile.get("credential_ref"))
    if not key and _uses_gateway(profile):
        from . import opencode_gateway

        key = opencode_gateway.read_api_key()
    return key


def _own_key(user_id: int | None, profile_id: int) -> str:
    if user_id is None:
        return ""
    ciphertext = keystore.get(int(user_id), profile_id)
    if not ciphertext:
        return ""
    from . import vault

    return vault.decrypt(ciphertext)


def key_for(profile: dict[str, Any], user_id: int | None) -> str:
    """The key a run started by `user_id` sends to the saved model; "" for a model
    that needs none. Raises `MissingKey` when the model needs one and the user has none."""
    key = _local_key(profile) if _local() else _own_key(user_id, int(profile["id"]))
    if not key and profile.get("key_required"):
        raise MissingKey(str(profile.get("name") or profile["id"]))
    return key


def status(profile: dict[str, Any], user: dict | None) -> dict[str, bool]:
    """What the API shows of a key: whether the model needs one, whether this user has one."""
    if _local():
        has_key = bool(_local_key(profile))
    else:
        has_key = bool(_own_key((user or {}).get("id"), int(profile["id"])))
    return {"key_required": bool(profile.get("key_required")), "has_key": has_key}


def statuses(profiles: list[dict[str, Any]], user: dict | None) -> dict[int, dict[str, bool]]:
    """`status` for many profiles, with one query for the user's keys."""
    if _local():
        return {int(p["id"]): status(p, user) for p in profiles}
    user_id = (user or {}).get("id")
    mine = keystore.profiles_with_key(int(user_id)) if user_id is not None else set()
    return {
        int(p["id"]): {"key_required": bool(p.get("key_required")), "has_key": int(p["id"]) in mine}
        for p in profiles
    }


def require_usable(profile: dict[str, Any], user_id: int | None, lang: str | None) -> None:
    """Refuse to start a run on a model the user has no key for (409, with what to do)."""
    try:
        key_for(profile, user_id)
    except MissingKey as exc:
        raise HTTPException(
            status_code=409, detail=msg("saved_model_key_missing", lang, name=exc.name)
        ) from exc


def _saved_profile(profile_id: int, lang: str | None) -> dict[str, Any]:
    from .consensus import LEGACY_PROFILE

    row = profilestore.get(profile_id)
    if row is None or row["name"] == LEGACY_PROFILE:
        raise HTTPException(status_code=404, detail=msg("saved_model_not_found", lang))
    return row


def set_key(profile_id: int, user: dict, api_key: str, lang: str | None = None) -> dict[str, Any]:
    """Store the user's own key for a saved model (replacing theirs, nobody else's)."""
    profile = _saved_profile(profile_id, lang)
    key = str(api_key or "").strip()
    if not key or len(key) > MAX_KEY_LENGTH:
        raise HTTPException(status_code=422, detail=msg("saved_model_key_empty", lang))
    from . import audit, vault
    from ..db import connect  # noqa: PLC0415

    if _local():
        ref = str(profile.get("credential_ref") or "")
        if not ref.startswith(f"vault:{_VAULT_PREFIX}"):
            ref = f"vault:{_VAULT_PREFIX}{uuid.uuid4().hex[:16]}"
        vault.put(ref.removeprefix("vault:"), key)
        profilestore.set_credential(profile_id, ref)
    else:
        keystore.put(int(user["id"]), profile_id, vault.encrypt(key))
    with connect() as conn:
        audit.record(
            conn, user, "model_key.saved", resource_type="compute_profile", resource_id=profile_id,
        )
    return {"id": profile_id, **status(profilestore.get(profile_id) or profile, user)}


def save_owner_key(profile_id: int, user: dict | None, api_key: str) -> None:
    """The key a model was saved with becomes the saving administrator's own."""
    from . import vault

    if _local() or not user or user.get("id") is None:
        ref = f"vault:{_VAULT_PREFIX}{uuid.uuid4().hex[:16]}"
        vault.put(ref.removeprefix("vault:"), api_key)
        profilestore.set_credential(profile_id, ref)
    else:
        keystore.put(int(user["id"]), profile_id, vault.encrypt(api_key))


def delete_key(profile_id: int, user: dict, lang: str | None = None) -> None:
    """Remove the user's own key for a saved model."""
    profile = _saved_profile(profile_id, lang)
    from . import audit, vault
    from ..db import connect  # noqa: PLC0415

    if _local():
        ref = str(profile.get("credential_ref") or "")
        if ref.startswith(f"vault:{_VAULT_PREFIX}"):
            vault.delete(ref)
        profilestore.set_credential(profile_id, None)
    elif not keystore.delete(int(user["id"]), profile_id):
        return
    with connect() as conn:
        audit.record(
            conn, user, "model_key.deleted", resource_type="compute_profile", resource_id=profile_id,
        )
