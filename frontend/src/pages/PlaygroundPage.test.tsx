// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom/vitest'
import { useEffect } from 'react'
import { MemoryRouter } from 'react-router'
import { setLocale } from '../i18n'

const mockState = vi.hoisted(() => ({
  uploadedCalls: 0,
}))

vi.mock('../lib/api', () => ({
  ApiError: class ApiError extends Error {},
  apiGet: vi.fn(async (path: string) => {
    if (path === '/system/prefill-engines') return { recommended: 'ocr', ocr: { available: true, engine: 'rapidocr' }, model: { available: false } }
    if (path === '/projects/3/pages') return { items: [{ id: 10, project_id: 3, rel_path: 'p10.png', width: 100, height: 100, status: 'new' }] }
    return { items: [] }
  }),
  apiPost: vi.fn(async () => ({
    ok: true, server: null, model: 'OCR · rapidocr', provider: 'local', width: 100, height: 100,
    items: [{ bbox_norm: [10, 10, 90, 30], bbox_px: [1, 1, 9, 3], label: 'Text', content: 'Linea di prova' }],
  })),
  apiPostForm: vi.fn(async () => {
    mockState.uploadedCalls += 1
    return {
      ok: true, server: null, model: 'OCR · rapidocr', provider: 'local', width: 100, height: 100,
      items: [{ bbox_norm: [10, 10, 90, 30], bbox_px: [1, 1, 9, 3], label: 'Text', content: 'Immagine locale' }],
    }
  }),
}))
vi.mock('../app/activeProject', () => ({
  useProjects: (onPick: (pid: number) => void) => {
    useEffect(() => { onPick(3) }, []) // eslint-disable-line react-hooks/exhaustive-deps
    return [{ id: 3, name: 'Archivio' }]
  },
  writeActiveProject: () => {},
}))
vi.mock('../app/inference', () => ({
  useInference: () => ({ enabled: false, available: false, isCloud: false, adapterId: '', model: '', url: '', provider: null }),
}))
vi.mock('../app/models/names', () => ({
  useModelNames: () => () => null,
}))

import PlaygroundPage from './PlaygroundPage'

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => 'blob:preview')
  URL.revokeObjectURL = vi.fn()
  mockState.uploadedCalls = 0
})
afterEach(cleanup)

describe('PlaygroundPage', () => {
  it('links to project recognitions, distinguishes Engine vs Page labels, and runs standalone upload', async () => {
    setLocale('it')
    const user = userEvent.setup()
    const { container } = render(<MemoryRouter><PlaygroundPage /></MemoryRouter>)

    // Accessible labels for Motore and Pagina do not collide via hint copy.
    expect(screen.getByLabelText('Motore')).toBeInTheDocument()
    expect(screen.getByLabelText('Pagina')).toBeInTheDocument()

    await screen.findByRole('option', { name: 'p10.png' })
    await user.selectOptions(screen.getByLabelText('Pagina'), '10')
    expect(screen.getByRole('link', { name: 'Riconosci e salva risultati' })).toHaveAttribute(
      'href',
      '/progetti/3/riconoscimenti?project=3&pages=10',
    )

    // Standalone image upload runs parse-upload and hides coordinates by default.
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File([new Uint8Array([1, 2, 3])], 'sample.png', { type: 'image/png' })
    await user.upload(fileInput, file)
    await user.click(screen.getByRole('button', { name: /Avvia prova/ }))
    await waitFor(() => expect(mockState.uploadedCalls).toBe(1))
    expect(await screen.findByText('Immagine locale')).toBeInTheDocument()
    expect(screen.queryByText('[1, 1, 9, 3]')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Coordinate' }))
    expect(screen.getByText('[1, 1, 9, 3]')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Pulisci' }))
    expect(screen.queryByText('Immagine locale')).not.toBeInTheDocument()
  })
})
