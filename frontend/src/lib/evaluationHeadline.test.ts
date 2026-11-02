import { describe, expect, it } from 'vitest'
import { evaluationHeadline } from './evaluationHeadline'

const agg = (over: Record<string, unknown> = {}) => ({
  layout: { recall: 0.94, precision: 0.9 },
  text: { n: 12, mean_cer: 0.021, mean_wer: 0.08 },
  tables: { n: 3, structure_ok_pct: 81.2, mean_cell_cer: 0.05 },
  ...over,
}) as never

describe('evaluationHeadline', () => {
  it('turns the three main metrics into plain percentages', () => {
    expect(evaluationHeadline(agg())).toEqual([
      { key: 'text', value: 2.1 },
      { key: 'regions', value: 94 },
      { key: 'tables', value: 81.2 },
    ])
  })

  it('leaves out what was not measured', () => {
    expect(evaluationHeadline(agg({ text: { n: 0, mean_cer: null, mean_wer: null }, tables: { n: 0, structure_ok_pct: null, mean_cell_cer: null } })))
      .toEqual([{ key: 'regions', value: 94 }])
  })
})
