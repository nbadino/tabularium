import { useCallback, useEffect, useRef, useState } from 'react'
import { Image as KonvaImage, Layer, Line, Rect, Stage } from 'react-konva'
import Konva from 'konva'
import useImage from 'use-image'
import { bboxPoints, type Pt } from '../lib/coords'
import type { DisplayBlock, Tool } from './types'
import { useViewport } from './useViewport'
import BlockLayer from './components/BlockLayer'
import { IconFit, IconMinus, IconPlus } from '../app/icons'
import { useI18n } from '../i18n'
import { clamp, clampDragDelta, clampPoints } from './canvasGeometry'

interface StudioCanvasProps {
  imageUrl: string
  imageNatural: { w: number; h: number }
  blocks: DisplayBlock[]
  selectedId: string | null
  tool: Tool
  activeLabel: string
  showFlow: boolean
  colorFor: (label: string) => string
  onSelect: (id: string | null) => void
  onAddBlock: (input: { kind: 'rect' | 'polygon'; points: Pt[]; label: string }) => void
  onUpdateBlock: (id: string, points: Pt[]) => void
  onDeleteBlock: (id: string) => void
}

const MIN_SIZE = 3

export default function StudioCanvas({
  imageUrl,
  imageNatural,
  blocks,
  selectedId,
  tool,
  activeLabel,
  showFlow,
  colorFor,
  onSelect,
  onAddBlock,
  onUpdateBlock,
  onDeleteBlock,
}: StudioCanvasProps) {
  const { t, tn } = useI18n()
  const [image] = useImage(imageUrl)
  const vp = useViewport(imageNatural, imageUrl)
  const { viewport, containerSize, containerRef, stageRef } = vp

  const [tempRect, setTempRect] = useState<Pt[] | null>(null)
  const [polyVerts, setPolyVerts] = useState<Pt[]>([])
  const [spacePan, setSpacePan] = useState(false)

  // coordinate scena dal puntatore
  const scenePoint = useCallback((): Pt | null => {
    const p = stageRef.current?.getPointerPosition()
    if (!p) return null
    return {
      x: clamp((p.x - viewport.x) / viewport.k, 0, imageNatural.w),
      y: clamp((p.y - viewport.y) / viewport.k, 0, imageNatural.h),
    }
  }, [imageNatural.h, imageNatural.w, viewport, stageRef])

  // --- mouse handlers (compongono pan + disegno) ----------------------------
  const onMouseDown = (e: Konva.KonvaEventObject<MouseEvent>) => {
    const wantsPan = tool === 'pan' || spacePan || e.evt.button === 1
    if (wantsPan) {
      vp.startPan(e)
      return
    }
    if (e.evt.button !== 0) return
    const sp = scenePoint()
    if (tool === 'rect' && sp) {
      setTempRect([sp, sp])
      return
    }
    if (tool === 'select') {
      const target = e.target
      const isShape = target.getType() === 'Rect' || target.getType() === 'Line'
      if (!isShape) onSelect(null)
    }
  }

  const onMouseMove = () => {
    if (vp.isPanning) {
      vp.updatePan()
      return
    }
    if (tempRect) {
      const sp = scenePoint()
      if (sp) setTempRect([tempRect[0], sp])
    }
  }

  const onMouseUp = () => {
    if (vp.isPanning) {
      vp.endPan()
      return
    }
    if (tempRect) {
      const [a, b] = tempRect
      const w = Math.abs(b.x - a.x)
      const h = Math.abs(b.y - a.y)
      if (w >= MIN_SIZE && h >= MIN_SIZE) {
        const x1 = Math.min(a.x, b.x)
        const y1 = Math.min(a.y, b.y)
        onAddBlock({
          kind: 'rect',
          label: activeLabel,
          points: [
            { x: x1, y: y1 },
            { x: x1 + w, y: y1 + h },
          ],
        })
      }
      setTempRect(null)
    }
  }

  useEffect(() => {
    setTempRect(null)
    setPolyVerts([])
    vp.endPan()
    // Il cambio strumento conclude il gesto corrente; endPan è stabile.
  }, [tool]) // eslint-disable-line react-hooks/exhaustive-deps

  const onStageClick = () => {
    if (tool === 'polygon') {
      const sp = scenePoint()
      if (sp) setPolyVerts((v) => [...v, sp])
    }
  }

  const onStageDblClick = () => {
    setPolyVerts((verts) => {
      const stripped = verts.slice(0, -1)
      if (stripped.length >= 3) {
        onAddBlock({ kind: 'polygon', label: activeLabel, points: stripped })
        return []
      }
      return verts
    })
  }

  // --- tastiera: cancella blocco selezionato / annulla poligono --------------
  // Solo Delete cancella: Backspace resta libero (vicino alla trascrizione).
  // La cancellazione è annullabile dal toast/Ctrl+Z prima del prossimo autosave.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (t?.closest('input, textarea, select, button, a, [contenteditable="true"]')) return
      if (e.key === 'Delete' && selectedId) {
        e.preventDefault()
        onDeleteBlock(selectedId)
      }
      if (e.key === 'Escape') {
        setPolyVerts([])
        setTempRect(null)
        vp.endPan()
        onSelect(null)
      }
      if (e.code === 'Space' && !e.repeat) {
        e.preventDefault()
        setSpacePan(true)
      }
      // Zoom da tastiera, come nei visualizzatori di sistema: Ctrl+0 rientra,
      // Ctrl +/- avvicina e allontana. Evita il browser zoom sulla pagina.
      if ((e.ctrlKey || e.metaKey) && (e.key === '0' || e.key === '+' || e.key === '=' || e.key === '-')) {
        e.preventDefault()
        if (e.key === '0') vp.fit()
        else vp.zoomBy(e.key === '-' ? 1 / 1.25 : 1.25)
      }
    }
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code !== 'Space') return
      setSpacePan(false)
      vp.endPan()
    }
    const onBlur = () => {
      setSpacePan(false)
      vp.endPan()
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('keyup', onKeyUp)
    window.addEventListener('blur', onBlur)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('keyup', onKeyUp)
      window.removeEventListener('blur', onBlur)
    }
  }, [selectedId, onDeleteBlock, onSelect, vp.endPan, vp.fit, vp.zoomBy])

  // --- transform / drag handlers ---------------------------------------------
  const onRectTransformEnd = (id: string) => {
    const node = (stageRef.current?.findOne(`#${id}`) ?? null) as Konva.Rect | null
    if (!node) return
    const sx = node.scaleX()
    const sy = node.scaleY()
    const points = clampPoints([
      { x: node.x(), y: node.y() },
      { x: node.x() + node.width() * sx, y: node.y() + node.height() * sy },
    ], imageNatural)
    node.scale({ x: 1, y: 1 })
    onUpdateBlock(id, points)
  }

  const onLineTransformEnd = (id: string) => {
    const node = (stageRef.current?.findOne(`#${id}`) ?? null) as Konva.Line | null
    if (!node) return
    const arr = node.points()
    const transform = node.getTransform().copy()
    const pts: Pt[] = []
    for (let i = 0; i < arr.length; i += 2) {
      pts.push(transform.point({ x: arr[i], y: arr[i + 1] }))
    }
    node.position({ x: 0, y: 0 })
    node.scale({ x: 1, y: 1 })
    const bounded = clampPoints(pts, imageNatural)
    node.points(bounded.flatMap((p) => [p.x, p.y]))
    onUpdateBlock(id, bounded)
  }

  // La posizione del nodo al dragstart è l'unico riferimento affidabile: un
  // re-render durante il drag (autosave, selezione…) ri-applica x/y da props
  // e la posizione al rilascio conterrebbe anche l'origine, non solo il gesto.
  // Leggere node.x() come fosse il solo spostamento faceva «crollare» il
  // blocco in basso della propria posizione, accumulando ad ogni trascinamento.
  const dragStartRef = useRef<{ x: number; y: number } | null>(null)
  const onDragStart = (id: string) => {
    const node = (stageRef.current?.findOne(`#${id}`) ?? null) as Konva.Node | null
    dragStartRef.current = node ? { x: node.x(), y: node.y() } : null
  }

  const onDragMove = (id: string) => {
    const node = (stageRef.current?.findOne(`#${id}`) ?? null) as Konva.Node | null
    const block = blocks.find((item) => item.id === id)
    const start = dragStartRef.current
    if (!node || !block || !start) return
    const box = bboxPoints(block.points)
    const delta = clampDragDelta(box, node.x() - start.x, node.y() - start.y, imageNatural)
    node.position({ x: start.x + delta.x, y: start.y + delta.y })
  }

  const onDragEnd = (id: string, _kind: 'rect' | 'polygon') => {
    const node = (stageRef.current?.findOne(`#${id}`) ?? null) as Konva.Node | null
    if (!node) return
    const block = blocks.find((b) => b.id === id)
    if (!block) return
    const start = dragStartRef.current ?? { x: 0, y: 0 }
    dragStartRef.current = null
    const dx = node.x() - start.x
    const dy = node.y() - start.y
    if (!dx && !dy) return
    node.x(start.x)
    node.y(start.y)
    onUpdateBlock(
      id,
      block.points.map((p) => ({ x: p.x + dx, y: p.y + dy })),
    )
  }

  const sw = 2 / viewport.k
  const interactionTool: Tool = spacePan ? 'pan' : tool
  const canvasCursor = vp.isPanning
    ? 'cursor-grabbing'
    : interactionTool === 'pan'
      ? 'cursor-grab'
      : interactionTool === 'select'
        ? 'cursor-default'
        : 'cursor-crosshair'
  const interactionHint = interactionTool === 'pan'
    ? t('canvas.panHint')
    : interactionTool === 'select'
      ? t('canvas.selectHint')
      : interactionTool === 'rect'
        ? t('canvas.rectHint')
        : t('canvas.polygonHint')

  useEffect(() => {
    const container = stageRef.current?.container()
    if (container) container.style.cursor = ''
  }, [interactionTool, stageRef, vp.isPanning])

  return (
    <div
      ref={containerRef}
      className="lighttable relative h-full w-full overflow-hidden"
      role="img"
      aria-label={t('canvas.aria', { n: blocks.length })}
      style={{ touchAction: 'none' }}
    >
      <Stage
        ref={stageRef}
        width={containerSize.w}
        height={containerSize.h}
        x={viewport.x}
        y={viewport.y}
        scaleX={viewport.k}
        scaleY={viewport.k}
        onWheel={vp.onWheel}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={() => {
          if (vp.isPanning) vp.endPan()
        }}
        onClick={onStageClick}
        onDblClick={onStageDblClick}
        onTouchStart={(e) => {
          vp.onTouchStart(e)
          if (e.evt.touches.length === 1 && interactionTool === 'pan') vp.startPan(e)
        }}
        onTouchMove={(e) => {
          vp.onTouchMove(e)
          if (e.evt.touches.length === 1 && vp.isPanning) vp.updatePan()
        }}
        onTouchEnd={(e) => {
          vp.onTouchEnd(e)
          if (e.evt.touches.length === 0) vp.endPan()
        }}
        className={canvasCursor}
      >
        <Layer listening={false}>
          <KonvaImage
            image={image}
            width={imageNatural.w}
            height={imageNatural.h}
          />
        </Layer>
        <Layer>
          <BlockLayer
            blocks={blocks}
            selectedId={selectedId}
            tool={interactionTool}
            viewportK={viewport.k}
            showFlow={showFlow}
            colorFor={colorFor}
            onSelect={onSelect}
            onDragStart={onDragStart}
            onDragMove={onDragMove}
            onDragEnd={onDragEnd}
            onRectTransformEnd={onRectTransformEnd}
            onLineTransformEnd={onLineTransformEnd}
          />

          {/* rettangolo temporaneo (disegno) */}
          {tempRect && (
            <Rect
              x={Math.min(tempRect[0].x, tempRect[1].x)}
              y={Math.min(tempRect[0].y, tempRect[1].y)}
              width={Math.abs(tempRect[1].x - tempRect[0].x)}
              height={Math.abs(tempRect[1].y - tempRect[0].y)}
              stroke="#e60012"
              strokeWidth={sw}
              dash={[6 / viewport.k, 4 / viewport.k]}
              listening={false}
            />
          )}

          {/* preview poligono */}
          {polyVerts.length > 0 && (
            <Line
              points={polyVerts.flatMap((p) => [p.x, p.y])}
              closed={polyVerts.length >= 3}
              stroke="#e60012"
              strokeWidth={sw}
              dash={[4 / viewport.k, 3 / viewport.k]}
              listening={false}
            />
          )}
          {polyVerts.map((v, i) => (
            <Rect
              key={i}
              x={v.x - 2 / viewport.k}
              y={v.y - 2 / viewport.k}
              width={4 / viewport.k}
              height={4 / viewport.k}
              fill="#e60012"
              listening={false}
            />
          ))}
        </Layer>
      </Stage>

      <p className="pointer-events-none absolute bottom-2 left-2 hidden max-w-[calc(100%-260px)] border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sheet)] px-2 py-1 text-[11px] text-[color:var(--color-ink-2)] sm:block">
        {interactionHint}
        {tool === 'polygon' && polyVerts.length >= 3 && ` — ${tn('canvas.vertices', polyVerts.length)}`}
      </p>

      {/* Il livello di zoom è un'informazione, non un mistero. */}
      <div className="absolute bottom-2 right-2 flex items-stretch border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sheet)]">
        <button
          type="button"
          onClick={() => vp.zoomBy(1 / 1.25)}
          aria-label={t('canvas.zoomOut')}
          className="btn btn-sm border-0"
        >
          <IconMinus size={11} />
          <span>{t('canvas.zoomOutShort')}</span>
        </button>
        <button
          type="button"
          onClick={vp.fit}
          className="btn btn-sm mono border-y-0"
          title={t('canvas.fitTitle')}
        >
          <IconFit size={11} />
          {t('canvas.fit')} · {Math.round(viewport.k * 100)}%
        </button>
        <button
          type="button"
          onClick={() => vp.zoomBy(1.25)}
          aria-label={t('canvas.zoomIn')}
          className="btn btn-sm border-0"
        >
          <IconPlus size={11} />
          <span>{t('canvas.zoomInShort')}</span>
        </button>
      </div>
    </div>
  )
}
