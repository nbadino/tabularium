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


def test_the_clone_is_pinned_to_one_upstream_commit(monkeypatch, tmp_path):
    """`parsing/serve.py` is executed: every install must run the same upstream code."""
    monkeypatch.setattr(vendor_repos.config, "ROOT_DIR", tmp_path)
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)

        class Result:
            stderr = ""

        return Result()

    monkeypatch.setattr(vendor_repos.subprocess, "run", fake_run)
    vendor_repos.ensure_monkeyocrv2_repo()
    assert len(vendor_repos.MONKEYOCRV2_COMMIT) == 40
    assert calls[-1][-2:] == ["--detach", vendor_repos.MONKEYOCRV2_COMMIT]


def _git(*args, cwd):
    import subprocess

    out = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


def _clone_at_old_commit(monkeypatch, tmp_path):
    """An upstream with two commits and a clone left at the first one, as an install
    made before the pin (or at an older pin) has."""
    upstream = tmp_path / "upstream"
    (upstream / "parsing").mkdir(parents=True)
    _git("init", "-q", cwd=upstream)
    (upstream / "parsing" / "serve.py").write_text("v1\n", encoding="utf-8")
    _git("add", ".", cwd=upstream)
    _git("commit", "-qm", "v1", cwd=upstream)
    old = _git("rev-parse", "HEAD", cwd=upstream)
    (upstream / "parsing" / "serve.py").write_text("v2\n", encoding="utf-8")
    _git("commit", "-qam", "v2", cwd=upstream)
    pinned = _git("rev-parse", "HEAD", cwd=upstream)

    monkeypatch.setattr(vendor_repos.config, "ROOT_DIR", tmp_path / "root")
    repo_dir = vendor_repos.monkeyocrv2_repo_dir()
    repo_dir.parent.mkdir(parents=True)
    _git("clone", "-q", str(upstream), str(repo_dir), cwd=tmp_path)
    _git("checkout", "-q", "--detach", old, cwd=repo_dir)
    monkeypatch.setattr(vendor_repos, "MONKEYOCRV2_COMMIT", pinned)
    return repo_dir, pinned


def test_an_existing_clone_is_moved_to_the_pin(monkeypatch, tmp_path):
    repo_dir, pinned = _clone_at_old_commit(monkeypatch, tmp_path)

    vendor_repos.ensure_monkeyocrv2_repo()

    assert _git("rev-parse", "HEAD", cwd=repo_dir) == pinned
    assert (repo_dir / "parsing" / "serve.py").read_text(encoding="utf-8") == "v2\n"


def test_a_clone_with_local_changes_is_left_alone(monkeypatch, tmp_path):
    repo_dir, pinned = _clone_at_old_commit(monkeypatch, tmp_path)
    (repo_dir / "parsing" / "serve.py").write_text("patched by the user\n", encoding="utf-8")

    vendor_repos.ensure_monkeyocrv2_repo()

    assert _git("rev-parse", "HEAD", cwd=repo_dir) != pinned
    assert (repo_dir / "parsing" / "serve.py").read_text(encoding="utf-8") == "patched by the user\n"


def test_a_shallow_clone_fetches_the_pin_it_lacks(monkeypatch, tmp_path):
    upstream = tmp_path / "upstream"
    (upstream / "parsing").mkdir(parents=True)
    _git("init", "-q", cwd=upstream)
    (upstream / "parsing" / "serve.py").write_text("v1\n", encoding="utf-8")
    _git("add", ".", cwd=upstream)
    _git("commit", "-qm", "v1", cwd=upstream)
    monkeypatch.setattr(vendor_repos.config, "ROOT_DIR", tmp_path / "root")
    repo_dir = vendor_repos.monkeyocrv2_repo_dir()
    repo_dir.parent.mkdir(parents=True)
    _git("clone", "-q", "--depth", "1", f"file://{upstream}", str(repo_dir), cwd=tmp_path)
    (upstream / "parsing" / "serve.py").write_text("v2\n", encoding="utf-8")
    _git("commit", "-qam", "v2", cwd=upstream)
    pinned = _git("rev-parse", "HEAD", cwd=upstream)
    monkeypatch.setattr(vendor_repos, "MONKEYOCRV2_COMMIT", pinned)

    vendor_repos.ensure_monkeyocrv2_repo()

    assert _git("rev-parse", "HEAD", cwd=repo_dir) == pinned
