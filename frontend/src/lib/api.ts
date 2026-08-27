/** Client API verso il backend. I percorsi possono avere o no il prefisso `/api`. */
const API_BASE = '/api'

import { getLocale } from '../i18n'

/** Evita il doppio `/api` quando il chiamante passa già il prefisso. */
function joinPath(path: string): string {
  if (path.startsWith('/api')) return path
  return `${API_BASE}${path}`
}

/**
 * Errore di rete o di risposta, con i pezzi che servono a spiegarlo
 * all'utente: `describeError` in `lib/errors.ts` li traduce in
 * {titolo, messaggio, suggerimento}. Non mostrare mai un ApiError grezzo.
 */
export class ApiError extends Error {
  readonly method: string
  readonly path: string
  /** null quando la richiesta non è mai arrivata al server. */
  readonly status: number | null
  readonly body: string

  constructor(
    method: string,
    path: string,
    status: number | null,
    body: string,
  ) {
    super(`API ${method} ${path} → ${status ?? 'nessuna risposta'}`)
    this.name = 'ApiError'
    this.method = method
    this.path = path
    this.status = status
    this.body = body
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  return api<T>('GET', path)
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return api<T>('POST', path, body)
}

export async function apiPatch<T>(path: string, body?: unknown): Promise<T> {
  return api<T>('PATCH', path, body)
}

export async function apiPut<T>(path: string, body?: unknown): Promise<T> {
  return api<T>('PUT', path, body)
}

export async function apiDelete<T>(path: string): Promise<T> {
  return api<T>('DELETE', path)
}

async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response
  const url = joinPath(path)
  try {
    res = await fetch(url, {
      method,
      headers:
        body === undefined
          ? { 'Accept-Language': getLocale() }
          : { 'Content-Type': 'application/json', 'Accept-Language': getLocale() },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (e) {
    // fetch fallisce solo se il server non ha risposto affatto.
    throw new ApiError(method, path, null, e instanceof Error ? e.message : String(e))
  }
  if (!res.ok) {
    throw new ApiError(method, path, res.status, await res.text())
  }
  return res.json() as Promise<T>
}
