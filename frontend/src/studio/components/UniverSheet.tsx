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
 * Solo le mutazioni dei dati rileggono il documento e ne confrontano
 * l'impronta: selezione e scorrimento non serializzano il foglio.
 */
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { LoadingGrid } from '../../app/ui'
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
import { CommandType, ICommandService, IUndoRedoService } from '@univerjs/core'
import type { ICellData } from '@univerjs/core'
import UniverItIT from '@univerjs/preset-sheets-core/locales/it-IT'
import UniverEnUS from '@univerjs/preset-sheets-core/locales/en-US'
import UniverFrFR from '@univerjs/preset-sheets-core/locales/fr-FR'
import type { FUniver } from '@univerjs/core/lib/facade'
import type { IScale, Univer } from '@univerjs/core'
import { IContextMenuService } from '@univerjs/ui'
import '@univerjs/preset-sheets-core/lib/index.css'

import type { TableGrid } from '../../lib/types'
import { gridToUniver, univerGridSignature, univerToGrid, UNIVER_SHEET_ID } from '../../lib/univerGrid'
import { setVerified } from '../../lib/grid'
import { useI18n } from '../../i18n'
import { commandAffectsGrid } from '../../lib/univerEvents'
import { toFractionGlyphs } from '../../lib/fractions'
import { wheelZoomFactor } from '../canvasGeometry'

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
  | 'fractions'
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

/** Le operazioni su righe e colonne: **struttura**, non testo.
 *
 *  Sono nostre e non di Univer perché solo `lib/grid.ts` sa cosa succede a una
 *  cella unita che attraversa il punto e come seguono `vlines`/`hlines`/
 *  `phantom_cols`: le stesse funzioni di `insertBoundary`/`dropBoundary`, ma sul
 *  numero di tracce invece che sul pixel. Inserire una riga o una colonna è come
 *  si corregge un'intestazione o una colonna che il modello ha mancato. */
export type TrackOp =
  | 'insertRowAbove'
  | 'insertRowBelow'
  | 'deleteRow'
  | 'insertColLeft'
  | 'insertColRight'
  | 'deleteCol'

type HeaderOp = 'saveHeader' | 'applyHeader'

const HEADER_COMMANDS: { op: HeaderOp; id: string; title: string; order: number }[] = [
  { op: 'saveHeader', id: 'tabularium.command.save-known-header', title: 'tabularium.saveKnownHeader', order: 50 },
  { op: 'applyHeader', id: 'tabularium.command.apply-known-header', title: 'tabularium.applyKnownHeader', order: 51 },
]

const TRACK_COMMANDS: { op: TrackOp; id: string; title: string; order: number }[] = [
  { op: 'insertRowAbove', id: 'tabularium.command.insert-row-above', title: 'tabularium.insertRowAbove', order: 100 },
  { op: 'insertRowBelow', id: 'tabularium.command.insert-row-below', title: 'tabularium.insertRowBelow', order: 101 },
  { op: 'deleteRow', id: 'tabularium.command.delete-row', title: 'tabularium.deleteRow', order: 102 },
  { op: 'insertColLeft', id: 'tabularium.command.insert-col-left', title: 'tabularium.insertColLeft', order: 103 },
  { op: 'insertColRight', id: 'tabularium.command.insert-col-right', title: 'tabularium.insertColRight', order: 104 },
  { op: 'deleteCol', id: 'tabularium.command.delete-col', title: 'tabularium.deleteCol', order: 105 },
]

/** Le etichette che Univer disegna nel suo menù: si registrano nella sua
 *  lingua, altrimenti al posto della voce comparirebbe la chiave. */
const LABELS_IT = {
  saveKnownHeader: 'Salva intestazione selezionata…',
  applyKnownHeader: 'Sostituisci con intestazione nota…',
  verify: 'Segna come verificata',
  unverify: 'Segna come non verificata',
  insertRowAbove: 'Inserisci una riga sopra',
  insertRowBelow: 'Aggiungi una riga',
  deleteRow: 'Elimina la riga',
  insertColLeft: 'Inserisci una colonna a sinistra',
  insertColRight: 'Aggiungi una colonna',
  deleteCol: 'Elimina la colonna',
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
  saveKnownHeader: 'Save selected header…',
  applyKnownHeader: 'Replace with known header…',
  verify: 'Mark as verified',
  unverify: 'Mark as not verified',
  insertRowAbove: 'Insert row above',
  insertRowBelow: 'Add a row',
  deleteRow: 'Delete row',
  insertColLeft: 'Insert column left',
  insertColRight: 'Add a column',
  deleteCol: 'Delete column',
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
  saveKnownHeader: 'Enregistrer l’en-tête sélectionné…',
  applyKnownHeader: 'Remplacer par un en-tête connu…',
  verify: 'Marquer comme vérifiée',
  unverify: 'Marquer comme non vérifiée',
  insertRowAbove: 'Insérer une ligne au-dessus',
  insertRowBelow: 'Ajouter une ligne',
  deleteRow: 'Supprimer la ligne',
  insertColLeft: 'Insérer une colonne à gauche',
  insertColRight: 'Ajouter une colonne',
  deleteCol: 'Supprimer la colonne',
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

/** I comandi nativi di Univer che nascondiamo.
 *
 *  Fanno quello che fanno i nostri (inserisci/elimina riga e colonna, blocca,
 *  proteggi) ma operano sulla griglia di Univer senza toccare il nostro modello:
 *  `header_rows`, `phantom_cols`, `misprints`. Due voci per lo stesso gesto, una
 *  delle quali sbagliata, è esattamente la confusione da togliere. */
const HIDDEN_NATIVE_MENUS = {
  // I menu degli header espongono anche comandi diretti, fuori dai sottomenu.
  'sheet.command.insert-row-before': { hidden: true },
  'sheet.command.insert-row-after': { hidden: true },
  'sheet.command.insert-multi-rows-above': { hidden: true },
  'sheet.command.insert-multi-rows-after': { hidden: true },
  'sheet.command.insert-col-before': { hidden: true },
  'sheet.command.insert-col-after': { hidden: true },
  'sheet.command.insert-multi-cols-before': { hidden: true },
  'sheet.command.insert-multi-cols-right': { hidden: true },
  'sheet.command.remove-row-confirm': { hidden: true },
  'sheet.command.remove-col-confirm': { hidden: true },
  'sheet.menu.row-insert': { hidden: true },
  'sheet.menu.col-insert': { hidden: true },
  'sheet.menu.cell-insert': { hidden: true },
  'sheet.menu.delete': { hidden: true },
  'sheet.menu.sheet-frozen': { hidden: true },
  'sheet.column-header-menu.sheet-frozen': { hidden: true },
  'sheet.row-header-menu.sheet-frozen': { hidden: true },
  'sheet.contextMenu.permission': { hidden: true },
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

/** Il ponte imperativo per sostituire il contenuto del foglio senza smontarlo.
 *  Serve alle operazioni strutturali: cambiare righe, colonne e merge non deve
 *  azzerare scroll, selezione o cronologia visibile all'annotatore. */
export interface UniverController {
  applyGrid: (grid: TableGrid) => boolean
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


/** La tela del foglio è più alta della barra della formula. */
function sheetCanvas(host: HTMLElement): HTMLCanvasElement | null {
  return Array.from(host.querySelectorAll('canvas')).find((c) => c.clientHeight > 60) ?? null
}

interface UniverSheetProps {
  /** Il modello di partenza: si carica una volta, poi comanda Univer. */
  grid: TableGrid
  /** Chiamata a ogni cambiamento reale (celle, merge, dimensioni). */
  onGridChange: (grid: TableGrid, beforeEdit?: TableGrid) => void
  /** Invocata da una voce di operazione di colonna, con la selezione corrente. */
  onColumnOp: (op: ColumnOp, selection: SheetSelection) => void
  /** Invocata da una voce su righe/colonne (struttura), con la selezione corrente. */
  onTrackOp: (op: TrackOp, selection: SheetSelection) => void
  /** Salva o applica un'intestazione nota alla selezione corrente. */
  onSaveHeader?: (selection: SheetSelection) => void
  onApplyHeader?: (selection: SheetSelection) => void
  /** Celle da evidenziare (controlli aritmetici), [riga, colonna]. */
  suspects?: [number, number][]
  /** La cella attiva, per dire accanto al foglio perché è evidenziata. */
  onSelectionChange?: (selection: SheetSelection) => void
  /** Controller mounted with the sheet; `null` while it is not ready. */
  onControllerChange?: (controller: UniverController | null) => void
  /** Undo/redo owned by the workspace's snapshot history. */
  onUndo?: () => void
  onRedo?: () => void
  /** Mostra lo scheletro soltanto finché la tela del foglio è pronta. */
  intro?: boolean
}

export default function UniverSheet({ grid, onGridChange, onColumnOp, onTrackOp, onSaveHeader, onApplyHeader, suspects, onSelectionChange, onControllerChange, onUndo, onRedo, intro = false }: UniverSheetProps) {
  const { t, locale } = useI18n()
  const host = useRef<HTMLDivElement | null>(null)
  const [failed, setFailed] = useState(false)
  const [ready, setReady] = useState(!intro)
  // Nessuna attesa decorativa dopo che il foglio è pronto.
  const [skeletonGone, setSkeletonGone] = useState(!intro)
  const gridRef = useRef(grid)
  const changeRef = useRef(onGridChange)
  changeRef.current = onGridChange
  const opRef = useRef(onColumnOp)
  opRef.current = onColumnOp
  const trackOpRef = useRef(onTrackOp)
  trackOpRef.current = onTrackOp
  const saveHeaderRef = useRef(onSaveHeader)
  saveHeaderRef.current = onSaveHeader
  const applyHeaderRef = useRef(onApplyHeader)
  applyHeaderRef.current = onApplyHeader
  const selectRef = useRef(onSelectionChange)
  selectRef.current = onSelectionChange
  const controllerChangeRef = useRef(onControllerChange)
  controllerChangeRef.current = onControllerChange
  const undoRef = useRef(onUndo)
  undoRef.current = onUndo
  const redoRef = useRef(onRedo)
  redoRef.current = onRedo
  const applyingGridRef = useRef(false)
  /** Ridipinge le celle sospette; `null` finché il foglio non è montato. */
  const paintRef = useRef<((cells: [number, number][]) => void) | null>(null)
  const suspectsRef = useRef(suspects ?? [])
  suspectsRef.current = suspects ?? []

  // Durante il lavoro sul foglio il pinch deve essere un gesto dell'editor,
  // non lo zoom della pagina. Il meta viewport vale solo finché il foglio è
  // montato e viene ripristinato appena si torna all'app.
  useEffect(() => {
    const meta = document.querySelector<HTMLMetaElement>('meta[name="viewport"]')
    const previous = meta?.content
    if (meta) meta.content = 'width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no'
    return () => {
      if (meta && previous != null) meta.content = previous
    }
  }, [])

  useEffect(() => {
    const container = host.current
    if (!container) return
    let univer: Univer | null = null
    let unsubscribe: { dispose?: () => void } | null = null
    let selectionListener: { dispose: () => void } | null = null
    let editStartedListener: { dispose: () => void } | null = null
    let editListener: { dispose: () => void } | null = null
    let disposeSheetGestures: (() => void) | null = null
    let cancelled = false
    let readyTimeout: number | undefined
    let apiRef: FUniver | null = null
        /** Stato stabile precedente all'edit: Univer può emettere più comandi
         *  durante lo stesso gesto, ma l'undo deve tornare a prima del gesto e
         *  non a uno stato intermedio già marcato come non verificato. */
        let editBase: TableGrid | null = null
    const language = univerLanguage(locale)

    /** Deve essere registrato prima di `createUniver`: il motore installa i
     *  propri shortcut su `window` e, se arrivasse prima lui, eseguirebbe
     *  l'undo nativo sul documento invece della cronologia della tabella. */
    const onUndoRedo = (event: KeyboardEvent) => {
      if (!apiRef || (!event.ctrlKey && !event.metaKey) || event.altKey) return
      const key = event.key.toLowerCase()
      if (key !== 'z' && key !== 'y') return
      const target = event.target
      if (target instanceof HTMLElement && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return
      event.preventDefault()
      event.stopImmediatePropagation()
      if (key === 'y' || (key === 'z' && event.shiftKey)) redoRef.current?.()
      else undoRef.current?.()
    }
    window.addEventListener('keydown', onUndoRedo, true)

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
              // I comandi di struttura nativi sono ridondanti coi nostri (v. la
              // costante): si nascondono.
              menu: HIDDEN_NATIVE_MENUS,
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
        apiRef = api
        api.createWorkbook(gridToUniver(gridRef.current))
        const injector = univer.__getInjector()
        const undoRedo = injector.get(IUndoRedoService)

        /** Ctrl+rotella e il pinch del touchpad arrivano al browser come wheel
         *  con ctrlKey. Senza fermarli qui, Chrome zooma l'intera pagina e la
         *  modale sembra cambiare dimensione invece del solo foglio. I listener
         *  stanno su window in cattura: alcuni browser consegnano il gesto al
         *  documento invece che al canvas sotto il puntatore. */
        let gestureBaseZoom = 1
        let touchBaseZoom = 1
        let touchStartDistance = 0
        const pointIsOverSheet = (x: number, y: number) => {
          const rect = container.getBoundingClientRect()
          return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom
        }
        const eventIsOverSheet = (event: Event) => {
          const target = event.target
          if (target instanceof Node && container.contains(target)) return true
          const point = event as Event & { clientX?: number; clientY?: number }
          return typeof point.clientX === 'number' && typeof point.clientY === 'number'
            ? pointIsOverSheet(point.clientX, point.clientY)
            : false
        }
        const zoomSheet = (factor: number) => {
          const sheet = api.getActiveSheet()
          if (!sheet) return
          sheet.worksheet.zoom(Math.min(4, Math.max(0.1, sheet.worksheet.getZoom() * factor)))
        }
        const onSheetWheel = (event: WheelEvent) => {
          if ((!event.ctrlKey && !event.metaKey) || !eventIsOverSheet(event)) return
          event.preventDefault()
          event.stopPropagation()
          const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? Math.max(1, container.clientHeight) : 1
          zoomSheet(wheelZoomFactor(event.deltaY * unit))
        }
        const onGestureStart = (event: Event) => {
          if (typeof (event as Event & { scale?: number }).scale !== 'number' || !eventIsOverSheet(event)) return
          event.preventDefault()
          event.stopPropagation()
          gestureBaseZoom = api.getActiveSheet()?.worksheet.getZoom() ?? 1
        }
        const onGestureChange = (event: Event) => {
          const scale = (event as Event & { scale?: number }).scale
          if (typeof scale !== 'number' || !eventIsOverSheet(event)) return
          event.preventDefault()
          event.stopPropagation()
          const sheet = api.getActiveSheet()
          if (sheet) sheet.worksheet.zoom(Math.min(4, Math.max(0.1, gestureBaseZoom * scale)))
        }
        const onSheetTouchStart = (event: TouchEvent) => {
          if (event.touches.length !== 2 || !pointIsOverSheet(event.touches[0].clientX, event.touches[0].clientY)) return
          event.preventDefault()
          event.stopPropagation()
          touchBaseZoom = api.getActiveSheet()?.worksheet.getZoom() ?? 1
          touchStartDistance = Math.max(1, Math.hypot(
            event.touches[1].clientX - event.touches[0].clientX,
            event.touches[1].clientY - event.touches[0].clientY,
          ))
        }
        const onSheetTouchMove = (event: TouchEvent) => {
          if (event.touches.length !== 2 || touchStartDistance <= 0) return
          event.preventDefault()
          event.stopPropagation()
          const distance = Math.max(1, Math.hypot(
            event.touches[1].clientX - event.touches[0].clientX,
            event.touches[1].clientY - event.touches[0].clientY,
          ))
          const sheet = api.getActiveSheet()
          if (sheet) sheet.worksheet.zoom(Math.min(4, Math.max(0.1, touchBaseZoom * distance / touchStartDistance)))
        }
        const onSheetTouchEnd = (event: TouchEvent) => {
          if (event.touches.length < 2) touchStartDistance = 0
        }
        window.addEventListener('wheel', onSheetWheel, { capture: true, passive: false })
        window.addEventListener('gesturestart', onGestureStart, { capture: true, passive: false })
        window.addEventListener('gesturechange', onGestureChange, { capture: true, passive: false })
        window.addEventListener('touchstart', onSheetTouchStart, { capture: true, passive: false })
        window.addEventListener('touchmove', onSheetTouchMove, { capture: true, passive: false })
        window.addEventListener('touchend', onSheetTouchEnd, { capture: true, passive: false })
        window.addEventListener('touchcancel', onSheetTouchEnd, { capture: true, passive: false })

        /** Escape prima annulla la modifica della cella o chiude un menu/popup
         *  di Univer; solo un Escape fuori da questi livelli deve poter chiudere
         *  la modale che contiene il foglio. */
        let escapeWasEditing = false
        let escapeWasInSheet = false
        let escapeWasContextMenu = false
        const onEscapeCapture = (event: KeyboardEvent) => {
          if (event.key !== 'Escape') return
          escapeWasEditing = Boolean(api.getActiveWorkbook()?.isCellEditing())
          escapeWasInSheet = container.contains(document.activeElement)
            || (event.target instanceof Node && container.contains(event.target))
          escapeWasContextMenu = contextMenus.visible
        }
        const onEscape = (event: KeyboardEvent) => {
          if (event.key !== 'Escape' || (!escapeWasEditing && !escapeWasInSheet && !escapeWasContextMenu)) return
          if (escapeWasContextMenu) contextMenus.hideContextMenu()
          escapeWasEditing = false
          escapeWasInSheet = false
          escapeWasContextMenu = false
          event.stopPropagation()
        }
        container.addEventListener('keydown', onEscapeCapture, true)
        container.addEventListener('keydown', onEscape)
        disposeSheetGestures = () => {
          window.removeEventListener('wheel', onSheetWheel, true)
          window.removeEventListener('gesturestart', onGestureStart, true)
          window.removeEventListener('gesturechange', onGestureChange, true)
          window.removeEventListener('touchstart', onSheetTouchStart, true)
          window.removeEventListener('touchmove', onSheetTouchMove, true)
          window.removeEventListener('touchend', onSheetTouchEnd, true)
          window.removeEventListener('touchcancel', onSheetTouchEnd, true)
          container.removeEventListener('keydown', onEscapeCapture, true)
          container.removeEventListener('keydown', onEscape)
        }

        // Pronto quando la tela del foglio (non quella della barra della
        // formula, che arriva prima) ha preso le sue dimensioni, più un frame
        // per disegnarla. Un tetto a 2 s: in una scheda in background i
        // frame sono sospesi, e il foglio non deve restare invisibile.
        let revealed = false
        const markReady = () => {
          if (cancelled || revealed) return
          revealed = true
          setReady(true)
          setSkeletonGone(true)
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
        // Solo le mutazioni del dato ricostruiscono il modello; selezione e
        // scorrimento restano a costo della vista, senza snapshot completo.
        unsubscribe = api.onCommandExecuted((command) => {
          if (applyingGridRef.current) return
          if (!commandAffectsGrid(command.id)) return
          const data = api.getActiveWorkbook()?.save()
          if (!data) return
          const next = univerToGrid(data, gridRef.current)
          const signature = univerGridSignature(next)
          if (signature === known) return
          known = signature
          gridRef.current = next
          changeRef.current(next, editBase ?? undefined)
          // La cronologia visibile è quella dello workspace. Lasciare in vita
          // anche la pila nativa significherebbe avere due “Annulla” diversi:
          // uno può ricostruire il foglio e l'altro no.
          undoRedo.clearUndoRedo(api.getActiveWorkbook()?.getId() ?? '')
          // Una cella riscritta perde la verifica: la tinta la segue.
          paintVerified(verifiedCells(next))
          refreshActive()
        })

        // Il telaio di Univer non ha l'unisci nel menù contestuale (sta nella
        // barra strumenti, che qui non c'è): si aggiunge la voce riusando i
        // suoi comandi, mentre la cronologia visibile resta dello workspace.
        const commands = injector.get(ICommandService)
        const contextMenus = injector.get(IContextMenuService)

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

        /** Sostituisce testo, merge e dimensioni mantenendo montata la vista.
         *  Tutti i comandi passano dalla stessa pila undo di Univer come un
         *  solo passo; la cronologia funzionale resta però quella dello
         *  workspace, che conserva anche `source`/`verified` per cella. */
        const applyGrid = (next: TableGrid): boolean => {
          const workbook = api.getActiveWorkbook()
          const sheet = workbook?.getActiveSheet()
          if (!workbook || !sheet) return false

          const snapshot = gridToUniver(next)
          const source = snapshot.sheets?.[UNIVER_SHEET_ID]
          const rows = Math.max(1, source?.rowCount ?? next.rows)
          const cols = Math.max(1, source?.columnCount ?? next.cols)
          const header = Math.min(Math.max(0, next.header_rows ?? 0), rows)
          const cellData = source?.cellData ?? {}
          const matrix: ICellData[][] = Array.from({ length: rows }, (_, r) =>
            Array.from({ length: cols }, (_, c) => ({ ...(cellData[r]?.[c] ?? {}) })),
          )
          const before = sheet.getSelection()?.getActiveRange()?.getRange() ?? null
          const zoom = sheet.getZoom()
          const oldRows = sheet.getMaxRows()
          const oldCols = sheet.getMaxColumns()

          applyingGridRef.current = true
          const batch = undoRedo.__tempBatchingUndoRedo(workbook.getId())
          try {
            // Prima si crea spazio sufficiente, poi si azzera l'intera vecchia
            // area: ridurre le dimensioni prima del clear lascerebbe fuori le
            // celle che stanno per scomparire.
            sheet.setRowCount(Math.max(oldRows, rows))
            sheet.setColumnCount(Math.max(oldCols, cols))
            for (const range of sheet.getMergeData()) range.breakApart()
            sheet.getRange(0, 0, Math.max(oldRows, rows), Math.max(oldCols, cols)).clear()
            sheet.setRowCount(rows)
            sheet.setColumnCount(cols)
            sheet.getRange(0, 0, rows, cols).setValues(matrix)
            for (const range of source?.mergeData ?? []) sheet.getRange(range).merge({ isForceMerge: true })
            sheet.setFrozenRows(header)
            sheet.zoom(zoom)
            if (before) {
              const startRow = Math.min(before.startRow, rows - 1)
              const endRow = Math.min(before.endRow, rows - 1)
              const startColumn = Math.min(before.startColumn, cols - 1)
              const endColumn = Math.min(before.endColumn, cols - 1)
              sheet.getRange(startRow, startColumn, endRow - startRow + 1, endColumn - startColumn + 1).activate()
            }
          } catch (error) {
            // eslint-disable-next-line no-console
            console.error('Impossibile aggiornare il foglio senza rimontarlo', error)
            return false
          } finally {
            batch.dispose()
            applyingGridRef.current = false
          }
          undoRedo.clearUndoRedo(workbook.getId())

          gridRef.current = next
          known = univerGridSignature(next)
          paintVerified(verifiedCells(next))
          refreshActive()
          return true
        }
        controllerChangeRef.current?.({ applyGrid })

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
        refreshActive()

        selectionListener = api.addEvent(api.Event.SelectionChanged, ({ selections }) => {
          const range = selections[0]
          if (range) {
            selectRef.current?.(range)
            refreshActive(range)
          }
        })

        editStartedListener = api.addEvent(api.Event.SheetEditStarted, () => {
          editBase = gridRef.current
        })

        // «8 1/8» → «8⅛» alla conferma della cella (v. `lib/fractions.ts`).
        // La conversione è un comando a sé, dopo quello della cella: Ctrl+Z la
        // annulla da sola e restituisce la frazione come l'hai digitata.
        editListener = api.addEvent(api.Event.SheetEditEnded, ({ worksheet, row, column, isConfirm }) => {
          editBase = null
          if (!isConfirm) return
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
        for (const entry of TRACK_COMMANDS) {
          commands.registerCommand({
            id: entry.id,
            type: CommandType.COMMAND,
            handler: () => {
              trackOpRef.current(entry.op, selection())
              return true
            },
          })
        }
        for (const entry of HEADER_COMMANDS) {
          commands.registerCommand({
            id: entry.id,
            type: CommandType.COMMAND,
            handler: () => {
              if (entry.op === 'saveHeader') saveHeaderRef.current?.(selection())
              else applyHeaderRef.current?.(selection())
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
        const group = [...HEADER_COMMANDS, ...TRACK_COMMANDS, ...COLUMN_COMMANDS].reduce(
          (acc, item) => ({ ...acc, ...entry(item.id, item.title, item.id, item.order) }),
          {},
        )
        injector.get(IMenuManagerService).appendRootMenu({
          [MenuManagerPosition.CONTEXT_MENU]: {
            // Sul corpo del foglio, sull'intestazione di colonna e su quella di
            // riga: il gesto è «tasto destro su riga/colonna», e le tre strade
            // devono portare allo stesso posto.
            [ContextMenuPosition.MAIN_AREA]: { [ContextMenuGroup.OTHERS]: group },
            [ContextMenuPosition.COL_HEADER]: { [ContextMenuGroup.OTHERS]: group },
            [ContextMenuPosition.ROW_HEADER]: { [ContextMenuGroup.OTHERS]: group },
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
      apiRef = null
      window.clearTimeout(readyTimeout)
      window.removeEventListener('keydown', onUndoRedo, true)
      paintRef.current = null
      selectionListener?.dispose()
      editStartedListener?.dispose()
      editListener?.dispose()
      controllerChangeRef.current?.(null)
      disposeSheetGestures?.()
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
