// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import type { GoldEvalRecord } from '../../lib/types'
import { GoldEvalPanel } from './GoldEvalPanel'

afterEach(cleanup)

const finished: GoldEvalRecord = {
  state: 'finished',
  complete: true,
  completed: 2,
  total: 2,
  aggregate: {
    tables: 2, pages: 2, cell_exact: 0.902, exact_cells: 902, reference_cells: 1000, cer: 0.085,
    header_structure_ok: 1, structure_ok: 1, missing_rows: 3, extra_rows: 1, mean_teds: 0.9,
    failures: { truncated: 1 }, failed: 1, seconds: 600,
  },
  pages: [
    { id: 'p13_b4', page_id: 13, block_id: 4, metrics: { cell_exact: 0.98, cer: 0.01, missing_rows: 0, extra_rows: 0, header_structure_ok: true, reference_cells: 500, exact_cells: 490 } },
    { id: 'p33_b9', page_id: 33, block_id: 9, failure: 'truncated', metrics: { cell_exact: 0, cer: 1, missing_rows: 3, extra_rows: 1, header_structure_ok: false, reference_cells: 500, exact_cells: 0 } },
  ],
  environment: { image: 'ghcr.io/nbadino/tabularium-eval-xiaomi:v1', image_digest: 'sha256:fd41c392fb545b1cc6a27aa8af211fa6ad6e8c1f78594c3756d485f2f3e14fbd', torch: '2.14.0+cu130', transformers: '5.17.0', gpu: 'NVIDIA A100' },
}

describe('GoldEvalPanel', () => {
  it('shows the aggregate, the environment and every table', () => {
    render(<GoldEvalPanel record={finished} />)
    expect(screen.getByText('90.2%')).toBeInTheDocument()
    expect(screen.getByText('8.5%')).toBeInTheDocument()
    expect(screen.getAllByText('1/2', { selector: '.mono' })).toHaveLength(2) // correct header, failed tables
    expect(screen.getByText(/torch 2\.14\.0\+cu130 · transformers 5\.17\.0/)).toBeInTheDocument()
    expect(screen.getByText('p33_b9')).toBeInTheDocument()
    expect(screen.getByText('truncated')).toBeInTheDocument()
  })

  it('warns that a capped evaluation is not comparable', () => {
    render(<GoldEvalPanel record={{ ...finished, complete: false, completed: 1 }} />)
    expect(screen.getByText(/Limite di tempo raggiunto: lette 1 tabelle su 2/)).toBeInTheDocument()
  })

  it('says the model was kept when the evaluation failed', () => {
    render(<GoldEvalPanel record={{ state: 'failed', error: 'CUDA driver too old' }} />)
    expect(screen.getByText(/il modello è stato comunque preparato e conservato/)).toBeInTheDocument()
    expect(screen.getByText('CUDA driver too old')).toBeInTheDocument()
  })

  it('shows progress while the GPU reads the gold tables', () => {
    render(<GoldEvalPanel record={{ state: 'running', phase: 'inference', completed: 5, total: 27 }} />)
    expect(screen.getByText('Lettura della tabella gold 5 di 27')).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '5')
  })

  it('explains before training why a run will not be evaluated', () => {
    render(<GoldEvalPanel plan={{ state: 'not_applicable', reason: 'no_gold_tables' }} />)
    expect(screen.getByText(/non ha pagine gold approvate/)).toBeInTheDocument()
    cleanup()
    render(<GoldEvalPanel plan={{ state: 'planned', tables: 27, pages: 27, max_minutes: 60 }} />)
    expect(screen.getByText(/verranno lette 27 tabelle gold di 27 pagine \(al massimo 60 min\)/)).toBeInTheDocument()
  })
})
