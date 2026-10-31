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
