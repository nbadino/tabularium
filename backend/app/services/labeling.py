"""Tassonomia etichette predefinita (schema Historic Shipping Index).

M2: schema fisso di default; dalla milestone del dataset builder la mappa
prompt/classi diventerà configurabile per progetto (settings_json).
I prompt fanno riferimento a ALL_PROMPT ufficiale (core_runner.py).
"""
from __future__ import annotations

from ..schemas import LabelDef, LabelSchemaOut

_PROMPT_TEXT = "Please output the text content from the image."
_PROMPT_TABLE = "Please extract the table from the image and represent it in OTSL format."
_PROMPT_FORMULA = (
    "Please write out the expression of the formula in the image using LaTeX format."
)

# Colori: inchiostri d'epoca (terre, verdi, bruni) scuri abbastanza da staccare
# sulla carta ingiallita delle scansioni (≥ 4,3:1, misurati su #e9dcb0) e
# lontani dal rosso, che sul canvas resta solo alla selezione. I gialli, il
# bianco e il rosa di prima scendevano a 1,1–2,0:1 e il rosa del Table
# sembrava una selezione.
DEFAULT_LABELS: list[LabelDef] = [
    # `Title` e `Page-header` sono classi PUBBLICHE del parsing (§2.7): il modello
    # base le conosce già, usarle è transfer gratuito rispetto a inventare classi
    # custom per la stessa cosa fisica.
    LabelDef(name="Title", color="#5c4033", short="TTL", prompt_kind="text", prompt=_PROMPT_TEXT),
    LabelDef(name="Page-header", color="#45594f", short="PGH", prompt_kind="text", prompt=_PROMPT_TEXT),
    # Il numero e la data del fascicolo sono due cose distinte, in due posizioni
    # distinte, e la data è il campo più importante dell'intero corpus (ogni
    # movimento nave è relativo ad essa). Tenerle separate evita di mantenere
    # una regex per ciascun formato di testata: il corpus ne ha almeno tre fra
    # il 1940 e il 1973. Non si chiama `Date` perché le date dentro le celle
    # sono centinaia per pagina e nessuna classe di layout può etichettarle.
    LabelDef(name="Issue-number", color="#24493d", short="NUM", prompt_kind="text", prompt=_PROMPT_TEXT),
    LabelDef(name="Issue-date", color="#6b4f1d", short="DAT", prompt_kind="text", prompt=_PROMPT_TEXT),
    LabelDef(name="Headline", color="#704214", short="HL", prompt_kind="text", prompt=_PROMPT_TEXT),
    LabelDef(name="Byline", color="#5a5046", short="BYL", prompt_kind="text", prompt=_PROMPT_TEXT),
    LabelDef(name="Column", color="#4a5560", short="COL", prompt_kind="structure", prompt=""),
    LabelDef(name="Text", color="#4b5d16", short="TXT", prompt_kind="text", prompt=_PROMPT_TEXT),
    # Le pagine del Voyage Supplement non sono tabelle ma sequenze di schede-nave
    # dentro colonne parallele: servono le classi di lista, non OTSL.
    LabelDef(name="Section-header", color="#7a5a12", short="SEC", prompt_kind="text", prompt=_PROMPT_TEXT),
    LabelDef(name="List-item", color="#556b2f", short="LI", prompt_kind="text", prompt=_PROMPT_TEXT),
    LabelDef(name="Advertisement", color="#6a4a5a", short="ADV", prompt_kind="text", prompt=_PROMPT_TEXT),
    LabelDef(name="Note", color="#2f5560", short="NTE", prompt_kind="text", prompt=_PROMPT_TEXT),
    LabelDef(name="Table", color="#1f6b5e", short="TBL", prompt_kind="table", prompt=_PROMPT_TABLE),
    LabelDef(name="Formula", color="#5b6114", short="FML", prompt_kind="formula", prompt=_PROMPT_FORMULA),
    LabelDef(name="Picture", color="#5d5d52", short="IMG", prompt_kind="picture", prompt=""),
]


def default_schema() -> LabelSchemaOut:
    return LabelSchemaOut(labels=DEFAULT_LABELS)