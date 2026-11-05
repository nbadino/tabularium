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
  /** Every block of each model in the cluster, in reading order (several = split). */
  parts: Record<string, number[]>
  /** The models read this area but segmented it differently. */
  split: boolean
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

/** The models' readings laid on the user's own table (`POST /blocks/{id}/consensus`). */
export interface GridConsensus {
  run_id: number | null
  models: { key: string; name: string }[]
  cells: { r: number; c: number; values: Record<string, string | null>; score: number | null }[]
  extra_rows: { model: string; index: number; texts: string[] }[]
}

/** `POST …/consensus-runs/{id}/adopt`: a model's reading written as the pages' drafts. */
export interface AdoptResult {
  written: { page_id: number; blocks: number; annotation_revision: number }[]
  /** `human_work`: the page has blocks a person drew or verified (`blocks` of them). */
  skipped: { page_id: number; reason: 'human_work' | 'no_result'; blocks: number }[]
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

/** A disagreement the user has settled: the same violet as a corrected table cell. */
export const RESOLVED_COLOR = '#7c3aed'
/** Same text, another class or segmentation: a choice to make, not a misreading. */
export const STRUCTURE_COLOR = '#5f6b7a'

/**
 * What kind of disagreement a cluster is, which decides its colour and the words used.
 *
 * - `text`: the models read different words; the one kind that is a likely error.
 * - `missing`: some model found nothing there.
 * - `structure`: same words, but another class, segmentation or position: a choice.
 * - `agree`: nothing to look at.
 */
export type VerdictKind = 'text' | 'missing' | 'structure' | 'agree'

export type Degree = 'same' | 'close' | 'partly' | 'different'

export interface Verdict {
  kind: VerdictKind
  text: Degree
  layout: Degree
  labelsDiffer: boolean
  split: boolean
}

/** Text: identical below 2 %, a character here and there below 10 %, then the score bands. */
export function textDegree(score: number): Degree {
  if (score < 0.02) return 'same'
  if (score < 0.1) return 'close'
  if (score < 0.35) return 'partly'
  return 'different'
}

/** Position: 1 − IoU; below 0.1 the boxes are the same area drawn a little differently. */
export function layoutDegree(score: number): Degree {
  if (score < 0.1) return 'same'
  if (score < 0.35) return 'close'
  return 'different'
}

export function verdictOf(cluster: ConsensusCluster): Verdict {
  const text = textDegree(cluster.text)
  const layout = layoutDegree(cluster.layout)
  const labelsDiffer = !cluster.label_agree && !cluster.split
  const kind: VerdictKind = cluster.missing.length > 0 ? 'missing'
    : text === 'partly' || text === 'different' ? 'text'
      : cluster.split || labelsDiffer || layout === 'different' ? 'structure'
        : 'agree'
  return { kind, text, layout, labelsDiffer, split: cluster.split }
}

/** The colour of a cluster on the canvas: the text's band, grey for a choice, violet once settled. */
export function verdictColor(cluster: ConsensusCluster, resolved: boolean): string {
  if (resolved) return RESOLVED_COLOR
  const verdict = verdictOf(cluster)
  if (verdict.kind === 'missing') return BAND_COLOR.disagree
  if (verdict.kind === 'text') return BAND_COLOR[scoreBand(cluster.text)]
  if (verdict.kind === 'structure') return STRUCTURE_COLOR
  return BAND_COLOR.agree
}

const KIND_ORDER: Record<VerdictKind, number> = { text: 0, missing: 1, structure: 2, agree: 3 }

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
  /** False over an area the user already has a block in: the click goes to that block. */
  listening: boolean
  /** Short words drawn on the page at the rectangle's corner (empty: none). */
  tag: string
}

/** 'disagreement' paints every cluster by score; a model key draws that model's own boxes. */
export type ConsensusView = 'disagreement' | string

export function overlayRects(
  page: PageConsensus,
  view: ConsensusView,
  ratio: number,
  {
    resolved = new Set<number>(),
    covered = new Set<number>(),
    tagOf = () => '',
  }: { resolved?: Set<number>; covered?: Set<number>; tagOf?: (cluster: ConsensusCluster) => string } = {},
): OverlayRect[] {
  const clusters = page.comparison?.clusters ?? []
  const box = (x1: number, y1: number, x2: number, y2: number): Pt[] => [
    { x: x1 * ratio, y: y1 * ratio },
    { x: x2 * ratio, y: y2 * ratio },
  ]
  const color = (cluster: ConsensusCluster) => verdictColor(cluster, resolved.has(cluster.id))
  if (view === 'disagreement') {
    return clusters.map((cluster) => ({
      cluster: cluster.id,
      points: box(...cluster.bbox),
      color: color(cluster),
      dashed: cluster.missing.length > 0,
      listening: !covered.has(cluster.id),
      tag: tagOf(cluster),
    }))
  }
  const blocks = page.outputs[view] ?? []
  return blocks.flatMap((block, index) => {
    const cluster = clusters.find((c) => (c.parts?.[view] ?? []).includes(index))
    if (!cluster) return []
    const [x1, y1, x2, y2] = boxOf(block.points)
    return [{
      cluster: cluster.id,
      points: box(x1, y1, x2, y2),
      color: color(cluster),
      dashed: false,
      listening: !covered.has(cluster.id),
      tag: '',
    }]
  })
}

export type Box = [number, number, number, number]

/** Bounding box of a block's points, in the units of the points. */
export function boxOf(points: [number, number][] | number[][]): Box {
  const xs = points.map((p) => p[0])
  const ys = points.map((p) => p[1])
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]
}

const area = (b: Box) => Math.max(0, b[2] - b[0]) * Math.max(0, b[3] - b[1])

function overlap(a: Box, b: Box): number {
  return area([Math.max(a[0], b[0]), Math.max(a[1], b[1]), Math.min(a[2], b[2]), Math.min(a[3], b[3])])
}

/** One of the user's own blocks, or a prefill draft not yet verified, in page pixels. */
export interface MineItem {
  kind: 'block' | 'draft'
  /** Client id of a canvas block; server id of a draft. */
  id: string
  label: string
  content: string
  box: Box
}

// The backend's thresholds (domain/consensus.py): the same area at IoU 0.5, a piece
// of it when 70 % of the user's block lies inside. A large block that merely
// contains the cluster (a whole Column) is not the user's reading of it.
const MATCH_IOU = 0.5
const PART_INSIDE = 0.7

/** The user's blocks that read the same area as the cluster. */
export function mineFor(cluster: ConsensusCluster, mine: MineItem[]): MineItem[] {
  return mine.filter((item) => {
    const shared = overlap(item.box, cluster.bbox)
    if (shared <= 0) return false
    const union = area(item.box) + area(cluster.bbox) - shared
    return shared / union >= MATCH_IOU || (area(item.box) > 0 && shared / area(item.box) >= PART_INSIDE)
  })
}

/** A model's blocks in the cluster, in reading order. */
export function partsOf(page: PageConsensus, cluster: ConsensusCluster, key: string): ConsensusBlock[] {
  return (cluster.parts?.[key] ?? []).map((index) => page.outputs[key]?.[index]).filter((b): b is ConsensusBlock => b != null)
}

/** A table, the models' or the user's, is corrected cell by cell in the table editor, not replaced from here. */
export function isTableCluster(page: PageConsensus, cluster: ConsensusCluster, mine: MineItem[] = []): boolean {
  return cluster.table != null
    || mine.some((item) => item.label === 'Table')
    || Object.keys(cluster.parts ?? {}).some((key) => partsOf(page, cluster, key).some((b) => b.label === 'Table'))
}

/** Disputed and not yet settled by the user. */
export function isOpen(cluster: ConsensusCluster, resolved: Set<number>): boolean {
  return verdictOf(cluster).kind !== 'agree' && !resolved.has(cluster.id)
}

/** What to look at first: different text, then what a model missed, then the choices; by score within each. */
export function byPriority(clusters: ConsensusCluster[]): ConsensusCluster[] {
  return [...clusters].sort((a, b) =>
    KIND_ORDER[verdictOf(a).kind] - KIND_ORDER[verdictOf(b).kind]
    || b.score - a.score || a.bbox[1] - b.bbox[1] || a.bbox[0] - b.bbox[0])
}

/** The open clusters still to look at, by kind. */
export function openCounts(clusters: ConsensusCluster[], resolved: Set<number>): Record<Exclude<VerdictKind, 'agree'>, number> {
  const counts = { text: 0, missing: 0, structure: 0 }
  for (const cluster of clusters) {
    const kind = verdictOf(cluster).kind
    if (kind !== 'agree' && !resolved.has(cluster.id)) counts[kind] += 1
  }
  return counts
}

/** The next open cluster after `current`, in priority order, wrapping round; null when none is left. */
export function nextOpen(clusters: ConsensusCluster[], resolved: Set<number>, current: number | null): number | null {
  const order = byPriority(clusters)
  const at = order.findIndex((c) => c.id === current)
  for (let step = 1; step <= order.length; step++) {
    const cluster = order[(at + step + order.length) % order.length]
    if (cluster.id !== current && isOpen(cluster, resolved)) return cluster.id
  }
  return null
}

/** Most disputed first; ties in reading order. */
export function byDisagreement(clusters: ConsensusCluster[]): ConsensusCluster[] {
  return [...clusters].sort((a, b) => b.score - a.score || a.bbox[1] - b.bbox[1] || a.bbox[0] - b.bbox[0])
}

/** Share of characters that differ between two readings, whitespace collapsed (0 = same). */
export function readingDistance(a: string, b: string): number {
  const left = a.replace(/\s+/g, ' ').trim()
  const right = b.replace(/\s+/g, ' ').trim()
  if (left === right) return 0
  let removed = 0
  let added = 0
  for (const part of charDiff(left, right)) {
    if (part.kind === 'del') removed += part.text.length
    if (part.kind === 'ins') added += part.text.length
  }
  return Math.max(removed, added) / Math.max(left.length, right.length)
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
