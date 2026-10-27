// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router'
import type { EvalReport } from '../lib/types'
import EvaluationPage from './EvaluationPage'

const layout = { n_gt: 2, n_pred: 3, matched: 2, precision: 2 / 3, recall: 1, mean_iou_of_matched: 0.8 }
const report: EvalReport = {
  project_id: 1,
  evaluated_at: '2026-10-08T00:00:00Z',
  config: { server_url: 'http://x', model: 'lightonocr-3', with_text: false, split: { ratio: 0.9, seed: 42 } },
  pages_evaluated: 1,
  val_pages: 1,
  aggregates: {
    layout,
    layout_strict: {
      ...layout,
      matched: 1,
      f1: 0.4,
      per_label: {
        Title: { n_gt: 1, n_pred: 1, matched: 1, precision: 1, recall: 1, f1: 1 },
        Table: { n_gt: 1, n_pred: 2, matched: 0, precision: 0, recall: 0, f1: 0 },
      },
    },
    order: { mean_levenshtein_norm: 0, exact_pct: 100 },
    text: { n: 0, mean_cer: null, mean_wer: null },
    tables: { n: 0, structure_ok_pct: null, mean_cell_cer: null },
  },
  pages: [],
  warnings: [],
  eval_dir: '/tmp/eval',
}

vi.mock('../lib/api', () => ({
  apiGet: vi.fn(async (path: string) =>
    path === '/projects' ? { items: [{ id: 1, name: 'P', root_dir: '/a' }] } : { items: [] },
  ),
  apiPost: vi.fn(async () => report),
}))
let finishedTraining = false

vi.mock('../app/pipeline', () => ({
  usePipelineState: () => ({ workflow: { approved_pages: 1 }, training: finishedTraining ? { run: { state: 'finished' } } : null, dataset: finishedTraining ? { report: { pages: { val: 0 } } } : null }),
  buildPipeline: () => [],
}))
vi.mock('../app/PipelineView', () => ({ PipelineStrip: () => null }))
vi.mock('../app/inference', () => ({
  useInference: () => ({
    enabled: true, available: true, url: '', model: 'lightonocr-3', adapterId: 'lightonocr-3', provider: null,
  }),
  setInference: vi.fn(),
  testInferenceConnection: vi.fn(async () => ({ ok: true, url: '', model: 'lightonocr-3', models_available: [], latency_ms: 1 })),
}))

describe('EvaluationPage', () => {
  it('blocks evaluation after training when there are no held-out pages', async () => {
    finishedTraining = true
    render(<MemoryRouter><EvaluationPage /></MemoryRouter>)
    await screen.findByText(/La valutazione dopo il training richiede pagine di validazione/)
    expect(screen.getByRole('button', { name: 'Esegui valutazione' })).toBeDisabled()
  })

  afterEach(() => { cleanup(); finishedTraining = false })

  it('mostra F1 a IoU 0,75 e il dettaglio per label', async () => {
    render(<MemoryRouter><EvaluationPage /></MemoryRouter>)
    const run = await screen.findByRole('button', { name: 'Esegui valutazione' })
    await vi.waitFor(() => expect(run).toBeEnabled())
    fireEvent.click(run)
    expect(await screen.findByText('40.0%')).toBeInTheDocument()
    expect(screen.getByText('1/2 trovati · 3 predetti')).toBeInTheDocument()
    const table = screen.getByRole('table')
    const tableRow = within(table).getByText('Table').closest('tr')!
    expect(within(tableRow).getByText('0/1')).toBeInTheDocument()
    expect(within(tableRow).getByText('2')).toBeInTheDocument()
  })
})
