"""Indra line reader for Lloyd's registers: adapter, installation, gateway.

The package itself (`rec.onnx`, `lloyds_lines.py`) is built outside Tabularium and is
never in the repository: the gateway is exercised with a stand-in package folder.
"""
from __future__ import annotations

import importlib.util
import io
import shutil
import sys
import time
import types
from pathlib import Path

import pytest
from PIL import Image

from app import config
from app.db import init_db

REPO = Path(__file__).resolve().parents[2]
GATEWAY = REPO / "scripts" / "cloud" / "indra_native_gateway.py"
ADAPTER = "indra-lloyds"
OTSL = "<fcel>VESSEL—Owner<fcel>Flg<nl><fcel>Triona (B.P.C.)<fcel>Br"

READER_SOURCE = '''
LOADED = []


class Reader:
    def read(self, gray):
        assert gray.ndim == 2 and str(gray.dtype) == "uint8"
        if int(gray[0, 0]) == 0:   # a black page stands for "no line found"
            return {"otsl": "", "lines": [], "ys": [], "ys_input": [], "x0": 0.0, "x1": 0.0,
                    "pitch": 0.0, "seconds": {}}
        return {
            "otsl": %r,
            "lines": [["VESSEL|Flg", [1, 2]], ["Triona (B.P.C.)|Br", [1, 2]]],
            "ys": [20.0, 60.0], "ys_input": [22.0, 61.0], "x0": 10.0, "x1": 180.0,
            "pitch": 40.0, "seconds": {"flatten": 0.1, "read": 0.2, "table": 0.0},
        }


def load(model_dir, providers=None, threads=4):
    LOADED.append({"model_dir": model_dir, "providers": providers, "threads": threads})
    return Reader()
''' % OTSL


def _package(parent: Path) -> Path:
    folder = parent / "release"
    folder.mkdir()
    (folder / "rec.onnx").write_bytes(b"onnx")
    (folder / "meta.json").write_text("{}", encoding="utf-8")
    (folder / "lloyds_lines.py").write_text(READER_SOURCE, encoding="utf-8")
    return folder


def _gateway_module():
    spec = importlib.util.spec_from_file_location("indra_native_gateway_lines_under_test", GATEWAY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def _clean_model_dir():
    yield
    shutil.rmtree(config.MODELS_DIR / ADAPTER, ignore_errors=True)


# --- declaration ------------------------------------------------------------


def test_the_reader_is_in_the_catalogue_with_every_surface_declared():
    from app.models import SPECS, NotApplicable
    from app.services.model_adapters import get_adapter

    spec = SPECS[ADAPTER]
    # The recipe launches the gateway here and nowhere else: the weights are a local package.
    assert spec.serve.value.local_only is True and spec.serve.value.hf_repo == ""
    assert isinstance(spec.train, NotApplicable) and isinstance(spec.cloud, NotApplicable)
    cap = get_adapter(ADAPTER).capabilities
    assert cap.table_format == "otsl"
    assert cap.hf_repo == ""
    assert cap.local_runtimes == ("onnxruntime",)
    assert cap.install_files == ("rec.onnx", "meta.json", "lloyds_lines.py")


def test_it_is_not_offered_on_a_rented_gpu_and_cannot_be_downloaded():
    init_db()
    from app.services import model_registry

    item = next(m for m in model_registry.list_models() if m["adapter_id"] == ADAPTER)
    assert item["downloadable"] is False
    assert item["vast_serve_ready"] is False and item["cloud_serve_ready"] is False
    assert item["local_serve_ready"] is True
    assert item["supports_native"] is True


# --- installation -----------------------------------------------------------


def test_the_package_folder_is_installed_by_import(tmp_path, _clean_model_dir):
    init_db()
    from app.services import model_registry

    assert model_registry.is_installed(ADAPTER) is False
    state = model_registry.import_local(ADAPTER, _package(tmp_path))
    assert state["installed"] is True
    assert (config.MODELS_DIR / ADAPTER / "lloyds_lines.py").is_file()


def test_a_folder_without_the_reader_module_is_refused(tmp_path, _clean_model_dir):
    init_db()
    from app.services import model_registry

    folder = _package(tmp_path)
    (folder / "lloyds_lines.py").unlink()
    with pytest.raises(ValueError, match="lloyds_lines.py"):
        model_registry.import_local(ADAPTER, folder)
    assert model_registry.is_installed(ADAPTER) is False


def test_other_models_keep_the_checkpoint_rule(tmp_path):
    from app.services import model_registry

    with pytest.raises(ValueError, match="config.json"):
        model_registry.import_local("glm-ocr", _package(tmp_path))


# --- serving ----------------------------------------------------------------


def test_local_serve_command_runs_the_gateway_with_the_line_reader():
    from app.services import indra_runtime
    from app.services.model_adapters import get_adapter

    argv = get_adapter(ADAPTER).serve_command("/models/indra-lloyds", 8889)
    assert argv[0] == str(indra_runtime.python_bin())
    assert Path(argv[1]).name == "indra_native_gateway.py"
    assert argv[argv.index("--model-dir") + 1] == "/models/indra-lloyds"
    assert argv[argv.index("--line-reader") + 1] == "lloyds_lines.py"
    assert argv[argv.index("--served-model-name") + 1] == "indra-lloyds"
    assert argv[argv.index("--host") + 1] == "127.0.0.1"


def test_a_local_only_recipe_is_not_in_the_rented_gpu_list():
    from app.services import serve_recipes

    remote = {item["adapter_id"] for item in serve_recipes.remote_models()}
    assert ADAPTER not in remote and "indra-ocr" in remote


def test_a_rented_gpu_setup_is_refused_instead_of_failing_on_the_instance():
    from app.services import cloud_manager

    with pytest.raises(ValueError, match="solo su questa macchina"):
        cloud_manager.build_provision_recipe(ADAPTER)


def test_workflow_settings_become_gateway_flags():
    from app.services import serve_recipes

    argv = serve_recipes.apply_workflow_overrides(
        ADAPTER, ["python", "gateway.py"], {"device": "cpu", "threads": 2},
    )
    assert argv[argv.index("--device") + 1] == "cpu"
    assert argv[argv.index("--threads") + 1] == "2"


def test_the_runtime_installs_what_the_package_imports():
    from app.services import indra_runtime

    first_step = indra_runtime.packages(gpu=False)[0]
    assert any(spec.startswith("scipy") for spec in first_step)
    assert any(spec.startswith("indra-ocr") for spec in first_step)


# --- client -----------------------------------------------------------------


def test_the_endpoint_is_the_gateway_itself():
    from app.services import inference

    assert inference._native_url(ADAPTER, "local", "http://127.0.0.1:8889/v1") == "http://127.0.0.1:8889"


def test_a_page_comes_back_as_one_otsl_table(monkeypatch):
    from app.services import inference

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "width": 200, "height": 100,
                "blocks": [{"label": "Table", "bbox": [10.0, 2.0, 180.0, 81.0], "text": OTSL}],
                "markdown": "", "lines": [{"text": "a"}, {"text": "b"}], "timings": {"read": 0.2},
            }

    monkeypatch.setattr(inference.requests, "post", lambda url, **kwargs: Response())
    client = inference.VllmClient(
        url="http://127.0.0.1:8889/v1", model="indra-lloyds",
        adapter=inference.get_adapter(ADAPTER), native_url="http://127.0.0.1:8889",
    )
    items = client.indra_native_page(Image.new("RGB", (200, 100), "white"))
    assert items == [{"bbox": [50, 20, 900, 810], "label": "Table", "content": OTSL}]
    assert client.last_trace["lines"] == 2


def test_the_table_content_is_valid_otsl():
    from app.domain import otsl

    grid = otsl.otsl_to_grid(OTSL)
    assert (grid["rows"], grid["cols"]) == (2, 2)


# --- gateway ----------------------------------------------------------------


def _ready_client(monkeypatch, folder: Path, *extra: str):
    from fastapi.testclient import TestClient

    ort = types.ModuleType("onnxruntime")
    ort.get_available_providers = lambda: ["CPUExecutionProvider"]
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    gateway = _gateway_module()
    app = gateway.create_app(gateway.parse_args([
        "--model-dir", str(folder), "--line-reader", "lloyds_lines.py",
        "--served-model-name", "indra-lloyds", "--threads", "2", *extra,
    ]))
    client = TestClient(app)
    for _ in range(100):
        if client.get("/health").json()["status"] != "starting":
            break
        time.sleep(0.02)
    return client, app


def _png(color: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (200, 100), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_gateway_reads_a_page_with_the_package_reader(monkeypatch, tmp_path):
    folder = _package(tmp_path)
    client, app = _ready_client(monkeypatch, folder)
    assert client.get("/health").json()["status"] == "ready"
    assert client.get("/v1/models").json()["data"][0]["id"] == "indra-lloyds"
    reply = client.post("/parse", content=_png("white"))
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert (body["width"], body["height"]) == (200, 100)
    # The table spans the lines found, half a line pitch above and below, inside the page.
    assert body["blocks"] == [{"label": "Table", "bbox": [10.0, 2.0, 180.0, 81.0], "text": OTSL}]
    assert [line["text"] for line in body["lines"]] == ["VESSEL|Flg", "Triona (B.P.C.)|Br"]
    assert body["timings"] == {"flatten": 0.1, "read": 0.2, "table": 0.0}
    loaded = app.state.gateway.reader_module.LOADED
    assert loaded == [{"model_dir": str(folder), "providers": ["CPUExecutionProvider"], "threads": 2}]


def test_gateway_returns_no_block_for_a_page_without_lines(monkeypatch, tmp_path):
    client, _app = _ready_client(monkeypatch, _package(tmp_path))
    body = client.post("/parse", content=_png("black")).json()
    assert body["blocks"] == [] and body["lines"] == []


def test_gateway_fails_loudly_when_the_reader_module_is_missing(monkeypatch, tmp_path):
    folder = _package(tmp_path)
    (folder / "lloyds_lines.py").unlink()
    client, _app = _ready_client(monkeypatch, folder)
    health = client.get("/health").json()
    assert health["status"] == "failed" and "lloyds_lines.py" in health["error"]
    assert client.post("/parse", content=_png("white")).status_code == 503


def test_gateway_refuses_a_reader_outside_the_model_folder():
    gateway = _gateway_module()
    with pytest.raises(SystemExit):
        gateway.parse_args(["--model-dir", "/m", "--line-reader", "../evil.py"])


# --- whole-page packages: several blocks, the stock models, a package folder ----

PAGE_READER_SOURCE = '''
from lloyds_reader_standin import BLOCKS   # a package next to this file

LOADED = []


class Reader:
    def read(self, gray):
        return {"otsl": "<fcel>a", "lines": [], "ys": [50.0], "ys_input": [50.0], "x0": 0.0,
                "x1": 200.0, "pitch": 20.0, "seconds": {}, "blocks": BLOCKS}


def load(model_dir, providers=None, threads=4, stock_dir=None):
    LOADED.append({"model_dir": model_dir, "stock_dir": stock_dir})
    return Reader()
'''

PAGE_BLOCKS = [
    {"label": "Title", "bbox": [60.0, 2.0, 140.0, 8.0], "text": "LLOYD'S SHIPPING INDEX"},
    {"label": "Issue-date", "bbox": [150.0, 2.0, 198.0, 8.0], "text": "[Mon., Apr. 3, 1950."},
    {"label": "Issue-number", "bbox": [2.0, 2.0, 12.0, 8.0], "text": "176"},
    {"label": "Page-header", "bbox": [20.0, 2.0, 50.0, 8.0], "text": "TRI-TUR"},
    {"label": "Table", "bbox": [0.0, 10.0, 200.0, 80.0], "text": "<fcel>a"},
    {"label": "Section-header", "bbox": [80.0, 40.0, 120.0, 44.0], "text": "SAILING VESSELS."},
    {"label": "Advertisement", "bbox": [0.0, 82.0, 200.0, 98.0], "text": "COALS"},
    {"label": "Text", "bbox": [0.0, 98.0, 200.0, 100.0], "text": "Printed by Lloyd's"},
]


def _page_package(parent: Path) -> Path:
    folder = _package(parent)
    (folder / "lloyds_lines.py").write_text(PAGE_READER_SOURCE, encoding="utf-8")
    package = folder / "lloyds_reader_standin"
    package.mkdir()
    (package / "__init__.py").write_text(f"BLOCKS = {PAGE_BLOCKS!r}\n", encoding="utf-8")
    return folder


def test_gateway_passes_the_readers_blocks_through_and_gives_it_the_stock_models(monkeypatch, tmp_path):
    folder = _page_package(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "lloyds_reader_standin", raising=False)
    client, app = _ready_client(monkeypatch, folder, "--stock-model-dir", "/models/indra-ocr")
    assert client.get("/health").json()["status"] == "ready", client.get("/health").json()
    body = client.post("/parse", content=_png("white")).json()
    assert body["blocks"] == PAGE_BLOCKS
    assert app.state.gateway.reader_module.LOADED == [
        {"model_dir": str(folder), "stock_dir": "/models/indra-ocr"},
    ]


def test_gateway_drops_a_block_it_cannot_place(monkeypatch, tmp_path):
    folder = _page_package(tmp_path)
    broken = PAGE_BLOCKS[:1] + [{"label": "Text", "bbox": [1, 2], "text": "x"}, {"label": "Text", "text": "y"}]
    (folder / "lloyds_reader_standin" / "__init__.py").write_text(f"BLOCKS = {broken!r}\n", encoding="utf-8")
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "lloyds_reader_standin", raising=False)
    client, _app = _ready_client(monkeypatch, folder)
    assert client.post("/parse", content=_png("white")).json()["blocks"] == PAGE_BLOCKS[:1]


def test_a_table_only_package_still_loads_when_the_stock_models_are_offered(monkeypatch, tmp_path):
    # Release t7b: `load(model_dir, providers, threads)` knows nothing of `stock_dir`.
    folder = _package(tmp_path)
    client, _app = _ready_client(monkeypatch, folder, "--stock-model-dir", "/models/indra-ocr")
    assert client.get("/health").json()["status"] == "ready"
    assert client.post("/parse", content=_png("white")).json()["blocks"][0]["label"] == "Table"


def test_the_serve_command_points_at_the_stock_indra_models():
    from app.services.model_adapters import get_adapter

    argv = get_adapter(ADAPTER).serve_command("/models/indra-lloyds", 8889)
    assert argv[argv.index("--stock-model-dir") + 1] == str(config.MODELS_DIR / "indra-ocr")
    assert argv[argv.index("--line-reader") + 1] == "lloyds_lines.py"


def test_every_block_of_the_page_keeps_its_label():
    from app.services.model_adapters import get_adapter

    payload = {"width": 200, "height": 100, "blocks": PAGE_BLOCKS, "markdown": ""}
    items = get_adapter(ADAPTER).parse_native_result(payload)
    assert [item["label"] for item in items] == [block["label"] for block in PAGE_BLOCKS]
    assert items[1] == {"bbox": [750, 20, 990, 80], "label": "Issue-date", "content": "[Mon., Apr. 3, 1950."}
    assert items[4]["content"] == "<fcel>a"


def test_the_labels_are_the_index_genres_own():
    from app.models import indra_lloyds
    from app.services.labeling import DEFAULT_LABELS

    assert set(indra_lloyds.LABELS) == {
        "Title", "Issue-date", "Issue-number", "Page-header", "Section-header",
        "Advertisement", "Text", "Table",
    }
    # Every one is a class a project has by default, so none is dropped at prefill.
    assert set(indra_lloyds.LABELS.values()) <= {label.name for label in DEFAULT_LABELS}


def test_the_runtime_installs_the_page_readers_imports_too():
    from app.services import indra_runtime

    assert any(spec.startswith("rapidfuzz") for spec in indra_runtime.packages(gpu=False)[0])
