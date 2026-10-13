"""Test delle classi di progetto: origine, classi note per modello, validazione."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services import dataset_builder, labeling


def _project(tmp_path: Path) -> int:
    archive = tmp_path / "archive"
    archive.mkdir(parents=True)
    Image.new("RGB", (1200, 1600), (200, 200, 200)).save(archive / "p01.png")
    with TestClient(app) as client:
        pid = client.post(
            "/api/projects", json={"name": "L", "archive_dir": str(archive)}
        ).json()["id"]
        client.post(f"/api/projects/{pid}/scan")
    return pid


def test_labels_have_origin_and_known_models(tmp_path: Path):
    with TestClient(app) as client:
        pid = _project(tmp_path / "labels")
        body = client.get(f"/api/projects/{pid}/labels").json()
        by_name = {l["name"]: l for l in body["labels"]}

        # `Title` è pubblica: il modello base la conosce, quindi ha modelli noti.
        assert by_name["Title"]["origin"] == "public"
        assert by_name["Title"]["known_models"]
        # `Issue-date` è custom: nessun modello base la emette già.
        assert by_name["Issue-date"]["origin"] == "custom"
        assert by_name["Issue-date"]["known_models"] == []
        # Il numero del fascicolo è custom ma alcuni modelli lo emettono
        # nativamente (page_number / number → Issue-number).
        assert by_name["Issue-number"]["origin"] == "custom"
        assert by_name["Issue-number"]["known_models"]


def test_project_labels_add_and_remove(tmp_path: Path):
    with TestClient(app) as client:
        pid = _project(tmp_path / "custom")
        custom = {
            "name": "Vessel-name",
            "color": "#3a5f7d",
            "short": "VSL",
            "prompt_kind": "text",
            "prompt": "Please output the text content from the image.",
        }
        out = client.put(f"/api/projects/{pid}/labels", json={"labels": [custom]}).json()
        names = {l["name"]: l for l in out["labels"]}
        assert "Vessel-name" in names
        assert names["Vessel-name"]["origin"] == "custom"

        # La classe resta nel progetto: una GET successiva la ritrova.
        again = client.get(f"/api/projects/{pid}/labels").json()
        assert "Vessel-name" in {l["name"] for l in again["labels"]}

        # Rimuovere le custom non tocca i default.
        cleared = client.put(f"/api/projects/{pid}/labels", json={"labels": []}).json()
        cleared_names = {l["name"] for l in cleared["labels"]}
        assert "Vessel-name" not in cleared_names
        assert "Title" in cleared_names


def test_project_labels_validation(tmp_path: Path):
    with TestClient(app) as client:
        pid = _project(tmp_path / "invalid")

        def put(label: dict, lang: str | None = None):
            headers = {"Accept-Language": lang} if lang else {}
            return client.put(f"/api/projects/{pid}/labels", json={"labels": [label]}, headers=headers)

        base = {
            "name": "Ok",
            "color": "#3a5f7d",
            "short": "OK",
            "prompt_kind": "text",
            "prompt": "text",
        }

        # Nome che collide con una classe di default: additivo → rifiutato.
        assert put({**base, "name": "Title"}).status_code == 400
        # Colore non valido.
        assert put({**base, "color": "rosso"}).status_code == 400
        # Tipo sconosciuto.
        assert put({**base, "prompt_kind": "magic"}).status_code == 400
        # Tipo testo senza prompt.
        assert put({**base, "prompt": ""}).status_code == 400

        # Messaggio localizzato: l'inglese arriva tradotto.
        res = put({**base, "color": "rosso"}, lang="en")
        assert "colour" in res.json()["detail"]


def test_create_single_custom_label(tmp_path: Path):
    with TestClient(app) as client:
        pid = _project(tmp_path / "create")
        label = {
            "name": "Vessel-name",
            "color": "#3a5f7d",
            "short": "VSL",
            "prompt_kind": "text",
            "prompt": "text",
            "aliases": ["ship_name"],
        }
        out = client.post(f"/api/projects/{pid}/labels", json=label)
        assert out.status_code == 200, out.text
        names = {l["name"]: l for l in out.json()["labels"]}
        assert names["Vessel-name"]["aliases"] == ["ship_name"]

        # Una seconda classe non riscrive la prima (POST ≠ replace).
        other = {**label, "name": "Port-of-call", "short": "PRT", "aliases": []}
        assert client.post(f"/api/projects/{pid}/labels", json=other).status_code == 200
        after = {l["name"] for l in client.get(f"/api/projects/{pid}/labels").json()["labels"]}
        assert {"Vessel-name", "Port-of-call"} <= after

        # Duplicato → 409, non silenzioso.
        dup = client.post(
            f"/api/projects/{pid}/labels",
            json=label,
            headers={"Accept-Language": "en"},
        )
        assert dup.status_code == 409
        assert "duplicate" in dup.json()["detail"].lower()

        # Nome di default → 400 (i default non si ridefiniscono).
        assert client.post(
            f"/api/projects/{pid}/labels", json={**label, "name": "Title"}
        ).status_code == 400


def test_project_labels_aliases_roundtrip_and_validation(tmp_path: Path):
    with TestClient(app) as client:
        pid = _project(tmp_path / "aliases")
        payload = {
            "name": "Vessel-name",
            "color": "#3a5f7d",
            "short": "VSL",
            "prompt_kind": "text",
            "prompt": "text",
            "aliases": ["ship_name", "vessel"],
        }
        out = client.put(f"/api/projects/{pid}/labels", json={"labels": [payload]}).json()
        entry = next(l for l in out["labels"] if l["name"] == "Vessel-name")
        assert entry["aliases"] == ["ship_name", "vessel"]

        # Un sinonimo con caratteri non ammessi è rifiutato, con messaggio tradotto.
        bad = {**payload, "aliases": ["bad/alias"]}
        res = client.put(
            f"/api/projects/{pid}/labels",
            json={"labels": [bad]},
            headers={"Accept-Language": "en"},
        )
        assert res.status_code == 400
        assert "alias" in res.json()["detail"].lower()


def test_default_labels_expose_native_aliases(tmp_path: Path):
    with TestClient(app) as client:
        pid = _project(tmp_path / "default-aliases")
        body = client.get(f"/api/projects/{pid}/labels").json()
        by_name = {l["name"]: l for l in body["labels"]}
        # La data del fascicolo accetta il sinonimo nativo `document_date`.
        assert "document_date" in by_name["Issue-date"]["aliases"]
        assert "grid" in by_name["Table"]["aliases"]


def test_project_label_info_reads_settings(tmp_path: Path):
    with TestClient(app) as client:
        pid = _project(tmp_path / "builder")
        client.put(
            f"/api/projects/{pid}/labels",
            json={
                "labels": [
                    {
                        "name": "Vessel-name",
                        "color": "#3a5f7d",
                        "short": "VSL",
                        "prompt_kind": "text",
                        "prompt": "Please output the text content from the image.",
                    }
                ]
            },
        )
    info = dataset_builder._project_label_info(pid)
    assert "Vessel-name" in info
    assert info["Vessel-name"].prompt_kind == "text"
    # I default restano presenti insieme alle custom.
    assert "Title" in info


def test_known_models_are_model_relative():
    # `Title` è nota a molti; una classe custom non è nota a nessuno.
    assert labeling.label_origin("Title") == "public"
    assert labeling.label_origin("Vessel-name") == "custom"
