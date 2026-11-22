"""Tests for standalone image upload in the Playground API."""
from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app


class FakeOcr:
    available = True
    name = "fake-ocr"

    def detect(self, _image):
        return [{"bbox": [12, 24, 160, 60], "text": "Shipping Register", "score": 0.98}]


def test_playground_parse_upload_with_local_ocr(monkeypatch):
    from app.services import ocr as ocrmod

    monkeypatch.setattr(ocrmod, "OcrEngine", FakeOcr)
    buf = BytesIO()
    Image.new("RGB", (200, 120), "white").save(buf, format="PNG")
    with TestClient(app) as client:
        resp = client.post(
            "/api/playground/parse-upload",
            data={"engine": "ocr"},
            files={"file": ("sample.png", buf.getvalue(), "image/png")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["width"] == 200 and body["height"] == 120
        assert body["items"][0]["label"] == "Text"
        assert body["items"][0]["content"] == "Shipping Register"

        bad = client.post(
            "/api/playground/parse-upload",
            data={"engine": "ocr"},
            files={"file": ("broken.txt", b"not an image", "text/plain")},
        )
        assert bad.status_code == 422
