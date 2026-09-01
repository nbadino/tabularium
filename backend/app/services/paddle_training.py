"""Recipe riproducibili per i due training Paddle ufficiali.

Il VLM usa ERNIEKit; il detector usa il trainer layout PaddleX/PaddleOCR.
Sono script espliciti e non vengono eseguiti finché l'utente non li avvia.
"""
from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from .dataset_builder import _project_dir


def preflight(project_id: int, ernie_dir: str = "", paddlex_dir: str = "") -> dict:
    root = _project_dir(project_id) / "paddle-dataset"
    errors: list[str] = []
    warnings: list[str] = []
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        errors.append("preparare prima il dataset Paddle")
        return {"ready": False, "errors": errors, "warnings": warnings}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"manifest Paddle non leggibile: {exc}")
        return {"ready": False, "errors": errors, "warnings": warnings}
    if manifest.get("counts", {}).get("vlm", {}).get("train", 0) < 1:
        errors.append("il dataset VLM non contiene campioni di training approvati")
    if manifest.get("counts", {}).get("vlm", {}).get("val", 0) < 1:
        warnings.append("validation VLM vuota: il training partirà senza valutazione utile")
    if manifest.get("counts", {}).get("layout", {}).get("train", 0) < 1:
        errors.append("il dataset layout non contiene annotazioni approvate")
    if manifest.get("counts", {}).get("layout", {}).get("val", 0) < 1:
        warnings.append("validation layout vuota: il detector non potrà essere valutato")
    ernie_path = Path(ernie_dir or os.environ.get("TABULARIUM_ERNIE_DIR", ""))
    if not ernie_path.is_dir():
        errors.append("ERNIEKit non trovato: impostare TABULARIUM_ERNIE_DIR")
    elif not (ernie_path / "examples/configs/PaddleOCR-VL/sft/run_ocr_vl_sft_16k.yaml").is_file():
        errors.append("config ufficiale ERNIEKit PaddleOCR-VL non trovato")
    paddlex_path = Path(paddlex_dir or os.environ.get("TABULARIUM_PADDLEX_DIR", ""))
    if not paddlex_path.is_dir():
        errors.append("PaddleX non trovato: impostare TABULARIUM_PADDLEX_DIR")
    config = Path(os.environ.get("TABULARIUM_PADDLE_LAYOUT_CONFIG", ""))
    if not config.is_file():
        errors.append("config layout mancante: impostare TABULARIUM_PADDLE_LAYOUT_CONFIG")
    return {"ready": not errors, "errors": errors, "warnings": warnings, "manifest": manifest, "ernie_dir": str(ernie_path), "paddlex_dir": str(paddlex_path), "layout_config": str(config)}


def prepare(project_id: int, vlm_model: str = "PaddlePaddle/PaddleOCR-VL", ernie_dir: str = "") -> dict:
    root = _project_dir(project_id) / "paddle-dataset"
    manifest = root / "manifest.json"
    if not manifest.exists():
        raise ValueError("preparare prima il dataset Paddle")
    meta = json.loads(manifest.read_text(encoding="utf-8"))
    run = root / "training"
    run.mkdir(parents=True, exist_ok=True)
    ernie = shlex.quote(ernie_dir) if ernie_dir else '"$TABULARIUM_ERNIE_DIR"'
    vlm = f'''#!/usr/bin/env bash
set -euo pipefail
: "${{TABULARIUM_ERNIE_DIR:?Impostare TABULARIUM_ERNIE_DIR sul checkout ERNIE}}"
cd {ernie}
erniekit train examples/configs/PaddleOCR-VL/sft/run_ocr_vl_sft_16k.yaml \\
  model_name_or_path={shlex.quote(vlm_model)} \\
  train_dataset_path={shlex.quote(str(root / "vlm_train.jsonl"))} \\
  eval_dataset_path={shlex.quote(str(root / "vlm_val.jsonl"))} \\
  output_dir={shlex.quote(str(run / "vlm"))} \\
  max_seq_len=16384 batch_size=1 packing_size=1 gradient_accumulation_steps=8
'''
    layout = f'''#!/usr/bin/env bash
set -euo pipefail
: "${{TABULARIUM_PADDLEX_DIR:?Impostare TABULARIUM_PADDLEX_DIR sul checkout PaddleX}}"
: "${{TABULARIUM_PADDLE_LAYOUT_CONFIG:?Impostare TABULARIUM_PADDLE_LAYOUT_CONFIG sul config del detector}}"
cd "$TABULARIUM_PADDLEX_DIR"
python main.py -c "$TABULARIUM_PADDLE_LAYOUT_CONFIG" \\
  -o Global.mode=train \\
  -o Global.dataset_dir={shlex.quote(str(root))} \\
  -o Global.output={shlex.quote(str(run / 'layout'))}
'''
    files = {"vlm_script": run / "train_vlm.sh", "layout_script": run / "train_layout.sh"}
    files["vlm_script"].write_text(vlm, encoding="utf-8")
    files["layout_script"].write_text(layout, encoding="utf-8")
    for path in files.values():
        path.chmod(0o750)
    return {"manifest": meta, "files": {key: str(value) for key, value in files.items()}, "status": "prepared", "preflight": preflight(project_id, ernie_dir)}
