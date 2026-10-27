"""Native model vocabulary, seen from the canonical one.

The fine-tuning pipeline speaks one language: the canonical names in `labeling.py`.
Every model emits its own names (PP-DocLayout `number`, MinerU `doc_title`, ...), and
the translation is a property of the model, so the map lives in the model's spec
(`app/models/<id>.py`). This module is the derived view: the names the adapters import,
the union used to answer "which models already emit this class", and the bridge.

Why it matters for annotators: "already known" is not a property of a class, it is a
property of the pair **class + model**. The UI computes the badge against the active
model of the project.
"""
from __future__ import annotations

import re

from .. import models as model_specs
# Re-exported: `model_adapters` reads these maps while parsing vendor output. The
# data itself is in the specs, which is why nothing here has to be kept in step.
from ..models.deepseek_ocr import DEEPSEEK_LABELS
from ..models.glm_ocr import GLM_LABELS
from ..models.llamaindex_ocria import LLAMAINDEX_LABELS
from ..models.mineru2_5 import MINERU_LAYOUT_LABELS, MINERU_NATIVE_LABELS
from ..models.paddleocr_vl import PADDLE_LABELS
from ..models.qwen3_vl_8b import QWEN3_LABELS
from ..models.teleocr import TELEOCR_LABELS
from ..models.unlimited_ocr import UNLIMITED_LABELS
from .labeling import PUBLIC_LABELS

LIGHTON_LABELS: dict[str, str] = model_specs.spec_for("lightonocr-3").value("labels")

# Union per adapter: only the derived view, used to compute the known classes.
NATIVE_LABELS: dict[str, dict[str, str]] = {
    adapter_id: spec.value("labels")
    for adapter_id, spec in model_specs.SPECS.items()
    if spec.value("labels")
}

# Classi canoniche che un adapter può emettere senza passare da una mappa
# nativa (regole strutturali, tag HTML, prompt che enumera già il canonico).
EXTRA_CANONICAL: dict[str, frozenset[str]] = {
    "qwen3-vl-8b": frozenset({"Title", "List-item"}),
}

# Adapter che emettono **direttamente** le classi pubbliche del parsing: la loro
# lingua nativa coincide con la tassonomia ufficiale. Derivate dalle spec, dove
# `labels=NotApplicable(reason)` è la dichiarazione esplicita.
DIRECT_CANONICAL: frozenset[str] = frozenset(model_specs.not_applicable_reasons("labels"))


def known_canonical(adapter_id: str) -> frozenset[str]:
    """Classi canoniche che il modello `adapter_id` emette nativamente.

    Vuoto = il modello non ha un vocabolario dichiarato mappato sul canonico
    (o emette marker propri): ogni classe è da apprendere.
    """
    if adapter_id in DIRECT_CANONICAL:
        return frozenset(PUBLIC_LABELS)
    mapped = NATIVE_LABELS.get(adapter_id)
    values = set(mapped.values()) if mapped else set()
    values |= EXTRA_CANONICAL.get(adapter_id, frozenset())
    return frozenset(values)


def known_models_for(name: str) -> list[str]:
    """Adapter (id) che emettono già la classe canonica `name`."""
    # Import differito: model_adapters importa questo modulo per le mappe.
    from .model_adapters import list_adapters  # noqa: PLC0415

    return sorted(
        cap["adapter_id"]
        for cap in list_adapters()
        if name in known_canonical(cap["adapter_id"])
    )


# --- risoluzione nativa → canonica (il "bridge") ------------------------------

_SEP_RE = re.compile(r"[\s_\-]+")


def normalize_label(name: object) -> str:
    """Minuscole, separatori (`spazio`, `_`, `-`) unificati a spazio singolo.

    `Table_document`, `table-document` e `Table Document` diventano la stessa
    chiave: il bridge non dipende dall'ortografia del modello.
    """
    return _SEP_RE.sub(" ", str(name or "").strip().lower()).strip()


def resolve_native_label(
    native: object,
    schema_labels: list,
    adapter_id: str | None = None,
) -> str | None:
    """Traduce una label nativa di un modello in una classe del progetto.

    Ordine di risoluzione, dal più specifico al più generico:
    1. **nome canonico esatto** (i modelli che parlano già il canonico, es.
       MonkeyOCRv2);
    2. **mappa nativa dell'adapter** (semantica del modello: vince sul generico);
    3. **alias di progetto/classe** (sinonimi dichiarati, anche per classi
       custom).

    Ritorna `None` se non c'è corrispondenza **o** se la classe risolta non è
    nel progetto: un token fuori tassonomia non deve entrare in silenzio nel
    dataset. Chi chiama decide il fallback e lo dichiara.
    """
    key = normalize_label(native)
    if not key:
        return None

    canonical: dict[str, str] = {}
    for label in schema_labels:
        canonical[normalize_label(label.name)] = label.name
    if key in canonical:
        return canonical[key]

    valid = set(canonical.values())
    mapped = NATIVE_LABELS.get(adapter_id or "", {})
    for native_key, target in mapped.items():
        if normalize_label(native_key) == key and target in valid:
            return target

    for label in schema_labels:
        for alias in getattr(label, "aliases", None) or []:
            if normalize_label(alias) == key:
                return label.name

    return None
