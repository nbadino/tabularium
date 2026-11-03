/**
 * Model comparison on the Recognition page: the saved models (compute profiles),
 * saving the model chosen in Models as one more, and a comparison run over the
 * selected pages. The run never writes annotations; the Studio shows its result.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'
import { apiDelete, apiGet, apiPost } from '../lib/api'
import { useI18n } from '../i18n'
import { useConfirm } from '../app/confirm'
import { Badge, ErrorNotice, Module } from '../app/ui'
import { projectPath } from '../app/projectPaths'

export interface SavedModel {
  id: number
  name: string
  model_adapter_id: string
  served_model_name: string
  has_credential: boolean
}

export interface ConsensusRun {
  id: number
  project_id: number
  state: 'queued' | 'running' | 'finished' | 'finished_with_errors' | 'failed' | 'cancelled'
  models: { key: string; profile_id: number; name: string }[]
  total: number
  succeeded: number
  failed: number
  pending: number
  error: string | null
}

const ACTIVE = new Set(['queued', 'running'])

export default function ConsensusPanel({
  projectId,
  pageIds,
  canManage,
  currentModel,
}: {
  projectId: number | ''
  pageIds: number[]
  /** Saving or deleting a model touches keys: administrators only. */
  canManage: boolean
  /** Display name of the model chosen in Models, proposed as the saved name. */
  currentModel: string
}) {
  const { t } = useI18n()
  const confirm = useConfirm()
  const [profiles, setProfiles] = useState<SavedModel[]>([])
  const [chosen, setChosen] = useState<Set<number>>(new Set())
  const [run, setRun] = useState<ConsensusRun | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const loadProfiles = useCallback(async () => {
    const out = await apiGet<{ items: SavedModel[] }>('/consensus/profiles')
    setProfiles(out.items)
  }, [])

  useEffect(() => { void loadProfiles().catch(setError) }, [loadProfiles])
  useEffect(() => {
    if (projectId === '') return
    void apiGet<{ items: ConsensusRun[] }>(`/projects/${projectId}/consensus-runs`)
      .then((out) => setRun(out.items[0] ?? null))
      .catch(setError)
  }, [projectId])

  // Follow an active run until it ends.
  useEffect(() => {
    if (!run || !ACTIVE.has(run.state)) return
    const timer = window.setInterval(() => {
      void apiGet<ConsensusRun>(`/projects/${run.project_id}/consensus-runs/${run.id}`).then(setRun).catch(setError)
    }, 2000)
    return () => window.clearInterval(timer)
  }, [run])

  const save = async () => {
    setBusy(true)
    try {
      const saved = await apiPost<SavedModel>('/consensus/profiles', { name: (name || currentModel).trim() })
      setName('')
      await loadProfiles()
      setChosen((prev) => new Set(prev).add(saved.id))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const remove = async (profile: SavedModel) => {
    if (!await confirm({ title: t('consensus.deleteTitle'), message: t('consensus.deleteConfirm', { name: profile.name }), acceptLabel: t('consensus.delete') })) return
    try {
      await apiDelete(`/consensus/profiles/${profile.id}`)
      setChosen((prev) => { const next = new Set(prev); next.delete(profile.id); return next })
      await loadProfiles()
    } catch (e) {
      setError(e)
    }
  }

  const start = async () => {
    if (projectId === '') return
    setBusy(true)
    try {
      setRun(await apiPost<ConsensusRun>(`/projects/${projectId}/consensus-runs`, {
        page_ids: pageIds,
        profile_ids: [...chosen],
      }))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    if (!run) return
    try {
      setRun(await apiPost<ConsensusRun>(`/projects/${run.project_id}/consensus-runs/${run.id}/cancel`))
    } catch (e) {
      setError(e)
    }
  }

  const canStart = chosen.size >= 2 && pageIds.length > 0 && projectId !== '' && !busy && !(run && ACTIVE.has(run.state))
  const tone = run?.state === 'finished' ? 'ok' : run && (run.state === 'failed' || run.state === 'finished_with_errors') ? 'warn' : 'neutral'

  return (
    <Module tab={t('consensus.panelTitle')}>
      <p className="text-[11px] text-[color:var(--color-ink-2)]">{t('consensus.panelHint')}</p>
      {error != null && <div className="mt-2"><ErrorNotice error={error} onDismiss={() => setError(null)} /></div>}

      <div className="mt-3 lbl">{t('consensus.profiles')}</div>
      {profiles.length === 0 ? (
        <p className="text-[12px] text-[color:var(--color-ink-3)]">{t('consensus.noProfiles')}</p>
      ) : (
        <ul className="mt-1 space-y-1">
          {profiles.map((profile) => (
            <li key={profile.id} className="flex items-center gap-2 text-[12px]">
              <label className="flex min-w-0 flex-1 items-center gap-2">
                <input
                  type="checkbox"
                  checked={chosen.has(profile.id)}
                  onChange={(e) => setChosen((prev) => {
                    const next = new Set(prev)
                    if (e.target.checked) next.add(profile.id)
                    else next.delete(profile.id)
                    return next
                  })}
                />
                <span className="min-w-0 truncate font-semibold">{profile.name}</span>
                <span className="mono truncate text-[11px] text-[color:var(--color-ink-3)]">{profile.served_model_name}</span>
              </label>
              {canManage && (
                <button type="button" className="text-[11px] underline" onClick={() => void remove(profile)}>{t('consensus.delete')}</button>
              )}
            </li>
          ))}
        </ul>
      )}

      {canManage && (
        <div className="mt-3 border-t border-[color:var(--color-rule)] pt-2">
          <label className="block">
            <span className="lbl">{t('consensus.saveName')}</span>
            <div className="flex gap-2">
              <input className="fld" value={name} placeholder={currentModel} onChange={(e) => setName(e.target.value)} />
              <button type="button" className="btn" disabled={busy || !(name || currentModel).trim()} onClick={() => void save()}>{t('consensus.saveCurrent')}</button>
            </div>
          </label>
          <p className="mt-1 text-[11px] text-[color:var(--color-ink-3)]">{t('consensus.saveHint')}</p>
        </div>
      )}

      <button type="button" className="btn btn-primary mt-3 w-full" disabled={!canStart} onClick={() => void start()}>
        {t('consensus.start', { n: pageIds.length })}
      </button>
      {!canStart && !(run && ACTIVE.has(run.state)) && <p className="mt-1 text-[11px] text-[color:var(--color-ink-3)]">{t('consensus.pickTwo')}</p>}

      {run && (
        <div className="mt-3 border-t border-[color:var(--color-rule)] pt-2 text-[12px]" data-testid="consensus-run">
          <div className="flex items-center gap-2">
            <span className="mono text-[11px]">#{run.id}</span>
            <span className="min-w-0 flex-1 truncate">{run.models.map((m) => m.name).join(' · ')}</span>
            <Badge tone={tone}>{t(`consensus.state.${run.state}`)}</Badge>
          </div>
          <div className="mt-1 text-[11px] text-[color:var(--color-ink-2)]">
            {t('consensus.progress', { done: run.succeeded + run.failed, total: run.total })}
            {run.failed > 0 && <span className="ml-2 text-[color:var(--color-sig-text)]">{t('consensus.failedCount', { n: run.failed })}</span>}
          </div>
          {run.error && <p className="text-[11px] text-[color:var(--color-sig-text)]">{run.error}</p>}
          <div className="mt-1 flex gap-3">
            {ACTIVE.has(run.state) && <button type="button" className="text-[11px] underline" onClick={() => void cancel()}>{t('consensus.cancel')}</button>}
            <Link to={projectPath(run.project_id, 'studio', { consensus: 1, page: pageIds[0] })} className="text-[11px] font-semibold">{t('consensus.openStudio')}</Link>
          </div>
        </div>
      )}
    </Module>
  )
}
