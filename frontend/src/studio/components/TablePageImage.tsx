import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { IconFit, IconMinus, IconPlus } from '../../app/icons'
import { useI18n } from '../../i18n'

interface ImageView {
  x: number
  y: number
  scale: number
}

/** Scansione intera accanto al foglio, con gesti da visualizzatore documenti. */
export default function TablePageImage({
  src,
  pageId,
  imageSize,
}: {
  src: string
  pageId?: number | null
  imageSize?: { w: number; h: number } | null
}) {
  const { t } = useI18n()
  const viewport = useRef<HTMLDivElement>(null)
  const drag = useRef<{ pointerId: number; x: number; y: number; startX: number; startY: number } | null>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [natural, setNatural] = useState(imageSize ?? { w: 0, h: 0 })
  const [view, setView] = useState<ImageView>({ x: 0, y: 0, scale: 1 })
  const [tileView, setTileView] = useState(view)
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    if (imageSize?.w && imageSize.h) setNatural(imageSize)
  }, [imageSize])

  useEffect(() => {
    const timer = window.setTimeout(() => setTileView(view), 120)
    return () => window.clearTimeout(timer)
  }, [view])

  useEffect(() => {
    const node = viewport.current
    if (!node) return
    const resize = () => setSize({ w: node.clientWidth, h: node.clientHeight })
    resize()
    const observer = new ResizeObserver(resize)
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  const fit = useCallback(() => {
    if (!natural.w || !natural.h || !size.w || !size.h) return
    const scale = Math.min(size.w / natural.w, size.h / natural.h) * 0.96
    setView({
      scale,
      x: (size.w - natural.w * scale) / 2,
      y: (size.h - natural.h * scale) / 2,
    })
  }, [natural, size])

  useEffect(() => { fit() }, [fit])

  // Le tessere multi-risoluzione del viewer: a ogni zoom si scarica solo il
  // livello e la porzione visibile, così i caratteri restano leggibili anche
  // sulle scansioni da oltre 10 megapixel.
  const tiles = useMemo(() => {
    if (!pageId || !natural.w || !natural.h || !size.w || !size.h) return []
    const level = Math.max(0, Math.min(8, Math.round(Math.log2(1 / Math.max(tileView.scale, 0.01)))))
    const factor = 2 ** level
    const left = Math.max(0, -tileView.x / tileView.scale)
    const top = Math.max(0, -tileView.y / tileView.scale)
    const right = Math.min(natural.w, (size.w - tileView.x) / tileView.scale)
    const bottom = Math.min(natural.h, (size.h - tileView.y) / tileView.scale)
    if (right <= left || bottom <= top) return []
    const levelW = Math.ceil(natural.w / factor)
    const levelH = Math.ceil(natural.h / factor)
    const x0 = Math.max(0, Math.floor(left / factor / 512))
    const y0 = Math.max(0, Math.floor(top / factor / 512))
    const x1 = Math.min(Math.ceil(levelW / 512) - 1, Math.floor(right / factor / 512))
    const y1 = Math.min(Math.ceil(levelH / 512) - 1, Math.floor(bottom / factor / 512))
    const visible = []
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        visible.push({
          key: `${level}/${x}/${y}`,
          src: `/api/pages/${pageId}/tile/${level}/${x}/${y}`,
          x: x * 512 * factor,
          y: y * 512 * factor,
          w: Math.min(512, levelW - x * 512) * factor,
          h: Math.min(512, levelH - y * 512) * factor,
        })
      }
    }
    return visible
  }, [natural, pageId, size, tileView])

  const zoomAt = (factor: number, point?: { x: number; y: number }) => {
    const rect = viewport.current?.getBoundingClientRect()
    if (!rect) return
    const focus = point ?? { x: size.w / 2, y: size.h / 2 }
    setView((current) => {
      const scale = Math.max(0.06, Math.min(12, current.scale * factor))
      const localX = focus.x - current.x
      const localY = focus.y - current.y
      return {
        scale,
        x: focus.x - localX * (scale / current.scale),
        y: focus.y - localY * (scale / current.scale),
      }
    })
  }

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    drag.current = { pointerId: event.pointerId, x: view.x, y: view.y, startX: event.clientX, startY: event.clientY }
    setDragging(true)
  }
  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const start = drag.current
    if (!start || start.pointerId !== event.pointerId) return
    setView((current) => ({
      ...current,
      x: start.x + event.clientX - start.startX,
      y: start.y + event.clientY - start.startY,
    }))
  }
  const endDrag = () => {
    drag.current = null
    setDragging(false)
  }

  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col border border-[color:var(--color-rule)]" aria-label={t('table.pageImage')}>
      <div className="flex shrink-0 items-center gap-1 border-b border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-2 py-1">
        <span className="lbl mb-0 mr-auto">{t('table.pageImage')}</span>
        <button type="button" className="btn btn-sm" title={t('table.zoomOut')} aria-label={t('table.zoomOut')} onClick={() => zoomAt(0.8)}><IconMinus size={12} /></button>
        <span className="mono w-12 text-center text-[11px]">{Math.round(view.scale * 100)}%</span>
        <button type="button" className="btn btn-sm" title={t('table.zoomIn')} aria-label={t('table.zoomIn')} onClick={() => zoomAt(1.25)}><IconPlus size={12} /></button>
        <button type="button" className="btn btn-sm" title={t('table.fitTitle')} aria-label={t('table.fitTitle')} onClick={fit}><IconFit size={12} /></button>
      </div>
      <div
        ref={viewport}
        className={`relative min-h-0 flex-1 overflow-hidden bg-[color:var(--color-fill-2)] ${dragging ? 'cursor-grabbing' : 'cursor-grab'}`}
        style={{ touchAction: 'none' }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onWheel={(event) => {
          event.preventDefault()
          if (event.ctrlKey || event.metaKey) {
            const rect = viewport.current?.getBoundingClientRect()
            if (rect) zoomAt(Math.exp(-event.deltaY * 0.0015), { x: event.clientX - rect.left, y: event.clientY - rect.top })
          } else {
            setView((current) => ({ ...current, x: current.x - event.deltaX, y: current.y - event.deltaY }))
          }
        }}
      >
        <img
            src={src}
            alt={t('table.pageImage')}
            draggable={false}
            onLoad={(event) => {
              if (!imageSize?.w) setNatural({ w: event.currentTarget.naturalWidth, h: event.currentTarget.naturalHeight })
            }}
            className="pointer-events-none absolute left-0 top-0 max-w-none select-none shadow-md"
            style={{
              width: natural.w || 'auto',
              height: natural.h || 'auto',
              transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})`,
              transformOrigin: 'top left',
            }}
        />
        {tiles.map((tile) => (
          <img
            key={tile.key}
            src={tile.src}
            alt=""
            draggable={false}
            className="pointer-events-none absolute left-0 top-0 max-w-none select-none"
            style={{
              width: tile.w,
              height: tile.h,
              transform: `translate(${view.x + tile.x * view.scale}px, ${view.y + tile.y * view.scale}px) scale(${view.scale})`,
              transformOrigin: 'top left',
            }}
          />
        ))}
      </div>
    </section>
  )
}
