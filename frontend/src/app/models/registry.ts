/**
 * Registro modelli e regole di compatibilità modello ↔ destinazione.
 *
 * Tutto ciò che decide «questo modello può girare lì?» vive qui, in funzioni
 * pure: il selettore del modello, quello della destinazione e la
 * configurazione leggono la stessa risposta, così nessun passo offre ciò che
 * il successivo rifiuterà.
 */
import { apiGet } from '../../lib/api'

export interface ModelItem {
  adapter_id: string
  display_name: string
  tasks: string[]
  table_format: string | null
  hf_repo: string
  approx_size_gb: number | null
  license_note: string
  train_toolchain: string
  serve_backend: string
  maturity: 'supported' | 'experimental' | 'catalog' | 'unavailable' | string
  supports_native: boolean
  supports_two_stage: boolean
  supports_end2end: boolean
  export_ready: boolean
  local_serve_ready: boolean
  /** Dove questo modello gira *su questa macchina*, con la causa quando non
   *  gira. Non è deducibile dal sistema operativo: dipende dall'architettura
   *  del modello (un port MLX esiste per alcuni, non per tutti). */
  local: {
    runnable: boolean
    runtime: string | null
    reason: string | null
    mlx_repo: string | null
  }
  cloud_serve_ready: boolean
  /** Template Modal che serve questo modello; `null` = nessuna ricetta. */
  cloud_template: string | null
  download_only: boolean
  downloadable?: boolean
  checkpoint_detached?: boolean
  installed: boolean
  downloading: boolean
  /** Stima del totale da scaricare, per la barra. Approssimata: è la
   *  dimensione dichiarata dall'adapter, non una misura del repo remoto. */
  expected_bytes: number | null
  path: string | null
  size_bytes: number
  state: string
  error: string | null
  vram_warning: string | null
  runtime_ready?: boolean
  runtime_state?: string | null
  runtime_error?: string | null
}

export type Destination = 'local' | 'vast' | 'runpod' | 'modal' | 'manual'

/** Ordine di presentazione e di raccomandazione: prima ciò che non costa
 *  niente, poi il serverless (si spegne da solo), poi le GPU a noleggio,
 *  infine l'endpoint già esistente, che presuppone un server altrui. */
export const DESTINATIONS: readonly Destination[] = ['local', 'modal', 'vast', 'runpod', 'manual']

export const REMOTE_DESTINATIONS: readonly Destination[] = ['vast', 'runpod', 'modal']

export const CUSTOM_ID_PREFIX = 'custom-'

export const isCustom = (m: Pick<ModelItem, 'adapter_id'>) => m.adapter_id.startsWith(CUSTOM_ID_PREFIX)

export const isDestination = (v: string | null | undefined): v is Destination =>
  v != null && (DESTINATIONS as readonly string[]).includes(v)

export interface DestinationVerdict {
  ok: boolean
  /** Chiave i18n della causa quando `ok` è falso. */
  reasonKey?: string
  /** Un checkpoint personalizzato non si deploya: si pubblica. */
  publish?: boolean
}

/**
 * Può `model` girare su `dest`? Rispecchia le regole che i pannelli dei
 * provider applicano davvero: una ricetta Modal esiste solo per i modelli con
 * `cloud_template`; un checkpoint personalizzato si pubblica su Vast o Modal
 * ma non ha una via RunPod; il locale è il verdetto del backend per *questa*
 * macchina.
 */
export function destinationVerdict(model: ModelItem, dest: Destination): DestinationVerdict {
  switch (dest) {
    case 'local':
      return model.local?.runnable
        ? { ok: true }
        : { ok: false, reasonKey: `localCompute.reason.${model.local?.reason ?? 'no_local_runtime'}` }
    case 'vast':
      if (isCustom(model)) return { ok: true, publish: !model.cloud_serve_ready }
      return model.cloud_serve_ready ? { ok: true } : { ok: false, reasonKey: 'modelsHub.why.noCloudRecipe' }
    case 'modal':
      if (isCustom(model)) return { ok: true, publish: true }
      return model.cloud_template ? { ok: true } : { ok: false, reasonKey: 'modelsHub.why.noModalTemplate' }
    case 'runpod':
      if (isCustom(model)) return { ok: false, reasonKey: 'modelsHub.why.customNoRunpod' }
      return model.cloud_serve_ready ? { ok: true } : { ok: false, reasonKey: 'modelsHub.why.noCloudRecipe' }
    case 'manual':
      // Un endpoint esterno serve qualsiasi modello, ma senza un percorso di
      // parsing il riconoscimento non saprebbe leggerne l'output.
      return model.supports_native ? { ok: true } : { ok: false, reasonKey: 'modelsHub.why.noParser' }
  }
}

/** La prima destinazione praticabile nell'ordine di `DESTINATIONS`. */
export function recommendedDestination(model: ModelItem): Destination | null {
  return DESTINATIONS.find((d) => destinationVerdict(model, d).ok) ?? null
}

/** Un modello si può scegliere solo se almeno una destinazione lo accoglie:
 *  altrimenti il secondo passo sarebbe un vicolo cieco. */
export function isSelectable(model: ModelItem): boolean {
  return recommendedDestination(model) !== null
}

export type StepId = 'model' | 'destination' | 'configure'

/**
 * Il passo mostrato: quello chiesto, ma mai uno i cui prerequisiti mancano
 * (un URL condiviso o un modello rimosso non devono aprire una
 * configurazione senza modello).
 */
export function resolveStep(
  requested: StepId | null,
  model: ModelItem | null,
  dest: Destination | null,
): StepId {
  if (!model) return 'model'
  const destOk = dest != null && destinationVerdict(model, dest).ok
  if (requested === 'model') return 'model'
  if (requested === 'destination' || !destOk) return 'destination'
  return 'configure'
}

// --- Cache del registro ------------------------------------------------------

// Il catalogo cambia solo dopo un'azione esplicita dell'utente (installazione,
// deploy o rimozione); cinque minuti coprono una sessione di lavoro senza
// rendere invisibili quei cambiamenti, perché le mutazioni forzano il reload.
const MODEL_REGISTRY_CACHE_TTL_MS = 5 * 60_000
// v2 invalida la cache pre-registro TeleOCR: la lista di adapter serviti dal
// backend può cambiare con un aggiornamento dell'app senza mutazioni utente.
const MODEL_REGISTRY_STORAGE_KEY = 'tabularium.models.registry.v2'
let modelRegistryCache: { items: ModelItem[]; loadedAt: number } | null = null
let modelRegistryRequest: Promise<ModelItem[]> | null = null

export function readStoredModelRegistry(): ModelItem[] {
  try {
    const raw = localStorage.getItem(MODEL_REGISTRY_STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as { items?: ModelItem[]; loadedAt?: number }
    if (!Array.isArray(parsed.items)) return []
    if (modelRegistryCache === null) {
      modelRegistryCache = { items: parsed.items, loadedAt: Number(parsed.loadedAt) || 0 }
    }
    return parsed.items
  } catch {
    return []
  }
}

function storeModelRegistry(items: ModelItem[]) {
  try {
    localStorage.setItem(MODEL_REGISTRY_STORAGE_KEY, JSON.stringify({ items, loadedAt: Date.now() }))
  } catch {
    /* storage non disponibile: il registro in memoria resta sufficiente */
  }
}

export async function fetchModelRegistry(force = false): Promise<ModelItem[]> {
  if (!force && modelRegistryCache && Date.now() - modelRegistryCache.loadedAt < MODEL_REGISTRY_CACHE_TTL_MS) {
    return modelRegistryCache.items
  }
  if (!modelRegistryRequest) {
    modelRegistryRequest = apiGet<{ items: ModelItem[] }>('/models')
      .then((res) => {
        modelRegistryCache = { items: res.items, loadedAt: Date.now() }
        storeModelRegistry(res.items)
        return res.items
      })
      .finally(() => {
        modelRegistryRequest = null
      })
  }
  return modelRegistryRequest
}

// --- Formati -----------------------------------------------------------------

export function fmtBytes(n: number): string {
  if (n <= 0) return '0 B'
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(0)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`
}

/** Durata leggibile: `45 s`, `2 min 14 s`, `1 h 07 min`. */
export function fmtDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s} s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} min ${String(s % 60).padStart(2, '0')} s`
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`
}
