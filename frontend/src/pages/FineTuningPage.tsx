/** Panoramica operativa: stato dei dati, prossimo gesto e accesso ai tre strumenti. */
import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { apiGet } from '../lib/api'
import type { DatasetStatus, TrainingStatus } from '../lib/types'
import { runStateLabel } from '../lib/vocab'
import { useProjects, writeActiveProject } from '../app/activeProject'
import { datasetUsable, type Workflow } from '../app/pipeline'
import { Badge, ErrorNotice, Field, Loading, Module, PageHeader } from '../app/ui'
import { useI18n, tn } from '../i18n'
import { useInference } from '../app/inference'

type Overview = { workflow: Workflow; dataset: DatasetStatus; training: TrainingStatus }

export default function FineTuningPage() {
  const { t } = useI18n()
  const inference = useInference()
  const [projectId, setProjectId] = useState<number | ''>('')
  const [overview, setOverview] = useState<Overview | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [retry, setRetry] = useState(0)
  const onProject = (id: number | '') => {
    setProjectId(id)
    writeActiveProject(id === '' ? null : id)
    setOverview(null)
    setError(null)
  }
  const projects = useProjects(onProject, setError)

  useEffect(() => {
    if (projectId === '') return
    let alive = true
    setOverview(null)
    setError(null)
    void Promise.all([
      apiGet<Workflow>(`/projects/${projectId}/workflow`),
      apiGet<DatasetStatus>(`/projects/${projectId}/datasets`),
      apiGet<TrainingStatus>(`/projects/${projectId}/training/status`),
    ]).then(([workflow, dataset, training]) => {
      if (alive) setOverview({ workflow, dataset, training })
    }).catch((e) => { if (alive) setError(e) })
    return () => { alive = false }
  }, [projectId, retry])

  const hasPages = (overview?.workflow.total_pages ?? 0) > 0
  const gold = overview?.workflow.approved_pages ?? 0
  const dataReady = overview ? datasetUsable(overview.dataset) : false
  const active = overview?.training.active === true
  const readOnly = projects.find((p) => p.id === projectId)?.access?.can_edit === false
  const trained = overview?.training.run?.state === 'finished'
  const prepared = overview?.training.run?.prepare_job
  const modelReady = prepared?.state === 'finished' && Boolean(prepared.model_id)
  const studio = overview?.workflow.next_page
    ? `/annotazione?project=${projectId}&page=${overview.workflow.next_page.id}`
    : '/annotazione'
  const next = readOnly
    ? { to: studio, title: 'team.readerTitle', body: 'team.readerNotice', action: 'team.openReader' }
    : active
    ? { to: '/training', title: 'ux.nextRunning', body: 'ux.nextRunningBody', action: 'ux.monitorTraining' }
    : !hasPages
      ? { to: `/progetti/${projectId}`, title: 'ux.nextImport', body: 'ux.nextImportBody', action: 'workflow.addPages' }
      : !dataReady && gold === 0
        ? { to: studio, title: 'ux.nextReview', body: 'ux.nextReviewBody', action: 'dataset.material.openStudio' }
        : !dataReady
          ? { to: '/dataset', title: 'ux.nextDataset', body: 'ux.nextDatasetBody', action: 'pipeline.steps.datasetAction' }
          : trained && !modelReady
            ? { to: '/training', title: 'ux.nextPrepare', body: 'ux.nextPrepareBody', action: 'training.prepareCheckpoint' }
            : trained && (inference.adapterId !== prepared?.model_id || !inference.enabled || !inference.available)
              ? { to: `/modelli?modello=${encodeURIComponent(prepared?.model_id ?? '')}&passo=dove`, title: 'ux.nextServe', body: 'ux.nextServeBody', action: 'training.serveModel' }
          : trained
            ? { to: '/valutazione', title: 'ux.nextEvaluate', body: 'ux.nextEvaluateBody', action: 'pipeline.steps.evaluateAction' }
            : { to: '/training?view=new', title: 'ux.nextTrain', body: 'ux.nextTrainBody', action: 'ux.configureTraining' }

  return (
    <div className="p-3">
      <PageHeader title={t('nav.training')} intro={`${t('ux.refineIntro')} ${t('ux.baselineHint')}`} aux={
        <div className="w-[min(24rem,100%)]">
        <Field label={t('training.project')}>
          <select className="fld" value={projectId} onChange={(e) => onProject(e.target.value ? Number(e.target.value) : '')}>
            <option value="">{t('common.chooseProject')}</option>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </Field>
        </div>
      } />
      {error != null && <div className="mb-3"><ErrorNotice error={error} />{projectId !== '' && <button className="btn mt-2" onClick={() => setRetry((n) => n + 1)}>{t('ux.retryStatus')}</button>}</div>}
      {projectId === '' && <Module tab={t('ux.nextStep')} quiet>
        <p className="mb-3 text-[13px]">{t('ux.pickRefineProject')}</p>
        <Link className="btn no-underline" to="/progetti">{t('nav.projects')}</Link>
      </Module>}
      {projectId !== '' && !overview && error == null && <Loading label={t('ux.loadingStatus')} />}
      {overview && <>
        <section aria-label={t('ux.nextStep')} className="mb-8 flex flex-wrap items-center gap-5 py-5">
          <div className="min-w-0 flex-1 basis-64">
            <h2 className="text-[18px] font-bold">{t(next.title)}</h2>
            <p className="mt-1 max-w-[85ch] text-[13px] text-[color:var(--color-ink-2)]">{t(next.body)}</p>
          </div>
          <Link to={next.to} className="btn btn-primary no-underline">{t(next.action)}</Link>
        </section>
        <section aria-label={t('ux.refineTools')} className="mt-6">
          <ol className="ruled">
            {[
              { to: '/dataset', title: 'workflow.dataset', status: dataReady ? t('ux.dataReady') : tn('ux.goldCount', gold) },
              { to: '/training', title: 'workflow.train', status: overview.training.run ? runStateLabel(overview.training.run.state) : t('ux.noTraining') },
              { to: '/valutazione', title: 'workflow.evaluate', status: gold > 0 ? tn('ux.goldCount', gold) : t('ux.needsGold') },
            ].map((step) => <li key={step.to}><Link to={step.to} className="flex flex-wrap items-center gap-x-5 gap-y-2 py-4 no-underline hover:bg-[color:var(--color-fill)]">
              <div className="min-w-0 flex-1 basis-64">
                <h3 className="text-[15px] font-semibold">{t(step.title)}</h3>
              </div>
              <Badge tone="neutral">{step.status}</Badge>
              <span aria-hidden="true">→</span>
            </Link></li>)}
          </ol>
        </section>
      </>}
    </div>
  )
}
