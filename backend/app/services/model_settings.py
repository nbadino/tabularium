"""Persisted, validated per-model inference and serving overrides.

Recommended values come from the model adapter and its official serving
recipe. Only explicit user overrides are saved; clearing overrides restores
the adapter defaults without guessing at a new workflow.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any

from fastapi import HTTPException

from .. import models as model_specs
from ..adapters import store
from ..models.types import (
    LLAMAINDEX_OCR_LANGUAGES,
    LLAMAINDEX_WORKFLOW_BOOLEANS,
    LLAMAINDEX_WORKFLOW_ENUMS,
    LLAMAINDEX_WORKFLOW_FIELDS,
    LLAMAINDEX_WORKFLOW_LIMITS,
    PADDLE_BOOLEAN_WORKFLOW,
    PADDLE_WORKFLOW_ENUMS,
    PADDLE_WORKFLOW_FIELDS,
    PADDLE_WORKFLOW_LIMITS,
    UNLIMITED_WORKFLOW_ENUMS,
    UNLIMITED_WORKFLOW_LIMITS,
    SettingsSpec,
    llamaindex_workflow_defaults,
)
from .model_adapters import get_adapter
from . import serve_recipes


_KEY_PREFIX = "model_settings:"
_SERVING_LIMITS = {
    "gpu_memory_utilization": (0.2, 0.98),
    "max_model_len": (512, 131072),
    "max_num_seqs": (1, 128),
    "max_num_batched_tokens": (512, 131072),
}
_SERVING_ENUMS = {
    # vLLM's documented dtype values. The model's recommendation remains the
    # default; this override lets users select FP16 on cards without BF16.
    "dtype": {"half", "float16", "bfloat16", "float", "float32"},
}
_GENERATION_LIMITS = {
    "temperature": (0.0, 2.0),
    "top_p": (0.000001, 1.0),
    "top_k": (1, 1000),
    "max_tokens": (1, 131072),
    "repetition_penalty": (0.5, 2.0),
    "presence_penalty": (-2.0, 2.0),
    "frequency_penalty": (-2.0, 2.0),
    "no_repeat_ngram_size": (0, 512),
}
_IMAGE_LIMITS = {"min_pixels": (1, 128000000), "max_pixels": (0, 128000000)}
_MLX_LIMITS = {
    "kv_bits": (2, 8),
    "kv_group_size": (16, 256),
    "max_kv_size": (512, 131072),
    "vision_cache_size": (0, 1024),
}
# Guard anti-loop del percorso MLX: validi solo per gli adapter che li
# dichiarano (`local_mlx_repetition_penalty`), perché nascono da una ricetta la
# cui difesa n-gram `mlx-vlm` non fornisce. Applicati solo al serving locale
# Apple Silicon (v. `inference._sampling_for`).
_MLX_REPEAT_FLOAT_LIMITS = {"repetition_penalty": (0.5, 2.0)}
_MLX_REPEAT_INT_LIMITS = {
    "repetition_context_size": (1, 2048),
    # Finestra della presence penalty: mlx-vlm la applica su 20 token di
    # default, troppo pochi per una riga di registro. Stesso gate della
    # repetition penalty (v. `local_mlx_repetition_penalty`).
    "presence_context_size": (1, 2048),
}
def _settings_spec(adapter_id: str) -> SettingsSpec:
    """The settings this model declares, or an empty declaration for a runtime model."""
    declared = model_specs.spec_for(adapter_id)
    value = declared.value("settings") if declared is not None else None
    return value or SettingsSpec()


def _defaults(adapter_id: str) -> dict[str, Any]:
    adapter = get_adapter(adapter_id)
    declared = _settings_spec(adapter_id)
    recipe = serve_recipes.RECIPES.get(adapter_id)
    # This panel tunes the remote serving recipe. Do not fill missing cloud values
    # from the adapter's local command: those may be machine-specific caps (an 8 GB
    # GPU) and would misrepresent what Vast actually launches. A missing recipe flag
    # means the runtime/model defaults apply.
    serving: dict[str, Any] = {
        "dtype": None,
        "gpu_memory_utilization": None,
        "max_model_len": None,
        "max_num_seqs": None,
        "max_num_batched_tokens": None,
    }
    if recipe:
        args = list(recipe.serve_args)
        for flag, key, cast in (
            ("--dtype", "dtype", str),
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
    # None preserves the mlx-vlm version's own default. These only apply to local
    # Apple Silicon serving, never to remote vLLM deployments.
    mlx: dict[str, Any] = {}
    if getattr(adapter.capabilities, "local_mlx_settings", False):
        mlx = {
            **{key: None for key in (*_MLX_LIMITS, "kv_quant_scheme", "log_level")},
            # Variant choice (e.g. quantization) when the adapter declares more than
            # one. None = the declared default.
            **({"repo": None} if getattr(adapter.capabilities, "local_mlx_repo_choices", ()) else {}),
            **(
                {
                    "repetition_penalty": adapter.capabilities.local_mlx_repetition_penalty,
                    "repetition_context_size": adapter.capabilities.local_mlx_repetition_context_size,
                }
                if getattr(adapter.capabilities, "local_mlx_repetition_penalty", None) is not None
                else {}
            ),
            **(
                {"presence_context_size": adapter.capabilities.local_mlx_presence_context_size}
                if getattr(adapter.capabilities, "local_mlx_presence_context_size", 0)
                else {}
            ),
        }
    return {
        "serving": serving,
        # Empty means keep the adapter's per-task, source-verified sampling.
        "generation": dict(getattr(adapter, "recommended_generation", {}) or {}),
        # Upstream pipeline defaults (TeleOCR 8000*8000, GLM-OCR's packaged 71,372,800,
        # …). A model without a documented cap gets None: its native processor and
        # model config choose the resolution, and the app does not resize.
        "image": dict(declared.image),
        "workflow": declared.workflow_defaults(),
        "mlx": mlx,
    }


def _load_overrides(adapter_id: str) -> dict[str, Any]:
    # During bootstrap (and in utility contexts that construct a client before the
    # lifespan initializes SQLite) there is no meta table yet: `get_or` treats that
    # as "no overrides", because a missing database must not make inference fail.
    value = store.get_or(_KEY_PREFIX + adapter_id, {})
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
        # Varianti MLX selezionabili per la UI (es. quantizzazione): l'etichetta
        # è stabile e tradotta lato frontend, il repo è la stringa che il server
        # passa a mlx-vlm.
        "mlx_repo_choices": [
            {"variant": variant, "repo": repo}
            for variant, repo in getattr(adapter.capabilities, "local_mlx_repo_choices", ())
        ],
        # What the panel may offer for this model. Both come from the spec, so a new
        # model needs no frontend change to hide a field its vendor rejects.
        "mlx_available": bool(recommended.get("mlx")),
        "serving_dtype": _settings_spec(adapter_id).serving_dtype,
        "no_repeat_ngram": _settings_spec(adapter_id).no_repeat_ngram,
        # The declared schema of the vendor workflow: the panel renders these fields
        # instead of keeping a JSX block per model (program.md, P2c).
        "workflow_fields": [asdict(field) for field in _settings_spec(adapter_id).workflow],
        "workflow_intro": _settings_spec(adapter_id).workflow_intro,
        "sections": list(_settings_spec(adapter_id).sections),
        "generation_unsupported": sorted(_settings_spec(adapter_id).generation_unsupported),
        "recommended": recommended,
        "overrides": overrides,
        "effective": effective,
        "restart_required": bool(
            overrides.get("serving")
            or overrides.get("mlx")
            or (_settings_spec(adapter_id).workflow_needs_restart and overrides.get("workflow"))
        ),
    }


def _validated_section(section: str, values: Any, adapter_id: str | None = None) -> dict[str, Any]:
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
    serving_enums = _SERVING_ENUMS if section == "serving" else {}
    if section == "serving" and set(values) - (set(limits) | set(serving_enums)):
        raise HTTPException(status_code=422, detail="parametro serving non riconosciuto")
    if section == "serving":
        result = {}
        for key, value in values.items():
            if value is None:
                continue
            if key in serving_enums:
                if not isinstance(value, str) or value not in serving_enums[key]:
                    raise HTTPException(status_code=422, detail="serving.dtype non valido")
                result[key] = value
                continue
            lo, hi = limits[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
                raise HTTPException(status_code=422, detail=f"serving.{key} deve essere tra {lo:g} e {hi:g}")
            result[key] = float(value) if key == "gpu_memory_utilization" else int(value)
        return result
    if section == "mlx":
        capabilities = get_adapter(adapter_id).capabilities if adapter_id else None
        repo_choices = (
            {repo for _label, repo in capabilities.local_mlx_repo_choices} if capabilities else set()
        )
        # I campi del guard anti-loop esistono solo per gli adapter che lo
        # dichiarano: su un modello senza guard sono un parametro sconosciuto.
        guard = bool(capabilities) and getattr(capabilities, "local_mlx_repetition_penalty", None) is not None
        repeat_float = _MLX_REPEAT_FLOAT_LIMITS if guard else {}
        repeat_int = _MLX_REPEAT_INT_LIMITS if guard else {}
        allowed = set(limits) | set(mlx_enums) | set(repeat_float) | set(repeat_int) | ({"repo"} if repo_choices else set())
        if set(values) - allowed:
            raise HTTPException(status_code=422, detail="parametro mlx non riconosciuto")
        result = {}
        for key, value in values.items():
            if value is None:
                continue
            if key == "repo":
                if value not in repo_choices:
                    raise HTTPException(status_code=422, detail="mlx.repo non è una variante disponibile")
                result[key] = value
                continue
            if key in mlx_enums:
                if value not in mlx_enums[key]:
                    raise HTTPException(status_code=422, detail=f"mlx.{key} non valido")
                result[key] = value
                continue
            if key in repeat_float or key in repeat_int:
                lo, hi = (repeat_float | repeat_int)[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
                    raise HTTPException(status_code=422, detail=f"mlx.{key} deve essere tra {lo:g} e {hi:g}")
                result[key] = float(value) if key in repeat_float else int(value)
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


def _workflow_error(field_key: str, message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=f"workflow.{field_key} {message}")


def _validated_workflow_field(field: "WorkflowField", value: Any) -> Any:
    """Validate one declared workflow field, by kind. No model is named here."""
    kind = field.kind
    if kind == "bool":
        if not isinstance(value, bool):
            raise _workflow_error(field.key, "deve essere booleano")
        return value
    if kind in {"int", "float"}:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _workflow_error(field.key, "deve essere un numero")
        if kind == "int" and int(value) != value:
            raise _workflow_error(field.key, "deve essere un intero")
        low, high = field.minimum, field.maximum
        noun = "un intero" if kind == "int" else "un numero"
        if (low is not None and value < low) or (high is not None and value > high):
            raise _workflow_error(
                field.key,
                f"deve essere {noun} tra {low:g} e {high:g}" if low is not None and high is not None
                else f"deve essere {noun} nei limiti dichiarati",
            )
        return float(value) if kind == "float" else int(value)
    if kind == "enum":
        if not isinstance(value, str) or value not in field.values:
            raise _workflow_error(field.key, f"deve essere uno tra {', '.join(field.values)}")
        return value
    if kind == "version":
        if not isinstance(value, str) or not (value == "latest" or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
            raise _workflow_error(field.key, "deve essere 'latest' o una data YYYY-MM-DD")
        return value
    if kind == "str":
        if not isinstance(value, str) or (field.max_len is not None and len(value) > field.max_len):
            limit = field.max_len if field.max_len is not None else "illimitato"
            raise _workflow_error(field.key, f"deve essere testo fino a {limit} caratteri")
        return value.strip()
    if kind == "str_list":
        if (
            not isinstance(value, list)
            or not 1 <= len(value) <= (field.max_items or 10)
            or any(not isinstance(item, str) or item not in field.values for item in value)
            or len(set(value)) != len(value)
        ):
            raise _workflow_error(field.key, "deve contenere codici ammessi, senza duplicati")
        return list(value)
    if kind == "int_pair":
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 2
            or any(
                isinstance(item, bool) or not isinstance(item, int)
                or (field.minimum is not None and item < field.minimum)
                or (field.maximum is not None and item > field.maximum)
                for item in value
            )
        ):
            raise _workflow_error(field.key, "deve contenere due interi nei limiti dichiarati")
        return list(value)
    if kind == "crop_box":
        if not isinstance(value, dict) or set(value) != {"top", "bottom", "left", "right"}:
            raise _workflow_error(field.key, "deve contenere top, bottom, left e right")
        normalized: dict[str, float] = {}
        for edge in ("top", "bottom", "left", "right"):
            raw = value.get(edge)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not 0 <= raw < 1:
                raise _workflow_error(field.key, f".{edge} deve essere tra 0 e 1")
            normalized[edge] = float(raw)
        if normalized["top"] + normalized["bottom"] >= 1 or normalized["left"] + normalized["right"] >= 1:
            raise _workflow_error(field.key, "lascia un'area vuota")
        return normalized
    raise HTTPException(status_code=500, detail=f"kind di workflow non gestito: {kind}")


def _validate_workflow(adapter_id: str, values: dict[str, Any], spec: "SettingsSpec") -> dict[str, Any]:
    """Validate a workflow payload against the model's declared field schema."""
    fields = {field.key: field for field in spec.workflow}
    unknown = set(values) - set(fields)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"parametro workflow non riconosciuto per {adapter_id}: {', '.join(sorted(unknown))}",
        )
    result: dict[str, Any] = {}
    for key, raw in values.items():
        if raw is None:
            # None means "leave the vendor default": only the model default remains,
            # which is what clearing an override does.
            continue
        result[key] = _validated_workflow_field(fields[key], raw)
    # Cross-field rules are declared on the field (`requires`), so the validator does
    # not grow a branch per model.
    effective = {
        **spec.workflow_defaults(),
        **(_load_overrides(adapter_id).get("workflow") or {}),
        **result,
    }
    for field in spec.workflow:
        if field.requires and effective.get(field.key) is not None:
            target, expected = field.requires
            if effective.get(target) != expected:
                raise HTTPException(
                    status_code=422,
                    detail=f"workflow.{field.key} richiede workflow.{target} = {expected}",
                )
    return result


def save_settings(adapter_id: str, payload: Any, actor: dict | None = None) -> dict[str, Any]:
    adapter = get_adapter(adapter_id)
    spec = _settings_spec(adapter_id)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="impostazioni modello non valide")
    unsupported_sections = set(payload) - set(spec.sections)
    if unsupported_sections:
        raise HTTPException(
            status_code=422,
            detail=f"sezioni non disponibili per {adapter_id}: {', '.join(sorted(unsupported_sections))}",
        )
    overrides: dict[str, Any] = {}
    for section, values in payload.items():
        if section == "workflow":
            if not isinstance(values, dict):
                raise HTTPException(status_code=422, detail="'workflow' deve essere un oggetto")
            if not spec.workflow and values:
                raise HTTPException(status_code=422, detail="workflow personalizzato non disponibile per questo modello")
            overrides[section] = _validate_workflow(adapter_id, values, spec)
            continue

        if section == "serving":
            if (
                isinstance(values, dict) and "dtype" in values
                and not spec.serving_dtype
                and serve_recipes.RECIPES.get(adapter_id) is not None
                and serve_recipes.RECIPES[adapter_id].runtime == "monkeyocr"
            ):
                raise HTTPException(status_code=422, detail="serving.dtype non è esposto dal wrapper ufficiale MonkeyOCRv2")
        if section == "mlx" and not getattr(adapter.capabilities, "local_mlx_settings", False):
            raise HTTPException(status_code=422, detail="impostazioni MLX non disponibili per questo modello")
        if section == "image" and isinstance(values, dict):
            extra = set(values) - set(spec.image_fields)
            if extra and "min_pixels" in extra:
                raise HTTPException(status_code=422, detail="image.min_pixels non è esposto per questo modello")
            if extra:
                raise HTTPException(
                    status_code=422,
                    detail="questo modello usa la propria preparazione immagini: regola le opzioni del workflow ufficiale",
                )
        overrides[section] = _validated_section(section, values, adapter_id)
        if section == "generation":
            unsupported = set(overrides[section]) & set(spec.generation_unsupported)
            if unsupported:
                raise HTTPException(
                    status_code=422,
                    detail=f"generation non supportato dal workflow ufficiale {adapter_id}: {', '.join(sorted(unsupported))}",
                )
        if section == "image":
            image = overrides[section]
            if spec.image_cap is not None and (image.get("max_pixels") or 0) > spec.image_cap:
                raise HTTPException(
                    status_code=422,
                    detail=f"image.max_pixels supera il limite ufficiale ({spec.image_cap})",
                )
            if (
                image.get("min_pixels") is not None
                and image.get("max_pixels") not in (None, 0)
                and image["max_pixels"] < image["min_pixels"]
            ):
                raise HTTPException(status_code=422, detail="image.max_pixels deve essere maggiore o uguale a image.min_pixels")

    if (
        overrides.get("generation", {}).get("no_repeat_ngram_size") is not None
        and not spec.no_repeat_ngram
    ):
        raise HTTPException(
            status_code=422,
            detail="generation.no_repeat_ngram_size richiede un logits processor previsto dalla ricetta del modello",
        )
    model_context = _defaults(adapter_id)["serving"]["max_model_len"]
    if model_context is None:
        # Validation still needs a ceiling when the cloud recipe delegates context
        # sizing to vLLM. Prefer the model contract, then the local command's
        # conservative cap; neither is presented as the cloud value.
        model_context = adapter.capabilities.max_model_len
    if model_context is None:
        try:
            command = adapter.serve_command("MODEL_PATH", 8888)
        except (AttributeError, NotImplementedError, TypeError, ValueError):
            command = None
        if command and "--max-model-len" in command:
            try:
                model_context = int(command[command.index("--max-model-len") + 1])
            except (ValueError, IndexError, TypeError):
                model_context = None
    model_context = model_context or _SERVING_LIMITS["max_model_len"][1]
    serving = overrides.get("serving", {})
    effective_context = serving.get("max_model_len") or model_context
    if effective_context > model_context:
        raise HTTPException(status_code=422, detail=f"max_model_len supera il contesto del modello ({model_context})")
    # `max_num_batched_tokens` is the prefill budget of the whole scheduler, not the
    # maximum length of one sequence: official recipes (GLM-OCR) may set it above
    # `max_model_len`, so the two values are validated separately.
    if overrides.get("generation", {}).get("max_tokens", 0) > max(1, effective_context - 2048):
        raise HTTPException(status_code=422, detail=f"generation.max_tokens deve lasciare spazio per l'immagine nel contesto ({effective_context})")
    overrides = {section: values for section, values in overrides.items() if values}
    key = _KEY_PREFIX + adapter_id
    if overrides:
        store.put(key, overrides)
    else:
        store.delete(key)
    from . import audit

    audit.record_event(
        "model_settings.updated",
        actor,
        resource_type="model",
        payload={"adapter_id": adapter_id, "sections": sorted(overrides)},
    )
    return get_settings(adapter_id)


def all_settings() -> list[dict[str, Any]]:
    return [get_settings(adapter_id) for adapter_id in serve_recipes.RECIPES]
