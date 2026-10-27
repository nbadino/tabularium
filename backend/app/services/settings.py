"""Impostazioni dell'istanza (self-hosted).

Salvate come JSON nella tabella `meta` (chiave `app_settings`), con default
derivati dall'ambiente al primo avvio. L'admin le cambia dalla pagina
Impostazioni; la UI usa `GET /api/settings` (pubblico per il gating) e
`PUT /api/settings` (solo admin).
"""
from __future__ import annotations

import json
import re

from .. import config
from ..adapters import store
from . import vault

_SETTINGS_KEY = "app_settings"
_DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")

# Default derivati dall'ambiente: l'istanza nasce coerente con le env var.
def _defaults() -> dict:
    return {
        "instance_name": config.APP_NAME,
        "allow_registration": bool(config.REGISTRATION_OPEN),
        "default_new_user_role": "editor",
        # OIDC is off until an administrator configures and enables it. The client
        # secret is not here: it lives in the vault, and only its presence is reported.
        "oidc_enabled": False,
        "oidc_issuer": "",
        "oidc_client_id": "",
        "oidc_scopes": "openid profile email",
        "oidc_allowed_domains": [],
        "oidc_button_label": "SSO",
    }


def get_app_settings() -> dict:
    """Impostazioni correnti (default + override persistiti)."""
    settings = _defaults()
    stored = store.get_or(_SETTINGS_KEY, None)
    if isinstance(stored, dict):
        settings.update({k: v for k, v in stored.items() if k in settings})
    # Derived, never stored: the panel must be able to say "a secret exists" without
    # ever receiving it.
    settings["oidc_client_secret_set"] = vault.has("vault:oidc")
    return settings


def save_app_settings(updates: dict) -> dict:
    """Applica solo le chiavi note; restituisce lo stato aggiornato."""
    settings = get_app_settings()
    known = {
        "instance_name",
        "allow_registration",
        "default_new_user_role",
        "oidc_enabled",
        "oidc_issuer",
        "oidc_client_id",
        "oidc_scopes",
        "oidc_allowed_domains",
        "oidc_button_label",
    }
    for key in known:
        if key in updates:
            settings[key] = updates[key]
    if not (0 < len(str(settings["instance_name"]).strip()) <= 100):
        raise ValueError("instance_name non valido")
    settings["allow_registration"] = bool(settings["allow_registration"])
    if settings["default_new_user_role"] not in {"editor", "viewer"}:
        settings["default_new_user_role"] = "editor"

    # The client secret never reaches the settings JSON: it goes to the vault, and an
    # explicit clear deletes it. Writing the whole object without the field keeps it.
    secret = updates.get("oidc_client_secret")
    if isinstance(secret, str) and secret.strip():
        vault.put("oidc", secret.strip())
    elif updates.get("oidc_client_secret_clear"):
        vault.delete("oidc")

    issuer = str(settings["oidc_issuer"]).strip().rstrip("/")
    if issuer and not issuer.startswith(("https://", "http://")):
        raise ValueError("OIDC issuer deve essere un URL https")
    if issuer.startswith("http://") and "localhost" not in issuer and "127.0.0.1" not in issuer:
        raise ValueError("OIDC richiede https (http è ammesso solo su localhost)")
    settings["oidc_issuer"] = issuer
    settings["oidc_client_id"] = str(settings["oidc_client_id"]).strip()[:200]
    scopes = " ".join(str(settings["oidc_scopes"]).split())
    if "openid" not in scopes.split():
        scopes = f"openid {scopes}".strip()
    settings["oidc_scopes"] = scopes[:300]
    settings["oidc_button_label"] = (str(settings["oidc_button_label"]).strip() or "SSO")[:40]

    domains = settings["oidc_allowed_domains"]
    if isinstance(domains, str):
        domains = [item.strip() for item in domains.replace(",", " ").split()]
    cleaned: list[str] = []
    for item in domains:
        domain = str(item).strip().lower().lstrip("@")
        if not domain:
            continue
        if not _DOMAIN.match(domain) or "." not in domain:
            raise ValueError(f"dominio OIDC non valido: {item}")
        cleaned.append(domain)
    settings["oidc_allowed_domains"] = sorted(set(cleaned))

    settings["oidc_enabled"] = bool(settings["oidc_enabled"])
    if settings["oidc_enabled"] and not (settings["oidc_issuer"] and settings["oidc_client_id"]):
        raise ValueError("per attivare OIDC servono issuer e client ID")

    settings["oidc_client_secret_set"] = vault.has("vault:oidc")
    # The derived flag is not stored: it is recomputed from the vault on every read.
    stored = {key: value for key, value in settings.items() if key != "oidc_client_secret_set"}
    store.put(_SETTINGS_KEY, stored)
    return settings
