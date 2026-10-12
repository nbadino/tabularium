/**
 * Le primitive del mosaico.
 *
 * `Module` è il contenitore universale di questo sistema: un rettangolo
 * rigato con una linguetta che lo nomina. Tutto quello che la pagina mostra
 * — un form da 16 campi, una tabella di metriche, la rassegna dell'archivio,
 * un pannello dello studio — è lo stesso oggetto a densità diverse.
 *
 * Regola non negoziabile: ogni zona porta il proprio nome scritto. Nessun
 * modulo senza linguetta, nessun pulsante affidato alla sola icona.
 */
import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { CSSProperties, MouseEvent as ReactMouseEvent, ReactNode, RefObject } from 'react'
import { IconAlert, IconChevron, IconClose, IconWarn } from './icons'
import { describeError } from '../lib/errors'
import { prefersReducedMotion } from '../lib/motion'
import type { StatusTone } from '../lib/vocab'
import { useI18n } from '../i18n'

/* ---------------------------------------------------------------- Module -- */

interface ModuleProps {
  /** Il nome stampato sulla linguetta. Obbligatorio: le zone si nominano. */
  tab: string
  /** Linguetta nera invece che rossa: il rosso segnala «qui è vivo». */
  quiet?: boolean
  /** Contenuto allineato a destra nella testata (conteggi, stato, azioni). */
  aux?: ReactNode
  /** Toglie il padding del corpo: per tabelle e liste che rigano da sole. */
  flush?: boolean
  className?: string
  children: ReactNode
}

export function Module({
  tab,
  quiet,
  aux,
  flush,
  className = '',
  children,
}: ModuleProps) {
  return (
    <section className={`mod ${className}`} aria-label={tab}>
      <header className="mod-head">
        <span className={`mod-tab ${quiet ? 'mod-tab-quiet' : ''}`}>{tab}</span>
        {aux && <div className="mod-head-aux">{aux}</div>}
      </header>
      <div className={flush ? 'mod-body-flush' : 'mod-body'}>{children}</div>
    </section>
  )
}

/* ------------------------------------------------------------ PageHeader -- */

/**
 * Titolo della pagina e, sotto, il suo scopo su una riga sola: lo spazio
 * verticale serve al lavoro. Se manca larghezza l'intro si tronca e resta
 * intera nel tooltip.
 */
export function PageHeader({
  title,
  intro,
  aux,
  className = 'mb-3 pb-2',
}: {
  title: ReactNode
  intro: string
  /** Contenuto accanto al titolo (versione, conteggi). */
  aux?: ReactNode
  className?: string
}) {
  return (
    <div className={`border-b border-[color:var(--color-rule-strong)] ${className}`}>
      <div className="flex items-baseline gap-3">
        <h1 className="text-[30px] font-bold leading-tight tracking-[-0.03em]">{title}</h1>
        {aux}
      </div>
      <p className="truncate text-[12px] text-[color:var(--color-ink-2)]" title={intro}>{intro}</p>
    </div>
  )
}

/* ------------------------------------------------------- modulo a soffietto */

// Durata di `.collapse-out` in index.css.
const COLLAPSE_MS = 140

export function Collapsible({
  tab,
  quiet,
  aux,
  defaultOpen = false,
  children,
}: ModuleProps & { defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  // Chiudendo, il corpo resta montato il tempo di `.collapse-out` (index.css);
  // riaprire nel frattempo annulla la chiusura.
  const [closing, setClosing] = useState(false)
  const closeTimer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(closeTimer.current), [])
  const toggle = () => {
    window.clearTimeout(closeTimer.current)
    if (open && !closing) {
      setClosing(true)
      closeTimer.current = window.setTimeout(() => {
        setOpen(false)
        setClosing(false)
      }, COLLAPSE_MS)
    } else {
      setOpen(true)
      setClosing(false)
    }
  }
  const expanded = open && !closing
  const id = useId()
  return (
    <section className="mod" aria-label={tab}>
      <h2 className="mod-head">
        <button
          type="button"
          onClick={toggle}
          aria-expanded={expanded}
          aria-controls={id}
          className="flex flex-1 items-stretch gap-2 text-left"
        >
          <span className={`mod-tab ${quiet ? 'mod-tab-quiet' : ''}`}>{tab}</span>
          <span className="mod-head-aux !ml-0 !px-0">
            <IconChevron
              size={12}
              className="transition-transform duration-150"
              style={{ transform: expanded ? 'rotate(0deg)' : 'rotate(-90deg)' }}
            />
          </span>
          {aux && <span className="mod-head-aux">{aux}</span>}
        </button>
      </h2>
      {open && (
        <div id={id} className={`mod-body ${closing ? 'collapse-out' : 'swap'}`}>
          {children}
        </div>
      )}
    </section>
  )
}

/* ----------------------------------------------------------------- Notice -- */

/**
 * Un errore mostrato come lo mostra questo prodotto: cosa è successo, e cosa
 * fare adesso. Il testo dell'eccezione vive dietro «dettaglio tecnico», dove
 * serve a chi debugga e non spaventa chi sta annotando.
 */
export function ErrorNotice({
  error,
  onDismiss,
}: {
  error: unknown
  onDismiss?: () => void
}) {
  const { t } = useI18n()
  const e = describeError(error)
  return (
    <div
      role="alert"
      className="mod border-[color:var(--color-sig-plate)]"
      aria-label={t('errors.prefix', { title: e.titolo })}
    >
      <div className="mod-head border-b-[color:var(--color-sig-plate)] !bg-[color:var(--color-sig-wash)]">
        <span className="mod-tab">
          <IconAlert size={12} />
          {e.titolo}
        </span>
        {onDismiss && (
          <div className="mod-head-aux">
            <button
              type="button"
              onClick={onDismiss}
              className="btn btn-sm"
              aria-label={t('errors.close')}
            >
              <IconClose size={11} />
              {t('common.close')}
            </button>
          </div>
        )}
      </div>
      <div className="mod-body">
        <p className="text-[13px] text-[color:var(--color-ink)]">{e.messaggio}</p>
        <p className="mt-1.5 text-[12px] text-[color:var(--color-ink-2)]">
          <b className="font-semibold text-[color:var(--color-ink)]">
            {t('errors.whatToDo')}
          </b>
          {e.suggerimento}
        </p>
        <details className="mt-2">
          <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-[0.04em] text-[color:var(--color-ink-3)]">
            {t('errors.technicalDetail')}
          </summary>
          <pre className="mono mt-1.5 max-h-32 overflow-auto rounded-md border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2 text-[11px] leading-relaxed whitespace-pre-wrap">
            {e.dettaglio}
          </pre>
        </details>
      </div>
    </div>
  )
}

/** Avviso non bloccante: il lavoro prosegue, ma c'è qualcosa da sapere. */
export function WarnNotice({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <div className="mod border-[color:var(--color-warn-rule)]">
      <div className="mod-head border-b-[color:var(--color-warn-rule)] !bg-[color:var(--color-warn-wash)]">
        <span className="mod-tab !bg-[color:var(--color-warn)]">
          <IconWarn size={12} />
          {title}
        </span>
      </div>
      <div className="mod-body text-[12px] text-[color:var(--color-ink-2)]">{children}</div>
    </div>
  )
}

/* ------------------------------------------------------------------- Menu -- */

/**
 * Le azioni rare dietro un pulsante con nome («Altro ▾»): restano a un clic,
 * scritte per esteso, ma non occupano la barra. Si apre col clic, mai al
 * passaggio del mouse; si chiude con un clic fuori, Esc o scegliendo.
 */
// Durata del ripiegamento: deve coincidere con `.menu-unfold[data-closing]`
// in index.css. Un timer e non `animationend`: l'evento può non arrivare mai
// (scheda in background, animazione saltata) e il menu resterebbe aperto.
const FOLD_MS = 450
let foldSeq = 0

/** Chiude il menu: prima lo ripiega, poi toglie `open`. */
function closeMenu(d: HTMLDetailsElement | null) {
  if (!d?.open || d.dataset.closing !== undefined) return
  if (prefersReducedMotion()) {
    d.open = false
    return
  }
  // Il gettone distingue questa chiusura da una successiva: se il menu viene
  // riaperto e richiuso prima dello scadere, il timer vecchio non deve chiudere.
  const token = String(++foldSeq)
  d.dataset.closing = token
  window.setTimeout(() => {
    if (d.dataset.closing !== token) return
    delete d.dataset.closing
    d.open = false
  }, FOLD_MS)
}

/** Clic fuori ed Escape chiudono il menu. */
function useDismiss(ref: RefObject<HTMLDetailsElement | null>) {
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current?.open && !ref.current.contains(e.target as Node)) closeMenu(ref.current)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeMenu(ref.current)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [ref])
}

/** Il toggle nativo del `<summary>` chiuderebbe di colpo: lo sostituiamo. */
function onSummaryClick(e: ReactMouseEvent, d: HTMLDetailsElement | null) {
  if (!d?.open) return
  e.preventDefault()
  if (d.dataset.closing !== undefined) delete d.dataset.closing // riaperto mentre si ripiegava
  else closeMenu(d)
}

export function Menu({
  label,
  children,
  align = 'right',
  className = '',
}: {
  label: ReactNode
  children: ReactNode
  align?: 'left' | 'right'
  className?: string
}) {
  const ref = useRef<HTMLDetailsElement>(null)
  useDismiss(ref)
  return (
    <details ref={ref} className={`menu-unfold relative ${className}`}>
      <summary
        className="btn btn-sm cursor-pointer list-none [&::-webkit-details-marker]:hidden"
        onClick={(e) => onSummaryClick(e, ref.current)}
      >
        {label}
        <IconChevron size={11} />
      </summary>
      <div
        className={`float-layer absolute top-full z-30 mt-1 flex w-max min-w-44 max-w-[calc(100vw-1.5rem)] flex-col gap-px p-1 text-[12px] ${align === 'right' ? 'right-0' : 'left-0'}`}
        onClick={(e) => {
          // Un pulsante scelto chiude il menu; i campi (select, radio) no.
          if ((e.target as HTMLElement).closest('button, a')) closeMenu(ref.current)
        }}
      >
        {children}
      </div>
    </details>
  )
}

/* ----------------------------------------------------------------- Select -- */

/**
 * Tendina che si srotola come il menu dell'account. Sostituisce `<select>`
 * dove serve l'animazione: la lista di un `<select>` nativo la disegna il
 * browser e il CSS non la raggiunge. Tastiera come un select: frecce,
 * Home/Fine, Invio, Escape; Tab chiude e prosegue.
 */
export function Select({
  value,
  options,
  onChange,
  disabled = false,
  className = '',
  ariaLabel,
  onFocus,
}: {
  value: string
  options: { value: string; label: string }[]
  onChange: (value: string) => void
  disabled?: boolean
  /** Classi del pulsante (es. `fld`, `fld !w-auto text-xs`). */
  className?: string
  ariaLabel?: string
  onFocus?: () => void
}) {
  const ref = useRef<HTMLDetailsElement>(null)
  const listId = useId()
  // Per `aria-expanded`: il ruolo combobox lo richiede, e il <details> non lo
  // espone una volta che il <summary> ha un ruolo suo.
  const [expanded, setExpanded] = useState(false)
  useDismiss(ref)
  const current = options.find((o) => o.value === value)
  const optionButtons = () => [...(ref.current?.querySelectorAll<HTMLButtonElement>('[role=option]') ?? [])]
  const summary = () => ref.current?.querySelector('summary')

  const pick = (v: string) => {
    closeMenu(ref.current)
    summary()?.focus()
    if (v !== value) onChange(v)
  }

  return (
    <details
      ref={ref}
      className={`menu-unfold relative ${disabled ? 'pointer-events-none opacity-50' : ''}`}
      onToggle={(e) => {
        setExpanded(e.currentTarget.open)
        // All'apertura il fuoco va sulla voce scelta, come in un select.
        if (e.currentTarget.open) {
          const btns = optionButtons()
          ;(btns.find((b) => b.getAttribute('aria-selected') === 'true') ?? btns[0])?.focus()
        }
      }}
    >
      <summary
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={expanded}
        aria-controls={listId}
        aria-label={ariaLabel}
        aria-disabled={disabled || undefined}
        tabIndex={disabled ? -1 : 0}
        onFocus={onFocus}
        className={`${className} flex cursor-pointer list-none items-center justify-between gap-2 [&::-webkit-details-marker]:hidden`}
        onClick={(e) => {
          if (disabled) e.preventDefault()
          else onSummaryClick(e, ref.current)
        }}
        onKeyDown={(e) => {
          if (disabled || ref.current?.open) return
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault()
            ref.current!.open = true
          }
        }}
      >
        <span className="truncate">{current?.label ?? ''}</span>
        <IconChevron size={11} />
      </summary>
      <div
        id={listId}
        role="listbox"
        aria-label={ariaLabel}
        className="float-layer absolute top-full left-0 z-30 mt-1 flex max-h-80 w-max min-w-full flex-col overflow-y-auto p-1 text-[12px]"
        onKeyDown={(e) => {
          const btns = optionButtons()
          const i = btns.indexOf(document.activeElement as HTMLButtonElement)
          const go = (n: number) => {
            e.preventDefault()
            btns[Math.max(0, Math.min(btns.length - 1, n))]?.focus()
          }
          // Una lettera porta alla voce successiva che comincia così, come in un
          // select nativo; e non arriva alle scorciatoie dello Studio (R, V, 1–9).
          if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
            e.stopPropagation()
            const ch = e.key.toLowerCase()
            const order = [...btns.slice(i + 1), ...btns.slice(0, i + 1)]
            const hit = order.find((b) => (b.textContent ?? '').trim().toLowerCase().startsWith(ch))
            if (hit) {
              e.preventDefault()
              hit.focus()
            }
            return
          }
          if (e.key === 'ArrowDown') go(i + 1)
          else if (e.key === 'ArrowUp') go(i - 1)
          else if (e.key === 'Home') go(0)
          else if (e.key === 'End') go(btns.length - 1)
          else if (e.key === 'Escape') summary()?.focus()
          else if (e.key === 'Tab') closeMenu(ref.current)
        }}
      >
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            role="option"
            aria-selected={o.value === value}
            data-value={o.value}
            onClick={() => pick(o.value)}
            className={`btn btn-sm justify-start whitespace-nowrap !border-transparent ${
              o.value === value ? 'bg-[color:var(--color-sig-wash)] font-semibold' : ''
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
    </details>
  )
}

/* ------------------------------------------------------------------ Badge -- */

const TONE_STYLE: Record<StatusTone, string> = {
  neutral: 'text-[color:var(--color-ink-3)]',
  progress: 'text-[color:var(--color-ink)]',
  ok: 'text-[color:var(--color-ok)] bg-[color:var(--color-ok-wash)]',
  warn: 'text-[color:var(--color-warn)] bg-[color:var(--color-warn-wash)]',
  sig: 'text-[color:var(--color-sig-text)] bg-[color:var(--color-sig-wash)]',
}

/**
 * Uno stato si legge come parola, non come pallino: il colore rinforza, non
 * informa. Chi non lo distingue riceve comunque l'informazione completa.
 */
export function Badge({
  tone = 'neutral',
  children,
}: {
  tone?: StatusTone
  children: ReactNode
}) {
  return <span className={`badge ${TONE_STYLE[tone]}`}>{children}</span>
}

/* ----------------------------------------------------------------- Notice -- */

const NOTICE_TONE: Record<StatusTone, string> = {
  neutral: 'text-[color:var(--color-ink)]',
  progress: 'text-[color:var(--color-ink)]',
  ok: 'text-[color:var(--color-ok)]',
  warn: 'text-[color:var(--color-warn)]',
  sig: 'text-[color:var(--color-sig-text)]',
}

/**
 * L'esito di un'azione, detto in una riga dentro una piastra.
 *
 * Serve a non avere cinque canali di ritorno diversi nella stessa pagina —
 * uno per il salvataggio, uno per il backup, uno per i profili — ciascuno
 * reso a modo suo. Per gli errori veri resta `ErrorNotice`, che spiega anche
 * cosa fare adesso: questo è per il «fatto», non per il «rotto».
 */
export function Notice({
  tone = 'neutral',
  children,
}: {
  tone?: StatusTone
  children: ReactNode
}) {
  return (
    <p
      role="status"
      className={`mt-3 rounded-md border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-2.5 py-1.5 text-[12px] ${NOTICE_TONE[tone]}`}
    >
      {children}
    </p>
  )
}

/* --------------------------------------------------------------- Progress -- */

/**
 * Avanzamento: un filetto che si riempie, senza angoli né ombre.
 *
 * Non porta mai il dato da solo — accanto c'è sempre la misura scritta
 * (`123 MB di ~1,8 GB`), come per i `Badge`: il segno rinforza, non informa.
 * Senza un totale attendibile la barra diventa indeterminata: dice «sta
 * procedendo», che è l'unica cosa vera che si può dire in quel caso.
 */
export function Progress({
  value,
  label,
  indeterminate,
}: {
  /** 0–100. Ignorato quando `indeterminate`. */
  value?: number
  label: string
  indeterminate?: boolean
}) {
  const pct = Math.max(0, Math.min(100, value ?? 0))
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      {...(indeterminate ? {} : { 'aria-valuenow': Math.round(pct) })}
      className="h-1.5 w-full overflow-hidden rounded-full border border-[color:var(--color-rule)] bg-[color:var(--color-fill)]"
    >
      <div
        className={`h-full bg-[color:var(--color-sig)] ${indeterminate ? 'prog-idle' : ''}`}
        style={indeterminate ? undefined : { width: `${pct}%` }}
      />
    </div>
  )
}

/* ---------------------------------------------------------------- Loading -- */

/**
 * «Sto caricando», detto da fermo e da vivo insieme: la frase resta leggibile
 * (e leggibile da uno screen reader) e sotto corre il filo della barra
 * indeterminata. Non promette una percentuale che non conosciamo.
 */
export function Loading({ label }: { label: string }) {
  return (
    <span className="inline-flex flex-col gap-1">
      <span className="text-[12px] text-[color:var(--color-ink-2)]">{label}</span>
      <span role="status" aria-label={label} className="load-rail">
        <i />
      </span>
    </span>
  )
}

/**
 * Lo scheletro di una griglia che sta arrivando: la forma di ciò che apparirà,
 * invece di una frase sola e poi un salto di layout. `label` resta per chi
 * legge con uno screen reader, che dallo scheletro non ricava nulla.
 */
export function LoadingGrid({ label, rows = 6 }: { label: string; rows?: number }) {
  const cols = 4
  return (
    <div
      role="status"
      aria-label={label}
      className="grid grid-cols-[2fr_1fr_1fr_1fr] gap-px border border-[color:var(--color-rule)] bg-[color:var(--color-rule)]"
    >
      {Array.from({ length: rows * cols }, (_, i) => (
        <span
          key={i}
          className="load-cell h-[18px]"
          style={{ '--load-delay': `${(i % cols) * 80}ms` } as CSSProperties}
        />
      ))}
    </div>
  )
}

/* -------------------------------------------------------------- Skeleton -- */

/**
 * La forma di ciò che sta arrivando, al posto di «Caricamento…»: righe grigie
 * che il contenuto vero sostituisce senza far saltare la pagina. La frase
 * resta per gli screen reader.
 */
export function SkeletonLines({ lines = 3, label }: { lines?: number; label: string }) {
  return (
    <div role="status" aria-label={label} className="flex flex-col gap-2 py-1">
      <span className="sr-only">{label}</span>
      {Array.from({ length: lines }, (_, i) => (
        <span
          key={i}
          aria-hidden
          className="load-cell block h-3 rounded-sm"
          style={{ width: `${[92, 78, 64, 85, 70][i % 5]}%`, ['--load-delay' as string]: `${i * 90}ms` }}
        />
      ))}
    </div>
  )
}

/** Una sezione che sta caricando: titolo e due moduli, nella loro forma. */
export function PageSkeleton({ label }: { label: string }) {
  return (
    <div role="status" aria-label={label} className="flex flex-col gap-3 p-3">
      <span className="sr-only">{label}</span>
      <span aria-hidden className="load-cell block h-7 w-56 rounded-md" />
      <span aria-hidden className="load-cell block h-3 w-[min(36rem,80%)] rounded-sm" />
      <div aria-hidden className="mt-2 grid gap-3 md:grid-cols-2">
        <span className="load-cell block h-40 rounded-[var(--radius-lg)]" />
        <span className="load-cell block h-40 rounded-[var(--radius-lg)]" style={{ ['--load-delay' as string]: '150ms' }} />
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ Modal -- */

/**
 * Finestra modale operabile da tastiera: Escape chiude, il focus entra
 * dentro, Tab resta intrappolato, e alla chiusura il focus torna esattamente
 * dove era. Nessuna modale del prodotto si scrive senza passare da qui.
 */
export function Modal({
  title,
  onClose,
  wide,
  extraWide,
  footer,
  children,
}: {
  title: string
  onClose: () => void
  wide?: boolean
  /** Finestra quasi a tutto schermo per strumenti di lavoro densi. */
  extraWide?: boolean
  footer?: ReactNode
  children: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const returnTo = useRef<HTMLElement | null>(null)
  const { t } = useI18n()

  useEffect(() => {
    returnTo.current = document.activeElement as HTMLElement | null
    const root = ref.current
    if (!root) return

    const focusables = () =>
      Array.from(
        root.querySelectorAll<HTMLElement>(
          'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((el) => el.offsetParent !== null || el === document.activeElement)

    // Il primo campo utile riceve il focus all'apertura.
    focusables()[0]?.focus()

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
        return
      }
      if (e.key !== 'Tab') return
      const items = focusables()
      if (items.length === 0) return
      const first = items[0]
      const last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }

    root.addEventListener('keydown', onKey)
    return () => {
      root.removeEventListener('keydown', onKey)
      returnTo.current?.focus?.()
    }
  }, [onClose])

  // Il portale su `body` non e' un dettaglio: la modale nasce dentro sezioni
  // che animano `clip-path` (`.swap`), e un antenato ritagliato ridefinisce il
  // blocco contenitore di `fixed` — la finestra finirebbe tagliata in basso.
  return createPortal(
    <div
      className="modal-backdrop fixed inset-0 z-50 flex items-center justify-center bg-[rgb(17_17_17/0.55)] p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`modal-panel float-layer flex w-full flex-col overflow-hidden ${
          extraWide
            ? 'h-[96vh] max-h-[96vh] max-w-[96vw]'
            : `max-h-[92vh] ${wide ? 'max-w-[1150px]' : 'max-w-md'}`
        }`}
      >
        <div className="mod-head">
          <h2 id={titleId} className="mod-tab">
            {title}
          </h2>
          <div className="mod-head-aux">
            <button
              type="button"
              onClick={onClose}
              className="btn btn-sm"
              aria-label={t('errors.closeModal')}
            >
              <IconClose size={11} />
              Esc
            </button>
          </div>
        </div>
        <div className={extraWide ? 'flex min-h-0 flex-1 overflow-hidden' : 'min-h-0 flex-1 overflow-auto'}>{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-2 border-t border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-3 py-2">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}

/* ------------------------------------------------------------------ campi -- */

export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string
  hint?: string
  error?: string | null
  children: ReactNode
}) {
  return (
    <label className="block min-w-0">
      <span className="lbl">{label}</span>
      {children}
      {error ? (
        <span className="mt-1 block text-[11px] text-[color:var(--color-sig-text)]">{error}</span>
      ) : (
        hint && (
          <span className="mt-1 block text-[11px] text-[color:var(--color-ink-3)]">{hint}</span>
        )
      )}
    </label>
  )
}
