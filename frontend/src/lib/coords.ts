/** Conversione coordinate tra spazio di lavoro (preview) e pixel pagina.
 *
 * Le annotazioni vengono salvate in PIXEL della pagina sorgente (pageSize);
 * in sessione di editing si lavora nello spazio della preview (imageSize,
 * che preserva l'aspect ratio). ratio = imageW / pageW (stesso per y).
 */
import { t } from '../i18n'

export interface Pt {
  x: number
  y: number
}

export interface PixelSize {
  w: number
  h: number
}

export function scaleRatio(imageSize: PixelSize, pageSize: PixelSize): number {
  if (pageSize.w <= 0) return 1
  return imageSize.w / pageSize.w
}

export function toDisplay(pt: Pt, ratio: number): Pt {
  return { x: pt.x * ratio, y: pt.y * ratio }
}

export function toPage(pt: Pt, ratio: number): Pt {
  return { x: pt.x / ratio, y: pt.y / ratio }
}

export function bboxPoints(points: Pt[]): { x: number; y: number; w: number; h: number } {
  let x1 = Infinity
  let y1 = Infinity
  let x2 = -Infinity
  let y2 = -Infinity
  for (const p of points) {
    x1 = Math.min(x1, p.x)
    y1 = Math.min(y1, p.y)
    x2 = Math.max(x2, p.x)
    y2 = Math.max(y2, p.y)
  }
  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 }
}

/** Risolve le dimensioni naturali di un'immagine via HTTP. */
export function loadImageSize(url: string): Promise<PixelSize> {
  return new Promise((resolve, reject) => {
    const im = new Image()
    im.onload = () => resolve({ w: im.naturalWidth, h: im.naturalHeight })
    im.onerror = () => reject(new Error(`${t('errors.imageLoad', { url })}`))
    im.src = url
  })
}