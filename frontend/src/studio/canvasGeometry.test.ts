import { describe, expect, it } from 'vitest'
import {
  clampDragDelta,
  clampPoints,
  clampViewport,
  wheelDeltaPixels,
  wheelZoomFactor,
} from './canvasGeometry'

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

  it('normalizza il delta della rotella a pixel per ogni deltaMode', () => {
    // Touchpad: pixel frazionari, nessuna conversione.
    expect(wheelDeltaPixels({ deltaX: -2.5, deltaY: 4, deltaMode: 0 }, 400))
      .toEqual({ x: -2.5, y: 4 })
    // Mouse Firefox: righe → ×16px.
    expect(wheelDeltaPixels({ deltaX: 0, deltaY: 3, deltaMode: 1 }, 400))
      .toEqual({ x: 0, y: 48 })
    // Pagina intera → altezza viewport.
    expect(wheelDeltaPixels({ deltaX: 0, deltaY: 1, deltaMode: 2 }, 400))
      .toEqual({ x: 0, y: 400 })
  })

  it('lo zoom da rotella è continuo col pinch e moderato sullo scatto del mouse', () => {
    // Pinch lento (pochi pixel): fattore prossimo a 1, gesto morbido.
    expect(wheelZoomFactor(2)).toBeGreaterThan(0.99)
    expect(wheelZoomFactor(-2)).toBeLessThan(1.01)
    // Notch del mouse (100px): limitato a ~±15%, mai un salto violento.
    expect(wheelZoomFactor(-100)).toBeCloseTo(1.18)
    expect(wheelZoomFactor(100)).toBeCloseTo(0.85)
    // Monotono: più delta (in entrambe le direzioni), più effetto.
    expect(wheelZoomFactor(-40)).toBeGreaterThan(wheelZoomFactor(-10))
    expect(wheelZoomFactor(40)).toBeLessThan(wheelZoomFactor(10))
  })
})
