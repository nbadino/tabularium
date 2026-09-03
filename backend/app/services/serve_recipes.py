"""Ricette di serving ufficiali, una per modello del registro.

Ogni modello vuole il *suo* framework e i *suoi* flag: sono quelli che
determinano la precisione, non solo la velocità — il logits processor n-gram di
DeepSeek/Unlimited, il `--chat-template-content-format string` di dots.mocr, il
prefix caching disattivato di PaddleOCR-VL, il decoding speculativo MTP di
GLM-OCR. Servire un modello con i flag di un altro produce output plausibili e
sbagliati, che è il modo peggiore di sbagliare.

I valori qui sono copiati dalle template Modal già verificate contro le fonti
ufficiali (`scripts/cloud/modal_*.py`, che citano README e recipe del modello);
un test confronta le due liste e fallisce se divergono. Da qui li leggono sia il
provisioning su GPU remota sia chi vorrà unificare le template.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Richieste simultanee sul singolo container: stesso default delle template.
DEFAULT_MAX_INPUTS = 4


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
    docker_image: str = ""
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
    "monkeyocrv2-parsing": ServeRecipe(
        adapter_id="monkeyocrv2-parsing",
        hf_repo="zenosai/MonkeyOCRv2-B-Parsing",
        served_model_name="MonkeyOCRv2",
        runtime="monkeyocr",
        # Allineata all'ambiente di serving locale verificato (data/vllm-runtime).
        vllm_version="0.28.0",
        serve_args=(
            "--gpu-memory-utilization", "0.9",
            "--max-model-len", "24576",
            "--max-num-batched-tokens", "24576",
            "--max-num-seqs", "8",
        ),
        source="parsing/serve.py del repo ufficiale; stessi flag di scripts/serve_model.sh",
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
            "--max-num-seqs", str(DEFAULT_MAX_INPUTS),
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
            "--max-num-seqs", str(DEFAULT_MAX_INPUTS),
        ),
        source="README dots.mocr: senza il formato stringa il template di chat non combacia",
    ),
    "glm-ocr": ServeRecipe(
        adapter_id="glm-ocr",
        hf_repo="zai-org/GLM-OCR",
        served_model_name="glm-ocr",
        runtime="vllm",
        vllm_version="0.19.0",
        serve_args=(
            "--speculative-config", '{"method": "mtp", "num_speculative_tokens": 3}',
            "--max-num-batched-tokens", "32768",
            "--max-num-seqs", str(DEFAULT_MAX_INPUTS),
        ),
        source="README GLM-OCR: MTP speculativo con 3 token è la configurazione pubblicata",
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
            "--max-num-seqs", str(DEFAULT_MAX_INPUTS),
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
            "--max-num-seqs", str(DEFAULT_MAX_INPUTS),
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
            "--max-num-seqs", str(DEFAULT_MAX_INPUTS),
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
            "--max-num-batched-tokens", "8192",
            "--max-model-len", "32768",
            "--max-num-seqs", str(DEFAULT_MAX_INPUTS),
            "--gpu-memory-utilization", "0.95",
        ),
        source="recipes.vllm.ai/baidu/Unlimited-OCR",
    ),
}


def recipe_for(adapter_id: str) -> ServeRecipe:
    recipe = RECIPES.get(str(adapter_id or "").strip())
    if recipe is None:
        raise ValueError(f"nessuna ricetta di serving per il modello '{adapter_id}'")
    return recipe


def serve_argv(
    recipe: ServeRecipe,
    *,
    model_path: str,
    port: int,
    host: str = "0.0.0.0",
    api_key: str = "",
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
    argv += ["--served-model-name", recipe.served_model_name]
    if api_key.strip():
        argv += ["--api-key", api_key.strip()]
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
        }
        for recipe in RECIPES.values()
    ]
