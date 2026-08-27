"""Test frontend built servito dal backend (single-process, M8).

Viene eseguito solo se `frontend/dist` esiste (build già effettuata).
"""
from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from app import config
from app.main import app

pytestmark = pytest.mark.skipif(
    not (config.FRONTEND_DIST / "index.html").exists(),
    reason="frontend/dist non presente: eseguire la build del frontend",
)


def test_spa_served_at_root():
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "root" in r.text


def test_api_registered_over_spa():
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/nonexistent").status_code == 404