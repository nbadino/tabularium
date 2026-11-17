import type { GoldEvalPlan, GoldEvalRecord } from '../../lib/types'
import { useI18n } from '../../i18n'

const pct = (value: number | null | undefined) => (value == null ? '—' : `${(value * 100).toFixed(1)}%`)

/** The run's gold-set score, read on the training GPU before it was released. */
export function GoldEvalPanel({ record, plan }: { record?: GoldEvalRecord | null; plan?: GoldEvalPlan | null }) {
  const { t } = useI18n()
  if (!record) {
    if (!plan) return null
    if (plan.state === 'planned') {
      return <section aria-label={t('training.gold.title')} className="mb-4 space-y-1">
        <p className="text-[13px] font-semibold">{t('training.gold.title')}</p>
        <p className="text-[12px]">{t('training.gold.planned', { tables: plan.tables ?? 0, pages: plan.pages ?? 0, minutes: plan.max_minutes ?? 0 })}</p>
      </section>
    }
    return <section aria-label={t('training.gold.title')} className="mb-4 space-y-1">
      <p className="text-[13px] font-semibold">{t('training.gold.title')}</p>
      <p className="text-[12px] text-[color:var(--color-ink-3)]">{t(`training.gold.reasons.${plan.reason ?? 'not_declared'}`)}</p>
    </section>
  }
  const aggregate = record.aggregate
  const env = record.environment
  return <section aria-label={t('training.gold.title')} className="mb-4 space-y-2">
    <p className="text-[13px] font-semibold">{t('training.gold.title')}</p>
    {record.state === 'running' && <>
      <p className="text-[12px]">{record.phase === 'upload' || !record.total
        ? t('training.gold.uploading')
        : t('training.gold.running', { done: record.completed ?? 0, total: record.total ?? 0 })}</p>
      <div role="progressbar" aria-label={t('training.gold.title')} aria-valuemin={0} aria-valuemax={record.total ?? 0} aria-valuenow={record.completed ?? 0} className="h-1.5 bg-[color:var(--color-fill)]">
        <div className="h-full bg-[color:var(--color-sig)]" style={{ width: `${record.total ? 100 * (record.completed ?? 0) / record.total : 0}%` }} />
      </div>
    </>}
    {record.state === 'failed' && <div className="text-[12px] text-[color:var(--color-warn)]">
      <p>{t('training.gold.failed')}</p>
      {record.error && <details className="mt-1"><summary className="cursor-pointer font-semibold">{t('training.showLogs')}</summary><pre className="mono mt-2 overflow-auto whitespace-pre-wrap text-[11px]">{record.error}</pre></details>}
    </div>}
    {record.state === 'finished' && aggregate && <>
      {record.complete === false && <p className="text-[12px] text-[color:var(--color-warn)]">{t('training.gold.incomplete', { done: record.completed ?? 0, total: record.total ?? aggregate.tables })}</p>}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        {[
          [t('training.gold.cellExact'), pct(aggregate.cell_exact)],
          [t('training.gold.cer'), pct(aggregate.cer)],
          [t('training.gold.header'), `${aggregate.header_structure_ok}/${aggregate.tables}`],
          [t('training.gold.rows'), `${aggregate.missing_rows} / ${aggregate.extra_rows}`],
          [t('training.gold.failures'), `${aggregate.failed}/${aggregate.tables}`],
        ].map(([label, value]) => <div key={label}>
          <div className="lbl">{label}</div>
          <div className="mono text-[14px] font-semibold">{value}</div>
        </div>)}
      </div>
      <p className="text-[11px] text-[color:var(--color-ink-3)]">{t('training.gold.scope', { tables: aggregate.tables, pages: aggregate.pages, cells: aggregate.reference_cells, minutes: Math.round(aggregate.seconds / 60) })}</p>
      {env && <p className="mono text-[11px] text-[color:var(--color-ink-3)]" title={env.image_digest}>
        {t('training.gold.environment')}: {env.image} ({env.image_digest.slice(0, 19)}) · torch {env.torch ?? '?'} · transformers {env.transformers ?? '?'}{env.gpu ? ` · ${env.gpu}` : ''}
      </p>}
      {record.pages && record.pages.length > 0 && <details>
        <summary className="w-fit cursor-pointer text-[12px]">{t('training.gold.perTable')}</summary>
        <table className="mt-1 w-full text-left text-[11px]">
          <thead><tr className="text-[color:var(--color-ink-3)]">
            <th className="py-1">{t('training.gold.table')}</th><th>{t('training.gold.cellExact')}</th><th>{t('training.gold.cer')}</th><th>{t('training.gold.rows')}</th><th>{t('training.gold.failure')}</th>
          </tr></thead>
          <tbody>{record.pages.map((item) => <tr key={item.id} className="border-t border-[color:var(--color-rule)]">
            <td className="mono py-1">{item.id}</td>
            <td className="mono">{pct(item.metrics.cell_exact)}</td>
            <td className="mono">{pct(item.metrics.cer)}</td>
            <td className="mono">{item.metrics.missing_rows} / {item.metrics.extra_rows}</td>
            <td className="mono text-[color:var(--color-warn)]">{item.failure ?? ''}</td>
          </tr>)}</tbody>
        </table>
      </details>}
    </>}
  </section>
}
