/** La scelta della vista segue il permesso del progetto, anche entrando da un URL diretto. */
import { lazy, Suspense, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { apiGet } from '../lib/api'
import type { Project } from '../lib/types'
import { pickActive } from '../app/activeProject'
import { projectAccess } from '../app/projectAccess'
import { useAuth } from '../app/auth'
import { ErrorNotice, Loading } from '../app/ui'
import { useI18n } from '../i18n'
const Editor = lazy(() => import('./AnnotationPage'))
const Reader = lazy(() => import('./ProjectReader'))

export default function AnnotationEntry() {
  const { t } = useI18n()
  const auth = useAuth()
  const [params] = useSearchParams()
  const requested = params.get('project') ?? ''
  const requestKey = `${auth.enabled}:${auth.user?.id ?? ''}:${requested}`
  const [state, setState] = useState<{ key: string; project: Project | null; projects: Project[] } | null>(null)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    let alive = true
    setError(null)
    apiGet<{ items: Project[] }>('/projects').then(async ({ items }) => {
      const id = requested ? Number(requested) : pickActive(items)
      const project = items.find((p) => p.id === id) ?? (requested ? await apiGet<Project>(`/projects/${requested}`) : null)
      if (alive) setState({ key: requestKey, project, projects: items })
    }).catch((e) => { if (alive) setError(e) })
    return () => { alive = false }
  }, [requested, requestKey])
  if (error) return <div className="p-3"><ErrorNotice error={error} /></div>
  if (!state || state.key !== requestKey) return <div className="p-3"><Loading label={t('team.loadingAccess')} /></div>
  const access = state.project ? projectAccess(state.project, auth) : null
  if (!state.project) return <div className="p-3"><p className="mb-3">{t('team.noAssignedProjects')}</p><Link className="btn" to="/progetti">{t('nav.projects')}</Link></div>
  if (!access) return <p className="p-3">{t('team.accessUnknownHint')}</p>
  return <Suspense fallback={<Loading label={t('common.loading')} />}>
    {access.can_edit ? <Editor key={state.project.id} /> : <Reader key={state.project.id} project={state.project} projects={state.projects} />}
  </Suspense>
}
