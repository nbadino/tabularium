// @vitest-environment jsdom
/**
 * A tab left on first-run setup after the administrator was created elsewhere
 * goes back through the gate (to the login) instead of showing a dead form.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { ApiError } from '../lib/api'
import SetupPage from './SetupPage'

const auth = vi.hoisted(() => ({ setup: vi.fn(), bootstrap: vi.fn(async () => {}) }))
vi.mock('../app/auth', () => ({
  useAuth: () => ({ setup: auth.setup }),
  bootstrap: auth.bootstrap,
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function fill() {
  fireEvent.change(screen.getByLabelText(/^Nome utente/), { target: { value: 'admin' } })
  fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: 'una-password' } })
  fireEvent.change(screen.getByLabelText(/^Conferma password/), { target: { value: 'una-password' } })
  fireEvent.click(screen.getByRole('button', { name: 'Crea amministratore' }))
}

describe('SetupPage', () => {
  it('re-reads the auth state when setup was already completed', async () => {
    auth.setup.mockRejectedValueOnce(new ApiError('POST', '/auth/setup', 409, '{"detail":"già completato"}'))
    render(<SetupPage />)
    fill()
    await waitFor(() => expect(auth.bootstrap).toHaveBeenCalledTimes(1))
  })

  it('does not promise recovery emails', () => {
    render(<SetupPage />)
    expect(screen.queryByText(/notifiche/i)).not.toBeInTheDocument()
    expect(screen.getByText(/non invia email/)).toBeInTheDocument()
  })
})
