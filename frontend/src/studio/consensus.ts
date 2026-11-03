/**
 * Consensus: where two or more models disagree on a page.
 *
 * The backend (`GET /pages/{id}/consensus`) matches the models' blocks and scores
 * them (`backend/app/domain/consensus.py`); this module only turns those scores into
 * what the Studio draws: a colour band, the rectangles of the overlay, and a
 * character diff for the side-by-side view.
 */
import type { Pt } from '../lib/coords'

export interface ConsensusModel {
  key: string
  profile_id: number
  name: string
  adapter_id: string
  model_name: string
  state: 'queued' | 'running' | 'finished' | 'failed' | 'cancelled' | 'missing'
  error: string | null
}

export interface ConsensusBlock {
  label: string
  points: [number, number][]
  content: string
  grid?: { rows: number; cols: number; cells: { r: number; c: number; text: string }[] } | null
}

export interface ConsensusCell {
  r: number
  c: number
  rowspan: number
  colspan: number
  values: Record<string, string | null>
  score: number
}

export interface ConsensusTable {
  reference: string
  rows: number
  cols: number
  cells: ConsensusCell[]
  extra_rows: { model: string; index: number; texts: string[] }[]
  extra_cols: { model: string; index: number }[]
  score: number
}

export interface ConsensusCluster {
  id: number
  members: Record<string, number | null>
  label: string
  labels: Record<string, string>
  bbox: [number, number, number, number]
  text: number
  layout: number
  label_agree: boolean
  missing: string[]
  score: number
  table: ConsensusTable | null
}

export interface PageConsensus {
  run_id: number | null
  models: ConsensusModel[]
  outputs: Record<string, ConsensusBlock[]>
  comparison: { models: string[]; agreement: number; clusters: ConsensusCluster[] } | null
}

export type ScoreBand = 'agree' | 'partly' | 'disagree'

/** Below 0.1 the readings differ by a character or two in ten; above 0.35 they are different readings. */
export function scoreBand(score: number): ScoreBand {
  if (score < 0.1) return 'agree'
  if (score < 0.35) return 'partly'
  return 'disagree'
}

/** Konva cannot read CSS variables: the theme's ok/warn/signal rules, as values. */
export const BAND_COLOR: Record<ScoreBand, string> = {
  agree: '#14a05c',
  partly: '#e8a100',
  disagree: '#a3000e',
}

/** Utility classes for the same bands in the DOM (table heatmap, list). */
export const BAND_CLASS: Record<ScoreBand, string> = {
  agree: 'bg-[color:var(--color-ok-wash)] text-[color:var(--color-ok)]',
  partly: 'bg-[color:var(--color-warn-wash)] text-[color:var(--color-warn)]',
  disagree: 'bg-[color:var(--color-sig-wash)] text-[color:var(--color-sig-text)]',
}

/** `#rrggbb` with an alpha channel, for Konva fills. */
export function withAlpha(hex: string, alpha: number): string {
  const value = Number.parseInt(hex.slice(1), 16)
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${alpha})`
}

export interface OverlayRect {
  /** Cluster id: clicking any rectangle selects the cluster it belongs to. */
  cluster: number
  points: Pt[]
  color: string
  dashed: boolean
}

/** 'disagreement' paints every cluster by score; a model key draws that model's own boxes. */
export type ConsensusView = 'disagreement' | string

export function overlayRects(page: PageConsensus, view: ConsensusView, ratio: number): OverlayRect[] {
  const clusters = page.comparison?.clusters ?? []
  const box = (x1: number, y1: number, x2: number, y2: number): Pt[] => [
    { x: x1 * ratio, y: y1 * ratio },
    { x: x2 * ratio, y: y2 * ratio },
  ]
  if (view === 'disagreement') {
    return clusters.map((cluster) => ({
      cluster: cluster.id,
      points: box(...cluster.bbox),
      color: BAND_COLOR[scoreBand(cluster.score)],
      dashed: cluster.missing.length > 0,
    }))
  }
  const blocks = page.outputs[view] ?? []
  return blocks.flatMap((block, index) => {
    const cluster = clusters.find((c) => c.members[view] === index)
    if (!cluster) return []
    const [[x1, y1], [x2, y2]] = [block.points[0], block.points[block.points.length - 1]]
    return [{
      cluster: cluster.id,
      points: box(Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2)),
      color: BAND_COLOR[scoreBand(cluster.score)],
      dashed: false,
    }]
  })
}

/** Most disputed first; ties in reading order. */
export function byDisagreement(clusters: ConsensusCluster[]): ConsensusCluster[] {
  return [...clusters].sort((a, b) => b.score - a.score || a.bbox[1] - b.bbox[1] || a.bbox[0] - b.bbox[0])
}

export interface DiffPart {
  text: string
  kind: 'same' | 'del' | 'ins'
}

// Past this many cells the LCS table costs more than the diff is worth: the two
// texts are shown whole, one deleted and one inserted.
const DIFF_LIMIT = 4_000_000

/** Character diff from `a` to `b` (longest common subsequence). */
export function charDiff(a: string, b: string): DiffPart[] {
  if (a === b) return a ? [{ text: a, kind: 'same' }] : []
  if (a.length * b.length > DIFF_LIMIT) {
    return [
      ...(a ? [{ text: a, kind: 'del' as const }] : []),
      ...(b ? [{ text: b, kind: 'ins' as const }] : []),
    ]
  }
  const n = a.length
  const m = b.length
  const lcs = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1])
    }
  }
  const parts: DiffPart[] = []
  const push = (text: string, kind: DiffPart['kind']) => {
    const last = parts[parts.length - 1]
    if (last && last.kind === kind) last.text += text
    else parts.push({ text, kind })
  }
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      push(a[i], 'same')
      i++
      j++
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      push(a[i], 'del')
      i++
    } else {
      push(b[j], 'ins')
      j++
    }
  }
  if (i < n) push(a.slice(i), 'del')
  if (j < m) push(b.slice(j), 'ins')
  return parts
}
