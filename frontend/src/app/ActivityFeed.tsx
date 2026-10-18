/**
 * Feed attività di un progetto.
 *
 * Una riga dice chi, cosa e *dove*: il codice grezzo (`page.annotations_saved`)
 * non direbbe né l'azione né la pagina. Usato sia nella pagina del progetto sia
 * nella finestra di condivisione: un'unica resa, una sola tabella di traduzione.
 */
import { Link } from 'react-router'
import type { PageItem } from '../lib/types'
import { useI18n } from '../i18n'
import { formatDateTime } from '../lib/dates'

export type ProjectActivity = {
  id: number
  action: string
  resource_type: string | null
  resource_id: number | null
  payload_json?: string | null
  created_at: string
  username: string | null
}

/** Azioni che il registro conosce: le altre restano col codice grezzo. */
const ACTIVITY_ACTIONS = new Set([
  'block.deleted', 'block.updated', 'page.annotations_deleted', 'page.annotations_saved', 'page.approved',
  'project.member_removed', 'project.member_set', 'project.owner_transferred', 'table.updated',
  'training.started', 'training.stopped',
])

function activityPayload(event: ProjectActivity): Record<string, unknown> {
  try {
    return event.payload_json ? (JSON.parse(event.payload_json) as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

export function ActivityFeed({
  items,
  projectId,
  pages = [],
}: {
  items: ProjectActivity[]
  projectId: number
  /** Pagine del progetto: serve a collegare gli eventi che ne parlano. */
  pages?: PageItem[]
}) {
  const { t, locale } = useI18n()
  return (
    <div className="divide-y divide-[color:var(--color-rule)] border-y border-[color:var(--color-rule)]">
      {items.map((event) => {
        const payload = activityPayload(event)
        const page = event.resource_type === 'page' ? pages.find((item) => item.id === event.resource_id) : undefined
        const count = Number(payload.blocks ?? payload.deleted ?? NaN)
        return (
          <div key={event.id} className="grid gap-1 py-2 text-[12px] sm:grid-cols-[minmax(0,1fr)_auto] sm:items-baseline sm:gap-3">
            <span className="min-w-0 break-words">
              <span className="font-semibold">{event.username || t('project.localActor')}</span>
              {' · '}
              {ACTIVITY_ACTIONS.has(event.action) ? t(`project.activity.${event.action.replace('.', '_')}`) : event.action}
              {Number.isFinite(count) && <span className="text-[color:var(--color-ink-3)]"> ({t('project.activityBlocks', { n: count })})</span>}
              {page && (
                <>
                  {' · '}
                  <Link className="mono" to={`/annotazione?project=${projectId}&page=${page.id}`}>{page.rel_path}</Link>
                </>
              )}
            </span>
            <time className="mono text-[11px] text-[color:var(--color-ink-3)]" dateTime={event.created_at}>{formatDateTime(event.created_at, locale)}</time>
          </div>
        )
      })}
      {!items.length && <p className="py-2 text-[12px] text-[color:var(--color-ink-2)]">{t('project.noActivity')}</p>}
    </div>
  )
}
