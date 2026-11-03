import { describe, expect, it } from 'vitest'
import { byDisagreement, charDiff, overlayRects, scoreBand, BAND_COLOR, type ConsensusCluster, type PageConsensus } from './consensus'

function cluster(id: number, score: number, members: Record<string, number | null>, bbox: [number, number, number, number]): ConsensusCluster {
  return {
    id, members, label: 'Text', labels: {}, bbox, text: score, layout: 0, label_agree: true,
    missing: Object.entries(members).filter(([, v]) => v === null).map(([k]) => k), score, table: null,
  }
}

const page: PageConsensus = {
  run_id: 1,
  models: [],
  outputs: {
    p1: [{ label: 'Text', points: [[10, 10], [110, 60]], content: 'a' }],
    p2: [
      { label: 'Text', points: [[12, 10], [110, 60]], content: 'b' },
      { label: 'Note', points: [[0, 200], [50, 220]], content: 'n' },
    ],
  },
  comparison: {
    models: ['p1', 'p2'],
    agreement: 0.5,
    clusters: [
      cluster(0, 0.02, { p1: 0, p2: 0 }, [10, 10, 110, 60]),
      cluster(1, 1, { p1: null, p2: 1 }, [0, 200, 50, 220]),
    ],
  },
}

describe('consensus helpers', () => {
  it('maps scores to agree / partly / disagree', () => {
    expect(scoreBand(0)).toBe('agree')
    expect(scoreBand(0.2)).toBe('partly')
    expect(scoreBand(0.4)).toBe('disagree')
  })

  it('draws clusters scaled to the preview, dashed when a model missed the block', () => {
    const rects = overlayRects(page, 'disagreement', 0.5)
    expect(rects).toHaveLength(2)
    expect(rects[0].points).toEqual([{ x: 5, y: 5 }, { x: 55, y: 30 }])
    expect(rects[0].color).toBe(BAND_COLOR.agree)
    expect(rects[1].dashed).toBe(true)
    expect(rects[1].color).toBe(BAND_COLOR.disagree)
  })

  it("draws one model's own boxes, each linked to its cluster", () => {
    const rects = overlayRects(page, 'p2', 1)
    expect(rects.map((r) => r.cluster)).toEqual([0, 1])
    expect(rects[0].points[0]).toEqual({ x: 12, y: 10 })
    expect(overlayRects(page, 'p1', 1)).toHaveLength(1)
  })

  it('lists the most disputed block first', () => {
    expect(byDisagreement(page.comparison!.clusters).map((c) => c.id)).toEqual([1, 0])
  })

  it('diffs two readings character by character', () => {
    expect(charDiff('Rossi', 'Russo')).toEqual([
      { text: 'R', kind: 'same' },
      { text: 'o', kind: 'del' },
      { text: 'u', kind: 'ins' },
      { text: 'ss', kind: 'same' },
      { text: 'i', kind: 'del' },
      { text: 'o', kind: 'ins' },
    ])
    expect(charDiff('', 'x')).toEqual([{ text: 'x', kind: 'ins' }])
    expect(charDiff('same', 'same')).toEqual([{ text: 'same', kind: 'same' }])
  })
})
