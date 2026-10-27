"""Ricette di serving ufficiali, una per modello del registro.

Ogni modello vuole il *suo* framework e i *suoi* flag: sono quelli che
determinano la precisione, non solo la velocità — il logits processor n-gram di
DeepSeek/Unlimited, il `--chat-template-content-format string` di dots.mocr, il
prefix caching disattivato di PaddleOCR-VL, il decoding speculativo MTP di
GLM-OCR. Servire un modello con i flag di un altro produce output plausibili e
sbagliati, che è il modo peggiore di sbagliare.

Qui compaiono i flag prescritti o documentati dalla fonte upstream del modello.
Limiti applicativi di batch/VRAM non documentati dal produttore restano vuoti e
si possono impostare per modello in Settings. Le template Modal condividono i
flag della ricetta; un test verifica che li supportino.
"""
from __future__ import annotations

import math

from .. import models as model_specs
from ..models.types import ServeRecipe  # noqa: F401  (re-exported for call sites)


# One recipe per model, declared in the model's own spec (`app/models/<id>.py`).
# This is the derived view the rest of the application imports.
RECIPES: dict[str, ServeRecipe] = model_specs.declared_values("serve")


def recipe_for(adapter_id: str) -> ServeRecipe:
    recipe = RECIPES.get(str(adapter_id or "").strip())
    if recipe is None:
        raise ValueError(f"nessuna ricetta di serving per il modello '{adapter_id}'")
    return recipe


def resource_budget(recipe: ServeRecipe) -> dict[str, int]:
    """Conservative preflight estimates derived from the selected checkpoint.

    These are guardrails, not upstream model requirements: the app's model
    registry supplies the approximate weight size, while the runtime category
    supplies a conservative application-side allowance. Temporary wheel
    extraction and headroom are included so setup can refuse before it fills
    the disk.
    """
    from .model_adapters import get_adapter

    size_gb = get_adapter(recipe.adapter_id).capabilities.approx_size_gb
    if size_gb is None or size_gb <= 0:
        raise ValueError(f"dimensione modello non dichiarata per {recipe.adapter_id}")
    runtime_disk_gb = 8 if recipe.runtime == "docker" else 16
    # When the exact framework environment already exists, setup only needs
    # headroom for model weights and their temporary download/extraction. Keep
    # the fresh-install budget conservative; the remote setup script chooses
    # this lower budget only after verifying the recipe venv is executable.
    reuse_runtime_disk_gb = math.ceil(size_gb * 1.5 + 2)
    return {
        "min_free_disk_gb": math.ceil(runtime_disk_gb + size_gb * 1.5 + 2),
        "min_free_disk_gb_reuse": reuse_runtime_disk_gb,
        # A complete checkpoint and an installed compatible runtime need only
        # operational headroom for logs, manifests and small metadata writes.
        "min_free_disk_gb_cached": 4,
        # Leave six GB over approximate weight size for CUDA context, kernels,
        # and a modest KV cache. GPU total and live free memory are both
        # checked separately by the remote setup script.
        "min_free_vram_gb": max(8, math.ceil(size_gb + 6)),
        # Host RAM backs tokenizer workers, weight staging and multimodal
        # preprocessing in addition to the GPU-resident model.
        "min_free_ram_gb": max(8, math.ceil(size_gb * 1.25 + 6)),
        "min_compute_capability": recipe.min_compute_capability,
        "min_cuda_driver": recipe.min_cuda_driver,
    }


def serve_argv(
    recipe: ServeRecipe,
    *,
    model_path: str,
    port: int,
    host: str = "0.0.0.0",
    api_key: str = "",
    lora_path: str = "",
    lora_name: str = "",
    served_model_name: str | None = None,
    draft_model_path: str = "",
    settings: dict | None = None,
) -> list[str]:
    """Comando di serving completo, ricetta più infrastruttura."""
    if recipe.runtime == "teleocr-native":
        # The producer's pipeline owns the full document workflow in its
        # sidecar and constructs AsyncLLM directly.
        return []
    if recipe.runtime == "monkeyocr":
        argv = ["serve.py", "--model-path", model_path]
    else:
        # Anche il runtime "docker" usa la CLI di vLLM: la differenza è che il
        # server è già nell'immagine invece di essere installato con pip.
        argv = ["-m", "vllm.entrypoints.cli.main", "serve", model_path]
    argv += ["--host", host, "--port", str(int(port))]
    argv += list(recipe.serve_args)
    argv = apply_serving_overrides(argv, (settings or {}).get("serving") or {})
    workflow = (settings or {}).get("workflow") or {}
    use_dflash = bool(draft_model_path.strip() and workflow.get("dflash_enabled", True))
    if use_dflash:
        if recipe.adapter_id != "monkeyocrv2-parsing" or recipe.runtime != "monkeyocr":
            raise ValueError("il draft DFlash è disponibile solo per MonkeyOCRv2-Parsing")
        if lora_path.strip():
            raise ValueError("DFlash non è abilitato per checkpoint LoRA")
        argv += ["-d", draft_model_path.strip()]
    workflow_overrides = workflow if use_dflash else {
        key: value for key, value in workflow.items()
        if key != "dflash_num_speculative_tokens"
    }
    argv = apply_workflow_overrides(recipe.adapter_id, argv, workflow_overrides)
    if lora_path.strip():
        argv += ["--enable-lora", "--lora-modules", f"{lora_name.strip() or recipe.served_model_name}={lora_path.strip()}"]
    argv += ["--served-model-name", served_model_name or recipe.served_model_name]
    if api_key.strip():
        argv += ["--api-key", api_key.strip()]
    return argv


def apply_serving_overrides(argv: list[str], serving: dict) -> list[str]:
    """Apply user overrides to the recipe argv without changing other flags."""
    argv = list(argv)
    flag_for = {
        "dtype": "--dtype",
        "gpu_memory_utilization": "--gpu-memory-utilization",
        "max_model_len": "--max-model-len",
        "max_num_seqs": "--max-num-seqs",
        "max_num_batched_tokens": "--max-num-batched-tokens",
    }
    for key, flag in flag_for.items():
        value = serving.get(key)
        if value is None:
            continue
        if flag in argv:
            argv[argv.index(flag) + 1] = str(value)
        else:
            argv += [flag, str(value)]
    return argv


def apply_workflow_overrides(adapter_id: str, argv: list[str], workflow: dict) -> list[str]:
    """Apply only model-specific performance controls to the vendor command."""
    argv = list(argv)
    if adapter_id == "monkeyocrv2-parsing" and workflow.get("dflash_num_speculative_tokens") is not None:
        flag = "--dflash-num-speculative-tokens"
        value = str(int(workflow["dflash_num_speculative_tokens"]))
        if flag in argv:
            argv[argv.index(flag) + 1] = value
        else:
            argv += [flag, value]
    if adapter_id == "glm-ocr" and workflow.get("speculative_tokens") is not None:
        import json

        flag = "--speculative-config"
        config = {"method": "mtp", "num_speculative_tokens": int(workflow["speculative_tokens"])}
        encoded = json.dumps(config, separators=(",", ":"))
        if flag in argv:
            argv[argv.index(flag) + 1] = encoded
        else:
            argv += [flag, encoded]
    return argv


def remote_models() -> list[dict[str, object]]:
    """Elenco per la UI: cosa si può servire su una GPU a noleggio e cosa no."""
    return [
        {
            "adapter_id": recipe.adapter_id,
            "hf_repo": recipe.hf_repo,
            "served_model_name": recipe.served_model_name,
            "runtime": recipe.runtime,
            "supported": True,
            "needs_own_image": recipe.needs_own_image,
            "docker_image": recipe.docker_image,
            "native_remote_port": recipe.native_remote_port,
            **resource_budget(recipe),
        }
        for recipe in RECIPES.values()
    ]
