/**
 * I percorsi del corpus.
 *
 * Sostituisce la catena unica di sei tappe: il corpus si lavora su tre
 * percorsi indipendenti — riconoscere, annotare, raffinare — e nessuno è
 * obbligatorio. Chi vuole soltanto riconoscere ed esportare non deve
 * attraversare dataset, training e valutazione; chi annota senza mai
 * addestrare sta comunque facendo un lavoro completo.
 *
 * Le tre righe dicono lo stato reale di ciascun percorso e portano all'unica
 * azione che lo fa avanzare. Il blocco è dichiarato, non nascosto: una riga
 * bloccata dice cosa manca.
 */
import { Link } from 'react-router'
import { useI18n } from '../i18n'
import { Badge, Module } from './ui'
import { IconNext } from './icons'
import type { Branch, Stage } from './pipeline'

function BranchRow({ branch }: { branch: Branch }) {
  const { t } = useI18n()
  const blocked = branch.state === 'blocked'
  const active = branch.state === 'active'

  return (
    <li className="border-b border-[color:var(--color-rule)] px-2 py-2 last:border-b-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className={branch.recommended ? 'text-[15px] font-bold tracking-[-0.02em]' : 'text-[14px] font-semibold'}>
          {branch.name}
        </span>
        {(blocked || active) && (
          <Badge tone={blocked ? 'warn' : 'sig'}>
            {blocked ? t('pipeline.stateBlocked') : t('pipeline.stateCurrent')}
          </Badge>
        )}
        {branch.detail && (
          <span className="mono text-[12px] text-[color:var(--color-ink-2)]">{branch.detail}</span>
        )}
        {blocked && (
          <span className="text-[12px] text-[color:var(--color-ink-2)]">
            {t('pipeline.needs')}: {branch.needs}
          </span>
        )}
        {!blocked && (
          <Link
            to={branch.action.to}
            className={`ml-auto shrink-0 no-underline ${branch.recommended ? 'btn btn-primary' : 'btn btn-sm'}`}
          >
            <IconNext size={12} />
            {branch.action.label}
          </Link>
        )}
      </div>
    </li>
  )
}

/** I tre percorsi del corpus: la home dell'archivio. */
export function CorpusPaths({ branches }: { branches: Branch[] }) {
  const { t } = useI18n()
  return (
    <Module tab={t('pipeline.pathsTab')} flush aux={<span>{t('pipeline.pathsHint')}</span>}>
      <ul className="ruled">
        {branches.map((b) => (
          <BranchRow key={b.id} branch={b} />
        ))}
      </ul>
    </Module>
  )
}

/**
 * Il prerequisito mancante, non la mappa del percorso.
 *
 * Su una pagina di fase la domanda è una sola: «perché non posso procedere?».
 * Finché il prerequisito c'è, questa striscia non compare: la pagina fa il suo
 * lavoro e non ripete l'intero percorso in miniatura. Quando manca, dice cosa
 * manca e porta al punto che lo sblocca.
 */
export function PipelineStrip({ stages, here }: { stages: Stage[]; here: string }) {
  const { t } = useI18n()
  const i = stages.findIndex((s) => s.id === here)
  if (i < 0) return null
  const stage = stages[i]
  if (stage.state !== 'blocked') return null
  const blocking = stages.slice(0, i).find((s) => s.state !== 'done')

  return (
    <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 border border-[color:var(--color-warn)] bg-[color:var(--color-warn-wash)] px-2 py-1.5">
      <Badge tone="warn">{t('pipeline.stateBlocked')}</Badge>
      <span className="text-[12px] text-[color:var(--color-ink)]">
        {t('pipeline.requires')} {stage.needs}
      </span>
      {blocking && (
        <Link to={blocking.action.to} className="btn btn-sm ml-auto no-underline">
          <IconNext size={11} />
          {blocking.action.label}
        </Link>
      )}
    </div>
  )
}
