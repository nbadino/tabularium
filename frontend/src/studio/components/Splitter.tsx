import { useRef, useState } from 'react'

interface SplitterProps {
  /** Larghezza corrente (px) del pannello che il manico governa. */
  value: number
  min: number
  max: number
  /** Lato del pannello rispetto al manico: 'left' = cresce trascinando a destra. */
  side: 'left' | 'right'
  label: string
  onChange: (value: number) => void
  /** Doppio click: torna alla larghezza di riposo. */
  onReset: () => void
}

const STEP = 16

/** Manico di ridimensionamento tra due colonne dello studio.
 *  Un filetto che si risveglia: 1px a riposo, piastra di segnale quando
 *  è vivo (hover o trascinamento). Comandi: trascinamento, frecce da
 *  tastiera (±{STEP}px), doppio click per il reset. */
export default function Splitter({ value, min, max, side, label, onChange, onReset }: SplitterProps) {
  const dragRef = useRef<{ startX: number; startValue: number } | null>(null)
  const [dragging, setDragging] = useState(false)

  const applyDelta = (delta: number) => {
    const next = side === 'left' ? dragRef.current!.startValue + delta : dragRef.current!.startValue - delta
    onChange(Math.min(max, Math.max(min, next)))
  }

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    dragRef.current = { startX: e.clientX, startValue: value }
    setDragging(true)
    // Il cursore segue l'elemento sotto il puntatore: durante il trascinamento
    // lo impostiamo sul body, così resta «col-resize» anche fuori dal filetto.
    document.body.style.cursor = 'col-resize'
  }

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return
    applyDelta(e.clientX - dragRef.current.startX)
  }

  const endDrag = () => {
    dragRef.current = null
    setDragging(false)
    document.body.style.cursor = ''
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    e.preventDefault()
    const dir = e.key === 'ArrowLeft' ? -1 : 1
    const next = side === 'left' ? value + dir * STEP : value - dir * STEP
    onChange(Math.min(max, Math.max(min, next)))
  }

  const live = dragging || undefined
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={Math.round(value)}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      title={label}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onKeyDown={onKeyDown}
      onDoubleClick={onReset}
      className="group relative z-10 w-[5px] shrink-0 cursor-col-resize touch-none select-none"
      data-dragging={live}
    >
      {/* Area di presa generosa, invisibile: il filetto disegnato resta 1px. */}
      <div className="absolute inset-y-0 -left-1 -right-1" />
      <div
        className={
          dragging
            ? 'absolute inset-y-0 left-1/2 w-[3px] -translate-x-1/2 bg-[color:var(--color-sig)]'
            : 'absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-[color:var(--color-rule)] transition-colors group-hover:bg-[color:var(--color-sig)] group-focus-visible:bg-[color:var(--color-sig)]'
        }
      />
    </div>
  )
}
