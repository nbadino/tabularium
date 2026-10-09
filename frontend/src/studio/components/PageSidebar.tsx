import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import type { PageItem, PageSums, Project } from '../../lib/types'
import { statusLabel, STATUS_TONE } from '../../lib/vocab'
import { Badge, Field } from '../../app/ui'
import { IconNext, IconPrev } from '../../app/icons'
import { t as translate, useI18n, tn } from '../../i18n'
import { pageLabel, pageShortLabel } from '../../lib/pageLabel'

interface PageSidebarProps {
  projects: Project[]
  projectId: number | ''
  pages: PageItem[]
  currentPage: PageItem | null
  onProjectChange: (pid: number | '') => void
  onPageSelect: (pid: number) => void
  /** Larghezza in px governata dallo splitter: se assente vale il default. */
  width?: number
  reviewScope?: { label: string; backTo: string; backLabel: string } | null
  /** Controlli aritmetici per pagina: dove le somme non tornano. */
  sums?: PageSums
  /** Le pagine stanno arrivando: si mostra la loro forma, non «nessuna pagina». */
  loading?: boolean
}

const INITIAL_PAGE_LIMIT = 100
const SEARCH_FROM = 20

/** L'etichetta dice **di che somme** si parla: una tavola per paese ha somme
 *  lungo le righe (Merchandise + Treasure = Total) e lungo le colonne (le voci
 *  che sommano al «Total»); un'altra può averne di un tipo solo. */
function sumsLabel({ failed, rows, cols }: { failed: number; rows: number; cols: number }) {
  const which = rows && cols ? 'Both' : rows ? 'Row' : 'Col'
  return failed > 0 ? tn(`sidebar.sumsFailed${which}`, failed) : translate(`sidebar.sumsOk${which}`)
}

export default function PageSidebar({
  projects,
  projectId,
  pages,
  currentPage,
  onProjectChange,
  onPageSelect,
  width,
  reviewScope,
  sums,
  loading,
}: PageSidebarProps) {
  const { t } = useI18n()
  const [pageQuery, setPageQuery] = useState('')
  const [pageLimit, setPageLimit] = useState(INITIAL_PAGE_LIMIT)
  const needle = pageQuery.trim().toLocaleLowerCase()
  const visiblePages = needle
    ? pages.filter((page) => pageLabel(page).toLocaleLowerCase().includes(needle))
    : pages
  const index = currentPage ? visiblePages.findIndex((p) => p.id === currentPage.id) : -1
  const prev = index > 0 ? visiblePages[index - 1] : null
  const next = index >= 0
    ? index < visiblePages.length - 1 ? visiblePages[index + 1] : null
    : visiblePages[0] ?? null
  const renderedPages = (() => {
    const initial = visiblePages.slice(0, pageLimit)
    if (!currentPage || initial.some((page) => page.id === currentPage.id) || !visiblePages.some((page) => page.id === currentPage.id)) {
      return initial
    }
    return [...initial.slice(0, Math.max(0, pageLimit - 1)), currentPage]
  })()

  useEffect(() => {
    setPageQuery('')
    setPageLimit(INITIAL_PAGE_LIMIT)
  }, [projectId])

  // La pagina aperta deve stare in vista, al suo posto nell'elenco: aperta da
  // un link o dopo un refresh poteva essere la 359ª di 361, fuori dalle prime
  // cento e quindi introvabile nella colonna.
  const listRef = useRef<HTMLUListElement>(null)
  useEffect(() => {
    if (index >= pageLimit) {
      setPageLimit(Math.ceil((index + 1) / INITIAL_PAGE_LIMIT) * INITIAL_PAGE_LIMIT)
      return
    }
    listRef.current?.querySelector<HTMLElement>('[aria-current="true"]')?.scrollIntoView?.({ block: 'nearest' })
  }, [currentPage?.id, index, pageLimit])

  return (
    <aside
      className="flex shrink-0 flex-col bg-[color:var(--color-sheet)]"
      style={width !== undefined ? { width } : undefined}
    >
      <div className="border-b border-[color:var(--color-rule)] p-2">
        {reviewScope && (
          <div className="mb-2 rounded-md border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2">
            <div className="text-[12px] font-semibold">{reviewScope.label}</div>
            <Link to={reviewScope.backTo} className="mt-1 inline-block text-[11px] font-semibold no-underline">
              {reviewScope.backLabel}
            </Link>
          </div>
        )}
        <div className="flex items-baseline justify-between">
          <span className="lbl">{t('sidebar.project')}</span>
          {!reviewScope && (
            <Link to="/progetti" className="text-[11px] text-[color:var(--color-ink-2)]">
              {t('sidebar.manageProjects')}
            </Link>
          )}
        </div>
        <select
            value={projectId}
            onChange={(e) => onProjectChange(e.target.value === '' ? '' : Number(e.target.value))}
            disabled={Boolean(reviewScope)}
            className="fld"
          >
            <option value="">{t('common.chooseProject')}</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        {/* Su poche pagine la lista si scorre a occhio: la ricerca serve
            quando l'archivio cresce. */}
        {pages.length > SEARCH_FROM && (
          <Field label={t('sidebar.pageSearch')}>
            <input
              value={pageQuery}
              onChange={(event) => {
                setPageQuery(event.target.value)
                setPageLimit(INITIAL_PAGE_LIMIT)
              }}
              placeholder={t('sidebar.pageSearchPlaceholder')}
              className="fld"
            />
          </Field>
        )}
      </div>

      {/* Scorrere l'archivio senza tornare all'elenco: il compito di Alex. */}
      {pages.length > 0 && (
        <div className="flex items-center gap-1 border-b border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-2 py-1">
          <button
            type="button"
            onClick={() => prev && onPageSelect(prev.id)}
            disabled={!prev}
            className="btn btn-sm"
            aria-label={t('sidebar.prevPage')}
            title={t('sidebar.prevPageTitle')}
          >
            <IconPrev size={11} />
          </button>
          <span className="mono flex-1 text-center text-[11px] text-[color:var(--color-ink-2)]">
            {index >= 0
              ? `${index + 1} / ${visiblePages.length}`
              : needle
                ? `— / ${visiblePages.length}`
                : tn('sidebar.pagesCount', pages.length)}
          </span>
          <button
            type="button"
            onClick={() => next && onPageSelect(next.id)}
            disabled={!next}
            className="btn btn-sm"
            aria-label={t('sidebar.nextPage')}
            title={t('sidebar.nextPageTitle')}
          >
            <IconNext size={11} />
          </button>
        </div>
      )}

      <ul ref={listRef} className="cascade min-h-0 flex-1 overflow-y-auto">
        {projectId === '' && (
          <li className="p-2 text-[12px] text-[color:var(--color-ink-2)]">
            {t('sidebar.noProject')}
          </li>
        )}
        {projectId !== '' && pages.length === 0 && loading &&
          Array.from({ length: 6 }, (_, i) => (
            <li key={`skeleton-${i}`} aria-hidden className="flex gap-2 border-b border-[color:var(--color-rule)] p-1.5">
              <span className="load-cell block h-14 w-11 shrink-0 rounded-sm" style={{ ['--load-delay' as string]: `${i * 80}ms` }} />
              <span className="flex flex-1 flex-col gap-1.5 pt-1">
                <span className="load-cell block h-2.5 w-10 rounded-sm" />
                <span className="load-cell block h-3.5 w-20 rounded-full" />
              </span>
            </li>
          ))}
        {projectId !== '' && pages.length === 0 && !loading && (
          <li className="p-2 text-[12px] text-[color:var(--color-ink-2)]">
            {t('sidebar.noPages')}
          </li>
        )}
        {projectId !== '' && pages.length > 0 && visiblePages.length === 0 && (
          <li className="p-2 text-[12px] text-[color:var(--color-ink-2)]">
            {t('sidebar.noPageMatches')}
          </li>
        )}
        {renderedPages.map((p) => {
          const on = currentPage?.id === p.id
          return (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => onPageSelect(p.id)}
                aria-current={on ? 'true' : undefined}
                className={`flex w-full gap-2 border-b border-[color:var(--color-rule)] p-1.5 text-left ${
                  on
                    ? 'rounded-md bg-[color:var(--color-sig-wash)] outline outline-2 -outline-offset-2 outline-[color:var(--color-sig)]'
                    : 'hover:bg-[color:var(--color-fill)]'
                }`}
              >
                <img data-fade
                  src={`/api/pages/${p.id}/thumbnail`}
                  alt=""
                  loading="lazy"
                  className="h-14 w-11 shrink-0 rounded-sm border border-[color:var(--color-rule)] bg-white object-cover object-top"
                />
                <span className="flex min-w-0 flex-1 flex-col items-start gap-1">
                  <span className="mono truncate text-[11px]" title={pageLabel(p)}>
                    {pageShortLabel(p)}
                  </span>
                  {p.status === 'new' && (p.drafts ?? 0) > 0 ? (
                    <Badge tone="warn">{t('home.draftsBadge', { n: p.drafts ?? 0 })}</Badge>
                  ) : (
                    <Badge tone={STATUS_TONE[p.status] ?? 'neutral'}>{statusLabel(p.status)}</Badge>
                  )}
                  {/* Le somme si segnalano quando non tornano; quando tornano
                      basta un segno quieto, con il conto nel titolo. */}
                  {sums?.[p.id] && (sums[p.id].failed > 0 ? (
                    <span title={t('sidebar.sumsFailedTitle', sums[p.id])}>
                      <Badge tone="warn">{sumsLabel(sums[p.id])}</Badge>
                    </span>
                  ) : (
                    <span
                      className="text-[11px] text-[color:var(--color-ok)]"
                      title={t('sidebar.sumsOkTitle', sums[p.id])}
                      aria-label={sumsLabel(sums[p.id])}
                    >
                      ✓
                    </span>
                  ))}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
      {renderedPages.length < visiblePages.length && (
        <div className="border-t border-[color:var(--color-rule)] p-2">
          <button
            type="button"
            className="btn btn-sm w-full"
            onClick={() => setPageLimit((before) => before + INITIAL_PAGE_LIMIT)}
          >
            {t('sidebar.showMorePages', { n: visiblePages.length - renderedPages.length })}
          </button>
        </div>
      )}
    </aside>
  )
}
