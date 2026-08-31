/**
 * Dialog di conferma del prefill.
 *
 * Il prefill è l'unico gesto dello studio che può cancellare lavoro esistente:
 * qui l'utente vede cosa c'è sulla pagina, sceglie cosa sostituire e conferma.
 * Nessuna modalità distruttiva parte da un semplice clic sul pulsante Prefill.
 */
import { useState } from 'react'
import { Badge, Modal } from '../../app/ui'
import { useI18n } from '../../i18n'
import {
  defaultPrefillMode,
  prefillSeverity,
  replacementPlan,
  type PrefillMode,
  type PrefillPageSummary,
} from '../prefill'

interface PrefillDialogProps {
  summary: PrefillPageSummary
  busy: boolean
  onRun: (mode: PrefillMode) => void
  onClose: () => void
}

export default function PrefillDialog({ summary, busy, onRun, onClose }: PrefillDialogProps) {
  const { t, tn } = useI18n()
  const [mode, setMode] = useState<PrefillMode>(() => defaultPrefillMode(summary))
  const plan = replacementPlan(mode, summary)
  const severity = prefillSeverity(mode, summary)

  const modes: Array<{ id: PrefillMode; label: string; hint: string }> = [
    {
      id: 'merge',
      label: t('annotate.prefillModeMerge'),
      hint: t('annotate.prefillModeMergeHint'),
    },
    {
      id: 'replace_drafts',
      label: t('annotate.prefillModeDrafts'),
      hint: t('annotate.prefillModeDraftsHint'),
    },
    {
      id: 'replace_all',
      label: t('annotate.prefillModeAll'),
      hint: t('annotate.prefillModeAllHint'),
    },
  ]

  return (
    <Modal title={t('annotate.prefillDialogTitle')} onClose={onClose} footer={
      <>
        <button type="button" className="btn btn-sm" onClick={onClose}>
          {t('common.cancel')}
        </button>
        <button
          type="button"
          className="btn btn-sm btn-primary"
          disabled={busy}
          onClick={() => onRun(mode)}
        >
          {busy ? t('annotate.ocrBusy') : t('annotate.prefillRun')}
        </button>
      </>
    }>
      <div className="space-y-3">
        <p className="text-[13px] text-[color:var(--color-ink-2)]">
          {t('annotate.prefillDialogIntro')}
        </p>
        <ul className="mono space-y-1 border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2 text-[12px] text-[color:var(--color-ink)]">
          <li>
            {tn('annotate.prefillCountBlocks', summary.blocks)}{' '}
            {tn('annotate.prefillCountTables', summary.tables)}
          </li>
          <li>{tn('annotate.prefillCountDrafts', summary.drafts)}</li>
        </ul>

        <fieldset className="space-y-1.5">
          <legend className="lbl">{t('annotate.prefillModeLabel')}</legend>
          {modes.map((m) => (
            <label key={m.id} className="flex cursor-pointer items-start gap-2">
              <input
                type="radio"
                name="prefill-mode"
                value={m.id}
                checked={mode === m.id}
                onChange={() => setMode(m.id)}
                className="mt-1 accent-[color:var(--color-sig)]"
              />
              <span>
                <span className="text-[13px] font-semibold text-[color:var(--color-ink)]">
                  {m.label}
                </span>
                <span className="block text-[11px] text-[color:var(--color-ink-2)]">
                  {m.hint}
                </span>
              </span>
            </label>
          ))}
        </fieldset>

        {plan.blocks > 0 && (
          <p className="text-[12px] text-[color:var(--color-ink-2)]">
            {tn('annotate.prefillPlan', plan.blocks)}
            {plan.tables > 0 && ' ' + tn('annotate.prefillPlanTables', plan.tables)}
          </p>
        )}
        {severity === 'human' && (
          <p className="border border-[color:var(--color-warn-rule)] bg-[color:var(--color-warn-wash)] p-2 text-[12px] text-[color:var(--color-warn)]">
            {t('annotate.prefillHumanWarning')}
          </p>
        )}
        <p className="text-[11px] text-[color:var(--color-ink-3)]">
          <Badge tone="neutral">{t('annotate.prefillSafeBadge')}</Badge>{' '}
          {t('annotate.prefillSafeNote')}
        </p>
      </div>
    </Modal>
  )
}
