/**
 * I tre percorsi del corpus.
 *
 * Queste asserzioni difendono la scelta di prodotto: il corpus non è una fila
 * di tappe obbligate, quindi il percorso di riconoscimento non può dipendere
 * dal fine-tuning, e i percorsi bloccati sono bloccati per una ragione detta.
 */
import { describe, expect, it } from 'vitest'
import { buildBranches, type BranchInput } from './pipeline'
import type { Project, RecognitionRun } from '../lib/types'

function project(): Project {
  return {
    id: 1,
    name: 'Tabularium Smoke',
    owner_id: null,
    root_dir: '/tmp/data',
    archive_dir: '/tmp/scans',
    settings_json: {},
    pages_count: 3,
    created_at: '2026-01-01',
  }
}

function run(overrides: Partial<RecognitionRun> = {}): RecognitionRun {
  return {
    id: 1,
    project_id: 1,
    state: 'finished',
    engine: 'model',
    mode: 'replace_drafts',
    model_mode: 'native',
    model_name: 'MonkeyOCRv2',
    adapter_id: 'monkeyocrv2-parsing',
    provider: 'local',
    endpoint: null,
    stop_policy: 'none',
    total_pages: 3,
    completed_pages: 3,
    succeeded_pages: 3,
    failed_pages: 0,
    error: null,
    created_at: '2026-01-01',
    started_at: null,
    heartbeat_at: null,
    ended_at: null,
    ...overrides,
  }
}

function input(overrides: Partial<BranchInput> = {}): BranchInput {
  return {
    project: project(),
    workflow: {
      counts: { new: 3 },
      total_pages: 3,
      approved_pages: 0,
      progress: 0,
      next_page: null,
    },
    dataset: { built: false, report: null },
    training: { active: false, run: null, log_tail: '', metrics: [], gpu: [] },
    runs: [],
    ...overrides,
  }
}

describe('buildBranches', () => {
  it('senza pagine registrate, riconoscere e annotare dichiarano cosa manca', () => {
    const branches = buildBranches(
      input({
        workflow: {
          counts: {},
          total_pages: 0,
          approved_pages: 0,
          progress: 0,
          next_page: null,
        },
      }),
    )
    const recognize = branches.find((b) => b.id === 'recognize')!
    const annotate = branches.find((b) => b.id === 'annotate')!
    expect(recognize.state).toBe('blocked')
    expect(annotate.state).toBe('blocked')
    expect(recognize.needs).toBeTruthy()
    expect(recognize.detail).toBe('')
  })

  it('senza run il percorso di riconoscimento è pronto e raccomandato, e non dipende dal training', () => {
    const branches = buildBranches(input())
    const recognize = branches.find((b) => b.id === 'recognize')!
    const refine = branches.find((b) => b.id === 'refine')!
    expect(recognize.state).toBe('todo')
    expect(recognize.recommended).toBe(true)
    expect(recognize.detail).toContain('3')
    expect(recognize.action.to).toBe('/')
    // Il ramo opzionale resta aperto ma non raccomandato e senza piastra.
    expect(refine.recommended).toBe(false)
    expect(refine.action.to).toBe('/dataset')
  })

  it('con risultati il riconoscimento porta ai risultati, non riparte da capo', () => {
    const branches = buildBranches(input({ runs: [run()] }))
    const recognize = branches.find((b) => b.id === 'recognize')!
    expect(recognize.detail).toContain('3')
    expect(recognize.action.to).toBe('/risultati')
  })

  it('una run in corso dice il progresso invece di offrire un’azione', () => {
    const branches = buildBranches(
      input({ runs: [run({ state: 'running', completed_pages: 1, succeeded_pages: 1 })] }),
    )
    const recognize = branches.find((b) => b.id === 'recognize')!
    expect(recognize.state).toBe('active')
    expect(recognize.detail).toContain('1/3')
    expect(recognize.action.to).toBe('/')
  })

  it('annotare è concluso solo quando nessuna pagina è più nuova', () => {
    const parziale = buildBranches(input()).find((b) => b.id === 'annotate')!
    expect(parziale.state).toBe('todo')
    const completo = buildBranches(
      input({
        workflow: {
          counts: { exported: 3 },
          total_pages: 3,
          approved_pages: 3,
          progress: 1,
          next_page: null,
        },
      }),
    ).find((b) => b.id === 'annotate')!
    expect(completo.state).toBe('done')
  })

  it('una pagina annotata conta come lavorata, come dice la testata dell’archivio', () => {
    const branches = buildBranches(
      input({
        workflow: {
          counts: { annotated: 1, new: 2 },
          total_pages: 3,
          approved_pages: 0,
          progress: 0,
          next_page: null,
        },
      }),
    )
    const annotate = branches.find((b) => b.id === 'annotate')!
    expect(annotate.detail).toContain('1/3')
    expect(annotate.state).toBe('todo')
  })

  it('il ramo «raffina» segue la prima tappa non chiusa', () => {
    const datasetAperto = buildBranches(input()).find((b) => b.id === 'refine')!
    expect(datasetAperto.action.to).toBe('/dataset')

    const trainingAperto = buildBranches(
      input({
        dataset: {
          built: true,
          report: {
            built_at: '2026-01-02T10:00:00Z',
          } as never,
        },
      }),
    ).find((b) => b.id === 'refine')!
    expect(trainingAperto.action.to).toBe('/training')

    const trainingInCorso = buildBranches(
      input({
        dataset: { built: true, report: { built_at: '2026-01-02T10:00:00Z' } as never },
        training: {
          active: true,
          run: { run_id: 'r1', state: 'running' },
          log_tail: '',
          metrics: [],
          gpu: [],
        },
      }),
    ).find((b) => b.id === 'refine')!
    expect(trainingInCorso.state).toBe('active')
  })
})
