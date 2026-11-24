"""Indra line reader for Lloyd's registers: a fine-tuned Indra recognizer and its pipeline.

Not the vendor's pipeline. The stock Indra recognizer (`rec.onnx`) was fine-tuned on
whole table lines of the *Lloyd's Shipping Index*, and the result is read by a pipeline
of its own: the page is flattened, the print lines are found across the full table
width, each line is recognized in one piece (the recognizer writes the column marks
inside the line) and the table is assembled from the marks. That pipeline and the
weights are one package, built outside Tabularium and installed from a folder:

    tabularium models import <package folder> --adapter indra-lloyds

The package is `rec.onnx` (six outputs, as the stock file), `meta.json` (the stock
Indra metadata) and `lloyds_lines.py`, whose contract is:

    reader = lloyds_lines.load(model_dir, providers=[...], threads=4)
    out = reader.read(gray)        # uint8 [H, W]
    out["otsl"], out["lines"], out["x0"], out["x1"], out["ys"], out["ys_input"],
    out["pitch"], out["seconds"]

Tabularium runs it in the Indra gateway (`scripts/cloud/indra_native_gateway.py
--line-reader lloyds_lines.py`) inside the Indra runtime, and reads the table as OTSL.
Facts from the package's README (release `t7b`, 2026-11-24): measured on table crops
of project collections dated 1920-1949, about 12 s a page on one CPU thread; the
package needs numpy, OpenCV, SciPy, ONNX Runtime and `indra_ocr.rec_preproc`.
"""
from __future__ import annotations

from ..models.base import Declared, ModelSpec, NotApplicable, SettingsSpec
from ..models.types import ServeRecipe, WorkflowField

ADAPTER_ID = "indra-lloyds"
SERVED_MODEL_NAME = "indra-lloyds"
# The module the gateway loads from the installed folder.
LINE_READER = "lloyds_lines.py"
# What makes the folder a complete installation: there is no `config.json` and no
# `*.safetensors`, so the generic checkpoint rule does not apply.
INSTALL_FILES = ("rec.onnx", "meta.json", LINE_READER)
# What the package imports beyond the Indra runtime (`indra-ocr` brings numpy, OpenCV
# and ONNX Runtime).
RUNTIME_PACKAGES = ("scipy>=1.11",)

# The reader returns one block per page: the table.
LABELS: dict[str, str] = {"Table": "Table"}

# The gateway flags that apply to a line reader: no `math`, the package has no formulas.
WORKFLOW = (
    WorkflowField("device", "enum", default="auto", values=("auto", "cpu", "cuda"),
                  label="settings.indraDevice", essential=True,
                  hints=(("auto", "settings.indraDeviceAutoHint"),)),
    WorkflowField("threads", "int", default=4, minimum=1, maximum=64,
                  label="settings.indraThreads", essential=True),
    WorkflowField("workers", "int", default=1, minimum=1, maximum=32,
                  label="settings.indraWorkers"),
)

SPEC = ModelSpec(
    adapter_id=ADAPTER_ID,
    serve=Declared(ServeRecipe(
        adapter_id=ADAPTER_ID,
        # Not on Hugging Face: the package is installed with `tabularium models import`.
        hf_repo="",
        served_model_name=SERVED_MODEL_NAME,
        # The Indra gateway, reading pages with the package's module.
        runtime="indra-native",
        serve_args=("--line-reader", LINE_READER),
        pip_extra=RUNTIME_PACKAGES,
        local_only=True,
        source="the package's README.md and lloyds_lines.py docstring (release t7b, 2026-11-24)",
    )),
    train=NotApplicable("the package is trained outside Tabularium; no training recipe"),
    labels=Declared(LABELS),
    cloud=NotApplicable("no Modal template: the package is installed from a local folder"),
    settings=Declared(SettingsSpec(
        image={"min_pixels": None, "max_pixels": None},
        image_fields=(),
        workflow=WORKFLOW,
        workflow_intro="settings.indraLloydsWorkflowIntro",
        sections=("workflow",),
        serving_dtype=False,
        workflow_needs_restart=True,
    )),
)
