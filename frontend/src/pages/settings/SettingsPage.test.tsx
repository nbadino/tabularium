// @vitest-environment jsdom
/**
 * Le impostazioni sono quattro zone, non una colonna di moduli: qui si
 * verifica ciò che quella scelta promette — una zona dominante per volta, la
 * scelta leggibile nell'URL, e i comandi di scrittura riservati all'admin
 * *con* la riga che spiega perché, invece di campi spenti senza motivo.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter, useLocation } from 'react-router'
import SettingsPage from './SettingsPage'

/** Sonda: MemoryRouter non tocca `window.location`, la rotta si legge da qui. */
function LocationProbe() {
  const location = useLocation()
  return <span data-testid="location">{location.pathname}{location.search}</span>
}

const role = { current: 'admin' as 'admin' | 'editor' }

vi.mock('../../app/auth', () => ({
  useAuth: () => ({
    user: { id: 1, username: 'nicolo', role: role.current, email: 'x@y.z', active: true },
    ready: true,
    mode: 'on',
  }),
}))

vi.mock('../../lib/api', () => ({
  apiGet: vi.fn(async (path: string) => {
    if (path === '/settings') {
      return { instance_name: 'Archivio', allow_registration: false, default_new_user_role: 'editor' }
    }
    if (path === '/system/compute-profiles') return []
    if (path === '/system/backup') {
      return { integrity: { ok: true, journal_mode: 'wal', messages: [] }, items: [] }
    }
    if (path === '/system/info') {
      return { app: 'tabularium', version: '0.1.0', data_dir: '/dati', db_path: '/dati/db', schema_version: '14', python: '3.12', platform: 'linux' }
    }
    if (path === '/health') return { status: 'ok', app: 'tabularium', version: '0.1.0' }
    return {}
  }),
  apiPost: vi.fn(async () => ({})),
  apiPut: vi.fn(async () => ({})),
  apiDelete: vi.fn(async () => ({})),
  ApiError: class ApiError extends Error {},
}))

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={['/impostazioni']}>
      <SettingsPage />
      <LocationProbe />
    </MemoryRouter>,
  )

const renderLegacyComputeLink = () =>
  render(
    <MemoryRouter initialEntries={['/impostazioni?s=calcolo']}>
      <SettingsPage />
      <LocationProbe />
    </MemoryRouter>,
  )

beforeEach(() => {
  role.current = 'admin'
})
afterEach(() => cleanup())

describe('SettingsPage', () => {
  it('apre sull’account e mostra una zona sola per volta', async () => {
    renderPage()
    expect(await screen.findByRole('region', { name: 'Il mio account' })).toBeInTheDocument()
    // Le altre zone non sono sotto, in fila: non sono proprio rese.
    expect(screen.queryByRole('region', { name: 'Identità e accesso' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'In uso ora' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Modello e calcolo' })).toBeNull()
  })

  it('manda i vecchi link del calcolo all’hub Modelli', async () => {
    renderLegacyComputeLink()
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/modelli'))
  })

  it('la linguetta cambia zona e la scelta resta nell’URL', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByRole('button', { name: 'Dati e backup' }))

    expect(await screen.findByRole('region', { name: 'Backup del database' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Il mio account' })).toBeNull()
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('s=dati'))
  })

  it('a chi non è admin i comandi mancano, ma il motivo è scritto', async () => {
    role.current = 'editor'
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByRole('button', { name: 'Istanza' }))

    expect(await screen.findByText(/solo un amministratore può cambiarla/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Salva impostazioni' })).toBeNull()
    // Il nome dell'istanza resta leggibile, solo non modificabile.
    expect(screen.getByDisplayValue('Archivio')).toBeDisabled()
  })
})
