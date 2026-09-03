"""Test deskew: endpoint, stato e interazione con blocchi esistenti."""
from __future__ import annotations

from pathlib import Path

import pytest

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from app.main import app
from app.services import dewarp, pages as pagesvc
from app.services.images import deskew, mesh_rectify, perspective_rectify
from app.db import connect


def _make_skewed(archive: Path) -> None:
    img = Image.new("RGB", (1100, 1500), "white")
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 44)
    except Exception:
        f = ImageFont.load_default()
    for i, t in enumerate(["HISTORIC SHIPPING INDEX", "Steam & Sailing Vessels", "Belle of the Seas"]):
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


def test_align_levels(tmp_path: Path, monkeypatch):
    # Il test verifica il contratto HTTP, non deve caricare il preprocessore.
    from app.services import monkey_preprocess

    monkeypatch.setattr(monkey_preprocess, "preprocess", lambda image, timeout=600.0: image)
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
    """Senza il preprocessore, medium/high devono essere identici al deskew."""
    from app.services import monkey_preprocess

    image = Image.new("RGB", (480, 640), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 40, 450, 600), outline="black", width=4)
    expected, expected_angle = deskew(image)
    monkeypatch.setattr(monkey_preprocess, "preprocess", lambda image, timeout=600.0: None)

    for level in ("medium", "high"):
        actual, angle = dewarp.align_page(image, level=level)
        assert actual.size == image.size
        assert angle == expected_angle
        assert actual.tobytes() == expected.tobytes()


def test_align_does_not_deskew_around_the_official_preprocessor(monkeypatch):
    """`core_runner.py` passa l'originale al `Preprocessor` e la rettifica
    include la rotazione: un deskew prima o dopo sarebbe geometria in più."""
    from app.services import monkey_preprocess

    seen: dict = {}
    rectified = Image.new("RGB", (480, 640), "green")

    def fake(image, timeout=600.0):
        seen["input"] = image.tobytes()
        return rectified

    monkeypatch.setattr(monkey_preprocess, "preprocess", fake)
    source = Image.new("RGB", (480, 640), "white")
    ImageDraw.Draw(source).rectangle((30, 40, 450, 600), outline="black", width=4)

    out, angle = dewarp.align_page(source, level="medium")

    assert seen["input"] == source.tobytes()  # originale, non il deskew
    assert angle == 0.0
    assert out.tobytes() == rectified.tobytes()


def test_only_the_official_rectifier_is_offered(monkeypatch):
    """I surrogati (UVDoc, DocScanner) sono usciti: chiederli è un errore, non
    un ripiego silenzioso su un altro motore."""
    for engine in ("uvdoc", "docscanner"):
        result = dewarp.run_transform(Image.new("RGB", (200, 300), "white"), engine)
        assert result.actual_engine == "none"
        assert result.error == "transform_engine_invalid"


def test_manual_rectifiers_preserve_canvas_and_validate_geometry():
    image = Image.new("RGB", (400, 600), "white")
    ImageDraw.Draw(image).rectangle((30, 40, 370, 560), outline="black", width=5)
    perspective = perspective_rectify(
        image,
        [[30, 40], [370, 40], [370, 560], [30, 560]],
    )
    assert perspective.size == image.size
    identity = [
        [[0.0, 0.0], [0.5, 0.0], [1.0, 0.0]],
        [[0.0, 0.5], [0.5, 0.5], [1.0, 0.5]],
        [[0.0, 1.0], [0.5, 1.0], [1.0, 1.0]],
    ]
    assert mesh_rectify(image, identity).size == image.size


def test_transform_candidate_accept_is_lossless_and_protected(tmp_path: Path):
    project_id = _setup(tmp_path / "candidate")
    with TestClient(app) as client:
        page = client.get(f"/api/projects/{project_id}/pages").json()["items"][0]
        page_id = page["id"]
        client.put(
            f"/api/pages/{page_id}/annotations",
            json={"items": [{"label": "Text", "kind": "rect", "points": [[0, 0], [100, 50]], "content": "x", "order_idx": 1}]},
        )
        response = client.post(
            f"/api/pages/{page_id}/transform/candidate",
            json={
                "engine": "perspective",
                "perspective_points": [[20, 20], [1079, 20], [1079, 1479], [20, 1479]],
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["candidate"]["actual_engine"] == "perspective"
        # La proposta non tocca il lavoro annotato.
        assert len(client.get(f"/api/pages/{page_id}/annotations").json()["items"]) == 1
        assert client.post(f"/api/pages/{page_id}/transform/accept").status_code == 409
        accepted = client.post(f"/api/pages/{page_id}/transform/accept?confirm=true")
        assert accepted.status_code == 200, accepted.text
        assert client.get(f"/api/pages/{page_id}/annotations").json()["items"] == []
        with Image.open(pagesvc.deskew_path(page_id)) as transformed:
            assert transformed.format == "PNG"
            assert transformed.size == (page["width"], page["height"])


def test_prefill_source_reuses_the_accepted_transform(tmp_path: Path):
    project_id = _setup(tmp_path / "prefill-master")
    with TestClient(app) as client:
        page = client.get(f"/api/projects/{project_id}/pages").json()["items"][0]
    active = Image.new("RGB", (page["width"], page["height"]), "red")
    path = pagesvc.deskew_path(page["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    active.save(path, "PNG")
    pagesvc.mark_transform(page["id"], "mesh", active.size)
    with connect() as conn:
        row = conn.execute("SELECT * FROM pages WHERE id=?", (page["id"],)).fetchone()
    image, angle = pagesvc.maybe_auto_deskew(row)
    assert angle == 0.0
    assert image.getpixel((10, 10)) == (255, 0, 0)


def test_monkeyocr_engine_uses_the_official_preprocessor(monkeypatch):
    """`monkeyocr` è lo stadio che `parsing/core_runner.py` esegue prima del
    parsing: la pagina va al `Preprocessor` così com'è, senza deskew nostro."""
    from app.services import monkey_preprocess

    seen: dict = {}
    rectified = Image.new("RGB", (1200, 1600), "green")

    def fake_preprocess(image, timeout=600.0):
        seen["size"] = image.size
        return rectified

    monkeypatch.setattr(monkey_preprocess, "preprocess", fake_preprocess)

    source = Image.new("RGB", (1200, 1600), "white")
    result = dewarp.run_transform(source, "monkeyocr")

    assert seen["size"] == (1200, 1600)  # nessuna riscalatura prima del modello
    assert result.actual_engine == "monkeyocr"
    assert result.error is None
    assert result.image.getpixel((10, 10)) == (0, 128, 0)


def test_monkeyocr_engine_falls_back_to_deskew_when_unavailable(monkeypatch):
    """Il preprocessore è opzionale: se l'ambiente non c'è, la pagina resta
    utilizzabile con il solo deskew invece di far fallire la trasformazione."""
    from app.services import monkey_preprocess

    monkeypatch.setattr(monkey_preprocess, "preprocess", lambda image, timeout=600.0: None)
    monkeypatch.setattr(monkey_preprocess, "last_error", lambda: "monkeyocr_preprocessor_unavailable")

    result = dewarp.run_transform(Image.new("RGB", (800, 1000), "white"), "monkeyocr")

    assert result.requested_engine == "monkeyocr"
    assert result.actual_engine == "deskew"
    assert result.error == "monkeyocr_preprocessor_unavailable"
    assert "neural_fallback_deskew" in result.warnings


def test_a_missing_source_says_which_file_is_missing(tmp_path: Path):
    """I percorsi delle pagine sono assoluti nel database: rinominare la
    cartella del progetto li invalida tutti insieme. Un «file sorgente non
    presente» nudo costringeva a interrogare il database per capirlo."""
    from fastapi import HTTPException

    from app.services import pages as pagesvc

    project_id = _setup(tmp_path / "sorgente-mancante")
    with TestClient(app) as client:
        page = client.get(f"/api/projects/{project_id}/pages").json()["items"][0]
    missing = str(tmp_path / "spostato" / "altrove.tif")
    with connect() as conn:
        conn.execute("UPDATE pages SET abs_path=? WHERE id=?", (missing, page["id"]))
        conn.commit()
        row = conn.execute("SELECT * FROM pages WHERE id=?", (page["id"],)).fetchone()

    error = pagesvc.source_missing(row)

    assert isinstance(error, HTTPException)
    assert error.status_code == 404
    assert missing in error.detail

    with pytest.raises(HTTPException) as raised:
        pagesvc.maybe_auto_deskew(row)
    assert missing in raised.value.detail
