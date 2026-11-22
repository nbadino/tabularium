// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router'
import ResultsPage from './ResultsPage'

vi.mock('../app/inference', () => ({ useInference: () => ({ enabled: false }), syncInferenceFromBackend: vi.fn(), toggleInferenceEnabled: vi.fn() }))
vi.mock('../app/auth', () => ({ useCanAdminister: () => false }))
vi.mock('../app/models/names', () => ({ useModelNames: () => () => 'OCR', runTitle: () => 'OCR' }))
vi.mock('../lib/api', () => ({
  apiGet: vi.fn(async (path: string) => {
    if (path === '/projects') return { items: [{ id: 1, name: 'Archive', pages_count: 1 }] }
    if (path === '/pages/6/annotations') {
      return { blocks: [{ id: 11, label: 'Text', content: 'SS Pamir arrived' }] }
    }
    const run = {
      id: 24, project_id: 1, state: 'finished', engine: 'ocr', provider: 'local', adapter_id: 'local-ocr',
      total_pages: 1, completed_pages: 1, failed_pages: 0, created_at: '2026-10-16T10:58:00',
      items: [{ id: 1, page_id: 6, rel_path: '000001_page.png', page_status: 'approved', state: 'finished', detected: 317, inserted: 317, blocks: 0, drafts: 0 }],
    }
    if (path.startsWith('/recognition-runs?')) return { items: [run] }
    return run
  }),
  apiDownloadFile: vi.fn(), apiPost: vi.fn(),
}))
afterEach(cleanup)

it('keeps original detections visible after the run’s saved blocks have been replaced during review', async () => {
  render(<MemoryRouter initialEntries={['/risultati?project=1&run=24']}><ResultsPage /></MemoryRouter>)
  expect(await screen.findByText('317 blocchi trovati in questo riconoscimento')).toBeInTheDocument()
  expect(screen.getByText('page.png')).toBeInTheDocument()
  expect(screen.getByText('Pagina attuale: Approvata')).toBeInTheDocument()
  expect(screen.queryByText('Senza blocchi')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Scarica .pdf' })).toBeInTheDocument()
  await screen.findByRole('button', { name: 'Anteprima' })
  screen.getByRole('button', { name: 'Anteprima' }).click()
  expect(await screen.findByText('SS Pamir arrived')).toBeInTheDocument()
})
