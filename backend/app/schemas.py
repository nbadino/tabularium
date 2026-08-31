"""Schemi Pydantic condivisi tra API e (concettualmente) frontend."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


# --- Autenticazione & utenti (self-hosted) ---------------------------------------
class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)


class SetupIn(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)
    email: str | None = Field(default=None, max_length=300)


class RegisterIn(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)
    email: str | None = Field(default=None, max_length=300)


class UserOut(BaseModel):
    id: int
    username: str
    email: str | None = None
    role: str
    active: bool = True
    created_at: str
    last_login_at: str | None = None


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)
    email: str | None = Field(default=None, max_length=300)
    role: str = "editor"
    active: bool = True


class UserUpdate(BaseModel):
    email: str | None = None
    role: str | None = None
    active: bool | None = None


class ResetPasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=500)


class ChangePasswordIn(BaseModel):
    """Cambio password autonomo: richiede la password corrente."""
    current_password: str = Field(min_length=1, max_length=500)
    new_password: str = Field(min_length=8, max_length=500)


class AuthStatusOut(BaseModel):
    auth_enabled: bool
    needs_setup: bool = False
    allow_registration: bool = False
    instance_name: str = "Tabularium"
    user: UserOut | None = None


class SettingsOut(BaseModel):
    instance_name: str
    allow_registration: bool
    default_new_user_role: str


class SettingsIn(BaseModel):
    instance_name: str | None = Field(default=None, max_length=100)
    allow_registration: bool | None = None
    default_new_user_role: Literal["editor", "viewer"] | None = None

    @field_validator("instance_name")
    @classmethod
    def _name_not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("nome istanza vuoto")
        return v


class SecretIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=10000)


class SecretOut(BaseModel):
    name: str
    ref: str
    configured: bool = True



# --- Progetti ----------------------------------------------------------------
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    archive_dir: str = Field(min_length=1, description="Cartella archivio con le scansioni")


class ProjectOut(BaseModel):
    id: int
    name: str
    owner_id: int | None = None
    root_dir: str
    archive_dir: str | None = None
    settings_json: dict = {}
    pages_count: int = 0
    created_at: str


class ProjectList(BaseModel):
    items: list[ProjectOut]


class ProjectMemberIn(BaseModel):
    user_id: int = Field(ge=1)
    role: Literal["editor", "viewer"] = "editor"


class ProjectOwnerIn(BaseModel):
    user_id: int = Field(ge=1)


class ProjectMemberOut(BaseModel):
    user_id: int
    username: str
    email: str | None = None
    role: Literal["owner", "editor", "viewer"]
    active: bool = True


class ComputeProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider: Literal["local", "ssh", "vast", "runpod", "modal", "custom"] = "local"
    purpose: Literal["inference", "training", "both"] = "inference"
    model_adapter_id: str = Field(min_length=1, max_length=120)
    model_revision: str | None = Field(default=None, max_length=300)
    served_model_name: str = Field(min_length=1, max_length=300)
    endpoint: str = Field(min_length=1, max_length=1000)
    credential_ref: str | None = Field(default=None, max_length=300)
    runtime_recipe_id: str | None = Field(default=None, max_length=200)
    generation_profile_id: str | None = Field(default=None, max_length=200)
    image_profile_id: str | None = Field(default=None, max_length=200)
    hardware_profile: dict = Field(default_factory=dict)


class ComputeProfileOut(ComputeProfileIn):
    id: int
    active: bool = False
    has_credential: bool = False
    last_health_check: str | None = None
    last_health_ok: bool | None = None
    last_health_error: str | None = None
    created_at: str
    updated_at: str


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
    annotation_revision: int = 0
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
    # Optional for old API clients; new clients must send the revision from
    # GET /annotations so stale full-page snapshots cannot overwrite work.
    expected_revision: int | None = Field(default=None, ge=0)


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
    annotation_revision: int = 0


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
    executor: Literal["local", "ssh", "vast", "runpod"] = "local"
    ssh_host: str | None = Field(default=None, max_length=255)
    ssh_user: str = Field(default="root", max_length=100)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_key_path: str | None = Field(default=None, max_length=1000)
    ssh_root: str = Field(default="/tmp/tabularium-runs", max_length=1000)
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
    adapter_id: str = "monkeyocrv2-parsing"
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
