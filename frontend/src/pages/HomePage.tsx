/**
 * La home è l'archivio.
 *
 * Non una dashboard di sysinfo: la prima cosa che si vede è il muro delle
 * pagine registrate, tinte per stato, con sopra l'unico compito che conta
 * adesso. Identità e onboarding risolti dalla stessa superficie — chi apre
 * l'app per la prima volta vede cosa fare, chi la apre ogni giorno vede a
 * che punto è il corpus.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { apiGet } from '../lib/api'
import type { PageItem, Project } from '../lib/types'
import { statusLabel, STATUS_RANK, STATUS_TONE } from '../lib/vocab'
import { Badge, ErrorNotice, Module } from '../app/ui'
import { pickActive, writeActiveProject } from '../app/activeProject'
import { IconArchive } from '../app/icons'
import { useI18n } from '../i18n'

/** Una pagina del muro: anteprima ritagliata dall'alto, dove sta la testata. */
function PageCell({ page }: { page: PageItem }) {
  const { t } = useI18n()
  return (
    <li className="relative border-b border-r border-[color:var(--color-rule)] bg-[color:var(--color-sheet)]">
      <Link to={`/annotazione?page=${page.id}`} className="block text-[color:var(--color-ink)] no-underline hover:bg-[color:var(--color-fill)]">
        <div className="lighttable aspect-[3/4] overflow-hidden">
          <img
            src={`/api/pages/${page.id}/thumbnail`}
            alt={t('home.thumbnailAlt', { path: page.rel_path })}
            loading="lazy"
            className="h-full w-full bg-white object-cover object-top"
          />
        </div>
        <div className="flex items-center gap-1.5 border-t border-[color:var(--color-rule)] px-1.5 py-1">
          <span
            className="mono min-w-0 flex-1 truncate text-[11px] text-[color:var(--color-ink-2)]"
            title={page.rel_path}
          >
            {page.rel_path}
          </span>
          <Badge tone={STATUS_TONE[page.status] ?? 'neutral'}>{statusLabel(page.status)}</Badge>
        </div>
      </Link>
    </li>
  )
}

export default function HomePage() {
  const { t } = useI18n()
  const [projects, setProjects] = useState<Project[]>([])
  const [projectId, setProjectId] = useState<number | null>(null)
  const [pages, setPages] = useState<PageItem[]>([])
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<string>('')

  useEffect(() => {
    let alive = true
    apiGet<{ items: Project[] }>('/projects')
      .then((r) => {
        if (!alive) return
        setProjects(r.items)
        setProjectId(pickActive(r.items))
      })
      .catch((e) => alive && setError(e))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    if (projectId == null) return
    writeActiveProject(projectId)
    let alive = true
    setPages([])
    apiGet<{ items: PageItem[] }>(`/projects/${projectId}/pages`)
      .then((r) => alive && setPages(r.items))
      .catch((e) => alive && setError(e))
    return () => {
      alive = false
    }
  }, [projectId])

  const project = projects.find((p) => p.id === projectId) ?? null
  // Il muro si ordina per avanzamento: il lavoro da fare viene per primo.
  const sorted = useMemo(
    () =>
      [...pages].sort(
        (a, b) =>
          (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9) ||
          a.rel_path.localeCompare(b.rel_path),
      ),
    [pages],
  )
  const shown = filter ? sorted.filter((p) => p.status === filter) : sorted

  const counts = pages.reduce<Record<string, number>>((out, page) => {
    out[page.status] = (out[page.status] ?? 0) + 1
    return out
  }, {})
  const worked = pages.filter((page) => page.status !== 'new').length
  const progress = pages.length ? worked / pages.length : 0
  const statuses = Object.keys(counts).sort(
    (a, b) => (STATUS_RANK[a] ?? 9) - (STATUS_RANK[b] ?? 9),
  )

  return (
    <div className="p-3">
      {error != null && (
        <div className="mb-3">
          <ErrorNotice error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {/* --- testata: il corpus, a scala che si legge dall'altra parte della stanza */}
      <div className="mb-3 flex flex-wrap items-end gap-x-4 gap-y-2 border-b border-[color:var(--color-rule-strong)] pb-3">
        <div className="min-w-0">
          <span className="lbl !mb-0.5">{t('home.activeArchive')}</span>
          {projects.length > 1 ? (
            <select
              value={projectId ?? ''}
              onChange={(e) => setProjectId(Number(e.target.value))}
              aria-label={t('home.activeArchive')}
              className="block max-w-full border-0 bg-transparent p-0 text-[34px] font-bold leading-[1.05] tracking-[-0.035em] focus:outline-none focus-visible:outline-2 focus-visible:outline-[color:var(--color-sig)]"
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          ) : (
            <h1 className="truncate text-[34px] font-bold leading-[1.05] tracking-[-0.035em]">
              {project?.name ?? (loading ? t('home.loadingHeader') : t('home.noArchive'))}
            </h1>
          )}
          {project?.archive_dir && (
            <p className="mono mt-1 truncate text-[11px] text-[color:var(--color-ink-3)]">
              {project.archive_dir}
            </p>
          )}
        </div>

        {pages.length > 0 && (
          <div className="ml-auto flex items-end gap-4">
            <div>
              <span className="lbl !mb-0">{t('home.pages')}</span>
              <span className="mono text-[22px] font-semibold leading-none">
                {pages.length}
              </span>
            </div>
            <div>
              <span className="lbl !mb-0">{t('home.worked')}</span>
              <span className="mono text-[22px] font-semibold leading-none">
                {worked}
                <span className="text-[13px] font-normal text-[color:var(--color-ink-3)]">
                  {' '}
                  / {pages.length}
                </span>
              </span>
            </div>
            {/* Avanzamento come barra rigata, non come anello decorativo. */}
            <div className="w-40">
              <span className="lbl !mb-0.5">
                {t('home.progress', { pct: (progress * 100).toFixed(0) })}
              </span>
              <div
                className="h-2 border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sheet)]"
                role="progressbar"
                aria-valuenow={Math.round(progress * 100)}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label={t('home.progressLabel')}
              >
                <div
                  className="h-full bg-[color:var(--color-sig)]"
                  style={{ width: `${progress * 100}%` }}
                />
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="mb-3 flex flex-wrap gap-2">
        <Link to="/" className="btn btn-primary no-underline">{t('home.recognizePages')}</Link>
        <Link to={projectId == null ? '/progetti' : `/progetti/${projectId}`} className="btn no-underline">{t('home.manageArchive')}</Link>
      </div>

      {/* --- il muro ---------------------------------------------------------- */}
      <Module
        tab={t('home.wall')}
        quiet
        flush
        aux={
          statuses.length > 0 && (
            <div className="flex flex-wrap items-center gap-1">
              <button
                type="button"
                onClick={() => setFilter('')}
                aria-pressed={filter === ''}
                className={`btn btn-sm ${filter === '' ? '!border-[color:var(--color-ink)] !bg-[color:var(--color-ink)] !text-white' : ''}`}
              >
                {t('home.all', { n: pages.length })}
              </button>
              {statuses.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setFilter(filter === s ? '' : s)}
                  aria-pressed={filter === s}
                  className={`btn btn-sm ${filter === s ? '!border-[color:var(--color-ink)] !bg-[color:var(--color-ink)] !text-white' : ''}`}
                >
                  {statusLabel(s)} {counts[s]}
                </button>
              ))}
            </div>
          )
        }
      >
        {shown.length > 0 ? (
          <ul className="grid grid-cols-2 border-l border-t border-[color:var(--color-rule)] sm:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8">
            {shown.map((p) => (
              <PageCell key={p.id} page={p} />
            ))}
          </ul>
        ) : (
          <div className="flex flex-col items-start gap-2 p-6">
            <IconArchive size={22} />
            <p className="text-[13px] font-semibold">
              {loading
                ? t('home.emptyLoading')
                : projects.length === 0
                  ? t('home.emptyNoArchive')
                  : filter
                    ? t('home.emptyNoPageInState', { state: statusLabel(filter) })
                    : t('home.emptyNoPagesYet')}
            </p>
            <p className="max-w-[60ch] text-[12px] text-[color:var(--color-ink-2)]">
              {projects.length === 0
                ? t('home.emptyNoArchiveBody')
                : filter
                  ? t('home.emptyFilterBody')
                  : t('home.emptyNoPagesBody')}
            </p>
            {filter ? (
              <button type="button" onClick={() => setFilter('')} className="btn">
                {t('home.showAll')}
              </button>
            ) : (
              <Link to={projects.length === 0 ? '/progetti' : `/progetti/${projectId}`} className="btn btn-primary no-underline">
                {projects.length === 0 ? t('home.createArchive') : t('home.manageArchive')}
              </Link>
            )}
          </div>
        )}
      </Module>
    </div>
  )
}
