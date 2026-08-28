import { useCallback, useEffect, useRef, useState } from 'react'
import Konva from 'konva'
import type { ViewState } from './types'
import { clamp, clampViewport } from './canvasGeometry'

type PanEvent = Konva.KonvaEventObject<MouseEvent | PointerEvent | TouchEvent>

interface UseViewportReturn {
  viewport: ViewState
  containerSize: { w: number; h: number }
  containerRef: React.RefObject<HTMLDivElement | null>
  stageRef: React.RefObject<Konva.Stage | null>
  isPanning: boolean
  fit: () => void
  zoomBy: (factor: number) => void
  onWheel: (e: Konva.KonvaEventObject<WheelEvent>) => void
  startPan: (e: PanEvent) => void
  updatePan: () => void
  endPan: () => void
  onTouchStart: (e: Konva.KonvaEventObject<TouchEvent>) => void
  onTouchMove: (e: Konva.KonvaEventObject<TouchEvent>) => void
  onTouchEnd: (e: Konva.KonvaEventObject<TouchEvent>) => void
}

const MIN_ZOOM = 0.03
const MAX_ZOOM = 24

export function useViewport(imageNatural: { w: number; h: number }, resetKey = ''): UseViewportReturn {
  const containerRef = useRef<HTMLDivElement>(null)
  const stageRef = useRef<Konva.Stage>(null)
  const viewportRef = useRef<ViewState>({ x: 0, y: 0, k: 1 })
  const [viewport, setViewportState] = useState<ViewState>(viewportRef.current)
  const [containerSize, setContainerSize] = useState({ w: 0, h: 0 })
  const panRef = useRef<{ sx: number; sy: number; vx: number; vy: number } | null>(null)
  const pinchRef = useRef<{ center: { x: number; y: number }; distance: number } | null>(null)
  const [isPanning, setIsPanning] = useState(false)

  const setViewport = useCallback((next: ViewState | ((current: ViewState) => ViewState)) => {
    const value = typeof next === 'function' ? next(viewportRef.current) : next
    viewportRef.current = value
    setViewportState(value)
  }, [])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const update = () => setContainerSize({ w: el.clientWidth, h: el.clientHeight })
    update()
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  const fit = useCallback(() => {
    if (imageNatural.w <= 0 || imageNatural.h <= 0) return
    const k = Math.min(containerSize.w / imageNatural.w, containerSize.h / imageNatural.h) * 0.96
    setViewport({
      k,
      x: (containerSize.w - imageNatural.w * k) / 2,
      y: (containerSize.h - imageNatural.h * k) / 2,
    })
  }, [containerSize.h, containerSize.w, imageNatural.h, imageNatural.w, setViewport])

  // Una nuova pagina parte interamente visibile. I successivi resize non
  // azzerano invece il lavoro di zoom/pan dell'annotatore.
  const fittedImageRef = useRef('')
  useEffect(() => {
    const key = `${resetKey}:${imageNatural.w}x${imageNatural.h}`
    if (!containerSize.w || !containerSize.h || fittedImageRef.current === key) return
    fittedImageRef.current = key
    fit()
  }, [containerSize.h, containerSize.w, fit, imageNatural.h, imageNatural.w, resetKey])

  useEffect(() => {
    if (!containerSize.w || !containerSize.h || !imageNatural.w || !imageNatural.h) return
    setViewport((current) => clampViewport(current, containerSize, imageNatural))
  }, [containerSize, imageNatural, setViewport])

  const zoomAt = useCallback((factor: number, focus?: { x: number; y: number }) => {
    setViewport((current) => {
      const point = focus ?? { x: containerSize.w / 2, y: containerSize.h / 2 }
      const k = clamp(current.k * factor, MIN_ZOOM, MAX_ZOOM)
      const sceneX = (point.x - current.x) / current.k
      const sceneY = (point.y - current.y) / current.k
      return clampViewport(
        { k, x: point.x - sceneX * k, y: point.y - sceneY * k },
        containerSize,
        imageNatural,
      )
    })
  }, [containerSize, imageNatural, setViewport])

  const zoomBy = useCallback((factor: number) => zoomAt(factor), [zoomAt])

  const onWheel = useCallback((e: Konva.KonvaEventObject<WheelEvent>) => {
    e.evt.preventDefault()
    const pointer = stageRef.current?.getPointerPosition()
    if (!pointer) return
    const factor = Math.exp(-e.evt.deltaY * 0.0015)
    zoomAt(clamp(factor, 0.75, 1.33), pointer)
  }, [zoomAt])

  const startPan = useCallback((e: PanEvent) => {
    const pointer = stageRef.current?.getPointerPosition()
    if (!pointer) return
    e.evt.preventDefault()
    const current = viewportRef.current
    panRef.current = { sx: pointer.x, sy: pointer.y, vx: current.x, vy: current.y }
    setIsPanning(true)
  }, [])

  const updatePan = useCallback(() => {
    const start = panRef.current
    const pointer = stageRef.current?.getPointerPosition()
    if (!start || !pointer) return
    setViewport((current) => clampViewport({
      k: current.k,
      x: start.vx + pointer.x - start.sx,
      y: start.vy + pointer.y - start.sy,
    }, containerSize, imageNatural))
  }, [containerSize, imageNatural, setViewport])

  const endPan = useCallback(() => {
    panRef.current = null
    setIsPanning(false)
  }, [])

  const touchPoint = useCallback((touch: Touch) => {
    const rect = stageRef.current?.container().getBoundingClientRect()
    return rect ? { x: touch.clientX - rect.left, y: touch.clientY - rect.top } : null
  }, [])

  const onTouchStart = useCallback((e: Konva.KonvaEventObject<TouchEvent>) => {
    if (e.evt.touches.length !== 2) return
    e.evt.preventDefault()
    endPan()
    const a = touchPoint(e.evt.touches[0])
    const b = touchPoint(e.evt.touches[1])
    if (!a || !b) return
    pinchRef.current = {
      center: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 },
      distance: Math.hypot(b.x - a.x, b.y - a.y),
    }
  }, [endPan, touchPoint])

  const onTouchMove = useCallback((e: Konva.KonvaEventObject<TouchEvent>) => {
    const previous = pinchRef.current
    if (!previous || e.evt.touches.length !== 2) return
    e.evt.preventDefault()
    const a = touchPoint(e.evt.touches[0])
    const b = touchPoint(e.evt.touches[1])
    if (!a || !b) return
    const center = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
    const distance = Math.max(1, Math.hypot(b.x - a.x, b.y - a.y))
    setViewport((current) => {
      const sceneX = (previous.center.x - current.x) / current.k
      const sceneY = (previous.center.y - current.y) / current.k
      const k = clamp(current.k * distance / Math.max(1, previous.distance), MIN_ZOOM, MAX_ZOOM)
      return clampViewport(
        { k, x: center.x - sceneX * k, y: center.y - sceneY * k },
        containerSize,
        imageNatural,
      )
    })
    pinchRef.current = { center, distance }
  }, [containerSize, imageNatural, setViewport, touchPoint])

  const onTouchEnd = useCallback((e: Konva.KonvaEventObject<TouchEvent>) => {
    if (e.evt.touches.length < 2) pinchRef.current = null
  }, [])

  return {
    viewport,
    containerSize,
    containerRef,
    stageRef,
    isPanning,
    fit,
    zoomBy,
    onWheel,
    startPan,
    updatePan,
    endPan,
    onTouchStart,
    onTouchMove,
    onTouchEnd,
  }
}
