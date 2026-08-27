"""Contratti per profili di dominio e adapter di modelli.

Il database conserva annotazioni ricche e indipendenti dal modello. Un adapter
traduce quello schema nei prompt, nei formati e nei vincoli del modello scelto.
MonkeyOCRv2 è il primo adapter, non il contratto globale dell'applicazione.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
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


class ModelAdapter(Protocol):
    adapter_id: str
    capabilities: ModelCapabilities

    def prompt_for(self, task: str, label: str | None = None) -> str | None: ...

    def serialize_target(self, task: str, value: object) -> str: ...


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
    )

    _PROMPTS = {
        "layout": "Please output the categories and coordinates of the document elements in reading order.",
        "text": "Please output the text content from the image.",
        "table": "Please extract the table from the image and represent it in OTSL format.",
        "formula": "Please write out the expression of the formula in the image using LaTeX format.",
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


_ADAPTERS: dict[str, ModelAdapter] = {
    MonkeyOCRv2ParsingAdapter.adapter_id: MonkeyOCRv2ParsingAdapter(),
}


def list_adapters() -> list[dict]:
    return [asdict(adapter.capabilities) for adapter in _ADAPTERS.values()]


def get_adapter(adapter_id: str) -> ModelAdapter:
    try:
        return _ADAPTERS[adapter_id]
    except KeyError as exc:
        raise ValueError(f"adapter modello sconosciuto: {adapter_id}") from exc
