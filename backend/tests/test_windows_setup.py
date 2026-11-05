"""The Windows scripts must do what their shell twins do (no PowerShell in CI:
these checks are on the script text)."""
from __future__ import annotations

from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def test_windows_setup_generates_the_vault_key_like_the_shell_setup():
    """Without it a release-archive install on Windows cannot save any credential."""
    text = (SCRIPTS / "setup_backend.ps1").read_text(encoding="utf-8")
    assert "TABULARIUM_VAULT_KEY=" in text and "Fernet.generate_key" in text


def test_windows_launchers_read_host_and_port_from_dotenv():
    """config.py reads .env; a launcher that does not would bind another address."""
    for name in ("run.ps1", "run_backend.ps1"):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert ".env" in text and "TABULARIUM_PORT" in text, name
