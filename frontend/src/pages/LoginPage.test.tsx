// @vitest-environment jsdom
/**
 * The SSO button exists only when an administrator enabled a provider, and a failed
 * round trip comes back as a code that the page translates.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import LoginPage from './LoginPage'

const state = vi.hoisted(() => ({
  oidcEnabled: true,
  oidcButtonLabel: 'Accedi con Unito',
}))

vi.mock('../app/auth', () => ({
  useAuth: () => ({
    instanceName: 'Archivio',
    allowRegistration: false,
    oidcEnabled: state.oidcEnabled,
    oidcButtonLabel: state.oidcButtonLabel,
    login: vi.fn(),
    register: vi.fn(),
  }),
}))

beforeEach(() => {
  state.oidcEnabled = true
  state.oidcButtonLabel = 'Accedi con Unito'
  window.history.pushState({}, '', '/')
})

afterEach(cleanup)

describe('LoginPage — identity provider', () => {
  it('offre il pulsante dell’ateneo quando l’amministratore lo ha attivato', () => {
    render(<LoginPage />)
    const link = screen.getByRole('link', { name: 'Accedi con Unito' })
    expect(link).toHaveAttribute('href', '/api/auth/oidc/start')
  })

  it('non mostra il pulsante se OIDC non è configurato', () => {
    state.oidcEnabled = false
    render(<LoginPage />)
    expect(screen.queryByRole('link', { name: /Unito/ })).not.toBeInTheDocument()
  })

  it('spiega un rifiuto per dominio senza mostrare un codice', () => {
    window.history.pushState({}, '', '/?auth_error=oidc_domain')
    render(<LoginPage />)
    expect(screen.getByRole('alert')).toHaveTextContent(/dominio ammesso/i)
    expect(screen.getByRole('alert')).not.toHaveTextContent('oidc_domain')
  })
})
