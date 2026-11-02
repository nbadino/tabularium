/**
 * The pages of a project as thumbnails, tinted by state, each opening the Studio
 * on that page. Filters by state (and by "has drafts") and searches by name.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import type { PageItem } from '../lib/types'
import { statusLabel, STATUS_RANK, STATUS_TONE } from '../lib/vocab'
import { pageLabel, pageShortLabel } from '../lib/pageLabel'
import { API_BASE } from '../lib/endpoints'
import { useI18n } from '../i18n'
import { Badge, Field, Loading } from './ui'
import { projectPath } from './projectPaths'

/** Not a state: the pages carrying model drafts nobody has checked yet. */
const DRAFTS_FILTER = '__drafts'
const BATCH = 80
const SEARCH_FROM = 20

function PageCell({ page }: { page: PageItem }) {
  const { t } = useI18n()
  return (
    <li className="relative border-b border-r border-[color:var(--color-rule)] bg-[color:var(--color-sheet)]">
      <Link to={projectPath(page.project_id, 'studio', { page: page.id })} className="block text-[color:var(--color-ink)] no-underline hover:bg-[color:var(--color-fill)]">
        <div className="lighttable aspect-[3/4] overflow-hidden">
          <img data-fade
            src={`${API_BASE}/pages/${page.id}/thumbnail`}
            alt={t('home.thumbnailAlt', { path: page.rel_path })}
            loading="lazy"
            className="h-full w-full bg-white object-cover object-top"
          />
        </div>
        <div className="flex items-center gap-1.5 border-t border-[color:var(--color-rule)] px-1.5 py-1">
          <span className="mono min-w-0 flex-1 truncate text-[11px] text-[color:var(--color-ink-2)]" title={pageLabel(page)}>
            {pageShortLabel(page)}
          </span>
          {/* Recognized but never touched by a person is not "new": it has drafts to check. */}
          {page.status === 'new' && (page.drafts ?? 0) > 0 ? (
            <Badge tone="warn">{t('home.draftsBadge', { n: page.drafts ?? 0 })}</Badge>
          ) : (
            <Badge tone={STATUS_TONE[page.status] ?? 'neutral'}>{statusLabel(page.status)}</Badge>
          )}
        </div>
      </Link>
    </li>
  )
}

export function PageWall({ pages, loading }: { pages: PageItem[]; loading: boolean }) {
  const { t } = useI18n()
  const [filter, setFilter] = useState('')
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(BATCH)
  useEffect(() => setLimit(BATCH), [filter, query])

  const sorted = useMemo(
    () => [...pages].sort((a, b) => (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9) || a.rel_path.localeCompare(b.rel_path)),
    [pages],
  )
  const withDrafts = pages.filter((p) => (p.drafts ?? 0) > 0).length
  const counts = pages.reduce<Record<string, number>>((out, p) => ({ ...out, [p.status]: (out[p.status] ?? 0) + 1 }), {})
  const statuses = Object.keys(counts).sort((a, b) => (STATUS_RANK[a] ?? 9) - (STATUS_RANK[b] ?? 9))
  const q = query.trim().toLocaleLowerCase()
  const shown = sorted
    .filter((p) => (filter === DRAFTS_FILTER ? (p.drafts ?? 0) > 0 : filter ? p.status === filter : true))
    .filter((p) => !q || pageLabel(p).toLocaleLowerCase().includes(q))

  const chip = (active: boolean) => `btn btn-sm ${active ? '!border-[color:var(--color-ink)] !bg-[color:var(--color-ink)] !text-white' : ''}`

  if (loading) return <Loading label={t('common.loading')} />
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-end gap-3">
        {/* With one state there is nothing to filter. */}
        {statuses.length + (withDrafts > 0 ? 1 : 0) > 1 && (
          <div className="flex flex-wrap items-center gap-1">
            <button type="button" onClick={() => setFilter('')} aria-pressed={filter === ''} className={chip(filter === '')}>
              {t('home.all', { n: pages.length })}
            </button>
            {withDrafts > 0 && (
              <button type="button" onClick={() => setFilter(filter === DRAFTS_FILTER ? '' : DRAFTS_FILTER)} aria-pressed={filter === DRAFTS_FILTER} className={chip(filter === DRAFTS_FILTER)}>
                {t('home.withDrafts', { n: withDrafts })}
              </button>
            )}
            {statuses.map((s) => (
              <button key={s} type="button" onClick={() => setFilter(filter === s ? '' : s)} aria-pressed={filter === s} className={chip(filter === s)}>
                {statusLabel(s)} {counts[s]}
              </button>
            ))}
          </div>
        )}
        {pages.length > SEARCH_FROM && (
          <div className="ml-auto w-full max-w-xs">
            <Field label={t('recognition.filterPages')}>
              <input type="search" className="fld" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t('recognition.filterPagesHint')} />
            </Field>
          </div>
        )}
      </div>
      {shown.length > 0 ? (
        <ul className="cascade grid grid-cols-2 border-l border-t border-[color:var(--color-rule)] sm:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8">
          {shown.slice(0, limit).map((p) => <PageCell key={p.id} page={p} />)}
        </ul>
      ) : (
        <div className="flex flex-col items-start gap-2 py-6">
          <p className="text-[13px] font-semibold">{q ? t('recognition.noFilteredPages') : t('home.emptyNoPageInState', { state: filter === DRAFTS_FILTER ? t('home.withDrafts', { n: 0 }) : statusLabel(filter) })}</p>
          <button type="button" onClick={() => { setFilter(''); setQuery('') }} className="btn">{t('home.showAll')}</button>
        </div>
      )}
      {shown.length > limit && <button type="button" className="btn mt-3" onClick={() => setLimit((n) => n + BATCH)}>{t('recognition.showMorePages', { n: shown.length - limit })}</button>}
    </div>
  )
}
