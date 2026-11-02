// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import Layout from './Layout'

vi.mock('../lib/api', () => ({ apiGet: vi.fn(() => new Promise(() => {})) }))
const inferenceState = vi.hoisted(() => ({
  value: { enabled: false, available: false, configured: false, url: '', model: '', adapterId: '', provider: null as string | null },
}))
vi.mock('./inference', () => ({
  syncInferenceFromBackend: vi.fn(async () => {}),
  useInference: () => inferenceState.value,
}))
vi.mock('./models/names', () => ({ useModelNames: () => () => null }))
vi.mock('./auth', () => ({ useAuth: () => ({ user: null }), logout: vi.fn() }))

function Where() {
  const { pathname, search } = useLocation()
  return <output data-testid="where">{pathname + search}</output>
}

describe('Layout', () => {
  afterEach(() => {
    cleanup()
    inferenceState.value = { enabled: false, available: false, configured: false, url: '', model: '', adapterId: '', provider: null }
  })

  const renderAt = (path: string) => render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route element={<Layout />}><Route path="*" element={<Where />} /></Route></Routes>
    </MemoryRouter>,
  )

  it('shows no model status before a model is chosen', () => {
    inferenceState.value = { ...inferenceState.value, enabled: true, model: 'MonkeyOCRv2', url: 'http://127.0.0.1:8888/v1' }
    renderAt('/')
    expect(screen.queryByText('Non disponibile')).not.toBeInTheDocument()
    expect(screen.queryByTitle('Cambia modello o esecuzione')).not.toBeInTheDocument()
  })

  it('names the app Tabularium', () => {
    renderAt('/')
    expect(screen.getByText('Tabularium')).toBeInTheDocument()
    expect(screen.queryByText('Lab')).not.toBeInTheDocument()
  })

  it('the main navigation is Progetti and Modelli', () => {
    renderAt('/progetti')
    const nav = screen.getByRole('navigation', { name: 'Sezioni' })
    expect(within(nav).getByRole('link', { name: 'Progetti' })).toHaveAttribute('href', '/progetti')
    expect(within(nav).getByRole('link', { name: 'Modelli' })).toHaveAttribute('href', '/modelli')
    for (const old of ['Archivio', 'Riconosci', 'Annotazione', 'Fine-tuning']) {
      expect(within(nav).queryByRole('link', { name: old })).not.toBeInTheDocument()
    }
  })

  it('inside a project, its sections are tabs of the project', () => {
    renderAt('/progetti/3/riconoscimenti')
    const nav = screen.getByRole('navigation', { name: 'Progetto' })
    expect(within(nav).getByRole('link', { name: 'Pagine' })).toHaveAttribute('href', '/progetti/3')
    expect(within(nav).getByRole('link', { name: 'Riconoscimenti' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'Fine-tuning' })).toHaveAttribute('href', '/progetti/3/fine-tuning')
    expect(screen.getByRole('link', { name: 'Progetti' })).toHaveAttribute('aria-current', 'page')
  })

  it('the fine-tuning steps sit under the project tab', () => {
    renderAt('/progetti/3/fine-tuning/addestra')
    const steps = screen.getByRole('navigation', { name: 'Percorso di fine-tuning' })
    expect(within(steps).getByRole('link', { name: '2 · Addestra' })).toHaveAttribute('href', '/progetti/3/fine-tuning/addestra')
    expect(within(steps).getByRole('link', { name: '1 · Prepara i dati' })).toHaveAttribute('href', '/progetti/3/fine-tuning/dati')
    expect(within(steps).getAllByRole('link')).toHaveLength(3)
  })

  it('comparing models is part of the evaluation step', () => {
    renderAt('/progetti/3/fine-tuning/confronta')
    const steps = screen.getByRole('navigation', { name: 'Percorso di fine-tuning' })
    expect(within(steps).getByRole('link', { name: '3 · Valuta la qualità' })).toHaveAttribute('aria-current', 'page')
    expect(within(steps).queryByRole('link', { name: 'Benchmark' })).not.toBeInTheDocument()
    expect(within(steps).queryByRole('link', { name: 'Panoramica' })).not.toBeInTheDocument()
  })

  it('the model trial stays under Modelli', () => {
    renderAt('/playground')
    expect(screen.getByRole('link', { name: 'Modelli' })).toHaveAttribute('aria-current', 'page')
  })

})
