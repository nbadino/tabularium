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

const KEY = 'tabularium.inference.preferences'

export interface InferenceCfg {
  enabled: boolean
  url: string
  model: string
  adapterId: string
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
  adapterId: 'monkeyocrv2-parsing',
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

/**
 * Preset di connessione: le etichette sono chiavi i18n (`cloud.presets.*`),
 * tradotte dalla UI al momento dell'uso.
 */
export const INFERENCE_PRESETS = [
  {
    id: 'local',
    labelKey: 'cloud.presets.local',
    hintKey: 'cloud.presets.localHint',
    url: 'http://127.0.0.1:8888/v1',
    model: 'MonkeyOCRv2',
  },
  {
    id: 'vast_ssh',
    labelKey: 'cloud.presets.vastSsh',
    hintKey: 'cloud.presets.vastSshHint',
    url: 'http://127.0.0.1:8888/v1',
    model: 'MonkeyOCRv2',
  },
  {
    id: 'runpod_proxy',
    labelKey: 'cloud.presets.runpodProxy',
    hintKey: 'cloud.presets.runpodProxyHint',
    url: 'https://<POD_ID>-8888.proxy.runpod.net/v1',
    model: 'MonkeyOCRv2',
  },
  {
    id: 'vast_direct',
    labelKey: 'cloud.presets.vastDirect',
    hintKey: 'cloud.presets.vastDirectHint',
    url: 'http://<IP_PUBBLICO>:<PORTA>/v1',
    model: 'MonkeyOCRv2',
  },
  {
    id: 'modal_serverless',
    labelKey: 'cloud.presets.modalServerless',
    hintKey: 'cloud.presets.modalServerlessHint',
    url: 'https://<WORKSPACE>--tabularium-vllm-serve.modal.run/v1',
    model: 'MonkeyOCRv2',
  },
  {
    id: 'runpod_serverless',
    labelKey: 'cloud.presets.runpodServerless',
    hintKey: 'cloud.presets.runpodServerlessHint',
    url: 'https://api.runpod.ai/v2/<ENDPOINT_ID>/openai/v1',
    model: 'MonkeyOCRv2',
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
      // Le credenziali non sono più persistite nel browser. Restano soltanto
      // in memoria per la sessione corrente e il backend non le restituisce.
      apiKey: '',
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
    const { apiKey: _secret, ...safe } = current
    localStorage.setItem(KEY, JSON.stringify(safe))
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
      adapterId: res.adapter_id ?? current.adapterId,
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
  /** Quale adapter interpreta prompt/formato tabella per l'endpoint servito
   *  (`monkeyocrv2-parsing`, `paddleocr-vl`, `mineru2.5`, ...). Omesso =
   *  lascia invariato l'adapter attuale sul backend. */
  adapterId?: string
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
  if (cfg.adapterId !== undefined) {
    payload.adapter_id = cfg.adapterId
  }

  const res = await apiPut<InferenceConfig>('/system/inference', payload)
  current = {
    ...current,
    enabled: res.enabled ?? true,
    url: res.url,
    model: res.model,
    adapterId: res.adapter_id ?? current.adapterId,
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
