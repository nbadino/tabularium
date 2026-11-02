// @vitest-environment jsdom
/**
 * The Pages tab leads with the one thing the project needs next, shows the pages
 * as thumbnails, and keeps the methodological tools (protocol, pilot, activity)
 * on the Advanced page instead of in the way.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import ProjectDetailPage from './ProjectDetailPage'

const state = vi.hoisted(() => ({
  pages: [] as Array<Record<string, unknown>>,
  posted: [] as Array<{ path: string; body: unknown }>,
}))

vi.mock('../lib/api', () => ({
  apiGet: vi.fn(async (path: string) => {
    if (path === '/projects/5') return { id: 5, name: 'Registri', archive_dir: null, pages_count: state.pages.length }
    if (path === '/projects/5/pages') return { items: state.pages }
    if (path === '/system/prefill-engines') return { recommended: 'ocr', ocr: { available: true }, model: { available: false } }
    return { items: [] }
  }),
  apiPost: vi.fn(async (path: string, body: unknown) => {
    state.posted.push({ path, body })
    return { id: 77, project_id: 5 }
  }),
  apiPatch: vi.fn(),
  apiPut: vi.fn(),
  apiDelete: vi.fn(),
}))
vi.mock('../app/auth', () => ({ useAuth: () => ({ enabled: false, user: null }), useCanAdminister: () => true }))
vi.mock('../app/inference', () => ({ useInference: () => ({ enabled: true, available: false }) }))

const page = (id: number, status: string, blocks = 0, drafts = 0) => ({
  id, project_id: 5, rel_path: `p${id}.png`, status, blocks, drafts, width: 100, height: 100,
})

function Where() {
  const { pathname, search } = useLocation()
  return <output data-testid="where">{pathname + search}</output>
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/progetti/5']}>
      <Routes>
        <Route path="/progetti/:id" element={<ProjectDetailPage />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  state.pages = []
  state.posted = []
})

describe('ProjectDetailPage — Pages tab', () => {
  it('an empty project is a place to drop pages', async () => {
    renderPage()
    expect(await screen.findByText('Trascina qui immagini o PDF')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Scegli i file' })).toBeInTheDocument()
  })

  it('recognizes the unread pages in one click and opens the result', async () => {
    state.pages = [page(1, 'new'), page(2, 'new'), page(3, 'approved', 2)]
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Riconosci 2 pagine' }))
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/progetti/5/riconoscimenti/77'))
    expect(state.posted[0].path).toBe('/projects/5/recognition-runs')
    expect(state.posted[0].body).toMatchObject({ page_ids: [1, 2], engine: 'ocr' })
  })

  it('shows thumbnails first and the register on request', async () => {
    state.pages = [page(1, 'new', 3, 3)]
    renderPage()
    expect(await screen.findByRole('link', { name: /p1\.png/ })).toHaveAttribute('href', '/progetti/5/studio?page=1')
    expect(screen.queryByText('Registro pagine')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Tabella' }))
    expect(await screen.findByText('Registro pagine')).toBeInTheDocument()
  })

  it('keeps protocol, pilot and activity on the Advanced page', async () => {
    state.pages = [page(1, 'new')]
    renderPage()
    await screen.findByRole('button', { name: 'Riconosci 1 pagina' })
    expect(screen.queryByText(/Protocollo studio/)).not.toBeInTheDocument()
    expect(screen.queryByText('Pilot iniziale')).not.toBeInTheDocument()
    expect(screen.queryByText('Attività recente')).not.toBeInTheDocument()
    const menu = screen.getByText('Altre azioni').closest('details') as HTMLElement
    expect(within(menu).getByRole('link', { name: 'Avanzate' })).toHaveAttribute('href', '/progetti/5/avanzate')
  })
})
