// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router'
import UsersPage from './UsersPage'

const mockPost = vi.fn(async (_url: string, _body?: unknown) => ({}))

vi.mock('../app/auth', () => ({
  useAuth: () => ({ user: { id: 1, username: 'admin', role: 'admin', active: true } }),
}))

vi.mock('../lib/api', () => ({
  apiGet: vi.fn(async () => [
    {
      id: 1,
      username: 'admin',
      role: 'admin',
      active: true,
      must_change_password: false,
      created_at: '2026-11-22T08:00:00Z',
    },
    {
      id: 2,
      username: 'audit_editor',
      role: 'editor',
      active: true,
      must_change_password: true,
      created_at: '2026-11-22T09:00:00Z',
    },
  ]),
  apiPost: (url: string, body?: unknown) => mockPost(url, body),
  apiPatch: vi.fn(async () => ({})),
  apiDelete: vi.fn(async () => ({})),
}))

afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('UsersPage', () => {
  it('aligns users in a real table with full usernames, localized roles, and inline empty validation', async () => {
    render(<MemoryRouter><UsersPage /></MemoryRouter>)

    const table = await screen.findByRole('table')
    expect(table).toBeInTheDocument()
    expect(Array.from(table.querySelectorAll('th')).every((th) => !th.classList.contains('lbl'))).toBe(true)
    expect(screen.getByText('audit_editor')).toBeInTheDocument()
    expect(screen.getAllByRole('option', { name: 'Amministratore' }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('option', { name: 'Lettore' }).length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: 'Nuovo utente' }))
    fireEvent.click(screen.getByRole('button', { name: 'Crea utente' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Inserisci un nome utente.')
    expect(mockPost).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Mostra password' })).toBeInTheDocument()
  })
})
