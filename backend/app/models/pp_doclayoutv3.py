"""PP-DocLayoutV3: the layout detector behind Xiaomi-OCR-0's region mode, trainable here.

Not an OCR adapter (it reads no text), so it has no `ModelSpec`: this module owns the
detector's facts and its training recipe, and `train_recipes` registers the recipe next
to the adapters' ones.

Facts from PaddleX release/3.7, read on 2026-11-20: the layout analysis tutorial
(`docs/module_usage/tutorials/ocr_modules/layout_analysis.md`), the module config
`paddlex/configs/modules/layout_analysis/PP-DocLayoutV3.yaml`, the PaddleDetection config
`paddlex/repo_apis/PaddleDetection_api/configs/PP-DocLayoutV3.yaml`, its registration in
`repo_apis/PaddleDetection_api/instance_seg/register.py` and the dataset checker
`modules/layout_analysis/dataset_checker/dataset_src/check_dataset.py`. The category
order is the `label_list` of the official inference model's `inference.yml`.
"""
from __future__ import annotations

from ..models.types import FAMILY_LAYOUT

DETECTOR_ID = "pp-doclayoutv3"
DISPLAY_NAME = "PP-DocLayoutV3 (layout)"

# `inference.yml` `label_list` of the official model: the class index is the category id.
CATEGORIES: tuple[str, ...] = (
    "abstract", "algorithm", "aside_text", "chart", "content", "display_formula",
    "doc_title", "figure_title", "footer", "footer_image", "footnote", "formula_number",
    "header", "header_image", "image", "inline_formula", "number", "paragraph_title",
    "reference", "reference_content", "seal", "table", "text", "vertical_text",
    "vision_footnote",
)

# Tabularium label -> the PP-DocLayoutV3 category it trains as. Where the detector has a
# class for the label this is the inverse of `XIAOMI_LABELS` (checked by a test); the rest
# have no native class and train as the one whose crop is read the same way.
CATEGORY_FOR_LABEL: dict[str, str] = {
    "Title": "doc_title",
    "Section-header": "paragraph_title",
    "Headline": "paragraph_title",
    "Page-header": "header",
    "Page-footer": "footer",
    "Issue-number": "number",
    "Text": "text",
    "Footnote": "footnote",
    "Caption": "figure_title",
    "List-item": "reference",
    "Table": "table",
    "Formula": "display_formula",
    "Picture": "image",
    # No native class: chosen with the user on 2026-11-20 (the date sits in the masthead).
    "Issue-date": "header",
    "Advertisement": "text",
    "Byline": "text",
    "Column": "text",
    "Note": "text",
}

PADDLEX_BRANCH = "release/3.7"
PADDLEX_CONFIG = "paddlex/configs/modules/layout_analysis/PP-DocLayoutV3.yaml"
PRETRAINED_WEIGHTS = (
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_pretrained_model/"
    "PP-DocLayoutV3_pretrained.pdparams"
)
# What `Global.mode=train` leaves under `Global.output`: the best epoch's weights and the
# exported inference model that `create_model(..., model_dir=...)` loads.
BEST_WEIGHTS = "best_model/best_model.pdparams"
INFERENCE_DIR = "best_model/inference"
INFERENCE_FILES = ("inference.json", "inference.pdiparams", "inference.yml")

TRAIN = {
    "maturity": "experimental",
    "toolchain": "paddlex",
    "data_view": "paddlex-layout",
    "families": (FAMILY_LAYOUT,),
    "train_types": ("full",),
    "official_doc": "https://paddlepaddle.github.io/PaddleX/latest/en/module_usage/tutorials/ocr_modules/layout_analysis.html",
    "note": "Layout detector only: trains the boxes Xiaomi-OCR-0's region mode reads, not the reader.",
    # The module config's Train section, as PaddleX ships it.
    "defaults": {
        "epochs": 100,
        "batch_size": 4,
        "learning_rate": 1e-4,
        "warmup_steps": 100,
        "log_interval": 10,
        "eval_interval": 1,
    },
    "flags": {
        # The module config says 11 (its demo set); the PaddleDetection config and the
        # pretrained head have 25. Keeping 25 keeps the pretrained classifier.
        "num_classes": len(CATEGORIES),
        "paddlex_config": PADDLEX_CONFIG,
        "pretrained_weights": PRETRAINED_WEIGHTS,
    },
    "environment": {"paddlex": PADDLEX_BRANCH, "paddlepaddle": ">=3.3"},
    "official_notes": (
        "Dataset: COCOInstSegDataset plus `read_order` (an int from 0, consecutive within an "
        "image); `images/`, `images_mask/` (same content), `annotations/instance_train.json`, "
        "`annotations/instance_val.json`. PaddleX converts no other format.",
        "Commands: `python main.py -c paddlex/configs/modules/layout_analysis/PP-DocLayoutV3.yaml "
        "-o Global.mode=check_dataset|train|evaluate`; the module documents no export mode, "
        "training writes `output/best_model/inference`.",
        "Devices registered for training: cpu, gpu, xpu, npu, mlu; AMP off, dy2st off. The "
        "documented install is Linux/Docker. Measured on 2026-11-20 on an Apple Silicon Mac "
        "(paddle 3.3.1 CPU): check_dataset and evaluate work, but the first training backward "
        "pass returns inf gradients (`silu_grad`), so the preflight refuses Apple Silicon CPU.",
        "Evaluation is the vendor's `DocLayoutV3Metric` on the val split, run on the official "
        "weights before training and on `best_model` after.",
        "The module config sets `num_classes: 11` (its demo set); the PaddleDetection config and "
        "the pretrained head have 25, so the recipe passes `Train.num_classes=25`.",
    ),
}
