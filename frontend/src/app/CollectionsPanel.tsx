/**
 * The project's collections as a context column next to the pages (docs/COLLECTIONS.md):
 * each row filters the wall and the table, says how many pages it holds by state, and
 * accepts pages dragged onto it. Rename and delete sit in the row's menu.
 */
import { useState } from 'react'
import type { DragEvent, ReactNode } from 'react'
import type { CollectionCounts, CollectionItem, CollectionsOverview } from '../lib/types'
import { useI18n, tn } from '../i18n'
import { Menu } from './ui'
import { PAGES_MIME } from './PageWall'
import { UNASSIGNED, verifiedCount } from './collections'
import type { CollectionFilter } from './collections'

function stateLine(counts: CollectionCounts, t: (k: string, v?: Record<string, string | number>) => string): string {
  const verified = verifiedCount(counts.by_status)
  const drafts = counts.with_drafts
  const fresh = Math.max(0, (counts.by_status.new ?? 0) - drafts)
  const other = Math.max(0, counts.pages - verified - (counts.by_status.new ?? 0))
  const parts = [t('collections.stateNew', { n: fresh })]
  if (drafts) parts.push(t('collections.stateDrafts', { n: drafts }))
  if (other) parts.push(t('collections.stateWorking', { n: other }))
  parts.push(t('collections.stateVerified', { n: verified }))
  return parts.join(' · ')
}

export default function CollectionsPanel({
  overview,
  active,
  canEdit,
  onSelect,
  onCreate,
  onRename,
  onDelete,
  onDropPages,
}: {
  overview: CollectionsOverview
  active: CollectionFilter
  canEdit: boolean
  onSelect: (filter: CollectionFilter) => void
  onCreate: () => void
  onRename: (collection: CollectionItem) => void
  onDelete: (collection: CollectionItem) => void
  onDropPages: (pageIds: number[], target: number | null) => void
}) {
  const { t } = useI18n()
  const [over, setOver] = useState<string | null>(null)

  const dropProps = (key: string, target: number | null) => canEdit ? {
    onDragOver: (e: DragEvent) => {
      if (!e.dataTransfer.types.includes(PAGES_MIME)) return
      e.preventDefault()
      e.stopPropagation()
      setOver(key)
    },
    onDragLeave: () => setOver((cur) => (cur === key ? null : cur)),
    onDrop: (e: DragEvent) => {
      const raw = e.dataTransfer.getData(PAGES_MIME)
      if (!raw) return
      e.preventDefault()
      e.stopPropagation()
      setOver(null)
      try {
        const ids = (JSON.parse(raw) as unknown[]).map(Number).filter(Number.isInteger)
        if (ids.length) onDropPages(ids, target)
      } catch { /* not ours */ }
    },
  } : {}

  const row = (key: string, filter: CollectionFilter, label: string, count: number, detail: string | null, menu?: ReactNode, target?: number | null) => {
    const isActive = active === filter
    return (
      <li
        key={key}
        className="group relative border-b border-[color:var(--color-rule)] last:border-b-0 data-[active]:bg-[color:var(--color-sig-wash)] data-[over]:outline data-[over]:outline-1 data-[over]:-outline-offset-4 data-[over]:outline-dashed data-[over]:outline-[color:var(--color-sig)]"
        data-active={isActive || undefined}
        data-over={over === key || undefined}
        {...(target !== undefined ? dropProps(key, target) : {})}
      >
        <button
          type="button"
          aria-pressed={isActive}
          onClick={() => onSelect(filter)}
          className={`block w-full py-1.5 pl-2.5 text-left text-[13px] text-[color:var(--color-ink)] ${menu ? 'pr-16' : 'pr-2.5'}`}
        >
          <span className="flex items-baseline gap-2">
            <span className={`min-w-0 flex-1 truncate ${isActive ? 'font-semibold' : ''} ${filter === UNASSIGNED ? 'italic text-[color:var(--color-ink-2)]' : ''}`}>{label}</span>
            <span className="mono text-[12px] font-semibold">{count}</span>
          </span>
          {detail && <span className="block truncate text-[11px] text-[color:var(--color-ink-2)]">{over === key ? t('collections.dropHere') : detail}</span>}
        </button>
        {menu && <div className="absolute right-1 top-1">{menu}</div>}
      </li>
    )
  }

  return (
    <nav aria-label={t('collections.title')} className="overflow-hidden rounded-[var(--radius-lg)] border border-[color:var(--color-rule)] self-start">
      <div className="bg-[color:var(--color-ink)] px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.05em] text-white">{t('collections.title')}</div>
      <ul>
        {row('all', 'all', t('collections.all'), overview.total.pages, null)}
        {overview.collections.map((c) => row(
          String(c.id), c.id, c.name, c.pages, stateLine(c, t),
          canEdit ? (
            <Menu label={<><span aria-hidden="true">⋯</span><span className="sr-only">{t('collections.actions', { name: c.name })}</span></>}>
              <button type="button" className="menu-item" onClick={() => onRename(c)}>{t('collections.rename')}</button>
              <button type="button" className="menu-item text-[color:var(--color-sig-text)]" onClick={() => onDelete(c)}>{t('collections.delete')}</button>
            </Menu>
          ) : undefined,
          c.id,
        ))}
        {row(UNASSIGNED, UNASSIGNED, t('collections.none'), overview.unassigned.pages, overview.unassigned.pages ? stateLine(overview.unassigned, t) : null, undefined, null)}
      </ul>
      {canEdit && (
        <button type="button" onClick={onCreate} className="block w-full border-t border-[color:var(--color-rule)] px-2.5 py-1.5 text-left text-[13px] text-[color:var(--color-sig-text)]">
          + {t('collections.create')}
        </button>
      )}
      <p className="sr-only">{tn('collections.count', overview.collections.length)}</p>
    </nav>
  )
}
