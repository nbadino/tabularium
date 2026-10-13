"""Tassonomia etichette (schema Historic Shipping Index).

Le classi sono quelle **pubbliche** del parsing (§2.7 di AGENTS.md) più le
classi custom del corpus (numero/data del fascicolo, testate di colonna, …).
Lo schema di default è il punto di partenza; ogni progetto lo estende con le
proprie classi, salvate in `projects.settings_json["labels"]` (modello
additivo: i default restano sempre disponibili, le custom si aggiungono e si
rimuovono).

I prompt fanno riferimento a ALL_PROMPT ufficiale (core_runner.py).
"""
from __future__ import annotations

import re

from ..schemas import LabelDef, LabelSchemaOut

_PROMPT_TEXT = "Please output the text content from the image."
_PROMPT_TABLE = "Please extract the table from the image and represent it in OTSL format."
_PROMPT_FORMULA = (
    "Please write out the expression of the formula in the image using LaTeX format."
)

# Classi pubbliche del parsing (§2.7): il modello base le conosce già, quindi
# usarle è transfer gratuito. L'origine `public` di una classe è questo insieme.
PUBLIC_LABELS = frozenset({
    "Caption",
    "Footnote",
    "List-item",
    "Page-footer",
    "Page-header",
    "Section-header",
    "Text",
    "Title",
    "Formula",
    "Table",
    "Picture",
})

# Colori: inchiostri d'epoca (terre, verdi, bruni) scuri abbastanza da staccare
# sulla carta ingiallita delle scansioni (≥ 4,3:1, misurati su #e9dcb0) e
# lontani dal rosso, che sul canvas resta solo alla selezione. I gialli, il
# bianco e il rosa di prima scendevano a 1,1–2,0:1 e il rosa del Table
# sembrava una selezione.
DEFAULT_LABELS: list[LabelDef] = [
    # `Title` e `Page-header` sono classi PUBBLICHE del parsing (§2.7): il modello
    # base le conosce già, usarle è transfer gratuito rispetto a inventare classi
    # custom per la stessa cosa fisica.
    LabelDef(name="Title", color="#5c4033", short="TTL", prompt_kind="text", prompt=_PROMPT_TEXT, aliases=["title"]),
    LabelDef(name="Page-header", color="#45594f", short="PGH", prompt_kind="text", prompt=_PROMPT_TEXT, aliases=["header"]),
    # Il numero e la data del fascicolo sono due cose distinte, in due posizioni
    # distinte, e la data è il campo più importante dell'intero corpus (ogni
    # movimento nave è relativo ad essa). Tenerle separate evita di mantenere
    # una regex per ciascun formato di testata: il corpus ne ha almeno tre fra
    # il 1940 e il 1973. Non si chiama `Date` perché le date dentro le celle
    # sono centinaia per pagina e nessuna classe di layout può etichettarle.
    LabelDef(name="Issue-number", color="#24493d", short="NUM", prompt_kind="text", prompt=_PROMPT_TEXT, aliases=["number", "issue number"]),
    LabelDef(name="Issue-date", color="#6b4f1d", short="DAT", prompt_kind="text", prompt=_PROMPT_TEXT, aliases=["date", "issue date", "document date", "document_date"]),
    LabelDef(name="Headline", color="#704214", short="HL", prompt_kind="text", prompt=_PROMPT_TEXT, aliases=["headline"]),
    LabelDef(name="Byline", color="#5a5046", short="BYL", prompt_kind="text", prompt=_PROMPT_TEXT, aliases=["byline"]),
    LabelDef(name="Column", color="#4a5560", short="COL", prompt_kind="structure", prompt="", aliases=["column"]),
    LabelDef(name="Text", color="#4b5d16", short="TXT", prompt_kind="text", prompt=_PROMPT_TEXT, aliases=["text", "paragraph"]),
    # Le pagine del Voyage Supplement non sono tabelle ma sequenze di schede-nave
    # dentro colonne parallele: servono le classi di lista, non OTSL.
    LabelDef(name="Section-header", color="#7a5a12", short="SEC", prompt_kind="text", prompt=_PROMPT_TEXT, aliases=["section header", "paragraph title"]),
    LabelDef(name="List-item", color="#556b2f", short="LI", prompt_kind="text", prompt=_PROMPT_TEXT, aliases=["list", "list item"]),
    LabelDef(name="Advertisement", color="#6a4a5a", short="ADV", prompt_kind="text", prompt=_PROMPT_TEXT, aliases=["advertisement", "ad"]),
    LabelDef(name="Note", color="#2f5560", short="NTE", prompt_kind="text", prompt=_PROMPT_TEXT, aliases=["note", "aside"]),
    LabelDef(name="Table", color="#1f6b5e", short="TBL", prompt_kind="table", prompt=_PROMPT_TABLE, aliases=["table", "grid"]),
    LabelDef(name="Formula", color="#5b6114", short="FML", prompt_kind="formula", prompt=_PROMPT_FORMULA, aliases=["formula", "equation"]),
    LabelDef(name="Picture", color="#5d5d52", short="IMG", prompt_kind="picture", prompt="", aliases=["image", "picture", "figure"]),
]

PROMPT_KINDS = ("text", "table", "formula", "picture", "structure")
# Tipi che non hanno senso senza un prompt di riconoscimento.
_KINDS_REQUIRING_PROMPT = ("text", "table", "formula")

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 ._-]{0,39}$")
_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$")
_MAX_ALIASES = 20

_DEFAULT_NAMES = frozenset(label.name for label in DEFAULT_LABELS)


def _clean_aliases(values: object) -> list[str]:
    """Normalizza una lista di sinonimi: stringhe non vuote, deduplicate."""
    if not isinstance(values, (list, tuple)):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        token = str(value or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= _MAX_ALIASES:
            break
    return out


class LabelValidationError(ValueError):
    """Errore di validazione con codice i18n + segnaposto (v. `services.i18n`)."""

    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


def label_origin(name: str) -> str:
    """`public` se il modello base conosce la classe, altrimenti `custom`."""
    return "public" if name in PUBLIC_LABELS else "custom"


def _with_origin(label: LabelDef) -> LabelDef:
    return label.model_copy(
        update={
            "origin": label_origin(label.name),
            "builtin": label.name in _DEFAULT_NAMES,
        }
    )


def default_schema() -> LabelSchemaOut:
    return LabelSchemaOut(labels=[_with_origin(label) for label in DEFAULT_LABELS])


def project_schema(settings: dict | None) -> LabelSchemaOut:
    """Classi del progetto: i default, più le custom salvate in settings.

    Modello additivo: i default non si rimuovono; una custom non può riusare un
    nome di default (la validazione lo impedisce in scrittura, qui la si salta
    per sicurezza).
    """
    labels = [_with_origin(label) for label in DEFAULT_LABELS]
    seen = {label.name for label in labels}
    raw_custom = (settings or {}).get("labels") or []
    for raw in raw_custom:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name or name in seen:
            continue
        try:
            label = LabelDef(
                name=name,
                color=str(raw.get("color") or "#4a5560"),
                short=str(raw.get("short") or name[:3].upper()),
                prompt_kind=str(raw.get("prompt_kind") or "text"),
                prompt=str(raw.get("prompt") or ""),
                aliases=_clean_aliases(raw.get("aliases")),
            )
        except (TypeError, ValueError):
            continue
        labels.append(_with_origin(label))
        seen.add(name)
    return LabelSchemaOut(labels=labels)


def validate_labels(items: list) -> list[dict]:
    """Valida le classi custom di un progetto e le normalizza in dict.

    Solleva `LabelValidationError` (codice i18n) sul primo problema: per il
    fine-tuning una tassonomia incoerente è un danno silenzioso, quindi i
    controlli sono rigidi e nominano la causa.
    """
    cleaned: list[dict] = []
    seen: set[str] = set()
    for item in items:
        name = str(getattr(item, "name", "") or "").strip()
        if not _NAME_RE.match(name):
            raise LabelValidationError("label_name_invalid", name=name or "—")
        if name in _DEFAULT_NAMES:
            raise LabelValidationError("label_name_conflict", name=name)
        if name in seen:
            raise LabelValidationError("label_name_duplicate", name=name)
        seen.add(name)

        color = str(getattr(item, "color", "") or "").strip()
        if not _COLOR_RE.match(color):
            raise LabelValidationError("label_color_invalid", name=name)

        short = str(getattr(item, "short", "") or "").strip()
        if not (1 <= len(short) <= 6):
            raise LabelValidationError("label_short_invalid", name=name)

        kind = str(getattr(item, "prompt_kind", "") or "").strip()
        if kind not in PROMPT_KINDS:
            raise LabelValidationError("label_kind_invalid", name=name, kind=kind or "—")

        prompt = str(getattr(item, "prompt", "") or "").strip()
        if kind in _KINDS_REQUIRING_PROMPT and not prompt:
            raise LabelValidationError("label_prompt_missing", name=name, kind=kind)

        aliases = _clean_aliases(getattr(item, "aliases", None))
        for alias in aliases:
            if not _ALIAS_RE.match(alias):
                raise LabelValidationError("label_alias_invalid", name=name, alias=alias)

        cleaned.append({
            "name": name,
            "color": color,
            "short": short,
            "prompt_kind": kind,
            "prompt": prompt,
            "aliases": aliases,
        })
    return cleaned
