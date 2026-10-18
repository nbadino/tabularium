/** Il permesso viene dal blocco richiesto, mai da `page` passato nella query. */
import { lazy, Suspense, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router'
import { apiGet } from '../lib/api'
import type { BlockOut, Project } from '../lib/types'
import { projectAccess } from '../app/projectAccess'
import { useAuth } from '../app/auth'
import { ErrorNotice, Loading, Notice } from '../app/ui'
import { useI18n } from '../i18n'
const Editor = lazy(() => import('./TableWorkspacePage'))

export default function TableEntry() {
  const { blockId } = useParams()
  const auth = useAuth()
  const { t } = useI18n()
  const [context, setContext] = useState<{ block: BlockOut; project: Project } | null>(null)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    let alive = true
    setContext(null); setError(null)
    void apiGet<BlockOut>(`/blocks/${blockId}`).then(async (block) => {
      const project = await apiGet<Project>(`/projects/${block.project_id}`)
      if (alive) setContext({ block, project })
    }).catch((e) => { if (alive) setError(e) })
    return () => { alive = false }
  }, [blockId, auth.user?.id])
  if (error) return <div className="p-3"><ErrorNotice error={error} /></div>
  if (!context || String(context.block.id) !== blockId) return <Loading label={t('team.loadingAccess')} />
  const access = projectAccess(context.project, auth)
  if (!access?.can_edit) return <div className="p-3"><Notice>{t('team.readerNotice')}</Notice><Link className="btn mt-3" to={`/annotazione?project=${context.project.id}&page=${context.block.page_id}`}>{t('team.openReader')}</Link></div>
  return <Suspense fallback={<Loading label={t('common.loading')} />}><Editor key={blockId} block={context.block} /></Suspense>
}
