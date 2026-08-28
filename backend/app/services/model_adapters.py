"""Contratti per profili di dominio e adapter di modelli.

Il database conserva annotazioni ricche e indipendenti dal modello. Un adapter
traduce quello schema nei prompt, nei formati e nei vincoli del modello scelto.
MonkeyOCRv2 è il primo adapter, non il contratto globale dell'applicazione.

Ogni adapter dichiara anche una *ricetta* di installazione (repo Hugging Face,
toolchain di training, backend di serving): è quanto serve al registro modelli
(`model_registry.py`) per scaricare i pesi e, nelle fasi successive, per
servirli e addestrarli. Per i modelli aggiunti nella prima fase multi-modello
(oltre a MonkeyOCRv2) solo la ricetta di download è verificata sul repo
ufficiale; `serve_command` resta `None` finché non arriva il relativo lavoro di
serving (vedi `docs/OCR_MODEL_ALTERNATIVES.md` e il piano multi-modello).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ModelCapabilities:
    """Capacità dichiarate da un backend di training/inferenza."""

    adapter_id: str
    display_name: str
    tasks: tuple[str, ...]
    coordinate_system: str
    table_format: str | None
    training_types: tuple[str, ...]
    inference_modes: tuple[str, ...]
    hardware: tuple[str, ...]
    geometry: tuple[str, ...] = ("bbox",)
    reading_order: bool = True
    languages: tuple[str, ...] = ("en",)
    # --- ricetta di installazione/training (nuovo) --------------------------
    hf_repo: str = ""
    hf_revision: str | None = None
    approx_size_gb: float | None = None
    license_note: str = ""
    # id logico di toolchain: "ms-swift" | "llama-factory" | "unsloth" |
    # "community" | "none" (nessun training ancora supportato).
    train_toolchain: str = "none"
    # "vllm-openai" (endpoint OpenAI-compatibile via vLLM) | "custom" | "none".
    serve_backend: str = "none"
    # Nome che il client OpenAI-compatibile deve passare nel campo "model"
    # della richiesta (`--served-model-name` di vLLM). `None` = deciso altrove
    # (MonkeyOCRv2 lo configura oggi da UI, storicamente "MonkeyOCRv2").
    served_model_name: str | None = None


class ModelAdapter(Protocol):
    adapter_id: str
    capabilities: ModelCapabilities

    def prompt_for(self, task: str, label: str | None = None) -> str | None: ...

    def serialize_target(self, task: str, value: object) -> str: ...

    def serve_command(self, model_path: str, port: int) -> list[str] | None: ...


class MonkeyOCRv2ParsingAdapter:
    """Adapter per il formato ufficiale MonkeyOCRv2-Parsing/ms-swift."""

    adapter_id = "monkeyocrv2-parsing"
    capabilities = ModelCapabilities(
        adapter_id=adapter_id,
        display_name="MonkeyOCRv2-Parsing",
        tasks=("layout", "text", "table", "formula"),
        coordinate_system="normalized-0-1000",
        table_format="otsl",
        training_types=("lora", "full"),
        inference_modes=("vllm", "cpu"),
        hardware=("cuda", "cpu-inference"),
        geometry=("bbox", "polygon"),
        reading_order=True,
        languages=("en", "multilingual-partial"),
        hf_repo="zenosai/MonkeyOCRv2-B-Parsing",
        approx_size_gb=1.5,
        license_note="",
        train_toolchain="ms-swift",
        serve_backend="vllm-openai",
    )

    _PROMPTS = {
        "layout": "Please output the categories and coordinates of the document elements in reading order.",
        "text": "Please output the text content from the image.",
        "table": "Please extract the table from the image and represent it in OTSL format.",
        "formula": "Please write out the expression of the formula in the image using LaTeX format.",
        "end2end": (
            "List the document elements in reading order, including their categories, "
            "coordinates, and the content of each element."
        ),
    }

    def prompt_for(self, task: str, label: str | None = None) -> str | None:
        if task == "text" and label in {"Column", "Picture"}:
            return None
        return self._PROMPTS.get(task)

    def serialize_target(self, task: str, value: object) -> str:
        if task == "layout":
            import json

            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if task == "formula":
            text = str(value).strip()
            return text if text.startswith("$") else f"${text}$"
        return str(value)

    def serve_command(self, model_path: str, port: int) -> list[str] | None:
        # Il repo ufficiale espone un wrapper `serve.py` proprio (non il
        # semplice `vllm serve`), da eseguire con l'env dedicato (gcc-13,
        # NVCC_PREPEND_FLAGS per il JIT di flash-attn) dalla cartella
        # `parsing/` del checkout vendored. Invece di reimplementare quelle
        # variabili qui, deleghiamo allo script già collaudato: un adapter
        # può sempre restituire l'invocazione di un wrapper invece del
        # comando nudo, `serve_manager.py` resta generico (`Popen(argv)`).
        script = Path(__file__).resolve().parents[3] / "scripts" / "serve_model.sh"
        return [str(script), model_path, str(port)]


class _StubAdapter:
    """Adapter di sola ricetta: capacità dichiarate + download, niente ancora
    di prompt/serving/training reali. `prompt_for`/`serialize_target` alzano
    un errore esplicito se qualcosa tenta di usarli prima che l'adapter sia
    completato — meglio di un prompt sbagliato passato in silenzio a un
    modello che non lo capisce."""

    def prompt_for(self, task: str, label: str | None = None) -> str | None:
        raise NotImplementedError(
            f"adapter '{self.adapter_id}': prompt non ancora implementati "
            "(solo download disponibile in questa fase)"
        )

    def serialize_target(self, task: str, value: object) -> str:
        raise NotImplementedError(
            f"adapter '{self.adapter_id}': training non ancora implementato"
        )

    def serve_command(self, model_path: str, port: int) -> list[str] | None:
        return None


class MinerU2_5Adapter(_StubAdapter):
    """opendatalab/MinerU2.5 — due stadi (layout su downscale + contenuto su
    crop nativo), OTSL nativo per le tabelle: l'architettura più vicina a
    quella già in uso per MonkeyOCRv2.

    `serve_command` è verificato sulla model card HF (`vllm serve
    "opendatalab/MinerU2.5-2509-1.2B"`). `prompt_for` resta NON implementato:
    la card ufficiale non documenta prompt diretti, e l'uso previsto passa dal
    pacchetto `mineru-vl-utils` (logica client di preprocessing/postprocessing
    del two-stage, non un semplice prompt testuale) — va verificato contro un
    server reale prima di dichiararlo funzionante."""

    adapter_id = "mineru2.5"
    capabilities = ModelCapabilities(
        adapter_id=adapter_id,
        display_name="MinerU2.5",
        tasks=("layout", "text", "table", "formula"),
        coordinate_system="unverified",
        table_format="otsl",
        training_types=(),
        inference_modes=("vllm",),
        hardware=("cuda",),
        languages=("en", "zh", "multilingual-partial"),
        hf_repo="opendatalab/MinerU2.5-2509-1.2B",
        approx_size_gb=2.5,
        license_note="",
        train_toolchain="none",
        serve_backend="vllm-openai",
        served_model_name="mineru2.5",
    )

    def serve_command(self, model_path: str, port: int) -> list[str] | None:
        return [
            "vllm", "serve", model_path,
            "--port", str(port),
            "--served-model-name", self.capabilities.served_model_name,
        ]


class DotsOcrAdapter(_StubAdapter):
    """rednote-hilab/dots.ocr — VLM singolo per layout+contenuto in una sola
    generazione, tabelle in HTML (convertibili con `otsl.html_to_otsl()`).
    dots.ocr-1.5 è stato rinominato dots.mocr dal publisher (marzo 2026): id
    dell'adapter e repo restano quelli del progetto originale finché non si
    verifica quale variante conviene installare di default.

    `serve_command` e il prompt "end2end" sono verificati sul README
    ufficiale. Lo schema esatto delle chiavi JSON restituite (`category` vs
    `label`, forma dell'oggetto) NON è verificato contro un server reale:
    `_tolerant_items()` in `inference.py` cerca la chiave `label`, che il
    README descrive come `category` — va controllato prima di fidarsi del
    parsing, non solo del prompt."""

    adapter_id = "dots-ocr"
    capabilities = ModelCapabilities(
        adapter_id=adapter_id,
        display_name="dots.ocr",
        tasks=("layout", "text", "table"),
        coordinate_system="unverified",
        table_format="html",
        training_types=(),
        inference_modes=("vllm",),
        hardware=("cuda",),
        languages=("en", "multilingual-partial"),
        hf_repo="rednote-hilab/dots.ocr",
        approx_size_gb=3.4,
        license_note='Richiede la dicitura "Built with dots.ocr" su pesi/derivati ridistribuiti.',
        train_toolchain="community",
        serve_backend="vllm-openai",
        served_model_name="dots-ocr",
    )

    _PROMPTS = {
        "end2end": (
            "Please output the layout information from the PDF image, including each "
            "layout element's bbox, its category, and the corresponding text content."
        ),
    }

    def prompt_for(self, task: str, label: str | None = None) -> str | None:
        if task in self._PROMPTS:
            return self._PROMPTS[task]
        raise NotImplementedError(
            f"adapter '{self.adapter_id}': prompt per '{task}' non verificato — "
            "solo il percorso end2end è confermato sul README ufficiale"
        )

    def serve_command(self, model_path: str, port: int) -> list[str] | None:
        return [
            "vllm", "serve", model_path,
            "--port", str(port),
            "--tensor-parallel-size", "1",
            "--gpu-memory-utilization", "0.9",
            "--chat-template-content-format", "string",
            "--served-model-name", self.capabilities.served_model_name,
            "--trust-remote-code",
        ]


class GlmOcrAdapter(_StubAdapter):
    """zai-org/GLM-OCR — percorso di fine-tuning più documentato del gruppo
    (tutorial ufficiale LLaMA-Factory, full-FT e LoRA)."""

    adapter_id = "glm-ocr"
    capabilities = ModelCapabilities(
        adapter_id=adapter_id,
        display_name="GLM-OCR",
        tasks=("layout", "text", "table"),
        coordinate_system="unverified",
        table_format="markdown",
        training_types=(),
        inference_modes=("vllm",),
        hardware=("cuda",),
        languages=("en", "zh", "multilingual-partial"),
        hf_repo="zai-org/GLM-OCR",
        approx_size_gb=None,
        license_note="Codice Apache-2.0, pesi MIT.",
        train_toolchain="llama-factory",
        serve_backend="none",
    )


class DeepSeekOcrAdapter(_StubAdapter):
    """deepseek-ai/DeepSeek-OCR — compressione ottica del contesto, utile per
    pagine molto dense (\"Gundam mode\"). Id repo per la variante -2 non
    verificato: da confermare sull'org Hugging Face di DeepSeek prima di
    abilitare il download reale."""

    adapter_id = "deepseek-ocr"
    capabilities = ModelCapabilities(
        adapter_id=adapter_id,
        display_name="DeepSeek-OCR",
        tasks=("layout", "text", "table"),
        coordinate_system="unverified",
        table_format="markdown",
        training_types=(),
        inference_modes=("vllm",),
        hardware=("cuda",),
        languages=("en", "zh", "multilingual-partial"),
        hf_repo="deepseek-ai/DeepSeek-OCR",  # da confermare per la variante -2
        approx_size_gb=6.0,
        license_note="MIT.",
        train_toolchain="unsloth",
        serve_backend="none",
    )


class PaddleOcrVlAdapter(_StubAdapter):
    """PaddlePaddle/PaddleOCR-VL-1.6 — punteggio più alto su OmniDocBench fra
    i candidati studiati, 0.9B, ma il fine-tuning ufficiale passa da ERNIEKit
    (toolchain diversa da ms-swift/LLaMA-Factory) e non è ancora integrato."""

    adapter_id = "paddleocr-vl"
    capabilities = ModelCapabilities(
        adapter_id=adapter_id,
        display_name="PaddleOCR-VL-1.6",
        tasks=("layout", "text", "table", "formula"),
        coordinate_system="unverified",
        table_format="html",
        training_types=(),
        inference_modes=("vllm",),
        hardware=("cuda",),
        languages=("en", "zh") + tuple(f"lang-{i}" for i in range(1)),  # 100+ lingue dichiarate, non enumerate qui
        hf_repo="PaddlePaddle/PaddleOCR-VL-1.6",
        approx_size_gb=1.8,
        license_note="",
        train_toolchain="none",  # ERNIEKit non ancora integrato
        serve_backend="none",
    )


class Qwen3VlAdapter(_StubAdapter):
    """Qwen/Qwen3-VL-8B-Instruct — VLM generalista, non specializzato
    documenti, ma con l'ecosistema di fine-tuning più maturo in assoluto
    (LoRA su singola GPU, compatibile con i template stock di ms-swift: stessa
    toolchain già in uso per MonkeyOCRv2, senza bisogno di un nuovo env).
    Id repo esatto (tag -Instruct, dimensione) da confermare su Hugging Face
    prima di abilitare il download reale."""

    adapter_id = "qwen3-vl-8b"
    capabilities = ModelCapabilities(
        adapter_id=adapter_id,
        display_name="Qwen3-VL-8B",
        tasks=("layout", "text", "table", "formula"),
        coordinate_system="unverified",
        table_format=None,
        training_types=("lora", "full"),
        inference_modes=("vllm",),
        hardware=("cuda",),
        languages=("en", "zh", "multilingual-partial"),
        hf_repo="Qwen/Qwen3-VL-8B-Instruct",  # da confermare
        approx_size_gb=16.0,
        license_note="Apache-2.0.",
        train_toolchain="ms-swift",
        serve_backend="none",
    )


_ADAPTERS: dict[str, ModelAdapter] = {
    adapter.adapter_id: adapter
    for adapter in (
        MonkeyOCRv2ParsingAdapter(),
        MinerU2_5Adapter(),
        DotsOcrAdapter(),
        GlmOcrAdapter(),
        DeepSeekOcrAdapter(),
        PaddleOcrVlAdapter(),
        Qwen3VlAdapter(),
    )
}


def list_adapters() -> list[dict]:
    return [asdict(adapter.capabilities) for adapter in _ADAPTERS.values()]


def get_adapter(adapter_id: str) -> ModelAdapter:
    try:
        return _ADAPTERS[adapter_id]
    except KeyError as exc:
        raise ValueError(f"adapter modello sconosciuto: {adapter_id}") from exc
