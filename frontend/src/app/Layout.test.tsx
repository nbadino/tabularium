// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
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

  it('reports a chosen model that does not answer', () => {
    inferenceState.value = { ...inferenceState.value, enabled: true, configured: true, model: 'MinerU', url: 'https://x.modal.run/v1', provider: 'modal' }
    renderAt('/')
    expect(screen.getByText('Non disponibile')).toBeInTheDocument()
  })

  it('names the app Tabularium', () => {
    renderAt('/')
    expect(screen.getByText('Tabularium')).toBeInTheDocument()
    expect(screen.queryByText('Lab')).not.toBeInTheDocument()
  })

  it('una linguetta riapre la sezione dove la si era lasciata', () => {
    render(
      <MemoryRouter initialEntries={['/annotazione?project=1&page=7']}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="*" element={<Where />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByRole('link', { name: 'Riconosci' }))
    fireEvent.click(screen.getByRole('link', { name: 'Risultati' }))
    expect(screen.getByTestId('where')).toHaveTextContent(/^\/risultati$/)
    fireEvent.click(screen.getByText('Annotazione'))
    expect(screen.getByTestId('where')).toHaveTextContent('/annotazione?project=1&page=7')
  })
  it('keeps the selected recognition run when returning through contextual navigation', () => {
    render(<MemoryRouter initialEntries={['/risultati?project=2&run=24']}>
      <Routes><Route element={<Layout />}><Route path="*" element={<Where />} /></Route></Routes>
    </MemoryRouter>)
    fireEvent.click(screen.getByRole('link', { name: 'Archivio' }))
    fireEvent.click(screen.getByRole('link', { name: 'Riconosci' }))
    fireEvent.click(screen.getByRole('link', { name: 'Risultati' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/risultati?project=2&run=24')
  })

  it.each([
    ['/training', 'Fine-tuning'],
    ['/valutazione', 'Fine-tuning'],
    ['/playground', 'Modelli'],
    ['/archivio', 'Archivio'],
  ])('la sottosezione %s mantiene visibile la sezione %s', (path, label) => {
    render(
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route element={<Layout />}><Route path="*" element={<Where />} /></Route></Routes>
      </MemoryRouter>,
    )
    expect(screen.getByRole('link', { name: label })).toHaveAttribute('aria-current', 'page')
  })

  it('Archive returns to the overview after project creation', () => {
    render(<MemoryRouter initialEntries={['/progetti?nuovo=1']}>
      <Routes><Route element={<Layout />}><Route path="*" element={<Where />} /></Route></Routes>
    </MemoryRouter>)
    fireEvent.click(screen.getByRole('link', { name: 'Riconosci' }))
    fireEvent.click(screen.getByRole('link', { name: 'Archivio' }))
    expect(screen.getByTestId('where')).toHaveTextContent(/^\/archivio$/)
  })

})
