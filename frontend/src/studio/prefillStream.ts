/** Consumatore dell'endpoint SSE `/projects/{id}/prelabel/stream`.
 *
 * Il prefill può prendere da secondi (OCR) a minuti (end2end su GPU remota):
 * invece di far attendere la risposta completa, ogni blocco scritto dal
 * backend arriva come evento e la UI lo mostra subito — ciò che compare
 * sul canvas esiste già nel database.
 */
import { getLocale } from '../i18n'

export type PrefillEvent =
  | { type: 'start'; engine: string }
  | { type: 'page'; page_id: number }
  | { type: 'output'; page_id: number; phase: string; text: string }
  | { type: 'output_reset'; page_id: number; phase: string }
  | {
      type: 'block'
      page_id: number
      block: {
        id: number
        label: string
        points: [number, number][]
        content: string
        order_idx: number
        prefill_source: string
        grid: unknown | null
        error: string | null
      }
    }
  | { type: 'page_done'; page_id: number; summary: Record<string, unknown> }
  | { type: 'error'; message: string; page_id?: number }
  | { type: 'end' }

/** Parser SSE puro: consuma il buffer e restituisce gli eventi completi più
 *  il residuo incompleto. Il protocollo è `data: <json>\n\n` per evento;
 *  i chunk di rete possono spezzare a metà, quindi si tiene il resto. */
export function parseSse(buffer: string): { events: PrefillEvent[]; rest: string } {
  const events: PrefillEvent[] = []
  let rest = buffer
  let sep: number
  while ((sep = rest.indexOf('\n\n')) !== -1) {
    const chunk = rest.slice(0, sep)
    rest = rest.slice(sep + 2)
    for (const line of chunk.split('\n')) {
      if (line.startsWith('data: ')) {
        try {
          events.push(JSON.parse(line.slice(6)) as PrefillEvent)
        } catch {
          // Un evento malformato non uccide lo stream: si scarta.
        }
      }
    }
  }
  return { events, rest }
}

export interface PrefillStreamOptions {
  projectId: number
  body: Record<string, unknown>
  onEvent: (ev: PrefillEvent) => void
  signal?: AbortSignal
  /** Limite totale dello stream: un prefill può durare minuti (cold start
   *  Modal + generazioni lunghe), ma non deve poter restare appeso all'infinito
   *  — senza limite, `prefillBusy` resta true e l'intero controllo Prefill
   *  muore, seletto compreso. Default: 15 minuti. */
  timeoutMs?: number
}

/** POST + lettura progressiva del body: `EventSource` non può fare POST,
 *  quindi si legge lo stream a mano con lo stesso parser. */
export async function runPrelabelStream(opts: PrefillStreamOptions): Promise<void> {
  const controller = new AbortController()
  const timer = setTimeout(
    () => controller.abort(new Error('prefill timeout')),
    opts.timeoutMs ?? 900_000,
  )
  if (opts.signal) {
    if (opts.signal.aborted) controller.abort(opts.signal.reason)
    else opts.signal.addEventListener('abort', () => controller.abort(opts.signal!.reason))
  }
  let res: Response
  try {
    res = await fetch(`/api/projects/${opts.projectId}/prelabel/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept-Language': getLocale(),
      },
      body: JSON.stringify(opts.body),
      signal: controller.signal,
    })
  } catch (e) {
    clearTimeout(timer)
    throw e
  }
  if (!res.ok || !res.body) {
    clearTimeout(timer)
    let detail = `${res.status}`
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch {
      /* risposta senza JSON: resta lo status */
    }
    throw new Error(detail)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const { events, rest } = parseSse(buffer)
      buffer = rest
      for (const ev of events) opts.onEvent(ev)
    }
    // Il residuo può contenere l'ultimo evento se lo stream chiude senza il
    // separatore finale: meglio consumarlo che perderlo.
    const tail = parseSse(buffer + '\n\n')
    for (const ev of tail.events) opts.onEvent(ev)
  } finally {
    clearTimeout(timer)
  }
}
