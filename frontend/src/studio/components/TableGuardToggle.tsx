import { useEffect, useState } from 'react'
import { apiGet, apiPut } from '../../lib/api'
import type { TableGuardOut } from '../../lib/types'
import { ErrorNotice } from '../../app/ui'
import { useI18n } from '../../i18n'

/**
 * Per-project switch of the structure guard on model tables (backend
 * `services/table_guard.py`). Unset, the project follows its domain profile's
 * documented default; the reset button returns to it.
 */
export default function TableGuardToggle({ projectId }: { projectId: number }) {
  const { t } = useI18n()
  const [state, setState] = useState<TableGuardOut | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    setState(null)
    apiGet<TableGuardOut>(`/projects/${projectId}/table-guard`).then(setState).catch(setError)
  }, [projectId])

  const save = async (enabled: boolean | null) => {
    setError(null)
    try {
      setState(await apiPut<TableGuardOut>(`/projects/${projectId}/table-guard`, { enabled }))
    } catch (e) {
      setError(e)
    }
  }

  if (!state) return error != null ? <ErrorNotice error={error} onDismiss={() => setError(null)} /> : null
  return (
    <div className="border-t border-[color:var(--color-rule)] p-3">
      {error != null && (
        <div className="mb-2">
          <ErrorNotice error={error} onDismiss={() => setError(null)} />
        </div>
      )}
      <p className="mb-1 text-[12px] font-semibold text-[color:var(--color-ink)]">{t('tableGuard.title')}</p>
      <p className="mb-2 text-[11px] text-[color:var(--color-ink-3)]">{t('tableGuard.intro')}</p>
      <div className="flex flex-wrap items-center gap-3">
        <label className="inline-flex items-center gap-1.5 text-[12px]">
          <input type="checkbox" checked={state.enabled} onChange={(e) => void save(e.target.checked)} />
          {t('tableGuard.enabled')}
        </label>
        <span className="text-[11px] text-[color:var(--color-ink-3)]">
          {state.source === 'profile'
            ? t('tableGuard.followsProfile', {
                profile: state.profile_id,
                state: state.profile_default ? t('tableGuard.stateOn') : t('tableGuard.stateOff'),
              })
            : t('tableGuard.projectSet')}
        </span>
        {state.source === 'project' && (
          <button type="button" className="btn btn-sm" onClick={() => void save(null)}>
            {t('tableGuard.reset')}
          </button>
        )}
      </div>
      {!state.declares_columns && (
        <p className="mt-2 text-[11px] text-[color:var(--color-ink-3)]">{t('tableGuard.noColumns')}</p>
      )}
    </div>
  )
}
