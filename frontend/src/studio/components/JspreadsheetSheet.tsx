/**
 * Il foglio tabella dello studio, su Jspreadsheet CE.
 *
 * Perché una libreria: le operazioni che mancavano — menù contestuale (tasto
 * destro → inserisci colonna), selezione a intervallo, copia/incolla
 * multi-cella, fill handle — sono quelle che una libreria Excel-like ha già
 * risolte, e rifarle a mano era il costo maggiore del foglio nostro.
 *
 * Cosa resta nostro, e non è negoziabile:
 *
 * - **`TableGrid` è la fonte di verità.** La libreria possiede testo e
 *   struttura; `lib/gridSheet.ts` traduce nei due sensi, e l'OTSL lo genera il
 *   server dal nostro modello (non dalla vista).
 * - **I metadati per cella** (`source`, `verified`) vivono nei `meta` della
 *   libreria: senza, una correzione umana tornerebbe a sembrare una bozza.
 * - **Le colonne fantasma** e la geometria (`vlines`/`hlines`) non hanno posto
 *   nel foglio: passano attraverso, intatte.
 * - **I dialog nativi.** Jspreadsheet chiede conferma con `window.confirm` e
 *   segnala con `window.alert`: la conferma la diamo noi con la `Modal` del
 *   design system *prima* di chiamare la libreria, e il suo dialog viene
 *   soppresso perché sarebbe un duplicato. L'`alert` diventa una `Notice`.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import * as jssModule from 'jspreadsheet-ce'
import 'jspreadsheet-ce/dist/jspreadsheet.css'
import type { TableDetectOut, TableDetectRequest, TableGrid } from '../../lib/types'
import { columnName, gridToSheet, parseRange, sheetToGrid } from '../../lib/gridSheet'
import { deleteTrack, dropBoundary, insertBoundary, insertTrack } from '../../lib/grid'
import type { Axis } from '../../lib/grid'
import TableGridOverlay from './TableGridOverlay'
import { WarnNotice } from '../../app/ui'
import { IconMinus, IconPlus, IconSave } from '../../app/icons'
import { useConfirm } from '../../app/confirm'
import { useI18n } from '../../i18n'

// `jspreadsheet-ce` è CommonJS: a seconda dell'interop il callable è il default
// o il namespace. Risolverlo a mano evita un `undefined` che si manifesta solo
// al primo click.
interface JspreadsheetSheetProps {
  grid: TableGrid
  onSave: (grid: TableGrid) => Promise<string>
  onDetect?: (opts: TableDetectRequest) => Promise<TableDetectOut>
  /** Ritaglio del blocco. Con questo, l'overlay mostra i confini sull'inchiostro
   *  e diventano trascinabili: senza, la griglia è solo testo. */
  cropUrl?: string | null
  /** Falso quando l'overlay lo disegna chi ospita: è la vista di lavoro a
   *  possedere i confini, non la libreria della griglia. */
  withOverlay?: boolean
}

/** Ritardo dell'autosave: le stesse ragioni del debounce dei blocchi (700ms)
 *  con un po' più d'aria, perché il salvataggio tocca un endpoint dedicato. */
const AUTOSAVE_DELAY = 900

/** Chiave del meta che conserva lo stato di verifica di una cella. */
const META_VERIFIED = 'tab_verified'
const META_SOURCE = 'tab_source'

/**
 * Impronta del modello, per riconoscere «questo l'ho già salvato io».
 *
 * Il salvataggio rilegge il foglio e riallinea lo stato: senza un confronto, il
 * suo `setGrid` ri-innesca l'effetto di autosave e il foglio scrive sul server
 * una volta al secondo, per sempre (misurato: 7 scritture in 6 secondi dopo un
 * solo «Salva griglia»).
 */
function gridSignature(grid: TableGrid): string {
  return JSON.stringify([
    grid.rows,
    grid.cols,
    grid.phantom_cols,
    grid.header_rows ?? 0,
    grid.vlines ?? [],
    grid.hlines ?? [],
    grid.cells.map((cell) => [
      cell.r,
      cell.c,
      cell.rowspan,
      cell.colspan,
      cell.text,
      cell.source ?? '',
      cell.verified ?? false,
    ]),
  ])
}

interface SheetInstance {
  getData: (processed?: boolean) => string[][]
  getMerge: () => Record<string, [number, number]> | null
  getMeta: () => Record<string, Record<string, unknown>> | null
  setMeta: (o: string, k: string, v: string) => void
  setMerge: (cellName?: string, colspan?: number, rowspan?: number) => void
  removeMerge: (cellName: string) => void
  /** `insertRow(numRighe, indice, inserisciPrima)` — l'ordine reale della
   *  libreria, che il `.d.ts` documenta male. */
  insertRow: (numOfRows?: number, rowNumber?: number, insertBefore?: number) => void
  insertColumn: (numOfColumns?: number, columnNumber?: number, insertBefore?: number) => void
  deleteRow: (rowNumber?: number, numOfRows?: number) => void
  deleteColumn: (columnNumber?: number, numOfColumns?: number) => void
  getSelection: () => { x1: number; y1: number; x2: number; y2: number } | boolean
  getCell: (cellName: string) => HTMLTableCellElement | null
}

interface SpreadsheetInstance {
  worksheets: SheetInstance[]
}

type JssFactory = (
  element: HTMLElement,
  options: Record<string, unknown>,
) => unknown

/** Il callable è il default CJS; `jspreadsheet()` restituisce un array che la
 *  libreria riempie **dopo** il load: l'istanza arriva da `onload`. */
const jspreadsheet = (
  (jssModule as unknown as { default?: JssFactory }).default ?? jssModule
) as unknown as JssFactory

/**
 * La selezione corrente, letta in modo tollerante.
 *
 * `getSelection()` restituisce un **array** `[x1, y1, x2, y2]`, mentre i tipi
 * dichiarati parlano di un oggetto con `row`/`column`: fidarsi della firma dà
 * `undefined` e quindi `NaN`, e la libreria con un indice `NaN` non fallisce
 * subito — sbaglia in silenzio fino a corrompere lo stato interno. Qui si
 * accettano entrambe le forme e si rifiuta tutto ciò che non è un numero.
 */
function readSelection(worksheet: SheetInstance): { x1: number; y1: number; x2: number; y2: number } | null {
  const raw = worksheet.getSelection() as unknown
  if (!raw) return null
  const values = Array.isArray(raw)
    ? { x1: raw[0], y1: raw[1], x2: raw[2], y2: raw[3] }
    : (raw as { x1?: unknown; y1?: unknown; x2?: unknown; y2?: unknown })
  const box = {
    x1: Number(values.x1),
    y1: Number(values.y1),
    x2: Number(values.x2),
    y2: Number(values.y2),
  }
  if ([box.x1, box.y1, box.x2, box.y2].some((value) => !Number.isFinite(value))) return null
  return box
}

export default function JspreadsheetSheet({ grid: initialGrid, onSave, onDetect, cropUrl, withOverlay = true }: JspreadsheetSheetProps) {
  const { t } = useI18n()
  const confirm = useConfirm()
  const container = useRef<HTMLDivElement | null>(null)
  const instance = useRef<SheetInstance | null>(null)
  const gridRef = useRef(initialGrid)
  /** Impronta dell'ultimo modello salvato (o di partenza): quello che è già
   *  sul server non va riscritto. */
  const savedRef = useRef(gridSignature(initialGrid))
  const saveRef = useRef(onSave)
  saveRef.current = onSave

  const [grid, setGrid] = useState<TableGrid>(initialGrid)
  const [otsl, setOtsl] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [detecting, setDetecting] = useState(false)
  const [detectFill, setDetectFill] = useState<'none' | 'ocr' | 'model'>('none')
  const [detectInfo, setDetectInfo] = useState<TableDetectOut | null>(null)
  const [active, setActive] = useState<{ r: number; c: number }>({ r: 0, c: 0 })

  const applyMeta = useCallback((target: SheetInstance, model: TableGrid) => {
    // La libreria non conosce `verified`/`source`: si conservano nei suoi meta,
    // altrimenti una correzione umana tornerebbe a sembrare una bozza.
    for (const cell of model.cells) {
      const ref = `${columnName(cell.c)}${cell.r + 1}`
      target.setMeta(ref, META_VERIFIED, cell.verified ? '1' : '0')
      target.setMeta(ref, META_SOURCE, cell.source ?? '')
    }
  }, [])

  const syncFromSheet = useCallback((): TableGrid => {
    const target = instance.current
    if (!target) return gridRef.current
    const data = target.getData()
    const merges = (target.getMerge() ?? {}) as Record<string, [number, number]>
    const meta = (target.getMeta() ?? {}) as Record<string, Record<string, unknown>>
    const next = sheetToGrid(data, merges, gridRef.current)
    next.cells = next.cells.map((cell) => {
      const ref = `${columnName(cell.c)}${cell.r + 1}`
      const entry = meta[ref]
      if (!entry) return cell
      const verified = entry[META_VERIFIED]
      const source = entry[META_SOURCE]
      return {
        ...cell,
        verified: verified === undefined ? cell.verified : verified === '1' || verified === 1 || verified === true,
        source: (source as TableGrid['cells'][number]['source']) || cell.source,
      }
    })
    return next
  }, [])

  const readBack = useCallback(() => {
    const next = syncFromSheet()
    gridRef.current = next
    setGrid(next)
  }, [syncFromSheet])

  // --- dialogs nativi ---------------------------------------------------------
  // La conferma distruttiva la diamo noi, prima di chiamare la libreria: la sua
  // sarebbe un secondo dialog nativo, quello che l'app ha eliminato altrove.
  useEffect(() => {
    const realAlert = window.alert
    const realConfirm = window.confirm
    window.alert = (message?: unknown) => {
      setNotice(String(message ?? ''))
    }
    window.confirm = () => true
    return () => {
      window.alert = realAlert
      window.confirm = realConfirm
    }
  }, [])

  // Il menù contestuale è costruito da una funzione che cambia a ogni render
  // (dipende dalla lingua e dalle azioni): il montaggio lo legge da un ref,
  // così il foglio non va rimontato a ogni cambio di stato.
  const buildMenuRef = useRef<(worksheet: SheetInstance, colIndex: number, rowIndex: number) => unknown>(() => [])

  // --- costruzione del foglio -------------------------------------------------
  /**
   * Monta (o rimonta) il foglio dal nostro modello.
   *
   * Le modifiche **strutturali** — inserire o togliere righe e colonne — non
   * passano dall'API della libreria: `insertColumn`/`insertRow` chiamano
   * `destroyMerge()`, che distrugge *tutti* i merge del foglio, e poi
   * `updateTableReferences` va in eccezione su un merge già dissolto
   * (`mergeCells[key][2]` di `undefined`). I merge restano nostri: la struttura
   * nuova la calcola `lib/grid.ts` (testato) e il foglio si rimonta da lì.
   */
  const mountSheet = useCallback(
    (model: TableGrid) => {
      const host = container.current
      if (!host) return
      instance.current = null
      host.innerHTML = ''
      const { data, mergeCells } = gridToSheet(model)
      // `jspreadsheet()` restituisce un array riempito DOPO il load: l'istanza
      // arriva da `onload`. Leggere subito `[0]` dà `undefined` — verificato.
      jspreadsheet(host, {
        // Gli eventi si leggono dal config dello **spreadsheet**, non dalla
        // worksheet: il dispatcher della libreria fa `r.config[evento]` sul
        // parent, quindi un handler dentro `worksheets` viene ignorato in
        // silenzio. Valeva per `onchange` — quello che fa partire l'autosave —
        // e il testo scritto in una cella restava nel DOM senza mai arrivare al
        // server: verificato nel sorgente e sul traffico di rete.
        contextMenu: (worksheet: SheetInstance, colIndex: number, rowIndex: number) =>
          buildMenuRef.current(worksheet, colIndex, rowIndex),
        onchange: () => readBack(),
        onmerge: () => readBack(),
        onselection: (worksheet: SheetInstance) => {
          const box = readSelection(worksheet)
          if (box) setActive({ r: box.y1, c: box.x1 })
        },
        onload: (spreadsheet: SpreadsheetInstance) => {
          const worksheet = spreadsheet?.worksheets?.[0]
          if (!worksheet) {
            setNotice(t('table.editorInitFailed'))
            return
          }
          instance.current = worksheet
          applyMeta(worksheet, model)
        },
        worksheets: [
          {
            data,
            mergeCells,
            minDimensions: [model.cols, model.rows],
            tableOverflow: true,
            tableWidth: '100%',
            // La griglia non cresce da sola: Tab sull'ultima cella non deve
            // aggiungere una colonna a un modello che descrive una scansione.
            // Si disattivano le opzioni *manual*, non `allowInsertColumn`/
            // `allowInsertRow`: quelle governano anche l'API.
            allowManualInsertColumn: false,
            allowManualInsertRow: false,
            columnDrag: false,
            columnSort: false,
            columnResize: true,
          },
        ],
      })
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [applyMeta, readBack, t],
  )

  useEffect(() => {
    if (!container.current || instance.current) return
    mountSheet(initialGrid)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // --- salvataggio ------------------------------------------------------------
  const doSave = useCallback(async (): Promise<boolean> => {
    setSaving(true)
    try {
      const next = syncFromSheet()
      gridRef.current = next
      setGrid(next)
      setOtsl(await saveRef.current(next))
      // Il modello riallineato è ora identico a quello sul server: senza questa
      // firma, il `setGrid` qui sopra farebbe ripartire l'effetto di autosave.
      savedRef.current = gridSignature(next)
      setNotice(null)
      return true
    } catch (error) {
      // Fallito: la firma resta indietro, così il prossimo cambiamento riprova.
      // Non si riprova da soli, altrimenti un 400 diventa un ciclo di errori.
      setNotice(error instanceof Error ? t('table.saveFailedWith', { msg: error.message }) : t('table.saveFailed'))
      return false
    } finally {
      setSaving(false)
    }
  }, [syncFromSheet, t])

  useEffect(() => {
    if (gridSignature(grid) === savedRef.current) return
    const timer = setTimeout(() => void doSave(), AUTOSAVE_DELAY)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [grid])

  // --- operazioni -------------------------------------------------------------
  /**
   * I merge che l'inserimento spezzerebbe: il punto cade **dentro** la loro
   * estensione. Inserire prima dell'inizio li sposta e basta, quindi non
   * vanno toccati — chiedere conferma a ogni inserimento su un foglio che ha
   * un merge qualsiasi sarebbe un falso allarme, e gli allarmi che scattano a
   * sproposito insegnano a ignorarli.
   */
  const mergesCrossing = (axis: 'row' | 'col', at: number): number => {
    let hit = 0
    for (const cell of gridRef.current.cells) {
      const rs = cell.rowspan ?? 1
      const cs = cell.colspan ?? 1
      if (rs <= 1 && cs <= 1) continue
      const crosses = axis === 'col' ? cell.c < at && at <= cell.c + cs - 1 : cell.r < at && at <= cell.r + rs - 1
      if (crosses) hit += 1
    }
    return hit
  }

  /**
   * Le modifiche strutturali sono **nostre**: la struttura nuova la calcola
   * `lib/grid.ts` — che sa già cosa succede a una cella unita quando le si
   * inserisce o toglie una riga — e il foglio si rimonta da lì.
   *
   * Non si usa `insertColumn`/`insertRow` della libreria: chiamano
   * `destroyMerge()`, che distrugge *tutti* i merge del foglio, e poi
   * `updateTableReferences` va in eccezione su un merge già dissolto
   * (`mergeCells[key][2]` di `undefined`). Verificato nel sorgente e a schermo:
   * inserire una colonna su un registro con una cella unita in testata
   * cancellava anche quella e lasciava lo stato interno incoerente.
   */
  const applyStructural = (next: TableGrid | null) => {
    if (!next) return
    gridRef.current = next
    setGrid(next)
    mountSheet(next)
  }

  // --- confini sull'immagine --------------------------------------------------
  // Lo spostamento di un confine non tocca le celle: il foglio non si rimonta e
  // la cella in scrittura resta dov'è. Aggiungere o rifiutare un confine cambia
  // invece il numero di tracce, quindi passa da `applyStructural`.
  const moveBoundary = (axis: Axis, index: number, value: number) => {
    setGrid((previous) => {
      const values = [...((axis === 'v' ? previous.vlines : previous.hlines) ?? [])]
      values[index] = value
      const next: TableGrid = axis === 'v' ? { ...previous, vlines: values } : { ...previous, hlines: values }
      gridRef.current = next
      return next
    })
  }

  const addBoundary = (axis: Axis, at: number) => {
    const next = insertBoundary(gridRef.current, axis, at)
    if (!next) {
      setNotice(t('table.boundaryInsertRefused'))
      return
    }
    setNotice(null)
    applyStructural(next)
  }

  const rejectBoundary = (axis: Axis, index: number) => {
    const next = dropBoundary(gridRef.current, axis, index)
    if (!next) {
      setNotice(t('table.boundaryDropRefused'))
      return
    }
    setNotice(null)
    applyStructural(next)
  }

  const insertRow = async (at: number, before: boolean) => {
    if (!Number.isFinite(at)) return
    const index = before ? at : at + 1
    if (mergesCrossing('row', index) > 0) {
      const ok = await confirm({ title: t('table.insertRowAbove'), message: t('table.insertDestroysMerges') })
      if (!ok) return
    }
    applyStructural(insertTrack(gridRef.current, 'row', index))
  }
  const insertCol = async (at: number, before: boolean) => {
    if (!Number.isFinite(at)) return
    const index = before ? at : at + 1
    if (mergesCrossing('col', index) > 0) {
      const ok = await confirm({ title: t('table.insertColLeft'), message: t('table.insertDestroysMerges') })
      if (!ok) return
    }
    applyStructural(insertTrack(gridRef.current, 'col', index))
  }
  const deleteRowAt = async (at: number) => {
    if (gridRef.current.rows < 2) return
    const ok = await confirm({ title: t('table.deleteRowAt'), message: t('table.deleteTrackConfirm') })
    if (!ok) return
    applyStructural(deleteTrack(gridRef.current, 'row', at))
  }
  const deleteColAt = async (at: number) => {
    if (gridRef.current.cols < 2) return
    const ok = await confirm({ title: t('table.deleteColAt'), message: t('table.deleteTrackConfirm') })
    if (!ok) return
    applyStructural(deleteTrack(gridRef.current, 'col', at))
  }

  /** Una cella è già dentro un'unione se il suo proprietario non è lei stessa. */
  const insideMerge = (r: number, c: number): boolean =>
    gridRef.current.cells.some(
      (cell) =>
        (cell.rowspan > 1 || cell.colspan > 1) &&
        (cell.r !== r || cell.c !== c) &&
        r >= cell.r &&
        r < cell.r + cell.rowspan &&
        c >= cell.c &&
        c < cell.c + cell.colspan,
    )

  /** Unisce l'intervallo **esteso** di una cella: con una sola cella selezionata
   *  «a destra» significa affiancarle quella accanto, non unire una cella con
   *  sé stessa (che `setMerge` accetta e non fa nulla). */
  const merge = (x1: number, y1: number, x2: number, y2: number) => {
    const target = instance.current
    if (!target) return
    if (![x1, y1, x2, y2].every(Number.isFinite)) return
    // `setMerge` su celle già unite non fa nulla e non lo dice: nessun errore,
    // nessuna unione. Meglio dirlo noi.
    const r1 = Math.min(y1, y2)
    const r2 = Math.max(y1, y2)
    const c1 = Math.min(x1, x2)
    const c2 = Math.max(x1, x2)
    for (let r = r1; r <= r2; r++) {
      for (let c = c1; c <= c2; c++) {
        if (insideMerge(r, c)) {
          setNotice(t('table.mergeOverlaps'))
          return
        }
      }
    }
    const before = JSON.stringify(target.getMerge() ?? {})
    const cs = Math.abs(x2 - x1) + 1
    const rs = Math.abs(y2 - y1) + 1
    target.setMerge(
      `${columnName(Math.min(x1, x2))}${Math.min(y1, y2) + 1}:${columnName(Math.max(x1, x2))}${Math.max(y1, y2) + 1}`,
      cs,
      rs,
    )
    if (JSON.stringify(target.getMerge() ?? {}) === before) {
      setNotice(t('table.mergeOverlaps'))
      return
    }
    readBack()
  }

  /**
   * Separa la cella unita. `removeMerge` vuole la **chiave dell'intervallo**
   * (`A2:B2`), non il nome della cella: con `A2` non fa nulla e non lo dice —
   * verificato. La chiave si ricava da `getMerge()`, cercando l'unione che
   * parte da quella cella.
   */
  const splitAt = (r: number, c: number) => {
    const target = instance.current
    if (!target) return
    const merges = target.getMerge() ?? {}
    const key = Object.keys(merges).find((range) => {
      const parsed = parseRange(range)
      return parsed !== null && parsed.r === r && parsed.c === c
    })
    if (!key) {
      setNotice(t('table.noMergeHere'))
      return
    }
    target.removeMerge(key)
    if (Object.keys(target.getMerge() ?? {}).length === Object.keys(merges).length) {
      setNotice(t('table.noMergeHere'))
      return
    }
    readBack()
  }

  const toggleVerified = () => {
    const target = instance.current
    if (!target) return
    const ref = `${columnName(active.c)}${active.r + 1}`
    const current = (target.getMeta()?.[ref] ?? {}) as Record<string, unknown>
    const next = current[META_VERIFIED] === '1' ? '0' : '1'
    target.setMeta(ref, META_VERIFIED, next)
    target.setMeta(ref, META_SOURCE, 'manual')
    readBack()
  }

  const togglePhantom = (column?: number) => {
    const c = Number.isFinite(column) ? (column as number) : active.c
    setGrid((previous) => {
      const has = previous.phantom_cols.includes(c)
      const next: TableGrid = {
        ...previous,
        phantom_cols: has ? previous.phantom_cols.filter((x) => x !== c) : [...previous.phantom_cols, c].sort((a, b) => a - b),
      }
      gridRef.current = next
      return next
    })
  }

  const onDetectClick = async () => {
    if (!onDetect) return
    const hasText = gridRef.current.cells.some((cell) => cell.text.trim() !== '')
    if (hasText) {
      const ok = await confirm({ title: t('table.detect'), message: t('table.detectOverwrite') })
      if (!ok) return
    }
    setDetecting(true)
    try {
      const out = await onDetect({ fill: detectFill })
      const next = out.grid
      gridRef.current = next
      setGrid(next)
      setDetectInfo(out)
      setNotice(null)
      // La struttura è cambiata sotto la libreria: si rimonta il foglio.
      mountSheet(next)
    } catch (error) {
      setDetectInfo(null)
      setNotice(error instanceof Error ? t('table.detectFailedWith', { msg: error.message }) : t('table.detectFailed'))
    } finally {
      setDetecting(false)
    }
  }

  /** Le voci del tasto destro. Sostituiscono quelle native (in inglese e senza
   *  unisci/separa) invece di affiancarsi: due menù con due vocabolari sono
   *  peggio di uno. */
  const buildMenu = (worksheet: SheetInstance, colIndex: number, rowIndex: number) => {
    // Il menù è tutto nostro: le voci native sono in inglese e non conoscono
    // unisci/separa/verificata. Copia e incolla restano da tastiera, dove la
    // libreria li gestisce già.
    // La cella cliccata è il riferimento di ripiego: la selezione può non
    // essere leggibile (o non essere ancora stata aggiornata dal click).
    const range = readSelection(worksheet) ?? {
      x1: colIndex,
      y1: rowIndex,
      x2: colIndex,
      y2: rowIndex,
    }
    return [
      {
        title: t('table.insertRowAbove'),
        onclick: () => void insertRow(range.y1, true),
      },
      {
        title: t('table.insertRowBelow'),
        onclick: () => void insertRow(range.y2 + 1, true),
      },
      {
        title: t('table.insertColLeft'),
        onclick: () => void insertCol(range.x1, true),
      },
      {
        title: t('table.insertColRight'),
        onclick: () => void insertCol(range.x2 + 1, true),
      },
      { type: 'line', title: '', onclick: () => {} },
      { title: t('table.mergeRight'), onclick: () => merge(range.x1, range.y1, range.x2 + 1, range.y1) },
      { title: t('table.mergeDown'), onclick: () => merge(range.x1, range.y1, range.x1, range.y2 + 1) },
      { title: t('table.splitCell'), onclick: () => splitAt(range.y1, range.x1) },
      { type: 'line', title: '', onclick: () => {} },
      { title: t('table.markVerified'), onclick: toggleVerified },
      { title: t('table.phantomColumn'), onclick: () => togglePhantom(range.x1) },
      { type: 'line', title: '', onclick: () => {} },
      { title: t('table.deleteRowAt'), onclick: () => void deleteRowAt(range.y1) },
      { title: t('table.deleteColAt'), onclick: () => void deleteColAt(range.x1) },
    ]
  }

  buildMenuRef.current = buildMenu

  const phantom = grid.phantom_cols

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
        <div>
          <span className="lbl">
            {t('table.rowsAction')}{' '}
            <span className="mono text-[color:var(--color-ink)]">{grid.rows}</span>
          </span>
          <div className="flex items-center gap-1">
            <button type="button" onClick={() => void insertRow(active.r, true)} className="btn btn-sm" title={t('table.insertRowAbove')}>
              <IconPlus size={11} />
              {t('table.above')}
            </button>
            <button type="button" onClick={() => void insertRow(active.r + 1, true)} className="btn btn-sm" title={t('table.insertRowBelow')}>
              <IconPlus size={11} />
              {t('table.below')}
            </button>
            <button type="button" onClick={() => void deleteRowAt(active.r)} className="btn btn-sm" title={t('table.deleteRowAt')}>
              <IconMinus size={11} />
              {t('table.rowShort')}
            </button>
          </div>
        </div>
        <div>
          <span className="lbl">
            {t('table.colsAction')}{' '}
            <span className="mono text-[color:var(--color-ink)]">{grid.cols}</span>
          </span>
          <div className="flex items-center gap-1">
            <button type="button" onClick={() => void insertCol(active.c, true)} className="btn btn-sm" title={t('table.insertColLeft')}>
              <IconPlus size={11} />
              {t('table.left')}
            </button>
            <button type="button" onClick={() => void insertCol(active.c + 1, true)} className="btn btn-sm" title={t('table.insertColRight')}>
              <IconPlus size={11} />
              {t('table.right')}
            </button>
            <button type="button" onClick={() => void deleteColAt(active.c)} className="btn btn-sm" title={t('table.deleteColAt')}>
              <IconMinus size={11} />
              {t('table.colShort')}
            </button>
          </div>
        </div>
        <div>
          <label className="lbl" htmlFor="table-header-rows-js">
            Header
          </label>
          <input
            id="table-header-rows-js"
            type="number"
            min={0}
            max={20}
            value={grid.header_rows ?? 0}
            onChange={(e) => {
              const value = Math.max(0, Math.min(20, Number(e.target.value) || 0))
              setGrid((previous) => {
                const next = { ...previous, header_rows: value }
                gridRef.current = next
                return next
              })
            }}
            className="fld fld-mono w-16"
            title={t('table.headerRowsTitle')}
          />
        </div>
        {onDetect && (
          <div>
            <span className="lbl">{t('table.detect')}</span>
            <div className="flex items-center gap-1">
              <button type="button" onClick={() => void onDetectClick()} disabled={detecting} className="btn btn-sm">
                {detecting ? t('table.detecting') : t('table.detect')}
              </button>
              <select
                value={detectFill}
                onChange={(e) => setDetectFill(e.target.value as 'none' | 'ocr' | 'model')}
                className="fld !w-auto text-xs"
                aria-label={t('table.detectFill')}
              >
                <option value="none">{t('table.detectStructureOnly')}</option>
                <option value="ocr">+ OCR per cella (CPU)</option>
                <option value="model">+ modello</option>
              </select>
            </div>
          </div>
        )}
        <div className="ml-auto flex items-center gap-2">
          <button type="button" onClick={() => void doSave()} disabled={saving} className="btn btn-sm btn-primary">
            <IconSave size={11} />
            {saving ? t('table.saving') : t('table.saveGrid')}
          </button>
        </div>
      </div>

      {notice && <WarnNotice title={t('table.notice')}>{notice}</WarnNotice>}
      {detectInfo?.warnings?.map((warning) => (
        <WarnNotice key={warning} title={t('table.detect')}>
          {warning}
        </WarnNotice>
      ))}

      {phantom.length > 0 && (
        <p className="text-[11px] text-[color:var(--color-ink-2)]">
          {t('table.phantomColumns', { n: phantom.map((c) => columnName(c)).join(', ') })}
        </p>
      )}

      {/* Nella vista di lavoro c'è spazio: il ritaglio a sinistra e il foglio a
          destra, così si guarda l'inchiostro mentre si corregge la cella che ne
          dipende. Se l'overlay lo disegna chi ospita, qui non si ripete. */}
      <div className="flex min-h-0 flex-1 gap-3">
        {withOverlay && cropUrl && (
          <figure className="m-0 w-1/2 shrink-0">
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

        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="tabularium-sheet min-h-0 flex-1 overflow-auto">
            <div ref={container} />
          </div>
          <p className="text-[11px] text-[color:var(--color-ink-2)]">{t('table.hintSheet')}</p>
        </div>
      </div>

      {otsl !== null && (
        <div className="border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2">
          <div className="flex items-center justify-between">
            <span className="lbl !mb-0">{t('table.otslNote')}</span>
            <button type="button" onClick={() => void navigator.clipboard?.writeText(otsl)} className="btn btn-sm">
              {t('common.copy')}
            </button>
          </div>
          <pre className="mono mt-1 max-h-32 overflow-auto text-[11px] whitespace-pre-wrap">{otsl}</pre>
        </div>
      )}
    </div>
  )
}
