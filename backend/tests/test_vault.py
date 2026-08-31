from __future__ import annotations

from app import db
from app.services import vault

import pytest


pytestmark = pytest.mark.skipif(vault.Fernet is None, reason="cryptography non installata nel virtualenv")


def test_vault_stores_only_authenticated_ciphertext(monkeypatch):
    db.init_db()
    monkeypatch.setenv("TABULARIUM_VAULT_KEY", "test-vault-key")

    ref = vault.put("test-inference", "super-secret-token")
    assert ref == "vault:test-inference"
    assert vault.get(ref) == "super-secret-token"
    assert vault.has(ref)

    with db.connect() as conn:
        row = conn.execute("SELECT ciphertext FROM secrets WHERE name=?", ("test-inference",)).fetchone()
    assert row is not None
    assert row["ciphertext"] != "super-secret-token"
    assert "super-secret-token" not in row["ciphertext"]


def test_vault_requires_a_deployment_key(monkeypatch):
    db.init_db()
    monkeypatch.delenv("TABULARIUM_VAULT_KEY", raising=False)
    try:
        vault.put("missing-key", "secret")
    except RuntimeError as exc:
        assert "TABULARIUM_VAULT_KEY" in str(exc)
    else:
        raise AssertionError("il vault ha accettato un secret senza chiave")
