"""Persisted, validated per-model inference and serving overrides.

Recommended values come from the model adapter and its official serving
recipe. Only explicit user overrides are saved; clearing overrides restores
the adapter defaults without guessing at a new workflow.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import HTTPException

from ..db import connect
from .model_adapters import get_adapter
from . import serve_recipes


_KEY_PREFIX = "model_settings:"
_SERVING_LIMITS = {
    "gpu_memory_utilization": (0.2, 0.98),
    "max_model_len": (512, 131072),
    "max_num_seqs": (1, 128),
    "max_num_batched_tokens": (512, 131072),
}
_GENERATION_LIMITS = {
    "temperature": (0.0, 2.0),
    "top_p": (0.01, 1.0),
    "top_k": (1, 1000),
    "max_tokens": (1, 131072),
    "repetition_penalty": (0.5, 2.0),
    "presence_penalty": (-2.0, 2.0),
    "frequency_penalty": (-2.0, 2.0),
    "no_repeat_ngram_size": (0, 512),
}
_IMAGE_LIMITS = {"min_pixels": (1, 64000000), "max_pixels": (0, 64000000)}
_MLX_LIMITS = {
    "kv_bits": (2, 8),
    "kv_group_size": (16, 256),
    "max_kv_size": (512, 131072),
    "vision_cache_size": (0, 1024),
}
_PADDLE_BOOLEAN_WORKFLOW = {
    "use_layout_detection",
    "layout_nms",
    "use_doc_orientation_classify",
    "use_doc_unwarping",
    "use_chart_recognition",
    "use_seal_recognition",
    "use_ocr_for_image_block",
    "format_block_content",
    "merge_layout_blocks",
    "use_queues",
}
_PADDLE_WORKFLOW_LIMITS = {
    "layout_threshold": (0.0, 1.0),
    "layout_unclip_ratio": (0.01, 10.0),
}
_PADDLE_WORKFLOW_ENUMS = {
    "layout_merge_bboxes_mode": {"large", "small", "union"},
}
_PADDLE_WORKFLOW_FIELDS = (
    _PADDLE_BOOLEAN_WORKFLOW
    | set(_PADDLE_WORKFLOW_LIMITS)
    | set(_PADDLE_WORKFLOW_ENUMS)
)
_NO_REPEAT_ADAPTERS = {"teleocr", "mineru2.5", "deepseek-ocr", "unlimited-ocr"}


def _defaults(adapter_id: str) -> dict[str, Any]:
    adapter = get_adapter(adapter_id)
    recipe = serve_recipes.RECIPES.get(adapter_id)
    # Reflect the exact serving command instead of presenting generic vLLM
    # guesses as each model's recommended values. Recipes cover cloud; adapter
    # commands cover local vLLM. Recipe values take precedence where both exist.
    serving: dict[str, Any] = {
        "gpu_memory_utilization": None,
        "max_model_len": None,
        "max_num_seqs": None,
        "max_num_batched_tokens": None,
    }
    command_sources = []
    if recipe:
        command_sources.append(list(recipe.serve_args))
    try:
        command = adapter.serve_command("MODEL_PATH", 8888)
    except (AttributeError, NotImplementedError, TypeError, ValueError):
        command = None
    if command:
        command_sources.append(command)
    for args in command_sources:
        for flag, key, cast in (
            ("--gpu-memory-utilization", "gpu_memory_utilization", float),
            ("--max-model-len", "max_model_len", int),
            ("--max-num-seqs", "max_num_seqs", int),
            ("--max-num-batched-tokens", "max_num_batched_tokens", int),
        ):
            if serving[key] is None and flag in args:
                try:
                    serving[key] = cast(args[args.index(flag) + 1])
                except (ValueError, IndexError, TypeError):
                    pass
    if serving["max_model_len"] is None:
        serving["max_model_len"] = adapter.capabilities.max_model_len
    return {
        "serving": serving,
        # Empty means keep the adapter's per-task, source-verified sampling.
        "generation": {},
        # TeleOCR/config.py upstream uses MAX_PIXELS=8000*8000. Other models
        # keep the application-wide image cap unless their workflow says more.
        "image": {
            "min_pixels": None,
            "max_pixels": 64_000_000 if adapter_id == "teleocr" else None,
        },
        # TeleOCR publishes two layout modes. Detection is the upstream
        # default; Segmentation is explicitly recommended for real degraded
        # scans, so expose the choice instead of freezing it in our prompt.
        "workflow": (
            {"layout_mode": "Detection"}
            if adapter_id == "teleocr"
            else {key: None for key in sorted(_PADDLE_WORKFLOW_FIELDS)}
            if adapter_id == "paddleocr-vl"
            else {}
        ),
        # None preserves the mlx-vlm version's own default. These only apply
        # to local Apple Silicon serving, never to remote vLLM deployments.
        "mlx": {key: None for key in (*_MLX_LIMITS, "kv_quant_scheme", "log_level")}
        if adapter.capabilities.local_mlx_repo
        else {},
    }


def _load_overrides(adapter_id: str) -> dict[str, Any]:
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key=?", (_KEY_PREFIX + adapter_id,)
            ).fetchone()
        value = json.loads(row["value"]) if row else {}
    # During bootstrap (and in utility contexts that construct a client before
    # app lifespan initializes SQLite) there is no meta table yet. Defaults
    # still apply; a missing database must not make inference construction fail.
    except (TypeError, ValueError, KeyError, sqlite3.OperationalError):
        return {}
    return value if isinstance(value, dict) else {}


def get_settings(adapter_id: str) -> dict[str, Any]:
    adapter = get_adapter(adapter_id)
    recommended = _defaults(adapter_id)
    overrides = _load_overrides(adapter_id)
    effective = json.loads(json.dumps(recommended))
    for section, values in overrides.items():
        if section in effective and isinstance(values, dict):
            effective[section].update(values)
    return {
        "adapter_id": adapter_id,
        "display_name": adapter.capabilities.display_name,
        "recommended": recommended,
        "overrides": overrides,
        "effective": effective,
        "restart_required": bool(overrides.get("serving") or overrides.get("mlx")),
    }


def _validated_section(section: str, values: Any) -> dict[str, Any]:
    if not isinstance(values, dict):
        raise HTTPException(status_code=422, detail=f"'{section}' deve essere un oggetto")
    if section == "workflow":
        raise HTTPException(status_code=422, detail="workflow deve essere validato per il modello selezionato")
    limits: dict[str, tuple[float, float]]
    if section == "serving":
        limits = _SERVING_LIMITS
    elif section == "generation":
        limits = _GENERATION_LIMITS
    elif section == "image":
        limits = _IMAGE_LIMITS
    elif section == "mlx":
        limits = _MLX_LIMITS
    else:
        raise HTTPException(status_code=422, detail=f"sezione '{section}' non riconosciuta")

    mlx_enums = {"kv_quant_scheme": {"uniform", "turboquant"}, "log_level": {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}}
    if section == "mlx":
        if set(values) - (set(limits) | set(mlx_enums)):
            raise HTTPException(status_code=422, detail="parametro mlx non riconosciuto")
        result = {}
        for key, value in values.items():
            if value is None:
                continue
            if key in mlx_enums:
                if value not in mlx_enums[key]:
                    raise HTTPException(status_code=422, detail=f"mlx.{key} non valido")
                result[key] = value
                continue
            lo, hi = limits[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
                raise HTTPException(status_code=422, detail=f"mlx.{key} deve essere tra {lo:g} e {hi:g}")
            result[key] = float(value) if key == "kv_bits" else int(value)
        return result
    if set(values) - set(limits):
        raise HTTPException(status_code=422, detail=f"parametro {section} non riconosciuto")
    result: dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            continue
        lo, hi = limits[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
            raise HTTPException(status_code=422, detail=f"{section}.{key} deve essere tra {lo:g} e {hi:g}")
        result[key] = float(value) if key in {
            "gpu_memory_utilization", "temperature", "top_p", "repetition_penalty",
            "presence_penalty", "frequency_penalty",
        } else int(value)
    return result


def save_settings(adapter_id: str, payload: Any, actor: dict | None = None) -> dict[str, Any]:
    adapter = get_adapter(adapter_id)
    if not isinstance(payload, dict) or set(payload) - {"serving", "generation", "image", "workflow", "mlx"}:
        raise HTTPException(status_code=422, detail="impostazioni modello non valide")
    overrides = {}
    for section, values in payload.items():
        if section != "workflow":
            if section == "mlx" and not adapter.capabilities.local_mlx_repo:
                raise HTTPException(status_code=422, detail="impostazioni MLX non disponibili per questo modello")
            if section == "image" and adapter_id != "paddleocr-vl" and "min_pixels" in values:
                raise HTTPException(status_code=422, detail="image.min_pixels è disponibile solo nel pipeline ufficiale PaddleOCR-VL")
            overrides[section] = _validated_section(section, values)
            continue
        if not isinstance(values, dict):
            raise HTTPException(status_code=422, detail="'workflow' deve essere un oggetto")
        if adapter_id == "teleocr":
            if set(values) - {"layout_mode"}:
                raise HTTPException(status_code=422, detail="parametro workflow TeleOCR non riconosciuto")
            mode = values.get("layout_mode")
            if mode is not None and mode not in {"Detection", "Segmentation"}:
                raise HTTPException(status_code=422, detail="workflow.layout_mode deve essere Detection o Segmentation")
            overrides[section] = {"layout_mode": mode} if mode is not None else {}
        elif adapter_id == "paddleocr-vl":
            if set(values) - _PADDLE_WORKFLOW_FIELDS:
                raise HTTPException(status_code=422, detail="parametro workflow PaddleOCR-VL non riconosciuto")
            result = {}
            for key, value in values.items():
                if value is None:
                    continue
                if key in _PADDLE_BOOLEAN_WORKFLOW:
                    if not isinstance(value, bool):
                        raise HTTPException(status_code=422, detail=f"workflow.{key} deve essere booleano")
                    result[key] = value
                elif key in _PADDLE_WORKFLOW_LIMITS:
                    lo, hi = _PADDLE_WORKFLOW_LIMITS[key]
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
                        raise HTTPException(status_code=422, detail=f"workflow.{key} deve essere tra {lo:g} e {hi:g}")
                    result[key] = float(value)
                elif key in _PADDLE_WORKFLOW_ENUMS:
                    if value not in _PADDLE_WORKFLOW_ENUMS[key]:
                        raise HTTPException(status_code=422, detail=f"workflow.{key} non valido")
                    result[key] = value
            overrides[section] = result
        elif values:
            raise HTTPException(status_code=422, detail="workflow personalizzato non disponibile per questo modello")
        else:
            overrides[section] = {}
    serving = overrides.get("serving", {})
    if (
        overrides.get("generation", {}).get("no_repeat_ngram_size") is not None
        and adapter_id not in _NO_REPEAT_ADAPTERS
    ):
        raise HTTPException(
            status_code=422,
            detail="generation.no_repeat_ngram_size richiede un logits processor previsto dalla ricetta del modello",
        )
    model_context = _defaults(adapter_id)["serving"]["max_model_len"]
    effective_context = serving.get("max_model_len", model_context)
    if effective_context > model_context:
        raise HTTPException(status_code=422, detail=f"max_model_len supera il contesto del modello ({model_context})")
    # `max_num_batched_tokens` è il budget del batch di prefill sull'intero
    # scheduler, non la lunghezza massima di una singola sequenza. Ricette
    # ufficiali possono (e GLM-OCR lo fa) impostarlo sopra `max_model_len`;
    # i due valori vanno quindi validati separatamente.
    if overrides.get("generation", {}).get("max_tokens", 0) > max(1, effective_context - 2048):
        raise HTTPException(status_code=422, detail=f"generation.max_tokens deve lasciare spazio per l'immagine nel contesto ({effective_context})")
    overrides = {section: values for section, values in overrides.items() if values}
    key = _KEY_PREFIX + adapter_id
    with connect() as conn:
        if overrides:
            conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(overrides, separators=(",", ":"))),
            )
        else:
            conn.execute("DELETE FROM meta WHERE key=?", (key,))
        from . import audit
        audit.record(conn, actor, "model_settings.updated", resource_type="model", payload={"adapter_id": adapter_id, "sections": sorted(overrides)})
    return get_settings(adapter_id)


def all_settings() -> list[dict[str, Any]]:
    return [get_settings(adapter_id) for adapter_id in serve_recipes.RECIPES]
