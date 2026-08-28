"""Primitivi di sicurezza — soli stdlib, nessuna dipendenza C.

- Hash password: PBKDF2-HMAC-SHA256 con salt casuale per utente e ~210k
  iterazioni (scelta difendibile per un'app self-hosted, auditabile, senza
  dipendenze native). Verifica in tempo costante.
- Token di sessione: `secrets.token_urlsafe`, salvati come SHA-256 in DB
  (una fuga del DB non espone token utilizzabili).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

PBKDF2_ITERATIONS = 210_000
SALT_BYTES = 16
TOKEN_BYTES = 32


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Restituisce `(salt_hex, hash_hex)`. Se `salt` è None lo genera."""
    salt_bytes = bytes.fromhex(salt) if salt else secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt_bytes, PBKDF2_ITERATIONS
    )
    return salt_bytes.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    """Verifica la password in tempo costante contro l'hash salvato."""
    salt_bytes = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt_bytes, PBKDF2_ITERATIONS
    )
    return hmac.compare_digest(digest.hex(), hash_hex)


def new_token() -> str:
    """Token di sessione casuale (url-safe)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Digest del token da salvare in DB (mai il token in chiaro)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
