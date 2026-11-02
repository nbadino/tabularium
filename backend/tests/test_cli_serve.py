"""The server trusts X-Forwarded-For only from the configured proxies."""
from __future__ import annotations

import sys
import types

from app import cli, config


def test_the_server_honours_the_trusted_proxies(monkeypatch) -> None:
    seen = {}
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda *a, **kw: seen.update(kw)))
    monkeypatch.setattr(config, "FORWARDED_ALLOW_IPS", "172.17.0.1")
    assert cli.main(["--no-browser"]) == 0
    assert seen["proxy_headers"] is True
    assert seen["forwarded_allow_ips"] == "172.17.0.1"


def test_startup_warns_on_an_unsupported_python(monkeypatch, capsys) -> None:
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda *a, **kw: None))
    monkeypatch.setattr(sys, "version_info", (3, 14, 4, "final", 0))
    assert cli.main(["--no-browser"]) == 0
    err = capsys.readouterr().err
    assert "Python 3.14" in err
    assert "uv tool install --reinstall --python 3.13 tabularium" in err


def test_help_lists_the_subcommands(capsys) -> None:
    import pytest

    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    usage = out.split("\n\n")[0]
    for command in ("doctor", "models", "backup", "restore-db", "reset-password"):
        assert command in usage


def test_the_banner_under_a_service_shows_the_public_url_and_no_ctrl_c(monkeypatch, capsys) -> None:
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda *a, **kw: None))
    monkeypatch.setenv("TABULARIUM_PUBLIC_URL", "https://tabularium.example.org")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert cli.main(["--no-browser"]) == 0
    out = capsys.readouterr().out
    assert "https://tabularium.example.org" in out
    assert "Ctrl+C" not in out


def test_the_banner_in_a_terminal_says_how_to_stop(monkeypatch, capsys) -> None:
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda *a, **kw: None))
    monkeypatch.delenv("TABULARIUM_PUBLIC_URL", raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    assert cli.main(["--no-browser", "--port", "8799"]) == 0
    out = capsys.readouterr().out
    assert "http://localhost:8799" in out
    assert "Ctrl+C" in out


def test_reset_password_sets_a_new_password_and_closes_sessions(capsys) -> None:
    from app.db import connect, init_db
    from app.services import users as usersvc

    init_db()
    with connect() as conn:
        user = usersvc.create_user(conn, "cli-reset", "old-password-1", role="admin")
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES ('h', ?, '', '')",
            (user["id"],),
        )
    assert cli.main(["reset-password", "cli-reset", "--password", "new-password-1"]) == 0
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username='cli-reset'").fetchone()
        assert conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (user["id"],)).fetchone()[0] == 0
    from app.services import security

    assert security.verify_password("new-password-1", row["salt"], row["password_hash"])
    assert cli.main(["reset-password", "nobody-here", "--password", "new-password-1"]) == 1
