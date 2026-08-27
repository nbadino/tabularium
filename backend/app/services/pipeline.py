"""Interfacce estendibili per una pipeline a plugin.

I componenti concreti restano separati: scanner, prelabeler, exporter,
trainer, evaluator e inference provider possono essere registrati da adapter
futuri senza cambiare il core delle annotazioni.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ScannerPlugin(Protocol):
    plugin_id: str
    def scan(self, source: str) -> list[dict[str, Any]]: ...


class PrelabelerPlugin(Protocol):
    plugin_id: str
    def predict(self, image_path: str, **kwargs: Any) -> list[dict[str, Any]]: ...


class ExporterPlugin(Protocol):
    plugin_id: str
    def export(self, project_id: int, destination: str) -> dict[str, Any]: ...


class TrainerPlugin(Protocol):
    plugin_id: str
    def start(self, project_id: int, config: dict[str, Any]) -> dict[str, Any]: ...


class EvaluatorPlugin(Protocol):
    plugin_id: str
    def evaluate(self, project_id: int, config: dict[str, Any]) -> dict[str, Any]: ...


class InferencePlugin(Protocol):
    plugin_id: str
    def infer(self, image_path: str, prompt: str, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PluginDescriptor:
    plugin_id: str
    kind: str
    display_name: str
    version: str = "1.0"


_REGISTRY: dict[str, PluginDescriptor] = {}


def register_plugin(descriptor: PluginDescriptor) -> PluginDescriptor:
    if not descriptor.plugin_id or not descriptor.kind:
        raise ValueError("plugin_id e kind sono obbligatori")
    _REGISTRY[descriptor.plugin_id] = descriptor
    return descriptor


def list_plugins() -> list[dict[str, str]]:
    return [descriptor.__dict__.copy() for descriptor in _REGISTRY.values()]


for _descriptor in (
    PluginDescriptor("builtin-scan", "scanner", "Archivio locale"),
    PluginDescriptor("builtin-ocr", "prelabeler", "OCR opzionale"),
    PluginDescriptor("builtin-export", "exporter", "Exporter dataset"),
    PluginDescriptor("builtin-training", "trainer", "ms-swift runner"),
    PluginDescriptor("builtin-eval", "evaluator", "Metriche documentali"),
    PluginDescriptor("builtin-vllm", "inference", "vLLM OpenAI-compatible"),
):
    register_plugin(_descriptor)
