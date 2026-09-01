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
import type { ReactNode } from 'react'
import { IconAlert, IconChevron, IconClose, IconWarn } from './icons'
import { describeError } from '../lib/errors'
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

/* ------------------------------------------------------- modulo a soffietto */

export function Collapsible({
  tab,
  quiet,
  aux,
  defaultOpen = false,
  children,
}: ModuleProps & { defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const id = useId()
  return (
    <section className="mod" aria-label={tab}>
      <h2 className="mod-head">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls={id}
          className="flex flex-1 items-stretch gap-2 text-left"
        >
          <span className={`mod-tab ${quiet ? 'mod-tab-quiet' : ''}`}>{tab}</span>
          <span className="mod-head-aux !ml-0 !px-0">
            <IconChevron
              size={12}
              style={{ transform: open ? 'rotate(0deg)' : 'rotate(-90deg)' }}
            />
          </span>
          {aux && <span className="mod-head-aux">{aux}</span>}
        </button>
      </h2>
      {open && (
        <div id={id} className="mod-body swap">
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
          <pre className="mono mt-1.5 max-h-32 overflow-auto border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2 text-[11px] leading-relaxed whitespace-pre-wrap">
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
      className={`mt-3 border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-2 py-1.5 text-[12px] ${NOTICE_TONE[tone]}`}
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
      className="h-1.5 w-full overflow-hidden border border-[color:var(--color-rule)] bg-[color:var(--color-fill)]"
    >
      <div
        className={`h-full bg-[color:var(--color-sig)] ${indeterminate ? 'prog-idle' : ''}`}
        style={indeterminate ? undefined : { width: `${pct}%` }}
      />
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
  footer,
  children,
}: {
  title: string
  onClose: () => void
  wide?: boolean
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

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[rgb(17_17_17/0.55)] p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`flex max-h-[92vh] w-full flex-col border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sheet)] ${
          wide ? 'max-w-[1150px]' : 'max-w-md'
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
        <div className="min-h-0 flex-1 overflow-auto">{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-2 border-t border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-3 py-2">
            {footer}
          </div>
        )}
      </div>
    </div>
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
