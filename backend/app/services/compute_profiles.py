"""Profili compute atomici per inferenza e training."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from fastapi import HTTPException

from .. import config
from ..adapters import compute_profiles as profilestore
from ..adapters import store
from ..db import connect
from . import audit
from .inference import VllmClient
from .model_adapters import get_adapter
from .url_security import validate_endpoint


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _out(row) -> dict:
    d = dict(row)
    d["hardware_profile"] = json.loads(d.pop("hardware_profile_json") or "{}")
    d["active"] = bool(d["active"])
    ref = d.get("credential_ref")
    if ref and str(ref).startswith("vault:"):
        from . import vault
        d["has_credential"] = vault.has(ref)
    elif ref and str(ref).startswith("env:"):
        d["has_credential"] = bool(os.environ.get(str(ref)[4:]))
    else:
        d["has_credential"] = bool(ref)
    d.pop("credential_ref", None)
    d["last_health_ok"] = None if d["last_health_ok"] is None else bool(d["last_health_ok"])
    return d


def _credential(ref: str | None) -> str:
    if not ref:
        return ""
    if ref.startswith("env:"):
        return os.environ.get(ref[4:], "")
    if ref.startswith("vault:"):
        from . import vault
        return vault.get(ref)
    # Secret values never belong in a profile row. A missing/unknown ref is
    # deliberately treated as no credential instead of being echoed.
    return ""


def ensure_legacy_profile() -> None:
    """Seed the row that mirrors the pre-v10 configuration, once per database."""
    if profilestore.any_exists():
        return
    from . import inference

    cfg = inference.get_inference_config()
    with connect() as conn:
        profilestore.insert(
            {
                "name": "legacy-active",
                "provider": "local",
                "purpose": "both",
                "model_adapter_id": cfg.get("adapter_id", "monkeyocrv2-parsing"),
                "served_model_name": cfg.get("model", "MonkeyOCRv2"),
                "endpoint": cfg.get("url", config.VLLM_URL),
                "credential_ref": "vault:inference" if cfg.get("api_key") else None,
                "runtime_recipe_id": "legacy",
                "generation_profile_id": "default",
                "image_profile_id": "default",
            },
            conn,
        )
        profilestore.activate_all(conn)


def list_profiles() -> list[dict]:
    ensure_legacy_profile()
    return [_out(row) for row in profilestore.list_all()]


def create_profile(payload: dict, actor: dict | None = None) -> dict:
    validate_endpoint(payload["endpoint"])
    get_adapter(payload["model_adapter_id"])
    with connect() as conn:
        profile_id = profilestore.insert(payload, conn)
        row = profilestore.get(profile_id, conn)
        audit.record(
            conn, actor, "compute_profile.created",
            resource_type="compute_profile", resource_id=profile_id,
        )
    return _out(row)


def activate(profile_id: int, actor: dict | None = None) -> dict:
    ensure_legacy_profile()
    with connect() as conn:
        row = profilestore.get(profile_id, conn)
        if row is None:
            raise HTTPException(status_code=404, detail="profilo compute non trovato")
        endpoint = validate_endpoint(row["endpoint"])
        adapter = get_adapter(row["model_adapter_id"])
        key = _credential(row["credential_ref"])
        if row["credential_ref"] == "meta:inference_api_key":
            from . import inference
            key = inference.get_inference_config().get("api_key", "")
        client = VllmClient(url=endpoint, model=row["served_model_name"], api_key=key, timeout=10, adapter=adapter)
        health = client.test_connection(timeout=10)
        now = _now()
        if not health.get("ok"):
            profilestore.mark_unhealthy(
                profile_id, at=now, error=health.get("error", "health check fallito"), conn=conn
            )
            raise HTTPException(status_code=409, detail={"message": "profilo non attivato: health check fallito", "health": health})
        profilestore.deactivate_all(at=now, conn=conn)
        profilestore.mark_healthy(profile_id, at=now, conn=conn)
        profilestore.activate_only(profile_id, conn)
        # Aggiorna la vista legacy in una stessa transazione, mantenendo i
        # consumer esistenti mentre il frontend migra ai profili.
        for key, value in (
            ("inference_url", endpoint),
            ("inference_model", row["served_model_name"]),
            ("inference_adapter_id", row["model_adapter_id"]),
            ("inference_enabled", "1"),
        ):
            store.put_raw(key, value, conn)
        audit.record(conn, actor, "compute_profile.activated", resource_type="compute_profile", resource_id=profile_id, payload={"previous": "rollback_available_in_history"})
        return _out(profilestore.get(profile_id, conn))
