import type { EvalReport } from './types'

/**
 * The three numbers that answer "is the model good on my pages?", as plain
 * percentages: characters wrong in the text, regions found, tables with the
 * right structure. The full metrics stay available under "Details".
 */
export type HeadlineItem = { key: 'text' | 'regions' | 'tables'; value: number }

const round1 = (v: number) => Math.round(v * 10) / 10

export function evaluationHeadline(a: EvalReport['aggregates']): HeadlineItem[] {
  const out: HeadlineItem[] = []
  if (a.text?.n && a.text.mean_cer != null) out.push({ key: 'text', value: round1(a.text.mean_cer * 100) })
  if (a.layout?.recall != null) out.push({ key: 'regions', value: round1(a.layout.recall * 100) })
  if (a.tables?.n && a.tables.structure_ok_pct != null) out.push({ key: 'tables', value: round1(a.tables.structure_ok_pct) })
  return out
}
