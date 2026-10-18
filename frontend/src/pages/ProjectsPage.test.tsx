// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter, Route, Routes } from 'react-router'
import ProjectsPage from './ProjectsPage'
import { apiGet, apiPost } from '../lib/api'

vi.mock('../lib/api', () => ({ apiGet: vi.fn(), apiPost: vi.fn() }))
vi.mock('../app/auth', () => ({ useCanAdminister: () => false, useAuth: () => ({ enabled: false, user: null }) }))

afterEach(() => { cleanup(); vi.clearAllMocks() })

it('l’elenco si filtra senza mostrare il modulo di creazione', async () => {
  vi.mocked(apiGet).mockResolvedValue({ items: [
    { id: 1, name: 'Registro 1904', pages_count: 4, created_at: '2026-10-17' },
    { id: 2, name: 'Registro 1905', pages_count: 6, created_at: '2026-10-17' },
  ] })
  render(<MemoryRouter><ProjectsPage /></MemoryRouter>)
  await screen.findByText('Registro 1904')
  expect(screen.queryByRole('textbox', { name: 'Nome' })).not.toBeInTheDocument()
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: '1905' } })
  expect(screen.queryByText('Registro 1904')).not.toBeInTheDocument()
  expect(screen.getByText('Registro 1905')).toBeInTheDocument()
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'nessuno' } })
  fireEvent.click(screen.getByRole('button', { name: 'Cancella ricerca' }))
  expect(screen.getByText('Registro 1904')).toBeInTheDocument()
})

it('il link nuovo progetto apre il dialogo e la creazione porta al progetto', async () => {
  vi.mocked(apiGet).mockResolvedValue({ items: [] })
  vi.mocked(apiPost).mockResolvedValue({ id: 17 })
  render(<MemoryRouter initialEntries={['/progetti?nuovo=1']}><Routes>
    <Route path="/progetti" element={<ProjectsPage />} />
    <Route path="/progetti/:id" element={<p>Progetto aperto</p>} />
  </Routes></MemoryRouter>)
  expect(screen.getByRole('dialog', { name: 'Nuovo progetto' })).toBeInTheDocument()
  fireEvent.change(screen.getByRole('textbox', { name: 'Nome' }), { target: { value: '  Registro 1904  ' } })
  fireEvent.click(screen.getByRole('button', { name: 'Crea progetto e continua' }))
  await screen.findByText('Progetto aperto')
  expect(apiPost).toHaveBeenCalledWith('/projects', { name: 'Registro 1904', archive_dir: null })
})
