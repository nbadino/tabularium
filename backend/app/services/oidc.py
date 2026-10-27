"""OpenID Connect login: authorization code + PKCE, configured by the administrator.

The instance is still a self-hosted appliance with local accounts: OIDC is an *extra*
door, enabled from Settings when an institution has an identity provider. It is not a
replacement and it never silently creates an account outside the rules below.

Decisions worth knowing:

- **State lives in the database, not in a signed cookie.** One row per attempt, deleted
  when consumed, ten minutes of life. A cookie would need signing, rotation and a story
  for what happens when the key changes; a row needs a `DELETE`.
- **PKCE even with a client secret.** The secret proves the client; the verifier proves
  that whoever exchanges the code is the same browser that started the flow.
- **No id_token signature verification.** The identity is read from `/userinfo` over
  TLS with the access token the provider just issued: verifying a JWT we do not need
  would add a JWKS cache and a class of bugs for no benefit.
- **A domain allowlist is the default posture.** An institution enables OIDC for its own
  people; without a list, anyone the provider knows can enter. The admin is told this
  in the panel, and an empty list means "any account the provider authenticates".
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from ..adapters import store
from . import vault

_STATE_PREFIX = "oidc_state:"
_STATE_TTL_SECONDS = 600
_DISCOVERY_TTL_SECONDS = 3600
_DISCOVERY: dict[str, tuple[float, dict]] = {}


class OidcError(Exception):
    """A failure the login page can explain. `code` is stable, not prose."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


@dataclass(frozen=True)
class OidcSetup:
    enabled: bool
    issuer: str
    client_id: str
    client_secret: str
    scopes: str
    allowed_domains: tuple[str, ...]
    button_label: str
    default_role: str

    @property
    def ready(self) -> bool:
        return bool(self.enabled and self.issuer and self.client_id)

    def email_allowed(self, email: str) -> bool:
        if not self.allowed_domains:
            return True
        domain = (email.rsplit("@", 1)[-1] if "@" in email else "").strip().lower()
        return domain in self.allowed_domains


def setup_from_settings(settings: dict) -> OidcSetup:
    """Build the provider configuration from the instance settings and the vault."""
    domains = settings.get("oidc_allowed_domains") or []
    if isinstance(domains, str):
        domains = [item.strip() for item in domains.replace(",", " ").split()]
    return OidcSetup(
        enabled=bool(settings.get("oidc_enabled")),
        issuer=str(settings.get("oidc_issuer") or "").strip().rstrip("/"),
        client_id=str(settings.get("oidc_client_id") or "").strip(),
        client_secret=vault.get("vault:oidc") if vault.has("vault:oidc") else "",
        scopes=str(settings.get("oidc_scopes") or "openid profile email").strip(),
        allowed_domains=tuple(
            sorted({str(item).strip().lower() for item in domains if str(item).strip()})
        ),
        button_label=str(settings.get("oidc_button_label") or "SSO").strip() or "SSO",
        default_role=str(settings.get("default_new_user_role") or "editor"),
    )


def _discovery(setup: OidcSetup) -> dict:
    cached = _DISCOVERY.get(setup.issuer)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    url = f"{setup.issuer}/.well-known/openid-configuration"
    try:
        response = httpx.get(url, timeout=10.0, follow_redirects=True)
        response.raise_for_status()
        document = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcError("oidc_discovery", f"discovery non raggiungibile: {exc}") from exc
    for key in ("authorization_endpoint", "token_endpoint"):
        if not document.get(key):
            raise OidcError("oidc_discovery", f"documento di discovery senza {key}")
    if not str(document["authorization_endpoint"]).startswith(("https://", "http://")):
        raise OidcError("oidc_discovery", "authorization_endpoint non valido")
    _DISCOVERY[setup.issuer] = (time.monotonic() + _DISCOVERY_TTL_SECONDS, document)
    return document


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _remember_state(verifier: str, redirect_uri: str) -> str:
    state = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(seconds=_STATE_TTL_SECONDS)).isoformat()
    store.delete_prefix(_STATE_PREFIX)
    store.put(
        f"{_STATE_PREFIX}{state}",
        {"verifier": verifier, "redirect_uri": redirect_uri, "expires_at": expires},
    )
    return state


def consume_state(state: str) -> tuple[str, str]:
    """Delete the row and return `(verifier, redirect_uri)`; single use, ten minutes."""
    if not state:
        raise OidcError("oidc_state", "state mancante")
    payload = store.pop(f"{_STATE_PREFIX}{state}")
    if not isinstance(payload, dict):
        raise OidcError("oidc_state", "state sconosciuto o già usato")
    if str(payload.get("expires_at") or "") < _now():
        raise OidcError("oidc_state", "state scaduto")
    return str(payload.get("verifier") or ""), str(payload.get("redirect_uri") or "")


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def authorization_url(setup: OidcSetup, redirect_uri: str) -> str:
    if not setup.ready:
        raise OidcError("oidc_disabled", "OIDC non configurato")
    document = _discovery(setup)
    verifier = secrets.token_urlsafe(48)
    state = _remember_state(verifier, redirect_uri)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": setup.client_id,
            "redirect_uri": redirect_uri,
            "scope": setup.scopes,
            "state": state,
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    endpoint = document["authorization_endpoint"]
    separator = "&" if "?" in endpoint else "?"
    return f"{endpoint}{separator}{query}"


def exchange_code(setup: OidcSetup, code: str, state: str) -> dict:
    """Exchange the authorization code and return the provider's claims."""
    verifier, redirect_uri = consume_state(state)
    document = _discovery(setup)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": setup.client_id,
        "code_verifier": verifier,
    }
    if setup.client_secret:
        data["client_secret"] = setup.client_secret
    try:
        token_response = httpx.post(document["token_endpoint"], data=data, timeout=15.0)
        token_response.raise_for_status()
        tokens = token_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcError("oidc_exchange", f"scambio del codice fallito: {exc}") from exc
    access_token = str(tokens.get("access_token") or "")
    if not access_token:
        raise OidcError("oidc_exchange", "il provider non ha restituito un access token")
    userinfo_endpoint = document.get("userinfo_endpoint")
    if not userinfo_endpoint:
        raise OidcError("oidc_discovery", "documento di discovery senza userinfo_endpoint")
    try:
        info_response = httpx.get(
            userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        info_response.raise_for_status()
        claims = info_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcError("oidc_userinfo", f"userinfo non leggibile: {exc}") from exc
    if not isinstance(claims, dict):
        raise OidcError("oidc_userinfo", "userinfo non è un oggetto")
    return claims


def identity_from_claims(claims: dict) -> dict:
    """Normalise the claims we require, and refuse the rest."""
    email = str(claims.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise OidcError("oidc_email", "il provider non ha restituito un indirizzo email")
    if claims.get("email_verified") is False:
        raise OidcError("oidc_email", "l'indirizzo email non è verificato dal provider")
    subject = str(claims.get("sub") or "").strip()
    label = str(claims.get("preferred_username") or claims.get("name") or "").strip()
    return {
        "subject": subject,
        "email": email,
        "name": label,
        "username": _username_from(label or email),
    }


def _username_from(raw: str) -> str:
    candidate = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "." for ch in raw.lower())
    candidate = candidate.strip("._-")[:32] or "utente"
    return candidate
