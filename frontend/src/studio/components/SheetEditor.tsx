import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { insertTrack, deleteTrack, mergeRange, ownerMap, splitCell } from '../../lib/grid'
import type { TableDetectOut, TableDetectRequest, TableGrid } from '../../lib/types'
import { WarnNotice } from '../../app/ui'
import { IconMinus, IconPlus, IconSave } from '../../app/icons'
import { useInference } from '../../app/inference'
import { useI18n } from '../../i18n'

interface SheetEditorProps {
  /** Griglia iniziale (dal server o dal prefill): il componente la possiede
   *  internamente; per cambiare blocco il padre lo rimonta con una `key`. */
  grid: TableGrid
  onSave: (grid: TableGrid) => Promise<string>
  /** Proposta geometrica del rilevatore: resta disponibile come prefill,
   *  ma la correzione avviene sul foglio, non su confini da trascinare. */
  onDetect?: (opts: TableDetectRequest) => Promise<TableDetectOut>
}

/** Ritardo dell'autosave del foglio: le stesse ragioni del debounce dei
 *  blocchi (700ms) ma con un po' più d'aria, perché il salvataggio tocca
 *  un endpoint dedicato (PUT /blocks/{id}/table). */
const AUTOSAVE_DELAY = 900

/** Foglio di calcolo: le celle unite del prefill (HTML/OTSL del
 *  modello o proposta geometrica) si vedono e si editano come in un vero
 *  foglio — merge reali su righe e colonne, non un'imitazione con colSpan
 *  solo orizzontale. Inserimento/eliminazione di righe e colonne restano
 *  operazioni nostre (stessa semantica di sempre sulle celle unite che
 *  attraversano il punto); Handsontable gestisce editing e navigazione da
 *  tastiera. Il salvataggio è esplicito + autosave con debounce. */
export default function SheetEditor({ grid: initialGrid, onSave, onDetect }: SheetEditorProps) {
  const { t } = useI18n()
  const inf = useInference()
  const [grid, setGrid] = useState<TableGrid>(initialGrid)
  const [saving, setSaving] = useState(false)
  const [otsl, setOtsl] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [detecting, setDetecting] = useState(false)
  const [detectFill, setDetectFill] = useState<'none' | 'ocr' | 'model'>('none')
  const [detectInfo, setDetectInfo] = useState<TableDetectOut | null>(null)
  const gridRef = useRef(grid)
  gridRef.current = grid
  const saveRef = useRef(onSave)
  saveRef.current = onSave
  const cellRefs = useRef<Record<string, HTMLTextAreaElement | null>>({})

  const doSave = async (): Promise<boolean> => {
    setSaving(true)
    try {
      setOtsl(await saveRef.current(gridRef.current))
      setNotice(null)
      return true
    } catch (e) {
      setNotice(
        e instanceof Error
          ? t('table.saveFailedWith', { msg: e.message })
          : t('table.saveFailed'),
      )
      return false
    } finally {
      setSaving(false)
    }
  }

  // Autosave con debounce: l'utente che scorre di cella in cella non deve
  // pensare a salvare, e un cambio blocco non può far perdere più di un
  // battito. L'errore resta visibile nel pannello, non scompare.
  const first = useRef(true)
  useEffect(() => {
    if (first.current) {
      first.current = false
      return
    }
    const timer = setTimeout(() => void doSave(), AUTOSAVE_DELAY)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [grid])

  const map = ownerMap(grid)

  // La cella attiva guida gli inserimenti: un foglio moderno agisce sulla
  // posizione del cursore, non solo in coda.
  const [activeCell, setActiveCell] = useState<{ r: number; c: number } | null>(null)
  const active = activeCell ?? { r: 0, c: 0 }

  const handleSelection = (row: number, col: number) => {
    const owner = ownerMap(grid)[row]?.[col]
    setActiveCell({ r: owner?.r ?? row, c: owner?.c ?? col })
  }

  const insertRowAt = (at: number) => setGrid((g) => insertTrack(g, 'row', at))
  const insertColAt = (at: number) => setGrid((g) => insertTrack(g, 'col', at))
  const mergeRight = () => {
    if (active.c >= grid.cols - 1) return
    setGrid((g) => mergeRange(g, active.r, active.c, active.r, active.c + 1) ?? g)
  }
  const mergeDown = () => {
    if (active.r >= grid.rows - 1) return
    setGrid((g) => mergeRange(g, active.r, active.c, active.r + 1, active.c) ?? g)
  }
  const splitActive = () => setGrid((g) => splitCell(g, active.r, active.c) ?? g)
  const deleteRowAt = () => {
    if (grid.rows < 2) return
    // Eliminare una riga perde il suo testo: conferma esplicita.
    if (!window.confirm(t('table.deleteTrackConfirm'))) return
    setGrid((g) => deleteTrack(g, 'row', active.r) ?? g)
    setActiveCell((previous) => previous ? ({ r: Math.min(previous.r, grid.rows - 2), c: previous.c }) : previous)
  }
  const deleteColAt = () => {
    if (grid.cols < 2) return
    if (!window.confirm(t('table.deleteTrackConfirm'))) return
    setGrid((g) => deleteTrack(g, 'col', active.c) ?? g)
    setActiveCell((previous) => previous ? ({ r: previous.r, c: Math.min(previous.c, grid.cols - 2) }) : previous)
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
      setGrid(out.grid)
      setDetectInfo(out)
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

  const phantom = grid.phantom_cols
  const focusCell = (r: number, c: number) => {
    const target = cellRefs.current[`${r}:${c}`]
    if (target) {
      target.focus()
      target.setSelectionRange(target.value.length, target.value.length)
    }
  }
  const handleCellKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>, r: number, c: number) => {
    const next = e.key === 'Enter'
      ? { r: r + 1, c }
      : e.key === 'ArrowRight' && e.currentTarget.selectionStart === e.currentTarget.value.length
        ? { r, c: c + 1 }
        : null
    if (!next || next.r >= grid.rows || next.c >= grid.cols) return
    e.preventDefault()
    focusCell(next.r, next.c)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
        <div>
          <span className="lbl">{t('table.rowsAction')}</span>
          <div className="flex items-center gap-1">
            <button onClick={() => insertRowAt(active.r)} className="btn btn-sm" aria-label={t('table.insertRowAbove')} title={t('table.insertRowAbove')}>
              <IconPlus size={11} />
            </button>
            <button onClick={() => insertRowAt(active.r + 1)} className="btn btn-sm" aria-label={t('table.insertRowBelow')} title={t('table.insertRowBelow')}>
              <IconPlus size={11} />
            </button>
            <button onClick={deleteRowAt} className="btn btn-sm" aria-label={t('table.deleteRowAt')} title={t('table.deleteRowAt')}>
              <IconMinus size={11} />
            </button>
          </div>
        </div>
        <div>
          <span className="lbl">{t('table.colsAction')}</span>
          <div className="flex items-center gap-1">
            <button onClick={() => insertColAt(active.c)} className="btn btn-sm" aria-label={t('table.insertColLeft')} title={t('table.insertColLeft')}>
              <IconPlus size={11} />
            </button>
            <button onClick={() => insertColAt(active.c + 1)} className="btn btn-sm" aria-label={t('table.insertColRight')} title={t('table.insertColRight')}>
              <IconPlus size={11} />
            </button>
            <button onClick={deleteColAt} className="btn btn-sm" aria-label={t('table.deleteColAt')} title={t('table.deleteColAt')}>
              <IconMinus size={11} />
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
                <option value="none">{t('table.detectFillNone')}</option>
                <option value="ocr">{t('table.detectFillOcr')} (CPU)</option>
                <option value="model" disabled={!inf.enabled || !inf.available}>
                  {t('table.detectFillModel')}{' '}
                  {!inf.enabled ? '(GPU)' : !inf.available ? '(—)' : `(${inf.isCloud ? 'Cloud' : 'GPU'})`}
                </option>
              </select>
            </div>
          </div>
        )}
        <div>
          <span className="lbl">{t('table.mergeAction')}</span>
          <div className="flex items-center gap-1">
            <button onClick={mergeRight} className="btn btn-sm" aria-label={t('table.mergeRight')} title={t('table.mergeRight')}>
              {t('table.mergeRightShort')}
            </button>
            <button onClick={mergeDown} className="btn btn-sm" aria-label={t('table.mergeDown')} title={t('table.mergeDown')}>
              {t('table.mergeDownShort')}
            </button>
            <button onClick={splitActive} className="btn btn-sm" aria-label={t('table.splitCell')} title={t('table.splitCell')}>
              {t('table.splitCellShort')}
            </button>
          </div>
        </div>
        <button onClick={() => void doSave()} disabled={saving} className="btn btn-primary btn-sm ml-auto">
          <IconSave size={12} />
          {saving ? t('table.saving') : t('table.saveGrid')}
        </button>
      </div>

      <p className="text-[11px] text-[color:var(--color-ink-2)]">{t('table.sheetHint')}</p>

      {notice && <WarnNotice title={t('table.notDone')}>{notice}</WarnNotice>}

      {/* Gli avvisi del rilevatore dicono che una condizione misurata della
          scansione limita la proposta: piastra d'allarme, non statistica. */}
      {detectInfo?.warnings?.includes('skewed') && (
        <WarnNotice title={t('table.detectWarnTitle')}>
          {t('table.detectWarnSkewed', {
            deg: (detectInfo.diagnostics.skew_deg ?? 0).toFixed(2),
          })}
        </WarnNotice>
      )}

      <div className="min-h-0 max-h-[60vh] flex-1 overflow-auto border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sheet)]">
        <table className="min-w-max w-full border-collapse text-[12px]">
          <tbody>
            {Array.from({ length: grid.rows }, (_, r) => (
              <tr key={r}>
                {Array.from({ length: grid.cols }, (_, c) => {
                  const cell = map[r]?.[c]
                  if (!cell || cell.r !== r || cell.c !== c) return null
                  const key = `${r}:${c}`
                  return (
                    <td key={key} rowSpan={cell.rowspan} colSpan={cell.colspan}
                      className={`align-top ${phantom.includes(c) ? 'border-2 border-dashed border-[color:var(--color-sig)]' : 'border border-[color:var(--color-rule-strong)]'}`}
                      onClick={() => handleSelection(r, c)}>
                      <textarea
                        ref={(el) => { cellRefs.current[key] = el }}
                        rows={2}
                        className="min-h-10 min-w-[96px] w-full resize-y bg-transparent p-1.5 align-top outline-none focus:bg-[color:var(--color-fill)]"
                        value={cell.text}
                        aria-label={t('table.cellAria', { r: r + 1, c: c + 1 })}
                        onChange={(e) => setGrid((g) => ({ ...g, cells: g.cells.map((item) => item.r === r && item.c === c ? { ...item, text: e.target.value } : item) }))}
                        onFocus={() => handleSelection(r, c)}
                        onKeyDown={(e) => handleCellKeyDown(e, r, c)}
                      />
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {phantom.length > 0 && (
        <p className="text-[11px] text-[color:var(--color-ink-2)]">
          <span className="mr-1 inline-block h-2.5 w-4 border-2 border-dashed border-[color:var(--color-sig)] align-middle" />
          {t('table.phantomNote')}
        </p>
      )}

      {otsl !== null && (
        <div>
          <div className="mb-1 flex items-center gap-2">
            <span className="lbl !mb-0">{t('table.otslNote')}</span>
            <button
              onClick={() => void navigator.clipboard?.writeText(otsl)}
              className="btn btn-sm ml-auto"
            >
              {t('common.copy')}
            </button>
          </div>
          <pre className="mono max-h-24 overflow-auto border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2 text-[11px]">
            {otsl}
          </pre>
        </div>
      )}
    </div>
  )
}
