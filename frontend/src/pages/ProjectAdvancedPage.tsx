/**
 * Advanced settings of a project: the methodological tools a research project
 * may need (study protocol, pilot sample), the project's region classes,
 * transcription rules and table schema, and the activity log. They used to sit
 * on the Pages tab; here they are one click away instead of in the way.
 */
import { useEffect, useState } from 'react'
import { useParams } from 'react-router'
import { apiGet, apiPost, apiPut } from '../lib/api'
import type { LabelDef, PageItem, Project } from '../lib/types'
import { ErrorNotice, Field, Loading, Module, PageHeader } from '../app/ui'
import { ActivityFeed, type ProjectActivity } from '../app/ActivityFeed'
import { useAuth } from '../app/auth'
import { projectAccess } from '../app/projectAccess'
import { useInference } from '../app/inference'
import LabelsEditor from '../studio/components/LabelsEditor'
import ConventionsChecklist from '../studio/components/ConventionsChecklist'
import TableHintEditor from '../studio/components/TableHintEditor'
import { useI18n, tn } from '../i18n'

type StudyProtocol = {
  corpus_scope: string
  time_start: string | null
  time_end: string | null
  objective: string
  domain_profile: string
  model_adapter: string
  gold_pages: number[]
  version: number
  updated_at: string
}

export default function ProjectAdvancedPage() {
  const { t } = useI18n()
  const { id } = useParams()
  const projectId = Number(id)
  const auth = useAuth()
  const inference = useInference()
  const [project, setProject] = useState<Project | null>(null)
  const [pages, setPages] = useState<PageItem[]>([])
  const [labels, setLabels] = useState<LabelDef[] | null>(null)
  const [protocol, setProtocol] = useState<StudyProtocol | null>(null)
  const [protocolBusy, setProtocolBusy] = useState(false)
  const [pilot, setPilot] = useState<{ pages: Array<{ id: number; rel_path: string }>; target: number } | null>(null)
  const [domainProfiles, setDomainProfiles] = useState<Array<{ id: string; name: string }>>([])
  const [modelAdapters, setModelAdapters] = useState<Array<{ adapter_id: string; display_name: string }>>([])
  const [activity, setActivity] = useState<ProjectActivity[]>([])
  const [error, setError] = useState<unknown>(null)
  const canEdit = project ? projectAccess(project, auth)?.can_edit === true : false

  useEffect(() => {
    apiGet<Project>(`/projects/${projectId}`).then(setProject).catch(setError)
    apiGet<{ items: PageItem[] }>(`/projects/${projectId}/pages`).then((r) => setPages(r.items)).catch(() => setPages([]))
    apiGet<{ labels: LabelDef[] }>(`/projects/${projectId}/labels`).then((r) => setLabels(r.labels)).catch(() => setLabels([]))
    apiGet<StudyProtocol>(`/projects/${projectId}/study-protocol`).then(setProtocol).catch(() => setProtocol(null))
    apiGet<{ items: ProjectActivity[] }>(`/projects/${projectId}/activity`).then((r) => setActivity(r.items)).catch(() => setActivity([]))
    apiGet<{ items: Array<{ id: string; name: string }> }>('/system/domain-profiles').then((r) => setDomainProfiles(r.items)).catch(() => setDomainProfiles([]))
    apiGet<{ items: Array<{ adapter_id: string; display_name: string }> }>('/system/model-adapters').then((r) => setModelAdapters(r.items)).catch(() => setModelAdapters([]))
  }, [projectId])

  const saveProtocol = async () => {
    if (!protocol) return
    setProtocolBusy(true)
    try {
      setProtocol(await apiPut<StudyProtocol>(`/projects/${projectId}/study-protocol`, protocol))
    } catch (e) {
      setError(e)
    } finally {
      setProtocolBusy(false)
    }
  }

  const generatePilot = async () => {
    try {
      setPilot(await apiGet<{ pages: Array<{ id: number; rel_path: string }>; target: number }>(`/projects/${projectId}/pilot-sample?target=40&seed=42`))
    } catch (e) { setError(e) }
  }

  const savePilot = async () => {
    if (!pilot) return
    try {
      await apiPost(`/projects/${projectId}/pilot-sample/save`, pilot.pages.map((p) => p.id))
    } catch (e) { setError(e) }
  }

  if (!project && error == null) return <div className="p-3"><Loading label={t('common.loading')} /></div>

  return (
    <div className="p-3">
      <PageHeader title={t('project.advancedTitle')} />
      {error != null && <div className="mb-3"><ErrorNotice error={error} onDismiss={() => setError(null)} /></div>}

      <div className="space-y-6">
        {labels && (
          <Module tab={t('labels.tab')} quiet>
            <LabelsEditor projectId={projectId} labels={labels} activeAdapterId={inference.adapterId} onSaved={setLabels} />
          </Module>
        )}

        {/* The checklist carries its own heading. */}
        <section>
          <ConventionsChecklist projectId={projectId} />
        </section>

        <Module tab={t('tableHint.tab')} quiet>
          <TableHintEditor projectId={projectId} />
        </Module>

        {protocol && (
          <Module
            tab={t('project.protocolTab', { version: protocol.version })}
            quiet
            aux={<span>{protocolBusy ? t('project.protocolSaving') : t('project.protocolVersioned')}</span>}
          >
            <fieldset disabled={!canEdit} className="grid min-w-0 gap-3 sm:grid-cols-2">
              <Field label={t('project.objective')}>
                <textarea value={protocol.objective} onChange={(e) => setProtocol((p) => p ? { ...p, objective: e.target.value } : p)} className="fld min-h-16" placeholder={t('project.objectivePlaceholder')} />
              </Field>
              <Field label={t('project.corpusScope')} hint={t('project.corpusScopeHint')}>
                <input value={protocol.corpus_scope} onChange={(e) => setProtocol((p) => p ? { ...p, corpus_scope: e.target.value } : p)} className="fld" />
              </Field>
              <div className="grid grid-cols-2 gap-2">
                <Field label={t('project.from')}><input type="date" value={protocol.time_start ?? ''} onChange={(e) => setProtocol((p) => p ? { ...p, time_start: e.target.value || null } : p)} className="fld" /></Field>
                <Field label={t('project.to')}><input type="date" value={protocol.time_end ?? ''} onChange={(e) => setProtocol((p) => p ? { ...p, time_end: e.target.value || null } : p)} className="fld" /></Field>
              </div>
              <Field label={t('project.domainProfile')}>
                <div className="flex gap-2">
                  <select value={protocol.domain_profile} onChange={(e) => setProtocol((p) => p ? { ...p, domain_profile: e.target.value } : p)} className="fld">
                    {(domainProfiles.some((d) => d.id === protocol.domain_profile) || !protocol.domain_profile
                      ? domainProfiles
                      : [{ id: protocol.domain_profile, name: protocol.domain_profile }, ...domainProfiles]
                    ).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                  </select>
                  <select value={protocol.model_adapter} onChange={(e) => setProtocol((p) => p ? { ...p, model_adapter: e.target.value } : p)} className="fld">
                    {(modelAdapters.some((a) => a.adapter_id === protocol.model_adapter) || !protocol.model_adapter
                      ? modelAdapters
                      : [{ adapter_id: protocol.model_adapter, display_name: protocol.model_adapter }, ...modelAdapters]
                    ).map((a) => <option key={a.adapter_id} value={a.adapter_id}>{a.display_name}</option>)}
                  </select>
                </div>
              </Field>
              <div className="flex items-end justify-end">
                <button type="button" onClick={() => void saveProtocol()} disabled={protocolBusy} className="btn btn-sm btn-primary">{t('project.saveProtocol')}</button>
              </div>
            </fieldset>
          </Module>
        )}

        <Module tab={t('project.pilotTab')} quiet aux={<span>{t('project.pilotRange')}</span>}>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" disabled={!canEdit} onClick={() => void generatePilot()} className="btn btn-sm">{t('project.generateSample')}</button>
            {pilot && <><span className="mono text-[12px]">{tn('project.pilotSelected', pilot.pages.length)}</span><button type="button" disabled={!canEdit} onClick={() => void savePilot()} className="btn btn-sm btn-primary">{t('project.saveToProtocol')}</button></>}
          </div>
          {pilot && <p className="mono mt-2 truncate text-[11px] text-[color:var(--color-ink-3)]">{pilot.pages.slice(0, 5).map((p) => p.rel_path).join(' · ')}{pilot.pages.length > 5 ? ' …' : ''}</p>}
        </Module>

        <Module tab={t('project.activityTab')} quiet>
          <ActivityFeed items={activity} projectId={projectId} pages={pages} />
        </Module>
      </div>
    </div>
  )
}
