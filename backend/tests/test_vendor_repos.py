"""Test per il clone automatico del repo ufficiale MonkeyOCRv2 (nessun
passaggio manuale richiesto all'utente)."""
from __future__ import annotations

import pytest

from app.services import vendor_repos


def test_ensure_repo_is_a_noop_when_already_cloned(monkeypatch, tmp_path):
    monkeypatch.setattr(vendor_repos.config, "ROOT_DIR", tmp_path)
    repo_dir = vendor_repos.monkeyocrv2_repo_dir()
    (repo_dir / "parsing").mkdir(parents=True)
    (repo_dir / "parsing" / "serve.py").write_text("# already here", encoding="utf-8")

    calls: list[list[str]] = []
    monkeypatch.setattr(vendor_repos.subprocess, "run", lambda *a, **k: calls.append(a[0]))

    result = vendor_repos.ensure_monkeyocrv2_repo()

    assert result == repo_dir
    assert calls == []  # nessun clone: era già presente


def test_ensure_repo_clones_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(vendor_repos.config, "ROOT_DIR", tmp_path)
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)

        class Result:
            stderr = ""

        return Result()

    monkeypatch.setattr(vendor_repos.subprocess, "run", fake_run)

    result = vendor_repos.ensure_monkeyocrv2_repo()

    assert result == vendor_repos.monkeyocrv2_repo_dir()
    assert calls and calls[0][:2] == ["git", "clone"]
    assert vendor_repos.MONKEYOCRV2_REPO_URL in calls[0]


def test_ensure_repo_raises_readable_error_on_clone_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(vendor_repos.config, "ROOT_DIR", tmp_path)

    def failing_run(*args, **kwargs):
        raise vendor_repos.subprocess.CalledProcessError(1, args[0], stderr="network unreachable")

    monkeypatch.setattr(vendor_repos.subprocess, "run", failing_run)

    with pytest.raises(RuntimeError, match="clone automatico"):
        vendor_repos.ensure_monkeyocrv2_repo()
