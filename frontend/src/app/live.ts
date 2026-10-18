/**
 * Canale live dello Studio (lato client).
 *
 * Un WebSocket per pagina aperta: porta la *presenza* (chi è collegato, chi
 * sta scrivendo) e l'avviso che le annotazioni sono cambiate. Non scrive mai:
 * le modifiche passano dalla REST, qui si riceve solo il segnale di
 * riallineamento.
 *
 * Come `auth.ts`, è uno store minimale letto con `useSyncExternalStore`. Si
 * riconnette da solo con backoff; certi codici di chiusura (sessione assente,
 * permesso negato, pagina inesistente) fermano il tentativo, perché ritentare
 * non cambierebbe l'esito.
 */
import { useSyncExternalStore } from 'react'

export interface LiveParticipant {
  connId: string
  userId: number | null
  username: string
  color: string
  editing: boolean
  canWrite: boolean
}

export interface LiveState {
  pageId: number | null
  status: 'idle' | 'connecting' | 'open' | 'closed'
  participants: LiveParticipant[]
  selfId: string | null
  canWrite: boolean
}

/** Codici di chiusura applicativi del backend. */
const CLOSE_UNAUTHORIZED = 4401
const CLOSE_FORBIDDEN = 4403
const CLOSE_NOT_FOUND = 4404
const STOP_CODES = new Set([CLOSE_UNAUTHORIZED, CLOSE_FORBIDDEN, CLOSE_NOT_FOUND])

/** `readyState` di WebSocket, come costanti: `WebSocket.OPEN` non esiste in
 *  ambienti senza il costruttore (test jsdom) e non va mai toccato lì. */
const WS_CONNECTING = 0
const WS_OPEN = 1

export interface AnnotationsChanged {
  pageId: number
  revision: number
  action: string
  authorId: number | null
  authorName: string | null
}

type Listener = () => void
type ChangeListener = (ev: AnnotationsChanged) => void

const INITIAL: LiveState = {
  pageId: null,
  status: 'idle',
  participants: [],
  selfId: null,
  canWrite: false,
}

let state: LiveState = INITIAL
const listeners = new Set<Listener>()
const changeListeners = new Set<ChangeListener>()

let socket: WebSocket | null = null
let currentPage: number | null = null
let intentionalClose = false
let attempts = 0
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let pingTimer: ReturnType<typeof setInterval> | null = null

function subscribe(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

function getSnapshot(): LiveState {
  return state
}

function set(next: Partial<LiveState>): void {
  state = { ...state, ...next }
  listeners.forEach((fn) => fn())
}

/** Ascolta le notifiche di modifica in arrivo da altri client. */
export function onAnnotationsChanged(fn: ChangeListener): () => void {
  changeListeners.add(fn)
  return () => {
    changeListeners.delete(fn)
  }
}

function toParticipant(raw: unknown): LiveParticipant | null {
  if (!raw || typeof raw !== 'object') return null
  const p = raw as Record<string, unknown>
  const connId = typeof p.conn_id === 'string' ? p.conn_id : null
  if (!connId) return null
  return {
    connId,
    userId: typeof p.user_id === 'number' ? p.user_id : null,
    username: typeof p.username === 'string' ? p.username : '?',
    color: typeof p.color === 'string' ? p.color : '#64748b',
    editing: Boolean(p.editing),
    canWrite: Boolean(p.can_write),
  }
}

function participantsOf(raw: unknown): LiveParticipant[] {
  if (!Array.isArray(raw)) return []
  return raw.map(toParticipant).filter((p): p is LiveParticipant => p !== null)
}

function wsUrl(pageId: number): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}/api/live/pages/${pageId}`
}

function clearTimers(): void {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  if (pingTimer) {
    clearInterval(pingTimer)
    pingTimer = null
  }
}

function handleMessage(raw: string): void {
  let msg: Record<string, unknown>
  try {
    msg = JSON.parse(raw) as Record<string, unknown>
  } catch {
    return
  }
  switch (msg.type) {
    case 'hello':
      set({
        selfId: typeof (msg.self as Record<string, unknown>)?.conn_id === 'string'
          ? ((msg.self as Record<string, unknown>).conn_id as string)
          : null,
        canWrite: Boolean(msg.can_write),
        participants: participantsOf(msg.participants),
      })
      break
    case 'presence':
      set({ participants: participantsOf(msg.participants) })
      break
    case 'peer': {
      // Aggiorna solo il peer interessato: la lista è piccola e immutabile.
      const connId = msg.conn_id
      if (typeof connId !== 'string') break
      set({
        participants: state.participants.map((p) =>
          p.connId === connId && typeof msg.editing === 'boolean'
            ? { ...p, editing: msg.editing }
            : p,
        ),
      })
      break
    }
    case 'annotations_changed': {
      const pageId = typeof msg.page_id === 'number' ? msg.page_id : currentPage
      if (pageId == null || typeof msg.revision !== 'number') break
      const author = (msg.author ?? {}) as Record<string, unknown>
      const ev: AnnotationsChanged = {
        pageId,
        revision: msg.revision,
        action: typeof msg.action === 'string' ? msg.action : 'sync',
        authorId: typeof author.id === 'number' ? author.id : null,
        authorName: typeof author.username === 'string' ? author.username : null,
      }
      changeListeners.forEach((fn) => fn(ev))
      break
    }
    default:
      break
  }
}

function scheduleReconnect(): void {
  if (intentionalClose || currentPage == null) return
  attempts += 1
  const delay = Math.min(8000, 500 * 2 ** Math.min(attempts, 4))
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    openSocket(currentPage as number)
  }, delay)
}

function openSocket(pageId: number): void {
  if (typeof WebSocket === 'undefined') {
    set({ status: 'closed' })
    return
  }
  set({ status: 'connecting' })
  const ws = new WebSocket(wsUrl(pageId))
  socket = ws
  ws.onopen = () => {
    attempts = 0
    set({ status: 'open' })
    pingTimer = setInterval(() => {
      if (socket?.readyState === WS_OPEN) {
        try {
          socket.send(JSON.stringify({ type: 'ping', t: Date.now() }))
        } catch {
          // Il prossimo close riconnette.
        }
      }
    }, 25_000)
  }
  ws.onmessage = (e) => {
    if (typeof e.data === 'string') handleMessage(e.data)
  }
  ws.onclose = (e) => {
    if (socket === ws) socket = null
    if (pingTimer) {
      clearInterval(pingTimer)
      pingTimer = null
    }
    if (intentionalClose || currentPage !== pageId) {
      set({ status: 'closed' })
      return
    }
    if (STOP_CODES.has(e.code)) {
      set({ status: 'closed' })
      return
    }
    set({ status: 'connecting' })
    scheduleReconnect()
  }
  ws.onerror = () => {
    // L'errore è sempre seguito da `onclose`: la logica di riconnessione vive lì.
  }
}

/** Apre (o riapre) il canale per la pagina indicata. */
export function connectPage(pageId: number): void {
  if (currentPage === pageId && (socket?.readyState === WS_OPEN || socket?.readyState === WS_CONNECTING)) {
    return
  }
  intentionalClose = false
  attempts = 0
  clearTimers()
  if (socket) {
    try {
      socket.close()
    } catch {
      // già chiuso
    }
    socket = null
  }
  currentPage = pageId
  set({ pageId, status: 'connecting', participants: [], selfId: null, canWrite: false })
  openSocket(pageId)
}

/** Chiude il canale: nessuna riconnessione fino al prossimo `connectPage`. */
export function disconnectPage(): void {
  intentionalClose = true
  clearTimers()
  currentPage = null
  if (socket) {
    try {
      socket.close()
    } catch {
      // già chiuso
    }
    socket = null
  }
  set({ ...INITIAL })
}

/** Segnala agli altri se stiamo modificando la pagina. */
export function notifyEditing(value: boolean): void {
  if (socket?.readyState !== WS_OPEN) return
  try {
    socket.send(JSON.stringify({ type: 'editing', value }))
  } catch {
    // ignorato: il close riconnetterà
  }
}

/** Invia la posizione del puntatore (coordinate pagina, 0–1). */
export function notifyCursor(x: number, y: number): void {
  if (socket?.readyState !== WS_OPEN) return
  try {
    socket.send(JSON.stringify({ type: 'cursor', x, y }))
  } catch {
    // ignorato
  }
}

/** Sottoscrive il componente allo stato live della pagina corrente. */
export function useLive(): LiveState {
  return useSyncExternalStore(subscribe, getSnapshot)
}
