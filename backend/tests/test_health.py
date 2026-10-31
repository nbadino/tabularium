from fastapi.testclient import TestClient

from app import db
from app.main import app


def test_health():
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"


def test_system_info():
    with TestClient(app) as client:
        r = client.get("/api/system/info")
        assert r.status_code == 200
        body = r.json()
        assert body["app"] == "Tabularium"
        assert body["schema_version"] == str(db.SCHEMA_VERSION)


def test_no_cross_origin_page_gets_credentialed_access():
    with TestClient(app) as client:
        r = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert "access-control-allow-origin" not in r.headers
