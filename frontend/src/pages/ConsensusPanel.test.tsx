// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router'
import { setLocale } from '../i18n'
import ConsensusPanel from './ConsensusPanel'

// Lloyd's, consensus run 3: two pages, Gemini still reading the second one.
vi.mock('../lib/api', () => ({
  apiGet: vi.fn(async (path: string) => {
    if (path === '/consensus/profiles') return { items: [] }
    if (path.endsWith('/consensus-runs')) {
      return { items: [{
        id: 3, project_id: 4, state: 'running', total: 4, succeeded: 3, failed: 0, pending: 1, error: null,
        models: [{ key: 'p1', profile_id: 1, name: 'Gemini 4 Argon' }, { key: 'p3', profile_id: 3, name: 'OCRia-1' }],
      }] }
    }
    if (path.endsWith('/consensus-runs/3/pages')) {
      return { items: [
        { page_id: 374, name: 'zip/files/LDI_1920_Apr1_Page_013.tif', states: { p1: 'finished', p3: 'finished' }, errors: {},
          agreement: 0.92, open: { digits: 8, text: 21, missing: 24 } },
        { page_id: 372, name: 'zip/files/LDI_1920_Apr1_Page_014.tif', states: { p1: 'running', p3: 'finished' }, errors: {},
          agreement: null, open: null },
      ] }
    }
    return {}
  }),
  apiPost: vi.fn(),
  apiDelete: vi.fn(),
}))

afterEach(() => cleanup())

describe('ConsensusPanel', () => {
  it('says page by page whether the comparison worked, with a link to each page', async () => {
    setLocale('it')
    render(<MemoryRouter><ConsensusPanel projectId={4} pageIds={[1]} canManage={false} currentModel="" /></MemoryRouter>)
    const pages = await screen.findByTestId('run-pages')
    const [done, going] = within(pages).getAllByRole('listitem')
    expect(done).toHaveTextContent('LDI_1920_Apr1_Page_013.tif')
    expect(done).toHaveTextContent('✓ ✓')
    expect(done).toHaveTextContent('Accordo 92%')
    expect(done).toHaveTextContent('da controllare: 8 numeri · 21 parole · 24 mancanti')
    expect(within(done).getByRole('link', { name: 'Apri ›' })).toHaveAttribute('href', expect.stringContaining('page=374'))
    expect(going).toHaveTextContent('… ✓')
    expect(going).toHaveTextContent('in attesa del secondo modello')
  })

  it('shows the progress of each model and lets the pages already read be used while it runs', async () => {
    setLocale('it')
    render(<MemoryRouter><ConsensusPanel projectId={4} pageIds={[1]} canManage={false} currentModel="" /></MemoryRouter>)
    const progress = await screen.findByTestId('model-progress')
    expect(progress).toHaveTextContent('Gemini 4 Argon 1/2')
    expect(progress).toHaveTextContent('OCRia-1 2/2')
    const adopt = screen.getByTestId('adopt-run')
    expect(adopt).toHaveTextContent('solo le pagine già lette')
  })

  it('always offers a button to the Studio, on the first compared page with cells left', async () => {
    setLocale('it')
    render(<MemoryRouter><ConsensusPanel projectId={4} pageIds={[1]} canManage={false} currentModel="" /></MemoryRouter>)
    await screen.findByTestId('run-pages')
    const button = screen.getByTestId('check-in-studio')
    expect(button).toHaveTextContent('Controlla nello Studio')
    expect(button).toHaveAttribute('href', expect.stringContaining('page=374'))
    expect(screen.getByText(/LDI_1920_Apr1_Page_013\.tif: la prima pagina confrontata/)).toBeInTheDocument()
    const rows = within(screen.getByTestId('run-pages')).getAllByRole('listitem')
    expect(within(rows[1]).getByRole('link', { name: 'Apri ›' })).toHaveAttribute('href', expect.stringContaining('page=372'))
  })
})
