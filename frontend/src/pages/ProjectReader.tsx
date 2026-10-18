/** Consultazione di pagine, blocchi e tabelle: nessun editor e nessun autosave montato. */
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { apiGet } from '../lib/api'
import type { BlockListOut, BlockOut, PageItem, Project, TableGridOut } from '../lib/types'
import { pageLabel } from '../lib/pageLabel'
import { useI18n } from '../i18n'
import { Badge, ErrorNotice, Field, Loading, Module, PageHeader } from '../app/ui'
import { connectPage, disconnectPage, onAnnotationsChanged } from '../app/live'
import { writeActiveProject } from '../app/activeProject'

function TablePreview({ block }: { block: BlockOut }) {
  const { t } = useI18n()
  const [table, setTable] = useState<TableGridOut | null>(null)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    let alive = true
    apiGet<TableGridOut>(`/blocks/${block.id}/table`).then((out) => { if (alive) setTable(out) }).catch((e) => { if (alive) setError(e) })
    return () => { alive = false }
  }, [block.id, block.updated_at])
  if (error) return <ErrorNotice error={error} />
  if (!table) return <Loading label={t('common.loading')} />
  if (!table.grid) return <p className="whitespace-pre-wrap text-[13px]">{block.content}</p>
  const grid = table.grid
  return <div className="overflow-auto"><table className="w-full border-collapse text-[12px]" aria-label={t('team.tablePreview')}><tbody>
    {Array.from({ length: grid.rows }, (_, r) => <tr key={r}>
      {grid.cells.filter((c) => c.r === r).sort((a, b) => a.c - b.c).map((cell) => <td key={cell.c} rowSpan={cell.rowspan} colSpan={cell.colspan} className="whitespace-pre-wrap border border-[color:var(--color-rule)] px-2 py-1">{cell.text}</td>)}
    </tr>)}
  </tbody></table></div>
}

export default function ProjectReader({ project, projects }: { project: Project; projects: Project[] }) {
  const { t } = useI18n()
  const [params, setParams] = useSearchParams()
  const [pages, setPages] = useState<PageItem[] | null>(null)
  const [blocks, setBlocks] = useState<BlockOut[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const requested = Number(params.get('page')) || null
  const page = pages?.find((p) => p.id === requested) ?? (!requested ? pages?.[0] : null)

  useEffect(() => {
    let alive = true
    setPages(null); setError(null)
    apiGet<{ items: PageItem[] }>(`/projects/${project.id}/pages`).then((out) => { if (alive) setPages(out.items) }).catch((e) => { if (alive) setError(e) })
    return () => { alive = false }
  }, [project.id])

  useEffect(() => {
    setBlocks(null)
    if (!page) return
    let alive = true
    const reload = () => apiGet<BlockListOut>(`/pages/${page.id}/annotations`).then((out) => {
      if (alive) { setBlocks(out.items); setError(null) }
    }).catch((e) => { if (alive) setError(e) })
    void reload()
    connectPage(page.id)
    const off = onAnnotationsChanged((event) => { if (event.pageId === page.id) void reload() })
    return () => { alive = false; off(); disconnectPage() }
  }, [page?.id])

  return <div className="p-3">
    <PageHeader title={t('team.readerTitle')} intro={`${t('team.readerIntro')} ${t('team.readerNotice')}`} aux={<Badge tone="neutral">{t('share.roleViewer')}</Badge>} />
    <div className="my-3 grid gap-3 sm:grid-cols-2">
      <Field label={t('training.project')}><select className="fld" value={project.id} onChange={(e) => { writeActiveProject(Number(e.target.value)); setParams({ project: e.target.value }) }}>
        {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select></Field>
      <Field label={t('playground.page')}><select className="fld" value={page?.id ?? ''} disabled={!pages?.length} onChange={(e) => setParams({ project: String(project.id), page: e.target.value })}>
        {!page && <option value="">{t('playground.pickPage')}</option>}
        {pages?.map((p) => <option key={p.id} value={p.id}>{pageLabel(p)}</option>)}
      </select></Field>
    </div>
    <Link to={`/progetti/${project.id}`} className="btn mb-3 no-underline">{t('team.projectOverview')}</Link>
    {error != null && <ErrorNotice error={error} />}
    {!pages && !error && <Loading label={t('common.loading')} />}
    {pages && !page && <p className="p-3 text-[13px]">{t(requested ? 'team.pageUnavailable' : 'project.emptyTitle')}</p>}
    {page && <div className="grid items-start gap-3 lg:grid-cols-2">
      <Module tab={pageLabel(page)} quiet><img src={`/api/pages/${page.id}/preview`} alt={t('team.pagePreview', { name: pageLabel(page) })} className="w-full" /></Module>
      <Module tab={t('team.savedContent')} quiet>
        {!blocks && !error && <Loading label={t('common.loading')} />}
        {blocks?.length === 0 && <p className="text-[13px]">{t('team.noBlocks')}</p>}
        <ol className="divide-y divide-[color:var(--color-rule)]">{blocks?.slice().sort((a, b) => (a.order_idx ?? 1e9) - (b.order_idx ?? 1e9)).map((block, i) => <li key={block.id} className="py-3">
          <div className="mb-2 flex flex-wrap items-center gap-2"><span className="font-semibold">{i + 1} · {block.label}</span><Badge tone={block.confirmed ? 'ok' : 'warn'}>{t(block.confirmed ? 'team.verified' : 'team.draft')}</Badge></div>
          {block.label === 'Table' ? <TablePreview block={block} /> : <p className="whitespace-pre-wrap text-[13px] leading-relaxed">{block.content}</p>}
        </li>)}</ol>
      </Module>
    </div>}
  </div>
}
