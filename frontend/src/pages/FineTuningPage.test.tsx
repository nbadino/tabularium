// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router'
import FineTuningPage from './FineTuningPage'
import { apiGet } from '../lib/api'

vi.mock('../lib/api', async (importOriginal) => ({ ...await importOriginal<typeof import('../lib/api')>(), apiGet: vi.fn() }))
let built = false
let fail = false
beforeEach(() => {
  built = false
  fail = false
  vi.mocked(apiGet).mockImplementation(async (path) => {
    if (path === '/projects') return { items: [{ id: 1, name: 'Registro', pages_count: 3 }] }
    if (path.endsWith('/workflow')) return { total_pages: 3, approved_pages: 0, counts: { new: 3 }, next_page: { id: 4 } }
    if (path.endsWith('/datasets')) {
      if (fail) throw new Error('Rete non disponibile')
      return { built, report: built ? { pages: { with_blocks: 1 } } : null }
    }
    return { active: false, run: null }
  })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('con sole bozze propone un campione corretto e mantiene visibili tutti gli strumenti', async () => {
  render(<MemoryRouter><FineTuningPage /></MemoryRouter>)
  await screen.findByRole('heading', { name: 'Prepara un campione corretto' })
  expect(screen.getByRole('link', { name: 'Apri lo Studio' })).toHaveAttribute('href', '/annotazione?project=1&page=4')
  expect(screen.getByRole('link', { name: /2 · Addestra/ })).toHaveAttribute('href', '/training')
  expect(screen.getByRole('link', { name: /3 · Valuta la qualità/ })).toBeInTheDocument()
  expect(screen.getByText('0 pagine approvate')).toBeInTheDocument()
})

it('con dati utili propone la configurazione del training senza richiedere tutto il corpus approvato', async () => {
  built = true
  render(<MemoryRouter><FineTuningPage /></MemoryRouter>)
  await screen.findByRole('heading', { name: 'Configura l’addestramento' })
  expect(screen.getByText('Dataset disponibile')).toBeInTheDocument()
})

it('un errore nel caricamento non viene descritto come dati mancanti', async () => {
  fail = true
  render(<MemoryRouter><FineTuningPage /></MemoryRouter>)
  await screen.findByRole('button', { name: 'Riprova a caricare lo stato' })
  expect(screen.queryByRole('heading', { name: 'Prepara un campione corretto' })).not.toBeInTheDocument()
  expect(screen.queryByText('Nessuna run registrata')).not.toBeInTheDocument()
})
