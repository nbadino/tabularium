/** Tipi per lo studio di annotazione (spazio display/preview). */
import type { Pt } from '../lib/coords'

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

export interface AddBlockInput {
  kind: 'rect' | 'polygon'
  points: Pt[]
  label: string
}

export interface SaveStatus {
  state: 'idle' | 'saving' | 'saved' | 'error'
  message?: string
}