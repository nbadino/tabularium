"""Vault server-side per segreti usati dagli adapter e dai provider.

Il database contiene solo ciphertext Fernet. La chiave non viene mai
persistita da Tabularium: deve arrivare da ``TABULARIUM_VAULT_KEY``
(una Fernet key) o da un secret manager dell'ambiente di deployment.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re

try:
    from cryptography.fernet import Fernet, InvalidToken
except ModuleNotFoundError:  # installata dai requirements nel deployment reale
    Fernet = None  # type: ignore[assignment,misc]

    class InvalidToken(Exception):
        pass

from ..db import connect

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")


def _fernet() -> Fernet:
    if Fernet is None:
        raise RuntimeError("dipendenza cryptography non installata: eseguire pip install -r requirements.txt")
    raw = os.environ.get("TABULARIUM_VAULT_KEY", "").strip()
    if not raw:
        raise RuntimeError("TABULARIUM_VAULT_KEY non configurata")
    try:
        key = raw.encode("ascii")
        return Fernet(key)
    except (ValueError, UnicodeEncodeError):
        key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
        return Fernet(key)


def _validate_name(name: str) -> str:
    value = name.strip()
    if not _NAME.fullmatch(value):
        raise ValueError("nome secret non valido")
    return value


def put(name: str, value: str) -> str:
    name = _validate_name(name)
    if not value:
        raise ValueError("secret vuoto")
    ciphertext = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    with connect() as conn:
        conn.execute(
            "INSERT INTO secrets(name,ciphertext) VALUES(?,?) "
            "ON CONFLICT(name) DO UPDATE SET ciphertext=excluded.ciphertext, "
            "updated_at=datetime('now')",
            (name, ciphertext),
        )
    return f"vault:{name}"


def get(ref: str | None) -> str:
    if not ref or not ref.startswith("vault:"):
        return ""
    name = _validate_name(ref[6:])
    with connect() as conn:
        row = conn.execute("SELECT ciphertext FROM secrets WHERE name=?", (name,)).fetchone()
    if row is None:
        return ""
    try:
        return _fernet().decrypt(row["ciphertext"].encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError):
        return ""


def delete(name: str) -> None:
    name = _validate_name(name.removeprefix("vault:"))
    with connect() as conn:
        conn.execute("DELETE FROM secrets WHERE name=?", (name,))


def has(ref: str | None) -> bool:
    return bool(get(ref))
