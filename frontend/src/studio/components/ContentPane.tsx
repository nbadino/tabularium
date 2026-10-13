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
import type { MutableRefObject } from 'react'
import type {
  TableDetectOut,
  TableDetectRequest,
  TableGrid,
  TableGridOut,
} from '../../lib/types'
import {
  emptyGrid,
  fillDown,
  joinColumns,
  mergeKeepsEveryText,
  mergeRange,
  normalizeColumn,
  fractionsColumn,
  splitCell,
  transformColumnCase,
} from '../../lib/grid'
import type { ColumnOp, SheetSelection } from './UniverSheet'

import { univerGridSignature } from '../../lib/univerGrid'
import { apiGet, apiPost } from '../../lib/api'
import { LoadingGrid, Modal, Module, Select, WarnNotice } from '../../app/ui'
import { IconDown, IconSave, IconTrash, IconUp } from '../../app/icons'
import { useI18n } from '../../i18n'
import type { LabelDef, TableCheck, TableChecksOut } from '../../lib/types'
import type { DisplayBlock, LivePrefillOutput, PrefillDraft } from '../types'
import JspreadsheetSheet from './JspreadsheetSheet'
import { useFlipList, useHeightTransition } from '../../lib/motion'
import SplitColumnDialog from './SplitColumnDialog'
import TablePageImage from './TablePageImage'
import LiveStream from './LiveStream'
import ConventionsChecklist from './ConventionsChecklist'
import LabelsEditor from './LabelsEditor'

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
 *  Il foglio si apre quasi a tutto schermo: il ricercatore lavora sui dati in
 *  una sola superficie ampia, mentre il ritaglio resta visibile nella scheda. */
function TableBlockEditor({
  id,
  serverId,
  pageId,
  pageImageUrl,
  pageImageSize,
  onSaveTable,
  onDetectTable,
  confirmed,
  onConfirmed,
}: {
  id: string
  serverId: number | null
  pageId?: number | null
  pageImageUrl?: string | null
  pageImageSize?: { w: number; h: number } | null
  onSaveTable: (serverId: number, grid: TableGrid) => Promise<string>
  onDetectTable?: (serverId: number, opts: TableDetectRequest) => Promise<TableDetectOut>
  /** «Verificata» del blocco, la stessa casella della scheda. */
  confirmed: boolean
  onConfirmed: (confirmed: boolean) => void
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
        {pageId && (
          // Una finestra sua, aperta da script e con un nome per tabella: così
          // sa da quale finestra viene (il suo «Chiudi e torna» la richiude e
          // riporta qui), e un secondo clic la richiama invece di duplicarla.
          <button
            type="button"
            onClick={() => {
              const url = `/tabella/${serverId}?page=${pageId}&width=${pageImageSize?.w ?? ''}&height=${pageImageSize?.h ?? ''}&confirmed=${confirmed ? 1 : 0}`
              const w = Math.round(window.screen.availWidth * 0.85)
              const h = Math.round(window.screen.availHeight * 0.85)
              window.open(url, `tabularium-table-${serverId}`, `popup,width=${w},height=${h}`)?.focus()
            }}
            className="btn btn-sm"
            title={t('table.openSecondScreen')}
          >
            {t('table.openSecondScreen')}
          </button>
        )}
        <button type="button" onClick={() => setOpen(true)} className="btn btn-sm btn-primary">
          {t('table.openWorkspace')}
        </button>
      </div>
      {open && (
        <Modal title={t('table.workspaceTitle')} extraWide onClose={() => setOpen(false)}>
          {/* La griglia si rilegge dal server all'apertura: la scheda non tiene
              una seconda copia del modello da tenere allineata. */}
          <div className="flex min-h-0 flex-1 flex-col p-3">
            <VerifiedToggle checked={confirmed} onChange={onConfirmed} className="mb-2" />
            <TableWorkspacePanel
              id={id}
              serverId={serverId}
              pageImageUrl={pageImageUrl ?? null}
              pageId={pageId ?? null}
              pageImageSize={pageImageSize ?? null}
              onSaveTable={onSaveTable}
              onDetectTable={onDetectTable}
            />
          </div>
        </Modal>
      )}
    </>
  )
}

/** La casella «Verificata» del blocco, anche dentro le viste della tabella. */
export function VerifiedToggle({ checked, onChange, className = '' }: { checked: boolean; onChange: (checked: boolean) => void; className?: string }) {
  const { t } = useI18n()
  return (
    <label className={`flex w-fit cursor-pointer items-center gap-1.5 text-[12px] text-[color:var(--color-ink-2)] ${className}`}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="accent-[color:var(--color-sig)]" />
      {t('content.confirmDraft')}
    </label>
  )
}

/** Univer è la superficie principale dell'editor tabellare. */
const TABLE_SURFACE: 'univer' | 'ce' = 'univer'

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
  id,
  serverId,
  pageImageUrl,
  pageId,
  pageImageSize,
  onSaveTable,
  onDetectTable,
  flushRef,
}: {
  id: string
  serverId: number
  pageImageUrl: string | null
  pageId: number | null
  pageImageSize: { w: number; h: number } | null
  onSaveTable: (serverId: number, grid: TableGrid) => Promise<string>
  onDetectTable?: (serverId: number, opts: TableDetectRequest) => Promise<TableDetectOut>
  /** Riceve una funzione che salva subito ciò che l'autosave non ha ancora
   *  salvato: chi chiude la vista (la seconda finestra) la chiama prima. */
  flushRef?: MutableRefObject<(() => Promise<void>) | null>
}) {
  const { t } = useI18n()
  const [grid, setGrid] = useState<TableGrid | null>(null)
  // Il foglio parte a dialogo già aperto: montare Univer durante l'ingresso
  // (200 ms) lo farebbe scattare. Nel frattempo resta lo scheletro.
  const [settled, setSettled] = useState(false)
  useEffect(() => {
    const id = window.setTimeout(() => setSettled(true), 220)
    return () => window.clearTimeout(id)
  }, [])
  // Lo scheletro del foglio si vede solo alla prima apertura; ai rimontaggi
  // (una colonna in più) il foglio si ricostruisce senza ripassarci.
  const sheetShown = useRef(false)
  useEffect(() => {
    if (settled) sheetShown.current = true
  }, [settled])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [otsl, setOtsl] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
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

  // L'autosave aspetta 900 ms: chi esce prima non deve perdere l'ultima
  // modifica. `flush` la salva subito; si chiama uscendo dalla vista (dialogo
  // chiuso, link, seconda finestra chiusa dal suo pulsante), e chiudere la
  // finestra con modifiche ancora in volo chiede conferma.
  const dirty = () =>
    TABLE_SURFACE === 'univer' && !!gridRef.current && univerGridSignature(gridRef.current) !== savedRef.current
  const flushLatest = useRef(async () => {})
  flushLatest.current = async () => {
    if (dirty()) await doSave(gridRef.current!)
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
      void flushLatest.current()
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
    <div className="enter flex min-h-0 flex-1 flex-col gap-2">
      {TABLE_SURFACE === 'univer' && (
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
          {/* Le azioni sulla colonna stanno nella stessa riga dello stato: in
              una finestra stretta vanno a capo da sole, intere. */}
          <div className="flex flex-wrap items-center gap-1">
            <span className="lbl mb-0 mr-2" title={t('table.quickActionsHint')}>{t('table.quickActions')}</span>
            {(['normalize', 'fill', 'join', 'upper', 'lower', 'split'] as const).map((op) => (
              <button key={op} type="button" className="btn btn-sm" onClick={() => runColumnOp(op, selection)}>{t(`table.${op}Short` as const)}</button>
            ))}
            <button type="button" className="btn btn-sm" title={t('table.toFractionsHint')} onClick={() => runColumnOp('fractions', selection)}>{t('table.toFractionsShort')}</button>
          </div>
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
      )}

      {/* Le somme che non tornano e toccano la cella selezionata, con il loro
          comando «refuso»: servono per agire, quindi restano in una riga. Il
          resto dello stato delle somme è il Σ qui sopra. */}
      {TABLE_SURFACE === 'univer' && activeProblems.length > 0 && (
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
            {undoSplit && (
              <button type="button" onClick={revertSplit} className="btn btn-sm">
                {t('table.splitUndo')}
              </button>
            )}
          </span>
        </WarnNotice>
      )}


      <div className="flex min-h-0 flex-1 gap-3">
        {pageImageUrl && <TablePageImage src={pageImageUrl} pageId={pageId} imageSize={pageImageSize} />}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {TABLE_SURFACE === 'univer' ? (
            // La chiave segue le dimensioni: un confine aggiunto o rifiutato
            // cambia il numero di tracce, e il documento va ricostruito su
            // quello nuovo invece di restare indietro.
            <Suspense
              fallback={<LoadingGrid label={t('content.tableLoading')} rows={10} />}
            >
              {!settled ? (
                <LoadingGrid label={t('content.tableLoading')} rows={10} />
              ) : (
              <UniverSheet
                key={`${serverId}:${grid.rows}x${grid.cols}:${sheetRevision}`}
                grid={grid}
                onGridChange={applyModel}
                onColumnOp={runColumnOp}
                suspects={checks?.suspects}
                onSelectionChange={(sel) => {
                  setActive({ r: sel.startRow, c: sel.startColumn })
                  setSelection(sel)
                }}
                intro={!sheetShown.current}
              />
              )}
            </Suspense>
          ) : (
            <JspreadsheetSheet
              key={`${id}:${serverId}`}
              grid={grid}
                onSave={(g) => onSaveTable(serverId, g)}
                onDetect={onDetectTable ? (opts) => onDetectTable(serverId, opts) : undefined}
                onColumnOp={runColumnOp}
              />
          )}
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
  /** Una riga sola: blocco già verificato e non selezionato. Il lavoro
   *  finito si riduce a numero, classe, anteprima e ✓; si riapre col clic. */
  collapsed?: boolean
  preview?: string
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
  collapsed,
  preview,
  children,
}: RowShellProps) {
  const { t } = useI18n()
  // Verificata, la riga si chiude in una riga sola: ci arriva scorrendo, e chi
  // le sta sotto sale con lei invece di saltare.
  const rowRef = useRef<HTMLLIElement>(null)
  useHeightTransition(rowRef, collapsed)
  const orderButton = (
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
          className="h-3 w-3 rounded-sm border border-[color:var(--color-rule-strong)]"
          style={{ background: color }}
        />
      )}
    </button>
  )
  if (collapsed) {
    return (
      <li
        ref={rowRef}
        id={`content-row-${id}`}
        data-block={id}
        className="stream-in flex cursor-pointer items-center gap-2 border-b border-[color:var(--color-rule)] px-2.5 py-1.5 last:border-b-0 hover:bg-[color:var(--color-fill)]"
        onClick={(e) => {
          if (!(e.target as HTMLElement).closest('button')) onSelect?.()
        }}
      >
        {orderButton}
        <span className="shrink-0 text-[12px] font-semibold">{label}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-[color:var(--color-ink-2)]">
          {preview}
        </span>
        <span className="shrink-0 text-[11px] text-[color:var(--color-ok)]" title={t('content.confirmDraft')}>
          ✓ {t('content.confirmDraft')}
        </span>
      </li>
    )
  }
  return (
    <li
      ref={rowRef}
      id={`content-row-${id}`}
      data-block={id}
      // Ogni riga entra animata e diventa un proprio contesto di sovrapposizione:
      // senza `z-40` la tendina della classe finirebbe sotto la riga successiva.
      className={`stream-in relative border-b border-[color:var(--color-rule)] p-2.5 last:border-b-0 has-[details[open]]:z-40 ${
        selected ? 'bg-[color:var(--color-sig-wash)]' : ''
      }`}
    >
      <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1.5">
        {orderButton}

        {onLabel && labels ? (
          <Select
            value={label}
            onChange={onLabel}
            onFocus={onSelect}
            ariaLabel={t('inspector.class')}
            className="fld !w-auto text-xs"
            options={labels.map((l) => ({ value: l.name, label: l.name }))}
          />
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
            <img data-fade
              src={cropUrl}
              alt={cropAlt}
              loading="lazy"
              className={`w-full rounded-sm border border-[color:var(--color-rule)] bg-[color:var(--color-table)] object-contain ${wide ? 'max-h-32 object-top' : ''}`}
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
  pageId?: number | null
  pageImageUrl?: string | null
  pageImageSize?: { w: number; h: number } | null
  /** Versione del ritaglio per blocco: cresce quando la regione cambia, e
   *  costringe griglia e immagine a rileggersi dal server. */
  tableVersions?: Record<number, number>
  /** Esito del ri-rilevamento seguito all'ultimo ridimensionamento. */
  tableRedetect?: { serverId: number; state: 'busy' | 'done' | 'stale' | 'error'; message?: string } | null
  onDismissRedetect?: () => void
  onRedetectNow?: (serverId: number) => void
  onDraftContent: (serverId: number, content: string) => void
  /** Cambia la classe di una bozza: le bozze vivono solo qui, quindi la loro
   *  label si governa da qui (non dal canvas). */
  onDraftLabel?: (serverId: number, label: string) => void
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
  /** Modello attivo: serve a dire quali classi conosce già (badge). */
  activeAdapterId?: string | null
  /** Il pannello classi ha salvato: il chiamante riallinea la tassonomia. */
  onLabelsSaved?: (labels: LabelDef[]) => void
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
  pageId,
  pageImageUrl,
  pageImageSize,
  tableVersions,
  tableRedetect,
  onDismissRedetect,
  onRedetectNow,
  onDraftContent,
  onDraftLabel,
  onDraftGrid,
  onSaveDraftGrid,
  onDraftConfirmed,
  onDraftReject,
  onMove,
  onReorderReset,
  colorFor,
  projectId,
  liveOutput,
  activeAdapterId,
  onLabelsSaved,
  working,
}: ContentPaneProps) {
  const { t, tn } = useI18n()
  // Le righe che cambiano posto scivolano (v. lib/motion.ts).
  const listRef = useRef<HTMLUListElement>(null)
  useFlipList(listRef)
  const [rulesOpen, setRulesOpen] = useState(false)
  const [labelsOpen, setLabelsOpen] = useState(false)

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
    document.getElementById(`content-row-${selectedId}`)?.scrollIntoView?.({ block: 'nearest' })
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
          <span className="mono text-[11px] text-[color:var(--color-ink-3)]" title={onMove ? t('layers.keys') : undefined}>
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
          {projectId != null && (
            <button type="button" onClick={() => setLabelsOpen(true)} className="btn btn-sm">
              {t('labels.tab')}
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
          <ul ref={listRef} className="cascade relative flex flex-col">
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
                    block.prefill && !block.confirmed
                      ? { text: 'OCR', title: t('layers.prefillTitle', { source: block.prefill }) }
                      : undefined
                  }
                  collapsed={block.confirmed && selectedId !== block.id && !isTable}
                  preview={noContent ? '' : block.content.replace(/\s+/g, ' ')}
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
                        pageId={pageId ?? null}
                        pageImageUrl={pageImageUrl ?? null}
                        pageImageSize={pageImageSize ?? null}
                        onSaveTable={onSaveTable}
                        onDetectTable={onDetectTable}
                        confirmed={block.confirmed}
                        onConfirmed={(v) => onConfirmed(block.id, v)}
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
                  labels={labels}
                  onLabel={onDraftLabel ? (label) => onDraftLabel(draft.serverId, label) : undefined}
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
                      pageId={pageId ?? null}
                      pageImageUrl={pageImageUrl ?? null}
                      pageImageSize={pageImageSize ?? null}
                      onSaveTable={(serverId, grid) => {
                        onDraftGrid(serverId, grid)
                        return onSaveDraftGrid(serverId, grid)
                      }}
                      confirmed={draft.confirmed}
                      onConfirmed={(v) => onDraftConfirmed(draft.serverId, v)}
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

      {labelsOpen && projectId != null && (
        <Modal title={t('labels.tab')} wide onClose={() => setLabelsOpen(false)}>
          <LabelsEditor
            projectId={projectId}
            labels={labels}
            activeAdapterId={activeAdapterId}
            onSaved={(next) => onLabelsSaved?.(next)}
          />
        </Modal>
      )}
    </Module>
  )
}
