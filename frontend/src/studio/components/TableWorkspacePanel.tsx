import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import type { MutableRefObject } from 'react'
import type { HeaderTemplate, KnownTableHeader, KnownTableHeadersOut, TableCell, TableGrid, TableGridOut, TableCheck, TableChecksOut } from '../../lib/types'
import { applyHeaderTemplate, deleteTrack, emptyGrid, extractHeaderTemplate, fillDown, insertTrack, joinColumns, mergeKeepsEveryText, mergeRange, normalizeColumn, fractionsColumn, splitCell, transformColumnCase } from '../../lib/grid'
import type { ColumnOp, SheetSelection, TrackOp, UniverController } from './UniverSheet'
import { useConfirm } from '../../app/confirm'
import { univerGridSignature } from '../../lib/univerGrid'
import { apiGet, apiPost } from '../../lib/api'
import { LoadingGrid, Menu, WarnNotice } from '../../app/ui'
import { IconRedo, IconSave, IconTable, IconUndo } from '../../app/icons'
import { useI18n } from '../../i18n'
import SplitColumnDialog from './SplitColumnDialog'
import TablePageImage from './TablePageImage'
import Splitter from './Splitter'
import KnownHeaderDialog from './KnownHeaderDialog'
import { API_BASE } from '../../lib/endpoints'
const UniverSheet = lazy(() => import('./UniverSheet'))

function sumErrorMessage(check: TableCheck, t: (key: string, vars?: Record<string, string | number>) => string) {
  const along = check.kind === 'row' ? 1 : 0
  const at = check.cells.map((cell) => `(${cell[along] + 1})`)
  const parts = at.length > 5 ? [at[0], at[1], '…', at[at.length - 1]].join(' + ') : at.join(' + ')
  return t(check.kind === 'row' ? 'table.checkSumErrorColumn' : 'table.checkSumErrorRow', {
    total: check.total[along] + 1,
    parts,
  })
}

/** Celle con testo confermate, sul totale di quelle che si possono confermare. */
function verifiedCount(grid: TableGrid): { n: number; total: number } {
  const filled = grid.cells.filter((cell) => (cell.text ?? '').trim() !== '')
  return { n: filled.filter((cell) => cell.verified).length, total: filled.length }
}
// Un gesto di modifica coinvolge più comandi di Univer (apertura, conferma,
// eventuale conversione): restano un solo passo finché riguardano le stesse
// celle. La finestra copre anche una digitazione non brevissima.
const CELL_COALESCE_MS = 5000

/** Chiave delle posizioni cambiate: più comandi prodotti dallo stesso edit
 *  sulla stessa cella entrano in un solo passo di undo. */
function changedCellKey(before: TableGrid, after: TableGrid): string | null {
  const beforeCells = new Map(before.cells.map((cell) => [`${cell.r}:${cell.c}`, cell]))
  const afterCells = new Map(after.cells.map((cell) => [`${cell.r}:${cell.c}`, cell]))
  const positions = new Set([...beforeCells.keys(), ...afterCells.keys()])
  const changed: string[] = []
  for (const position of positions) {
    const a = beforeCells.get(position)
    const b = afterCells.get(position)
    const shape = (cell: typeof a) => cell && [
      cell.text ?? '', cell.rowspan, cell.colspan, cell.source ?? '',
      Boolean(cell.verified), cell.verification_source ?? '',
    ]
    if (JSON.stringify(shape(a)) !== JSON.stringify(shape(b))) changed.push(position)
  }
  return changed.length > 0 ? `${before.rows}x${before.cols}:${changed.sort().join(',')}` : null
}

/** Contenuto della tabella senza i metadati di revisione della cella. Serve a
 *  riconoscere gli stati intermedi che Univer crea durante un singolo edit:
 *  stesso testo e stessa geometria, ma `verified` già spento. */
function gridContentSignature(grid: TableGrid): string {
  return JSON.stringify([
    grid.rows,
    grid.cols,
    grid.header_rows ?? 0,
    grid.phantom_cols,
    grid.cells.map((cell) => [cell.r, cell.c, cell.rowspan, cell.colspan, cell.text ?? '']),
  ])
}

const cellPosition = (cell: TableCell) => `${cell.r}:${cell.c}`

/** Mantiene l'ultima forma verificata di ogni cella. Un edit di testo rende la
 *  cella non verificata, ma non deve cancellare la verità precedente: serve a
 *  ripristinarla quando Ctrl+Z torna esattamente a quel testo. */
function updateVerifiedCache(cache: Map<string, TableCell>, before: TableGrid, after: TableGrid) {
  const previous = new Map(before.cells.map((cell) => [cellPosition(cell), cell]))
  for (const cell of after.cells) {
    const old = previous.get(cellPosition(cell))
    if (cell.verified) cache.set(cellPosition(cell), cell)
    else if (old?.verified && (old.text ?? '') === (cell.text ?? '')) cache.delete(cellPosition(cell))
  }
}

function restoreVerifiedMetadata(grid: TableGrid, cache: Map<string, TableCell>): TableGrid {
  let changed = false
  const cells = grid.cells.map((cell) => {
    if (cell.verified || (cell.text ?? '').trim() === '') return cell
    const verified = cache.get(cellPosition(cell))
    if (!verified?.verified || (verified.text ?? '') !== (cell.text ?? '')) return cell
    changed = true
    return {
      ...cell,
      source: verified.source,
      verified: true,
      verification_source: verified.verification_source,
    }
  })
  return changed ? { ...grid, cells } : grid
}

function columnName(column: number): string {
  let value = column + 1
  let out = ''
  while (value > 0) {
    value -= 1
    out = String.fromCharCode(65 + (value % 26)) + out
    value = Math.floor(value / 26)
  }
  return out
}

function selectionLabel(selection: SheetSelection): string {
  const start = `${columnName(selection.startColumn)}${selection.startRow + 1}`
  const end = `${columnName(selection.endColumn)}${selection.endRow + 1}`
  return start === end ? start : `${start}:${end}`
}

/** Il corpo della vista di lavoro: carica la griglia dal server, possiede i
 *  confini sull'inchiostro e monta la superficie scelta. */
export function TableWorkspacePanel({
  serverId,
  projectId,
  version = 0,
  pageImageUrl,
  pageId,
  pageImageSize,
  onSaveTable,
  flushRef,
}: {
  serverId: number
  /** Libreria di intestazioni note: vive nel progetto, non nel browser. */
  projectId: number | null
  /** Versione esterna della griglia. Un aggiornamento ricarica i dati nello
   *  stesso pannello, senza smontarlo. */
  version?: number
  pageImageUrl: string | null
  pageId: number | null
  pageImageSize: { w: number; h: number } | null
  onSaveTable: (serverId: number, grid: TableGrid) => Promise<string>
  /** Riceve una funzione che salva subito ciò che l'autosave non ha ancora
   *  salvato: chi chiude la vista (la seconda finestra) la chiama prima. */
  flushRef?: MutableRefObject<(() => Promise<void>) | null>
}) {
  const { t } = useI18n()
  const confirm = useConfirm()
  const [grid, setGrid] = useState<TableGrid | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [knownHeaders, setKnownHeaders] = useState<KnownTableHeader[]>([])
  const [headerDialog, setHeaderDialog] = useState<{
    mode: 'save' | 'apply'
    selection: SheetSelection
    template?: HeaderTemplate
  } | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveVersion, setSaveVersion] = useState(0)
  const [showPage, setShowPage] = useState(false)
  const [imageWidth, setImageWidth] = useState(45)
  const saveQueue = useRef<Promise<void>>(Promise.resolve())
  const pendingSave = useRef<{ signature: string; promise: Promise<void> } | null>(null)
  const workspace = useRef<HTMLDivElement>(null)
  const [workspaceWidth, setWorkspaceWidth] = useState(800)
  useEffect(() => {
    if (!grid || !workspace.current) return
    const node = workspace.current
    const resize = () => setWorkspaceWidth(node.clientWidth || 800)
    resize()
    const observer = new ResizeObserver(resize)
    observer.observe(node)
    return () => observer.disconnect()
  }, [!!grid])

  const [sheetFallbackRevision, setSheetFallbackRevision] = useState(0)
  const [splitAt, setSplitAt] = useState<number | null>(null)
  /** La cronologia è una sequenza di snapshot completi: testo, merge e
   *  metadati `source`/`verified` tornano indietro insieme. Univer riceve ogni
   *  snapshot senza essere rimontato, quindi scroll e selezione restano dove
   *  sono. */
  const historyPast = useRef<TableGrid[]>([])
  const historyFuture = useRef<TableGrid[]>([])
  const sheetEditBase = useRef<TableGrid | null>(null)
  const lastSheetChange = useRef<{ key: string; at: number } | null>(null)
  const verifiedCache = useRef(new Map<string, TableCell>())
  const [sheetController, setSheetController] = useState<UniverController | null>(null)
  const gridRef = useRef<TableGrid | null>(null)
  const savedRef = useRef('')

  useEffect(() => {
    if (!projectId) {
      setKnownHeaders([])
      return
    }
    let stale = false
    apiGet<KnownTableHeadersOut>(`/projects/${projectId}/table-headers`)
      .then((out) => { if (!stale) setKnownHeaders(out.items ?? []) })
      .catch(() => { if (!stale) setKnownHeaders([]) })
    return () => { stale = true }
  }, [projectId])

  // Controlli aritmetici: il server rifà le somme sui valori correnti poco
  // dopo ogni modifica e indica le celle sospette. Un errore di rete toglie
  // solo l'aiuto, non blocca il foglio.
  const [checks, setChecks] = useState<TableChecksOut | null>(null)
  const [active, setActive] = useState<{ r: number; c: number } | null>(null)
  const [selection, setSelection] = useState<SheetSelection>({ startRow: 0, startColumn: 0, endRow: 0, endColumn: 0 })
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
  /** Somma confrontata con la scansione: la stampa sbaglia, la trascrizione
   *  resta com'è e la somma smette di contare come errore. */
  const toggleMisprint = (key: string, on: boolean) => {
    const current = gridRef.current
    if (!current) return
    const rest = (current.misprints ?? []).filter((k) => k !== key)
    commitModelChange({ ...current, misprints: on ? [...rest, key] : rest }, false)
  }

  useEffect(() => {
    setError(null)
    apiGet<TableGridOut>(`/blocks/${serverId}/table`)
      .then((out) => {
        const model = out.grid ?? emptyGrid(3, 4)
        historyPast.current = []
        historyFuture.current = []
        sheetEditBase.current = null
        lastSheetChange.current = null
        verifiedCache.current = new Map(model.cells.filter((cell) => cell.verified).map((cell) => [cellPosition(cell), cell]))
        gridRef.current = model
        savedRef.current = univerGridSignature(model)
        setGrid(model)
      })
      .catch(() => setError(t('table.loadFailed')))
  }, [serverId, t, version])

  // Tutte le scritture passano dalla stessa coda: una risposta vecchia non
  // può sovrascrivere la correzione nuova. Flush attende anche i salvataggi in volo.
  const doSave = useCallback((model: TableGrid): Promise<void> => {
    const signature = univerGridSignature(model)
    if (pendingSave.current?.signature === signature) return pendingSave.current.promise
    const operation = saveQueue.current.catch(() => {}).then(async () => {
      setSaving(true)
      try {
        await onSaveTable(serverId, model)
        savedRef.current = signature
        setSaveVersion((v) => v + 1)
        setNotice(null)
      } catch (error) {
        setNotice(error instanceof Error ? t('table.saveFailedWith', { msg: error.message }) : t('table.saveFailed'))
        throw error
      } finally {
        if (pendingSave.current?.promise === operation) pendingSave.current = null
        setSaving(false)
      }
    })
    pendingSave.current = { signature, promise: operation }
    saveQueue.current = operation
    return operation
  }, [onSaveTable, serverId, t])

  // Autosave col debounce dei blocchi. Vale per la superficie Univer, che
  // notifica il modello dopo ogni modifica effettiva.
  useEffect(() => {
    if (!grid) return
    if (univerGridSignature(grid) === savedRef.current) return
    const timer = setTimeout(() => void doSave(grid).catch(() => {}), 900)
    return () => clearTimeout(timer)
  }, [grid, doSave])

  // L'autosave aspetta 900 ms: chi esce prima non deve perdere l'ultima
  // modifica. `flush` la salva subito; si chiama uscendo dalla vista (dialogo
  // chiuso, link, seconda finestra chiusa dal suo pulsante), e chiudere la
  // finestra con modifiche ancora in volo chiede conferma.
  const dirty = () =>
    !!gridRef.current && univerGridSignature(gridRef.current) !== savedRef.current
  const flushLatest = useRef(async () => {})
  flushLatest.current = async () => {
    await saveQueue.current.catch(() => {})
    while (dirty()) await doSave(gridRef.current!)
  }
  useEffect(() => {
    if (flushRef) flushRef.current = () => flushLatest.current()
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (dirty()) e.preventDefault()
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload)
      if (flushRef) flushRef.current = null
      void flushLatest.current().catch(() => {})
    }
  }, [flushRef])

  const applyModel = (next: TableGrid) => {
    gridRef.current = next
    setGrid(next)
  }

  const remember = (current: TableGrid) => {
    historyPast.current = [...historyPast.current.slice(-49), current]
    historyFuture.current = []
  }

  /** Porta un modello completo nel foglio senza rimontarlo. Se il controller
   *  non è ancora pronto si usa il remount come ripiego, ma è l'eccezione: il
   *  lavoro normale conserva vista e selezione. */
  const applySnapshot = (next: TableGrid) => {
    const applied = sheetController?.applyGrid(next) ?? false
    if (!applied) setSheetFallbackRevision((revision) => revision + 1)
    applyModel(next)
  }

  /** Una modifica nata nel foglio è già visibile in Univer: qui si registra
   *  solo lo snapshot precedente per la cronologia comune. */
  const commitFromSheet = (next: TableGrid, beforeEdit?: TableGrid) => {
    const current = gridRef.current
    if (!current || univerGridSignature(next) === univerGridSignature(current)) return
    const key = changedCellKey(current, next)
    const last = lastSheetChange.current
    const coalesce = Boolean(key && last && last.key === key && Date.now() - last.at < CELL_COALESCE_MS)
    if (!coalesce && beforeEdit) {
      if (sheetEditBase.current !== beforeEdit) {
        remember(beforeEdit)
        sheetEditBase.current = beforeEdit
      }
    } else if (!coalesce) {
      sheetEditBase.current = null
      remember(current)
    }
    if (key) lastSheetChange.current = { key, at: Date.now() }
    updateVerifiedCache(verifiedCache.current, current, next)
    applyModel(next)
  }

  /** Modifica calcolata fuori da Univer (merge, split, colonne, righe). */
  const commitModelChange = (next: TableGrid, updateSheet = true) => {
    const current = gridRef.current
    if (!current || univerGridSignature(next) === univerGridSignature(current)) return
    sheetEditBase.current = null
    lastSheetChange.current = null
    remember(current)
    updateVerifiedCache(verifiedCache.current, current, next)
    if (updateSheet) applySnapshot(next)
    else applyModel(next)
  }

  /** Applica la separazione scelta nel dialogo senza ricaricare la vista. */
  const applySplit = (next: TableGrid | null, parts: number, mode: 'columns' | 'headerRows' = 'columns') => {
    const current = gridRef.current
    setSplitAt(null)
    if (!current || !next) return
    void parts
    void mode
    // Operazione riuscita: il risultato è già nel foglio. Un banner verde o
    // giallo che dice soltanto «fatto» aggiunge rumore e sposta l'attenzione.
    setNotice(null)
    commitModelChange(next)
  }

  const undo = () => {
    const current = gridRef.current
    let previous = historyPast.current.pop()
    if (!current || !previous) return
    const currentContent = gridContentSignature(current)
    let previousContent = gridContentSignature(previous)
    // Se l'undo sta tornando da un testo modificato a quello originale, salta
    // gli stati intermedi che hanno già il testo originale ma conservano il
    // flag «non verificata» prodotto dall'apertura dell'editor.
    if (previousContent !== currentContent) {
      while (historyPast.current.length > 0) {
        const older = historyPast.current[historyPast.current.length - 1]
        if (gridContentSignature(older) !== previousContent) break
        previous = historyPast.current.pop()!
        previousContent = gridContentSignature(previous)
      }
    }
    sheetEditBase.current = null
    lastSheetChange.current = null
    historyFuture.current.push(current)
    setNotice(null)
    applySnapshot(restoreVerifiedMetadata(previous, verifiedCache.current))
  }

  const redo = () => {
    const current = gridRef.current
    const next = historyFuture.current.pop()
    if (!current || !next) return
    sheetEditBase.current = null
    lastSheetChange.current = null
    historyPast.current.push(current)
    setNotice(null)
    applySnapshot(next)
  }

  const canUndo = historyPast.current.length > 0
  const canRedo = historyFuture.current.length > 0

  const openSaveHeader = (selection: SheetSelection) => {
    const current = gridRef.current
    if (!projectId || !current) return
    const template = extractHeaderTemplate(current, selection)
    if (!template) {
      setNotice(t('knownHeader.selectionInvalid'))
      return
    }
    setNotice(null)
    setHeaderDialog({ mode: 'save', selection, template })
  }

  const openApplyHeader = (selection: SheetSelection) => {
    if (!projectId) return
    setNotice(null)
    setHeaderDialog({ mode: 'apply', selection })
  }

  const applyKnownHeader = (header: KnownTableHeader): string | null => {
    const current = gridRef.current
    if (!current || !headerDialog) return t('knownHeader.applyInvalid')
    const next = applyHeaderTemplate(current, header, headerDialog.selection)
    if (!next) return t('knownHeader.applyInvalid')
    commitModelChange(next)
    return null
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
      void before
      setNotice(null)
      commitModelChange(next)
    }

    if (op === 'merge') {
      if (from === to && column === endColumn) {
        setNotice(t('table.mergeNothing'))
        return
      }
      // Una fusione non può tenere più valori diversi. Su un registro questo
      // significherebbe perdere un valore restando per giunta `verified`: si
      // rifiuta e si dice cosa fare, invece di scegliere. Un solo valore passa
      // anche se sta sotto l'angolo vuoto (caso tipico delle testate a due righe).
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
          : op === 'fractions'
            ? fractionsColumn(current, column)
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

  /** Riga o colonna: si aggiunge o si toglie una traccia.
   *
   *  La struttura nuova la calcola `lib/grid.ts` (`insertTrack`/`deleteTrack`),
   *  che sa cosa succede a una cella unita che attraversa il punto e come seguono
   *  `vlines`/`hlines`/`phantom_cols`; il foglio riceve il nuovo snapshot in
   *  place. È il gesto con cui si corregge un'intestazione o una colonna che il
   *  modello ha mancato. */
  const runTrackOp = async (op: TrackOp, selection: SheetSelection) => {
    const current = gridRef.current
    if (!current) return
    const { startRow, startColumn, endRow, endColumn } = selection
    if (op === 'deleteRow' || op === 'deleteCol') {
      const ok = await confirm({
        title: t('table.deleteTrackConfirm'),
        message: t('table.deleteTrackMessage'),
      })
      if (!ok) return
    }
    const next =
      op === 'insertRowAbove'
        ? insertTrack(current, 'row', startRow)
        : op === 'insertRowBelow'
          ? insertTrack(current, 'row', endRow + 1)
          : op === 'insertColLeft'
            ? insertTrack(current, 'col', startColumn)
            : op === 'insertColRight'
              ? insertTrack(current, 'col', endColumn + 1)
              : op === 'deleteRow'
                ? deleteTrack(current, 'row', startRow)
                : deleteTrack(current, 'col', startColumn)
    if (!next) {
      setNotice(t('table.columnOpRefused'))
      return
    }
    // Una traccia è una modifica strutturale e entra nella stessa cronologia
    // delle correzioni di cella, senza rimontare il foglio.
    setNotice(null)
    commitModelChange(next)
  }

  if (error && !grid) return <p className="text-[12px] text-[color:var(--color-ink-2)]">{error}</p>
  if (!grid) {
    // La forma della vista che sta arrivando — barra, azioni, pagina e foglio —
    // così all'arrivo dei dati niente salta: ogni pezzo compare al suo posto.
    return (
      <div className="flex min-h-0 flex-1 flex-col gap-2" role="status" aria-label={t('content.tableLoading')}>
        <span className="load-cell h-7 w-full rounded-[var(--radius-md)]" />
        <span className="load-cell h-7 w-3/5 rounded-[var(--radius-md)]" />
        <div className="flex min-h-0 flex-1 gap-3">
          {pageImageUrl && <span className="load-cell min-h-[320px] flex-1 rounded-[var(--radius-md)]" />}
          <div className="min-w-0 flex-1">
            <LoadingGrid label={t('content.tableLoading')} rows={10} />
          </div>
        </div>
      </div>
    )
  }
  const verified = verifiedCount(grid)
  const isDirty = dirty()
  const saveLabel = saving ? t('table.saving') : isDirty ? t('table.saveGrid') : t('table.saved')
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div
        role="toolbar"
        aria-label={t('table.workspaceTools')}
        className="flex min-h-9 flex-wrap items-center gap-1.5 border-b border-[color:var(--color-rule)] pb-1.5 text-[11px] text-[color:var(--color-ink-2)]"
      >
        <span className="mono px-1 text-[color:var(--color-ink)]" title={t('table.sheetSelection')}>
          {selectionLabel(selection)}
        </span>
        <span aria-hidden="true" className="h-4 w-px bg-[color:var(--color-rule)]" />
        <span
          className="inline-flex items-center gap-1 px-1"
          title={`${t('table.verifiedCount', verified)} — ${t('table.verifyHint')}`}
        >
          <span aria-hidden="true" className="text-[color:var(--color-ok)]">✓</span>
          <span className="mono text-[color:var(--color-ink)]">{verified.n}/{verified.total}</span>
          <span className="sr-only">{t('table.verifiedShort')}</span>
        </span>
        {checks && checks.failed > 0 && (
          <span
            className="inline-flex items-center gap-1 px-1 font-semibold text-[color:var(--color-warn)]"
            title={
              t('table.checksSummary', { passed: checks.passed, total: checks.checks.length, failed: checks.failed }) +
              ((checks.misprints ?? 0) > 0 ? ` ${t('table.misprintsCount', { n: checks.misprints ?? 0 })}` : '')
            }
          >
            Σ <span className="mono">{checks.passed}/{checks.checks.length}</span>
          </span>
        )}

        <div className="ml-auto flex items-center gap-1">
          <Menu label={<><IconTable size={12} />{t('table.structure')}</>} className="z-20">
            <p className="px-2 py-1 text-[11px] text-[color:var(--color-ink-3)]">
              {t('table.workspaceSummary', { rows: grid.rows, cols: grid.cols })}
              {grid.phantom_cols.length > 0 ? ` · ${t('table.phantomColumns', { n: grid.phantom_cols.length })}` : ''}
            </p>
            <label className="menu-item justify-between" title={t('table.headerRowsTitle')}>
              <span>{t('table.headerShort')}</span>
              <input
                type="number"
                min={0}
                max={Math.min(grid.rows, 20)}
                value={grid.header_rows ?? 0}
                onChange={(event) => {
                  const n = Math.max(0, Math.min(Math.min(grid.rows, 20), Number(event.target.value) || 0))
                  if (n === (grid.header_rows ?? 0)) return
                  commitModelChange({ ...grid, header_rows: n })
                }}
                className="w-12 rounded-[var(--radius-sm)] border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] px-1 py-0.5 text-[11px]"
              />
            </label>
            {projectId != null && (
              <>
                <button type="button" className="menu-item" onClick={() => openSaveHeader(selection)}>
                  {t('knownHeader.saveMenu')}
                </button>
                <button type="button" className="menu-item flex items-center justify-between gap-3" onClick={() => openApplyHeader(selection)}>
                  <span>{t('knownHeader.applyMenu')}</span>
                  {knownHeaders.length > 0 && <span className="mono text-[color:var(--color-ink-3)]">{knownHeaders.length}</span>}
                </button>
              </>
            )}
            <hr className="menu-sep" />
            <button type="button" className="menu-item" onClick={() => runColumnOp('split', selection)}>
              {t('table.splitTitle')}
            </button>
            <label className="flex flex-col gap-1 px-2 py-1 text-[11px] text-[color:var(--color-ink-3)]">
              <span>{t('table.quickActions')}</span>
              <select
                aria-label={t('table.quickActions')}
                title={t('table.quickActionsHint')}
                value=""
                className="fld !w-full"
                onChange={(event) => {
                  if (event.target.value) runColumnOp(event.target.value as ColumnOp, selection)
                }}
              >
                <option value="" disabled>{t('table.quickActions')}</option>
                {(['normalize', 'fill', 'join', 'upper', 'lower', 'fractions', 'merge', 'unmerge'] as const).map((op) => (
                  <option key={op} value={op}>{op === 'fractions' ? t('table.toFractionsShort') : op === 'merge' ? t('table.modeMerge') : op === 'unmerge' ? t('table.splitCell') : t(`table.${op}Short` as const)}</option>
                ))}
              </select>
            </label>
          </Menu>
          <span aria-hidden="true" className="mx-0.5 h-5 w-px bg-[color:var(--color-rule)]" />
          <button
            type="button"
            className="btn btn-sm !px-2"
            title={t('annotate.undoTitle')}
            aria-label={t('annotate.undoTitle')}
            disabled={!canUndo}
            onClick={undo}
          >
            <IconUndo size={12} />
          </button>
          <button
            type="button"
            className="btn btn-sm !px-2"
            title={t('annotate.redoTitle')}
            aria-label={t('annotate.redoTitle')}
            disabled={!canRedo}
            onClick={redo}
          >
            <IconRedo size={12} />
          </button>
          <button
            type="button"
            onClick={() => void doSave(gridRef.current ?? grid).catch(() => {})}
            disabled={saving || !isDirty}
            className={`btn btn-sm ${isDirty ? 'btn-primary' : ''}`}
            title={isDirty ? t('table.unsaved') : saveLabel}
            data-save-version={saveVersion}
          >
            <IconSave size={11} />
            {saveLabel}
          </button>
        </div>
      </div>

      {/* Le somme che non tornano e toccano la cella selezionata, con il loro
          comando «refuso»: servono per agire, quindi restano in una riga. Il
          resto dello stato delle somme è il Σ qui sopra. */}
      {activeProblems.length > 0 && (
        <p className="text-[11px] text-[color:var(--color-warn)]">
          {activeProblems.map((k, i) => (
            <span key={i} className="mr-2 inline-flex items-center gap-1">
              <span className={`mono ${k.misprint ? 'text-[color:var(--color-ink-2)] line-through' : 'text-[color:var(--color-ink)]'}`}>
                {sumErrorMessage(k, t)}
              </span>
              {k.key && (
                <button
                  type="button"
                  className="btn btn-sm"
                  title={t('table.misprintHint')}
                  onClick={() => toggleMisprint(k.key!, !k.misprint)}
                >
                  {k.misprint ? t('table.misprintUndo') : t('table.misprintMark')}
                </button>
              )}
            </span>
          ))}
        </p>
      )}

      {notice && (
        <WarnNotice title={t('table.notice')}>
          <span className="flex flex-wrap items-center gap-2">{notice}</span>
        </WarnNotice>
      )}


      <div ref={workspace} className="flex min-h-0 flex-1 gap-2">
        <div className="flex min-h-0 min-w-0 flex-col" style={{ flex: `0 0 ${imageWidth}%` }}>
          <TablePageImage
            key={showPage ? 'page' : 'crop'}
            src={showPage && pageImageUrl ? pageImageUrl : `${API_BASE}/blocks/${serverId}/crop`}
            pageId={showPage ? pageId : null}
            imageSize={showPage ? pageImageSize : null}
            mode={showPage ? 'page' : 'crop'}
            onModeChange={pageImageUrl ? (next) => setShowPage(next === 'page') : undefined}
          />
        </div>
        <Splitter value={workspaceWidth * imageWidth / 100} min={workspaceWidth * 0.25} max={workspaceWidth * 0.65} side="left" label={t('table.imageWidth')} onChange={(width) => setImageWidth(width / workspaceWidth * 100)} onReset={() => setImageWidth(45)} />
        <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
          <Suspense fallback={<LoadingGrid label={t('content.tableLoading')} rows={10} />}>
              <UniverSheet
                key={`${serverId}:${version}:${sheetFallbackRevision}`}
                grid={grid}
                onGridChange={commitFromSheet}
                onColumnOp={runColumnOp}
                onTrackOp={runTrackOp}
                onSaveHeader={projectId != null ? openSaveHeader : undefined}
                onApplyHeader={projectId != null ? openApplyHeader : undefined}
                suspects={checks?.suspects}
                onControllerChange={setSheetController}
                onUndo={undo}
                onRedo={redo}
                onSelectionChange={(sel) => {
                  setActive({ r: sel.startRow, c: sel.startColumn })
                  setSelection(sel)
                }}
                intro
              />
          </Suspense>
        </div>
      </div>

      {splitAt !== null && (
        <SplitColumnDialog
          grid={gridRef.current ?? grid}
          column={splitAt}
          onApply={applySplit}
          onClose={() => setSplitAt(null)}
        />
      )}

      {headerDialog && projectId != null && (
        <KnownHeaderDialog
          projectId={projectId}
          mode={headerDialog.mode}
          template={headerDialog.template}
          headers={knownHeaders}
          onHeadersChange={setKnownHeaders}
          onApply={headerDialog.mode === 'apply' ? applyKnownHeader : undefined}
          onClose={() => setHeaderDialog(null)}
        />
      )}
    </div>
  )
}

