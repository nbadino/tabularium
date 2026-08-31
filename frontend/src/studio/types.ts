/** Tipi per lo studio di annotazione (spazio display/preview). */
import type { Pt } from '../lib/coords'
import type { TableGrid } from '../lib/types'

export type Tool = 'select' | 'rect' | 'polygon' | 'pan'

export interface DisplayBlock {
  /** id client (uuid) */
  id: string
  serverId: number | null
  label: string
  kind: 'rect' | 'polygon'
  /** punti in coordinate display (spazio preview) */
  points: Pt[]
  content: string
  orderIdx: number | null
  confirmed: boolean
  /** origine prefill (es. 'rapidocr') se blocco generato automaticamente */
  prefill?: string | null
}

export interface ViewState {
  x: number
  y: number
  k: number // scala zoom
}

/** Risultato del prefill nativo mentre viene revisionato nel pannello:
 *  vive nel database ma NON sul canvas — sull'immagine non compare nulla
 *  finché l'utente non lo verifica. */
export interface PrefillDraft {
  serverId: number
  label: string
  content: string
  confirmed: boolean
  /** Per le Table: la griglia riconosciuta dal backend — si mostra come
   *  tabella formattata nel pannello, non come riquadro sull'immagine. */
  grid?: TableGrid | null
}

export interface LivePrefillOutput {
  phase: string
  text: string
}

export interface AddBlockInput {
  kind: 'rect' | 'polygon'
  points: Pt[]
  label: string
}

export interface SaveStatus {
  state: 'idle' | 'saving' | 'saved' | 'error' | 'conflict'
  message?: string
}
