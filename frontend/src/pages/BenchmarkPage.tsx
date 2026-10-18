/**
 * Benchmark modelli (M17) — pagina a sé, staccata dal Fine-tuning.
 *
 * Si scelgono **modelli** (target: adapter + endpoint + nome servito) e
 * **pagine** (con annotazione verificata: tutti i blocchi confermati). Il
 * riferimento è l'annotazione umana; il benchmark non scrive nel DB, produce
 * solo un report su disco.
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { apiGet, apiPost } from '../lib/api'
import type { BenchStatus, BenchmarkReport, BenchmarkTarget } from '../lib/types'
import type { ModelItem } from '../app/models/registry'
import { ErrorNotice, Field, Module, Notice, PageHeader, Badge } from '../app/ui'
import { useProjects, writeActiveProject } from '../app/activeProject'
import { useInference } from '../app/inference'
import { useModelNames } from '../app/models/names'
import { useI18n, tn } from '../i18n'

const num = (v: number | null | undefined, d = 3) => (v == null ? '—' : v.toFixed(d))
const pct = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(1)}%`)

interface BenchPage {
  page_id: number
  rel_path: string
  status: string
  blocks: number
  unconfirmed: number
  ready: boolean
}

export default function BenchmarkPage() {
  const { t } = useI18n()
  const inference = useInference()
  const nameOf = useModelNames()
  const [projectId, setProjectId] = useState<number | ''>('')
  const [adapters, setAdapters] = useState<ModelItem[]>([])
  const [targets, setTargets] = useState<BenchmarkTarget[]>([])
  const [pages, setPages] = useState<BenchPage[]>([])
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [limit, setLimit] = useState(10)
  const [withText, setWithText] = useState(true)
  const [tableOnly, setTableOnly] = useState(false)
  const [running, setRunning] = useState(false)
  const [report, setReport] = useState<BenchmarkReport | null>(null)
  const [status, setStatus] = useState<BenchStatus | null>(null)
  const [error, setError] = useState<unknown>(null)

  const onProject = (pid: number | '') => {
    setProjectId(pid)
    writeActiveProject(pid === '' ? null : pid)
    setReport(null)
    setPicked(new Set())
  }
  const projects = useProjects(onProject, setError)

  useEffect(() => {
    apiGet<{ items: ModelItem[] }>('/models').then((out) => setAdapters(out.items)).catch(() => {})
  }, [])

  useEffect(() => {
    setTargets((current) =>
      current.length > 0
        ? current
        : [{ adapter_id: inference.adapterId ?? '', url: inference.url, model: inference.model }],
    )
  }, [inference.adapterId, inference.url, inference.model])

  useEffect(() => {
    if (projectId === '') return
    apiGet<{ items: BenchPage[] }>(`/projects/${projectId}/benchmark/pages`)
      .then((out) => setPages(out.items))
      .catch(() => setPages([]))
  }, [projectId])

  const readyPages = pages.filter((p) => p.ready)

  const toggle = (id: number) =>
    setPicked((cur) => {
      const next = new Set(cur)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })

  const patch = (index: number, part: Partial<BenchmarkTarget>) =>
    setTargets((list) => list.map((x, i) => (i === index ? { ...x, ...part } : x)))

  const pollStatus = async () => {
    if (projectId === '') return
    try {
      const s = await apiGet<BenchStatus>(`/projects/${projectId}/benchmark/status`)
      setStatus(s)
      if (s.state === 'running') {
        setRunning(true)
      } else {
        setRunning(false)
        if (s.state === 'done') {
          const rep = await apiGet<BenchmarkReport>(`/projects/${projectId}/benchmark/latest`)
          if (rep.available) setReport(rep)
        } else if (s.state === 'error') {
          setError(s.error ?? 'benchmark')
        }
      }
    } catch (e) {
      setError(e)
      setRunning(false)
    }
  }

  // Il benchmark gira in background lato server: al refresh si ritrova lo stato
  // e, se è in corso, si continua a seguirlo.
  useEffect(() => {
    setReport(null)
    setStatus(null)
    void pollStatus()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  useEffect(() => {
    if (projectId === '' || !running) return
    const id = window.setInterval(() => void pollStatus(), 2000)
    return () => window.clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, projectId])

  const run = async () => {
    if (projectId === '') return
    const clean = targets.filter((x) => x.adapter_id)
    if (clean.length === 0) return
    setError(null)
    setReport(null)
    try {
      await apiPost<{ started: boolean }>(`/projects/${projectId}/benchmark`, {
        targets: clean,
        page_ids: picked.size > 0 ? [...picked] : null,
        limit,
        with_text: withText,
        table_only: tableOnly,
      })
      setRunning(true)
      setStatus({ state: 'running' })
    } catch (e) {
      setError(e)
    }
  }

  return (
    <div className="p-3">
      <PageHeader title={t('benchmark.title')} intro={t('benchmark.intro')} />

      {error != null && <div className="mb-3"><ErrorNotice error={error} onDismiss={() => setError(null)} /></div>}

      <Module tab={t('benchmark.run')}>
        <Field label={t('benchmark.project')}>
          <select
            value={projectId}
            onChange={(e) => onProject(e.target.value === '' ? '' : Number(e.target.value))}
            className="fld"
          >
            <option value="">{t('common.chooseProject')}</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </Field>
      </Module>

      {projectId !== '' && (
        <>
          <div className="mb-3">
            <Module tab={t('benchmark.models')}>
              <div className="space-y-1">
                {adapters.map((m) => {
                  const ticked = targets.some((x) => x.adapter_id === m.adapter_id)
                  const local = Boolean(m.local?.runnable)
                  return (
                    <div key={m.adapter_id} className="border-b border-[color:var(--color-rule)] py-1.5">
                      <label className="flex items-center gap-2 text-[12px]">
                        <input
                          type="checkbox"
                          checked={ticked}
                          onChange={(e) => {
                            if (e.target.checked) {
                              const defModel = m.served_model_name || m.local?.mlx_repo || m.adapter_id
                              const defUrl = m.adapter_id === inference.adapterId
                                ? inference.url
                                : local ? 'http://127.0.0.1:8888/v1' : ''
                              setTargets((list) => [...list, { adapter_id: m.adapter_id, url: defUrl, model: defModel }])
                            } else {
                              setTargets((list) => list.filter((x) => x.adapter_id !== m.adapter_id))
                            }
                          }}
                        />
                        <span className="font-semibold">{m.display_name}</span>
                        <Badge tone={local ? 'ok' : 'neutral'}>{local ? t('benchmark.local') : t('benchmark.remote')}</Badge>
                        {local && <span className="text-[10px] text-[color:var(--color-ink-3)]">{m.local?.runtime ?? ''}</span>}
                      </label>
                      {ticked && (
                        <div className="mt-1 grid gap-2 pl-6 sm:grid-cols-2">
                          <input
                            className="fld fld-mono"
                            value={targets.find((x) => x.adapter_id === m.adapter_id)?.url ?? ''}
                            onChange={(e) => patch(targets.findIndex((x) => x.adapter_id === m.adapter_id), { url: e.target.value })}
                            placeholder="http://127.0.0.1:8888/v1"
                          />
                          <input
                            className="fld fld-mono"
                            value={targets.find((x) => x.adapter_id === m.adapter_id)?.model ?? ''}
                            onChange={(e) => patch(targets.findIndex((x) => x.adapter_id === m.adapter_id), { model: e.target.value })}
                            placeholder="MonkeyOCRv2"
                          />
                        </div>
                      )}
                    </div>
                  )
                })}
                {targets.some((x) => adapters.find((m) => m.adapter_id === x.adapter_id)?.local?.runnable) && (
                  <p className="pt-1 text-[11px] text-[color:var(--color-ink-3)]">{t('benchmark.localHint')}</p>
                )}
              </div>
            </Module>
          </div>

          <div className="mb-3">
            <Module
              tab={t('benchmark.pages')}
              aux={
                <span className="flex items-center gap-2 text-[11px]">
                  <span>{tn('benchmark.readyCount', readyPages.length)}</span>
                  <button type="button" className="btn btn-sm" onClick={() => setPicked(new Set(readyPages.map((p) => p.page_id)))}>
                    {t('benchmark.selectAll')}
                  </button>
                  <button type="button" className="btn btn-sm" onClick={() => setPicked(new Set())}>
                    {t('benchmark.selectNone')}
                  </button>
                </span>
              }
            >
              {pages.length === 0 ? (
                <Notice>{t('benchmark.noPages')}</Notice>
              ) : (
                <div className="max-h-72 overflow-y-auto">
                  {pages.map((p) => (
                    <label key={p.page_id} className={`flex items-center gap-2 border-b border-[color:var(--color-rule)] py-1 text-[12px] ${p.ready ? '' : 'opacity-50'}`}>
                      <input type="checkbox" disabled={!p.ready} checked={picked.has(p.page_id)} onChange={() => toggle(p.page_id)} />
                      <span className="mono min-w-0 flex-1 truncate">{p.rel_path}</span>
                      <span className="text-[10px] text-[color:var(--color-ink-3)]">
                        {p.ready ? `${p.blocks} ${t('benchmark.blocks')}` : `${p.unconfirmed} ${t('benchmark.unconfirmed')}`}
                      </span>
                    </label>
                  ))}
                </div>
              )}
              <div className="mt-3 flex flex-wrap items-end gap-3">
                <Field label={t('benchmark.pages')} hint={t('benchmark.limitHint')}>
                  <input type="number" min={1} max={200} className="fld !w-24" value={limit} onChange={(e) => setLimit(Math.max(1, Math.min(200, Number(e.target.value) || 1)))} />
                </Field>
                <label className="flex items-center gap-2 text-[12px]">
                  <input type="checkbox" checked={withText} onChange={(e) => setWithText(e.target.checked)} />
                  {t('benchmark.withText')}
                </label>
                <label className="flex items-center gap-2 text-[12px]" title={t('benchmark.tableOnlyHint')}>
                  <input type="checkbox" checked={tableOnly} onChange={(e) => setTableOnly(e.target.checked)} />
                  {t('benchmark.tableOnly')}
                </label>
                <button type="button" className="btn btn-primary btn-sm" onClick={() => void run()} disabled={running}>
                  {running ? t('benchmark.running') : t('benchmark.run')}
                </button>
              </div>
              <p className="mt-2 max-w-[80ch] text-[11px] text-[color:var(--color-ink-3)]">
                {picked.size > 0
                  ? t('benchmark.selectedHint', { n: picked.size })
                  : t('benchmark.limitUsedHint', { n: limit })}
              </p>
              {running && (
                <div className="mt-2">
                  <Notice>
                    {t('benchmark.runningNow', {
                      model: status?.current_adapter ?? '…',
                      i: (status?.index ?? 0) + 1,
                      n: status?.total ?? '?',
                    })}
                    {status?.page_total ? ` · ${t('benchmark.pageProgress', { i: status.page_index ?? 0, n: status.page_total })}` : ''}
                  </Notice>
                </div>
              )}
              {status?.state === 'error' && status.error && (
                <div className="mt-2"><Notice tone="warn">{t('benchmark.failed', { error: status.error })}</Notice></div>
              )}
            </Module>
          </div>

          {(() => {
            const rows = report?.leaderboard ?? status?.results ?? []
            if (rows.length === 0) return null
            const gold = report?.gold_pages ?? status?.gold_pages
            return (
            <Module tab={t('benchmark.results')} aux={<span className="text-[11px]">{gold != null ? tn('benchmark.goldCount', gold) : ''}</span>}>
              <div className="overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead>
                    <tr className="border-b border-[color:var(--color-rule)] text-left">
                      <th className="py-1 pr-3">#</th>
                      <th className="py-1 pr-3">{t('benchmark.model')}</th>
                      <th className="py-1 pr-3">F1@0.75</th>
                      <th className="py-1 pr-3">F1@0.5</th>
                      <th className="py-1 pr-3">{t('benchmark.order')}</th>
                      <th className="py-1 pr-3">{t('benchmark.tables')}</th>
                      <th className="py-1 pr-3">TEDS</th>
                      <th className="py-1 pr-3">CER</th>
                      <th className="py-1">{t('benchmark.seconds')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((entry, i) => (
                      <tr key={`${entry.adapter_id}-${entry.model}`} className="border-b border-[color:var(--color-rule)]">
                        <td className="mono py-1 pr-3">{i + 1}</td>
                        <td className="py-1 pr-3">
                          <span className="font-semibold">{nameOf(entry.adapter_id) ?? entry.adapter_id}</span>
                          <span className="mono block text-[10px] text-[color:var(--color-ink-3)]">{entry.model}</span>
                        </td>
                        <td className="mono py-1 pr-3">{num(entry.aggregates.layout_strict?.f1)}</td>
                        <td className="mono py-1 pr-3">{num(entry.aggregates.layout.f1)}</td>
                        <td className="mono py-1 pr-3">{num(entry.aggregates.order.mean_levenshtein_norm)}</td>
                        <td className="mono py-1 pr-3">{pct(entry.aggregates.tables.structure_ok_pct)}</td>
                        <td className="mono py-1 pr-3">{num(entry.aggregates.tables.mean_teds)}</td>
                        <td className="mono py-1 pr-3">{num(entry.aggregates.text.mean_cer)}</td>
                        <td className="mono py-1">{entry.seconds}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-[11px] text-[color:var(--color-ink-3)]">{t('benchmark.rankHint')}</p>
            </Module>
            )
          })()}
        </>
      )}

      {projectId === '' && (
        <p className="text-[12px] text-[color:var(--color-ink-2)]">
          {t('benchmark.pickProject')} <Link to="/progetti" className="font-semibold underline underline-offset-2">{t('nav.projects')}</Link>
        </p>
      )}
    </div>
  )
}
