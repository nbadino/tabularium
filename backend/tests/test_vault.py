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


@pytest.fixture
def isolated_secrets():
    """La suite condivide un DB: svuota `secrets` e ripristinalo dopo."""
    db.init_db()
    with db.connect() as conn:
        saved = [dict(r) for r in conn.execute("SELECT * FROM secrets").fetchall()]
        conn.execute("DELETE FROM secrets")
    vault._warned.clear()
    yield
    with db.connect() as conn:
        conn.execute("DELETE FROM secrets")
        for row in saved:
            cols = ",".join(row)
            conn.execute(f"INSERT INTO secrets({cols}) VALUES({','.join('?' * len(row))})", tuple(row.values()))
    vault._warned.clear()


def test_matching_key_reports_no_problem(monkeypatch, isolated_secrets):
    monkeypatch.setenv("TABULARIUM_VAULT_KEY", "key-original")
    vault.put("vast", "token")
    assert vault.unreadable_names() == []
    assert vault.key_problem() is None


def test_fresh_install_without_secrets_does_not_warn(monkeypatch, isolated_secrets):
    monkeypatch.delenv("TABULARIUM_VAULT_KEY", raising=False)
    assert vault.key_problem() is None


def test_wrong_key_is_named_instead_of_looking_unconfigured(monkeypatch, caplog, isolated_secrets):
    monkeypatch.setenv("TABULARIUM_VAULT_KEY", "key-original")
    vault.put("vast", "token")
    monkeypatch.setenv("TABULARIUM_VAULT_KEY", "key-from-another-env")

    with caplog.at_level("WARNING", logger="app.services.vault"):
        # Il contratto dei chiamanti non cambia: illeggibile resta "".
        assert vault.get("vault:vast") == ""
        assert not vault.has("vault:vast")
    warnings = [r for r in caplog.records if "vast" in r.getMessage()]
    assert len(warnings) == 1, "un solo avviso per secret, non uno per lettura"

    assert vault.unreadable_names() == ["vast"]
    problem = vault.key_problem()
    assert problem is not None and "non corrisponde" in problem and "vast" in problem


def test_missing_key_with_saved_secrets_is_reported(monkeypatch, isolated_secrets):
    monkeypatch.setenv("TABULARIUM_VAULT_KEY", "key-original")
    vault.put("vast", "token")
    monkeypatch.delenv("TABULARIUM_VAULT_KEY")

    assert vault.unreadable_names() == ["vast"]
    problem = vault.key_problem()
    assert problem is not None and "non è configurata" in problem
