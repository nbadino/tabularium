/**
 * La mappa del percorso.
 *
 * Sostituisce la card «prossimo passo» invece di affiancarla: il passo
 * successivo È la riga corrente della mappa, non una seconda voce che dice
 * la stessa cosa altrove.
 *
 * La fase corrente è l'unica regione dominante: numero su piastra piena,
 * fondo di segnale, il perché scritto e il pulsante. Le altre restano righe.
 */
import { Link } from 'react-router'
import { useI18n } from '../i18n'
import { Badge, Module } from './ui'
import { IconCheck, IconNext } from './icons'
import type { Stage } from './pipeline'
import { completedCount } from './pipeline'

function StageRow({ stage, n }: { stage: Stage; n: number }) {
  const { t } = useI18n()
  const current = stage.state === 'current'
  const done = stage.state === 'done'

  return (
    <li
      className={
        current
          ? 'bg-[color:var(--color-sig-wash)] px-2 py-2.5'
          : 'px-2 py-1.5'
      }
      aria-current={current ? 'step' : undefined}
    >
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        {/* Il numero è una piastra quando la fase è viva, una cifra quando no. */}
        <span
          className={`mono shrink-0 text-center text-[11px] ${
            current
              ? 'bg-[color:var(--color-sig)] px-1.5 py-0.5 font-semibold text-white'
              : 'w-5 text-[color:var(--color-ink-3)]'
          }`}
          aria-hidden="true"
        >
          {n}
        </span>

        <span
          className={
            current
              ? 'text-[16px] font-bold tracking-[-0.02em]'
              : done
                ? 'text-[13px] font-semibold'
                : 'text-[13px] text-[color:var(--color-ink-2)]'
          }
        >
          {stage.name}
        </span>

        {done && (
          <span className="inline-flex items-center gap-1 text-[12px] text-[color:var(--color-ok)]">
            <IconCheck size={11} />
            {stage.detail || t('pipeline.stateDone')}
          </span>
        )}

        {current && stage.detail && (
          <span className="mono text-[12px] text-[color:var(--color-ink-2)]">
            {stage.detail}
          </span>
        )}

        {stage.state === 'blocked' && (
          <span className="text-[12px] text-[color:var(--color-ink-3)]">
            {t('pipeline.needs')}: {stage.needs}
          </span>
        )}

        {current && (
          <span className="ml-auto shrink-0">
            <Badge tone="sig">{t('pipeline.stateCurrent')}</Badge>
          </span>
        )}
      </div>

      {current && (
        <div className="mt-1.5 flex flex-wrap items-start gap-x-4 gap-y-2 pl-0 sm:pl-7">
          <p className="min-w-0 flex-1 basis-64 text-[13px] text-[color:var(--color-ink-2)]">
            {stage.why}
          </p>
          <Link to={stage.action.to} className="btn btn-primary shrink-0 no-underline">
            <IconNext size={13} />
            {stage.action.label}
          </Link>
        </div>
      )}
    </li>
  )
}

/** La mappa completa: la home. */
export function Pipeline({ stages }: { stages: Stage[] }) {
  const { t } = useI18n()
  return (
    <Module
      tab={t('pipeline.tab')}
      flush
      aux={
        <span>
          {t('pipeline.position', {
            done: completedCount(stages),
            total: stages.length,
          })}
        </span>
      }
    >
      <ol className="ruled">
        {stages.map((s, i) => (
          <StageRow key={s.id} stage={s} n={i + 1} />
        ))}
      </ol>
    </Module>
  )
}

/**
 * La striscia compatta: sulle pagine di fase, dove la domanda non è «cosa
 * faccio adesso» ma «perché non posso procedere».
 */
export function PipelineStrip({ stages, here }: { stages: Stage[]; here: string }) {
  const { t } = useI18n()
  const i = stages.findIndex((s) => s.id === here)
  if (i < 0) return null
  const stage = stages[i]
  const blocking = stages.slice(0, i).find((s) => s.state !== 'done')

  return (
    <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-2 py-1.5">
      <span className="mono text-[11px] text-[color:var(--color-ink-3)]">
        {t('pipeline.tab')} · {i + 1}/{stages.length}
      </span>
      <span className="text-[12px] font-semibold">{stage.name}</span>

      {stage.state === 'blocked' && blocking ? (
        <>
          <Badge tone="warn">{t('pipeline.stateBlocked')}</Badge>
          <span className="text-[12px] text-[color:var(--color-ink-2)]">
            {t('pipeline.requires')} {stage.needs}
          </span>
          <Link to={blocking.action.to} className="btn btn-sm ml-auto no-underline">
            <IconNext size={11} />
            {blocking.action.label}
          </Link>
        </>
      ) : stage.state === 'done' ? (
        <>
          <Badge tone="ok">{t('pipeline.stateDone')}</Badge>
          {stage.detail && (
            <span className="mono text-[12px] text-[color:var(--color-ink-2)]">
              {stage.detail}
            </span>
          )}
        </>
      ) : (
        <>
          <Badge tone="sig">{t('pipeline.stateCurrent')}</Badge>
          {stage.detail && (
            <span className="mono text-[12px] text-[color:var(--color-ink-2)]">
              {stage.detail}
            </span>
          )}
        </>
      )}
    </div>
  )
}
