import { describe, expect, it } from 'vitest'
import { clampDragDelta, clampPoints, clampViewport } from './canvasGeometry'

describe('geometria interattiva del canvas', () => {
  it('lascia sempre un bordo della pagina visibile durante il pan', () => {
    expect(clampViewport({ x: -900, y: 500, k: 1 }, { w: 500, h: 400 }, { w: 800, h: 600 }))
      .toEqual({ x: -744, y: 344, k: 1 })
  })

  it('impedisce di trascinare un blocco fuori dalla pagina', () => {
    expect(clampDragDelta({ x: 20, y: 30, w: 100, h: 80 }, -50, 600, { w: 400, h: 500 }))
      .toEqual({ x: -20, y: 390 })
  })

  it('limita i punti prodotti dal ridimensionamento', () => {
    expect(clampPoints([{ x: -3, y: 10 }, { x: 120, y: 90 }], { w: 100, h: 80 }))
      .toEqual([{ x: 0, y: 10 }, { x: 100, y: 80 }])
  })
})
