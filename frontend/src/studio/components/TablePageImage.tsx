import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { IconFit, IconMinus, IconPlus, IconScan, IconTable } from '../../app/icons'
import { useI18n } from '../../i18n'
import { API_BASE } from '../../lib/endpoints'

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
  mode = 'crop',
  onModeChange,
}: {
  src: string
  pageId?: number | null
  imageSize?: { w: number; h: number } | null
  mode?: 'crop' | 'page'
  onModeChange?: (mode: 'crop' | 'page') => void
}) {
  const { t } = useI18n()
  const viewport = useRef<HTMLDivElement>(null)
  const drag = useRef<{ pointerId: number; x: number; y: number; startX: number; startY: number } | null>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [natural, setNatural] = useState(imageSize ?? { w: 0, h: 0 })
  const [view, setView] = useState<ImageView>({ x: 0, y: 0, scale: 1 })
  const [tileView, setTileView] = useState(view)
  const [dragging, setDragging] = useState(false)

  // Il chiamante passa spesso un oggetto nuovo a ogni render: conta il valore.
  useEffect(() => {
    if (imageSize?.w && imageSize.h) setNatural({ w: imageSize.w, h: imageSize.h })
  }, [imageSize?.w, imageSize?.h])

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

  // Si adatta una volta per immagine, come il canvas (v. useViewport): i
  // re-render dello Studio (polling della sessione ogni 1,2 s) e i resize non
  // devono buttare via lo zoom dell'annotatore.
  const fittedFor = useRef('')
  useEffect(() => {
    const key = `${src}:${natural.w}x${natural.h}`
    if (!natural.w || !natural.h || !size.w || !size.h || fittedFor.current === key) return
    fittedFor.current = key
    fit()
  }, [fit, natural.w, natural.h, size.w, size.h, src])

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
          src: `${API_BASE}/pages/${pageId}/tile/${level}/${x}/${y}`,
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
  const zoomAtRef = useRef(zoomAt)
  zoomAtRef.current = zoomAt

  // React ascolta wheel in modo passivo: il preventDefault dell'handler React
  // non basta a fermare lo zoom pagina di Chrome. Questi listener nativi sono
  // non passivi e trasformano il pinch in zoom della sola scansione.
  useEffect(() => {
    const node = viewport.current
    if (!node) return
    let pinchDistance = 0
    let pinchActive = false
    let gestureScale = 1
    const overScan = (x: number, y: number) => {
      const rect = node.getBoundingClientRect()
      return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom
    }
    const pointInScan = (event: MouseEvent) => overScan(event.clientX, event.clientY)
    const onWheel = (event: WheelEvent) => {
      if (!pointInScan(event)) return
      event.preventDefault()
      event.stopPropagation()
      if (event.ctrlKey || event.metaKey) {
        const rect = node.getBoundingClientRect()
        zoomAtRef.current(Math.exp(-event.deltaY * 0.0015), {
          x: event.clientX - rect.left,
          y: event.clientY - rect.top,
        })
      } else {
        setView((current) => ({ ...current, x: current.x - event.deltaX, y: current.y - event.deltaY }))
      }
    }
    const distance = (touches: TouchList) => Math.max(1, Math.hypot(
      touches[1].clientX - touches[0].clientX,
      touches[1].clientY - touches[0].clientY,
    ))
    const onTouchStart = (event: TouchEvent) => {
      if (event.touches.length !== 2 || !overScan(event.touches[0].clientX, event.touches[0].clientY)) return
      event.preventDefault()
      event.stopPropagation()
      pinchActive = true
      pinchDistance = distance(event.touches)
    }
    const onTouchMove = (event: TouchEvent) => {
      if (!pinchActive || event.touches.length !== 2) return
      event.preventDefault()
      event.stopPropagation()
      const rect = node.getBoundingClientRect()
      const nextDistance = distance(event.touches)
      const center = {
        x: (event.touches[0].clientX + event.touches[1].clientX) / 2 - rect.left,
        y: (event.touches[0].clientY + event.touches[1].clientY) / 2 - rect.top,
      }
      zoomAtRef.current(nextDistance / pinchDistance, center)
      pinchDistance = nextDistance
    }
    const onTouchEnd = (event: TouchEvent) => {
      if (event.touches.length < 2) pinchActive = false
    }
    const onGestureStart = (event: Event) => {
      const point = event as Event & { clientX?: number; clientY?: number; scale?: number }
      if (typeof point.scale !== 'number' || typeof point.clientX !== 'number' || typeof point.clientY !== 'number' || !overScan(point.clientX, point.clientY)) return
      event.preventDefault()
      event.stopPropagation()
      gestureScale = 1
    }
    const onGestureChange = (event: Event) => {
      const point = event as Event & { clientX?: number; clientY?: number; scale?: number }
      if (typeof point.scale !== 'number' || typeof point.clientX !== 'number' || typeof point.clientY !== 'number' || !overScan(point.clientX, point.clientY)) return
      event.preventDefault()
      event.stopPropagation()
      const rect = node.getBoundingClientRect()
      zoomAtRef.current(point.scale / Math.max(gestureScale, 0.01), { x: point.clientX - rect.left, y: point.clientY - rect.top })
      gestureScale = point.scale
    }
    window.addEventListener('wheel', onWheel, { capture: true, passive: false })
    window.addEventListener('touchstart', onTouchStart, { capture: true, passive: false })
    window.addEventListener('touchmove', onTouchMove, { capture: true, passive: false })
    window.addEventListener('touchend', onTouchEnd, { capture: true, passive: false })
    window.addEventListener('touchcancel', onTouchEnd, { capture: true, passive: false })
    window.addEventListener('gesturestart', onGestureStart, { capture: true, passive: false })
    window.addEventListener('gesturechange', onGestureChange, { capture: true, passive: false })
    return () => {
      window.removeEventListener('wheel', onWheel, true)
      window.removeEventListener('touchstart', onTouchStart, true)
      window.removeEventListener('touchmove', onTouchMove, true)
      window.removeEventListener('touchend', onTouchEnd, true)
      window.removeEventListener('touchcancel', onTouchEnd, true)
      window.removeEventListener('gesturestart', onGestureStart, true)
      window.removeEventListener('gesturechange', onGestureChange, true)
    }
  }, [])

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
        {onModeChange && (
          <div role="group" aria-label={t('table.imageMode')} className="mr-auto inline-flex overflow-hidden rounded-[var(--radius-sm)] border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sheet)]">
            <button
              type="button"
              aria-pressed={mode === 'crop'}
              aria-label={t('table.tableCrop')}
              title={t('table.tableCrop')}
              onClick={() => onModeChange('crop')}
              className={`flex h-6 w-7 items-center justify-center border-r border-[color:var(--color-rule)] ${
                mode === 'crop' ? 'bg-[color:var(--color-ink)] text-white' : 'text-[color:var(--color-ink-2)] hover:bg-[color:var(--color-fill-2)]'
              }`}
            >
              <IconTable size={12} />
            </button>
            <button
              type="button"
              aria-pressed={mode === 'page'}
              aria-label={t('table.wholePage')}
              title={t('table.wholePage')}
              onClick={() => onModeChange('page')}
              className={`flex h-6 w-7 items-center justify-center ${
                mode === 'page' ? 'bg-[color:var(--color-ink)] text-white' : 'text-[color:var(--color-ink-2)] hover:bg-[color:var(--color-fill-2)]'
              }`}
            >
              <IconScan size={12} />
            </button>
          </div>
        )}
        {!onModeChange && <span className="mr-auto" />}
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
      >
        <img
            data-fade
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
            data-fade
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
