import type { Pt } from '../lib/coords'
import type { ViewState } from './types'

export interface Size {
  w: number
  h: number
}

export interface Box {
  x: number
  y: number
  w: number
  h: number
}

export const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value))

/** Mantiene sempre una porzione afferrabile della pagina dentro il tavolo. */
export function clampViewport(
  viewport: ViewState,
  container: Size,
  image: Size,
  visibleEdge = 56,
): ViewState {
  const pageW = image.w * viewport.k
  const pageH = image.h * viewport.k
  const edgeX = Math.min(visibleEdge, container.w / 2, pageW / 2)
  const edgeY = Math.min(visibleEdge, container.h / 2, pageH / 2)
  return {
    ...viewport,
    x: clamp(viewport.x, edgeX - pageW, container.w - edgeX),
    y: clamp(viewport.y, edgeY - pageH, container.h - edgeY),
  }
}

/** Limita lo spostamento senza deformare il blocco. */
export function clampDragDelta(box: Box, dx: number, dy: number, image: Size): Pt {
  return {
    x: clamp(dx, -box.x, image.w - box.x - box.w),
    y: clamp(dy, -box.y, image.h - box.y - box.h),
  }
}

/** Riporta punti trasformati nei limiti della pagina. */
export function clampPoints(points: Pt[], image: Size): Pt[] {
  return points.map((point) => ({
    x: clamp(point.x, 0, image.w),
    y: clamp(point.y, 0, image.h),
  }))
}

/** Normalizza il delta di una wheel event a pixel: i mouse (Firefox) parlano
 *  in righe o pagine, i touchpad in pixel frazionari. */
export function wheelDeltaPixels(
  evt: { deltaX: number; deltaY: number; deltaMode: number },
  viewportHeight: number,
): Pt {
  const unit = evt.deltaMode === 1 ? 16 : evt.deltaMode === 2 ? Math.max(1, viewportHeight) : 1
  return { x: evt.deltaX * unit, y: evt.deltaY * unit }
}

/** Fattore di zoom per un gesto Ctrl+rotella / pinch del touchpad: esponenziale
 *  sul delta reale (il pinch resta continuo), con clamp che limita lo scatto
 *  discreto del mouse (~±15% per notch) senza appiattire il gesto lento. */
export function wheelZoomFactor(dy: number): number {
  return clamp(Math.exp(-dy * 0.0025), 0.85, 1.18)
}
