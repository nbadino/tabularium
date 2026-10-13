// @vitest-environment jsdom
/**
 * Creazione rapida di una classe: dichiara lo scope di progetto e scrive con
 * `POST /labels` (una sola classe).
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom/vitest'

vi.mock('../../lib/api', () => ({ apiPost: vi.fn() }))

import { apiPost } from '../../lib/api'
import NewLabelDialog from './NewLabelDialog'
import type { LabelDef } from '../../lib/types'

const mockPost = vi.mocked(apiPost)

afterEach(() => {
  cleanup()
  mockPost.mockReset()
})

const EXISTING: LabelDef[] = [
  {
    name: 'Title', color: '#5c4033', short: 'TTL', prompt_kind: 'text', prompt: 't',
    origin: 'public', builtin: true, aliases: [], known_models: [],
  },
]

describe('NewLabelDialog', () => {
  it('dichiara lo scope di progetto e crea una sola classe', async () => {
    const user = userEvent.setup()
    const onCreated = vi.fn()
    mockPost.mockResolvedValue({ labels: EXISTING } as never)

    render(
      <NewLabelDialog
        projectId={5}
        existingNames={EXISTING.map((l) => l.name)}
        onClose={() => {}}
        onCreated={onCreated}
      />,
    )

    // La nota di scope è visibile prima di creare.
    expect(screen.getByText(/a livello di progetto/i)).toBeTruthy()

    await user.type(screen.getByPlaceholderText('Es. Vessel-name'), 'Vessel-name')
    await user.type(screen.getByPlaceholderText('VSL'), 'VSL')
    await user.type(
      screen.getByPlaceholderText('Es. Please output the text content from the image.'),
      'text',
    )
    await user.click(screen.getByRole('button', { name: 'Crea' }))

    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1))
    const [path, body] = mockPost.mock.calls[0] as unknown as [string, { name: string }]
    expect(path).toBe('/projects/5/labels')
    expect(body.name).toBe('Vessel-name')
    expect(onCreated).toHaveBeenCalledWith(EXISTING, 'Vessel-name')
  })

  it('non crea con un nome già esistente', async () => {
    const user = userEvent.setup()
    render(
      <NewLabelDialog
        projectId={5}
        existingNames={['Title']}
        onClose={() => {}}
        onCreated={() => {}}
      />,
    )
    await user.type(screen.getByPlaceholderText('Es. Vessel-name'), 'Title')
    await user.type(screen.getByPlaceholderText('VSL'), 'TTL')
    await user.type(
      screen.getByPlaceholderText('Es. Please output the text content from the image.'),
      'text',
    )
    expect(screen.getByRole('button', { name: 'Crea' })).toBeDisabled()
  })
})
