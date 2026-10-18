// @vitest-environment jsdom
/**
 * Hint di schema tabella: carica, salva, e avvisa quando il modello attivo non
 * accetta un hint testuale (motori a prompt di protocollo o pipeline nativa).
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom/vitest'

vi.mock('../../lib/api', () => ({ apiGet: vi.fn(), apiPut: vi.fn() }))

import { apiGet, apiPut } from '../../lib/api'
import TableHintEditor from './TableHintEditor'

const mockGet = vi.mocked(apiGet) as unknown as ReturnType<typeof vi.fn>
const mockPut = vi.mocked(apiPut) as unknown as ReturnType<typeof vi.fn>

afterEach(() => {
  cleanup()
  mockGet.mockReset()
  mockPut.mockReset()
})

describe('TableHintEditor', () => {
  it('carica il hint, lo mostra e lo salva', async () => {
    mockGet.mockResolvedValue({
      hint: 'Year e Tons unite',
      active_model: 'Qwen3-VL-8B',
      accepts_hint: true,
    })
    mockPut.mockResolvedValue({
      hint: 'Year e Tens unite',
      active_model: 'Qwen3-VL-8B',
      accepts_hint: true,
    })

    render(<TableHintEditor projectId={3} />)
    const area = await screen.findByRole('textbox')
    await waitFor(() => expect(area).toHaveValue('Year e Tons unite'))
    expect(screen.getByText(/Il modello attivo/)).toBeTruthy()

    await userEvent.clear(area)
    await userEvent.type(area, 'Year e Tens unite')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    await waitFor(() =>
      expect(mockPut).toHaveBeenCalledWith('/projects/3/table-hint', {
        hint: 'Year e Tens unite',
      }),
    )
  })

  it('avvisa quando il modello attivo ignora l’hint testuale', async () => {
    mockGet.mockResolvedValue({
      hint: '',
      active_model: 'MinerU2.5',
      accepts_hint: false,
    })

    render(<TableHintEditor projectId={3} />)
    expect(await screen.findByText(/ignora/)).toBeTruthy()
  })
})
