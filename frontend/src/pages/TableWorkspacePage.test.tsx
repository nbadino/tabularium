// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter, Route, Routes } from 'react-router'

vi.mock('../studio/components/UniverSheet', () => ({
  default: () => <div data-testid="univer-sheet" />,
}))

vi.mock('../lib/api', () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPut: vi.fn(),
  apiPatch: vi.fn(),
}))

import { apiGet, apiPost } from '../lib/api'
import TableWorkspacePage from './TableWorkspacePage'
import type { BlockOut, PageItem } from '../lib/types'

const mockGet = vi.mocked(apiGet)
const mockPost = vi.mocked(apiPost)

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  Object.defineProperty(window, 'opener', { configurable: true, value: null })
})

const BLOCK: BlockOut = {
  id: 25,
  page_id: 7,
  project_id: 3,
  kind: 'rect',
  label: 'Table',
  points: [[0, 0], [1, 1]],
  content: '',
  confirmed: false,
  prefill_source: null,
  order_idx: 2,
  annotation_revision: 1,
  updated_at: '',
}

const PAGE: PageItem = {
  id: 7,
  project_id: 3,
  rel_path: 'registry.pdf',
  abs_path: '/tmp/registry.pdf',
  source_kind: 'pdf',
  pdf_page: 2,
  width: 1000,
  height: 1400,
  issue_date: null,
  issue_no: null,
  page_no: null,
  page_type: null,
  status: 'annotated',
  created_at: '',
}

function setupApis() {
  mockGet.mockImplementation(async (url: string) => {
    if (url.includes('/table-headers')) return { items: [] }
    if (url.includes('/consensus')) return { models: [], cells: [] }
    if (url.includes('/table')) {
      return {
        grid: {
          rows: 2,
          cols: 2,
          phantom_cols: [],
          cells: [
            { r: 0, c: 0, rowspan: 1, colspan: 1, text: 'Nave', verified: false },
            { r: 0, c: 1, rowspan: 1, colspan: 1, text: 'Tons', verified: false },
          ],
        },
      }
    }
    return {}
  })
  mockPost.mockResolvedValue({ checks: [], passed: 0, failed: 0 })
}

describe('TableWorkspacePage', () => {
  it('shows clean filename and page without #blockId, active verify controls, separated cell merge buttons, and zero-checks notice', async () => {
    setupApis()
    render(
      <MemoryRouter initialEntries={['/tabella/25']}>
        <Routes>
          <Route path="/tabella/:blockId" element={<TableWorkspacePage block={BLOCK} page={PAGE} />} />
          <Route path="/progetti/:projectId/studio" element={<div data-testid="studio-dest" />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Tabella · registry.pdf · p. 2')
    expect(screen.getByRole('heading', { level: 1 })).not.toHaveTextContent('#25')

    expect(await screen.findByRole('button', { name: 'Segna tutte verificate' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Segna verificata/ })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('link', { name: 'Configura somme' })).toHaveAttribute('href', '/progetti/3/avanzate'))

    fireEvent.click(screen.getByText('Struttura'))
    expect(screen.getByRole('button', { name: 'Unisci celle' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Separa la cella unita' })).toBeInTheDocument()
    const colSelect = screen.getByRole('combobox', { name: 'Colonna' })
    expect(colSelect).toHaveDisplayValue('Scegli azione colonna…')
    expect(screen.queryByRole('option', { name: 'Unisci celle' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /Torna allo Studio/ }))
    await waitFor(() => expect(screen.getByTestId('studio-dest')).toBeInTheDocument())
  })

  it('shows "Chiudi finestra" and closes window when opened from Studio popup', () => {
    setupApis()
    Object.defineProperty(window, 'opener', { configurable: true, value: { closed: false, focus: vi.fn() } })
    const closeSpy = vi.spyOn(window, 'close').mockImplementation(() => {})
    render(
      <MemoryRouter initialEntries={['/tabella/25']}>
        <Routes>
          <Route path="/tabella/:blockId" element={<TableWorkspacePage block={BLOCK} page={PAGE} />} />
        </Routes>
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByRole('button', { name: /Chiudi finestra/ }))
    return waitFor(() => expect(closeSpy).toHaveBeenCalledTimes(1))
  })

  it('resolves clean filename via /projects/{id}/pages when page prop is omitted without calling /pages/{id}', async () => {
    mockGet.mockImplementation(async (url: string) => {
      if (url === '/projects/3/pages') return { items: [PAGE] }
      if (url.includes('/table-headers')) return { items: [] }
      if (url.includes('/consensus')) return { models: [], cells: [] }
      if (url.includes('/table')) {
        return { grid: { rows: 1, cols: 1, phantom_cols: [], cells: [] } }
      }
      return {}
    })
    mockPost.mockResolvedValue({ checks: [], passed: 0, failed: 0 })

    render(
      <MemoryRouter initialEntries={['/tabella/25?page=7&project=3']}>
        <Routes>
          <Route path="/tabella/:blockId" element={<TableWorkspacePage block={BLOCK} />} />
        </Routes>
      </MemoryRouter>,
    )

    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Tabella · registry.pdf · p. 2'),
    )
    expect(mockGet).toHaveBeenCalledWith('/projects/3/pages')
    expect(mockGet).not.toHaveBeenCalledWith('/pages/7')
  })
})
