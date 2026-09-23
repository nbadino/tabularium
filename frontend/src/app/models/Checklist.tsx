/**
 * Lista di controllo dei passi di configurazione (locale, Vast.ai).
 *
 * Una voce = numero, titolo, stato scritto, corpo. Lo stato non è mai solo
 * colore: il badge lo dice a parole e il numero diventa ✓ a voce compiuta.
 * Le voci in attesa restano visibili ma attenuate, così si vede cosa viene
 * dopo senza poterlo toccare prima del tempo.
 */
import type { ReactNode } from 'react'
import { Badge } from '../ui'

export type ChecklistState = 'done' | 'active' | 'waiting' | 'failed'

const TONE: Record<ChecklistState, 'ok' | 'progress' | 'neutral' | 'warn'> = {
  done: 'ok',
  active: 'progress',
  waiting: 'neutral',
  failed: 'warn',
}

export function ChecklistItem({
  n,
  title,
  state,
  stateLabel,
  aux,
  children,
}: {
  n: number
  title: string
  state: ChecklistState
  stateLabel: string
  /** Azione secondaria in testata (es. «Cambia» su una voce compiuta). */
  aux?: ReactNode
  children?: ReactNode
}) {
  return (
    <li
      className={`grid grid-cols-[2rem_1fr] gap-x-3 border-b border-[color:var(--color-rule)] py-3 last:border-b-0 ${state === 'waiting' ? 'opacity-60' : ''}`}
      aria-current={state === 'active' ? 'step' : undefined}
    >
      <span
        className={`flex h-7 w-7 items-center justify-center text-[13px] font-bold ${
          state === 'done'
            ? 'bg-[color:var(--color-ok)] text-white'
            : state === 'active'
              ? 'bg-[color:var(--color-sig-plate)] text-white'
              : state === 'failed'
                ? 'bg-[color:var(--color-warn)] text-white'
                : 'border border-[color:var(--color-rule)] text-[color:var(--color-ink-3)]'
        }`}
        aria-hidden
      >
        {state === 'done' ? '✓' : n}
      </span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[14px] font-bold">{title}</span>
          <Badge tone={TONE[state]}>{stateLabel}</Badge>
          {aux && <span className="ml-auto">{aux}</span>}
        </div>
        {children && <div className="mt-1 text-[12px] text-[color:var(--color-ink-2)]">{children}</div>}
      </div>
    </li>
  )
}
