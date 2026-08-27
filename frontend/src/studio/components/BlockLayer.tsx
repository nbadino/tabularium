import { useEffect, useRef } from 'react'
import { Arrow, Line, Rect, Transformer } from 'react-konva'
import Konva from 'konva'
import { bboxPoints } from '../../lib/coords'
import type { DisplayBlock, Tool } from '../types'

interface BlockLayerProps {
  blocks: DisplayBlock[]
  selectedId: string | null
  tool: Tool
  viewportK: number
  showFlow: boolean
  colorFor: (label: string) => string
  onSelect: (id: string | null) => void
  onDragStart: (id: string) => void
  onDragEnd: (id: string, kind: 'rect' | 'polygon') => void
  onRectTransformEnd: (id: string) => void
  onLineTransformEnd: (id: string) => void
}

export default function BlockLayer({
  blocks,
  selectedId,
  tool,
  viewportK,
  showFlow,
  colorFor,
  onSelect,
  onDragStart,
  onDragEnd,
  onRectTransformEnd,
  onLineTransformEnd,
}: BlockLayerProps) {
  const trRef = useRef<Konva.Transformer>(null)
  const nodeRefs = useRef<Record<string, Konva.Node>>({})

  const sw = 2 / viewportK

  // transformer collegato al blocco selezionato
  useEffect(() => {
    const tr = trRef.current
    if (!tr) return
    if (tool !== 'select' || !selectedId) {
      tr.nodes([])
      tr.getLayer()?.batchDraw()
      return
    }
    const node = nodeRefs.current[selectedId]
    if (!node) {
      tr.nodes([])
      tr.getLayer()?.batchDraw()
      return
    }
    if (node.getType() === 'Line') {
      const line = node as Konva.Line
      ;(tr as unknown as { transformPointFun?: (x: number, y: number) => { x: number; y: number } })
        .transformPointFun = (x: number, y: number) => {
        const transform = line.getAbsoluteTransform().copy()
        transform.invert()
        return transform.point({ x, y })
      }
    } else {
      ;(tr as unknown as { transformPointFun?: (x: number, y: number) => { x: number; y: number } })
        .transformPointFun = undefined
    }
    tr.nodes([node])
    tr.getLayer()?.batchDraw()
  }, [selectedId, tool, blocks.length])

  const orderedBlocks = blocks
    .filter((b) => b.orderIdx != null)
    .sort((a, b) => (a.orderIdx ?? 0) - (b.orderIdx ?? 0))

  return (
    <>
      {blocks.map((b) => {
        const color = colorFor(b.label) || '#64748b'
        const selected = b.id === selectedId
        if (b.kind === 'rect') {
          const bb = bboxPoints(b.points)
          return (
            <Rect
              key={b.id}
              id={b.id}
              ref={(el) => {
                nodeRefs.current[b.id] = el as Konva.Node
              }}
              x={bb.x}
              y={bb.y}
              width={Math.max(bb.w, 1)}
              height={Math.max(bb.h, 1)}
              fill={color}
              opacity={0.28}
              stroke={color}
              strokeWidth={selected ? sw * 1.8 : sw}
              dash={selected ? [6 / viewportK, 4 / viewportK] : undefined}
              draggable={tool === 'select'}
              onClick={(e) => {
                e.cancelBubble = true
                onSelect(b.id)
              }}
              onDragStart={() => onDragStart(b.id)}
              onDragEnd={() => onDragEnd(b.id, 'rect')}
              onTransformEnd={() => onRectTransformEnd(b.id)}
              perfectDrawEnabled={false}
            />
          )
        }
        return (
          <Line
            key={b.id}
            id={b.id}
            ref={(el) => {
              nodeRefs.current[b.id] = el as Konva.Node
            }}
            points={b.points.flatMap((p) => [p.x, p.y])}
            closed
            fill={color}
            opacity={0.24}
            stroke={color}
            strokeWidth={selected ? sw * 1.8 : sw}
            dash={selected ? [6 / viewportK, 4 / viewportK] : undefined}
            draggable={tool === 'select'}
            onClick={(e) => {
              e.cancelBubble = true
              onSelect(b.id)
            }}
            onDragStart={() => onDragStart(b.id)}
            onDragEnd={() => onDragEnd(b.id, 'polygon')}
            onTransformEnd={() => onLineTransformEnd(b.id)}
          />
        )
      })}

      {/* frecce flusso lettura */}
      {showFlow && orderedBlocks.slice(0, -1).map((b, i) => {
        const next = orderedBlocks[i + 1]
        const bb1 = bboxPoints(b.points)
        const bb2 = bboxPoints(next.points)
        return (
          <Arrow
            key={`flow-${b.id}`}
            points={[
              bb1.x + bb1.w / 2,
              bb1.y + bb1.h / 2,
              bb2.x + bb2.w / 2,
              bb2.y + bb2.h / 2,
            ]}
            stroke="#f87171"
            strokeWidth={1.6 / viewportK}
            fill="#f87171"
            pointerLength={8 / viewportK}
            pointerWidth={6 / viewportK}
            listening={false}
          />
        )
      })}

      <Transformer
        ref={trRef}
        rotateEnabled={false}
        flipEnabled={false}
        keepRatio={false}
        anchorSize={8 / viewportK}
        borderStrokeWidth={sw}
        anchorCornerRadius={2 / viewportK}
      />
    </>
  )
}
