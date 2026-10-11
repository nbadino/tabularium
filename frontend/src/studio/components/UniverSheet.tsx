/**
 * Il foglio della vista di lavoro, su **Univer**.
 *
 * Univer è un SDK, non un widget: monta un runtime su un contenitore e si
 * guida per comandi. Qui si compone il preset «sheets core» — griglia, merge,
 * selezione, copia/incolla, fill handle, menù contestuale — **senza** la sua
 * barra degli strumenti: dentro la nostra vista di lavoro il telaio è nostro,
 * e due barre sopra la stessa griglia sarebbero due prodotti in uno. Della sua
 * intestazione resta la sola barra della formula.
 *
 * Il nostro `TableGrid` resta la fonte di verità. `lib/univerGrid.ts` traduce
 * nei due sensi: Univer possiede testo, stile e merge, non la geometria sul
 * ritaglio né i metadati per cella (`source`/`verified`).
 *
 * A ogni comando si rilegge il documento e lo si confronta con l'impronta di
 * ciò che è già noto: si notifica solo ciò che è cambiato davvero. Serve
 * perché Univer emette un comando anche per la sola selezione, e perché il
 * salvataggio non deve ri-innescarsi da solo.
 */
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { LoadingGrid } from '../../app/ui'
import { prefersReducedMotion } from '../../lib/motion'
import { createUniver, LocaleType, mergeLocales } from '@univerjs/presets'
import {
  ContextMenuPosition,
  ContextMenuGroup,
  IMenuManagerService,
  IUIPartsService,
  MenuItemType,
  MenuManagerPosition,
  SheetExtension,
  UniverSheetsCorePreset,
} from '@univerjs/preset-sheets-core'
import type { SpreadsheetSkeleton, UniverRenderingContext } from '@univerjs/preset-sheets-core'
import { CommandType, ICommandService } from '@univerjs/core'
import UniverItIT from '@univerjs/preset-sheets-core/locales/it-IT'
import UniverEnUS from '@univerjs/preset-sheets-core/locales/en-US'
import UniverFrFR from '@univerjs/preset-sheets-core/locales/fr-FR'
import type { FUniver } from '@univerjs/core/lib/facade'
import type { IScale, Univer } from '@univerjs/core'
import '@univerjs/preset-sheets-core/lib/index.css'

import type { TableGrid } from '../../lib/types'
import { gridToUniver, univerGridSignature, univerToGrid } from '../../lib/univerGrid'
import { setVerified } from '../../lib/grid'
import { useI18n } from '../../i18n'
import { toFractionGlyphs } from '../../lib/fractions'

/** I comandi nostri: ognuno esegue un'operazione sulla selezione.
 *
 *  Le voci di menù di Univer invocano un comando, non una callback: è il loro
 *  modo di far passare ogni azione dalla stessa catena. Un comando per
 *  operazione, invece di uno con un parametro: la voce dice cosa fa, e la
 *  catena dei comandi resta leggibile.
 *
 *  Anche «unisci» e «separa la cella» sono nostri. I comandi di merge di Univer
 *  esistono, ma pretendono i loro parametri e invocati da una voce di menù
 *  sollevano `Cannot read properties of undefined (reading 'some')`: provato.
 *  E la fusione la decidiamo noi comunque — rifiutiamo i casi ambigui invece di
 *  sceglierli, e il testo riscritto torna non verificato. */
export type ColumnOp =
  | 'split'
  | 'join'
  | 'normalize'
  | 'upper'
  | 'lower'
  | 'title'
  | 'fill'
  | 'merge'
  | 'unmerge'

const COLUMN_COMMANDS: { op: ColumnOp; id: string; title: string; order: number }[] = [
  { op: 'split', id: 'tabularium.command.split-column', title: 'tabularium.splitColumn', order: 200 },
  { op: 'join', id: 'tabularium.command.join-column', title: 'tabularium.joinColumn', order: 201 },
  { op: 'normalize', id: 'tabularium.command.normalize-spaces', title: 'tabularium.normalizeSpaces', order: 202 },
  { op: 'upper', id: 'tabularium.command.case-upper', title: 'tabularium.caseUpper', order: 203 },
  { op: 'lower', id: 'tabularium.command.case-lower', title: 'tabularium.caseLower', order: 204 },
  { op: 'title', id: 'tabularium.command.case-title', title: 'tabularium.caseTitle', order: 205 },
  { op: 'fill', id: 'tabularium.command.fill-down', title: 'tabularium.fillDown', order: 206 },
  { op: 'merge', id: 'tabularium.command.merge-cells', title: 'tabularium.merge', order: 207 },
  { op: 'unmerge', id: 'tabularium.command.unmerge-cell', title: 'tabularium.unmerge', order: 208 },
]

/** Le etichette che Univer disegna nel suo menù: si registrano nella sua
 *  lingua, altrimenti al posto della voce comparirebbe la chiave. */
const LABELS_IT = {
  verify: 'Segna come verificata',
  unverify: 'Segna come non verificata',
  splitColumn: 'Separa per separatore…',
  joinColumn: 'Unisci con la colonna a destra',
  normalizeSpaces: 'Normalizza gli spazi',
  caseUpper: 'Maiuscole',
  caseLower: 'Minuscole',
  caseTitle: 'Iniziale maiuscola',
  fillDown: 'Propaga il valore verso il basso',
  merge: 'Unisci le celle',
  unmerge: 'Separa la cella unita',
}
const LABELS_EN = {
  verify: 'Mark as verified',
  unverify: 'Mark as not verified',
  splitColumn: 'Split by separator…',
  joinColumn: 'Merge with the column to the right',
  normalizeSpaces: 'Normalize spaces',
  caseUpper: 'Uppercase',
  caseLower: 'Lowercase',
  caseTitle: 'Capitalize',
  fillDown: 'Fill the value down',
  merge: 'Merge cells',
  unmerge: 'Unmerge the cell',
}
const LABELS_FR = {
  verify: 'Marquer comme vérifiée',
  unverify: 'Marquer comme non vérifiée',
  splitColumn: 'Séparer par séparateur…',
  joinColumn: 'Fusionner avec la colonne à droite',
  normalizeSpaces: 'Normaliser les espaces',
  caseUpper: 'Majuscules',
  caseLower: 'Minuscules',
  caseTitle: 'Initiale majuscule',
  fillDown: 'Propager la valeur vers le bas',
  merge: 'Fusionner les cellules',
  unmerge: 'Séparer la cellule fusionnée',
}

/** La lingua di Univer segue quella dell'app: il foglio non deve parlare
 *  italiano dentro un'interfaccia inglese. */
function univerLanguage(locale: string) {
  if (locale.startsWith('en')) {
    return { type: LocaleType.EN_US, bundle: UniverEnUS, labels: LABELS_EN }
  }
  if (locale.startsWith('fr')) {
    return { type: LocaleType.FR_FR, bundle: UniverFrFR, labels: LABELS_FR }
  }
  return { type: LocaleType.IT_IT, bundle: UniverItIT, labels: LABELS_IT }
}

/** L'intervallo scelto nel foglio: serve intero a unire, la sola prima colonna
 *  alle operazioni di colonna. */
export interface SheetSelection {
  startRow: number
  startColumn: number
  endRow: number
  endColumn: number
}

/** Una tinta su un insieme di celle — le sospette dei controlli aritmetici,
 *  le verificate dall'annotatore — dipinta **sul canvas** e non
 *  nel documento: uno stile scritto con i comandi di Univer entrerebbe nella
 *  sua storia (Ctrl+Z toglierebbe l'evidenziazione) e nel modello salvato.
 *  Il livello sta sopra lo sfondo delle celle (21) e sotto il testo (45): la
 *  cifra da controllare resta leggibile. */
class CellShading extends SheetExtension {
  protected override Z_INDEX = 30
  constructor(
    override uKey: string,
    private cells: [number, number][],
    private fill: string,
    private stroke: string | null,
  ) {
    super()
  }
  override draw(ctx: UniverRenderingContext, _scale: IScale, skeleton: SpreadsheetSkeleton) {
    ctx.save()
    ctx.fillStyle = this.fill
    ctx.lineWidth = 2
    if (this.stroke) ctx.strokeStyle = this.stroke
    for (const [r, c] of this.cells) {
      const cell = skeleton.getCellWithCoordByIndex(r, c, false)
      const box = cell.isMerged || cell.isMergedMainCell ? cell.mergeInfo : cell
      const w = box.endX - box.startX
      const h = box.endY - box.startY
      ctx.fillRect(box.startX, box.startY, w, h)
      if (this.stroke) ctx.strokeRect(box.startX + 1, box.startY + 1, w - 2, h - 2)
    }
    ctx.restore()
  }
}

/** Le celle confermate dall'annotatore, per la tinta verde. */
function verifiedCells(grid: TableGrid): [number, number][] {
  return grid.cells.filter((cell) => cell.verified && (cell.text ?? '').trim() !== '').map((cell) => [cell.r, cell.c])
}


// Geometria del foglio (v. `lib/univerGrid.ts`): intestazione di colonna e righe.
const COLUMN_HEADER_PX = 22
const ROW_PX = 24
/** Un passo per riga, e mai più di questo in tutto: una tabella lunga non fa aspettare. */
const REVEAL_STEP_MS = 25
const REVEAL_MAX_MS = 450

/** La tela delle celle: la più alta. Quella della barra della formula è una
 *  striscia di ~27 px, e finché Univer non ha dimensionato il foglio è l'unica
 *  ad avere un'altezza. */
function sheetCanvas(host: HTMLElement): HTMLCanvasElement | null {
  const tall = Array.from(host.querySelectorAll('canvas')).filter((c) => c.clientHeight > 60)
  return tall.sort((a, b) => b.clientHeight - a.clientHeight)[0] ?? null
}

/**
 * Alla prima apertura il foglio si scopre dall'alto, una riga alla volta,
 * come un foglio che esce dalla stampa. È solo una maschera (`clip-path` a
 * scatti) sul disegno già completo: i dati ci sono tutti fin dall'inizio, e
 * l'autosave non vede mai una griglia a metà. Contano solo le righe in vista.
 */
function revealRows(host: HTMLElement, rows: number): void {
  if (prefersReducedMotion() || !host.animate) return
  const box = host.getBoundingClientRect()
  if (box.height <= 0) return
  // La tela del foglio comincia sotto quella della barra della formula: si misura.
  const sheet = sheetCanvas(host)
  const firstRow = (sheet ? sheet.getBoundingClientRect().top - box.top : 0) + COLUMN_HEADER_PX
  const visible = Math.max(1, Math.min(rows, Math.ceil((box.height - firstRow) / ROW_PX)))
  const frames: Keyframe[] = []
  for (let k = 0; k <= visible; k++) {
    const shown = Math.min(box.height, firstRow + k * ROW_PX)
    frames.push({ clipPath: `inset(0 0 ${box.height - shown}px 0)`, offset: k / (visible + 1), easing: 'step-end' })
  }
  frames.push({ clipPath: 'inset(0 0 0 0)', offset: 1 })
  host.animate(frames, { duration: Math.min(REVEAL_MAX_MS, (visible + 1) * REVEAL_STEP_MS) })
}

interface UniverSheetProps {
  /** Il modello di partenza: si carica una volta, poi comanda Univer. */
  grid: TableGrid
  /** Chiamata a ogni cambiamento reale (celle, merge, dimensioni). */
  onGridChange: (grid: TableGrid) => void
  /** Invocata da una voce di operazione di colonna, con la selezione corrente. */
  onColumnOp: (op: ColumnOp, selection: SheetSelection) => void
  /** Celle da evidenziare (controlli aritmetici), [riga, colonna]. */
  suspects?: [number, number][]
  /** La cella attiva, per dire accanto al foglio perché è evidenziata. */
  onSelectionChange?: (selection: SheetSelection) => void
  /** Prima apertura: lo scheletro resta finché il foglio non ha disegnato,
   *  poi i due si scambiano in dissolvenza. Ai rimontaggi (una colonna
   *  aggiunta) il foglio non ripassa dallo scheletro. */
  intro?: boolean
  /** «8 1/8» → «8⅛» alla conferma della cella (v. `lib/fractions.ts`). */
  autoFractions?: boolean
}

export default function UniverSheet({ grid, onGridChange, onColumnOp, suspects, onSelectionChange, autoFractions = false, intro = false }: UniverSheetProps) {
  const { t, locale } = useI18n()
  const host = useRef<HTMLDivElement | null>(null)
  const [failed, setFailed] = useState(false)
  const [ready, setReady] = useState(!intro)
  // Lo scheletro sfuma insieme al foglio che compare, e si toglie a
  // dissolvenza finita: toglierlo subito lascerebbe un attimo di vuoto.
  const [skeletonGone, setSkeletonGone] = useState(!intro)
  const gridRef = useRef(grid)
  const changeRef = useRef(onGridChange)
  changeRef.current = onGridChange
  const opRef = useRef(onColumnOp)
  opRef.current = onColumnOp
  const selectRef = useRef(onSelectionChange)
  selectRef.current = onSelectionChange
  const fractionsRef = useRef(autoFractions)
  fractionsRef.current = autoFractions
  /** Ridipinge le celle sospette; `null` finché il foglio non è montato. */
  const paintRef = useRef<((cells: [number, number][]) => void) | null>(null)
  const suspectsRef = useRef(suspects ?? [])
  suspectsRef.current = suspects ?? []

  useEffect(() => {
    const container = host.current
    if (!container) return
    let univer: Univer | null = null
    let unsubscribe: { dispose?: () => void } | null = null
    let selectionListener: { dispose: () => void } | null = null
    let editListener: { dispose: () => void } | null = null
    let cancelled = false
    let readyTimeout: number | undefined
    const language = univerLanguage(locale)

    // Univer legge la dimensione del contenitore quando monta: dentro una
    // modale appena aperta il layout non è ancora assestato, e montarlo subito
    // darebbe un foglio alto zero. Un frame d'attesa risolve.
    const frame = requestAnimationFrame(() => {
      if (cancelled) return
      try {
        const created = createUniver({
          locale: language.type,
          locales: {
            [language.type]: mergeLocales(language.bundle, { tabularium: language.labels }),
          },
          presets: [
            UniverSheetsCorePreset({
              container,
              // L'header resta solo per la barra della formula: mostra il
              // contenuto intero della cella attiva, come in Excel, dove la
              // colonna stretta lo tronca. Niente barra degli strumenti.
              header: true,
              toolbar: false,
              formulaBar: true,
              footer: false,
              // Le celle sono testo per scelta («1,837» resta com'è stampato):
              // l'avviso «numero memorizzato come testo» e il suo triangolino
              // verde comparirebbero su ogni cifra. In 0.25.1 il messaggio
              // non ha nemmeno la traduzione e mostra la chiave grezza.
              sheets: { disableForceStringAlert: true, disableForceStringMark: true },
            }),
          ],
        })
        univer = created.univer
        const api: FUniver = created.univerAPI
        api.createWorkbook(gridToUniver(gridRef.current))

        // Pronto quando la tela del foglio (non quella della barra della
        // formula, che arriva prima) ha preso le sue dimensioni, più un frame
        // per disegnarla. Un tetto a 2 s: in una scheda in background i
        // frame sono sospesi, e il foglio non deve restare invisibile.
        let revealed = false
        const markReady = () => {
          if (cancelled || revealed) return
          revealed = true
          if (intro) revealRows(container, gridRef.current.rows)
          setReady(true)
          window.setTimeout(() => !cancelled && setSkeletonGone(true), 400)
        }
        const waitCanvas = (attempt = 0) => {
          if (cancelled) return
          if (sheetCanvas(container) || attempt > 90) requestAnimationFrame(markReady)
          else requestAnimationFrame(() => waitCanvas(attempt + 1))
        }
        waitCanvas()
        readyTimeout = window.setTimeout(markReady, 2000)

        // Un'estensione per tinta: cambiate le celle, si toglie la vecchia
        // (che rende il foglio «sporco») e si registra la nuova. Appena creato
        // il documento l'unità di rendering non esiste ancora: si riprova ai
        // frame successivi. L'evidenziazione è un aiuto e non deve mai
        // impedire al foglio di montarsi.
        const painter = (uKey: string, fill: string, stroke: string | null, latest: () => [number, number][]) => {
          let shading: { dispose: () => void } | null = null
          const paint = (cells: [number, number][], attempt = 0) => {
            const unitId = api.getActiveWorkbook()?.getId()
            if (!unitId || cancelled) return
            try {
              shading?.dispose()
              shading = api.registerSheetMainExtension(unitId, new CellShading(uKey, cells, fill, stroke))
            } catch {
              shading = null
              if (attempt < 30) requestAnimationFrame(() => paint(latest(), attempt + 1))
            }
          }
          return paint
        }
        const paintSuspects = painter('tabularium-suspects', 'rgba(232, 161, 0, 0.16)', '#e8a100', () => suspectsRef.current)
        const paintVerified = painter('tabularium-verified', 'rgba(46, 125, 50, 0.12)', null, () => verifiedCells(gridRef.current))
        paintRef.current = (cells) => paintSuspects(cells)
        paintSuspects(suspectsRef.current)
        paintVerified(verifiedCells(gridRef.current))

        // Impronta di ciò che è già noto: riconosce i comandi che non hanno
        // cambiato niente (selezione, scroll, un click su una cella).
        let known = univerGridSignature(gridRef.current)
        // Si assegna più sotto, quando esiste lo stato della cella attiva: ogni
        // comando — anche solo di selezione — lo riallinea.
        let afterCommand = () => {}
        unsubscribe = api.onCommandExecuted(() => {
          afterCommand()
          const data = api.getActiveWorkbook()?.save()
          if (!data) return
          const next = univerToGrid(data, gridRef.current)
          const signature = univerGridSignature(next)
          if (signature === known) return
          known = signature
          gridRef.current = next
          changeRef.current(next)
          // Una cella riscritta perde la verifica: la tinta la segue.
          paintVerified(verifiedCells(next))
          refreshActive()
        })

        // Il telaio di Univer non ha l'unisci nel menù contestuale (sta nella
        // barra strumenti, che qui non c'è): si aggiunge la voce riusando i
        // suoi comandi, così l'undo resta il suo.
        const injector = univer.__getInjector()
        const commands = injector.get(ICommandService)

        // La selezione si legge al momento del comando: il menù si apre su una
        // scelta che cambia, e un intervallo serve intero (per unire) o solo la
        // sua prima colonna (per le operazioni di colonna).
        const selection = () => {
          const range = api.getActiveSheet()?.worksheet.getSelection()?.getActiveRange()?.getRange()
          return {
            startRow: range?.startRow ?? 0,
            startColumn: range?.startColumn ?? 0,
            endRow: range?.endRow ?? 0,
            endColumn: range?.endColumn ?? 0,
          }
        }

        // ✓ / ✕: la verifica è un metadato nostro, non un comando di Univer.
        // Si aggiorna qui, dove vive la copia del modello che il foglio usa
        // per i confronti; altrimenti la prossima modifica la cancellerebbe.
        // Lo stato della cella attiva (verificata, no, vuota = null) accende
        // il ✓ nella barra della formula.
        const active = { value: null as boolean | null, listeners: new Set<() => void>() }
        // L'evento di selezione porta la cella nuova: la selezione «attiva»
        // del foglio in quel momento è ancora quella di prima.
        const refreshActive = (range: { startRow: number; startColumn: number } = selection()) => {
          const { startRow, startColumn } = range
          const cell = gridRef.current.cells.find((c) => c.r === startRow && c.c === startColumn)
          const value = cell && (cell.text ?? '').trim() !== '' ? Boolean(cell.verified) : null
          if (value === active.value) return
          active.value = value
          active.listeners.forEach((listener) => listener())
        }
        const verify = (verified: boolean) => {
          const next = setVerified(gridRef.current, selection(), verified)
          const signature = univerGridSignature(next)
          if (signature !== known) {
            known = signature
            gridRef.current = next
            changeRef.current(next)
            paintVerified(verifiedCells(next))
          }
          refreshActive()
        }
        const labels = language.labels
        const subscribe = (listener: () => void) => {
          active.listeners.add(listener)
          return () => active.listeners.delete(listener)
        }
        // In fondo alla barra della formula (lo slot «formula-aux» di Univer):
        // colorati, per non confondersi con ✕ / ✓ della modifica accanto a fx.
        function VerifyButtons() {
          const value = useSyncExternalStore(subscribe, () => active.value)
          const base = 'flex h-6 w-6 items-center justify-center border text-[13px] leading-none disabled:opacity-40'
          return (
            <div className="flex shrink-0 items-center gap-1 border-l border-[color:var(--color-rule)] px-2">
              <button
                type="button"
                title={labels.verify}
                aria-label={labels.verify}
                aria-pressed={value === true}
                disabled={value === null}
                onClick={() => verify(true)}
                className={`${base} ${value ? 'border-[color:var(--color-ok)] bg-[color:var(--color-ok)] text-white' : 'border-[color:var(--color-ok)] text-[color:var(--color-ok)]'}`}
              >
                ✓
              </button>
              <button
                type="button"
                title={labels.unverify}
                aria-label={labels.unverify}
                disabled={value === null}
                onClick={() => verify(false)}
                className={`${base} border-[color:var(--color-rule-strong)] text-[color:var(--color-ink-2)]`}
              >
                ✕
              </button>
            </div>
          )
        }
        injector.get(IUIPartsService).registerComponent('formula-aux', () => VerifyButtons)
        afterCommand = () => refreshActive()
        refreshActive()

        selectionListener = api.addEvent(api.Event.SelectionChanged, ({ selections }) => {
          const range = selections[0]
          if (range) {
            selectRef.current?.(range)
            refreshActive(range)
          }
        })

        // La conversione è un comando a sé, dopo quello della cella: Ctrl+Z la
        // annulla da sola e restituisce la frazione come l'hai digitata.
        editListener = api.addEvent(api.Event.SheetEditEnded, ({ worksheet, row, column, isConfirm }) => {
          if (!isConfirm || !fractionsRef.current) return
          const range = worksheet.getRange(row, column)
          const text = range.getValue()
          if (typeof text !== 'string') return
          const converted = toFractionGlyphs(text)
          if (converted !== text) void range.setValue(converted)
        })

        for (const entry of COLUMN_COMMANDS) {
          commands.registerCommand({
            id: entry.id,
            type: CommandType.COMMAND,
            handler: () => {
              opRef.current(entry.op, selection())
              return true
            },
          })
        }

        const entry = (key: string, title: string, commandId: string, order: number) => ({
          [key]: {
            order,
            menuItemFactory: () => ({ id: key, type: MenuItemType.BUTTON, title, commandId }),
          },
        })
        const group = COLUMN_COMMANDS.reduce(
          (acc, item) => ({ ...acc, ...entry(item.id, item.title, item.id, item.order) }),
          {},
        )
        injector.get(IMenuManagerService).appendRootMenu({
          [MenuManagerPosition.CONTEXT_MENU]: {
            // Sia sul corpo del foglio sia sull'intestazione di colonna: il
            // gesto è «tasto destro su una colonna», e le due strade devono
            // portare allo stesso posto.
            [ContextMenuPosition.MAIN_AREA]: { [ContextMenuGroup.OTHERS]: group },
            [ContextMenuPosition.COL_HEADER]: { [ContextMenuGroup.OTHERS]: group },
          },
        })
      } catch (error) {
        // eslint-disable-next-line no-console
        console.error('Univer non si è potuto montare', error)
        setFailed(true)
      }
    })

    return () => {
      cancelled = true
      window.clearTimeout(readyTimeout)
      paintRef.current = null
      selectionListener?.dispose()
      editListener?.dispose()
      cancelAnimationFrame(frame)
      unsubscribe?.dispose?.()
      // Univer monta una **sua** radice React dentro il contenitore: chiuderla
      // mentre React sta già renderizzando fa lamentare React («Attempted to
      // synchronously unmount a root while React was already rendering»), e
      // succede a ogni rimontaggio — cioè a ogni colonna aggiunta. Un tick dopo
      // siamo fuori dal render e la radice si chiude da sola.
      const instance = univer
      if (instance) setTimeout(() => instance.dispose(), 0)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    paintRef.current?.(suspects ?? [])
  }, [suspects])

  if (failed) {
    return (
      <p className="border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-3 text-[12px] text-[color:var(--color-ink-2)]">
        {t('table.univerFailed')}
      </p>
    )
  }
  return (
    <div className="relative h-full min-h-[320px] w-full">
      <div ref={host} data-testid="univer-host" className="sheet-host h-full w-full" data-ready={ready || undefined} />
      {!skeletonGone && (
        <div className="sheet-skeleton pointer-events-none absolute inset-0">
          <LoadingGrid label={t('content.tableLoading')} rows={10} />
        </div>
      )}
    </div>
  )
}
