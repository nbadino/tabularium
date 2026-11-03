/**
 * The Studio's consensus pane: where the models disagree on this page.
 *
 * Read-only. The canvas paints the heatmap (`overlayRects`); this pane lists the
 * blocks from the most disputed down, explains one block (text, position, class),
 * shows a table cell by cell as a heatmap, and puts the models' readings side by
 * side with a character diff against the first model.
 */
import { useState } from 'react'
import { useI18n } from '../../i18n'
import { labelName } from '../../i18n/labelName'
import {
  BAND_CLASS,
  BAND_COLOR,
  byDisagreement,
  charDiff,
  scoreBand,
  type ConsensusCluster,
  type ConsensusModel,
  type ConsensusView,
  type PageConsensus,
} from '../consensus'

const pct = (value: number) => Math.round(value * 100)

function Readings({ values, models }: { values: Record<string, string | null>; models: ConsensusModel[] }) {
  const { t } = useI18n()
  const present = models.filter((m) => m.key in values)
  const reference = present.find((m) => values[m.key] != null)
  return (
    <div className="space-y-2" aria-label={t('consensus.readings')}>
      {present.map((model) => {
        const value = values[model.key]
        const isReference = model.key === reference?.key
        return (
          <div key={model.key} className="border-l-2 border-[color:var(--color-rule-strong)] pl-2">
            <div className="text-[11px] font-semibold text-[color:var(--color-ink-2)]">
              {model.name}{isReference && <span className="ml-1 font-normal text-[color:var(--color-ink-3)]">· {t('consensus.reference')}</span>}
            </div>
            {value == null ? (
              <div className="text-[12px] italic text-[color:var(--color-ink-3)]">{t('consensus.noValue')}</div>
            ) : (
              <div className="mono whitespace-pre-wrap break-words text-[12px]" data-testid={`reading-${model.key}`}>
                {isReference || reference == null
                  ? value
                  : charDiff(values[reference.key] ?? '', value).map((part, index) =>
                      part.kind === 'same' ? <span key={index}>{part.text}</span>
                        : part.kind === 'ins' ? <ins key={index} className="bg-[color:var(--color-ok-wash)] text-[color:var(--color-ok)] no-underline">{part.text}</ins>
                          : <del key={index} className="bg-[color:var(--color-sig-wash)] text-[color:var(--color-sig-text)]">{part.text}</del>)}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function TableHeatmap({ cluster, models }: { cluster: ConsensusCluster; models: ConsensusModel[] }) {
  const { t } = useI18n()
  const table = cluster.table!
  const [cell, setCell] = useState<{ r: number; c: number } | null>(null)
  const byPos = new Map(table.cells.map((item) => [`${item.r}:${item.c}`, item]))
  const covered = new Set<string>()
  for (const item of table.cells) {
    for (let r = item.r; r < item.r + item.rowspan; r++) {
      for (let c = item.c; c < item.c + item.colspan; c++) if (r !== item.r || c !== item.c) covered.add(`${r}:${c}`)
    }
  }
  const selected = cell ? byPos.get(`${cell.r}:${cell.c}`) : undefined
  // A long register hides its few disagreements: count them and step through them.
  const disputed = table.cells.filter((item) => scoreBand(item.score) !== 'agree')
  const next = () => {
    if (!disputed.length) return
    const at = cell ? disputed.findIndex((item) => item.r > cell.r || (item.r === cell.r && item.c > cell.c)) : 0
    const target = disputed[at < 0 ? 0 : at]
    setCell({ r: target.r, c: target.c })
    document.querySelector(`[data-testid="cell-${target.r}-${target.c}"]`)?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
  }
  const nameOf = (key: string) => models.find((m) => m.key === key)?.name ?? key
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-[color:var(--color-ink-3)]">{t('consensus.tableCells')}</span>
        <span className="text-[11px] text-[color:var(--color-ink-2)]" data-testid="disputed-count">{t('consensus.disputedCells', { n: disputed.length })}</span>
        {disputed.length > 0 && <button type="button" className="btn ml-auto" onClick={next}>{t('consensus.nextCell')}</button>}
      </div>
      <div className="max-h-[360px] overflow-auto border border-[color:var(--color-rule)]">
        <table className="w-full border-collapse text-[11px]">
          <tbody>
            {Array.from({ length: table.rows }, (_, r) => (
              <tr key={r}>
                {Array.from({ length: table.cols }, (_, c) => {
                  const key = `${r}:${c}`
                  if (covered.has(key)) return null
                  const item = byPos.get(key)
                  if (!item) return <td key={c} className="border border-[color:var(--color-rule)]" />
                  const ref = item.values[table.reference] ?? ''
                  const active = cell?.r === r && cell?.c === c
                  return (
                    <td
                      key={c}
                      rowSpan={item.rowspan}
                      colSpan={item.colspan}
                      className={`cursor-pointer border p-1 align-top ${BAND_CLASS[scoreBand(item.score)]} ${active ? 'border-2 border-[color:var(--color-ink)]' : 'border-[color:var(--color-rule)]'}`}
                      title={`${pct(item.score)}%`}
                      data-testid={`cell-${r}-${c}`}
                      onClick={() => setCell({ r, c })}
                    >
                      {ref || ' '}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.extra_rows.map((row) => (
        <div key={`${row.model}-${row.index}`} className="border border-dashed border-[color:var(--color-sig)] p-1 text-[11px] text-[color:var(--color-sig-text)]">
          {t('consensus.extraRow', { name: nameOf(row.model), texts: row.texts.filter(Boolean).join(' · ') })}
        </div>
      ))}
      {Object.entries(table.extra_cols.reduce<Record<string, number>>((acc, col) => ({ ...acc, [col.model]: (acc[col.model] ?? 0) + 1 }), {})).map(([model, n]) => (
        <div key={model} className="text-[11px] text-[color:var(--color-sig-text)]">{t('consensus.extraCols', { name: nameOf(model), n })}</div>
      ))}
      {selected ? <Readings values={selected.values} models={models} /> : <p className="text-[11px] text-[color:var(--color-ink-3)]">{t('consensus.cellHint')}</p>}
    </div>
  )
}

export default function ConsensusPane({
  page,
  loading,
  view,
  onView,
  selected,
  onSelect,
  onClose,
}: {
  page: PageConsensus | null
  loading: boolean
  view: ConsensusView
  onView: (view: ConsensusView) => void
  selected: number | null
  onSelect: (cluster: number | null) => void
  onClose: () => void
}) {
  const { t } = useI18n()
  const models = page?.models ?? []
  const nameOf = (key: string) => models.find((m) => m.key === key)?.name ?? key
  const clusters = page?.comparison?.clusters ?? []
  const current = clusters.find((c) => c.id === selected) ?? null

  return (
    <div className="space-y-3 text-[12px]" aria-label={t('consensus.title')}>
      <div className="flex items-center gap-2">
        <h2 className="text-[14px] font-semibold">{t('consensus.title')}</h2>
        {page?.run_id != null && <span className="mono text-[11px] text-[color:var(--color-ink-3)]">{t('consensus.run', { id: page.run_id })}</span>}
        <button type="button" className="btn ml-auto" onClick={onClose}>{t('consensus.close')}</button>
      </div>
      <p className="text-[11px] text-[color:var(--color-ink-3)]">{t('consensus.readOnly')}</p>

      {loading && !page && <p className="text-[color:var(--color-ink-3)]">{t('consensus.loading')}</p>}
      {page && page.run_id == null && (
        <div><p className="font-semibold">{t('consensus.noRun')}</p><p className="text-[color:var(--color-ink-2)]">{t('consensus.noRunHint')}</p></div>
      )}
      {models.filter((m) => m.state === 'failed').map((m) => (
        <p key={m.key} className="text-[color:var(--color-sig-text)]">{t('consensus.modelFailed', { name: m.name, error: m.error ?? '' })}</p>
      ))}
      {models.filter((m) => m.state === 'queued' || m.state === 'running').map((m) => (
        <p key={m.key} className="text-[color:var(--color-ink-2)]">{t('consensus.modelPending', { name: m.name })}</p>
      ))}
      {page && page.run_id != null && !page.comparison && <p className="text-[color:var(--color-warn)]">{t('consensus.notEnough')}</p>}

      {page?.comparison && (
        <>
          <div className="flex items-baseline gap-2">
            <span className="text-[20px] font-bold" data-testid="agreement">{t('consensus.agreement', { pct: pct(page.comparison.agreement) })}</span>
          </div>
          <div role="group" aria-label={t('consensus.viewHint')} className="flex flex-wrap gap-1">
            {(['disagreement', ...page.comparison.models] as ConsensusView[]).map((key) => (
              <button
                key={key}
                type="button"
                aria-pressed={view === key}
                className={`btn ${view === key ? 'btn-primary' : ''}`}
                onClick={() => onView(key)}
              >
                {key === 'disagreement' ? t('consensus.viewDisagreement') : nameOf(key)}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap gap-3 text-[11px] text-[color:var(--color-ink-2)]">
            {(['agree', 'partly', 'disagree'] as const).map((band) => (
              <span key={band} className="inline-flex items-center gap-1">
                <span className="inline-block h-2.5 w-2.5" style={{ background: BAND_COLOR[band] }} />
                {t(`consensus.legend${band[0].toUpperCase()}${band.slice(1)}`)}
              </span>
            ))}
            <span>{t('consensus.legendDashed')}</span>
          </div>

          <div>
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[color:var(--color-ink-3)]">{t('consensus.blocks')}</div>
            <ul className="max-h-[220px] divide-y divide-[color:var(--color-rule)] overflow-y-auto border border-[color:var(--color-rule)]">
              {byDisagreement(clusters).map((cluster) => (
                <li key={cluster.id}>
                  <button
                    type="button"
                    aria-pressed={cluster.id === selected}
                    onClick={() => onSelect(cluster.id)}
                    className={`flex w-full items-center gap-2 px-2 py-1 text-left hover:bg-[color:var(--color-fill)] ${cluster.id === selected ? 'bg-[color:var(--color-fill-2)]' : ''}`}
                  >
                    <span className="inline-block h-2.5 w-2.5 shrink-0" style={{ background: BAND_COLOR[scoreBand(cluster.score)] }} />
                    <span className="min-w-0 flex-1 truncate">{labelName(cluster.label)}</span>
                    {cluster.missing.length > 0 && (
                      <span className="text-[11px] text-[color:var(--color-sig-text)]">
                        {t('consensus.missingIn', { names: cluster.missing.map(nameOf).join(', ') })}
                      </span>
                    )}
                    <span className="mono w-10 text-right">{pct(cluster.score)}%</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>

          {current ? (
            <div className="space-y-2 border-t border-[color:var(--color-rule)] pt-2" data-testid="cluster-detail">
              <dl className="grid grid-cols-3 gap-2">
                <div><dt className="lbl">{t('consensus.text')}</dt><dd className="mono">{pct(current.text)}%</dd></div>
                <div><dt className="lbl">{t('consensus.layout')}</dt><dd className="mono">{pct(current.layout)}%</dd></div>
                <div>
                  <dt className="lbl">{t('consensus.label')}</dt>
                  <dd>{current.label_agree
                    ? t('consensus.labelSame')
                    : Object.entries(current.labels).map(([key, label]) => `${nameOf(key)}: ${labelName(label)}`).join(' · ')}</dd>
                </div>
              </dl>
              {current.missing.length > 0 && (
                <p className="text-[color:var(--color-sig-text)]">
                  {t('consensus.onlyIn', {
                    names: Object.entries(current.members).filter(([, v]) => v != null).map(([k]) => nameOf(k)).join(', '),
                  })}
                </p>
              )}
              {current.table ? (
                <TableHeatmap key={current.id} cluster={current} models={models} />
              ) : (
                <Readings
                  values={Object.fromEntries(models.filter((m) => m.key in current.members).map((m) => {
                    const index = current.members[m.key]
                    return [m.key, index == null ? null : page.outputs[m.key]?.[index]?.content ?? '']
                  }))}
                  models={models}
                />
              )}
            </div>
          ) : (
            <p className="text-[11px] text-[color:var(--color-ink-3)]">{t('consensus.selectHint')}</p>
          )}
        </>
      )}
    </div>
  )
}
