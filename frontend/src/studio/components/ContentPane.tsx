/**
 * Il contenuto della pagina: **una zona sola** nel rail destro.
 *
 * Prima erano tre moduli impilati — le regole di trascrizione, i livelli, il
 * contenuto — e nessuno dei primi due mostrava quello che si è lì per
 * guardare: i dati estratti. L'elenco dei livelli era poi la stessa lista di
 * blocchi del contenuto, scritta due volte; le regole sono materiale di
 * consultazione, non un pannello che occupa il rail per sempre.
 *
 * Qui c'è una riga per blocco — numero d'ordine, ritaglio, testo o foglio —
 * e sopra, mentre il modello scrive, l'output in diretta. L'ordine di lettura
 * si governa dalle stesse righe (frecce, Alt+frecce, Canc), quindi la lista
 * resta l'equivalente DOM del canvas Konva: navigabile da tastiera e
 * leggibile da uno screen reader, che è la ragione per cui esisteva
 * l'elenco dei livelli. Le regole vivono dietro il loro pulsante.
 */
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import type {
  TableDetectOut,
  TableDetectRequest,
  TableGrid,
  TableGridOut,
} from '../../lib/types'
import {
  dropBoundary,
  emptyGrid,
  fillDown,
  insertBoundary,
  joinColumns,
  mergeKeepsEveryText,
  mergeRange,
  normalizeColumn,
  splitCell,
  transformColumnCase,
} from '../../lib/grid'
import type { Axis } from '../../lib/grid'
import type { ColumnOp, SheetSelection } from './UniverSheet'

import { univerGridSignature } from '../../lib/univerGrid'
import { apiGet, apiPost } from '../../lib/api'
import { LoadingGrid, Modal, Module, WarnNotice } from '../../app/ui'
import { IconDown, IconSave, IconTrash, IconUp } from '../../app/icons'
import { useI18n } from '../../i18n'
import type { LabelDef, TableCheck, TableChecksOut } from '../../lib/types'
import type { DisplayBlock, LivePrefillOutput, PrefillDraft } from '../types'
import JspreadsheetSheet from './JspreadsheetSheet'
import TableGridOverlay from './TableGridOverlay'
import SplitColumnDialog from './SplitColumnDialog'
import LiveStream from './LiveStream'
import ConventionsChecklist from './ConventionsChecklist'

/** Univer pesa: il preset «sheets core» con il motore di rendering e quello
 *  delle formule porta la pagina di annotazione da 892 kB a **7,4 MB** se lo si
 *  importa in cima. Si carica quando si apre la tabella, che è l'unico momento
 *  in cui serve. */
const UniverSheet = lazy(() => import('./UniverSheet'))

/** «Summation Error in column (4) ≠ column (2) + (3)»: posizioni contate
 *  da 1 come nel foglio (la colonna A è la 1). Una somma lungo una riga si
 *  dice per colonne, una lungo una colonna per righe; oltre cinque addendi si
 *  mostrano i primi due e l'ultimo. */
function sumErrorMessage(check: TableCheck, t: (key: string, vars?: Record<string, string | number>) => string) {
  const along = check.kind === 'row' ? 1 : 0
  const at = check.cells.map((cell) => `(${cell[along] + 1})`)
  const parts = at.length > 5 ? [at[0], at[1], '…', at[at.length - 1]].join(' + ') : at.join(' + ')
  return t(check.kind === 'row' ? 'table.checkSumErrorColumn' : 'table.checkSumErrorRow', {
    total: check.total[along] + 1,
    parts,
  })
}

/** Le classi che portano testo da trascrivere. Le altre (Picture, Column)
 *  entrano solo nel layout: la riga lo dichiara invece di fingere un editor. */
const NO_CONTENT_LABELS = new Set(['Picture', 'Column'])

/** Il ritaglio è servito dal bbox corrente del blocco: cambiata la regione,
 *  l'URL è lo stesso e il browser mostrerebbe l'immagine vecchia. */
const cropUrlFor = (serverId: number, version = 0) =>
  version ? `/api/blocks/${serverId}/crop?v=${version}` : `/api/blocks/${serverId}/crop`

/** La tabella non vive nel rail: nel rail ci sta la sua scheda, con il
 *  ritaglio e il comando che la apre.
 *
 *  Misurato: il rail dei contenuti è 520 px, un registro ne chiede 459 solo per
 *  le colonne. Affiancare ritaglio e foglio lì dentro lasciava al foglio metà
 *  delle colonne che gli servono; impilati, il ritaglio alto 578 px schiacciava
 *  il foglio a 101. Una tabella vuole una superficie sua: si apre a 1150 px e
 *  lì ritaglio e foglio stanno **accanto**, con lo spazio per correggere i
 *  confini sull'inchiostro. */
function TableBlockEditor({
  id,
  serverId,
  onSaveTable,
  onDetectTable,
  cropUrl,
}: {
  id: string
  serverId: number | null
  onSaveTable: (serverId: number, grid: TableGrid) => Promise<string>
  onDetectTable?: (serverId: number, opts: TableDetectRequest) => Promise<TableDetectOut>
  cropUrl: string | null
}) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)

  if (!serverId) {
    return (
      <p className="border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2 text-[11px] text-[color:var(--color-ink-2)]">
        {t('content.cropUnsaved')}
      </p>
    )
  }
  return (
    <>
      <div className="flex items-center gap-2">
        <p className="min-w-0 flex-1 text-[12px] text-[color:var(--color-ink-2)]">
          {t('content.tableInWorkspace')}
        </p>
        <button type="button" onClick={() => setOpen(true)} className="btn btn-sm btn-primary">
          {t('table.openWorkspace')}
        </button>
      </div>
      {open && (
        <Modal title={t('table.workspaceTitle')} wide onClose={() => setOpen(false)}>
          {/* La griglia si rilegge dal server all'apertura: la scheda non tiene
              una seconda copia del modello da tenere allineata. */}
          <div className="min-h-0 flex-1 p-3">
            <TableWorkspace
              id={id}
              serverId={serverId}
              cropUrl={cropUrl}
              onSaveTable={onSaveTable}
              onDetectTable={onDetectTable}
            />
          </div>
        </Modal>
      )}
    </>
  )
}

/** Superficie della griglia.
 *
 *  Univer è la scelta: il preset «sheets core» porta menù contestuale, merge,
 *  copia/incolla multi-cella, fill handle e blocco delle righe senza che li
 *  scriviamo noi. Jspreadsheet CE resta raggiungibile con una riga finché lo
 *  scambio non è chiuso su tutta la parità (i comandi «verificata» e «colonna
 *  fantasma», che sul foglio Univer non sono ancora ricablati). */
const TABLE_SURFACE: 'univer' | 'ce' = 'univer'

/** Il corpo della vista di lavoro: carica la griglia dal server, possiede i
 *  confini sull'inchiostro e monta la superficie scelta. */
function TableWorkspace({
  id,
  serverId,
  cropUrl,
  onSaveTable,
  onDetectTable,
}: {
  id: string
  serverId: number
  cropUrl: string | null
  onSaveTable: (serverId: number, grid: TableGrid) => Promise<string>
  onDetectTable?: (serverId: number, opts: TableDetectRequest) => Promise<TableDetectOut>
}) {
  const { t } = useI18n()
  const [grid, setGrid] = useState<TableGrid | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [otsl, setOtsl] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [splitAt, setSplitAt] = useState<number | null>(null)
  /** Il modello di prima dell'ultima separazione: una separazione riscrive una
   *  colonna intera e non entra nella storia di Univer, quindi il modo di
   *  tornare indietro lo diamo noi, per un passo. */
  const [undoSplit, setUndoSplit] = useState<TableGrid | null>(null)
  const gridRef = useRef<TableGrid | null>(null)
  const savedRef = useRef('')

  // Controlli aritmetici: il server rifà le somme sui valori correnti poco
  // dopo ogni modifica e indica le celle sospette. Un errore di rete toglie
  // solo l'aiuto, non blocca il foglio.
  const [checks, setChecks] = useState<TableChecksOut | null>(null)
  const [active, setActive] = useState<{ r: number; c: number } | null>(null)
  useEffect(() => {
    if (!grid) return
    let stale = false
    const timer = setTimeout(() => {
      apiPost<TableChecksOut>('/tables/checks', grid)
        .then((out) => { if (!stale) setChecks(Array.isArray(out?.checks) ? out : null) })
        .catch(() => { if (!stale) setChecks(null) })
    }, 400)
    return () => { stale = true; clearTimeout(timer) }
  }, [grid])
  const activeProblems = active
    ? (checks?.checks ?? []).filter(
        (k) => !k.ok && [...k.cells, k.total].some(([r, c]) => r === active.r && c === active.c),
      )
    : []

  useEffect(() => {
    setError(null)
    apiGet<TableGridOut>(`/blocks/${serverId}/table`)
      .then((out) => {
        const model = out.grid ?? emptyGrid(3, 4)
        gridRef.current = model
        savedRef.current = univerGridSignature(model)
        setGrid(model)
      })
      .catch(() => setError(t('table.loadFailed')))
  }, [serverId, t])

  const doSave = useCallback(
    async (model: TableGrid) => {
      setSaving(true)
      try {
        setOtsl(await onSaveTable(serverId, model))
        savedRef.current = univerGridSignature(model)
        setNotice(null)
      } catch (e) {
        // Fallito: la firma resta indietro, così il prossimo cambiamento
        // riprova. Non si riprova da soli, o un 400 diventa un ciclo.
        setNotice(
          e instanceof Error ? t('table.saveFailedWith', { msg: e.message }) : t('table.saveFailed'),
        )
      } finally {
        setSaving(false)
      }
    },
    [onSaveTable, serverId, t],
  )

  // Autosave col debounce dei blocchi. Vale per la superficie Univer, che
  // notifica il modello; il foglio CE ha la sua gestione e non si duplica.
  useEffect(() => {
    if (!grid || TABLE_SURFACE !== 'univer') return
    if (univerGridSignature(grid) === savedRef.current) return
    const timer = setTimeout(() => void doSave(grid), 900)
    return () => clearTimeout(timer)
  }, [grid, doSave])

  const applyModel = (next: TableGrid) => {
    gridRef.current = next
    setGrid(next)
  }

  const moveBoundary = (axis: Axis, index: number, value: number) => {
    const current = gridRef.current
    if (!current) return
    const values = [...((axis === 'v' ? current.vlines : current.hlines) ?? [])]
    values[index] = value
    applyModel(axis === 'v' ? { ...current, vlines: values } : { ...current, hlines: values })
  }

  const addBoundary = (axis: Axis, at: number) => {
    const current = gridRef.current
    if (!current) return
    const next = insertBoundary(current, axis, at)
    if (!next) {
      setNotice(t('table.boundaryInsertRefused'))
      return
    }
    setNotice(null)
    applyModel(next)
  }

  const rejectBoundary = (axis: Axis, index: number) => {
    const current = gridRef.current
    if (!current) return
    const next = dropBoundary(current, axis, index)
    if (!next) {
      setNotice(t('table.boundaryDropRefused'))
      return
    }
    setNotice(null)
    applyModel(next)
  }

  /** Applica la separazione scelta nel dialogo, tenendo da parte il modello di
   *  prima: è l'unico modo di tornare indietro, perché il documento di Univer
   *  si ricostruisce e la sua storia non attraversa questa modifica. */
  const applySplit = (next: TableGrid | null, parts: number) => {
    const current = gridRef.current
    setSplitAt(null)
    if (!current || !next) return
    setUndoSplit(current)
    setNotice(t('table.splitDone', { n: parts }))
    applyModel(next)
  }

  const revertSplit = () => {
    if (!undoSplit) return
    setUndoSplit(null)
    setNotice(null)
    applyModel(undoSplit)
  }

  /** Le operazioni di colonna. Quelle che non hanno niente da chiedere si
   *  applicano subito; la separazione passa dal dialogo, che serve a scegliere
   *  il separatore e a vedere l'effetto **prima** di applicarlo. */
  const runColumnOp = (op: ColumnOp, selection: SheetSelection) => {
    const current = gridRef.current
    if (!current) return
    const { startRow: from, startColumn: column, endRow: to, endColumn } = selection

    if (op === 'split') {
      setSplitAt(column)
      return
    }

    /** Riscrivere il modello dicendo quante celle sono cambiate davvero: su una
     *  colonna di cinquanta righe è l'unico riscontro che si ha. */
    const commit = (before: TableGrid, next: TableGrid) => {
      const changed = next.cells.filter((cell) => {
        const old = before.cells.find((c) => c.r === cell.r && c.c === cell.c)
        return old ? old.text !== cell.text : false
      }).length
      setNotice(t('table.columnOpDone', { n: changed }))
      applyModel(next)
    }

    if (op === 'merge') {
      if (from === to && column === endColumn) {
        setNotice(t('table.mergeNothing'))
        return
      }
      // Una fusione tiene il testo della cella in alto a sinistra e **butta**
      // quello di tutte le altre. Su un registro questo significa perdere un
      // valore restando per giunta `verified`: si rifiuta e si dice cosa fare,
      // invece di scegliere. Il caso normale — un'intestazione sopra celle
      // vuote — passa.
      if (!mergeKeepsEveryText(current, from, column, to, endColumn)) {
        setNotice(t('table.mergeWouldLose'))
        return
      }
      const merged = mergeRange(current, from, column, to, endColumn)
      if (!merged) {
        setNotice(t('table.columnOpRefused'))
        return
      }
      commit(current, merged)
      return
    }

    if (op === 'unmerge') {
      const split = splitCell(current, from, column)
      if (!split) {
        setNotice(t('table.notMerged'))
        return
      }
      commit(current, split)
      return
    }

    const before = current
    const next =
      op === 'join'
        ? joinColumns(current, column, { separator: ' ' })
        : op === 'normalize'
          ? normalizeColumn(current, column)
          : op === 'fill'
            ? // Con **una cella sola** selezionata l'intento è «questo valore
              // vale per tutto ciò che sta sotto»: si propaga fino in fondo
              // alla colonna. Con un intervallo scelto si rispetta l'intervallo.
              fillDown(current, column, from, to === from ? current.rows - 1 : to)
            : transformColumnCase(current, column, op)
    if (!next) {
      setNotice(t('table.columnOpRefused'))
      return
    }
    commit(before, next)
  }

  if (!grid) {
    return error
      ? <p className="text-[12px] text-[color:var(--color-ink-2)]">{error}</p>
      : <LoadingGrid label={t('content.tableLoading')} />
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="mono text-[11px] text-[color:var(--color-ink-2)]">
          {t('table.workspaceSummary', { rows: grid.rows, cols: grid.cols })}
        </span>
        {grid.phantom_cols.length > 0 && (
          <span className="text-[11px] text-[color:var(--color-ink-2)]">
            {t('table.phantomColumns', { n: grid.phantom_cols.length })}
          </span>
        )}
        <button
          type="button"
          onClick={() => void doSave(gridRef.current ?? grid)}
          disabled={saving}
          className="btn btn-sm btn-primary ml-auto"
        >
          <IconSave size={11} />
          {saving ? t('table.saving') : t('table.saveGrid')}
        </button>
      </div>

      {checks && checks.checks.length > 0 && (
        <p className={`text-[11px] ${checks.failed > 0 ? 'text-[color:var(--color-warn)]' : 'text-[color:var(--color-ink-2)]'}`}>
          {checks.failed > 0
            ? t('table.checksSummary', { passed: checks.passed, total: checks.checks.length, failed: checks.failed })
            : t('table.checksBalanced', { n: checks.passed })}
          {activeProblems.map((k, i) => (
            <span key={i} className="mono ml-2 text-[color:var(--color-ink)]">
              {sumErrorMessage(k, t)}
            </span>
          ))}
        </p>
      )}

      {notice && (
        <WarnNotice title={t('table.notice')}>
          <span className="flex flex-wrap items-center gap-2">
            {notice}
            {undoSplit && (
              <button type="button" onClick={revertSplit} className="btn btn-sm">
                {t('table.splitUndo')}
              </button>
            )}
          </span>
        </WarnNotice>
      )}

      <div className="flex min-h-0 flex-1 gap-3">
        {/* I confini si correggono sull'inchiostro: appartengono alla vista di
            lavoro, non alla libreria che disegna le celle. */}
        {cropUrl && (
          <figure className="m-0 w-1/2 shrink-0">
            <TableGridOverlay
              cropUrl={cropUrl}
              vlines={grid.vlines ?? []}
              hlines={grid.hlines ?? []}
              rowColumns={grid.row_columns}
              rowColumnsProven={grid.row_columns_proven}
              rows={grid.rows}
              onMove={moveBoundary}
              onInsert={addBoundary}
              onDrop={rejectBoundary}
            />
          </figure>
        )}

        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {TABLE_SURFACE === 'univer' ? (
            // La chiave segue le dimensioni: un confine aggiunto o rifiutato
            // cambia il numero di tracce, e il documento va ricostruito su
            // quello nuovo invece di restare indietro.
            <Suspense
              fallback={<LoadingGrid label={t('content.tableLoading')} rows={8} />}
            >
              <UniverSheet
                key={`${serverId}:${grid.rows}x${grid.cols}`}
                grid={grid}
                onGridChange={applyModel}
                onColumnOp={runColumnOp}
                suspects={checks?.suspects}
                onSelectionChange={(sel) => setActive({ r: sel.startRow, c: sel.startColumn })}
              />
            </Suspense>
          ) : (
            <JspreadsheetSheet
              key={`${id}:${serverId}`}
              grid={grid}
              cropUrl={cropUrl}
              withOverlay={false}
              onSave={(g) => onSaveTable(serverId, g)}
              onDetect={onDetectTable ? (opts) => onDetectTable(serverId, opts) : undefined}
            />
          )}
        </div>
      </div>

      {otsl !== null && (
        <div className="border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2">
          <div className="flex items-center justify-between">
            <span className="lbl !mb-0">{t('table.otslNote')}</span>
            <button
              type="button"
              onClick={() => void navigator.clipboard?.writeText(otsl)}
              className="btn btn-sm"
            >
              {t('common.copy')}
            </button>
          </div>
          <pre className="mono mt-1 max-h-32 overflow-auto text-[11px] whitespace-pre-wrap">{otsl}</pre>
        </div>
      )}

      {splitAt !== null && (
        <SplitColumnDialog
          grid={gridRef.current ?? grid}
          column={splitAt}
          onApply={applySplit}
          onClose={() => setSplitAt(null)}
        />
      )}
    </div>
  )
}

/** Esito del ri-rilevamento dopo un ridimensionamento del riquadro.
 *
 *  Vive accanto alla tabella e non altrove perché è di quella tabella che
 *  parla: la riga dice cosa è successo alla griglia mentre l'utente guardava
 *  l'immagine, e nel caso `stale` chiede il permesso invece di prenderselo. */
function RedetectNotice({
  state,
  message,
  onRun,
  onDismiss,
}: {
  state: 'busy' | 'done' | 'stale' | 'error'
  message?: string
  onRun: () => void
  onDismiss: () => void
}) {
  const { t } = useI18n()
  const tone =
    state === 'error'
      ? 'border-[color:var(--color-danger)] text-[color:var(--color-danger)]'
      : state === 'stale'
        ? 'border-[color:var(--color-warn)] text-[color:var(--color-warn)]'
        : 'border-[color:var(--color-rule-strong)] text-[color:var(--color-ink-2)]'
  return (
    <div className={`mb-2 flex items-center gap-2 border ${tone} bg-[color:var(--color-fill)] px-2 py-1 text-[11px]`}>
      <span className="min-w-0 flex-1">
        {state === 'busy' && t('table.redetectBusy')}
        {state === 'stale' && t('table.redetectStale')}
        {state === 'done' && (message ?? t('table.redetectDoneShort'))}
        {state === 'error' && t('table.redetectFailedWith', { msg: message ?? '' })}
      </span>
      {(state === 'stale' || state === 'error') && (
        <button type="button" onClick={onRun} className="btn btn-sm">
          {t('table.redetectRun')}
        </button>
      )}
      {state !== 'busy' && (
        <button type="button" onClick={onDismiss} className="btn btn-sm">
          {t('common.close')}
        </button>
      )}
    </div>
  )
}

interface RowShellProps {
  id: string
  /** Numero d'ordine di lettura, o `null` per una bozza non ancora sul canvas. */
  order: number | null
  label: string
  color?: string
  labels?: LabelDef[]
  onLabel?: (label: string) => void
  /** Origine automatica del contenuto, con il testo che la spiega. */
  badge?: { text: string; title: string }
  confirmed: boolean
  onConfirmed: (v: boolean) => void
  onDelete?: () => void
  deleteTitle: string
  selected: boolean
  onSelect?: () => void
  onMove?: (dir: -1 | 1) => void
  canMoveUp?: boolean
  canMoveDown?: boolean
  rowAria: string
  cropUrl: string | null
  cropAlt: string
  cropUnsavedHint: string
  wide?: boolean
  children: React.ReactNode
}

/** Guscio comune a ogni riga: numero e comandi in testata, ritaglio a
 *  sinistra, editor a destra. Un blocco confermato e una bozza di prefill
 *  condividono lo stesso guscio — cambia solo da dove arrivano dati e
 *  callback. I comandi stanno sempre nel DOM, visibili: non compaiono al
 *  passaggio del mouse (v. DESIGN.md). */
function RowShell({
  id,
  order,
  label,
  color,
  labels,
  onLabel,
  badge,
  confirmed,
  onConfirmed,
  onDelete,
  deleteTitle,
  selected,
  onSelect,
  onMove,
  canMoveUp,
  canMoveDown,
  rowAria,
  cropUrl,
  cropAlt,
  cropUnsavedHint,
  wide,
  children,
}: RowShellProps) {
  const { t } = useI18n()
  return (
    <li
      id={`content-row-${id}`}
      data-block={id}
      className={`stream-in border-b border-[color:var(--color-rule)] p-2.5 last:border-b-0 ${
        selected ? 'bg-[color:var(--color-sig-wash)]' : ''
      }`}
    >
      <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1.5">
        <button
          type="button"
          onClick={onSelect}
          aria-pressed={selected}
          aria-label={rowAria}
          onKeyDown={(e) => {
            if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
              e.preventDefault()
              onMove?.(e.key === 'ArrowUp' ? -1 : 1)
              return
            }
            if (e.key === 'Delete') {
              e.preventDefault()
              onDelete?.()
            }
          }}
          className="mono flex shrink-0 items-center gap-1.5 text-[11px] text-[color:var(--color-ink-3)]"
        >
          <span className="w-5 text-right">{order ?? '—'}</span>
          {color && (
            <span
              aria-hidden
              className="h-3 w-3 border border-[color:var(--color-rule-strong)]"
              style={{ background: color }}
            />
          )}
        </button>

        {onLabel && labels ? (
          <select
            value={label}
            onChange={(e) => onLabel(e.target.value)}
            onFocus={onSelect}
            className="fld !w-auto text-xs"
          >
            {labels.map((l) => (
              <option key={l.name} value={l.name}>
                {l.name}
              </option>
            ))}
          </select>
        ) : (
          <span className="mono text-[11px] uppercase tracking-[0.04em] text-[color:var(--color-ink-3)]">
            {label}
          </span>
        )}

        {badge && (
          <span className="badge text-[color:var(--color-ink-3)]" title={badge.title}>
            {badge.text}
          </span>
        )}

        <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-[color:var(--color-ink-2)]">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => onConfirmed(e.target.checked)}
            className="accent-[color:var(--color-sig)]"
          />
          {t('content.confirmDraft')}
        </label>

        <span className="ml-auto flex shrink-0 items-center gap-1">
          {onMove && (
            <>
              <button
                type="button"
                onClick={() => onMove(-1)}
                disabled={!canMoveUp}
                aria-label={t('layers.moveUp', { label })}
                className="p-1 text-[color:var(--color-ink-2)] hover:text-[color:var(--color-ink)] disabled:opacity-30"
              >
                <IconUp size={11} />
              </button>
              <button
                type="button"
                onClick={() => onMove(1)}
                disabled={!canMoveDown}
                aria-label={t('layers.moveDown', { label })}
                className="p-1 text-[color:var(--color-ink-2)] hover:text-[color:var(--color-ink)] disabled:opacity-30"
              >
                <IconDown size={11} />
              </button>
            </>
          )}
          {onDelete && (
            <button type="button" onClick={onDelete} title={deleteTitle} aria-label={deleteTitle} className="btn btn-sm">
              <IconTrash size={12} />
            </button>
          )}
        </span>
      </div>

      {/* Una tabella ha bisogno di tutta la larghezza della riga: un registro
          a otto colonne in un quarto di pannello mostra due colonne e mezza. Il
          suo ritaglio qui è il riferimento della scheda; il lavoro vero si fa
          nella vista di lavoro, che si apre a parte. */}
      <div className={wide ? 'flex min-w-0 flex-col gap-2' : 'flex min-w-0 items-start gap-3'}>
        <figure
          onClick={onSelect}
          className={`m-0 shrink-0 ${wide ? 'max-h-32 w-full overflow-hidden' : 'w-1/4'} ${onSelect ? 'cursor-pointer' : ''}`}
        >
          {cropUrl ? (
            <img
              src={cropUrl}
              alt={cropAlt}
              loading="lazy"
              className={`w-full border border-[color:var(--color-rule-strong)] bg-[color:var(--color-table)] object-contain ${wide ? 'max-h-32 object-top' : ''}`}
            />
          ) : (
            <p className="border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2 text-[11px] text-[color:var(--color-ink-2)]">
              {cropUnsavedHint}
            </p>
          )}
        </figure>
        <div className="min-w-0 flex-1">{children}</div>
      </div>
    </li>
  )
}

interface ContentPaneProps {
  /** Blocchi confermati e blocchi disegnati a mano: quelli che vivono anche
   *  sul canvas. */
  blocks: DisplayBlock[]
  /** Bozze del prefill non ancora verificate: vivono solo qui, mai sul
   *  canvas, finché l'utente non le conferma. */
  drafts: PrefillDraft[]
  labels: LabelDef[]
  selectedId: string | null
  onSelect: (id: string | null) => void
  onContent: (id: string, content: string) => void
  onLabel: (id: string, label: string) => void
  onConfirmed: (id: string, confirmed: boolean) => void
  onDelete: (id: string) => void
  onSaveTable: (serverId: number, grid: TableGrid) => Promise<string>
  onDetectTable?: (serverId: number, opts: TableDetectRequest) => Promise<TableDetectOut>
  /** Versione del ritaglio per blocco: cresce quando la regione cambia, e
   *  costringe griglia e immagine a rileggersi dal server. */
  tableVersions?: Record<number, number>
  /** Esito del ri-rilevamento seguito all'ultimo ridimensionamento. */
  tableRedetect?: { serverId: number; state: 'busy' | 'done' | 'stale' | 'error'; message?: string } | null
  onDismissRedetect?: () => void
  onRedetectNow?: (serverId: number) => void
  onDraftContent: (serverId: number, content: string) => void
  onDraftGrid: (serverId: number, grid: TableGrid) => void
  onSaveDraftGrid: (serverId: number, grid: TableGrid) => Promise<string>
  onDraftConfirmed: (serverId: number, confirmed: boolean) => void
  onDraftReject: (serverId: number) => void
  /** Ordine di lettura: governato dalle righe, non da un secondo elenco. */
  onMove?: (id: string, dir: -1 | 1) => void
  onReorderReset?: () => void
  colorFor?: (label: string) => string
  /** Serve solo ad aprire le regole del progetto: qui non si annota nulla. */
  projectId?: number
  liveOutput?: LivePrefillOutput | null
  /** Presente mentre il modello scrive: l'output live sta sopra la lista e i
   *  blocchi arrivano SOTTO, in diretta, senza coprire nulla. */
  working: { engine: string; startedAt: number; blocks: number; last: string | null; output?: LivePrefillOutput | null } | null
}

export default function ContentPane({
  blocks,
  drafts,
  labels,
  selectedId,
  onSelect,
  onContent,
  onLabel,
  onConfirmed,
  onDelete,
  onSaveTable,
  onDetectTable,
  tableVersions,
  tableRedetect,
  onDismissRedetect,
  onRedetectNow,
  onDraftContent,
  onDraftGrid,
  onSaveDraftGrid,
  onDraftConfirmed,
  onDraftReject,
  onMove,
  onReorderReset,
  colorFor,
  projectId,
  liveOutput,
  working,
}: ContentPaneProps) {
  const { t, tn } = useI18n()
  const [rulesOpen, setRulesOpen] = useState(false)

  // Le bozze già confermate sono già ricomparse fra i blocchi dopo il ricarico
  // dal server: qui restano solo quelle ancora da revisionare, altrimenti la
  // stessa trascrizione apparirebbe due volte.
  const confirmedServerIds = new Set(blocks.map((b) => b.serverId).filter((x): x is number => x != null))
  const pendingDrafts = drafts.filter((d) => !confirmedServerIds.has(d.serverId))

  const sortedBlocks = [...blocks].sort(
    (a, b) => (a.orderIdx ?? Number.MAX_SAFE_INTEGER) - (b.orderIdx ?? Number.MAX_SAFE_INTEGER),
  )

  // Segue la selezione fatta sul canvas: la riga corrispondente entra in vista.
  useEffect(() => {
    if (!selectedId) return
    document.getElementById(`content-row-${selectedId}`)?.scrollIntoView({ block: 'nearest' })
  }, [selectedId])

  const total = sortedBlocks.length + pendingDrafts.length
  const stream = working?.output ?? liveOutput ?? null
  const empty = total === 0

  return (
    <Module
      tab={t('content.paneTab')}
      flush
      aux={
        <>
          <span className="mono text-[11px] text-[color:var(--color-ink-3)]">
            {tn('content.blocksCount', total)}
          </span>
          {onReorderReset && !empty && (
            <button
              type="button"
              onClick={onReorderReset}
              className="btn btn-sm"
              title={t('layers.renumberTitle')}
            >
              {t('layers.renumber')}
            </button>
          )}
          {projectId != null && (
            <button type="button" onClick={() => setRulesOpen(true)} className="btn btn-sm">
              {t('content.rules')}
            </button>
          )}
        </>
      }
    >
      {(working || stream?.text) && (
        <LiveStream
          working={working}
          text={stream?.text ?? ''}
          phase={stream?.phase}
        />
      )}

      {empty ? (
        <p className="p-3 text-[12px] text-[color:var(--color-ink-2)]">{t('content.emptyPageBody')}</p>
      ) : (
        <>
          {onMove && (
            <p className="border-b border-[color:var(--color-rule)] px-2.5 py-1 text-[11px] text-[color:var(--color-ink-3)]">
              {t('layers.keys')}
            </p>
          )}
          <ul className="flex flex-col">
            {sortedBlocks.map((block, i) => {
              const isTable = block.label === 'Table'
              const noContent = NO_CONTENT_LABELS.has(block.label)
              return (
                <RowShell
                  key={block.id}
                  id={block.id}
                  order={block.orderIdx ?? i}
                  label={block.label}
                  color={colorFor?.(block.label)}
                  labels={labels}
                  onLabel={(label) => onLabel(block.id, label)}
                  badge={
                    block.prefill
                      ? { text: 'OCR', title: t('layers.prefillTitle', { source: block.prefill }) }
                      : undefined
                  }
                  confirmed={block.confirmed}
                  onConfirmed={(v) => onConfirmed(block.id, v)}
                  onDelete={() => onDelete(block.id)}
                  deleteTitle={t('inspector.deleteBlock')}
                  selected={selectedId === block.id}
                  onSelect={() => onSelect(block.id)}
                  onMove={onMove ? (dir) => onMove(block.id, dir) : undefined}
                  canMoveUp={i > 0}
                  canMoveDown={i < sortedBlocks.length - 1}
                  rowAria={t('layers.blockAria', { n: i + 1, label: block.label })}
                  cropUrl={block.serverId ? cropUrlFor(block.serverId, tableVersions?.[block.serverId] ?? 0) : null}
                  cropAlt={t('content.cropAlt', { label: block.label })}
                  cropUnsavedHint={t('content.cropUnsaved')}
                  wide={isTable}
                >
                  {isTable ? (
                    <>
                      {tableRedetect && tableRedetect.serverId === block.serverId && (
                        <RedetectNotice
                          state={tableRedetect.state}
                          message={tableRedetect.message}
                          onRun={() => onRedetectNow?.(tableRedetect.serverId)}
                          onDismiss={() => onDismissRedetect?.()}
                        />
                      )}
                      <TableBlockEditor
                        id={block.id}
                        serverId={block.serverId}
                        cropUrl={block.serverId ? cropUrlFor(block.serverId, tableVersions?.[block.serverId] ?? 0) : null}
                        onSaveTable={onSaveTable}
                        onDetectTable={onDetectTable}
                      />
                    </>
                  ) : noContent ? (
                    <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('content.noContentBody')}</p>
                  ) : (
                    <textarea
                      value={block.content}
                      onChange={(e) => onContent(block.id, e.target.value)}
                      onFocus={() => onSelect(block.id)}
                      rows={Math.min(10, Math.max(3, block.content.split('\n').length + 1))}
                      placeholder={t('inspector.transcriptionPlaceholder')}
                      aria-label={t('content.blockAria', { label: block.label })}
                      className="fld resize-y font-mono text-[12px] leading-relaxed"
                    />
                  )}
                </RowShell>
              )
            })}

            {pendingDrafts.map((draft) => {
              const isTable = draft.label === 'Table'
              const noContent = NO_CONTENT_LABELS.has(draft.label)
              return (
                <RowShell
                  key={`draft-${draft.serverId}`}
                  id={`draft-${draft.serverId}`}
                  order={null}
                  label={draft.label}
                  badge={{ text: 'OCR', title: t('content.prefillDraftBody') }}
                  confirmed={draft.confirmed}
                  onConfirmed={(v) => onDraftConfirmed(draft.serverId, v)}
                  onDelete={() => onDraftReject(draft.serverId)}
                  deleteTitle={t('content.rejectDraft')}
                  selected={false}
                  rowAria={t('content.draftAria', { label: draft.label })}
                  cropUrl={cropUrlFor(draft.serverId)}
                  cropAlt={t('content.cropAlt', { label: draft.label })}
                  cropUnsavedHint={t('content.cropUnsaved')}
                  wide={isTable}
                >
                  {isTable ? (
                    <TableBlockEditor
                      id={`draft-${draft.serverId}`}
                      serverId={draft.serverId}
                      cropUrl={cropUrlFor(draft.serverId)}
                      onSaveTable={(serverId, grid) => {
                        onDraftGrid(serverId, grid)
                        return onSaveDraftGrid(serverId, grid)
                      }}
                    />
                  ) : noContent ? (
                    <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('content.noContentBody')}</p>
                  ) : (
                    <textarea
                      value={draft.content}
                      onChange={(e) => onDraftContent(draft.serverId, e.target.value)}
                      rows={Math.min(10, Math.max(3, draft.content.split('\n').length + 1))}
                      aria-label={t('content.draftAria', { label: draft.label })}
                      className="fld resize-y font-mono text-[12px] leading-relaxed"
                    />
                  )}
                </RowShell>
              )
            })}
          </ul>
        </>
      )}

      {rulesOpen && projectId != null && (
        <Modal title={t('conventions.tab')} onClose={() => setRulesOpen(false)}>
          <div className="p-3">
            <ConventionsChecklist projectId={projectId} />
          </div>
        </Modal>
      )}
    </Module>
  )
}
