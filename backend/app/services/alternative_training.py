"""Recipe di training per modelli multimodali non Monkey.

Il formato interno di Tabularium resta la fonte del dataset. Qui vengono
create viste derivate nel formato richiesto dal toolchain del modello, senza
modificare annotazioni o convertire silenziosamente il target.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import dataset_builder
from .model_adapters import get_adapter


def _sharegpt_row(row: dict) -> dict:
    messages = row.get("messages") or []
    return {
        "conversations": [
            {"from": "human" if item.get("role") == "user" else "gpt", "value": item.get("content", "")}
            for item in messages
            if item.get("role") in {"user", "assistant"}
        ],
        "images": row.get("images", []),
    }


def prepare_glm(project_id: int, approved_only: bool = True) -> dict:
    """Prepara dataset + config LLaMA-Factory per GLM-OCR.

    Il template ``glm_ocr`` è lasciato configurabile perché dipende dalla
    versione di LLaMA-Factory installata; il preflight dello script lo verifica
    prima di lanciare il training.
    """
    adapter = get_adapter("glm-ocr")
    report = dataset_builder.build_datasets(
        project_id,
        split_ratio=0.9,
        seed=42,
        adapter_id=adapter.adapter_id,
        approved_only=approved_only,
    )
    root = Path(report["dataset_dir"])
    train_rows: list[dict] = []
    val_rows: list[dict] = []
    for split, target in (("train", train_rows), ("val", val_rows)):
        for family in ("layout", "text_rec", "table", "formula"):
            path = root / f"{family}_{split}.jsonl"
            if path.exists():
                target.extend(_sharegpt_row(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    out = root / "glm-ocr-llamafactory"
    out.mkdir(parents=True, exist_ok=True)
    train = out / "train.json"
    val = out / "val.json"
    train.write_text(json.dumps(train_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    val.write_text(json.dumps(val_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    info = out / "dataset_info.json"
    info.write_text(json.dumps({
        "tabularium_glm": {
            "file_name": str(train),
            "formatting": "sharegpt",
            "columns": {"messages": "conversations", "images": "images"},
        },
        "tabularium_glm_val": {
            "file_name": str(val),
            "formatting": "sharegpt",
            "columns": {"messages": "conversations", "images": "images"},
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    config = out / "glm_ocr_lora.yaml"
    config.write_text(
        "\n".join([
            "model_name_or_path: zai-org/GLM-OCR",
            "stage: sft",
            "do_train: true",
            "do_eval: true",
            "finetuning_type: lora",
            "template: glm_ocr",
            "dataset: tabularium_glm",
            "eval_dataset: tabularium_glm_val",
            "dataset_dir: " + str(out),
            "output_dir: " + str(out / "checkpoint"),
            "cutoff_len: 16384",
            "per_device_train_batch_size: 1",
            "gradient_accumulation_steps: 8",
            "learning_rate: 0.00001",
            "num_train_epochs: 1.0",
            "logging_steps: 5",
            "save_steps: 500",
            "bf16: true",
        ]) + "\n",
        encoding="utf-8",
    )
    script = out / "train_glm_ocr.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        ": \"${TABULARIUM_LLAMA_FACTORY_DIR:?Impostare TABULARIUM_LLAMA_FACTORY_DIR}\"\n"
        "cd \"$TABULARIUM_LLAMA_FACTORY_DIR\"\n"
        f"llamafactory-cli train {config}\n",
        encoding="utf-8",
    )
    script.chmod(0o750)
    return {"adapter_id": adapter.adapter_id, "report": report, "files": {"train": str(train), "val": str(val), "dataset_info": str(info), "config": str(config), "script": str(script)}}


def prepare_deepseek(project_id: int, approved_only: bool = True) -> dict:
    """Prepara la vista conversazionale usata dal notebook Unsloth ufficiale."""
    adapter = get_adapter("deepseek-ocr")
    report = dataset_builder.build_datasets(
        project_id, split_ratio=0.9, seed=42, adapter_id=adapter.adapter_id,
        approved_only=approved_only,
    )
    root = Path(report["dataset_dir"])
    out = root / "deepseek-ocr2-unsloth"
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for split in ("train", "val"):
        rows: list[dict] = []
        for family in ("layout", "text_rec", "table", "formula"):
            path = root / f"{family}_{split}.jsonl"
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                user = row["messages"][0]
                assistant = row["messages"][1]
                rows.append({"messages": [
                    {"role": "<|User|>", "content": user["content"], "images": row.get("images", [])},
                    {"role": "<|Assistant|>", "content": assistant["content"]},
                ]})
        target = out / f"{split}.json"
        target.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        written[split] = str(target)
    script = out / "train_deepseek_ocr2.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        ": \"${TABULARIUM_UNSLOTH_SCRIPT:?Impostare TABULARIUM_UNSLOTH_SCRIPT sul notebook/script Unsloth validato}\"\n"
        "python \"$TABULARIUM_UNSLOTH_SCRIPT\"\n",
        encoding="utf-8",
    )
    script.chmod(0o750)
    return {"adapter_id": adapter.adapter_id, "report": report, "status": "dataset_ready_runner_required", "files": {**written, "script": str(script)}}


def prepare_grounded_end2end(project_id: int, adapter_id: str, approved_only: bool = True) -> dict:
    """Crea esempi end-to-end per dots.ocr o Unlimited-OCR.

    Questi modelli non condividono il JSON/OTSL di Monkey: il target conserva
    bbox, categoria e testo in un'unica risposta. Il runner di fine-tuning è
    volutamente configurabile perché non esiste un trainer ufficiale comune.
    """
    if adapter_id not in {"dots-ocr", "unlimited-ocr"}:
        raise ValueError("adapter end-to-end non supportato")
    data = dataset_builder.collect_pages_with_blocks(project_id)
    if not data:
        raise ValueError("nessuna pagina annotata nel progetto")
    train_ids, val_ids = dataset_builder.compute_split(project_id, 0.9, 42, "page", approved_only=approved_only)
    root = dataset_builder._project_dir(project_id) / f"{adapter_id}-training"
    root.mkdir(parents=True, exist_ok=True)
    (root / "images").mkdir(parents=True, exist_ok=True)
    rows = {"train": [], "val": []}
    warnings: list[str] = []
    for page_id, item in data.items():
        if approved_only and item["page"]["status"] not in {"approved", "exported"}:
            continue
        page = item["page"]
        image = dataset_builder._page_image_path(page, root / "images", warnings)
        if not image:
            continue
        target_items = []
        for block in item["blocks"]:
            if approved_only and not bool(block["confirmed"]):
                continue
            points = dataset_builder.parse_points(block["points"])
            if len(points) < 2:
                continue
            xs, ys = [float(p[0]) for p in points], [float(p[1]) for p in points]
            bbox = [round(min(xs) / page["width"] * 1000), round(min(ys) / page["height"] * 1000), round(max(xs) / page["width"] * 1000), round(max(ys) / page["height"] * 1000)]
            target_items.append({"bbox": bbox, "category": block["label"], "text": str(block["content"] or "")})
        if not target_items:
            continue
        if adapter_id == "dots-ocr":
            target = json.dumps(target_items, ensure_ascii=False, separators=(",", ":"))
            prompt = "Please output the layout information from the image, including each layout element's bbox, category, and text content."
        else:
            target = "\n".join(f"<|det|>{x['category']} [{', '.join(str(v) for v in x['bbox'])}]<|/det|>{x['text']}" for x in target_items)
            prompt = "<image>document parsing."
        split = "train" if page_id in train_ids else "val" if page_id in val_ids else None
        if split:
            rows[split].append({"messages": [{"role": "user", "content": prompt}, {"role": "assistant", "content": target}], "images": [image]})
    files = {}
    for split in rows:
        path = root / f"{split}.jsonl"
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows[split]) + ("\n" if rows[split] else ""), encoding="utf-8")
        files[split] = str(path)
    manifest = {"adapter_id": adapter_id, "format": "grounded-end2end-v1", "counts": {key: len(value) for key, value in rows.items()}, "warnings": warnings, "files": files}
    env_name = "TABULARIUM_DOTS_TRAIN_SCRIPT" if adapter_id == "dots-ocr" else "TABULARIUM_UNLIMITED_TRAIN_SCRIPT"
    script = root / f"train_{adapter_id}.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f": \"${{{env_name}:?Impostare {env_name} sul runner di training validato}}\"\n"
        f"python \"${env_name}\"\n",
        encoding="utf-8",
    )
    script.chmod(0o750)
    files["script"] = str(script)
    manifest["files"] = files
    manifest["status"] = "dataset_ready_runner_required"
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def prepare_mineru(project_id: int, approved_only: bool = True) -> dict:
    """Prepara il dataset canonico MinerU e un launcher per il data engine.

    MinerU pubblica l'inferenza e il formato OTSL, ma non un comando SFT
    stabile equivalente a ms-swift: il runner viene quindi iniettato tramite
    ``TABULARIUM_MINERU_TRAIN_SCRIPT`` e non viene inventato dal backend.
    """
    report = dataset_builder.build_datasets(
        project_id, split_ratio=0.9, seed=42, adapter_id="mineru2.5",
        approved_only=approved_only,
    )
    root = Path(report["dataset_dir"]) / "mineru2.5-training"
    root.mkdir(parents=True, exist_ok=True)
    script = root / "train_mineru2.5.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        ": \"${TABULARIUM_MINERU_TRAIN_SCRIPT:?Impostare TABULARIUM_MINERU_TRAIN_SCRIPT sul data engine MinerU validato}\"\n"
        "python \"$TABULARIUM_MINERU_TRAIN_SCRIPT\"\n",
        encoding="utf-8",
    )
    script.chmod(0o750)
    return {"adapter_id": "mineru2.5", "status": "dataset_ready_runner_required", "report": report, "files": {"dataset": str(Path(report["dataset_dir"])), "script": str(script)}}
