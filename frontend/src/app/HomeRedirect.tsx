/**
 * `/` resumes work: the project used last, or the only one there is. With
 * several projects and none remembered, the list is the place to choose.
 */
import { useEffect, useState } from 'react'
import { Navigate, useLocation } from 'react-router'
import { apiGet } from '../lib/api'
import type { Project } from '../lib/types'
import { readActiveProject } from './activeProject'
import { legacyTarget } from './legacyRoutes'
import { projectPath } from './projectPaths'
import { Loading } from './ui'
import { useI18n } from '../i18n'

export function HomeRedirect() {
  const { t } = useI18n()
  const [target, setTarget] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    apiGet<{ items: Project[] }>('/projects')
      .then(({ items }) => {
        if (!alive) return
        const remembered = items.find((p) => p.id === readActiveProject())
        const only = items.length === 1 ? items[0] : undefined
        const chosen = remembered ?? only
        setTarget(chosen ? projectPath(chosen.id) : '/progetti')
      })
      .catch(() => alive && setTarget('/progetti'))
    return () => {
      alive = false
    }
  }, [])
  if (!target) return <div className="p-6"><Loading label={t('common.loading')} /></div>
  return <Navigate to={target} replace />
}

/** An address of the old section-based navigation, sent to its place in the project. */
export function LegacyRedirect() {
  const { pathname, search } = useLocation()
  return <Navigate to={legacyTarget(pathname, search, readActiveProject())} replace />
}
