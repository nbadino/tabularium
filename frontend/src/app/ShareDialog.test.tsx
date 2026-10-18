import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const h = vi.hoisted(() => ({
  members: [
    { user_id: 1, username: 'owner', email: 'owner@x.it', role: 'owner', active: true },
    { user_id: 2, username: 'bella', email: 'bella@x.it', role: 'editor', active: true },
  ],
  candidates: [{ user_id: 3, username: 'carlo', email: null, role: 'viewer', active: true }],
  calls: [] as Array<{ url: string; body: unknown }>,
}))

vi.mock('../lib/api', () => ({
  apiGet: vi.fn(async (url: string) => {
    if (url.includes('/member-candidates')) return h.candidates
    if (url.includes('/activity')) return { items: [] }
    if (url.includes('/members')) return h.members
    return {}
  }),
  apiPut: vi.fn(async (url: string, body: unknown) => {
    h.calls.push({ url, body })
    return {}
  }),
  apiPost: vi.fn(async () => ({})),
  apiDelete: vi.fn(async () => ({})),
  ApiError: class ApiError extends Error {},
}))

vi.mock('../app/confirm', () => ({ useConfirm: () => async () => true }))

import ShareDialog from './ShareDialog'

afterEach(() => { cleanup(); h.calls.length = 0 })

describe('ShareDialog', () => {
  it('mostra i membri e, per chi amministra, il campo per aggiungere', async () => {
    render(<ShareDialog projectId={1} projectName="Registro" canManage onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('owner')).toBeInTheDocument())
    expect(screen.getByText('bella')).toBeInTheDocument()
    const input = screen.getByLabelText('Nome o email…')
    expect(input).toBeInTheDocument()

    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'car' } })
    const option = await screen.findByText('carlo')
    fireEvent.click(option)
    expect(h.calls).toHaveLength(0)
    fireEvent.change(screen.getByLabelText('Accesso da assegnare'), { target: { value: 'editor' } })
    fireEvent.click(screen.getByRole('button', { name: 'Aggiungi al progetto' }))
    await waitFor(() => expect(h.calls.some((c) => c.url.endsWith('/members/3'))).toBe(true))
    expect(h.calls[h.calls.length - 1]?.body).toMatchObject({ user_id: 3, role: 'editor' })
  })

  it('in sola lettura non offre di aggiungere né di modificare i ruoli', async () => {
    render(<ShareDialog projectId={1} projectName="Registro" canManage={false} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('bella')).toBeInTheDocument())
    expect(screen.queryByLabelText('Nome o email…')).not.toBeInTheDocument()
    // I ruoli sono testo, non menù a tendina.
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })
  it('assegna sola lettura per default e non scrive quando si sceglie una persona', async () => {
    render(<ShareDialog projectId={1} projectName="Registro" canManage onClose={() => {}} />)
    const input = await screen.findByLabelText('Nome o email…')
    await waitFor(() => expect(input).toBeEnabled())
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'car' } })
    fireEvent.click(await screen.findByText('carlo'))
    expect(h.calls).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Aggiungi al progetto' }))
    await waitFor(() => expect(h.calls).toHaveLength(1))
    expect(h.calls[0].body).toMatchObject({ user_id: 3, role: 'viewer' })
  })

})
