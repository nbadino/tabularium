import { useRef, useState } from 'react'
import type { Axis } from '../../lib/grid'
import { useI18n } from '../../i18n'

/**
 * Il ritaglio della tabella con la griglia proposta **sopra**, trascinabile.
 *
 * È il punto in cui il lavoro dell'utente si fa davvero. La resa precedente
 * teneva l'immagine da una parte e i confini dall'altra, come cursori numerici:
 * su un ritaglio da 2600 px un cursore largo 80 px sposta la linea di ~32 px per
 * pixel di trascinamento, quindi la correzione fine non era possibile — e
 * nemmeno la verifica, perché la linea non si vedeva mai sull'inchiostro.
 *
 * Qui la linea sta dove passa, si prende dove si vede e si muove dove serve.
 *
 * Il **supporto** del rilevatore (su quante righe un confine è attestato) si
 * legge dalla forma prima che dal colore: continuo = attestato, tratteggiato =
 * da guardare. Il rosso di segnale resta riservato al confine vivo, cioè quello
 * selezionato, e la riga di stato lo nomina a parole.
 */

interface TableGridOverlayProps {
  cropUrl: string
  vlines: number[]
  hlines: number[]
  /** Supporto per confine verticale (len = vlines.length); assente = non rilevato. */
  columnSupport?: number[]
  /** Confini interni riga per riga, 0–1: dove il taglio passa davvero. */
  rowColumns?: number[][]
  /** Per ciascun confine di ciascuna riga, se un varco è stato provato. */
  rowColumnsProven?: boolean[][]
  rows: number
  onMove: (axis: Axis, index: number, value: number) => void
  onInsert: (axis: Axis, at: number) => void
  onDrop: (axis: Axis, index: number) => void
}

/** Sotto questa quota di righe un confine di colonna va guardato a mano. */
const WEAK = 0.5
/** Distanza minima fra due confini, in frazione del ritaglio. */
const MIN_GAP = 0.002

type Selection = { axis: Axis; index: number }

export default function TableGridOverlay({
  cropUrl,
  vlines,
  hlines,
  columnSupport,
  rowColumns,
  rowColumnsProven,
  rows,
  onMove,
  onInsert,
  onDrop,
}: TableGridOverlayProps) {
  const { t } = useI18n()
  const boxRef = useRef<HTMLDivElement | null>(null)
  const [zoom, setZoom] = useState(1)
  const [selected, setSelected] = useState<Selection | null>(null)
  const [arming, setArming] = useState<Axis | null>(null)
  const dragging = useRef<Selection | null>(null)

  const lines = (axis: Axis) => (axis === 'v' ? vlines : hlines)

  /** Posizione del puntatore nel ritaglio, 0–1, indipendente da zoom e scorrimento. */
  const normalised = (e: { clientX: number; clientY: number }, axis: Axis) => {
    const box = boxRef.current?.getBoundingClientRect()
    if (!box) return 0
    const value = axis === 'v' ? (e.clientX - box.left) / box.width : (e.clientY - box.top) / box.height
    return Math.min(1, Math.max(0, value))
  }

  const onLinePointerDown = (axis: Axis, index: number) => (e: React.PointerEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setArming(null)
    setSelected({ axis, index })
    // I bordi esterni delimitano il contenuto: si spostano, ma non si rifiutano.
    dragging.current = { axis, index }
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
  }

  const onPointerMove = (e: React.PointerEvent) => {
    const drag = dragging.current
    if (!drag) return
    const values = lines(drag.axis)
    const lower = drag.index > 0 ? values[drag.index - 1] + MIN_GAP : 0
    const upper = drag.index < values.length - 1 ? values[drag.index + 1] - MIN_GAP : 1
    if (upper <= lower) return
    const at = Math.min(upper, Math.max(lower, normalised(e, drag.axis)))
    onMove(drag.axis, drag.index, at)
  }

  const endDrag = () => {
    dragging.current = null
  }

  const onSurfaceClick = (e: React.MouseEvent) => {
    if (!arming) {
      setSelected(null)
      return
    }
    onInsert(arming, normalised(e, arming))
    setArming(null)
  }

  /**
   * La spezzata di ogni confine interno, tagliata nei tratti «provato» e
   * «non provato»: un solo `polyline` non può cambiare tratteggio a metà.
   */
  const drift = (() => {
    if (!rowColumns?.length || hlines.length < 2) return []
    const centre = (r: number) => ((hlines[r] ?? 0) + (hlines[r + 1] ?? 1)) / 2
    const count = rowColumns[0]?.length ?? 0
    const out: { path: string; proven: boolean }[][] = []
    for (let i = 0; i < count; i++) {
      const segments: { path: string; proven: boolean }[] = []
      let current: string[] = []
      let mode: boolean | null = null
      for (let r = 0; r < rowColumns.length && r < hlines.length - 1; r++) {
        const x = rowColumns[r]?.[i]
        if (x === undefined) continue
        const proven = rowColumnsProven?.[r]?.[i] ?? true
        const point = `${x},${centre(r)}`
        if (mode === null) {
          mode = proven
        } else if (proven !== mode) {
          current.push(point)
          segments.push({ path: current.join(' '), proven: mode })
          current = []
          mode = proven
        }
        current.push(point)
      }
      if (current.length > 1 && mode !== null) {
        segments.push({ path: current.join(' '), proven: mode })
      }
      out.push(segments)
    }
    return out
  })()

  const supportOf = (index: number) => columnSupport?.[index]
  const isWeak = (axis: Axis, index: number) => {
    if (axis !== 'v' || index === 0 || index === vlines.length - 1) return false
    const support = supportOf(index)
    return support !== undefined && rows > 0 && support < WEAK * rows
  }
  const isEdge = (axis: Axis, index: number) =>
    index === 0 || index === lines(axis).length - 1

  const statusText = () => {
    if (arming) return t(arming === 'v' ? 'table.overlayArmV' : 'table.overlayArmH')
    if (!selected) return t('table.overlayIdle')
    const { axis, index } = selected
    const total = lines(axis).length - 1
    const where = t(axis === 'v' ? 'table.overlayColumnLine' : 'table.overlayRowLine', {
      n: index + 1,
      total: total + 1,
    })
    if (isEdge(axis, index)) return `${where} · ${t('table.overlayEdge')}`
    const support = supportOf(index)
    if (axis === 'v' && support !== undefined) {
      const attested = t('table.overlaySupport', { n: support, rows })
      return isWeak(axis, index)
        ? `${where} · ${attested} · ${t('table.overlayWeak')}`
        : `${where} · ${attested}`
    }
    return where
  }

  const canDrop = !!selected && !isEdge(selected.axis, selected.index)

  return (
    <div className="flex min-h-0 flex-col">
      <div className="mb-1 flex flex-wrap items-center gap-1">
        <span className="lbl !mb-0 mr-auto">{t('table.overlayTitle')}</span>
        <button
          type="button"
          className={`btn btn-sm${arming === 'v' ? ' btn-primary' : ''}`}
          aria-pressed={arming === 'v'}
          onClick={() => setArming((a) => (a === 'v' ? null : 'v'))}
        >
          {t('table.overlayAddColumn')}
        </button>
        <button
          type="button"
          className={`btn btn-sm${arming === 'h' ? ' btn-primary' : ''}`}
          aria-pressed={arming === 'h'}
          onClick={() => setArming((a) => (a === 'h' ? null : 'h'))}
        >
          {t('table.overlayAddRow')}
        </button>
        <button
          type="button"
          className="btn btn-sm"
          disabled={!canDrop}
          onClick={() => {
            if (!selected) return
            onDrop(selected.axis, selected.index)
            setSelected(null)
          }}
        >
          {t('table.overlayReject')}
        </button>
        <span className="mx-1 h-4 w-px bg-[color:var(--color-rule)]" />
        <button type="button" className="btn btn-sm" onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))} aria-label={t('table.zoomOut')}>−</button>
        <span className="mono w-10 text-center text-[10px]">{Math.round(zoom * 100)}%</span>
        <button type="button" className="btn btn-sm" onClick={() => setZoom((z) => Math.min(6, z + 0.25))} aria-label={t('table.zoomIn')}>+</button>
        <button type="button" className="btn btn-sm" onClick={() => setZoom(1)}>{t('table.resetZoom')}</button>
      </div>

      <div
        className="lighttable max-h-[52vh] min-h-0 flex-1 overflow-auto border border-[color:var(--color-rule)]"
        onWheel={(e) => {
          if (!e.ctrlKey) return
          e.preventDefault()
          setZoom((z) => Math.max(0.5, Math.min(6, z + (e.deltaY < 0 ? 0.1 : -0.1))))
        }}
      >
        <div
          ref={boxRef}
          className="relative origin-top-left select-none"
          style={{ width: `${zoom * 100}%`, cursor: arming ? 'crosshair' : 'default' }}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onClick={onSurfaceClick}
        >
          <img src={cropUrl} alt={t('table.cropAlt')} className="block w-full" draggable={false} />

          {/* La deriva reale della colonna, sopra il ritaglio.
              La retta è ciò che si modifica e ciò che viene salvato; questa
              spezzata è dove il taglio è passato davvero riga per riga, ed è
              l'unica resa che rende visibile perché una retta non basta.
              Tratteggiata dove nessun varco è stato provato: lì il taglio non
              è una misura, è il prior rimasto in piedi. */}
          {drift.length > 0 && (
            <svg
              className="pointer-events-none absolute inset-0 h-full w-full"
              viewBox="0 0 1 1"
              preserveAspectRatio="none"
              aria-hidden
            >
              {drift.map((segments, i) =>
                segments.map((points, k) => (
                  <polyline
                    key={`d${i}-${k}`}
                    points={points.path}
                    fill="none"
                    stroke="var(--color-ink-3)"
                    strokeWidth={1}
                    strokeDasharray={points.proven ? undefined : '3 3'}
                    vectorEffect="non-scaling-stroke"
                  />
                )),
              )}
            </svg>
          )}

          {(['v', 'h'] as Axis[]).map((axis) =>
            lines(axis).map((value, index) => {
              const live = selected?.axis === axis && selected.index === index
              const weak = isWeak(axis, index)
              const colour = live ? 'var(--color-sig)' : 'var(--color-ink)'
              const along = axis === 'v'
                ? { left: `${value * 100}%`, top: 0, bottom: 0, width: 0 }
                : { top: `${value * 100}%`, left: 0, right: 0, height: 0 }
              return (
                <div
                  key={`${axis}${index}`}
                  role="button"
                  tabIndex={0}
                  aria-label={t(
                    axis === 'v' ? 'table.vlineAria' : 'table.hlineAria',
                    { n: index + 1 },
                  )}
                  onPointerDown={onLinePointerDown(axis, index)}
                  onKeyDown={(e) => {
                    const step = e.shiftKey ? 0.01 : 0.001
                    const back = axis === 'v' ? 'ArrowLeft' : 'ArrowUp'
                    const fwd = axis === 'v' ? 'ArrowRight' : 'ArrowDown'
                    if (e.key !== back && e.key !== fwd) return
                    e.preventDefault()
                    setSelected({ axis, index })
                    onMove(axis, index, Math.min(1, Math.max(0, value + (e.key === fwd ? step : -step))))
                  }}
                  className="absolute"
                  style={{
                    ...along,
                    // La zona di presa è più larga della linea: il confine si
                    // afferra dove si vede, non a un pixel di distanza.
                    ...(axis === 'v'
                      ? { borderLeft: `1px ${weak ? 'dashed' : 'solid'} ${colour}`, paddingLeft: 4, marginLeft: -4, boxSizing: 'content-box' as const, cursor: 'col-resize' }
                      : { borderTop: `1px ${weak ? 'dashed' : 'solid'} ${colour}`, paddingTop: 4, marginTop: -4, boxSizing: 'content-box' as const, cursor: 'row-resize' }),
                    zIndex: live ? 2 : 1,
                  }}
                />
              )
            }),
          )}
        </div>
      </div>

      <p className="mt-1 text-[11px] text-[color:var(--color-ink-2)]">{statusText()}</p>
    </div>
  )
}
