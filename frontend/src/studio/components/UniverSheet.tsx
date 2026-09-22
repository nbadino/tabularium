/**
 * Il foglio della vista di lavoro, su **Univer**.
 *
 * Univer è un SDK, non un widget: monta un runtime su un contenitore e si
 * guida per comandi. Qui si compone il preset «sheets core» — griglia, merge,
 * selezione, copia/incolla, fill handle, menù contestuale — **senza** la sua
 * intestazione né la sua barra degli strumenti: dentro la nostra vista di
 * lavoro il telaio è nostro, e due barre sopra la stessa griglia sarebbero due
 * prodotti in uno.
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
import { useEffect, useRef, useState } from 'react'
import { createUniver, LocaleType, mergeLocales } from '@univerjs/presets'
import {
  ContextMenuPosition,
  IMenuManagerService,
  MenuItemType,
  MenuManagerPosition,
  UniverSheetsCorePreset,
} from '@univerjs/preset-sheets-core'
import { CommandType, ICommandService } from '@univerjs/core'
import UniverItIT from '@univerjs/preset-sheets-core/locales/it-IT'
import UniverEnUS from '@univerjs/preset-sheets-core/locales/en-US'
import UniverFrFR from '@univerjs/preset-sheets-core/locales/fr-FR'
import type { FUniver } from '@univerjs/core/lib/facade'
import type { Univer } from '@univerjs/core'
import '@univerjs/preset-sheets-core/lib/index.css'

import type { TableGrid } from '../../lib/types'
import { gridToUniver, univerGridSignature, univerToGrid } from '../../lib/univerGrid'
import { useI18n } from '../../i18n'

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

interface UniverSheetProps {
  /** Il modello di partenza: si carica una volta, poi comanda Univer. */
  grid: TableGrid
  /** Chiamata a ogni cambiamento reale (celle, merge, dimensioni). */
  onGridChange: (grid: TableGrid) => void
  /** Invocata da una voce di operazione di colonna, con la selezione corrente. */
  onColumnOp: (op: ColumnOp, selection: SheetSelection) => void
}

export default function UniverSheet({ grid, onGridChange, onColumnOp }: UniverSheetProps) {
  const { t, locale } = useI18n()
  const host = useRef<HTMLDivElement | null>(null)
  const [failed, setFailed] = useState(false)
  const gridRef = useRef(grid)
  const changeRef = useRef(onGridChange)
  changeRef.current = onGridChange
  const opRef = useRef(onColumnOp)
  opRef.current = onColumnOp

  useEffect(() => {
    const container = host.current
    if (!container) return
    let univer: Univer | null = null
    let unsubscribe: { dispose?: () => void } | null = null
    let cancelled = false
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
              header: false,
              toolbar: false,
              footer: false,
            }),
          ],
        })
        univer = created.univer
        const api: FUniver = created.univerAPI
        api.createWorkbook(gridToUniver(gridRef.current))

        // Impronta di ciò che è già noto: riconosce i comandi che non hanno
        // cambiato niente (selezione, scroll, un click su una cella).
        let known = univerGridSignature(gridRef.current)
        unsubscribe = api.onCommandExecuted(() => {
          const data = api.getActiveWorkbook()?.save()
          if (!data) return
          const next = univerToGrid(data, gridRef.current)
          const signature = univerGridSignature(next)
          if (signature === known) return
          known = signature
          gridRef.current = next
          changeRef.current(next)
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
        injector.get(IMenuManagerService).mergeMenu({
          [MenuManagerPosition.CONTEXT_MENU]: {
            // Sia sul corpo del foglio sia sull'intestazione di colonna: il
            // gesto è «tasto destro su una colonna», e le due strade devono
            // portare allo stesso posto.
            [ContextMenuPosition.MAIN_AREA]: group,
            [ContextMenuPosition.COL_HEADER]: group,
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

  if (failed) {
    return (
      <p className="border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-3 text-[12px] text-[color:var(--color-ink-2)]">
        {t('table.univerFailed')}
      </p>
    )
  }
  return <div ref={host} data-testid="univer-host" className="h-full min-h-[320px] w-full" />
}
