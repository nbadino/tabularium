import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import type { MutableRefObject } from 'react'
import type { TableGrid, TableGridOut, TableCheck, TableChecksOut } from '../../lib/types'
import { deleteTrack, emptyGrid, fillDown, insertTrack, joinColumns, mergeKeepsEveryText, mergeRange, normalizeColumn, fractionsColumn, splitCell, transformColumnCase } from '../../lib/grid'
import type { ColumnOp, SheetSelection, TrackOp } from './UniverSheet'
import { useConfirm } from '../../app/confirm'
import { univerGridSignature } from '../../lib/univerGrid'
import { apiGet, apiPost } from '../../lib/api'
import { LoadingGrid, WarnNotice } from '../../app/ui'
import { IconSave } from '../../app/icons'
import { useI18n } from '../../i18n'
import SplitColumnDialog from './SplitColumnDialog'
import TablePageImage from './TablePageImage'
import Splitter from './Splitter'
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
const OTSL_KEY = 'tabularium.table.otslPreview'

/** Un dato con la sua parola davanti («Somme 9/9»); la frase intera sta nel
 *  titolo al passaggio. Niente copia nascosta: finiva nel testo copiato. */
function Compact({ label, full, className = '', children }: { label: string; full: string; className?: string; children: React.ReactNode }) {
  return (
    <span title={full} className="flex items-baseline gap-1">
      <span>{label}</span>
      <span className={`mono text-[color:var(--color-ink)] ${className}`}>{children}</span>
    </span>
  )
}

/** Il corpo della vista di lavoro: carica la griglia dal server, possiede i
 *  confini sull'inchiostro e monta la superficie scelta. */
export function TableWorkspacePanel({
  serverId,
  pageImageUrl,
  pageId,
  pageImageSize,
  onSaveTable,
  flushRef,
}: {
  serverId: number
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
  const [otsl, setOtsl] = useState<string | null>(null)
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

  // Ripiegata finché l'annotatore non la apre: è un controllo, non il lavoro.
  const [otslOpen, setOtslOpen] = useState(() => {
    try {
      return localStorage.getItem(OTSL_KEY) === 'open'
    } catch {
      return false
    }
  })
  const toggleOtsl = (open: boolean) => {
    if (open === otslOpen) return
    setOtslOpen(open)
    try {
      localStorage.setItem(OTSL_KEY, open ? 'open' : 'closed')
    } catch {
      /* storage non disponibile: la scelta vale per questa sessione */
    }
  }
  const [sheetRevision, setSheetRevision] = useState(0)
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
    applyModel({ ...current, misprints: on ? [...rest, key] : rest })
  }

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

  // Tutte le scritture passano dalla stessa coda: una risposta vecchia non
  // può sovrascrivere la correzione nuova. Flush attende anche i salvataggi in volo.
  const doSave = useCallback((model: TableGrid): Promise<void> => {
    const signature = univerGridSignature(model)
    if (pendingSave.current?.signature === signature) return pendingSave.current.promise
    const operation = saveQueue.current.catch(() => {}).then(async () => {
      setSaving(true)
      try {
        setOtsl(await onSaveTable(serverId, model))
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
    setSheetRevision((revision) => revision + 1)
  }

  const revertSplit = () => {
    if (!undoSplit) return
    setUndoSplit(null)
    setNotice(null)
    applyModel(undoSplit)
    setSheetRevision((revision) => revision + 1)
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
      setUndoSplit(before)
      setNotice(t('table.columnOpDone', { n: changed }))
      applyModel(next)
      setSheetRevision((revision) => revision + 1)
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
   *  `vlines`/`hlines`/`phantom_cols`; il foglio si rimonta da lì. È il gesto con
   *  cui si corregge un'intestazione o una colonna che il modello ha mancato. */
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
    // Una traccia è una modifica strutturale: il modo di tornare indietro è
    // quello delle operazioni di colonna, per un passo.
    setUndoSplit(current)
    setNotice(null)
    applyModel(next)
    setSheetRevision((revision) => revision + 1)
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
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      {(
        // Una riga sola di stato: dimensioni, celle verificate, somme. Le frasi
        // intere stanno nel titolo al passaggio e restano ai lettori di schermo.
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-[color:var(--color-rule)] pb-2 text-[11px] text-[color:var(--color-ink-2)]">
          <Compact label={t('table.gridShort')} full={t('table.workspaceSummary', { rows: grid.rows, cols: grid.cols })}>
            {grid.rows} × {grid.cols}
          </Compact>
          {grid.phantom_cols.length > 0 && (
            <span>{t('table.phantomColumns', { n: grid.phantom_cols.length })}</span>
          )}
          {/* ✓ / ✕ stanno nella barra della formula; qui il conto. */}
          <Compact
            label={t('table.verifiedShort')}
            full={`${t('table.verifiedCount', verified)} — ${t('table.verifyHint')}`}
          >
            {verified.n}/{verified.total}
          </Compact>
          {checks && checks.checks.length > 0 && (
            <Compact
              full={
                (checks.failed > 0
                  ? t('table.checksSummary', { passed: checks.passed, total: checks.checks.length, failed: checks.failed })
                  : t('table.checksBalanced', { n: checks.passed })) +
                ((checks.misprints ?? 0) > 0 ? ` ${t('table.misprintsCount', { n: checks.misprints ?? 0 })}` : '')
              }
              label={t('table.sumsShort')}
              className={checks.failed > 0 ? '!text-[color:var(--color-warn)] font-semibold' : ''}
            >
              {checks.passed}/{checks.checks.length}
            </Compact>
          )}
          <label className="flex items-center gap-1" title={t('table.headerRowsTitle')}>
            {t('table.headerShort')}
            <input
              type="number"
              min={0}
              max={Math.min(grid.rows, 20)}
              value={grid.header_rows ?? 0}
              onChange={(event) => {
                const n = Math.max(0, Math.min(Math.min(grid.rows, 20), Number(event.target.value) || 0))
                if (n === (grid.header_rows ?? 0)) return
                applyModel({ ...grid, header_rows: n })
                setSheetRevision((revision) => revision + 1)
              }}
              className="w-12 rounded-[var(--radius-sm)] border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] px-1 py-0.5 text-[11px]"
            />
          </label>
          <button type="button" className="btn btn-sm" onClick={() => runColumnOp('split', selection)}>{t('table.splitTitle')}</button>
          <button type="button" className="btn btn-sm" disabled={!undoSplit} onClick={revertSplit}>{t('table.undoOperation')}</button>
          <select aria-label={t('table.quickActions')} value="" className="btn btn-sm max-w-48" onChange={(event) => {
            if (event.target.value) runColumnOp(event.target.value as ColumnOp, selection)
          }}>
            <option value="" disabled>{t('table.quickActions')}</option>
            {(['normalize', 'fill', 'join', 'upper', 'lower', 'fractions', 'merge', 'unmerge'] as const).map((op) => (
              <option key={op} value={op}>{op === 'fractions' ? t('table.toFractionsShort') : op === 'merge' ? t('table.modeMerge') : op === 'unmerge' ? t('table.splitCell') : t(`table.${op}Short` as const)}</option>
            ))}
          </select>
          <span role="status" className="ml-auto" data-save-version={saveVersion}>{saving ? t('table.saving') : dirty() ? t('table.unsaved') : t('table.saved')}</span>
          <button
            type="button"
            onClick={() => void doSave(gridRef.current ?? grid).catch(() => {})}
            disabled={saving || !dirty()}
            className="btn btn-sm btn-primary ml-auto"
          >
            <IconSave size={11} />
            {saving ? t('table.saving') : t('table.saveGrid')}
          </button>
        </div>
      )}

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
          <span className="flex flex-wrap items-center gap-2">
            {notice}

          </span>
        </WarnNotice>
      )}


      <div ref={workspace} className="flex min-h-0 flex-1 gap-2">
        <div className="flex min-h-0 min-w-0 flex-col" style={{ flex: `0 0 ${imageWidth}%` }}>
          {pageImageUrl && (
            <div className="flex items-center gap-2 pb-1 text-[11px]">
              <button className="btn btn-sm" aria-pressed={!showPage} onClick={() => setShowPage(false)}>{t('table.tableCrop')}</button>
              <button className="btn btn-sm" aria-pressed={showPage} onClick={() => setShowPage(true)}>{t('table.wholePage')}</button>
            </div>
          )}
          <TablePageImage
            key={showPage ? 'page' : 'crop'}
            src={showPage && pageImageUrl ? pageImageUrl : `/api/blocks/${serverId}/crop`}
            pageId={showPage ? pageId : null}
            imageSize={showPage ? pageImageSize : null}
          />
        </div>
        <Splitter value={workspaceWidth * imageWidth / 100} min={workspaceWidth * 0.25} max={workspaceWidth * 0.65} side="left" label={t('table.imageWidth')} onChange={(width) => setImageWidth(width / workspaceWidth * 100)} onReset={() => setImageWidth(45)} />
        <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
          <Suspense fallback={<LoadingGrid label={t('content.tableLoading')} rows={10} />}>
              <UniverSheet
                key={`${serverId}:${grid.rows}x${grid.cols}:${sheetRevision}`}
                grid={grid}
                onGridChange={(next) => { setUndoSplit(null); applyModel(next) }}
                onColumnOp={runColumnOp}
                onTrackOp={runTrackOp}
                suspects={checks?.suspects}
                onSelectionChange={(sel) => {
                  setActive({ r: sel.startRow, c: sel.startColumn })
                  setSelection(sel)
                }}
                intro
              />
          </Suspense>
        </div>
      </div>

      {otsl !== null && (
        // Ripiegabile: serve a controllare, non a lavorare, e ruba altezza al
        // foglio. La scelta si ricorda.
        <details
          open={otslOpen}
          onToggle={(e) => toggleOtsl(e.currentTarget.open)}
          className="border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2"
        >
          <summary className="flex cursor-pointer items-center justify-between">
            <span className="lbl !mb-0">{t('table.otslNote')}</span>
            <button
              type="button"
              onClick={(e) => {
                e.preventDefault()
                void navigator.clipboard?.writeText(otsl)
              }}
              className="btn btn-sm"
            >
              {t('common.copy')}
            </button>
          </summary>
          <pre className="mono mt-1 max-h-32 overflow-auto text-[11px] whitespace-pre-wrap">{otsl}</pre>
        </details>
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

