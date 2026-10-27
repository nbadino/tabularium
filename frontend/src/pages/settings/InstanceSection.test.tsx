// @vitest-environment jsdom
/**
 * OIDC is configured by the administrator from this panel, and the client secret is
 * write-only: it is sent when typed, never rendered back.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router'
import InstanceSection from './InstanceSection'
import { apiGet, apiPut } from '../../lib/api'

vi.mock('../../app/auth', () => ({
  useAuth: () => ({ enabled: true }),
}))

const baseSettings = {
  instance_name: 'Archivio',
  allow_registration: false,
  default_new_user_role: 'editor',
  oidc_enabled: true,
  oidc_issuer: 'https://idp.unito.it',
  oidc_client_id: 'client-1',
  oidc_scopes: 'openid profile email',
  oidc_allowed_domains: ['unito.it'],
  oidc_button_label: 'Accedi con Unito',
  oidc_client_secret_set: true,
}

vi.mock('../../lib/api', () => ({
  apiGet: vi.fn(async () => baseSettings),
  apiPut: vi.fn(async (_path: string, payload: Record<string, unknown>) => ({
    ...baseSettings,
    ...payload,
    oidc_client_secret_set: true,
  })),
}))

const renderSection = () =>
  render(
    <MemoryRouter>
      <InstanceSection isAdmin />
    </MemoryRouter>,
  )

beforeEach(() => vi.clearAllMocks())
afterEach(cleanup)

describe('InstanceSection — OIDC', () => {
  it('salva il secret una volta sola e i domini come elenco', async () => {
    const user = userEvent.setup()
    renderSection()
    await screen.findByLabelText(/^Issuer/)

    await user.type(await screen.findByLabelText(/^Client secret/), 'nuovo-secret')
    const domains = screen.getByLabelText(/^Domini ammessi/)
    await user.clear(domains)
    await user.type(domains, 'unito.it, ateneo.it')
    await user.click(screen.getByRole('button', { name: /Salva impostazioni/i }))

    await waitFor(() => expect(apiPut).toHaveBeenCalled())
    const payload = vi.mocked(apiPut).mock.calls[0][1] as Record<string, unknown>
    expect(payload.oidc_client_secret).toBe('nuovo-secret')
    expect(payload.oidc_allowed_domains).toEqual(['unito.it', 'ateneo.it'])
    expect(payload).not.toHaveProperty('oidc_client_secret_set')
  })

  it('non lascia salvare un OIDC attivo senza issuer', async () => {
    const user = userEvent.setup()
    vi.mocked(apiGet).mockResolvedValueOnce({ ...baseSettings, oidc_issuer: '' })
    renderSection()
    const issuer = await screen.findByLabelText(/^Issuer/)
    await user.clear(issuer)
    expect(await screen.findByText(/servono issuer e client ID/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Salva impostazioni/i })).toBeDisabled()
  })
})
