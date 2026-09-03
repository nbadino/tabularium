import { useRef, useState } from 'react'
import type { Axis } from '../../lib/grid'
import {
  dropBoundary,
  insertBoundary,
  mergeRange,
  normalizeTableGrid,
  ownerMap,
  resizeGrid,
  sortedCells,
  splitCell,
} from '../../lib/grid'
import type { TableDetectOut, TableDetectRequest, TableGrid } from '../../lib/types'
import { Modal, WarnNotice } from '../../app/ui'
import { IconCopy, IconMinus, IconPlus, IconSave } from '../../app/icons'
import { useInference } from '../../app/inference'
import { useI18n } from '../../i18n'
import TableGridOverlay from './TableGridOverlay'

interface TableCellsEditorProps {
  grid: TableGrid
  cropUrl: string | null
  onSave: (grid: TableGrid) => Promise<string>
  /** Assente quando il blocco non è ancora salvato lato server. */
  onDetect?: (opts: TableDetectRequest) => Promise<TableDetectOut>
  onClose: () => void
}

/** Sotto questa quota di righe un confine di colonna va guardato a mano. */
const WEAK_BOUNDARY = 0.5

type Mode = 'edit' | 'merge' | 'select'

const MODE_KEY: Record<Mode, string> = {
  edit: 'table.modeEdit',
  merge: 'table.modeMerge',
  select: 'table.modeSelect',
}

const MODE_HINT_KEY: Record<Mode, string> = {
  edit: 'table.hintEdit',
  merge: 'table.hintMerge',
  select: 'table.hintSelect',
}

export default function TableCellsEditor({
  grid: initialGrid,
  cropUrl,
  onSave,
  onDetect,
  onClose,
}: TableCellsEditorProps) {
  const { t } = useI18n()
  const inf = useInference()
  const [grid, setGrid] = useState<TableGrid>(() => normalizeTableGrid(initialGrid))
  const [detecting, setDetecting] = useState(false)
  const [detectFill, setDetectFill] = useState<'none' | 'ocr' | 'model'>('none')
  const [detectInfo, setDetectInfo] = useState<TableDetectOut | null>(null)
  const [mode, setMode] = useState<Mode>('edit')
  const [selStart, setSelStart] = useState<{ r: number; c: number } | null>(null)
  const [selEnd, setSelEnd] = useState<{ r: number; c: number } | null>(null)
  const [selectedCell, setSelectedCell] = useState<{ r: number; c: number } | null>(null)
  const [saving, setSaving] = useState(false)
  const [otsl, setOtsl] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const textareaRefs = useRef<Record<string, HTMLTextAreaElement | null>>({})

  const map = ownerMap(grid)
  const cells = sortedCells(grid)
  const textareaKey = (r: number, c: number) => `${r}:${c}`

  const setCellText = (r: number, c: number, text: string) =>
    setGrid((g) => ({
      ...g,
      cells: g.cells.map((cell) => (cell.r === r && cell.c === c ? { ...cell, text } : cell)),
    }))

  const setLine = (axis: 'vlines' | 'hlines', index: number, value: number) =>
    setGrid((g) => {
      const values = [...(g[axis] ?? [])]
      values[index] = value
      return { ...g, [axis]: values }
    })

  const moveBoundary = (axis: Axis, index: number, value: number) =>
    setLine(axis === 'v' ? 'vlines' : 'hlines', index, value)

  // Inserire o rifiutare un confine cambia il numero di tracce e quindi le
  // celle: le due primitive rifiutano i casi ambigui invece di indovinare, e
  // qui il rifiuto diventa un avviso, non un silenzio.
  const addBoundary = (axis: Axis, at: number) =>
    setGrid((g) => {
      const next = insertBoundary(g, axis, at)
      if (!next) {
        setNotice(t('table.boundaryInsertRefused'))
        return g
      }
      setNotice(null)
      return next
    })

  const rejectBoundary = (axis: Axis, index: number) =>
    setGrid((g) => {
      const next = dropBoundary(g, axis, index)
      if (!next) {
        setNotice(t('table.boundaryDropRefused'))
        return g
      }
      setNotice(null)
      setSelectedCell(null)
      return next
    })

  const onSaveClick = async () => {
    setSaving(true)
    try {
      setOtsl(await onSave(grid))
      setNotice(null)
    } catch (e) {
      setNotice(
        e instanceof Error
          ? t('table.saveFailedWith', { msg: e.message })
          : t('table.saveFailed'),
      )
    } finally {
      setSaving(false)
    }
  }

  const onDetectClick = async () => {
    if (!onDetect) return
    // Il rilevamento riscrive righe, colonne e celle: se c'è già trascrizione
    // dentro, la si perde. Meglio chiedere che far sparire il lavoro fatto.
    const hasText = grid.cells.some((c) => c.text.trim() !== '')
    if (hasText && !window.confirm(t('table.detectOverwrite'))) return

    setDetecting(true)
    try {
      const out = await onDetect({ fill: detectFill })
      setGrid(normalizeTableGrid(out.grid))
      setDetectInfo(out)
      setSelectedCell(null)
      setNotice(null)
    } catch (e) {
      setDetectInfo(null)
      setNotice(
        e instanceof Error ? t('table.detectFailedWith', { msg: e.message }) : t('table.detectFailed'),
      )
    } finally {
      setDetecting(false)
    }
  }

  // I bordi esterni non sono "attestati" come i confini interni: si contano
  // solo quelli interni, che sono le decisioni che l'utente deve verificare.
  const weakBoundaries = detectInfo
    ? detectInfo.column_support
        .slice(1, -1)
        .filter((s) => s < WEAK_BOUNDARY * detectInfo.grid.rows).length
    : 0

  const onCellMouseDown = (r: number, c: number, e: React.MouseEvent) => {
    if (mode === 'merge') {
      e.preventDefault()
      setSelStart({ r, c })
      setSelEnd({ r, c })
    }
  }

  const onDocMouseUp = () => {
    if (mode === 'merge' && selStart && selEnd) {
      const r1 = Math.min(selStart.r, selEnd.r)
      const c1 = Math.min(selStart.c, selEnd.c)
      const r2 = Math.max(selStart.r, selEnd.r)
      const c2 = Math.max(selStart.c, selEnd.c)
      if (r1 !== r2 || c1 !== c2) {
        const merged = mergeRange(grid, r1, c1, r2, c2)
        if (merged) {
          setGrid(merged)
          setNotice(null)
        } else {
          setNotice(t('table.mergeConflict'))
        }
      }
    }
    setSelStart(null)
    setSelEnd(null)
  }

  const doSplit = () => {
    if (!selectedCell) return
    const out = splitCell(grid, selectedCell.r, selectedCell.c)
    if (out) {
      setGrid(out)
      setNotice(null)
    } else {
      setNotice(t('table.notMerged'))
    }
  }

  const selOwner = selectedCell ? map[selectedCell.r]?.[selectedCell.c] : undefined
  const selectedIsMerged = !!selOwner && (selOwner.rowspan > 1 || selOwner.colspan > 1)

  return (
    <Modal
      title={t('table.title')}
      onClose={onClose}
      wide
      footer={
        <>
          <button onClick={onClose} className="btn">
            {t('common.cancel')}
          </button>
          <button onClick={() => void onSaveClick()} disabled={saving} className="btn btn-primary">
            <IconSave size={13} />
            {saving ? t('table.saving') : t('table.saveGrid')}
          </button>
        </>
      }
    >
      <div className="flex flex-wrap items-end gap-4 border-b border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-3 py-2">
        <div>
          <span className="lbl">{t('table.rows')}</span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setGrid((g) => resizeGrid(g, Math.max(1, g.rows - 1), g.cols))}
              className="btn btn-sm"
              aria-label={t('table.removeRow')}
            >
              <IconMinus size={11} />
            </button>
            <span className="mono w-7 text-center text-[13px]">{grid.rows}</span>
            <button
              onClick={() => setGrid((g) => resizeGrid(g, g.rows + 1, g.cols))}
              className="btn btn-sm"
              aria-label={t('table.addRow')}
            >
              <IconPlus size={11} />
            </button>
          </div>
        </div>
        <div>
          <span className="lbl">{t('table.cols')}</span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setGrid((g) => resizeGrid(g, g.rows, Math.max(1, g.cols - 1)))}
              className="btn btn-sm"
              aria-label={t('table.removeCol')}
            >
              <IconMinus size={11} />
            </button>
            <span className="mono w-7 text-center text-[13px]">{grid.cols}</span>
            <button
              onClick={() => setGrid((g) => resizeGrid(g, g.rows, g.cols + 1))}
              className="btn btn-sm"
              aria-label={t('table.addCol')}
            >
              <IconPlus size={11} />
            </button>
          </div>
        </div>
        <div>
          <label className="lbl" htmlFor="table-header-rows-modal">Header</label>
          <input
            id="table-header-rows-modal"
            type="number"
            min={0}
            max={20}
            value={grid.header_rows ?? 0}
            onChange={(e) => setGrid((g) => ({ ...g, header_rows: Math.max(0, Math.min(20, Number(e.target.value) || 0)) }))}
            className="fld fld-mono w-16"
            title="Numero di righe di intestazione dichiarate manualmente"
          />
        </div>
        <div>
          <span className="lbl">{t('table.mode')}</span>
          <div className="flex items-center gap-1">
            {(['edit', 'merge', 'select'] as const).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                aria-pressed={mode === m}
                className={`btn btn-sm ${
                  mode === m ? '!border-[color:var(--color-ink)] !bg-[color:var(--color-ink)] !text-white' : ''
                }`}
              >
                {t(MODE_KEY[m])}
              </button>
            ))}
            <button
              onClick={doSplit}
              disabled={!selectedIsMerged}
              className="btn btn-sm ml-1"
              title={t('table.separateTitle')}
            >
              {t('table.separate')}
            </button>
          </div>
        </div>
        {onDetect && (
          <div>
            <span className="lbl">{t('table.detect')}</span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => void onDetectClick()}
                disabled={detecting}
                className="btn btn-sm"
                title={t('table.detectTitle')}
              >
                {detecting ? t('table.detecting') : t('table.detectRun')}
              </button>
              <select
                value={detectFill}
                onChange={(e) => setDetectFill(e.target.value as 'none' | 'ocr' | 'model')}
                disabled={detecting}
                aria-label={t('table.detectFill')}
                className="btn btn-sm !px-1 text-[11px]"
              >
                <option value="none">📐 {t('table.detectFillNone')}</option>
                <option value="ocr">📄 {t('table.detectFillOcr')} (CPU)</option>
                <option
                  value="model"
                  disabled={!inf.enabled || !inf.available}
                >
                  ⚡ {t('table.detectFillModel')}{' '}
                  {!inf.enabled
                    ? '(GPU Disattivata)'
                    : !inf.available
                      ? '(Offline)'
                      : `(${inf.isCloud ? 'Cloud' : 'GPU'})`}
                </option>
              </select>
            </div>
          </div>
        )}
        <p className="ml-auto max-w-[26rem] text-[11px] text-[color:var(--color-ink-2)]">
          {t(MODE_HINT_KEY[mode])}
        </p>
      </div>

      {detectInfo && (
        <div className="border-b border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-3 py-2 text-[11px] text-[color:var(--color-ink-2)]">
          <span className="mono">
            {t('table.detectSummary', {
              rows: detectInfo.grid.rows,
              cols: detectInfo.grid.cols,
              pitch: detectInfo.diagnostics.pitch_px ?? 0,
              dots: detectInfo.diagnostics.leader_dots_suppressed ?? 0,
            })}
          </span>
          {detectInfo.ocr && (
            <span className="mono ml-3">
              {t('table.detectOcrSummary', {
                engine: detectInfo.ocr.engine ?? '',
                filled: detectInfo.ocr.filled ?? 0,
                blank: detectInfo.ocr.blank ?? 0,
                score: Math.round((detectInfo.ocr.mean_score ?? 0) * 100),
              })}
            </span>
          )}
          {weakBoundaries > 0 && (
            <span className="ml-3 text-[color:var(--color-warn)]">
              {t('table.detectWeak', { n: weakBoundaries })}
            </span>
          )}
          {/* La deriva non è un allarme — c'è su ogni pagina del corpus — ma
              un conto: quante celle hanno un confine che nessun varco prova.
              Sono esattamente quelle da guardare per prime. */}
          {(detectInfo.diagnostics.row_columns_unproven ?? 0) > 0 && (
            <span className="ml-3 text-[color:var(--color-warn)]">
              {t('table.detectDrift', {
                n: detectInfo.diagnostics.row_columns_unproven ?? 0,
              })}
            </span>
          )}
        </div>
      )}

      {/* Gli avvisi del rilevatore non sono statistica: dicono che una condizione
          misurata della scansione limita la proposta, e cosa fare per toglierla.
          Vanno quindi in una piastra d'allarme, non nella riga di riepilogo. */}
      {detectInfo?.warnings?.includes('skewed') && (
        <div className="p-3 pb-0">
          <WarnNotice title={t('table.detectWarnTitle')}>
            {t('table.detectWarnSkewed', {
              deg: (detectInfo.diagnostics.skew_deg ?? 0).toFixed(2),
            })}
          </WarnNotice>
        </div>
      )}

      {notice && (
        <div className="p-3 pb-0">
          <WarnNotice title={t('table.notDone')}>{notice}</WarnNotice>
        </div>
      )}

      <div className="flex min-h-0 gap-3 p-3">
        {cropUrl && (
          <figure className="m-0 flex w-1/2 shrink-0 flex-col">
            <TableGridOverlay
              cropUrl={cropUrl}
              vlines={grid.vlines ?? []}
              hlines={grid.hlines ?? []}
              columnSupport={detectInfo?.column_support}
              rowColumns={grid.row_columns ?? detectInfo?.diagnostics.row_columns}
              rowColumnsProven={grid.row_columns_proven ?? detectInfo?.diagnostics.row_columns_proven}
              rows={grid.rows}
              onMove={moveBoundary}
              onInsert={addBoundary}
              onDrop={rejectBoundary}
            />
          </figure>
        )}
        <div className="min-w-0 flex-1">
          <span className="lbl">{t('table.grid')}</span>
          <div className="max-h-[52vh] overflow-auto border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2">
            <table className="border-collapse" onMouseUp={onDocMouseUp}>
              <tbody>
                {Array.from({ length: grid.rows }, (_, r) => {
                  const rowCells = cells.filter((c) => c.r === r)
                  return (
                    <tr key={r}>
                      {rowCells.map((cell) => {
                        const key = textareaKey(cell.r, cell.c)
                        const phantom = grid.phantom_cols.includes(cell.c)
                        const inRange =
                          selStart && selEnd
                            ? cell.r >= Math.min(selStart.r, selEnd.r) &&
                              cell.r <= Math.max(selStart.r, selEnd.r) &&
                              cell.c >= Math.min(selStart.c, selEnd.c) &&
                              cell.c <= Math.max(selStart.c, selEnd.c)
                            : false
                        const isSelected =
                          selectedCell?.r === cell.r && selectedCell?.c === cell.c
                        return (
                          <td
                            key={key}
                            rowSpan={cell.rowspan}
                            colSpan={cell.colspan}
                            onMouseDown={(e) => onCellMouseDown(cell.r, cell.c, e)}
                            onMouseEnter={() =>
                              mode === 'merge' && selStart && setSelEnd({ r: cell.r, c: cell.c })
                            }
                            onClick={() => mode === 'select' && setSelectedCell({ r: cell.r, c: cell.c })}
                            style={{
                              border: phantom
                                ? '1.5px dashed var(--color-sig)'
                                : '1px solid var(--color-rule-strong)',
                              background: inRange
                                ? 'var(--color-sig-wash)'
                                : isSelected
                                  ? 'var(--color-warn-wash)'
                                  : 'var(--color-sheet)',
                              minWidth: 72,
                            }}
                            title={phantom ? t('table.phantomTitle') : undefined}
                          >
                            {mode === 'edit' ? (
                              <textarea
                                ref={(el) => {
                                  textareaRefs.current[key] = el
                                }}
                                className="h-full w-full resize-none bg-transparent p-1 text-[12px] outline-none focus:bg-[color:var(--color-fill)]"
                                value={cell.text}
                                aria-label={t('table.cellAria', { r: cell.r + 1, c: cell.c + 1 })}
                                onChange={(e) => setCellText(cell.r, cell.c, e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === 'Tab' && !e.shiftKey) {
                                    const below = textareaRefs.current[textareaKey(cell.r + 1, cell.c)]
                                    if (below) {
                                      e.preventDefault()
                                      below.focus()
                                    }
                                  }
                                }}
                              />
                            ) : (
                              <div className="min-h-8 p-1 text-[12px]">{cell.text || ' '}</div>
                            )}
                          </td>
                        )
                      })}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {grid.phantom_cols.length > 0 && (
            <p className="mt-1 text-[11px] text-[color:var(--color-ink-2)]">
              <span className="mr-1 inline-block h-2.5 w-4 border-2 border-dashed border-[color:var(--color-sig)] align-middle" />
              {t('table.phantomNote')}
            </p>
          )}
        </div>
      </div>

      {otsl !== null && (
        <div className="border-t border-[color:var(--color-rule)] p-3">
          <div className="mb-1 flex items-center gap-2">
            <span className="lbl !mb-0">{t('table.otslNote')}</span>
            <button
              onClick={() => void navigator.clipboard?.writeText(otsl)}
              className="btn btn-sm ml-auto"
            >
              <IconCopy size={11} />
              {t('common.copy')}
            </button>
          </div>
          <pre className="mono max-h-28 overflow-auto border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2 text-[11px]">
            {otsl}
          </pre>
        </div>
      )}
    </Modal>
  )
}
