import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import type { MutableRefObject } from 'react'
import type { HeaderTemplate, KnownTableHeader, KnownTableHeadersOut, TableCell, TableGrid, TableGridOut, TableCheck, TableChecksOut } from '../../lib/types'
import { applyHeaderTemplate, setVerified, deleteTrack, emptyGrid, extractHeaderTemplate, fillDown, insertTrack, joinColumns, mergeKeepsEveryText, mergeRange, normalizeColumn, fractionsColumn, splitCell, transformColumnCase } from '../../lib/grid'
import type { CellChoice, ColumnOp, SheetSelection, TrackOp, UniverController } from './UniverSheet'
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
import { describeError } from '../../lib/errors'
import { cellBand, charDiff, confirmGroups, disputedCells, estimateRowBand, nextDisputedCell, type GridConsensus } from '../consensus'
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
  const { t, tn } = useI18n()
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
  // Model comparison (consensus): the models' readings aligned onto this grid,
  // asked again after each change so inserted rows keep the colours in place.
  // Without a comparison for the page the answer is empty and nothing shows.
  const [consensus, setConsensus] = useState<GridConsensus | null>(null)
  const [showConsensus, setShowConsensus] = useState(true)
  useEffect(() => {
    if (!grid) return
    let stale = false
    const timer = setTimeout(() => {
      apiPost<GridConsensus>(`/blocks/${serverId}/consensus`, grid)
        .then((out) => { if (!stale) setConsensus(out?.cells?.length ? out : null) })
        .catch(() => { if (!stale) setConsensus(null) })
    }, 600)
    return () => { stale = true; clearTimeout(timer) }
  }, [grid, serverId])
  const consensusCells = new Map((consensus?.cells ?? []).map((cell) => [`${cell.r}:${cell.c}`, cell]))
  // A cell that differs only by a convention (a dash, dot leaders…) is not painted:
  // it is no misreading. Its readings still show on hover and on right click.
  const disputed = disputedCells((consensus?.cells ?? []).filter((cell) => cell.score != null))
  // A disputed cell the user has edited (source becomes 'manual') or verified is
  // resolved: violet instead of amber or red, so the remaining work stays visible.
  const currentCells = new Map((grid?.cells ?? []).map((cell) => [`${cell.r}:${cell.c}`, cell]))
  const isResolved = (r: number, c: number) => {
    const cell = currentCells.get(`${r}:${c}`)
    return Boolean(cell && (cell.source === 'manual' || cell.verified))
  }
  const open = disputed.filter((cell) => !isResolved(cell.r, cell.c))
  const disagreements = consensus && showConsensus
    ? {
        partly: open.filter((cell) => cellBand(cell) === 'partly').map((cell) => [cell.r, cell.c] as [number, number]),
        disagree: open.filter((cell) => cellBand(cell) === 'disagree').map((cell) => [cell.r, cell.c] as [number, number]),
        resolved: disputed.filter((cell) => isResolved(cell.r, cell.c)).map((cell) => [cell.r, cell.c] as [number, number]),
      }
    : undefined
  const cellChoices = consensus && showConsensus
    ? (row: number, column: number) => {
        const cell = consensusCells.get(`${row}:${column}`)
        if (!cell || cell.score == null || cell.kind === 'same') return []
        const mine = currentCells.get(`${row}:${column}`)?.text ?? ''
        // A model that read nothing would empty the cell: said so, and offered last.
        const choices = consensus.models.flatMap((model) => {
          const value = cell.values[model.key]
          if (value == null || value === mine) return []
          return [value.trim()
            ? { label: t('consensus.useModel', { name: model.name, value }), value, model: model.key }
            : { label: t('consensus.useModelEmpty', { name: model.name }), value, model: model.key }]
        })
        return [...choices.filter((choice) => choice.value.trim()), ...choices.filter((choice) => !choice.value.trim())]
      }
    : undefined
  const cellHint = consensus && showConsensus
    ? (row: number, column: number) => {
        const cell = consensusCells.get(`${row}:${column}`)
        if (!cell || cell.score == null) return null
        const mine = (gridRef.current ?? grid)?.cells.find((item) => item.r === row && item.c === column)?.text ?? ''
        return (
          <div className="space-y-1" data-testid="consensus-hint">
            {cell.kind !== 'same' && (
              <div className="text-[11px] font-semibold" data-testid="consensus-hint-kind">
                {cell.kind === 'convention'
                  ? `${t('consensus.kind.convention')}: ${cell.conventions.map((rule) => t(`consensus.rule.${rule}`)).join(', ')}`
                  : t(`consensus.kind.${cell.kind}`)}
              </div>
            )}
            <div className="text-[11px] text-[color:var(--color-ink-3)]">{t('consensus.mine')}: <span className="mono text-[color:var(--color-ink)]">{mine || '—'}</span></div>
            {consensus.models.map((model) => {
              const value = cell.values[model.key]
              return (
                <div key={model.key}>
                  <span className="font-semibold">{model.name}: </span>
                  {value == null || !value.trim()
                    ? <span className="italic text-[color:var(--color-ink-3)]">{value == null ? t('consensus.noValue') : t('consensus.emptyValue')}</span>
                    // The model's reading as it is, with what differs from mine marked.
                    : <span className="mono">{charDiff(mine, value).filter((part) => part.kind !== 'del').map((part, index) =>
                        part.kind === 'same' ? <span key={index}>{part.text}</span>
                          : <mark key={index} className="bg-[color:var(--color-sig-wash)] text-[color:var(--color-sig-text)]">{part.text}</mark>)}</span>}
                </div>
              )
            })}
          </div>
        )
      }
    : undefined

  // Working through the disagreements: the kinds still open, the next cell
  // (numbers first), the cell's estimated place on the scan, and confirming a
  // reading that is already right, one cell or a whole column's pattern.
  const openKinds = { digits: 0, text: 0, missing: 0 }
  for (const cell of open) if (cell.kind !== 'same' && cell.kind !== 'convention') openKinds[cell.kind] += 1
  const conventionCells = (consensus?.cells ?? []).filter((cell) => cell.kind === 'convention').length
  const goToCell = (cell: { r: number; c: number } | null) => {
    if (!cell) return
    sheetController?.select(cell.r, cell.c)
    setActive({ r: cell.r, c: cell.c })
    setSelection({ startRow: cell.r, startColumn: cell.c, endRow: cell.r, endColumn: cell.c })
  }
  const goNext = () => goToCell(nextDisputedCell(open, active))
  const myText = (r: number, c: number) => (gridRef.current ?? grid)?.cells.find((cell) => cell.r === r && cell.c === c)?.text ?? ''
  // Each choice made here is recorded for everyone on the page (who, which model,
  // before and after), so it shows in the comparison's history and can be reverted.
  const cellState = (r: number, c: number) => {
    const cell = (gridRef.current ?? grid)?.cells.find((item) => item.r === r && item.c === c)
    return { r, c, text: cell?.text ?? '', verified: Boolean(cell?.verified), source: cell?.source ?? 'manual' }
  }
  const recordCells = (
    kind: 'use_model' | 'confirm_mine' | 'group_confirm',
    before: ReturnType<typeof cellState>[],
    after: { r: number; c: number; text?: string; verified?: boolean }[],
    modelKey?: string,
  ) => {
    if (pageId == null || consensus?.run_id == null || before.length === 0) return
    apiPost(`/pages/${pageId}/consensus/decisions`, {
      run_id: consensus.run_id, kind, block_id: serverId,
      ...(modelKey ? { model_key: modelKey } : {}),
      before: { cells: before }, after: { cells: after },
    }).catch((e) => setNotice(t('consensus.history.recordFailed', { error: describeError(e).messaggio })))
  }
  const onCellChoice = (row: number, column: number, choice: CellChoice) => {
    const before = cellState(row, column)
    if (before.text === choice.value) return
    recordCells('use_model', [before], [{ r: row, c: column, text: choice.value }], choice.model)
  }
  /** Confirms cells and records it; only the cells that were not verified yet count. */
  const confirmCells = (kind: 'confirm_mine' | 'group_confirm', cells: { r: number; c: number }[]) => {
    const before = cells.map((cell) => cellState(cell.r, cell.c)).filter((cell) => !cell.verified)
    verifyCells(cells)
    recordCells(kind, before, before.map((cell) => ({ r: cell.r, c: cell.c, text: cell.text, verified: true })))
  }
  const verifyCells = (cells: { r: number; c: number }[]) => {
    const current = gridRef.current
    if (!current || !cells.length) return
    const next = cells.reduce((acc, cell) => setVerified(acc, { startRow: cell.r, startColumn: cell.c, endRow: cell.r, endColumn: cell.c }, true), current)
    if (univerGridSignature(next) === univerGridSignature(current) && next.cells.every((cell, i) => cell.verified === current.cells[i]?.verified)) return
    remember(current)
    applySnapshot(next)
  }
  const confirmAndNext = () => {
    if (!active || !myText(active.r, active.c).trim()) return
    const after = nextDisputedCell(open, active)
    confirmCells('confirm_mine', [active])
    if (after && (after.r !== active.r || after.c !== active.c)) goToCell(after)
  }
  const activeConsensus = active ? consensusCells.get(`${active.r}:${active.c}`) : undefined
  const groups = consensus && showConsensus
    ? confirmGroups(open, myText, consensus.models.map((model) => model.key))
    : []
  const modelName = (key: string) => consensus?.models.find((model) => model.key === key)?.name ?? key
  const columnName = (c: number) => {
    const letter = String.fromCharCode(65 + (c % 26))
    const header = [0, 1].map((r) => myText(r, c).trim()).filter(Boolean).join(' ')
    return header ? `${letter} (${header})` : letter
  }
  const confirmGroup = async (group: (typeof groups)[number]) => {
    const ok = await confirm({
      title: t('consensus.groupTitle'),
      message: t('consensus.groupConfirm', {
        n: group.cells.length,
        column: columnName(group.column),
        agree: group.agree.map(modelName).join(', '),
        differ: group.differ.map(modelName).join(', '),
      }),
      acceptLabel: t('consensus.groupAccept', { n: group.cells.length }),
    })
    if (ok) confirmCells('group_confirm', group.cells)
  }
  const keysRef = useRef<{ goNext: () => void; confirmAndNext: () => void; editing: () => boolean }>({ goNext, confirmAndNext, editing: () => false })
  keysRef.current = { goNext, confirmAndNext, editing: () => sheetController?.isEditing() ?? false }
  useEffect(() => {
    if (!consensus) return
    // Captured before the sheet's own keys. The sheet keeps the focus in a hidden
    // editable element even when nothing is typed: ask it whether a cell is being
    // edited (the key would confirm the old text), and leave other fields alone.
    const onKey = (event: KeyboardEvent) => {
      if (!event.altKey || event.ctrlKey || event.metaKey) return
      if (event.code !== 'KeyN' && event.code !== 'KeyV') return
      const target = event.target
      if (target instanceof HTMLElement && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return
      if (keysRef.current.editing()) return
      event.preventDefault()
      event.stopPropagation()
      if (event.code === 'KeyN') keysRef.current.goNext()
      else keysRef.current.confirmAndNext()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [consensus])
  const imageFocus = consensus && showConsensus && active && activeConsensus && activeConsensus.kind !== 'same' && grid && !showPage
    ? { ...estimateRowBand(grid.rows, active.r), label: t('consensus.estimated', { row: active.r + 1, column: columnName(active.c) }) }
    : null

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

      {consensus && (
        <div className="space-y-1 text-[11px] text-[color:var(--color-ink-2)]" data-testid="consensus-bar">
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-semibold text-[color:var(--color-ink)]">{tn('consensus.editorBar', open.length)}</span>
            {openKinds.digits > 0 && <span className="font-semibold text-[color:var(--color-sig-text)]" data-testid="open-digits">{tn('consensus.kindCount.digits', openKinds.digits)}</span>}
            {openKinds.text > 0 && <span className="font-semibold text-[color:var(--color-warn)]">{tn('consensus.kindCount.text', openKinds.text)}</span>}
            {openKinds.missing > 0 && <span className="font-semibold text-[color:var(--color-sig-text)]">{tn('consensus.kindCount.missing', openKinds.missing)}</span>}
            {conventionCells > 0 && <span title={t('consensus.conventionNotPainted')}>{tn('consensus.kindCount.convention', conventionCells)}</span>}
            {disputed.length > open.length && <span className="inline-flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 border border-[#7c3aed]" style={{ background: 'rgba(124, 58, 237, 0.3)' }} />{tn('consensus.resolvedCount', disputed.length - open.length)}</span>}
            {showConsensus && open.length > 0 && (
              <span className="ml-auto inline-flex items-center gap-1">
                <button type="button" className="btn btn-primary btn-sm" aria-keyshortcuts="Alt+N" onClick={goNext}>
                  {t('consensus.nextCell')} <kbd className="mono text-[10px]">Alt N</kbd>
                </button>
                <button
                  type="button"
                  className="btn btn-sm"
                  aria-keyshortcuts="Alt+V"
                  disabled={!active || !myText(active.r, active.c).trim()}
                  title={t('consensus.confirmMineHint')}
                  onClick={confirmAndNext}
                >
                  {t('consensus.confirmMine')} <kbd className="mono text-[10px]">Alt V</kbd>
                </button>
              </span>
            )}
            <label className={`${showConsensus && open.length > 0 ? '' : 'ml-auto '}inline-flex items-center gap-1`}>
              <input type="checkbox" checked={showConsensus} onChange={(event) => setShowConsensus(event.target.checked)} />
              {t('consensus.editorToggle')}
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <span>{consensus.models.map((model) => model.name).join(' · ')}</span>
            <span className="inline-flex items-center gap-1"><span className="inline-block h-2.5 w-2.5" style={{ background: 'rgba(232, 161, 0, 0.45)' }} />{t('consensus.legendPartly')}</span>
            <span className="inline-flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 border border-[#a3000e]" style={{ background: 'rgba(163, 0, 14, 0.25)' }} />{t('consensus.legendDisagreeCell')}</span>
            <span>{t('consensus.editorHover')}</span>
            <span>{t('consensus.editorRightClick')}</span>
          </div>
          {groups.length > 0 && (
            <ul className="space-y-0.5" data-testid="confirm-groups">
              {groups.slice(0, 3).map((group) => (
                <li key={`${group.column}-${group.agree.join()}`} className="flex flex-wrap items-center gap-2">
                  <span>{t('consensus.groupLine', {
                    n: group.cells.length,
                    column: columnName(group.column),
                    agree: group.agree.map(modelName).join(', '),
                    differ: group.differ.map(modelName).join(', '),
                  })}</span>
                  <button type="button" className="btn btn-sm" onClick={() => goToCell(group.cells[0])}>{t('consensus.groupFirst')}</button>
                  <button type="button" className="btn btn-sm" onClick={() => void confirmGroup(group)}>{t('consensus.groupAccept', { n: group.cells.length })}</button>
                </li>
              ))}
            </ul>
          )}
        </div>
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
            focus={imageFocus}
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
                disagreements={disagreements}
                cellHint={cellHint}
                cellChoices={cellChoices}
                onCellChoice={onCellChoice}
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

