"""Vocabolario nativo dei modelli, come dato.

Il fine-tuning parla **una sola lingua**: i nomi canonici di `labeling.py`.
Ogni modello però emette i propri nomi (PP-DocLayout `number`, MinerU
`doc_title`, …), e la traduzione verso il canonico esisteva già — sparsa in
`dict` dentro i metodi di parsing, invisibile a chi annota.

Questo modulo è la **fonte unica** di quelle mappe: gli adapter importano da
qui invece di ridefinire i letterali, e l'API può dire, per ogni classe
canonica, **quali modelli la emettono già** (`known_models_for`). Una classe
che un modello conosce nativamente è transfer gratuito; una che nessuno
conosce è un token nuovo, da insegnare con esempi.

Perché conta per chi annota: «già nota» non è una proprietà assoluta di una
classe, è una proprietà della coppia **classe + modello**. La UI calcola il
badge rispetto al modello attivo del progetto.
"""
from __future__ import annotations

import re

from .labeling import PUBLIC_LABELS

# --- sotto-mappe per metodo/sito d'uso ----------------------------------------
# Rimangono distinte dove i fallback differiscono (una label ignota in
# `parse_layout` resta il nome grezzo, in `parse_native_result` ricade su
# "Text"): unirle cambierebbe il comportamento.

MINERU_LAYOUT_LABELS: dict[str, str] = {
    "text": "Text",
    "title": "Title",
    "doc_title": "Title",
    "table": "Table",
    "equation": "Formula",
    "image": "Picture",
    "chart": "Picture",
}

MINERU_NATIVE_LABELS: dict[str, str] = {
    "text": "Text", "title": "Title", "table": "Table",
    "equation": "Formula", "formula_number": "Formula",
    "equation_block": "Formula", "image": "Picture", "chart": "Picture",
    "image_block": "Picture", "header": "Page-header",
    "footer": "Page-footer", "page_number": "Page-footer",
    "page_footnote": "Footnote", "table_footnote": "Footnote",
    "image_footnote": "Footnote", "list": "List-item",
    "list_item": "List-item", "ref_text": "List-item",
    "table_caption": "Caption", "image_caption": "Caption",
    "code_caption": "Caption", "code": "Text", "algorithm": "Text",
    "aside_text": "Note", "index": "Text", "phonetic": "Text",
}

GLM_LABELS: dict[str, str] = {
    "title": "Title", "text": "Text", "table": "Table",
    "figure": "Picture", "formula": "Formula", "header": "Page-header",
    "footer": "Page-footer", "page_number": "Page-footer", "seal": "Picture",
    "reference": "Text", "chart": "Picture", "image": "Picture",
    "display_formula": "Formula", "inline_formula": "Formula",
}

DEEPSEEK_LABELS: dict[str, str] = {
    "title": "Title", "doc_title": "Title", "paragraph_title": "Section-header",
    "section_header": "Section-header", "text": "Text", "paragraph": "Text",
    "table": "Table", "figure": "Picture", "image": "Picture", "chart": "Picture",
    "caption": "Caption", "footnote": "Footnote", "header": "Page-header",
    "footer": "Page-footer", "list": "List-item", "list_item": "List-item",
    "formula": "Formula", "equation": "Formula",
}

QWEN3_LABELS: dict[str, str] = {
    "caption": "Caption", "footnote": "Footnote", "footer": "Page-footer",
    "header": "Page-header", "list-item": "List-item", "formula": "Formula",
    "image": "Picture", "chart": "Picture", "table": "Table",
}

TELEOCR_LABELS: dict[str, str] = {
    "text": "Text",
    "title": "Title",
    "table": "Table",
    "image": "Picture",
    "header": "Page-header",
    "footer": "Page-footer",
    # Sul corpus la testata porta il numero del fascicolo.
    "page_number": "Issue-number",
    "page_footnote": "Footnote",
    "aside_text": "Text",
    "equation": "Formula",
    "equation_block": "Formula",
    "ref_text": "List-item",
    "list": "List-item",
    "table_caption": "Caption",
    "image_caption": "Caption",
    "table_footnote": "Footnote",
    "image_footnote": "Footnote",
}

LIGHTON_LABELS: dict[str, str] = {
    "text": "Text", "title": "Title", "list": "List-item",
    "header": "Page-header", "footer": "Page-footer",
    # Come TeleOCR: sulle pagine `index` è il numero del fascicolo.
    "page_number": "Issue-number",
    "footnote": "Footnote", "caption": "Caption", "formula": "Formula",
    "code": "Text", "table": "Table", "aside_text": "Note",
    "image": "Picture", "chart": "Picture",
    "header_image": "Picture", "footer_image": "Picture",
}

# Intera tassonomia di PP-DocLayout: una label fuori elenco resta un buco
# dichiarato, non una voce muta.
PADDLE_LABELS: dict[str, str] = {
    # titoli
    "doc_title": "Title",
    "paragraph_title": "Title",
    "title": "Title",
    # didascalie di figure, tabelle e grafici
    "figure_title": "Caption",
    "chart_title": "Caption",
    "table_title": "Caption",
    # testata e piede
    "number": "Issue-number",
    "header": "Page-header",
    "footer": "Page-footer",
    "footnote": "Footnote",
    "vision_footnote": "Footnote",
    # corpi di testo
    "text": "Text",
    "abstract": "Text",
    "content": "Text",
    "aside_text": "Text",
    "vertical_text": "Text",
    "reference": "List-item",
    # contenuti non testuali
    "table": "Table",
    "formula": "Formula",
    "image": "Picture",
    "figure": "Picture",
    "chart": "Picture",
    "seal": "Picture",
    "header_image": "Picture",
    "footer_image": "Picture",
}

# --- unione per adapter (unicamente per calcolare le classi note) --------------
UNLIMITED_LABELS: dict[str, str] = {
    "text": "Text",
    "title": "Title",
    "table": "Table",
    "figure": "Picture",
    "image": "Picture",
    "chart": "Picture",
    "formula": "Formula",
    "equation": "Formula",
    "header": "Page-header",
    "page_header": "Page-header",
    "footer": "Page-footer",
    "page_footer": "Page-footer",
    "page_number": "Page-footer",
    "list": "List-item",
    "list_item": "List-item",
    "caption": "Caption",
    "footnote": "Footnote",
    "reference": "Text",
}

NATIVE_LABELS: dict[str, dict[str, str]] = {
    "mineru2.5": {**MINERU_LAYOUT_LABELS, **MINERU_NATIVE_LABELS},
    "glm-ocr": GLM_LABELS,
    "unlimited-ocr": UNLIMITED_LABELS,
    "deepseek-ocr": DEEPSEEK_LABELS,
    "qwen3-vl-8b": QWEN3_LABELS,
    "teleocr": TELEOCR_LABELS,
    "lightonocr-3": LIGHTON_LABELS,
    "paddleocr-vl": PADDLE_LABELS,
}

# Classi canoniche che un adapter può emettere senza passare da una mappa
# nativa (regole strutturali, tag HTML, prompt che enumera già il canonico).
EXTRA_CANONICAL: dict[str, frozenset[str]] = {
    "qwen3-vl-8b": frozenset({"Title", "List-item"}),
}

# Adapter che emettono **direttamente** le classi pubbliche del parsing: la
# loro lingua nativa coincide con la tassonomia ufficiale (§2.7).
DIRECT_CANONICAL: frozenset[str] = frozenset({"monkeyocrv2-parsing", "dots-ocr"})


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
