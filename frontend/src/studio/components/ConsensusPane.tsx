/**
 * Model comparison inside the page content: where the models disagree, said in words.
 *
 * In comparison mode the Studio keeps its content rail; these pieces go into it.
 * `ConsensusSummary` heads the rail (agreement, what is left by kind, the next one, a
 * model as the page's base). Each row of the user's own blocks carries a
 * `ClusterChip` with its verdict and, for the selected area, a `ClusterDetail`: what
 * differs (text, position, class, segmentation) in words, the models' readings diffed
 * against the user's, and what each action will do before it is done.
 * `ModelOnlyClusters` lists the areas the models read and the user has no block for.
 * The canvas paints the same verdicts on the page (`overlayRects`).
 */
import { useState } from 'react'
import { useI18n } from '../../i18n'
import { labelName } from '../../i18n/labelName'
import {
  BAND_CLASS,
  BAND_COLOR,
  CONVENTION_COLOR,
  RESOLVED_COLOR,
  STRUCTURE_COLOR,
  cellBand,
  charDiff,
  disputedCells,
  isTableCluster,
  openCounts,
  partsOf,
  readingDistance,
  scoreBand,
  verdictColor,
  verdictOf,
  type ConsensusCluster,
  type ConsensusModel,
  type ConsensusTable,
  type ConventionRule,
  type ConsensusView,
  type MineItem,
  type PageConsensus,
} from '../consensus'

/** What the user can do with a disputed block; without it the pane only shows. */
export interface ConsensusActions {
  /** The user's blocks (and drafts) that read the cluster's area. */
  mine: (cluster: ConsensusCluster) => MineItem[]
  resolved: Set<number>
  /** False for a reader: the actions are not offered. */
  canEdit: boolean
  onUse: (cluster: ConsensusCluster, model: string) => void
  onRemove: (cluster: ConsensusCluster) => void
  /** Marks the block as fine as it is, or opens it again. */
  onToggleResolved: (cluster: ConsensusCluster) => void
  onNext: () => void
  /** The model's whole reading becomes the page's drafts. */
  onAdopt: (model: string) => void
}


const pct = (value: number) => Math.round(value * 100)

type T = (key: string, vars?: Record<string, string | number>) => string

/** The convention rules, named, with how many cells (or blocks) need each. */
function rulesText(rules: Partial<Record<ConventionRule, number>> | ConventionRule[], t: T): string {
  const entries = Array.isArray(rules) ? rules.map((rule) => [rule, 0] as const) : Object.entries(rules) as [ConventionRule, number][]
  return entries.map(([rule, n]) => n ? `${t(`consensus.rule.${rule}`)} ${n}` : t(`consensus.rule.${rule}`)).join(' · ')
}

/** A table's cells by kind of difference: numbers, words, missing; then the conventions apart. */
function CellKinds({ table }: { table: ConsensusTable }) {
  const { t, tn } = useI18n()
  const { digits, text, missing, convention } = table.kinds
  return (
    <div className="space-y-0.5 text-[11px]" data-testid="cell-kinds">
      <div className="flex flex-wrap gap-x-3">
        {digits > 0 && <span className="font-semibold" style={{ color: BAND_COLOR.disagree }}>{tn('consensus.kindCount.digits', digits)}</span>}
        {text > 0 && <span className="font-semibold text-[color:var(--color-warn)]">{tn('consensus.kindCount.text', text)}</span>}
        {missing > 0 && <span className="font-semibold text-[color:var(--color-sig-text)]">{tn('consensus.kindCount.missing', missing)}</span>}
      </div>
      {convention > 0 && (
        <div className="text-[color:var(--color-ink-2)]">
          {tn('consensus.kindCount.convention', convention)}: {rulesText(table.conventions, t)}
        </div>
      )}
    </div>
  )
}

const kbd = (key: string) => <kbd className="mono text-[10px] text-[color:var(--color-ink-3)]">{key}</kbd>

function Readings({ values, models, reference: mine }: {
  values: Record<string, string | null>
  models: ConsensusModel[]
  /** The user's own text, already shown in the row: the models are diffed against it. */
  reference?: string | null
}) {
  const { t } = useI18n()
  const present = models.filter((m) => m.key in values)
  const first = present.find((m) => values[m.key] != null)
  const base = mine != null ? mine : first ? values[first.key] : null
  return (
    <div className="space-y-2" aria-label={t('consensus.readings')}>
      {mine != null && <p className="text-[11px] text-[color:var(--color-ink-3)]">{t('consensus.diffAgainstMine')}</p>}
      {present.map((model) => {
        const value = values[model.key]
        const isBase = mine == null && model.key === first?.key
        return (
          <div key={model.key} className="border-l-2 border-[color:var(--color-rule-strong)] pl-2">
            <div className="text-[11px] font-semibold text-[color:var(--color-ink-2)]">
              {model.name}{isBase && <span className="ml-1 font-normal text-[color:var(--color-ink-3)]">· {t('consensus.reference')}</span>}
            </div>
            {value == null ? (
              <div className="text-[12px] italic text-[color:var(--color-ink-3)]">{t('consensus.noValue')}</div>
            ) : (
              <div className="mono whitespace-pre-wrap break-words text-[12px]" data-testid={`reading-${model.key}`}>
                {isBase || base == null
                  ? value
                  : charDiff(base, value).map((part, index) =>
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
  // A long register hides its few disagreements: count them and step through them,
  // numbers first. A cell that differs only by a convention is not one of them.
  const disputed = disputedCells(table.cells)
  const next = () => {
    if (!disputed.length) return
    const at = cell ? disputed.findIndex((item) => item.r === cell.r && item.c === cell.c) : -1
    const target = disputed[(at + 1) % disputed.length]
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
      <CellKinds table={table} />
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
                      className={`cursor-pointer border p-1 align-top ${BAND_CLASS[cellBand(item)]} ${active ? 'border-2 border-[color:var(--color-ink)]' : 'border-[color:var(--color-rule)]'}`}
                      title={cellTitle(item, t)}
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
      {selected && selected.kind !== 'same' && <p className="text-[11px] font-semibold" data-testid="cell-kind">{cellTitle(selected, t)}</p>}
      {selected ? <Readings values={selected.values} models={models} /> : <p className="text-[11px] text-[color:var(--color-ink-3)]">{t('consensus.cellHint')}</p>}
    </div>
  )
}

/** What kind of difference a cell is, in words: "numero diverso", "convenzione: lineetta". */
function cellTitle(cell: ConsensusTable['cells'][number], t: T): string {
  if (cell.kind === 'same') return t('consensus.kind.same')
  if (cell.kind === 'convention') return `${t('consensus.kind.convention')}: ${rulesText(cell.conventions, t)}`
  return `${t(`consensus.kind.${cell.kind}`)} (${pct(cell.score)}%)`
}

/** Joined labels of a model's blocks in the area, in the user's language. */
function labelsOf(page: PageConsensus, cluster: ConsensusCluster, key: string): string {
  return [...new Set(partsOf(page, cluster, key).map((b) => b.label))].map(labelName).join(' + ')
}

/** The verdict in a few words: the chip of a row, and the tag on the canvas. */
export function verdictTag(cluster: ConsensusCluster, page: PageConsensus, t: (key: string, vars?: Record<string, string | number>) => string): string {
  const verdict = verdictOf(cluster)
  const nameOf = (key: string) => page.models.find((m) => m.key === key)?.name ?? key
  if (verdict.kind === 'missing') {
    const found = Object.keys(cluster.parts ?? {}).filter((key) => partsOf(page, cluster, key).length > 0)
    return t('consensus.tagOnlyIn', { names: found.map(nameOf).join(', ') })
  }
  if (verdict.kind === 'text') {
    if (cluster.table) return t('consensus.tagCells', { n: disputedCells(cluster.table.cells).length })
    return verdict.digits ? t('consensus.tagDigits') : t('consensus.tagText', { pct: pct(cluster.text) })
  }
  if (verdict.kind === 'structure') {
    return verdict.split ? t('consensus.tagSplit') : verdict.labelsDiffer ? t('consensus.tagLabel') : t('consensus.tagLayout')
  }
  if (verdict.kind === 'convention') return t('consensus.tagConvention', { rules: rulesText(cluster.conventions, t) })
  return t('consensus.tagAgree')
}

const CHIP_CLASS: Record<string, string> = {
  text: 'border-[color:var(--color-sig)] text-[color:var(--color-sig-text)] bg-[color:var(--color-sig-wash)]',
  partly: 'border-[color:var(--color-warn-rule)] text-[color:var(--color-warn)] bg-[color:var(--color-warn-wash)]',
  missing: 'border-dashed border-[color:var(--color-sig)] text-[color:var(--color-sig-text)]',
  structure: 'border-[color:var(--color-rule-strong)] text-[color:var(--color-ink-2)] bg-[color:var(--color-fill)]',
  convention: 'border-dotted border-[color:var(--color-rule-strong)] text-[color:var(--color-ink-2)]',
  agree: 'border-[color:var(--color-ok-rule)] text-[color:var(--color-ok)]',
  resolved: 'border-[#7c3aed] text-[#5b21b6] bg-[rgba(124,58,237,0.08)]',
}

/** The verdict of the area a row reads, as a button that opens its detail. */
export function ClusterChip({ cluster, page, resolved, selected, onSelect }: {
  cluster: ConsensusCluster
  page: PageConsensus
  resolved: boolean
  selected: boolean
  onSelect: () => void
}) {
  const { t } = useI18n()
  const verdict = verdictOf(cluster)
  const tone = resolved ? 'resolved'
    : verdict.kind === 'text' && !verdict.digits && scoreBand(cluster.text) !== 'disagree' ? 'partly'
      : verdict.kind
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      data-testid={`chip-${cluster.id}`}
      className={`inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[11px] font-semibold ${CHIP_CLASS[tone]} ${selected ? 'ring-2 ring-[color:var(--color-ink)]' : ''}`}
    >
      {resolved ? `✓ ${t('consensus.legendResolved')} · ` : ''}{verdictTag(cluster, page, t)}
    </button>
  )
}

/** One aspect of the verdict on its own line: a mark, the aspect, what the models did. */
function Aspect({ ok, name, children }: { ok: boolean; name: string; children: React.ReactNode }) {
  return (
    <li className="flex gap-1.5">
      <span aria-hidden className={ok ? 'text-[color:var(--color-ok)]' : 'text-[color:var(--color-sig-text)]'}>{ok ? '✓' : '≠'}</span>
      <span className="font-semibold">{`${name}:`}</span>{' '}
      <span className="min-w-0 flex-1 text-[color:var(--color-ink-2)]">{children}</span>
    </li>
  )
}

/** What differs in the area, the models' readings, and the actions with what each will do. */
export function ClusterDetail({ cluster, page, mine, resolved, actions }: {
  cluster: ConsensusCluster
  page: PageConsensus
  mine: MineItem[]
  resolved: boolean
  actions?: ConsensusActions
}) {
  const { t } = useI18n()
  const models = page.models
  const keys = page.comparison?.models ?? []
  const nameOf = (key: string) => models.find((m) => m.key === key)?.name ?? key
  const verdict = verdictOf(cluster)
  const found = keys.filter((key) => partsOf(page, cluster, key).length > 0)
  const isTable = isTableCluster(page, cluster, mine)
  const disputed = cluster.table ? disputedCells(cluster.table.cells).length : 0
  const headline = verdict.kind === 'missing' ? t('consensus.headMissing', { names: found.map(nameOf).join(', ') })
    : verdict.kind === 'text' ? t(isTable ? 'consensus.headCells' : verdict.digits ? 'consensus.headDigits' : 'consensus.headText')
      : verdict.kind === 'structure' ? t('consensus.headStructure')
        : verdict.kind === 'convention' ? t('consensus.headConvention')
          : t('consensus.headAgree')
  const perModel = (render: (key: string) => string) => keys.map((key) => `${nameOf(key)}: ${render(key)}`).join(' · ')
  const readings = Object.fromEntries(keys.map((key) => {
    const parts = partsOf(page, cluster, key)
    return [key, parts.length === 0 ? null : parts.map((b) => b.content).join('\n')]
  })) as Record<string, string | null>
  const myText = mine.length ? mine.map((item) => item.content).join('\n') : null
  // The models may agree with each other and not with the user (a correction, or a slip).
  const mineDiffers = !isTable && myText != null && Object.values(readings).some((value) => value != null && readingDistance(myText, value) >= 0.02)

  return (
    <div className="mt-2 space-y-2 border-t border-[color:var(--color-rule)] pt-2 text-[12px]" data-testid="cluster-detail">
      <p className="font-semibold" style={{ color: verdictColor(cluster, resolved) }}>{headline}</p>
      {verdict.kind !== 'missing' && (
        <ul className="space-y-0.5 text-[12px]" aria-label={t('consensus.aspects')}>
          <Aspect ok={isTable ? disputed === 0 : !verdict.digits && (verdict.text === 'same' || verdict.text === 'close')} name={t('consensus.text')}>
            {isTable
              ? t('consensus.cellsDiffer', { n: disputed, total: cluster.table?.cells.length ?? 0 })
              : t(`consensus.degree.${verdict.text}`, { pct: pct(cluster.text) })}
            {isTable && cluster.table && cluster.table.extra_rows.length > 0 && ` · ${t('consensus.extraRowsCount', { n: cluster.table.extra_rows.length })}`}
            {verdict.split && !isTable && ` ${t('consensus.anyOrder')}`}
          </Aspect>
          {cluster.conventions.length > 0 && (
            <Aspect ok name={t('consensus.conventionsAspect')}>
              {cluster.table ? rulesText(cluster.table.conventions, t) : rulesText(cluster.conventions, t)}
            </Aspect>
          )}
          <Aspect ok={verdict.layout !== 'different'} name={t('consensus.layout')}>
            {t(`consensus.layoutDegree.${verdict.layout}`)}
          </Aspect>
          {verdict.split ? (
            <Aspect ok={false} name={t('consensus.segmentation')}>
              {perModel((key) => {
                const n = partsOf(page, cluster, key).length
                return `${t(n === 1 ? 'consensus.oneBlock' : 'consensus.nBlocks', { n })} (${labelsOf(page, cluster, key)})`
              })}
            </Aspect>
          ) : (
            <Aspect ok={!verdict.labelsDiffer} name={t('consensus.label')}>
              {verdict.labelsDiffer ? perModel((key) => labelsOf(page, cluster, key)) : labelName(cluster.label)}
            </Aspect>
          )}
        </ul>
      )}

      {cluster.table && mine.length === 0 ? (
        <TableHeatmap key={cluster.id} cluster={cluster} models={models} />
      ) : isTable ? (
        <p className="text-[11px] text-[color:var(--color-ink-2)]">{t('consensus.tableAction')}</p>
      ) : (verdict.kind !== 'agree' || mine.length === 0 || mineDiffers) && (
        <>
          {mineDiffers && <p className="text-[12px] font-semibold text-[color:var(--color-warn)]">{t('consensus.mineDiffers')}</p>}
          <Readings values={readings} models={models} reference={myText} />
        </>
      )}

      {actions?.canEdit && (
        <div className="space-y-1" data-testid="cluster-actions">
          <div className="flex flex-wrap gap-1">
            {!isTable && keys.map((key, index) => found.includes(key) && (
              <button key={key} type="button" className="btn" aria-keyshortcuts={String(index + 1)} onClick={() => actions.onUse(cluster, key)}>
                {t('consensus.useReading', { name: nameOf(key) })} {kbd(String(index + 1))}
              </button>
            ))}
            {!isTable && mine.length > 0 && (
              <button type="button" className="btn" aria-keyshortcuts="X" onClick={() => actions.onRemove(cluster)}>
                {t('consensus.removeMine', { n: mine.length })} {kbd('X')}
              </button>
            )}
            <button type="button" className="btn" aria-pressed={resolved} aria-keyshortcuts="A" onClick={() => actions.onToggleResolved(cluster)}>
              {resolved ? t('consensus.reopen') : t('consensus.keep')} {kbd('A')}
            </button>
          </div>
          {!isTable && (
            <ul className="space-y-0.5 text-[11px] text-[color:var(--color-ink-3)]" data-testid="use-preview">
              {found.map((key) => <li key={key}>{t('consensus.useReading', { name: nameOf(key) })} → {previewOf(page, cluster, key, mine, t)}</li>)}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

/** What *Use <model>* will do here, said before it is done. */
function previewOf(page: PageConsensus, cluster: ConsensusCluster, key: string, mine: MineItem[], t: (key: string, vars?: Record<string, string | number>) => string): string {
  const parts = partsOf(page, cluster, key)
  const labels = parts.map((b) => labelName(b.label)).join(', ')
  if (mine.length === 1 && parts.length === 1) return t('consensus.previewPatch', { label: labels })
  if (mine.length === 0) return t(parts.length === 1 ? 'consensus.previewAddOne' : 'consensus.previewAdd', { n: parts.length, labels })
  return t('consensus.previewReplace', { n: parts.length, labels, mine: mine.length })
}

/** Head of the content rail in comparison mode. */
export function ConsensusSummary({ page, loading, view, onView, onClose, actions }: {
  page: PageConsensus | null
  loading: boolean
  view: ConsensusView
  onView: (view: ConsensusView) => void
  onClose: () => void
  actions?: ConsensusActions
}) {
  const { t, tn } = useI18n()
  const models = page?.models ?? []
  const nameOf = (key: string) => models.find((m) => m.key === key)?.name ?? key
  const clusters = page?.comparison?.clusters ?? []
  const resolved = actions?.resolved ?? new Set<number>()
  const counts = openCounts(clusters, resolved)
  const open = counts.text + counts.missing + counts.structure
  return (
    <section className="mb-3 space-y-2 border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sheet)] p-2.5 text-[12px]" aria-label={t('consensus.title')}>
      <div className="flex items-center gap-2">
        <h2 className="text-[13px] font-semibold">{t('consensus.title')}</h2>
        {page?.run_id != null && <span className="mono text-[11px] text-[color:var(--color-ink-3)]">{t('consensus.run', { id: page.run_id })}</span>}
        <button type="button" className="btn ml-auto" onClick={onClose}>{t('consensus.close')}</button>
      </div>

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
          <p className="text-[12px] text-[color:var(--color-ink-2)]">{page.comparison.models.map(nameOf).join(' · ')}</p>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1" data-testid="open-count">
            {open === 0 ? (
              <span className="font-semibold text-[color:var(--color-ok)]">{t('consensus.allSettled')}</span>
            ) : (
              <>
                {counts.text > 0 && <span className="font-semibold" style={{ color: BAND_COLOR.disagree }}>{tn('consensus.countText', counts.text)}</span>}
                {counts.missing > 0 && <span className="font-semibold text-[color:var(--color-sig-text)]">{tn('consensus.countMissing', counts.missing)}</span>}
                {counts.structure > 0 && <span className="font-semibold" style={{ color: STRUCTURE_COLOR }}>{tn('consensus.countStructure', counts.structure)}</span>}
              </>
            )}
            {actions && open > 0 && (
              <button type="button" className="btn btn-primary ml-auto" aria-keyshortcuts="N" onClick={actions.onNext}>
                {t('consensus.nextBlock')} {kbd('N')}
              </button>
            )}
          </div>
          {Object.keys(page.comparison.conventions).length > 0 && (
            // Not a misreading: the same print written two ways, to be settled once per rule.
            <p className="text-[11px] text-[color:var(--color-ink-2)]" data-testid="page-conventions">
              <span className="font-semibold">{t('consensus.pageConventions')}</span> {rulesText(page.comparison.conventions, t)}
            </p>
          )}
          <p className="text-[11px] text-[color:var(--color-ink-3)]">{t(actions?.canEdit ? 'consensus.workHint' : 'consensus.readOnly')}</p>
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-[color:var(--color-ink-2)]">
            <span className="inline-flex items-center gap-1"><span className="inline-block h-2.5 w-2.5" style={{ background: BAND_COLOR.disagree }} />{t('consensus.legendText')}</span>
            <span className="inline-flex items-center gap-1"><span className="inline-block h-2.5 w-2.5" style={{ background: STRUCTURE_COLOR }} />{t('consensus.legendStructure')}</span>
            <span className="inline-flex items-center gap-1"><span className="inline-block h-2.5 w-2.5" style={{ background: CONVENTION_COLOR }} />{t('consensus.legendConvention')}</span>
            <span className="inline-flex items-center gap-1"><span className="inline-block h-2.5 w-2.5" style={{ background: BAND_COLOR.agree }} />{t('consensus.legendAgree')}</span>
            {actions && <span className="inline-flex items-center gap-1"><span className="inline-block h-2.5 w-2.5" style={{ background: RESOLVED_COLOR }} />{t('consensus.legendResolved')}</span>}
            <span>{t('consensus.legendDashed')}</span>
          </div>
          <details className="text-[11px]">
            <summary className="cursor-pointer text-[color:var(--color-ink-2)]">{t('consensus.moreOptions', { pct: pct(page.comparison.agreement) })}</summary>
            <div className="mt-2 space-y-2">
              <div role="group" aria-label={t('consensus.viewHint')} className="flex flex-wrap gap-1">
                {(['disagreement', ...page.comparison.models] as ConsensusView[]).map((key) => (
                  <button key={key} type="button" aria-pressed={view === key} className={`btn ${view === key ? 'btn-primary' : ''}`} onClick={() => onView(key)}>
                    {key === 'disagreement' ? t('consensus.viewDisagreement') : nameOf(key)}
                  </button>
                ))}
              </div>
              {actions?.canEdit && (
                <div className="flex flex-wrap items-center gap-1" data-testid="adopt">
                  <span className="text-[color:var(--color-ink-3)]">{t('consensus.adoptLead')}</span>
                  {page.comparison.models.map((key) => (
                    <button key={key} type="button" className="font-semibold underline" onClick={() => actions.onAdopt(key)}>{nameOf(key)}</button>
                  ))}
                </div>
              )}
            </div>
          </details>
        </>
      )}
    </section>
  )
}

/** The areas the models read where the user has no block, at the foot of the content. */
export function ModelOnlyClusters({ page, clusters, selected, onSelect, actions }: {
  page: PageConsensus
  clusters: ConsensusCluster[]
  selected: number | null
  onSelect: (cluster: number) => void
  actions?: ConsensusActions
}) {
  const { t } = useI18n()
  if (clusters.length === 0) return null
  const resolved = actions?.resolved ?? new Set<number>()
  return (
    <section className="border-t border-dashed border-[color:var(--color-rule-strong)] p-2.5" aria-label={t('consensus.modelOnly')} data-testid="model-only">
      <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[color:var(--color-ink-3)]">{t('consensus.modelOnly')}</h3>
      <ul className="space-y-2">
        {clusters.map((cluster) => (
          <li key={cluster.id} id={`consensus-area-${cluster.id}`} className={`border border-dashed border-[color:var(--color-rule-strong)] p-2 ${cluster.id === selected ? 'bg-[color:var(--color-sig-wash)]' : ''}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[12px] font-semibold">{labelName(cluster.label)}</span>
              <ClusterChip cluster={cluster} page={page} resolved={resolved.has(cluster.id)} selected={cluster.id === selected} onSelect={() => onSelect(cluster.id)} />
            </div>
            {cluster.id === selected && <ClusterDetail cluster={cluster} page={page} mine={[]} resolved={resolved.has(cluster.id)} actions={actions} />}
          </li>
        ))}
      </ul>
    </section>
  )
}

/** With the comparison off: this page has one, which models, and what is left in it. */
export function ConsensusOffer({ page, resolved, onOpen }: {
  page: PageConsensus
  resolved: Set<number>
  onOpen: () => void
}) {
  const { t, tn } = useI18n()
  if (!page.comparison) return null
  const counts = openCounts(page.comparison.clusters, resolved)
  const nameOf = (key: string) => page.models.find((m) => m.key === key)?.name ?? key
  return (
    <section className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 border border-[color:var(--color-rule-strong)] bg-[color:var(--color-fill)] p-2.5 text-[12px]" data-testid="consensus-offer">
      <span className="min-w-0 flex-1">
        <span className="font-semibold">{t('consensus.offer', { names: page.comparison.models.map(nameOf).join(' · ') })}</span>
        <span className="ml-2 text-[color:var(--color-ink-2)]">
          {counts.text + counts.missing + counts.structure === 0
            ? t('consensus.allSettled')
            : [
                counts.text > 0 ? tn('consensus.countText', counts.text) : '',
                counts.missing > 0 ? tn('consensus.countMissing', counts.missing) : '',
                counts.structure > 0 ? tn('consensus.countStructure', counts.structure) : '',
              ].filter(Boolean).join(' · ')}
        </span>
      </span>
      <button type="button" className="btn btn-primary" onClick={onOpen}>{t('consensus.offerOpen')}</button>
    </section>
  )
}
