import { useEffect, useRef, useState } from 'react'
import { apiGet, apiPost } from '../lib/api'
import type { DatasetReport, DatasetStatus } from '../lib/types'
import { familyLabel, lines as linesPlural, splitStrategyLabel } from '../lib/vocab'
import { Collapsible, ErrorNotice, Field, Module, WarnNotice } from '../app/ui'
import { PipelineStrip } from '../app/Pipeline'
import { buildPipeline, usePipelineState } from '../app/pipeline'
import { useProjects, writeActiveProject } from '../app/activeProject'
import { IconDataset, IconPlus } from '../app/icons'
import { useI18n, tn } from '../i18n'

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} kB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

export default function DatasetPage() {
  const { t } = useI18n()
  const [projectId, setProjectId] = useState<number | ''>('')
  const [ratio, setRatio] = useState(0.9)
  const [seed, setSeed] = useState(42)
  const [splitStrategy, setSplitStrategy] = useState('page')
  const [approvedOnly, setApprovedOnly] = useState(true)
  const [pilotOnly, setPilotOnly] = useState(false)
  const [adapterId, setAdapterId] = useState('monkeyocrv2-parsing')
  const [adapters, setAdapters] = useState<Array<{ adapter_id: string; display_name: string; tasks: string[]; training_types: string[]; export_ready?: boolean }>>([])
  const [building, setBuilding] = useState(false)
  const [status, setStatus] = useState<DatasetStatus>({ built: false, report: null })
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    apiGet<{ items: Array<{ adapter_id: string; display_name: string; tasks: string[]; training_types: string[]; export_ready?: boolean }> }>('/system/model-adapters')
      // Solo adapter con export davvero supportato (`supports_export`, sondato
      // lato backend come le modalità prefill): il builder chiama `prompt_for`
      // per ogni famiglia, quindi un adapter stub o senza prompt per una
      // famiglia (dots.ocr, PaddleOCR-VL, gli stub di sola ricetta) produrrebbe
      // un build che finisce sempre in NotImplementedError — non va offerto.
      .then((r) => setAdapters(r.items.filter((a) => a.export_ready !== false)))
      .catch(() => setAdapters([]))
  }, [])

  const onProject = async (pid: number | '') => {
    setProjectId(pid)
    writeActiveProject(pid === '' ? null : pid)
    setStatus({ built: false, report: null })
    resetBuildArm()
    if (pid === '') return
    try {
      setStatus(await apiGet<DatasetStatus>(`/projects/${pid}/datasets`))
    } catch (e) {
      setError(e)
    }
  }

  // conferma a due passi quando un export esiste già: il rebuild lo sovrascrive
  const [buildArmed, setBuildArmed] = useState(false)
  const buildArmTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const resetBuildArm = () => {
    setBuildArmed(false)
    if (buildArmTimer.current) clearTimeout(buildArmTimer.current)
  }

  useEffect(() => () => resetBuildArm(), [])

  const projects = useProjects((pid) => void onProject(pid), setError)

  const build = async () => {
    if (projectId === '') return
    if (status.built && !buildArmed) {
      setBuildArmed(true)
      if (buildArmTimer.current) clearTimeout(buildArmTimer.current)
      buildArmTimer.current = setTimeout(() => setBuildArmed(false), 5000)
      return
    }
    resetBuildArm()
    setBuilding(true)
    setError(null)
    try {
      const rep = await apiPost<DatasetReport>(`/projects/${projectId}/datasets/build`, {
        split_ratio: ratio,
        seed,
        split_strategy: splitStrategy,
        approved_only: approvedOnly,
        pilot_only: pilotOnly,
        adapter_id: adapterId,
      })
      setStatus({ built: true, report: rep })
    } catch (e) {
      setError(e)
    } finally {
      setBuilding(false)
    }
  }

  const project = projects.find((p) => p.id === projectId) ?? null
  const { workflow, training } = usePipelineState(projectId === '' ? null : projectId)
  const stages = buildPipeline({ project, workflow, dataset: status, training })

  const report = status.report

  return (
    <div className="p-3">
      <div className="mb-3 border-b border-[color:var(--color-rule-strong)] pb-3">
        <h1 className="text-[26px] font-bold leading-tight tracking-[-0.03em]">
          {t('dataset.title')}
        </h1>
        <p className="mt-1 max-w-[80ch] text-[13px] text-[color:var(--color-ink-2)]">
          {t('dataset.intro')}
        </p>
      </div>

      {error != null && (
        <div className="mb-3">
          <ErrorNotice error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {projectId !== '' && <PipelineStrip stages={stages} here="dataset" />}

      <div className="mb-3">
        <Module tab={t('dataset.build')}>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Field label={t('dataset.project')}>
              <select
                value={projectId}
                onChange={(e) => void onProject(e.target.value === '' ? '' : Number(e.target.value))}
                className="fld"
              >
                <option value="">{t('common.chooseProject')}</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              label={t('dataset.trainShare', { pct: (ratio * 100).toFixed(0) })}
              hint={t('dataset.trainShareHint', { pct: (100 - ratio * 100).toFixed(0) })}
            >
              <input
                type="range"
                min={0.5}
                max={0.95}
                step={0.05}
                value={ratio}
                onChange={(e) => setRatio(Number(e.target.value))}
                aria-label={t('dataset.trainShareLabel')}
                className="mt-1.5 w-full"
              />
            </Field>
            <Field
              label={t('dataset.splitUnit')}
              hint={t('dataset.splitUnitHint')}
            >
              <select
                value={splitStrategy}
                onChange={(e) => setSplitStrategy(e.target.value)}
                className="fld"
              >
                {['page', 'issue', 'year', 'source', 'scanner', 'collection', 'page_type'].map((k) => (
                  <option key={k} value={k}>
                    {splitStrategyLabel(k)}
                  </option>
                ))}
              </select>
            </Field>
            <label className="flex items-center gap-2 self-end pb-2 text-[12px]">
              <input type="checkbox" checked={approvedOnly} onChange={(e) => setApprovedOnly(e.target.checked)} />
              {t('dataset.approvedOnly')}
            </label>
          </div>

          {/* Seed e adapter si decidono una volta per corpus: stanno sotto. */}
          <div className="mt-3">
            <Collapsible tab={t('dataset.advanced')} quiet aux={<span>{t('dataset.advancedCount')}</span>}>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                <Field label={t('dataset.seed')} hint={t('dataset.seedHint')}>
                  <input
                    type="number"
                    value={seed}
                    onChange={(e) => setSeed(Number(e.target.value))}
                    className="fld fld-mono"
                  />
                </Field>
                <Field label={t('dataset.adapter')} hint={t('dataset.adapterHint')}>
                  <select value={adapterId} onChange={(e) => setAdapterId(e.target.value)} className="fld">
                    {(adapters.length ? adapters : [{ adapter_id: 'monkeyocrv2-parsing', display_name: 'MonkeyOCRv2 Parsing', tasks: [], training_types: [] }]).map((a) => <option key={a.adapter_id} value={a.adapter_id}>{a.display_name}</option>)}
                  </select>
                </Field>
                <label className="flex items-center gap-2 self-end pb-2 text-[12px]">
                  <input type="checkbox" checked={pilotOnly} onChange={(e) => setPilotOnly(e.target.checked)} />
                  {t('dataset.pilotOnly')}
                </label>
              </div>
            </Collapsible>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              onClick={() => void build()}
              disabled={building || projectId === ''}
              className="btn btn-primary"
            >
              <IconPlus size={13} />
              {building
                ? t('dataset.building')
                : buildArmed
                  ? t('dataset.confirmOverwrite')
                  : t('dataset.buildBtn')}
            </button>
            {buildArmed && (
              <span className="text-[11px] text-[color:var(--color-sig-text)]">
                {t('dataset.overwriteNote')}
              </span>
            )}
          </div>
        </Module>
      </div>

      {report ? (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <Module tab={t('dataset.pagesWithBlocks')} quiet>
              <div className="mono text-[26px] font-semibold leading-none">
                {report.pages.with_blocks}
              </div>
              <p className="mt-1 text-[12px] text-[color:var(--color-ink-2)]">
                {t('dataset.trainVal', { train: report.pages.train, val: report.pages.val })}
              </p>
            </Module>
            <Module tab={t('dataset.cropsGenerated')} quiet>
              <div className="mono text-[26px] font-semibold leading-none">
                {report.crops_generated}
              </div>
              <p className="mt-1 text-[12px] text-[color:var(--color-ink-2)]">
                {t('dataset.splitSeed', { pct: (report.split.ratio * 100).toFixed(0), seed: report.split.seed })}
              </p>
            </Module>
            <Module tab={t('dataset.lastBuild')} quiet>
              <div className="mono text-[17px] font-semibold leading-tight">
                {report.built_at.slice(0, 16).replace('T', ' ')}
              </div>
              <p className="mt-1 text-[12px] text-[color:var(--color-ink-2)]">{t('dataset.utc')}</p>
            </Module>
          </div>

          <Module tab={t('dataset.perFamily')} quiet flush>
            <table className="w-full border-collapse text-[12px]">
              <thead className="border-b border-[color:var(--color-rule)] bg-[color:var(--color-fill)]">
                <tr>
                  <th className="px-2 py-1 text-left text-[11px] font-semibold uppercase tracking-[0.04em] text-[color:var(--color-ink-2)]">
                    {t('dataset.family')}
                  </th>
                  <th className="px-2 py-1 text-right text-[11px] font-semibold uppercase tracking-[0.04em] text-[color:var(--color-ink-2)]">
                    {t('dataset.train')}
                  </th>
                  <th className="px-2 py-1 text-right text-[11px] font-semibold uppercase tracking-[0.04em] text-[color:var(--color-ink-2)]">
                    {t('dataset.validation')}
                  </th>
                  <th className="px-2 py-1 text-right text-[11px] font-semibold uppercase tracking-[0.04em] text-[color:var(--color-ink-2)]">
                    {t('dataset.total')}
                  </th>
                </tr>
              </thead>
              <tbody className="ruled">
                {Object.entries(report.counts).map(([fam, c]) => (
                  <tr key={fam}>
                    <td className="px-2 py-1">{familyLabel(fam)}</td>
                    <td className="mono px-2 py-1 text-right">{c.train}</td>
                    <td className="mono px-2 py-1 text-right">{c.val}</td>
                    <td className="mono px-2 py-1 text-right font-semibold">{c.train + c.val}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Module>

          {report.warnings.length > 0 && (
            <WarnNotice title={tn('common.warningsCount', report.warnings.length)}>
              <ul className="list-inside list-disc space-y-0.5">
                {report.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
              <p className="mt-1.5">
                <b className="font-semibold">{t('errors.whatToDo')}</b>
                {t('dataset.warningsBody')}
              </p>
            </WarnNotice>
          )}

          <Module
            tab={t('dataset.generatedFiles')}
            quiet
            aux={<span className="mono truncate">{report.dataset_dir}</span>}
            flush
          >
            <ul className="ruled">
              {report.files.map((f) => (
                <li key={f.path} className="flex gap-3 px-2 py-1 text-[12px]">
                  <span className="mono min-w-0 flex-1 truncate" title={f.path}>
                    {f.path.split('/').pop()}
                  </span>
                  <span className="mono shrink-0 text-[color:var(--color-ink-2)]">
                    {linesPlural(f.lines)}
                  </span>
                  <span className="mono w-20 shrink-0 text-right text-[color:var(--color-ink-2)]">
                    {fmtSize(f.size)}
                  </span>
                </li>
              ))}
            </ul>
          </Module>

          {Object.entries(report.sample_lines)
            .filter(([, lines]) => lines.length > 0)
            .map(([fam, lines]) => (
              <Collapsible key={fam} tab={t('dataset.preview', { family: familyLabel(fam) })} quiet>
                <p className="mb-2 max-w-[80ch] text-[12px] text-[color:var(--color-ink-2)]">
                  {t('dataset.previewIntro')}
                </p>
                {lines.map((l, i) => (
                  <pre
                    key={i}
                    className="mono mb-2 overflow-x-auto border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2 text-[11px] leading-relaxed"
                  >
                    {l.length > 700 ? l.slice(0, 700) + '…' : l}
                  </pre>
                ))}
              </Collapsible>
            ))}
        </div>
      ) : (
        <Module tab={t('dataset.noExport')} quiet>
          <div className="flex flex-col items-start gap-2 py-4">
            <IconDataset size={22} />
            <p className="text-[13px] font-semibold">
              {projectId === ''
                ? t('dataset.noExportSelect')
                : t('dataset.noExportBuilt')}
            </p>
            <p className="max-w-[70ch] text-[12px] text-[color:var(--color-ink-2)]">
              {t('dataset.noExportBody')}
            </p>
          </div>
        </Module>
      )}
    </div>
  )
}
