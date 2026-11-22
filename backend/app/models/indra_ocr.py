"""Indra OCR (Datalab): a CPU-first OCR pipeline on ONNX Runtime.

Not a VLM: line detection, CTC recognition (four script recognizers), a layout and
reading-order model and n-gram language models, about 7.3M parameters in all (the
Latin recognizer has 1.25M). It returns the page's lines and its layout blocks with
pixel boxes, labels and text, plus a markdown rendering where tables are HTML.

Every fact here comes from github.com/datalab-to/indra (README, CHANGELOG,
`indra_ocr/models.py`, `pipeline.py`, `layout_post.py`, `order_post.py`, package
1.1.2) and from the Hugging Face repo `datalab-to/indra-ocr` at tag `v1.1.0`, read on
2026-11-22:
  - `pip install indra-ocr` installs ONNX Runtime, OpenCV and pypdfium2, no PyTorch;
    Python 3.10+, tested on Linux x86_64/aarch64 and macOS on Apple silicon;
  - `OrtOCR(..., providers=[...])` takes ONNX Runtime execution providers, CPU by
    default; the CHANGELOG measures CUDA with onnxruntime-gpu (A100, T4) and gives
    the arena back after every run on a GPU (`gpu_run_options`);
  - the models (76 MB, revision `v1.1.0`, `HF_REVISION` in `models.py`) are
    `det.onnx`, `rec*.onnx`, `order.onnx`, `orient.onnx`, `script.onnx`, `meta.json`
    and `lm*.npz`; the math models (`--math`) are a further 20 MB;
  - licence: code Apache-2.0, weights under a modified OpenRAIL-M.
"""
from __future__ import annotations

from ..models.base import Declared, ModelSpec, NotApplicable, SettingsSpec
from ..models.types import ServeRecipe, WorkflowField

ADAPTER_ID = "indra-ocr"
HF_REPO = "datalab-to/indra-ocr"
# `indra_ocr/models.py` HF_REVISION: the model set package 1.1.2 is tested against.
HF_REVISION = "v1.1.0"
INDRA_PACKAGE = "indra-ocr==1.1.2"
# The last onnxruntime-gpu built on CUDA 12 with wheels for Python 3.10-3.13 (1.24-1.26
# drop 3.10, 1.27+ need a CUDA 13 driver). The `cuda`/`cudnn` extras bring the CUDA
# runtime and cuDNN 9 as pip packages, so the host needs only the NVIDIA driver.
ONNXRUNTIME_GPU_PACKAGE = "onnxruntime-gpu[cuda,cudnn]==1.23.2"
# What the gateway imports besides Indra itself. Pillow decodes the page: `indra-ocr`
# reads images with OpenCV and does not depend on it (2026-11-22, prod: the gateway
# died on `import PIL` in a runtime built from `indra-ocr` alone).
GATEWAY_PACKAGES = ("fastapi>=0.110", "uvicorn>=0.29", "pillow>=10")
GATEWAY_SCRIPT = "indra_native_gateway.py"
SERVED_MODEL_NAME = "indra-ocr"

# `order_post.BLOCK_CLASSES` plus `Picture` (picture regions, `pipeline.py`).
INDRA_LABELS: dict[str, str] = {
    "Text": "Text",
    "Section-Header": "Section-header",
    "Page-Header": "Page-header",
    "Page-Footer": "Page-footer",
    "List-Group": "List-item",
    "Table": "Table",
    "Caption": "Caption",
    "Footnote": "Footnote",
    "Table-Of-Contents": "Text",
    "Form": "Text",
    "Picture": "Picture",
}

# The gateway's flags. The defaults are Indra's own (`indra_ocr.load`: threads=4,
# math=False) except `workers`, which is Tabularium's: pages read at once.
INDRA_WORKFLOW = (
    WorkflowField("device", "enum", default="auto", values=("auto", "cpu", "cuda"),
                  label="settings.indraDevice", essential=True,
                  hints=(("auto", "settings.indraDeviceAutoHint"),)),
    WorkflowField("threads", "int", default=4, minimum=1, maximum=64,
                  label="settings.indraThreads", essential=True),
    WorkflowField("workers", "int", default=1, minimum=1, maximum=32,
                  label="settings.indraWorkers"),
    WorkflowField("math", "bool", default=False, label="settings.indraMath"),
)

SPEC = ModelSpec(
    adapter_id=ADAPTER_ID,
    serve=Declared(ServeRecipe(
        adapter_id=ADAPTER_ID,
        hf_repo=HF_REPO,
        served_model_name=SERVED_MODEL_NAME,
        # Tabularium's gateway (`scripts/cloud/indra_native_gateway.py`) around
        # `indra_ocr.load(...).ocr(gray)`: no vLLM, no PyTorch.
        runtime="indra-native",
        # ONNX Runtime's CUDA 12 build runs on Maxwell and newer; 7.5 keeps the Vast
        # search on the cards the rest of the catalogue uses. A CPU-only host serves
        # it as well: the floor only applies when a GPU is rented for it.
        min_compute_capability=7.5,
        # CUDA 12 minor-version compatibility: any 12.x driver runs a 12.x build.
        min_cuda_driver=12.0,
        pip_extra=(INDRA_PACKAGE, *GATEWAY_PACKAGES, ONNXRUNTIME_GPU_PACKAGE),
        source="github.com/datalab-to/indra README, indra_ocr/pipeline.py OrtOCR (1.1.2, 2026-11-04)",
    )),
    train=NotApplicable("the vendor publishes no training code for the Indra models"),
    labels=Declared(INDRA_LABELS),
    cloud=NotApplicable("no Modal template: on Vast.ai the gateway runs on the rented GPU"),
    settings=Declared(SettingsSpec(
        image={"min_pixels": None, "max_pixels": None},
        image_fields=(),
        workflow=INDRA_WORKFLOW,
        workflow_intro="settings.indraWorkflowIntro",
        # Only the pipeline's own knobs: no vLLM flags, no sampling, no image cap.
        sections=("workflow",),
        serving_dtype=False,
        workflow_needs_restart=True,
    )),
)
