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
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServeRecipe:
    """Come si serve un modello, secondo la sua documentazione ufficiale."""

    adapter_id: str
    hf_repo: str
    served_model_name: str
    # "vllm": `vllm serve <pesi> <args>` — "monkeyocr": il wrapper ufficiale
    # `parsing/serve.py` del repo MonkeyOCRv2 — "docker": immagine dedicata,
    # non installabile via pip.
    runtime: str
    vllm_version: str = ""
    # Empty means let the pinned vLLM release resolve its supported range.
    # Model plugins may constrain Transformers themselves (TeleOCR does).
    transformers_version: str = ""
    docker_image: str = ""
    # Optional model-owned HTTP bridge port (e.g. TeleOCR's official runner).
    native_remote_port: int | None = None
    pip_extra: tuple[str, ...] = ()
    # Flag dopo il modello. `--host`, `--port` e `--served-model-name` li
    # aggiunge `serve_argv`: sono infrastruttura, non ricetta.
    serve_args: tuple[str, ...] = field(default_factory=tuple)
    source: str = ""

    @property
    def needs_own_image(self) -> bool:
        """Vero quando l'architettura vive solo in un'immagine dedicata.

        Su Vast.ai non è un ostacolo: l'immagine del container si sceglie al
        noleggio, quindi basta affittare l'istanza con quella. È su Modal che
        va costruita — la differenza è chi la fornisce, non se si può usare.
        """
        return self.runtime == "docker"

    @property
    def installs_vllm(self) -> bool:
        """Falso quando vLLM è già dentro l'immagine: reinstallarlo la romperebbe."""
        return self.runtime != "docker"


RECIPES: dict[str, ServeRecipe] = {
    "teleocr": ServeRecipe(
        adapter_id="teleocr",
        hf_repo="StarDoc-AI/TeleOCR",
        served_model_name="StarDoc-AI/TeleOCR",
        runtime="vllm",
        # Plugin ufficiale out-of-tree: pyproject.toml pinna vLLM e Transformers
        # e registra Qwen2_5_VLForConditionalGeneration in vLLM. Senza di esso
        # l'architettura personalizzata del checkpoint non viene caricata.
        vllm_version="0.11.0",
        transformers_version="4.57.1",
        native_remote_port=8889,
        pip_extra=(
            # Upstream documents pip install -e . at the repository root.
            # This registers both the architecture plugin and the official
            # TeleOCR package, including its no-repeat-ngram logits processor.
            "git+https://github.com/caipeng328/TeleOCR.git@main",
        ),
        serve_args=(
            "--trust-remote-code",
            "--logits-processors",
            "TeleOCR.vlm_utils.vlm_client.vllm_v1_no_repeat_ngram:VllmV1NoRepeatNGramLogitsProcessor",
            "--dtype", "bfloat16",
            # TeleOCR/config.py upstream recommends 0.95.
            "--gpu-memory-utilization", "0.95",
            "--max-model-len", "16384",
        ),
        source="Repository ufficiale TeleOCR + model card StarDoc-AI/TeleOCR",
    ),
    "monkeyocrv2-parsing": ServeRecipe(
        adapter_id="monkeyocrv2-parsing",
        hf_repo="zenosai/MonkeyOCRv2-B-Parsing",
        served_model_name="MonkeyOCRv2",
        runtime="monkeyocr",
        # The upstream README pins 0.25.1 for DFlash-enabled serving.
        vllm_version="0.25.1",
        source="parsing/serve.py e versione consigliata nel README ufficiale",
    ),
    "mineru2.5": ServeRecipe(
        adapter_id="mineru2.5",
        hf_repo="opendatalab/MinerU2.5-Pro-2605-1.2B",
        served_model_name="mineru2.5",
        runtime="vllm",
        # L'extra `[vllm]` di mineru-vl-utils dichiara vLLM < 0.22.
        vllm_version="0.21.0",
        pip_extra=("mineru-vl-utils",),
        serve_args=(
            "--logits-processors", "mineru_vl_utils:MinerULogitsProcessor",
        ),
        source="README MinerU2.5: il logits processor è parte della ricetta, non un'opzione",
    ),
    "dots-ocr": ServeRecipe(
        adapter_id="dots-ocr",
        hf_repo="dots-studio/dots.mocr",
        served_model_name="dots-mocr",
        runtime="vllm",
        vllm_version="0.28.0",
        serve_args=(
            "--trust-remote-code",
            "--chat-template-content-format", "string",
        ),
        source="README dots.mocr: senza il formato stringa il template di chat non combacia",
    ),
    "glm-ocr": ServeRecipe(
        adapter_id="glm-ocr",
        hf_repo="zai-org/GLM-OCR",
        served_model_name="glm-ocr",
        runtime="vllm",
        vllm_version="0.19.0",
        transformers_version="5.3.1",
        native_remote_port=8890,
        pip_extra=("glmocr[selfhosted]", "fastapi", "uvicorn"),
        serve_args=(
            "--speculative-config", '{"method": "mtp", "num_speculative_tokens": 1}',
        ),
        source="README GLM-OCR: vLLM MTP ufficiale + SDK self-hosted PP-DocLayout/OCR",
    ),
    "deepseek-ocr": ServeRecipe(
        adapter_id="deepseek-ocr",
        hf_repo="deepseek-ai/DeepSeek-OCR-2",
        served_model_name="deepseek-ocr-2",
        runtime="vllm",
        vllm_version="0.12.0",
        serve_args=(
            "--trust-remote-code",
            "--logits-processors", "vllm.model_executor.models.deepseek_ocr:NGramPerReqLogitsProcessor",
            "--no-enable-prefix-caching",
            "--mm-processor-cache-gb", "0",
        ),
        source="recipe vLLM ufficiale DeepSeek-OCR: n-gram logits processor contro i loop",
    ),
    "paddleocr-vl": ServeRecipe(
        adapter_id="paddleocr-vl",
        hf_repo="PaddlePaddle/PaddleOCR-VL-1.6",
        served_model_name="PaddleOCR-VL-1.6",
        runtime="vllm",
        vllm_version="0.28.0",
        serve_args=(
            "--trust-remote-code",
            "--max-num-batched-tokens", "16384",
            "--no-enable-prefix-caching",
            "--mm-processor-cache-gb", "0",
        ),
        source="recipe PaddleOCR-VL: prefix caching e cache del processore multimodale vanno spenti",
    ),
    "qwen3-vl-8b": ServeRecipe(
        adapter_id="qwen3-vl-8b",
        hf_repo="Qwen/Qwen3-VL-8B-Instruct",
        served_model_name="qwen3-vl-8b",
        runtime="vllm",
        vllm_version="0.28.0",
        serve_args=(
            "--dtype", "bfloat16",
            "--max-model-len", "32768",
            "--limit-mm-per-prompt", '{"image":4,"video":0}',
        ),
        source="model card Qwen3-VL: limiti multimodali espliciti per non saturare il prefill",
    ),
    "unlimited-ocr": ServeRecipe(
        adapter_id="unlimited-ocr",
        hf_repo="baidu/Unlimited-OCR",
        served_model_name="Unlimited-OCR",
        # L'architettura non è nella wheel pip stabile: serve l'immagine
        # dedicata, che su Vast.ai si ottiene noleggiando l'istanza con essa.
        runtime="docker",
        docker_image="vllm/vllm-openai:unlimited-ocr",
        serve_args=(
            "--trust-remote-code",
            "--logits_processors", "vllm.model_executor.models.unlimited_ocr:NGramPerReqLogitsProcessor",
            "--no-enable-prefix-caching",
            "--mm-processor-cache-gb", "0",
            "--max-model-len", "32768",
        ),
        source="recipes.vllm.ai/baidu/Unlimited-OCR",
    ),
}


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
    return {
        "min_free_disk_gb": math.ceil(runtime_disk_gb + size_gb * 1.5 + 2),
        # Leave six GB over approximate weight size for CUDA context, kernels,
        # and a modest KV cache. GPU total and live free memory are both
        # checked separately by the remote setup script.
        "min_free_vram_gb": max(8, math.ceil(size_gb + 6)),
        # Host RAM backs tokenizer workers, weight staging and multimodal
        # preprocessing in addition to the GPU-resident model.
        "min_free_ram_gb": max(8, math.ceil(size_gb * 1.25 + 6)),
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
    settings: dict | None = None,
) -> list[str]:
    """Comando di serving completo, ricetta più infrastruttura."""
    if recipe.runtime == "monkeyocr":
        argv = ["serve.py", "--model-path", model_path]
    else:
        # Anche il runtime "docker" usa la CLI di vLLM: la differenza è che il
        # server è già nell'immagine invece di essere installato con pip.
        argv = ["-m", "vllm.entrypoints.cli.main", "serve", model_path]
    argv += ["--host", host, "--port", str(int(port))]
    argv += list(recipe.serve_args)
    argv = apply_serving_overrides(argv, (settings or {}).get("serving") or {})
    argv = apply_workflow_overrides(recipe.adapter_id, argv, (settings or {}).get("workflow") or {})
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
