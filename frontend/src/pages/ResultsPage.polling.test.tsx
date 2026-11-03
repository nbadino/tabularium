// @vitest-environment jsdom
/** The run picker follows the run it shows: a finished run no longer reads "running · 0/1". */
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router'
import ResultsPage from './ResultsPage'

const calls = vi.hoisted(() => ({ detail: 0 }))
vi.mock('../app/inference', () => ({ useInference: () => ({ enabled: false }), syncInferenceFromBackend: vi.fn(async () => {}), toggleInferenceEnabled: vi.fn() }))
vi.mock('../app/auth', () => ({ useCanAdminister: () => false }))
vi.mock('../app/models/names', () => ({ useModelNames: () => () => 'OCR', runTitle: () => 'OCR' }))
vi.mock('../lib/api', () => ({
  apiGet: vi.fn(async (path: string) => {
    if (path === '/projects') return { items: [{ id: 1, name: 'Archive', pages_count: 1 }] }
    const running = {
      id: 24, project_id: 1, state: 'running', engine: 'ocr', provider: 'local', adapter_id: 'local-ocr',
      total_pages: 1, completed_pages: 0, failed_pages: 0, created_at: '2026-10-16T10:58:00',
      items: [{ id: 1, page_id: 6, rel_path: 'page.png', page_status: 'new', state: 'running', detected: 0, inserted: 0, blocks: 0, drafts: 0 }],
    }
    if (path.startsWith('/recognition-runs?')) return { items: [running] }
    if (path.endsWith('/collections')) return { collections: [], unassigned: { key: 'none', pages: 1, with_drafts: 0, by_status: {} }, total: { pages: 1, with_drafts: 0, by_status: {} } }
    calls.detail += 1
    return calls.detail === 1 ? running : { ...running, state: 'finished', completed_pages: 1, items: [{ ...running.items[0], state: 'finished' }] }
  }),
  apiDownload: vi.fn(), apiPost: vi.fn(),
}))
afterEach(cleanup)

it('updates the run picker when the run finishes', async () => {
  render(<MemoryRouter initialEntries={['/risultati?project=1&run=24']}><ResultsPage /></MemoryRouter>)
  const option = await screen.findByRole('option', { name: /#24/ })
  expect(option).toHaveTextContent('0/1')
  await waitFor(() => expect(screen.getByRole('option', { name: /#24/ })).toHaveTextContent('1/1'), { timeout: 6000 })
}, 10000)
