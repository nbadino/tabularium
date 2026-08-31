import { Link } from 'react-router'
import type { PageItem, Project } from '../../lib/types'
import { statusLabel, STATUS_TONE } from '../../lib/vocab'
import { Badge, Field } from '../../app/ui'
import { IconNext, IconPrev } from '../../app/icons'
import { useI18n, tn } from '../../i18n'

interface PageSidebarProps {
  projects: Project[]
  projectId: number | ''
  pages: PageItem[]
  currentPage: PageItem | null
  onProjectChange: (pid: number | '') => void
  onPageSelect: (pid: number) => void
  /** Larghezza in px governata dallo splitter: se assente vale il default. */
  width?: number
}

export default function PageSidebar({
  projects,
  projectId,
  pages,
  currentPage,
  onProjectChange,
  onPageSelect,
  width,
}: PageSidebarProps) {
  const { t } = useI18n()
  const index = currentPage ? pages.findIndex((p) => p.id === currentPage.id) : -1
  const prev = index > 0 ? pages[index - 1] : null
  const next = index >= 0 && index < pages.length - 1 ? pages[index + 1] : null

  return (
    <aside
      className="flex shrink-0 flex-col bg-[color:var(--color-sheet)]"
      style={width !== undefined ? { width } : undefined}
    >
      <div className="border-b border-[color:var(--color-rule)] p-2">
        <Field label={t('sidebar.project')}>
          <select
            value={projectId}
            onChange={(e) => onProjectChange(e.target.value === '' ? '' : Number(e.target.value))}
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
        <Link
          to="/progetti"
          className="mt-1.5 inline-block text-[11px] font-semibold uppercase tracking-[0.04em] no-underline"
        >
          {t('sidebar.manageProjects')}
        </Link>
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
            {index >= 0 ? `${index + 1} / ${pages.length}` : tn('sidebar.pagesCount', pages.length)}
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

      <ul className="min-h-0 flex-1 overflow-y-auto">
        {projectId === '' && (
          <li className="p-2 text-[12px] text-[color:var(--color-ink-2)]">
            {t('sidebar.noProject')}
          </li>
        )}
        {projectId !== '' && pages.length === 0 && (
          <li className="p-2 text-[12px] text-[color:var(--color-ink-2)]">
            {t('sidebar.noPages')}
          </li>
        )}
        {pages.map((p) => {
          const on = currentPage?.id === p.id
          return (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => onPageSelect(p.id)}
                aria-current={on ? 'true' : undefined}
                className={`flex w-full gap-2 border-b border-[color:var(--color-rule)] p-1.5 text-left ${
                  on
                    ? 'bg-[color:var(--color-sig-wash)] outline outline-2 -outline-offset-2 outline-[color:var(--color-sig)]'
                    : 'hover:bg-[color:var(--color-fill)]'
                }`}
              >
                <img
                  src={`/api/pages/${p.id}/thumbnail`}
                  alt=""
                  loading="lazy"
                  className="h-14 w-11 shrink-0 border border-[color:var(--color-rule)] bg-white object-cover object-top"
                />
                <span className="flex min-w-0 flex-1 flex-col gap-1">
                  <span className="mono truncate text-[11px]" title={p.rel_path}>
                    {p.rel_path}
                  </span>
                  <Badge tone={STATUS_TONE[p.status] ?? 'neutral'}>{statusLabel(p.status)}</Badge>
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </aside>
  )
}