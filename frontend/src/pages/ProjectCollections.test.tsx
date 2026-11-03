// @vitest-environment jsdom
/**
 * Collections on the Pages tab: a column that filters the wall, counts pages by
 * state, moves selected pages, and never deletes pages without the user saying so.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import ProjectDetailPage from './ProjectDetailPage'

const state = vi.hoisted(() => ({
  posted: [] as Array<{ path: string; body: unknown }>,
  deleted: [] as string[],
}))

const page = (id: number, collection_id: number | null, status = 'new') => ({
  id, project_id: 5, rel_path: `p${id}.png`, status, blocks: 0, drafts: 0, width: 100, height: 100, collection_id,
})
const pages = [page(1, 1), page(2, 1, 'approved'), page(3, 2), page(4, null)]
const overview = {
  collections: [
    { id: 1, name: 'Registri', created_at: '', updated_at: '', pages: 2, with_drafts: 0, by_status: { new: 1, approved: 1 } },
    { id: 2, name: 'Lettere', created_at: '', updated_at: '', pages: 1, with_drafts: 0, by_status: { new: 1 } },
  ],
  unassigned: { key: 'none', pages: 1, with_drafts: 0, by_status: { new: 1 } },
  total: { pages: 4, with_drafts: 0, by_status: { new: 3, approved: 1 } },
}

vi.mock('../lib/api', () => ({
  apiGet: vi.fn(async (path: string) => {
    if (path === '/projects/5') return { id: 5, name: 'Lloyds', archive_dir: null, pages_count: 4 }
    if (path === '/projects/5/pages') return { items: pages }
    if (path === '/projects/5/collections') return overview
    if (path === '/system/prefill-engines') return { recommended: 'ocr', ocr: { available: true }, model: { available: false } }
    return { items: [] }
  }),
  apiPost: vi.fn(async (path: string, body: unknown) => {
    state.posted.push({ path, body })
    return { moved: 1 }
  }),
  apiPatch: vi.fn(),
  apiPut: vi.fn(),
  apiDelete: vi.fn(async (path: string) => { state.deleted.push(path); return {} }),
}))
vi.mock('../app/auth', () => ({ useAuth: () => ({ enabled: false, user: null }), useCanAdminister: () => true }))
vi.mock('../app/inference', () => ({ useInference: () => ({ enabled: true, available: false }) }))

function Where() {
  const { search } = useLocation()
  return <output data-testid="search">{search}</output>
}

function renderPage(entry = '/progetti/5') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/progetti/:id" element={<><ProjectDetailPage /><Where /></>} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  state.posted = []
  state.deleted = []
})

describe('Collections on the Pages tab', () => {
  it('lists collections with counts by state and filters the wall', async () => {
    renderPage()
    const column = await screen.findByRole('navigation', { name: 'Collezioni' })
    expect(within(column).getByText('1 nuove · 1 verificate')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: /p\d\.png/ })).toHaveLength(4)
    fireEvent.click(within(column).getByRole('button', { name: /Lettere/ }))
    await waitFor(() => expect(screen.getByTestId('search')).toHaveTextContent('collezione=2'))
    expect(screen.getAllByRole('link', { name: /p\d\.png/ })).toHaveLength(1)
    fireEvent.click(within(column).getByRole('button', { name: /Senza collezione/ }))
    await waitFor(() => expect(screen.getByTestId('search')).toHaveTextContent('collezione=none'))
    expect(screen.getByRole('link', { name: /p4\.png/ })).toBeInTheDocument()
  })

  it('moves selected pages to another collection', async () => {
    renderPage()
    fireEvent.click(await screen.findByLabelText('Seleziona p1.png'))
    fireEvent.click(screen.getByLabelText('Seleziona p4.png'))
    expect(screen.getByText('2 pagine selezionate')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Sposta in…'))
    const menu = screen.getByText('Sposta in…').closest('details') as HTMLElement
    fireEvent.click(within(menu).getByRole('button', { name: 'Lettere' }))
    await waitFor(() => expect(state.posted).toContainEqual({ path: '/projects/5/pages/collection', body: { page_ids: [1, 4], collection_id: 2 } }))
    expect(await screen.findByText('2 pagine spostate in «Lettere».')).toBeInTheDocument()
  })

  it('asks what happens to the pages before deleting a collection', async () => {
    renderPage()
    const column = await screen.findByRole('navigation', { name: 'Collezioni' })
    const rowMenu = within(column).getByText('Azioni per Registri').closest('details') as HTMLElement
    fireEvent.click(within(rowMenu).getByText('Azioni per Registri'))
    fireEvent.click(within(rowMenu).getByRole('button', { name: 'Elimina…' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText(/Spostale in «Senza collezione»/)).toBeChecked()
    fireEvent.click(within(dialog).getByLabelText(/Eliminale insieme alla collezione/))
    const confirm = within(dialog).getByRole('button', { name: 'Elimina collezione e 2 pagine' })
    expect(confirm).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Per confermare, scrivi Registri'), { target: { value: 'Registri' } })
    fireEvent.click(confirm)
    await waitFor(() => expect(state.deleted).toEqual(['/projects/5/collections/1?pages=delete&confirm=true']))
  })

  it('moving pages back by default keeps them', async () => {
    renderPage()
    const column = await screen.findByRole('navigation', { name: 'Collezioni' })
    const rowMenu = within(column).getByText('Azioni per Lettere').closest('details') as HTMLElement
    fireEvent.click(within(rowMenu).getByText('Azioni per Lettere'))
    fireEvent.click(within(rowMenu).getByRole('button', { name: 'Elimina…' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Elimina la collezione' }))
    await waitFor(() => expect(state.deleted).toEqual(['/projects/5/collections/2?pages=unassign']))
  })
})
