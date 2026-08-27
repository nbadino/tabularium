import { useCallback, useEffect, useState } from 'react'
import { Image as KonvaImage, Layer, Line, Rect, Stage } from 'react-konva'
import Konva from 'konva'
import useImage from 'use-image'
import type { Pt } from '../lib/coords'
import type { DisplayBlock, Tool } from './types'
import { useViewport } from './useViewport'
import BlockLayer from './components/BlockLayer'
import { IconMinus, IconPlus } from '../app/icons'
import { useI18n } from '../i18n'

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
  const vp = useViewport(imageNatural)
  const { viewport, containerSize, containerRef, stageRef } = vp

  const [tempRect, setTempRect] = useState<Pt[] | null>(null)
  const [polyVerts, setPolyVerts] = useState<Pt[]>([])

  // coordinate scena dal puntatore
  const scenePoint = useCallback((): Pt | null => {
    const p = stageRef.current?.getPointerPosition()
    if (!p) return null
    return { x: (p.x - viewport.x) / viewport.k, y: (p.y - viewport.y) / viewport.k }
  }, [viewport, stageRef])

  // --- mouse handlers (compongono pan + disegno) ----------------------------
  const onMouseDown = (e: Konva.KonvaEventObject<MouseEvent>) => {
    if (e.evt.button !== 0) return
    if (tool === 'pan') {
      vp.startPan(e)
      return
    }
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
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
      if (e.key === 'Delete' && selectedId) {
        e.preventDefault()
        onDeleteBlock(selectedId)
      }
      if (e.key === 'Escape') {
        setPolyVerts([])
        onSelect(null)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selectedId, onDeleteBlock, onSelect])

  // --- transform / drag handlers ---------------------------------------------
  const onRectTransformEnd = (id: string) => {
    const node = (stageRef.current?.findOne(`#${id}`) ?? null) as Konva.Rect | null
    if (!node) return
    const sx = node.scaleX()
    const sy = node.scaleY()
    node.scaleX(1)
    node.scaleY(1)
    onUpdateBlock(id, [
      { x: node.x(), y: node.y() },
      { x: node.x() + node.width() * sx, y: node.y() + node.height() * sy },
    ])
  }

  const onLineTransformEnd = (id: string) => {
    const node = (stageRef.current?.findOne(`#${id}`) ?? null) as Konva.Line | null
    if (!node) return
    const sx = node.scaleX()
    const sy = node.scaleY()
    node.scaleX(1)
    node.scaleY(1)
    const arr = node.points()
    const pts: Pt[] = []
    for (let i = 0; i < arr.length; i += 2) {
      pts.push({ x: arr[i] * sx, y: arr[i + 1] * sy })
    }
    node.points(pts.flatMap((p) => [p.x, p.y]))
    onUpdateBlock(id, pts)
  }

  const onDragEnd = (id: string, _kind: 'rect' | 'polygon') => {
    const node = (stageRef.current?.findOne(`#${id}`) ?? null) as Konva.Node | null
    if (!node) return
    const block = blocks.find((b) => b.id === id)
    if (!block) return
    const dx = node.x()
    const dy = node.y()
    node.x(0)
    node.y(0)
    onUpdateBlock(
      id,
      block.points.map((p) => ({ x: p.x + dx, y: p.y + dy })),
    )
  }

  const sw = 2 / viewport.k

  return (
    <div
      ref={containerRef}
      className="lighttable relative h-full w-full overflow-hidden"
      role="img"
      aria-label={t('canvas.aria', { n: blocks.length })}
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
        onClick={onStageClick}
        onDblClick={onStageDblClick}
        className="cursor-crosshair"
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
            tool={tool}
            viewportK={viewport.k}
            showFlow={showFlow}
            colorFor={colorFor}
            onSelect={onSelect}
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

      {tool === 'polygon' && (
        <p className="pointer-events-none absolute bottom-2 left-1/2 -translate-x-1/2 border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sheet)] px-2 py-1 text-[11px]">
          {t('canvas.polygonHint')}
          {polyVerts.length >= 3 && ` — ${tn('canvas.vertices', polyVerts.length)}`}
        </p>
      )}

      {/* Il livello di zoom è un'informazione, non un mistero. */}
      <div className="absolute bottom-2 right-2 flex items-stretch border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sheet)]">
        <button
          type="button"
          onClick={() => vp.zoomBy(1 / 1.25)}
          aria-label={t('canvas.zoomOut')}
          className="px-1.5 hover:bg-[color:var(--color-fill)]"
        >
          <IconMinus size={11} />
        </button>
        <button
          type="button"
          onClick={vp.fit}
          className="mono border-x border-[color:var(--color-rule)] px-2 py-1 text-[11px] hover:bg-[color:var(--color-fill)]"
          title={t('canvas.fitTitle')}
        >
          {Math.round(viewport.k * 100)}%
        </button>
        <button
          type="button"
          onClick={() => vp.zoomBy(1.25)}
          aria-label={t('canvas.zoomIn')}
          className="px-1.5 hover:bg-[color:var(--color-fill)]"
        >
          <IconPlus size={11} />
        </button>
      </div>
    </div>
  )
}
