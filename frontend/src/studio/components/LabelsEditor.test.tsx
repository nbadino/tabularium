// @vitest-environment jsdom
/**
 * Editor delle classi: mostra l'origine (pubblica/custom), distingue i default
 * dalle custom, e salva **solo** le custom del progetto.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom/vitest'

vi.mock('../../lib/api', () => ({ apiPut: vi.fn() }))
vi.mock('../../app/models/names', () => ({
  useModelNames: () => (id: string | null | undefined) => id ?? null,
}))

import { apiPut } from '../../lib/api'
import LabelsEditor from './LabelsEditor'
import type { LabelDef } from '../../lib/types'

const mockPut = vi.mocked(apiPut)

function label(patch: Partial<LabelDef>): LabelDef {
  return {
    name: 'X',
    color: '#4a5560',
    short: 'X',
    prompt_kind: 'text',
    prompt: 'p',
    origin: 'custom',
    builtin: false,
    aliases: [],
    known_models: [],
    ...patch,
  }
}

const LABELS: LabelDef[] = [
  label({ name: 'Title', origin: 'public', builtin: true, known_models: ['paddleocr-vl'] }),
  label({ name: 'Issue-date', origin: 'custom', builtin: true, known_models: [] }),
  label({ name: 'Vessel-name', origin: 'custom', builtin: false }),
]

afterEach(() => {
  cleanup()
  mockPut.mockReset()
})

describe('LabelsEditor', () => {
  it('mostra l’origine delle classi e distingue default e custom', () => {
    render(<LabelsEditor projectId={7} labels={LABELS} onSaved={() => {}} />)
    expect(screen.getByText('già nota al modello')).toBeTruthy() // Title, pubblica
    // Issue-date è custom ma di default; Vessel-name è custom del progetto.
    expect(screen.getAllByText('da apprendere').length).toBe(2)
    expect(screen.getAllByText('di default').length).toBe(2)
    // La classe custom è rimovibile; i default no.
    expect(screen.getAllByRole('button', { name: 'Rimuovi' }).length).toBe(1)
  })

  it('aggiunge una classe e salva solo le custom', async () => {
    const user = userEvent.setup()
    const onSaved = vi.fn()
    mockPut.mockResolvedValue({ labels: LABELS } as never)

    render(<LabelsEditor projectId={7} labels={LABELS} onSaved={onSaved} />)

    await user.click(screen.getByRole('button', { name: 'Aggiungi classe' }))
    await user.type(screen.getByPlaceholderText('Es. Vessel-name'), 'Port-of-call')
    await user.type(screen.getByPlaceholderText('VSL'), 'PRT')
    await user.type(
      screen.getByPlaceholderText('Es. Please output the text content from the image.'),
      'text',
    )
    await user.type(screen.getByPlaceholderText('table, Table_document, grid'), 'port_name')
    await user.click(screen.getByRole('button', { name: 'Aggiungi classe' }))

    await user.click(screen.getByRole('button', { name: 'Salva classi' }))

    await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1))
    const [path, body] = mockPut.mock.calls[0] as unknown as [string, { labels: Array<{ name: string; aliases: string[] }> }]
    expect(path).toBe('/projects/7/labels')
    const names = body.labels.map((l) => l.name)
    expect(names).toEqual(['Vessel-name', 'Port-of-call'])
    // Il sinonimo nativo viaggia nel payload: è il bridge del progetto.
    expect(body.labels[1].aliases).toEqual(['port_name'])
    expect(onSaved).toHaveBeenCalled()
  })

  it('blocca il salvataggio della bozza con nome non valido', async () => {
    const user = userEvent.setup()
    render(<LabelsEditor projectId={7} labels={LABELS} onSaved={() => {}} />)

    await user.click(screen.getByRole('button', { name: 'Aggiungi classe' }))
    await user.type(screen.getByPlaceholderText('Es. Vessel-name'), '1bad')
    await user.type(screen.getByPlaceholderText('VSL'), 'BAD')
    await user.type(
      screen.getByPlaceholderText('Es. Please output the text content from the image.'),
      'text',
    )
    // Il pulsante di conferma della bozza resta spento finché il nome è invalido.
    expect(screen.getByRole('button', { name: 'Aggiungi classe' })).toBeDisabled()
  })
})
