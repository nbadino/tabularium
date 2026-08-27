"""Test deskew: endpoint, stato e interazione con blocchi esistenti."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from app.main import app
from app.services import dewarp
from app.services.images import deskew


def _make_skewed(archive: Path) -> None:
    img = Image.new("RGB", (1100, 1500), "white")
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 44)
    except Exception:
        f = ImageFont.load_default()
    for i, t in enumerate(["LLOYD'S LIST", "Steam & Sailing Vessels", "Belle of the Seas"]):
        d.text((150, 160 + i * 160), t, fill="black", font=f)
    img = img.rotate(3.5, expand=False, fillcolor="white")
    img.save(archive / "p.png")


def _setup(tmp_path: Path) -> int:
    archive = tmp_path / "archive"
    archive.mkdir(parents=True)
    _make_skewed(archive)
    with TestClient(app) as client:
        pid = client.post(
            "/api/projects", json={"name": "DK", "archive_dir": str(archive)}
        ).json()["id"]
        client.post(f"/api/projects/{pid}/scan")
    return pid


def test_deskew_flow(tmp_path: Path):
    pid = _setup(tmp_path / "dk")
    with TestClient(app) as client:
        page = client.get(f"/api/projects/{pid}/pages").json()["items"][0]
        pid_ = page["id"]

        # pagina senza blocchi -> deskew ok
        r = client.post(f"/api/pages/{pid_}/deskew")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["deskewed"] is True
        assert abs(body["angle"]) > 0.5  # angolo stimato ~ -3.5

        # preview e thumbnail si rigenerano dal deskew
        assert client.get(f"/api/pages/{pid_}/preview").status_code == 200

        # con blocchi -> 409 senza conferma, ok con ?confirm=true
        client.put(
            f"/api/pages/{pid_}/annotations",
            json={"items": [{"label": "Text", "kind": "rect", "points": [[0, 0], [100, 50]], "content": "x", "order_idx": 1}]},
        )
        r = client.post(f"/api/pages/{pid_}/deskew")
        assert r.status_code == 409
        r = client.post(f"/api/pages/{pid_}/deskew?confirm=true")
        assert r.status_code == 200
        blocks = client.get(f"/api/pages/{pid_}/annotations").json()["items"]
        assert blocks == []  # blocchi eliminati dalla conferma

        # rimozione deskew
        r = client.delete(f"/api/pages/{pid_}/deskew")
        assert r.status_code == 200
        assert r.json()["deskewed"] is False


def test_align_levels(tmp_path: Path):
    pid = _setup(tmp_path / "al")
    with TestClient(app) as client:
        page = client.get(f"/api/projects/{pid}/pages").json()["items"][0]
        pid_ = page["id"]

        # livello non valido -> 422
        assert client.post(
            f"/api/pages/{pid_}/align", json={"level": "max"}
        ).status_code == 422

        # high su pagina vuota -> applicato
        r = client.post(f"/api/pages/{pid_}/align", json={"level": "high", "strength": 1.0})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["applied"] is True
        assert body["level"] == "high"
        # preview rigenerata dal dewarp
        assert client.get(f"/api/pages/{pid_}/preview").status_code == 200

        # basic (solo rotazione) sempre applicabile
        r = client.post(f"/api/pages/{pid_}/align", json={"level": "basic"})
        assert r.status_code == 200 and r.json()["applied"] is True

        # con blocchi -> 409, con confirm -> ok
        client.put(
            f"/api/pages/{pid_}/annotations",
            json={"items": [{"label": "Text", "kind": "rect", "points": [[0, 0], [100, 50]], "content": "x", "order_idx": 1}]},
        )
        assert client.post(f"/api/pages/{pid_}/align", json={"level": "medium"}).status_code == 409
        assert client.post(
            f"/api/pages/{pid_}/align?confirm=true", json={"level": "medium"}
        ).status_code == 200

        # il reset protegge le lavoro annotato come le altre trasformazioni
        client.put(
            f"/api/pages/{pid_}/annotations",
            json={"items": [{"label": "Text", "kind": "rect", "points": [[0, 0], [100, 50]], "content": "x", "order_idx": 1}]},
        )
        assert client.delete(f"/api/pages/{pid_}/deskew").status_code == 409
        assert client.delete(f"/api/pages/{pid_}/deskew?confirm=true").status_code == 200
        assert client.get(f"/api/pages/{pid_}/annotations").json()["items"] == []


def test_dewarp_levels_never_use_unsafe_fallback(monkeypatch):
    """Senza il motore neurale, medium/high devono essere identici al deskew."""
    image = Image.new("RGB", (480, 640), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 40, 450, 600), outline="black", width=4)
    expected, expected_angle = deskew(image)
    monkeypatch.setattr(dewarp, "_uvdoc_dewarp", lambda _image: None)

    for level in ("medium", "high"):
        actual, angle = dewarp.align_page(image, level=level)
        assert actual.size == image.size
        assert angle == expected_angle
        assert actual.tobytes() == expected.tobytes()


def test_fit_without_crop_rejects_distorted_aspect_ratio():
    image = Image.new("RGB", (1000, 1000), "white")
    assert dewarp._fit_without_crop(image, (1000, 1500)) is None


def test_fit_without_crop_rejects_content_moved_to_edge():
    source = Image.new("RGB", (400, 600), "white")
    ImageDraw.Draw(source).rectangle((30, 30, 370, 570), outline="black", width=8)
    candidate = Image.new("RGB", (400, 600), "white")
    ImageDraw.Draw(candidate).rectangle((0, 0, 399, 599), outline="black", width=8)
    assert dewarp._fit_without_crop(candidate, source.size, source=source) is None
