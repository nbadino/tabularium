// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router'
import { ConfirmProvider } from '../app/confirm'
import DatasetPage from './DatasetPage'

const mockDelete = vi.fn(async (_url: string) => ({ deleted: true }))
const mockPost = vi.fn(async (_url: string, _body?: unknown) => ({
  dataset_dir: '/tmp/ds',
  built_at: '2026-11-22T10:00:00Z',
  split: { ratio: 0.8, seed: 42 },
  pages: { with_blocks: 1, train: 1, val: 0 },
  crops_generated: 2,
  counts: { text: { train: 2, val: 0 } },
  files: [],
  warnings: [],
  sample_lines: {},
}))

vi.mock('../lib/api', () => ({
  apiGet: vi.fn(async (path: string) => {
    if (path === '/projects') return { items: [{ id: 1, name: 'Registro' }] }
    if (path === '/system/model-adapters') return { items: [] }
    if (path.endsWith('/datasets')) {
      return {
        built: true,
        report: {
          dataset_dir: '/tmp/ds',
          built_at: '2026-11-22T09:00:00Z',
          split: { ratio: 0.9, seed: 42 },
          pages: { with_blocks: 1, train: 1, val: 0 },
          crops_generated: 1,
          counts: {},
          files: [],
          warnings: [],
          sample_lines: {},
        },
      }
    }
    if (path.endsWith('/workflow')) return { total_pages: 1, approved_pages: 1, counts: { approved: 1 }, next_page: { id: 2 } }
    if (path.endsWith('/pages')) return { items: [] }
    if (path.endsWith('/collections')) return { collections: [], unassigned: { pages: 0 } }
    return {}
  }),
  apiPost: (url: string, body?: unknown) => mockPost(url, body),
  apiDelete: (url: string) => mockDelete(url),
}))

afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('DatasetPage', () => {
  it('uses singular Italian "1 pagina approvata", Confirm modal for overwrite, and guarded delete', async () => {
    render(
      <MemoryRouter>
        <ConfirmProvider>
          <DatasetPage />
        </ConfirmProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByText('pagina approvata')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Apri lo Studio' })).toHaveAttribute('href', '/progetti/1/studio?page=2')

    fireEvent.click(screen.getByRole('button', { name: 'Costruisci dataset' }))
    expect(await screen.findByRole('dialog', { name: 'Sovrascrivere il dataset esistente?' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Annulla' }))
    expect(mockPost).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Elimina dataset' }))
    const delDialog = await screen.findByRole('dialog', { name: 'Eliminare il dataset costruito?' })
    fireEvent.click(within(delDialog).getByRole('button', { name: 'Elimina dataset' }))
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith('/projects/1/datasets'))
  })
})
