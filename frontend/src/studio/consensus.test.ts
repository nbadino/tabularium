import { describe, expect, it } from 'vitest'
import { byDisagreement, charDiff, mineFor, nextOpen, overlayRects, readingDistance, verdictOf, verdictColor, STRUCTURE_COLOR, scoreBand, BAND_COLOR, RESOLVED_COLOR, type ConsensusCluster, type MineItem, type PageConsensus } from './consensus'

function cluster(id: number, score: number, members: Record<string, number | null>, bbox: [number, number, number, number]): ConsensusCluster {
  return {
    id, members, label: 'Text', labels: {}, bbox, text: score, layout: 0, label_agree: true,
    parts: Object.fromEntries(Object.entries(members).filter(([, v]) => v !== null).map(([k, v]) => [k, [v as number]])),
    split: false,
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

  it("links every piece of a split block to its cluster in a model's view", () => {
    const split: PageConsensus = {
      ...page,
      comparison: { ...page.comparison!, clusters: [{ ...page.comparison!.clusters[0], parts: { p1: [0], p2: [0, 1] }, split: true }] },
    }
    expect(overlayRects(split, 'p2', 1).map((r) => r.cluster)).toEqual([0, 0])
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

describe('settling disagreements', () => {
  const mine = (id: string, box: MineItem['box']): MineItem => ({ kind: 'block', id, label: 'Text', content: '', box })

  it("finds the user's block for the same area or a piece of it, not a column that holds it", () => {
    const area = page.comparison!.clusters[0]
    const found = mineFor(area, [
      mine('same', [10, 12, 108, 60]),
      mine('piece', [10, 10, 60, 30]),
      mine('column', [0, 0, 400, 900]),
      mine('elsewhere', [0, 300, 50, 320]),
    ])
    expect(found.map((item) => item.id)).toEqual(['same', 'piece'])
  })

  it('steps through the open ones, most disputed first, skipping the settled and the agreed', () => {
    const clusters = [
      cluster(0, 0.02, { p1: 0, p2: 0 }, [0, 0, 10, 10]),
      cluster(1, 0.9, { p1: 1, p2: 1 }, [0, 20, 10, 30]),
      cluster(2, 0.5, { p1: 2, p2: 2 }, [0, 40, 10, 50]),
      cluster(3, 0.2, { p1: 3, p2: 3 }, [0, 60, 10, 70]),
    ]
    expect(nextOpen(clusters, new Set(), null)).toBe(1)
    expect(nextOpen(clusters, new Set(), 1)).toBe(2)
    expect(nextOpen(clusters, new Set([3]), 2)).toBe(1)
    expect(nextOpen(clusters, new Set([1, 2, 3]), 3)).toBeNull()
  })

  it('paints a settled block violet and lets clicks through where the user has a block', () => {
    const rects = overlayRects(page, 'disagreement', 1, { resolved: new Set([1]), covered: new Set([0]) })
    expect(rects[1].color).toBe(RESOLVED_COLOR)
    expect(rects.map((r) => r.listening)).toEqual([false, true])
  })
})

describe('verdicts', () => {
  it('calls a class or segmentation change a structure choice, not an error', () => {
    const relabelled = { ...cluster(0, 0.5, { p1: 0, p2: 0 }, [0, 0, 10, 10]), text: 0, label_agree: false }
    expect(verdictOf(relabelled)).toMatchObject({ kind: 'structure', text: 'same', labelsDiffer: true })
    expect(verdictColor(relabelled, false)).toBe(STRUCTURE_COLOR)
    const split = { ...cluster(1, 0.3, { p1: 0, p2: 0 }, [0, 0, 10, 10]), text: 0, split: true }
    expect(verdictOf(split).kind).toBe('structure')
  })

  it('keeps red for different words and for what one model missed', () => {
    const misread = { ...cluster(0, 0.4, { p1: 0, p2: 0 }, [0, 0, 10, 10]), text: 0.4 }
    expect(verdictOf(misread).kind).toBe('text')
    expect(verdictColor(misread, false)).toBe(BAND_COLOR.disagree)
    expect(verdictOf(page.comparison!.clusters[1]).kind).toBe('missing')
    expect(verdictColor(misread, true)).toBe(RESOLVED_COLOR)
  })

  it('steps through words before structure', () => {
    const structure = { ...cluster(0, 0.6, { p1: 0, p2: 0 }, [0, 0, 10, 10]), text: 0, label_agree: false }
    const words = { ...cluster(1, 0.2, { p1: 1, p2: 1 }, [0, 20, 10, 30]), text: 0.2 }
    expect(nextOpen([structure, words], new Set(), null)).toBe(1)
  })

  it('measures how far two readings are apart', () => {
    expect(readingDistance('Arrivi  nel porto', 'Arrivi nel porto')).toBe(0)
    expect(readingDistance('Rossi', 'Russo')).toBeCloseTo(0.4)
  })
})
