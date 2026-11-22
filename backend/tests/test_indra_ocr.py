"""Indra OCR (datalab-to/indra): adapter, recipes, gateway and local runtime.

The vendor facts these tests pin come from github.com/datalab-to/indra 1.1.2 and the
Hugging Face repo datalab-to/indra-ocr at tag v1.1.0 (see `app/models/indra_ocr.py`).
Nothing here imports `indra_ocr` itself: the gateway is exercised with a stand-in
module, as the other vendor gateways are.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import sys
import time
import types
from pathlib import Path

import pytest
from PIL import Image

from app.db import init_db

REPO = Path(__file__).resolve().parents[2]
GATEWAY = REPO / "scripts" / "cloud" / "indra_native_gateway.py"


def _gateway_module():
    spec = importlib.util.spec_from_file_location("indra_native_gateway_under_test", GATEWAY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- spec and adapter -------------------------------------------------------


def test_indra_is_registered_with_every_surface_declared():
    from app.models import SPECS
    from app.services import model_surface

    assert "indra-ocr" in SPECS
    row = next(row for row in model_surface.matrix() if row.adapter_id == "indra-ocr")
    assert all(state.ok for state in row.surfaces.values()), row.surfaces


def test_indra_labels_cover_every_vendor_block_class():
    from app.models.indra_ocr import INDRA_LABELS
    from app.services.labeling import PUBLIC_LABELS

    # `indra_ocr/order_post.py` BLOCK_CLASSES, plus the picture regions of pipeline.py.
    vendor = {
        "Text", "Section-Header", "Page-Header", "Page-Footer", "List-Group", "Table",
        "Caption", "Footnote", "Table-Of-Contents", "Form", "Picture",
    }
    assert set(INDRA_LABELS) == vendor
    assert set(INDRA_LABELS.values()) <= set(PUBLIC_LABELS)


def test_indra_adapter_declares_a_cpu_first_native_pipeline():
    from app.services import model_adapters

    adapter = model_adapters.get_adapter("indra-ocr")
    cap = adapter.capabilities
    assert cap.hf_repo == "datalab-to/indra-ocr" and cap.hf_revision == "v1.1.0"
    assert cap.local_runtimes == ("onnxruntime",)
    assert cap.serve_backend == "indra-native"
    assert adapter.native_runner == "indra"
    assert model_adapters.supported_prefill_modes(adapter)["supports_native"] is True
    assert model_adapters.supports_export(adapter) is False


def _payload(**extra):
    payload = {
        "width": 2000,
        "height": 1000,
        "markdown": (
            "## Title\n\n<table><tr><td>a</td><td>b</td></tr></table>\n\nsingle row"
        ),
        "blocks": [
            {"label": "Section-Header", "bbox": [100, 50, 900, 100], "text": "Title"},
            {"label": "Table", "bbox": [100, 200, 1900, 600], "text": "a b\nc d"},
            {"label": "Table", "bbox": [100, 650, 1900, 700], "text": "single row"},
            {"label": "Picture", "bbox": [0, 700, 400, 1000], "text": ""},
            {"label": "Something-New", "bbox": [500, 800, 600, 900], "text": " x "},
            {"label": "Text", "bbox": [10, 10, 10, 20], "text": "zero width"},
            {"label": "Text", "bbox": "broken", "text": "no box"},
        ],
    }
    payload.update(extra)
    return payload


def test_parse_native_result_normalizes_pixels_and_pairs_tables_with_their_html():
    from app.services import model_adapters

    items = model_adapters.get_adapter("indra-ocr").parse_native_result(_payload())
    assert items == [
        {"bbox": [50, 50, 450, 100], "label": "Section-header", "content": "Title"},
        # The one table of several lines is the one `<table>` of the markdown.
        {"bbox": [50, 200, 950, 600], "label": "Table",
         "content": "<table><tr><td>a</td><td>b</td></tr></table>"},
        {"bbox": [50, 650, 950, 700], "label": "Table", "content": "single row"},
        {"bbox": [0, 700, 200, 1000], "label": "Picture", "content": ""},
        # A label the map does not know reads as text, it is not dropped.
        {"bbox": [250, 800, 300, 900], "label": "Text", "content": "x"},
    ]


def test_parse_native_result_keeps_lines_when_tables_cannot_be_paired():
    from app.services import model_adapters

    items = model_adapters.get_adapter("indra-ocr").parse_native_result(
        _payload(markdown="<table>1</table><table>2</table>")
    )
    tables = [item["content"] for item in items if item["label"] == "Table"]
    assert tables == ["a b\nc d", "single row"]


def test_parse_native_result_rejects_a_page_without_size():
    from app.services import model_adapters

    adapter = model_adapters.get_adapter("indra-ocr")
    assert adapter.parse_native_result(_payload(width=0)) == []
    assert adapter.parse_native_result(None) == []
    # The client's own image size stands in for a missing one.
    assert adapter.parse_native_result(_payload(width=None), image_width=2000)


# --- serving recipe ----------------------------------------------------------


def test_serve_argv_is_the_gateway_with_workflow_flags():
    from app.services import serve_recipes

    recipe = serve_recipes.recipe_for("indra-ocr")
    assert recipe.installs_vllm is False
    argv = serve_recipes.serve_argv(
        recipe, model_path="/m/indra", port=8889, api_key="s3cret",
        settings={"workflow": {"device": "cuda", "threads": 2, "workers": 3, "math": True}},
    )
    assert argv[0] == "indra_native_gateway.py"
    assert argv[argv.index("--model-dir") + 1] == "/m/indra"
    assert argv[argv.index("--port") + 1] == "8889"
    assert argv[argv.index("--device") + 1] == "cuda"
    assert argv[argv.index("--threads") + 1] == "2"
    assert argv[argv.index("--workers") + 1] == "3"
    assert "--math" in argv and "--no-math" not in argv
    assert argv[argv.index("--served-model-name") + 1] == "indra-ocr"
    assert argv[argv.index("--api-key") + 1] == "s3cret"
    # Defaults are not written: the gateway's own (Indra's) apply.
    plain = serve_recipes.serve_argv(recipe, model_path="/m", port=1)
    assert "--threads" not in plain and "--math" not in plain
    with pytest.raises(ValueError):
        serve_recipes.serve_argv(recipe, model_path="/m", port=1, lora_path="/lora")


def test_gateway_defaults_match_the_spec_defaults():
    from app.models.indra_ocr import INDRA_WORKFLOW

    args = _gateway_module().parse_args(["--model-dir", "/m"])
    for field in INDRA_WORKFLOW:
        assert getattr(args, field.key) == field.default, field.key


def test_local_serve_command_runs_the_shipped_gateway_in_its_runtime():
    from app.services import indra_runtime, model_adapters

    argv = model_adapters.get_adapter("indra-ocr").serve_command("/models/indra", 8890)
    assert argv[0] == str(indra_runtime.python_bin())
    assert Path(argv[1]) == GATEWAY and GATEWAY.is_file()
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert argv[argv.index("--port") + 1] == "8890"


def test_vast_recipe_ships_the_gateway_and_pins_the_revision():
    from app.services import cloud_manager as cm

    init_db()
    recipe = cm.build_provision_recipe("indra-ocr", server_api_key="k")
    assert recipe["runtime"] == "indra-native"
    assert recipe["install_vllm"] is False
    assert recipe["hf_revision"] == "v1.1.0"
    assert recipe["venv_dir"].endswith("/indra-ocr")
    assert recipe["native_remote_port"] is None
    assert recipe["argv"][0] == "indra_native_gateway.py"
    assert base64.b64decode(recipe["native_gateway_b64"]) == GATEWAY.read_bytes()
    assert "indra-ocr==1.1.2" in recipe["pip_extra"]
    assert any(p.startswith("onnxruntime-gpu[cuda,cudnn]==") for p in recipe["pip_extra"])
    # Another checkpoint than the declared repo does not inherit its tag.
    other = cm.build_provision_recipe("indra-ocr", model="someone/indra-finetune")
    assert other["hf_revision"] == ""


def test_vast_setup_script_runs_the_gateway_without_vllm():
    script = (REPO / "scripts" / "cloud" / "setup_cloud_vllm.sh").read_text()
    branch = script[script.index('elif [ "$RECIPE_RUNTIME" = "indra-native" ]; then'):]
    branch = branch[: branch.index('elif [ "$RECIPE_INSTALL_VLLM" = "0" ]; then')]
    # CPU build first, then replaced: the two wheels install the same module.
    cpu = branch.index('pip install --quiet "${INDRA_CPU_PACKAGES[@]}"')
    remove = branch.index("pip uninstall --quiet -y onnxruntime")
    gpu = branch.index('pip install --quiet "${INDRA_GPU_PACKAGES[@]}"')
    assert cpu < remove < gpu
    assert "CUDAExecutionProvider" in branch and "vllm==" not in branch
    tail = script[script.rindex('if [ "$RECIPE_RUNTIME" = "indra-native" ]; then'):]
    assert 'SERVE_ARGV[0]="$NATIVE_GATEWAY"' in tail
    assert 'revision=os.environ.get("MODEL_REVISION")' in script


# --- client -----------------------------------------------------------------


@pytest.mark.parametrize("provider", ["local", "vast", "custom", "ssh"])
def test_native_url_is_the_endpoint_without_v1(provider):
    from app.services import inference

    assert inference._native_url("indra-ocr", provider, "http://127.0.0.1:8889/v1") == "http://127.0.0.1:8889"
    assert inference._native_url("indra-ocr", provider, "https://ocr.example.org/") == "https://ocr.example.org"


def test_indra_native_page_posts_the_page_and_returns_blocks(monkeypatch):
    from app.services import inference

    sent = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return _payload()

    def post(url, **kwargs):
        sent["url"] = url
        sent["headers"] = kwargs["headers"]
        sent["image"] = Image.open(io.BytesIO(kwargs["data"]))
        return Response()

    monkeypatch.setattr(inference.requests, "post", post)
    client = inference.VllmClient(
        url="http://127.0.0.1:8889/v1", model="indra-ocr", api_key="k",
        adapter=inference.get_adapter("indra-ocr"), native_url="http://127.0.0.1:8889",
    )
    items = client.indra_native_page(Image.new("RGB", (2000, 1000), "white"))
    assert sent["url"] == "http://127.0.0.1:8889/parse"
    assert sent["headers"]["Authorization"] == "Bearer k"
    assert sent["image"].size == (2000, 1000)
    assert [item["label"] for item in items][:2] == ["Section-header", "Table"]
    assert client.last_trace["items"] == len(items)


def test_indra_native_page_without_a_gateway_says_where_to_start_it():
    from app.services import inference

    client = inference.VllmClient(
        url="http://127.0.0.1:8889/v1", model="indra-ocr", adapter=inference.get_adapter("indra-ocr"),
    )
    client.native_url = None
    with pytest.raises(RuntimeError, match="gateway Indra OCR"):
        client.indra_native_page(Image.new("RGB", (8, 8)))


# --- hardware and local runtime ---------------------------------------------


def _cpu_linux():
    return {
        "apple_silicon": False,
        "memory_gb": 8,
        "cuda": {"available": False, "devices": []},
        "runtimes": {
            "vllm": {"supported": False}, "mlx-vlm": {"supported": False},
            "onnxruntime": {"supported": True},
        },
        "usable_runtimes": ["onnxruntime"],
    }


def test_indra_runs_on_a_cpu_only_server_and_vllm_models_still_say_why_not():
    from app.services import hardware, model_adapters

    indra = model_adapters.get_adapter("indra-ocr").capabilities
    assert hardware.pick_serve_runtime(indra, _cpu_linux()) == hardware.RUNTIME_ONNX
    plan = hardware.plan_local(indra.local_runtimes, machine=_cpu_linux(), approx_size_gb=indra.approx_size_gb)
    assert plan == {"runnable": True, "runtime": "onnxruntime", "reason": None}
    # A vLLM model on the same machine: the machine has no accelerator, as before.
    vllm_plan = hardware.plan_local(("vllm",), machine=_cpu_linux(), approx_size_gb=2)
    assert vllm_plan["reason"] == hardware.REASON_NO_LOCAL_RUNTIME


def test_indra_runtime_swaps_in_the_gpu_build_only_with_an_nvidia_gpu():
    from app.models.indra_ocr import ONNXRUNTIME_GPU_PACKAGE
    from app.services import indra_runtime

    assert indra_runtime.wants_gpu(system="Linux", gpus=[{"memory_total": 8192}]) is True
    assert indra_runtime.wants_gpu(system="Linux", gpus=[]) is False
    assert indra_runtime.wants_gpu(system="Darwin", gpus=[{"memory_total": 1}]) is False
    cpu = indra_runtime.packages(gpu=False)
    assert len(cpu) == 1 and cpu[0][0] == "indra-ocr==1.1.2"
    gpu = indra_runtime.packages(gpu=True)
    assert gpu[1] == ["--uninstall", "onnxruntime"] and gpu[2] == [ONNXRUNTIME_GPU_PACKAGE]


def test_serve_manager_prepares_the_indra_runtime_before_launching(monkeypatch, tmp_path):
    from app.services import indra_runtime, serve_manager

    calls = []
    monkeypatch.setattr(indra_runtime, "is_ready", lambda: False)
    monkeypatch.setattr(indra_runtime, "ensure_ready", lambda: calls.append("ensure"))
    monkeypatch.setattr(serve_manager, "is_installed", lambda _adapter_id: True)
    monkeypatch.setattr(serve_manager, "model_weights_path", lambda _adapter_id: tmp_path)

    def stop_here(*_args, **_kwargs):
        raise RuntimeError("stop before launching")

    monkeypatch.setattr(serve_manager, "stop", stop_here)
    init_db()
    with pytest.raises(RuntimeError, match="stop before launching"):
        serve_manager.start("indra-ocr", port=8891)
    assert calls == ["ensure"]


# --- gateway ----------------------------------------------------------------


def test_gateway_picks_cuda_only_when_onnxruntime_has_it():
    gateway = _gateway_module()
    cuda = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert gateway.pick_providers("auto", cuda) == cuda
    assert gateway.pick_providers("auto", ["CPUExecutionProvider"]) == ["CPUExecutionProvider"]
    assert gateway.pick_providers("cpu", cuda) == ["CPUExecutionProvider"]
    with pytest.raises(RuntimeError, match="onnxruntime-gpu"):
        gateway.pick_providers("cuda", ["CPUExecutionProvider"])


def _fake_indra(monkeypatch, loaded: list):
    class Line:
        def __init__(self, box, text, conf):
            self.box, self.text, self.conf = box, text, conf

    class Page:
        def __init__(self, shape):
            self.blocks = [{"label": "Text", "bbox": [1.0, 2.0, 30.0, 4.0], "text": "hello"}]
            self.lines = [Line([1.0, 2.0, 30.0, 4.0], "hello", 0.99)]
            self.markdown = "hello"
            self.source, self.rotation, self.skew, self.vertical = "ocr", 0, 0.0, False
            self.timings = {"total_ms": 1.0, "shape": list(shape)}

    class Converter:
        def ocr(self, gray):
            assert gray.ndim == 2 and str(gray.dtype) == "uint8"
            return Page(gray.shape)

    def load(**kwargs):
        loaded.append(kwargs)
        return Converter()

    indra = types.ModuleType("indra_ocr")
    indra.load = load
    ort = types.ModuleType("onnxruntime")
    ort.get_available_providers = lambda: ["CPUExecutionProvider"]
    monkeypatch.setitem(sys.modules, "indra_ocr", indra)
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)


def test_gateway_serves_models_health_and_parse(monkeypatch):
    from fastapi.testclient import TestClient

    loaded: list = []
    _fake_indra(monkeypatch, loaded)
    gateway = _gateway_module()
    app = gateway.create_app(gateway.parse_args([
        "--model-dir", "/m", "--workers", "2", "--threads", "1", "--api-key", "k",
    ]))
    client = TestClient(app)
    for _ in range(100):
        if client.get("/health").json()["status"] == "ready":
            break
        time.sleep(0.02)
    assert client.get("/health").json() == {"status": "ready", "providers": ["CPUExecutionProvider"]}
    assert len(loaded) == 2
    assert loaded[0] == {
        "path": "/m", "threads": 1, "mode": "ocr", "providers": ["CPUExecutionProvider"], "math": False,
    }
    assert client.get("/v1/models").status_code == 401
    auth = {"Authorization": "Bearer k"}
    assert client.get("/v1/models", headers=auth).json()["data"][0]["id"] == "indra-ocr"
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buffer, format="PNG")
    reply = client.post("/parse", content=buffer.getvalue(), headers=auth)
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert (body["width"], body["height"]) == (40, 20)
    assert body["blocks"] == [{"label": "Text", "bbox": [1.0, 2.0, 30.0, 4.0], "text": "hello"}]
    assert body["timings"]["shape"] == [20, 40]
    assert client.post("/parse", content=b"not an image", headers=auth).status_code == 415


def test_gateway_reports_a_failed_load_instead_of_hanging(monkeypatch):
    from fastapi.testclient import TestClient

    _fake_indra(monkeypatch, [])
    gateway = _gateway_module()
    app = gateway.create_app(gateway.parse_args(["--model-dir", "/m", "--device", "cuda"]))
    client = TestClient(app)
    for _ in range(100):
        if client.get("/health").json()["status"] != "starting":
            break
        time.sleep(0.02)
    health = client.get("/health").json()
    assert health["status"] == "failed" and "onnxruntime-gpu" in health["error"]
    assert client.get("/v1/models").status_code == 503


# --- regression: the gateway's imports must be installed (prod, 2026-11-22) ----


# Import name -> the distribution that provides it, for what the gateway imports.
_PROVIDED_BY = {
    "fastapi": "fastapi", "uvicorn": "uvicorn", "PIL": "pillow", "indra_ocr": "indra-ocr",
    # Dependencies of indra-ocr itself (its pyproject: numpy, onnxruntime, ...).
    "numpy": "indra-ocr", "onnxruntime": "indra-ocr",
}


def test_every_module_the_gateway_imports_is_installed_by_the_runtime():
    import ast

    from app.models.indra_ocr import GATEWAY_PACKAGES, INDRA_PACKAGE

    declared = {
        package.split("[")[0].split("=")[0].split(">")[0].split("<")[0]
        for package in (INDRA_PACKAGE, *GATEWAY_PACKAGES)
    }
    imported = set()
    for node in ast.walk(ast.parse(GATEWAY.read_text())):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.add(node.module.split(".")[0])
    third_party = {name for name in imported if name not in sys.stdlib_module_names}
    assert third_party <= set(_PROVIDED_BY), third_party - set(_PROVIDED_BY)
    assert {_PROVIDED_BY[name] for name in third_party} <= declared


def test_a_runtime_built_from_an_older_package_list_is_rebuilt(monkeypatch, tmp_path):
    import json

    from app.services import indra_runtime

    monkeypatch.setattr(indra_runtime, "_dir", lambda: tmp_path)
    monkeypatch.setattr(indra_runtime, "wants_gpu", lambda **_kw: False)
    indra_runtime.python_bin().parent.mkdir(parents=True)
    indra_runtime.python_bin().touch()
    # What the first release wrote: ready, CPU, and no package list (no Pillow).
    (tmp_path / ".install_state.json").write_text(json.dumps({"state": "ready", "gpu": False}))
    assert indra_runtime.is_ready() is False
    (tmp_path / ".install_state.json").write_text(json.dumps(
        {"state": "ready", "gpu": False, "packages": indra_runtime.packages(gpu=False)}
    ))
    assert indra_runtime.is_ready() is True
