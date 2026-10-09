// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import Layout from './Layout'

vi.mock('../lib/api', () => ({ apiGet: vi.fn(() => new Promise(() => {})) }))
vi.mock('./inference', () => ({
  syncInferenceFromBackend: vi.fn(async () => {}),
  useInference: () => ({ enabled: false, available: false, url: '', model: '', adapterId: '', provider: null }),
}))
vi.mock('./models/names', () => ({ useModelNames: () => () => null }))
vi.mock('./auth', () => ({ useAuth: () => ({ user: null }), logout: vi.fn() }))

function Where() {
  const { pathname, search } = useLocation()
  return <output data-testid="where">{pathname + search}</output>
}

describe('Layout', () => {
  afterEach(cleanup)

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
    fireEvent.click(screen.getByText('Risultati'))
    expect(screen.getByTestId('where')).toHaveTextContent(/^\/risultati$/)
    fireEvent.click(screen.getByText('Annotazione'))
    expect(screen.getByTestId('where')).toHaveTextContent('/annotazione?project=1&page=7')
  })
})
