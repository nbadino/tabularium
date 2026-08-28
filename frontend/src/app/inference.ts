/**
 * Gestione centralizzata dell'endpoint di inferenza (Locale o Cloud).
 *
 * Sincronizza lo stato tra il backend (SQLite / env) e la UI locale.
 * Supporta tunnel SSH (Vast.ai), proxy HTTPS (RunPod), API keys e
 * verifica della latenza in tempo reale.
 */
import { useSyncExternalStore } from 'react'
import { apiGet, apiPost, apiPut } from '../lib/api'
import type { InferenceConfig, InferenceTestResult } from '../lib/types'

const KEY = 'tabularium.inference'

export interface InferenceCfg {
  enabled: boolean
  url: string
  model: string
  apiKey: string
  hasApiKey: boolean
  extraHeaders: Record<string, string>
  timeout: number
  maxPixels: number | null
  isCloud: boolean
  available: boolean
  latencyMs: number | null
  modelsAvailable: string[]
  error: string | null
}

const EMPTY: InferenceCfg = {
  enabled: true,
  url: '',
  model: '',
  apiKey: '',
  hasApiKey: false,
  extraHeaders: {},
  timeout: 180,
  maxPixels: null,
  isCloud: false,
  available: false,
  latencyMs: null,
  modelsAvailable: [],
  error: null,
}

export const INFERENCE_PRESETS = [
  {
    id: 'local',
    label: 'Locale (GPU su questa macchina)',
    url: 'http://127.0.0.1:8888/v1',
    model: 'MonkeyOCRv2',
    hint: 'Richiede GPU NVIDIA locale avviata con ./scripts/serve_model.sh',
  },
  {
    id: 'vast_ssh',
    label: 'Vast.ai (Tunnel SSH Sicuro)',
    url: 'http://127.0.0.1:8888/v1',
    model: 'MonkeyOCRv2',
    hint: 'Collega la GPU remota via ./scripts/cloud/ssh_tunnel.sh',
  },
  {
    id: 'runpod_proxy',
    label: 'RunPod (Proxy HTTPS Diretto)',
    url: 'https://<POD_ID>-8888.proxy.runpod.net/v1',
    model: 'MonkeyOCRv2',
    hint: 'Endpoint HTTPS fornito direttamente dalla dashboard di RunPod',
  },
  {
    id: 'vast_direct',
    label: 'Vast.ai / Cloud VPS (Porta Pubblica)',
    url: 'http://<IP_PUBBLICO>:<PORTA>/v1',
    model: 'MonkeyOCRv2',
    hint: 'Connessione diretta tramite IP pubblico e porta esposta',
  },
] as const

function loadLocal(): InferenceCfg {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return EMPTY
    const parsed = JSON.parse(raw) as Partial<InferenceCfg>
    return {
      ...EMPTY,
      ...parsed,
      enabled: parsed.enabled ?? true,
      url: String(parsed.url ?? ''),
      model: String(parsed.model ?? ''),
      apiKey: String(parsed.apiKey ?? ''),
    }
  } catch {
    return EMPTY
  }
}

let current: InferenceCfg = loadLocal()
const listeners = new Set<() => void>()

function notify(): void {
  listeners.forEach((fn) => fn())
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function getInference(): InferenceCfg {
  return current
}

export function setInference(patch: Partial<InferenceCfg>): void {
  current = { ...current, ...patch }
  try {
    localStorage.setItem(KEY, JSON.stringify(current))
  } catch {
    /* storage non disponibile */
  }
  notify()
}

/** Carica la configurazione effettiva dal backend */
export async function syncInferenceFromBackend(): Promise<InferenceCfg> {
  try {
    const res = await apiGet<InferenceConfig>('/system/inference')
    current = {
      ...current,
      enabled: res.enabled ?? true,
      url: res.url,
      model: res.model,
      hasApiKey: res.has_api_key ?? false,
      extraHeaders: res.extra_headers ?? {},
      timeout: res.timeout ?? 180,
      maxPixels: res.max_pixels ?? null,
      isCloud: res.is_cloud ?? false,
      available: res.available ?? false,
      latencyMs: res.latency_ms ?? null,
      modelsAvailable: res.models_available ?? [],
      error: res.error ?? null,
    }
    notify()
    return current
  } catch {
    return current
  }
}

/** Salva e applica la configurazione nel backend SQLite */
export async function saveInferenceToBackend(cfg: {
  enabled?: boolean
  url?: string
  model?: string
  apiKey?: string
  extraHeaders?: Record<string, string>
  timeout?: number
}): Promise<InferenceCfg> {
  const payload: Record<string, unknown> = {
    enabled: cfg.enabled !== undefined ? cfg.enabled : current.enabled,
    url: (cfg.url !== undefined ? cfg.url : current.url).trim(),
    model: (cfg.model !== undefined ? cfg.model : current.model).trim() || 'MonkeyOCRv2',
    timeout: cfg.timeout ?? current.timeout ?? 180,
  }
  if (cfg.apiKey !== undefined) {
    payload.api_key = cfg.apiKey.trim()
  }
  if (cfg.extraHeaders !== undefined) {
    payload.extra_headers = cfg.extraHeaders
  }

  const res = await apiPut<InferenceConfig>('/system/inference', payload)
  current = {
    ...current,
    enabled: res.enabled ?? true,
    url: res.url,
    model: res.model,
    apiKey: cfg.apiKey ?? current.apiKey,
    hasApiKey: res.has_api_key ?? false,
    extraHeaders: res.extra_headers ?? {},
    timeout: res.timeout ?? 180,
    maxPixels: res.max_pixels ?? null,
    isCloud: res.is_cloud ?? false,
    available: res.available ?? false,
    latencyMs: res.latency_ms ?? null,
    modelsAvailable: res.models_available ?? [],
    error: res.error ?? null,
  }
  setInference(current)
  return current
}

export async function toggleInferenceEnabled(enabled: boolean): Promise<InferenceCfg> {
  return saveInferenceToBackend({ enabled })
}

/** Esegue un test di connessione in tempo reale verso l'endpoint specificato */
export async function testInferenceConnection(opts: {
  url: string
  model?: string
  apiKey?: string
  extraHeaders?: Record<string, string>
  timeout?: number
}): Promise<InferenceTestResult> {
  return apiPost<InferenceTestResult>('/system/inference/test', {
    url: opts.url.trim(),
    model: opts.model?.trim() || 'MonkeyOCRv2',
    api_key: opts.apiKey?.trim() || '',
    extra_headers: opts.extraHeaders ?? {},
    timeout: opts.timeout ?? 10,
  })
}

/** Sottoscrive un componente alla configurazione condivisa. */
export function useInference(): InferenceCfg {
  return useSyncExternalStore(subscribe, getInference, getInference)
}

