// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router'
import { setLocale } from '../i18n'
import ConsensusPanel from './ConsensusPanel'

// An editor on the Lloyd's instance: the admin saved OCRia-1 and Gemini 4 Argon with
// their own keys. The editor has added a key for Argon only.
const state = { ocriaKey: false }
const puts: { path: string; body: unknown }[] = []
const deletes: string[] = []
vi.mock('../lib/api', () => ({
  apiGet: vi.fn(async (path: string) => {
    if (path === '/consensus/profiles') {
      return { items: [
        { id: 1, name: 'Gemini 4 Argon', model_adapter_id: 'gemini-argon', served_model_name: 'gemini-4-argon', key_required: true, has_key: true },
        { id: 3, name: 'OCRia-1', model_adapter_id: 'llamaindex-ocria-1', served_model_name: 'ocria-1', key_required: true, has_key: state.ocriaKey },
      ] }
    }
    if (path.endsWith('/consensus-runs')) return { items: [] }
    return {}
  }),
  apiPost: vi.fn(),
  apiPut: vi.fn(async (path: string, body: unknown) => {
    puts.push({ path, body })
    state.ocriaKey = true
    return { id: 3, key_required: true, has_key: true }
  }),
  apiDelete: vi.fn(async (path: string) => {
    deletes.push(path)
    return undefined
  }),
}))
vi.mock('../app/confirm', () => ({ useConfirm: () => async () => true }))

afterEach(() => cleanup())

describe('ConsensusPanel: each user adds their own key', () => {
  it('a model without the user’s key cannot be picked until they add one, and the key is never shown', async () => {
    setLocale('it')
    render(<MemoryRouter><ConsensusPanel projectId={4} pageIds={[1, 2]} canManage={false} currentModel="" /></MemoryRouter>)
    const ocria = (await screen.findByText('OCRia-1')).closest('li')!
    const argon = screen.getByText('Gemini 4 Argon').closest('li')!
    expect(within(argon).getByRole('checkbox')).toBeEnabled()
    expect(within(argon).getByText('La tua chiave: aggiunta')).toBeInTheDocument()
    expect(within(ocria).getByRole('checkbox')).toBeDisabled()
    expect(within(ocria).getByText('Aggiungi la tua chiave API per usarlo')).toBeInTheDocument()

    await userEvent.click(within(ocria).getByRole('button', { name: 'Aggiungi la tua chiave' }))
    const input = within(ocria).getByLabelText('La tua chiave API per OCRia-1')
    expect(input).toHaveAttribute('type', 'password')
    await userEvent.type(input, 'llx-mine')
    await userEvent.click(within(ocria).getByRole('button', { name: 'Salva la chiave' }))

    expect(puts).toEqual([{ path: '/consensus/profiles/3/key', body: { api_key: 'llx-mine' } }])
    const updated = (await screen.findAllByText('La tua chiave: aggiunta')).map((el) => el.closest('li'))
    expect(updated).toHaveLength(2)
    expect(within(screen.getByText('OCRia-1').closest('li')!).getByRole('checkbox')).toBeEnabled()
    expect(document.body.textContent).not.toContain('llx-mine')

    await userEvent.click(within(screen.getByText('OCRia-1').closest('li')!).getByRole('button', { name: 'Rimuovi' }))
    expect(deletes).toEqual(['/consensus/profiles/3/key'])
  })
})
