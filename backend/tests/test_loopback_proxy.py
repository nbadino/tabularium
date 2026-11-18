"""Calls to the app's own model servers on 127.0.0.1 never go through a proxy.

macOS hands Python its system proxy; a network one without a localhost exception sent every
readiness probe and recognition to the proxy, so a local MLX server stayed "loading" forever.
"""
from __future__ import annotations

import urllib.request

import requests

from app import config

_NAMES = ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")


def _clean(monkeypatch, system: dict[str, str]) -> None:
    for name in _NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(urllib.request, "getproxies", lambda: dict(system))


def test_a_system_proxy_is_kept_for_the_internet_and_skipped_for_loopback(monkeypatch):
    _clean(monkeypatch, {"http": "http://proxy.example:8080"})
    config.bypass_proxy_for_loopback()
    assert urllib.request.getproxies_environment()["http"] == "http://proxy.example:8080"
    for url in ("http://127.0.0.1:8889/v1/models", "http://localhost:8889/v1", "http://[::1]:8889/v1"):
        assert requests.utils.should_bypass_proxies(url, None), url
        assert urllib.request.proxy_bypass_environment(url.split("/")[2].rsplit(":", 1)[0].strip("[]"))
    assert not requests.utils.should_bypass_proxies("https://huggingface.co/api/models", None)


def test_an_existing_no_proxy_list_is_extended_not_replaced(monkeypatch):
    _clean(monkeypatch, {})
    monkeypatch.setenv("NO_PROXY", "intranet.example")
    config.bypass_proxy_for_loopback()
    hosts = config.os.environ["no_proxy"].split(",")
    assert hosts[0] == "intranet.example" and "127.0.0.1" in hosts
    config.bypass_proxy_for_loopback()
    assert config.os.environ["no_proxy"].split(",").count("127.0.0.1") == 1
