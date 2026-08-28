"""Schemi Pydantic condivisi tra API e (concettualmente) frontend."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# --- Progetti ----------------------------------------------------------------
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    archive_dir: str = Field(min_length=1, description="Cartella archivio con le scansioni")


class ProjectOut(BaseModel):
    id: int
    name: str
    root_dir: str
    archive_dir: str | None = None
    settings_json: dict = {}
    pages_count: int = 0
    created_at: str


class ProjectList(BaseModel):
    items: list[ProjectOut]


class StudyProtocolIn(BaseModel):
    corpus_scope: str = Field(default="", max_length=2000)
    time_start: str | None = None
    time_end: str | None = None
    objective: str = Field(default="", max_length=4000)
    domain_profile: str = "tabularium-list-1900s"
    model_adapter: str = "monkeyocrv2-parsing"
    gold_pages: list[int] = Field(default_factory=list, max_length=500)


class StudyProtocolOut(StudyProtocolIn):
    version: int = 1
    updated_at: str


# --- Pagine ------------------------------------------------------------------
class PageOut(BaseModel):
    id: int
    project_id: int
    rel_path: str
    abs_path: str
    source_kind: str
    pdf_page: int | None = None
    width: int
    height: int
    issue_date: str | None = None
    issue_no: str | None = None
    page_no: str | None = None
    page_type: str | None = None
    status: str
    created_at: str


class PageUpdate(BaseModel):
    issue_date: str | None = None
    issue_no: str | None = None
    page_no: str | None = None
    page_type: str | None = None
    status: str | None = None


class PageList(BaseModel):
    items: list[PageOut]


# --- Scan --------------------------------------------------------------------
class ScanReportOut(BaseModel):
    found_files: int = 0
    registered: int = 0
    duplicates: int = 0
    unsupported: int = 0
    errors: list[str] = []


# --- Blocchi (annotazioni) ----------------------------------------------------
# points: lista di coordinate [x, y] in PIXEL della pagina sorgente.
class BlockWrite(BaseModel):
    id: int | None = Field(default=None, ge=1)
    label: str
    kind: str = "rect"
    points: list[list[float]]
    content: str = ""
    order_idx: int | None = None
    confirmed: bool = False


class BlockBulkWrite(BaseModel):
    items: list[BlockWrite]


class BlockUpdate(BaseModel):
    label: str | None = None
    kind: str | None = None
    points: list[list[float]] | None = None
    content: str | None = None
    order_idx: int | None = None
    confirmed: bool | None = None


class BlockOut(BaseModel):
    id: int
    page_id: int
    label: str
    kind: str
    points: list[list[float]]
    content: str
    order_idx: int | None = None
    confirmed: bool
    prefill_source: str | None = None
    updated_at: str


class BlockListOut(BaseModel):
    items: list[BlockOut]


# --- Tassonomia label ---------------------------------------------------------
class LabelDef(BaseModel):
    name: str
    color: str
    short: str
    prompt_kind: str  # 'text' | 'table' | 'formula' | 'picture' | 'structure'
    prompt: str = ""


class LabelSchemaOut(BaseModel):
    labels: list[LabelDef]


# --- Tabelle (grid) -----------------------------------------------------------
class TableCell(BaseModel):
    r: int = Field(ge=0)
    c: int = Field(ge=0)
    rowspan: int = Field(default=1, ge=1)
    colspan: int = Field(default=1, ge=1)
    text: str = ""
    # Chi ha scritto il testo: `manual` quando l'annotatore l'ha toccata, `ocr`
    # o `model` per il prefill. `verified` è falso finché un umano non conferma:
    # il builder distingue le due cose perché il dataset è fatto dalle correzioni,
    # non dall'output grezzo (le celle senza testo non sono interessate).
    source: Literal["manual", "ocr", "model"] = "manual"
    verified: bool = True


class TableGrid(BaseModel):
    rows: int = Field(ge=1, le=256)
    cols: int = Field(ge=1, le=256)
    cells: list[TableCell] = []
    phantom_cols: list[int] = []
    vlines: list[float] = []
    hlines: list[float] = []


class TableGridOut(BaseModel):
    grid: TableGrid | None = None


class TableSaveOut(BaseModel):
    grid: TableGrid
    otsl: str


# --- Rilevamento struttura tabella --------------------------------------------
class TableDetectRequest(BaseModel):
    # Frazione di righe su cui un allineamento deve ricorrere per valere come
    # confine: alzarla dà meno colonne ma più solide.
    min_support: float = Field(default=0.22, ge=0.0, le=1.0)
    suppress_leaders: bool = True
    # Come riempire le celle:
    #   none  → solo struttura, celle vuote
    #   ocr   → RapidOCR/PaddleOCR cella per cella (nessuna GPU richiesta)
    #   model → MonkeyOCRv2 a bande: struttura E contenuto dal modello, molto
    #           più accurato sulle celle corte, ma richiede il server vLLM
    fill: Literal["none", "ocr", "model"] = "none"
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)
    rows_per_band: int = Field(default=15, ge=1, le=200)
    # Nessuna euristica: 0 significa che la tabella non dichiara intestazioni.
    header_rows: int = Field(default=0, ge=0, le=20)


class TableCellRecognizeRequest(BaseModel):
    """Ri-riconoscimento di una singola cella della griglia corrente."""

    r: int = Field(ge=0)
    c: int = Field(ge=0)
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)


class TableCellRecognizeOut(BaseModel):
    text: str
    score: float
    engine: str


class TableDetectOut(BaseModel):
    grid: TableGrid
    # Su quante righe è attestato ciascun confine verticale (len = cols + 1).
    column_support: list[int] = []
    # Condizioni misurate che limitano la proposta, come codici stabili
    # (`skewed`): la UI li traduce, il backend non spedisce mai prosa.
    warnings: list[str] = []
    diagnostics: dict = {}
    ocr: dict | None = None


# --- Convenzioni di trascrizione ----------------------------------------------
class ConventionItem(BaseModel):
    id: str
    label: str
    checked: bool = True


class ConventionsOut(BaseModel):
    conventions: list[ConventionItem]


class ConventionsIn(BaseModel):
    conventions: list[ConventionItem]


# --- Training -----------------------------------------------------------------
class TrainConfig(BaseModel):
    model: str | None = None
    model_path: str | None = None
    train_type: Literal["lora", "full"] = "lora"
    lora_rank: int = Field(default=8, ge=1, le=256)
    lora_alpha: int = Field(default=32, ge=1, le=1024)
    freeze_vit: bool = True
    epochs: float = Field(default=1.0, gt=0, le=100)
    learning_rate: float = Field(default=1e-5, gt=0, le=0.1)
    batch_size: int = Field(default=4, ge=1, le=128)
    grad_accum: int = Field(default=1, ge=1, le=128)
    max_length: int = Field(default=16384, ge=128, le=65536)
    max_pixels: int = Field(default=1003520, ge=1000, le=4000000)
    gpus: str = "0"
    nproc: int = Field(default=1, ge=1, le=64)
    eval_steps: int = Field(default=200, ge=1, le=100000)
    output_dir: str | None = None


# --- Inferenza Cloud & Locale -------------------------------------------------
class InferenceConfigIn(BaseModel):
    enabled: bool = True
    url: str = Field(default="http://127.0.0.1:8888/v1", max_length=1000)
    model: str = Field(default="MonkeyOCRv2", max_length=200)
    api_key: str = Field(default="", max_length=500)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    timeout: int = Field(default=180, ge=5, le=3600)
    max_pixels: int | None = Field(default=None, ge=1000, le=20000000)


class InferenceConfigOut(BaseModel):
    enabled: bool = True
    url: str
    model: str
    has_api_key: bool = False
    extra_headers: dict[str, str] = {}
    timeout: int = 180
    max_pixels: int | None = None
    is_cloud: bool = False
    available: bool = False
    latency_ms: float | None = None
    models_available: list[str] = []
    error: str | None = None


class InferenceTestRequest(BaseModel):
    url: str
    model: str = "MonkeyOCRv2"
    api_key: str | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)
    timeout: int = 10


class InferenceTestOut(BaseModel):
    ok: bool
    url: str
    model: str
    models_available: list[str] = []
    latency_ms: float | None = None
    is_cloud: bool = False
    error: str | None = None

