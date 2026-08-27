/**
 * Il percorso: dove sei, e cosa ti blocca.
 *
 * Sei fasi, calcolate dallo stato reale del corpus e non da un contatore
 * salvato da qualche parte. Un solo posto decide l'ordine e i prerequisiti,
 * così la home, le pagine di fase e il «prossimo passo» non possono mai
 * raccontare tre storie diverse.
 */
import { useEffect, useState } from 'react'
import { apiGet } from '../lib/api'
import type { DatasetStatus, Project, TrainingStatus } from '../lib/types'
import { runStateLabel } from '../lib/vocab'
import { t } from '../i18n'

export interface Workflow {
  counts: Record<string, number>
  total_pages: number
  approved_pages: number
  progress: number
  next_page: { id: number; rel_path: string; status: string; blocks: number } | null
}

/** `blocked` significa che manca un prerequisito, non che l'utente ha sbagliato. */
export type StageState = 'done' | 'current' | 'blocked'

export interface Stage {
  id: string
  /** Nome della fase, già tradotto. */
  name: string
  state: StageState
  /** Cosa è vero adesso di questa fase (conteggi, data, stato del run). */
  detail: string
  /** Perché vale la pena farla — mostrato solo sulla fase corrente. */
  why: string
  /** Cosa manca — mostrato solo sulle fasi bloccate. */
  needs: string
  action: { label: string; to: string }
}

export interface PipelineInput {
  project: Project | null
  workflow: Workflow | null
  dataset: DatasetStatus | null
  training: TrainingStatus | null
}

/**
 * Le fasi in ordine, ciascuna con la condizione che la considera soddisfatta.
 * `satisfied` guarda solo la propria fase: l'ordinamento in done/current/
 * blocked lo decide `buildPipeline`, così esiste sempre una sola corrente.
 */
function definitions({ project, workflow, dataset, training }: PipelineInput) {
  const wf = workflow
  const total = wf?.total_pages ?? 0
  const daFare =
    (wf?.counts.new ?? 0) + (wf?.counts.annotated ?? 0) + (wf?.counts.review ?? 0)
  const lavorate = Math.max(0, total - daFare)
  const runState = training?.run?.state
  const trainingDone = runState === 'finished'

  return [
    {
      id: 'project',
      name: t('pipeline.steps.projectName'),
      satisfied: project != null,
      detail: project?.name ?? '',
      why: t('pipeline.steps.projectTodo'),
      needs: t('pipeline.steps.projectNeeds'),
      action: { label: t('pipeline.steps.projectAction'), to: '/progetti' },
    },
    {
      id: 'scan',
      name: t('pipeline.steps.scanName'),
      satisfied: total > 0,
      detail: total > 0 ? t('pipeline.steps.scanDone', { n: total }) : '',
      why: t('pipeline.steps.scanTodo'),
      needs: t('pipeline.steps.scanNeeds'),
      action: {
        label: t('pipeline.steps.scanAction'),
        to: project ? `/progetti/${project.id}` : '/progetti',
      },
    },
    {
      id: 'annotate',
      name: t('pipeline.steps.annotateName'),
      satisfied: total > 0 && daFare === 0,
      detail: total > 0 ? t('pipeline.steps.annotateDetail', { done: lavorate, total }) : '',
      why: t('pipeline.steps.annotateTodo'),
      needs: t('pipeline.steps.annotateNeeds'),
      action: { label: t('pipeline.steps.annotateAction'), to: '/annotazione' },
    },
    {
      id: 'dataset',
      name: t('pipeline.steps.datasetName'),
      satisfied: dataset?.built === true,
      detail:
        dataset?.built && dataset.report
          ? t('pipeline.steps.datasetDone', {
              when: dataset.report.built_at.slice(0, 10),
            })
          : '',
      why: t('pipeline.steps.datasetTodo'),
      needs: t('pipeline.steps.datasetNeeds'),
      action: { label: t('pipeline.steps.datasetAction'), to: '/dataset' },
    },
    {
      id: 'train',
      name: t('pipeline.steps.trainName'),
      satisfied: trainingDone,
      detail: runState ? t('pipeline.steps.trainDone', { state: runStateLabel(runState) }) : '',
      why: t('pipeline.steps.trainTodo'),
      needs: t('pipeline.steps.trainNeeds'),
      action: { label: t('pipeline.steps.trainAction'), to: '/training' },
    },
    {
      id: 'evaluate',
      // La valutazione non ha uno stato persistito lato server: resta sempre
      // l'ultima fase disponibile, mai «fatta». Dichiararla completata
      // sarebbe una bugia comoda.
      name: t('pipeline.steps.evaluateName'),
      satisfied: false,
      detail: '',
      why: t('pipeline.steps.evaluateTodo'),
      needs: t('pipeline.steps.evaluateNeeds'),
      action: { label: t('pipeline.steps.evaluateAction'), to: '/valutazione' },
    },
  ]
}

export function buildPipeline(input: PipelineInput): Stage[] {
  const defs = definitions(input)
  const firstOpen = defs.findIndex((d) => !d.satisfied)
  return defs.map((d, i) => ({
    id: d.id,
    name: d.name,
    state:
      firstOpen === -1 || i < firstOpen ? 'done' : i === firstOpen ? 'current' : 'blocked',
    detail: d.detail,
    why: d.why,
    needs: d.needs,
    action: d.action,
  }))
}

/** Quante fasi sono già chiuse — per il contatore «{n} di 6». */
export function completedCount(stages: Stage[]): number {
  return stages.filter((s) => s.state === 'done').length
}

/**
 * Carica lo stato che serve al percorso. Le tre chiamate non sono critiche:
 * se una fallisce la fase corrispondente risulta semplicemente non ancora
 * soddisfatta, e il percorso resta leggibile.
 */
export function usePipelineState(projectId: number | null) {
  const [workflow, setWorkflow] = useState<Workflow | null>(null)
  const [dataset, setDataset] = useState<DatasetStatus | null>(null)
  const [training, setTraining] = useState<TrainingStatus | null>(null)

  useEffect(() => {
    setWorkflow(null)
    setDataset(null)
    setTraining(null)
    if (projectId == null) return
    let alive = true
    apiGet<Workflow>(`/projects/${projectId}/workflow`)
      .then((w) => alive && setWorkflow(w))
      .catch(() => {})
    apiGet<DatasetStatus>(`/projects/${projectId}/datasets`)
      .then((d) => alive && setDataset(d))
      .catch(() => {})
    apiGet<TrainingStatus>(`/projects/${projectId}/training/status`)
      .then((s) => alive && setTraining(s))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [projectId])

  return { workflow, dataset, training }
}
