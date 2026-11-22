// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom/vitest'
import { useEffect } from 'react'
import { MemoryRouter } from 'react-router'
import { setLocale } from '../i18n'

// Lloyd's: the active model is off; Gemini 4 Argon and OCRia-1 are saved with their
// own keys. The page opened on «Local OCR» and did not offer them.
const posted: { path: string; body: unknown }[] = []
let mockProjects = [{ id: 4, name: 'Lloyds', access: { role: 'owner', source: 'local', can_edit: true, can_manage: true } }]
const withKeys = [
  { id: 1, name: 'Gemini 4 Argon', model_adapter_id: 'gemini-argon', served_model_name: 'gemini-4-argon', key_required: true, has_key: true },
  { id: 3, name: 'OCRia-1 (LlamaIndex)', model_adapter_id: 'llamaindex-ocria-1', served_model_name: 'ocria-1', key_required: true, has_key: true },
]
let savedModels = withKeys
vi.mock('../lib/api', () => ({
  ApiError: class ApiError extends Error { status = 0 },
  apiGet: vi.fn(async (path: string) => {
    if (path === '/consensus/profiles') {
      return { items: savedModels }
    }
    if (path === '/system/prefill-engines') return { recommended: 'ocr', ocr: { available: true }, model: { available: false } }
    if (path === '/projects/4/pages') return { items: [{ id: 376, rel_path: 'LDI_1920_Apr1_Page_003.tif', status: 'todo', drafts: 0 }] }
    if (path.startsWith('/recognition-runs')) return { items: [] }
    if (path.endsWith('/consensus-runs')) return { items: [] }
    return { items: [] }
  }),
  apiPost: vi.fn(async (path: string, body: unknown) => {
    posted.push({ path, body })
    return { id: 9, project_id: 4, state: 'queued', engine: 'model', total_pages: 1, completed_pages: 0, succeeded_pages: 0, failed_pages: 0 }
  }),
  apiDelete: vi.fn(),
}))
vi.mock('../app/activeProject', () => ({
  useRouteProjectId: () => 4,
  writeActiveProject: () => {},
  useProjects: (onPick: (pid: number) => void) => {
    useEffect(() => { onPick(4) }, []) // eslint-disable-line react-hooks/exhaustive-deps
    return mockProjects
  },
}))
vi.mock('../app/inference', () => ({
  syncInferenceFromBackend: async () => {},
  useInference: () => ({ enabled: false, available: false, isCloud: false, adapterId: '', model: '', url: '', provider: null }),
}))
vi.mock('../app/auth', () => ({ useCanAdminister: () => true }))
vi.mock('../app/collections', () => ({
  UNASSIGNED: 'none',
  matchesCollection: () => true,
  useCollections: () => ({ overview: null, refresh: async () => {} }),
}))
vi.mock('../app/models/names', () => ({
  useModelNames: () => () => null,
  runTitle: () => '',
  // Only Unlimited-OCR 2 reads whole documents in this catalog.
  useReadsDocuments: () => (id: string | null | undefined) => id === 'unlimited-ocr-2',
}))

import RecognizePage from './RecognizePage'

afterEach(() => cleanup())

describe('RecognizePage engines', () => {
  it('offers the saved models, opens on one when the active model is off, and runs with it', async () => {
    setLocale('it')
    render(<MemoryRouter initialEntries={['/progetti/4/riconoscimenti?project=4&pages=376']}><RecognizePage /></MemoryRouter>)
    const engine = await screen.findByTestId('engine')
    await waitFor(() => expect(engine).toHaveValue('profile:1'))
    expect(screen.getByRole('option', { name: 'OCRia-1 (LlamaIndex)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Modello attivo (spento)' })).toBeDisabled()
    // Nothing to start or stop: no «choose a model», no «stop the model after».
    expect(screen.queryByText('Scegli un modello')).toBeNull()
    await userEvent.selectOptions(engine, 'profile:3')
    await userEvent.click(await screen.findByRole('button', { name: /Avvia/ }))
    await waitFor(() => expect(posted[posted.length - 1]?.body).toMatchObject({ engine: 'model', profile_id: 3, stop_policy: 'none' }))
  })

  it('does not run a saved model the user has no key for, and says where to add one', async () => {
    setLocale('it')
    savedModels = [
      { ...withKeys[0], has_key: false },
      { ...withKeys[1], has_key: true },
    ]
    try {
      render(<MemoryRouter initialEntries={['/progetti/4/riconoscimenti?project=4&pages=376']}><RecognizePage /></MemoryRouter>)
      const engine = await screen.findByTestId('engine')
      // Opens on the saved model the user can run, not on the one without a key.
      await waitFor(() => expect(engine).toHaveValue('profile:3'))
      await userEvent.selectOptions(engine, 'profile:1')
      expect(await screen.findByText(/Non hai ancora aggiunto la tua chiave API per questo modello/)).toBeInTheDocument()
      expect(screen.getByText('manca la chiave')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /Avvia/ })).toBeDisabled()
    } finally {
      savedModels = withKeys
    }
  })

  it('offers «read as one document» only for a model that reads whole documents', async () => {
    setLocale('it')
    savedModels = [...withKeys, { id: 5, name: 'Unlimited-OCR-2', model_adapter_id: 'unlimited-ocr-2', served_model_name: 'Unlimited-OCR-2', key_required: false, has_key: true }]
    try {
      render(<MemoryRouter initialEntries={['/progetti/4/riconoscimenti?project=4&pages=376']}><RecognizePage /></MemoryRouter>)
      const engine = await screen.findByTestId('engine')
      await waitFor(() => expect(engine).toHaveValue('profile:1'))
      // A page-by-page model: no such choice on screen.
      expect(screen.queryByText('Leggi come un documento unico')).toBeNull()

      await userEvent.selectOptions(engine, 'profile:5')
      const option = await screen.findByRole('checkbox', { name: /Leggi come un documento unico/ })
      expect(option).toBeChecked()
      expect(screen.getByText(/nell’ordine del libro/)).toBeInTheDocument()
      await userEvent.click(screen.getByRole('button', { name: /Avvia/ }))
      await waitFor(() => expect(posted[posted.length - 1]?.body).toMatchObject({ profile_id: 5, model_mode: 'document' }))

      // Turned off, the same model goes page by page.
      await userEvent.click(option)
      await userEvent.click(screen.getByRole('button', { name: /Avvia/ }))
      await waitFor(() => expect(posted[posted.length - 1]?.body).toMatchObject({ profile_id: 5, model_mode: 'native' }))

      await userEvent.selectOptions(engine, 'profile:3')
      expect(screen.queryByText('Leggi come un documento unico')).toBeNull()
    } finally {
      savedModels = withKeys
    }
  })

  it('blocks write actions for read-only viewers and uses distinct latest-run heading', async () => {
    setLocale('it')
    mockProjects = [{ id: 4, name: 'Lloyds', access: { role: 'viewer', source: 'member', can_edit: false, can_manage: false } }]
    try {
      render(<MemoryRouter initialEntries={['/progetti/4/riconoscimenti?project=4&pages=376']}><RecognizePage /></MemoryRouter>)
      expect(await screen.findByText(/Accesso in sola lettura/)).toBeInTheDocument()
      expect(screen.queryByRole('link', { name: 'Aggiungi pagine' })).toBeNull()
      expect(screen.getByRole('button', { name: /Avvia riconoscimento/ })).toBeDisabled()
      expect(screen.getByRole('link', { name: /Configuralo in Modelli/ })).toHaveAttribute('href', '/modelli')
    } finally {
      mockProjects = [{ id: 4, name: 'Lloyds', access: { role: 'owner', source: 'local', can_edit: true, can_manage: true } }]
    }
  })
})
