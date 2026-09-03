/** Tipi condivisi tra frontend e API backend. */

// --- Autenticazione & utenti (self-hosted) -----------------------------------

export interface User {
  id: number
  username: string
  email: string | null
  role: 'admin' | 'editor' | 'viewer'
  active: boolean
  created_at: string
  last_login_at: string | null
}

export interface AuthStatus {
  auth_enabled: boolean
  needs_setup: boolean
  allow_registration: boolean
  instance_name: string
  user: User | null
}

export interface InstanceSettings {
  instance_name: string
  allow_registration: boolean
  default_new_user_role: 'editor' | 'viewer'
}

export interface ComputeProfile {
  id: number
  name: string
  provider: 'local' | 'ssh' | 'vast' | 'runpod' | 'modal' | 'custom'
  purpose: 'inference' | 'training' | 'both'
  model_adapter_id: string
  model_revision: string | null
  served_model_name: string
  endpoint: string
  credential_ref: string | null
  runtime_recipe_id: string | null
  generation_profile_id: string | null
  image_profile_id: string | null
  hardware_profile: Record<string, unknown>
  active: boolean
  has_credential: boolean
  last_health_check: string | null
  last_health_ok: boolean | null
  last_health_error: string | null
  created_at: string
  updated_at: string
}

export interface HealthResponse {
  status: string
  app: string
  version: string
}

export interface SystemInfo {
  app: string
  version: string
  data_dir: string
  db_path: string
  schema_version: string | null
  python: string
  platform: string
  capabilities?: {
    dashboard?: boolean
    cpu_ocr?: boolean
    local_cuda?: boolean
    remote_gpu?: boolean
    cuda_note?: string | null
  }
}

// --- Progetti & pagine (M1) --------------------------------------------------

export interface Project {
  id: number
  name: string
  owner_id: number | null
  root_dir: string
  archive_dir: string | null
  settings_json: Record<string, unknown>
  pages_count: number
  created_at: string
}

export interface ProjectMember {
  user_id: number
  username: string
  email: string | null
  role: 'owner' | 'editor' | 'viewer'
  active: boolean
}

export interface WorkflowStatus {
  project_id: number
  counts: Record<string, number>
  total_pages: number
  approved_pages: number
  progress: number
  next_page: {
    id: number
    rel_path: string
    status: string
    issue_date: string | null
    issue_no: string | null
    page_no: string | null
    blocks: number
  } | null
}

export interface PageItem {
  id: number
  project_id: number
  rel_path: string
  abs_path: string
  source_kind: 'image' | 'pdf'
  pdf_page: number | null
  width: number
  height: number
  issue_date: string | null
  issue_no: string | null
  page_no: string | null
  page_type: string | null
  status: string
  annotation_revision?: number
  created_at: string
}

export type TransformEngine = 'deskew' | 'monkeyocr' | 'perspective' | 'mesh'

export interface TransformMetadata {
  version: number
  level: string
  engine: string
  requested_engine?: string
  actual_engine?: string
  size: [number, number]
  angle?: number
  warnings?: string[]
  error?: string | null
  diagnostics?: Record<string, unknown>
  created_at?: string
  accepted?: boolean
}

export interface TransformState {
  page_id: number
  active: TransformMetadata | null
  candidate: TransformMetadata | null
  original_preview_url: string
  active_preview_url: string | null
  candidate_preview_url: string | null
  engines?: Partial<Record<TransformEngine, { available: boolean; reason?: string }>>
  generated?: TransformMetadata
  accepted?: boolean
  rejected?: boolean
}

export interface ScanReport {
  found_files: number
  registered: number
  duplicates: number
  unsupported: number
  missing?: number
  errors: string[]
}

export const PAGE_TYPES = [
  'front',
  'editorial',
  'shipping',
  'casualties',
  'adverts',
  'misc',
] as const

export const PAGE_STATUSES = [
  'new',
  'annotated',
  'qa',
  'exported',
  'review',
  'approved',
] as const

// --- Annotazioni (M2) --------------------------------------------------------

export interface BlockOut {
  id: number
  page_id: number
  label: string
  kind: 'rect' | 'polygon'
  points: number[][]
  content: string
  order_idx: number | null
  confirmed: boolean
  prefill_source: string | null
  updated_at: string
  /** Presente solo nella risposta a una PATCH: la revisione della pagina dopo
   *  la modifica, da riallineare nello stato di annotazione. */
  annotation_revision?: number
}

/** Risposta del salvataggio bulk: tutti i blocchi della pagina, la revisione
 *  raggiunta e — allineati al payload — gli id assegnati ai suoi item. */
export interface BlockListOut {
  items: BlockOut[]
  annotation_revision?: number
  assigned_ids?: number[]
}

export interface BlockBulkWrite {
  expected_revision?: number
  /** Blocchi cancellati davvero: il server non lo deduce dall'assenza, perché
   *  il canvas non porta tutte le bozze del prefill. */
  deleted_ids?: number[]
  items: Array<{
    id?: number
    label: string
    kind: 'rect' | 'polygon'
    points: number[][]
    content: string
    order_idx: number | null
    confirmed?: boolean
  }>
}

export interface LabelDef {
  name: string
  color: string
  short: string
  prompt_kind: 'text' | 'table' | 'formula' | 'picture' | 'structure'
  prompt: string
}

export interface LabelSchema {
  labels: LabelDef[]
}

// --- Tabelle (M3) ------------------------------------------------------------

export interface TableCell {
  r: number
  c: number
  rowspan: number
  colspan: number
  text: string
  /** Chi ha scritto il testo: `manual` dopo una modifica umana, altrimenti il prefill. */
  source?: 'manual' | 'ocr' | 'model'
  /** Falso finché un annotatore non conferma il testo proposto dal prefill. */
  verified?: boolean
}

export interface TableGrid {
  rows: number
  cols: number
  cells: TableCell[]
  phantom_cols: number[]
  /** Righe header dichiarate manualmente; zero significa nessuna. */
  header_rows?: number
  vlines?: number[]
  hlines?: number[]
  /** Confini verticali piegati, persistiti riga per riga quando rilevati. */
  row_columns?: number[][]
  row_columns_proven?: boolean[][]
}

export interface TableGridOut {
  grid: TableGrid | null
}

export interface TableSaveOut {
  grid: TableGrid
  otsl: string
  /** Revisione della pagina dopo il salvataggio: va riallineata nello stato di
   *  annotazione, altrimenti il prossimo autosave del canvas vede un 409. */
  annotation_revision?: number
}

export interface PrefillEngines {
  ocr: { available: boolean; engine: string | null }
  model: {
    available: boolean
    url: string
    model: string
    adapter_id?: string
    /** Cosa sa fare DAVVERO l'adapter attivo (sondato lato backend, non
     *  dichiarativo): un modello può servire senza supportare entrambe le
     *  modalità (es. MinerU2.5 fa due-stadi ma non end2end). */
    supports_two_stage?: boolean
    supports_end2end?: boolean
    /** Il percorso che il prefill offre oggi: inferenza nativa
     *  (prompt di default del modello, immagine non riscalata). */
    supports_native?: boolean
  }
  /** Quale usare di default: il modello quando è servito. */
  recommended: 'ocr' | 'model' | null
}

/** Risposta del ri-riconoscimento di una singola cella (non persiste nulla). */
export interface TableCellRecognizeOut {
  text: string
  score: number
  engine: string
}

export interface TableDetectRequest {
  min_support?: number
  suppress_leaders?: boolean
  /** none = solo struttura · ocr = per cella · model = struttura e testo dal modello */
  fill?: 'none' | 'ocr' | 'model'
  min_score?: number
  rows_per_band?: number
}

export interface TableDetectOut {
  grid: TableGrid
  /** Su quante righe è attestato ciascun confine verticale (len = cols + 1). */
  column_support: number[]
  /**
   * Condizioni misurate che limitano la proposta: codici stabili, tradotti a
   * video. Facoltativo perché un backend avviato prima di questo campo non lo
   * manda affatto, e l'editor non deve rompersi per una risposta più vecchia.
   */
  warnings?: string[]
  diagnostics: {
    pitch_px?: number
    row_bands?: number
    glyph_height_px?: number
    glyphs?: number
    otsu?: number
    shear?: number
    skew_deg?: number
    /** Confini interni riga per riga, 0–1: dove il taglio passa davvero. */
    row_columns?: number[][]
    /** Per ciascun confine di ciascuna riga, se un varco è stato provato. */
    row_columns_proven?: boolean[][]
    /** Quanti tagli sono rimasti sul prior perché nessun varco li provava. */
    row_columns_unproven?: number
    leader_dots_suppressed?: number
    gutters?: number[]
    content_x?: [number, number]
  }
  ocr: {
    engine?: string
    cells?: number
    filled?: number
    blank?: number
    below_threshold?: number
    uncertain?: number
    snapped?: boolean
    mean_score?: number
  } | null
}

// --- Convenzioni --------------------------------------------------------------

export interface ConventionItem {
  id: string
  label: string
  checked: boolean
}

export interface ConventionsOut {
  conventions: ConventionItem[]
}

// --- Dataset (M4) --------------------------------------------------------------

export interface DatasetReport {
  project_id: number
  built_at: string
  split: { ratio: number; seed: number; strategy?: string }
  adapter_id?: string
  pages: { total: number; with_blocks: number; train: number; val: number }
  counts: Record<string, { train: number; val: number }>
  crops_generated: number
  warnings: string[]
  files: Array<{ path: string; size: number; lines: number }>
  sample_lines: Record<string, string[]>
  dataset_dir: string
}

export interface DatasetStatus {
  built: boolean
  report: DatasetReport | null
}

// --- Training (M5) --------------------------------------------------------------

export interface GpuInfo {
  index: string
  name: string
  memory_total: number
  memory_used: number
  utilization: number
  temp: number
}

export interface TrainingMetric {
  t?: number
  loss?: number
  lr?: number
  step?: number
}

export interface TrainingRun {
  run_id: string
  state: string
  started_at?: string
  ended_at?: string
  exit_code?: number
  config?: Record<string, unknown>
}

export interface TrainingStatus {
  active: boolean
  run: TrainingRun | null
  log_tail: string
  metrics: TrainingMetric[]
  gpu: GpuInfo[]
}

export interface TrainingPreflight {
  ready: boolean
  errors: string[]
  warnings: string[]
  dataset: { dir: string; counts: Record<string, number> }
  training_repo: string | null
  python: string
  gpus: GpuInfo[]
}

export interface TrainConfigBody {
  executor?: 'local' | 'ssh' | 'vast' | 'runpod'
  ssh_host?: string
  ssh_user?: string
  ssh_port?: number
  ssh_key_path?: string
  ssh_root?: string
  ssh_train_repo?: string
  ssh_python?: string
  resume_run_id?: string
  model?: string
  model_path?: string
  adapter_id?: string
  train_type?: 'lora' | 'full'
  lora_rank?: number
  lora_alpha?: number
  freeze_vit?: boolean
  epochs?: number
  learning_rate?: number
  batch_size?: number
  grad_accum?: number
  max_length?: number
  max_pixels?: number
  gpus?: string
  nproc?: number
  eval_steps?: number
  output_dir?: string
}

// --- Valutazione (M6) ------------------------------------------------------------

export interface LayoutMetrics {
  n_gt: number
  n_pred: number
  matched: number
  precision: number
  recall: number
  mean_iou_of_matched: number | null
}

export interface OrderMetrics {
  mean_levenshtein_norm: number
  exact_pct: number | null
}

export interface EvalPage {
  page_id: number
  rel_path: string
  error?: string
  layout: {
    n_gt: number
    n_pred: number
    matched: number
    precision: number
    recall: number
    mean_iou: number
  }
  order: { levenshtein_norm: number; exact: boolean; n: number }
  gt_items: Array<{ bbox: number[]; label: string; content?: string }>
  pred_items: Array<{ bbox: number[]; label: string }>
  text?: Array<{ label: string; cer: number; wer: number; gt: string; hyp: string }>
  tables?: Array<{ structure_ok: boolean; cell_cer: number }>
  actions?: string[]
}

export interface EvalReport {
  project_id: number
  evaluated_at: string
  config: {
    server_url: string
    model: string
    with_text: boolean
    split: { ratio: number; seed: number }
  }
  pages_evaluated: number
  val_pages: number
  aggregates: {
    layout: LayoutMetrics
    order: OrderMetrics
    text: { n: number; mean_cer: number | null; mean_wer: number | null }
    tables: { n: number; structure_ok_pct: number | null; mean_cell_cer: number | null }
  }
  pages: EvalPage[]
  warnings: string[]
  eval_dir: string
  comparison?: {
    layout_recall_delta: number
    layout_precision_delta: number
    text_cer_delta: number
    order_delta: number
  }
}

export interface PlaygroundResult {
  ok: boolean
  server: string
  model: string
  width: number
  height: number
  items: Array<{ bbox_norm: number[]; bbox_px: number[]; label: string; content: string }>
}

export interface InferenceConfig {
  enabled?: boolean
  url: string
  model: string
  adapter_id?: string
  provider?: string | null
  resource_id?: string | null
  has_api_key?: boolean
  extra_headers?: Record<string, string>
  timeout?: number
  max_pixels?: number | null
  is_cloud?: boolean
  available?: boolean
  latency_ms?: number | null
  models_available?: string[]
  error?: string | null
}

export interface InferenceTestResult {
  ok: boolean
  url: string
  model: string
  models_available: string[]
  latency_ms?: number | null
  is_cloud?: boolean
  error?: string | null
}

// --- Sessioni persistenti di riconoscimento ---------------------------------

export type RecognitionRunState =
  | 'queued'
  | 'running'
  | 'finished'
  | 'finished_with_errors'
  | 'failed'
  | 'cancelled'

export type RecognitionItemState =
  | 'queued'
  | 'running'
  | 'finished'
  | 'failed'
  | 'cancelled'

export interface RecognitionRunItem {
  id: number
  run_id: number
  page_id: number
  rel_path: string
  page_status: string
  state: RecognitionItemState
  detected: number
  inserted: number
  blocks: number
  drafts: number
  result: {
    summary?: Record<string, unknown>
    blocks?: Array<Record<string, unknown>>
  }
  error: string | null
  started_at: string | null
  ended_at: string | null
}

export interface RecognitionRun {
  id: number
  project_id: number
  state: RecognitionRunState
  engine: 'model' | 'ocr'
  mode: 'merge' | 'replace_drafts' | 'replace_all'
  model_mode: string
  model_name: string | null
  adapter_id: string | null
  provider: string
  endpoint: string | null
  stop_policy: 'none' | 'disable_inference'
  total_pages: number
  completed_pages: number
  succeeded_pages: number
  failed_pages: number
  error: string | null
  created_at: string
  started_at: string | null
  heartbeat_at: string | null
  ended_at: string | null
  items?: RecognitionRunItem[]
}
