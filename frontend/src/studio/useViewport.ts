import { useCallback, useEffect, useRef, useState } from 'react'
import Konva from 'konva'
import type { ViewState } from './types'

interface UseViewportReturn {
  viewport: ViewState
  containerSize: { w: number; h: number }
  containerRef: React.RefObject<HTMLDivElement | null>
  stageRef: React.RefObject<Konva.Stage | null>
  isPanning: boolean
  /** Riporta l'immagine all'inquadratura iniziale. */
  fit: () => void
  /** Zoom a passi dal centro del contenitore, per i controlli e la tastiera. */
  zoomBy: (factor: number) => void
  onWheel: (e: Konva.KonvaEventObject<WheelEvent>) => void
  startPan: (e: Konva.KonvaEventObject<MouseEvent>) => void
  updatePan: () => void
  endPan: () => void
}

export function useViewport(imageNatural: { w: number; h: number }): UseViewportReturn {
  const containerRef = useRef<HTMLDivElement>(null)
  const stageRef = useRef<Konva.Stage>(null)
  const [viewport, setViewport] = useState<ViewState>({ x: 0, y: 0, k: 1 })
  const [containerSize, setContainerSize] = useState({ w: 960, h: 640 })
  const [panPos, setPanPos] = useState<{ sx: number; sy: number; vx: number; vy: number } | null>(null)

  // dimensioni contenitore + resize observer
  useEffect(() => {
    if (!containerRef.current) return
    const el = containerRef.current
    const update = () => setContainerSize({ w: el.clientWidth, h: el.clientHeight })
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // fit iniziale immagine
  const imageRef = useRef(imageNatural)
  imageRef.current = imageNatural
  useEffect(() => {
    const iw = imageNatural.w
    const ih = imageNatural.h
    if (iw <= 0 || ih <= 0) return
    const k = Math.min(containerSize.w / iw, containerSize.h / ih) * 0.96
    setViewport({ k, x: (containerSize.w - iw * k) / 2, y: (containerSize.h - ih * k) / 2 })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [imageNatural.w, imageNatural.h, containerSize.w, containerSize.h])

  const fit = useCallback(() => {
    const { w: iw, h: ih } = imageRef.current
    if (iw <= 0 || ih <= 0) return
    const k = Math.min(containerSize.w / iw, containerSize.h / ih) * 0.96
    setViewport({ k, x: (containerSize.w - iw * k) / 2, y: (containerSize.h - ih * k) / 2 })
  }, [containerSize.w, containerSize.h])

  const zoomBy = useCallback(
    (factor: number) => {
      setViewport((v) => {
        const k = Math.min(24, Math.max(0.03, v.k * factor))
        // Lo zoom tiene fermo il centro del contenitore, non l'angolo.
        const cx = containerSize.w / 2
        const cy = containerSize.h / 2
        const mx = (cx - v.x) / v.k
        const my = (cy - v.y) / v.k
        return { k, x: cx - mx * k, y: cy - my * k }
      })
    },
    [containerSize.w, containerSize.h],
  )

  const onWheel = useCallback(
    (e: Konva.KonvaEventObject<WheelEvent>) => {
      e.evt.preventDefault()
      const pointer = stageRef.current?.getPointerPosition()
      if (!pointer) return
      const oldK = viewport.k
      const factor = e.evt.deltaY < 0 ? 1.1 : 0.9
      const k = Math.min(24, Math.max(0.03, oldK * factor))
      const mx = (pointer.x - viewport.x) / oldK
      const my = (pointer.y - viewport.y) / oldK
      setViewport({ k, x: pointer.x - mx * k, y: pointer.y - my * k })
    },
    [viewport],
  )

  const startPan = useCallback(
    (e: Konva.KonvaEventObject<MouseEvent>) => {
      if (e.evt.button !== 0) return
      const p = stageRef.current?.getPointerPosition()
      if (!p) return
      setPanPos({ sx: p.x, sy: p.y, vx: viewport.x, vy: viewport.y })
    },
    [viewport],
  )

  const updatePan = useCallback(() => {
    if (!panPos) return
    const p = stageRef.current?.getPointerPosition()
    if (!p) return
    setViewport({
      k: viewport.k,
      x: panPos.vx + (p.x - panPos.sx),
      y: panPos.vy + (p.y - panPos.sy),
    })
  }, [panPos, viewport.k])

  const endPan = useCallback(() => {
    setPanPos(null)
  }, [])

  return {
    viewport,
    containerSize,
    containerRef,
    stageRef,
    isPanning: panPos !== null,
    fit,
    zoomBy,
    onWheel,
    startPan,
    updatePan,
    endPan,
  }
}
