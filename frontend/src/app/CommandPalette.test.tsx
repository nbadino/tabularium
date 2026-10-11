// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { CommandPalette, matchesQuery } from './CommandPalette'

vi.mock('../lib/api', () => ({
  apiGet: vi.fn(async (path: string) =>
    path === '/projects' ? { items: [{ id: 7, name: 'Lloyd 1940', pages_count: 3 }] } : { items: [] },
  ),
}))
vi.mock('./auth', () => ({ useAuth: () => ({ user: { username: 'a', role: 'editor' }, logout: vi.fn() }) }))

afterEach(cleanup)

function Where() {
  const loc = useLocation()
  return <output data-testid="where">{loc.pathname + loc.search}</output>
}

function setup() {
  render(
    <MemoryRouter initialEntries={['/']}>
      <CommandPalette />
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('CommandPalette', () => {
  it('ogni parola deve comparire, in qualunque ordine', () => {
    expect(matchesQuery('Lloyd 1940 apri in Annotazione', '1940 lloyd')).toBe(true)
    expect(matchesQuery('Archivio', 'arch x')).toBe(false)
  })

  it('Ctrl+K apre, la ricerca filtra, Invio porta alla sezione', async () => {
    setup()
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true })
    const input = screen.getByRole('combobox')
    expect(input).toHaveFocus()
    fireEvent.change(input, { target: { value: 'archiv' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByTestId('where')).toHaveTextContent('/archivio')
  })

  it('le frecce scelgono un progetto e lo aprono in Annotazione', async () => {
    setup()
    fireEvent.click(screen.getByRole('button', { name: /Cerca/ }))
    const input = screen.getByRole('combobox')
    fireEvent.change(input, { target: { value: 'lloyd' } })
    await waitFor(() => expect(screen.getByRole('option', { name: /Lloyd 1940/ })).toBeInTheDocument())
    expect(screen.getByRole('option', { name: /Lloyd 1940/ })).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByTestId('where')).toHaveTextContent('/annotazione?project=7')
  })

  it('Escape chiude senza navigare', () => {
    setup()
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true })
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByTestId('where')).toHaveTextContent(/^\/$/)
  })
})
