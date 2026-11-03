// @vitest-environment jsdom
/**
 * A ZIP goes up in pieces, is inspected, and only an archive with several folders
 * asks the user how to import it. The summary says what was imported and why the
 * rest was skipped.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import ZipImportDialog from './ZipImportDialog'
import { uploadZip } from '../lib/zipImport'

const state = vi.hoisted(() => ({
  plan: {} as Record<string, unknown>,
  posts: [] as Array<{ path: string; body: unknown }>,
}))

const report = {
  root: '', files: 3, folders: [], loose_files: 0, needs_choice: false,
  skipped: [{ name: 'notes.txt', reason: 'not_image' }], skipped_counts: { not_image: 1 }, skipped_total: 1,
  ignored: 2, registered: 3, duplicates: 0, unsupported: 0, errors: [],
  collections: [{ id: 1, name: 'Registri', created: true, pages: 2 }], unassigned_pages: 1,
}

vi.mock('../lib/api', () => ({
  ApiError: class extends Error {},
  apiPost: vi.fn(async (path: string, body: unknown) => {
    state.posts.push({ path, body })
    if (path.endsWith('/zip-uploads')) return { upload_id: 'u1', chunk_size: 4 }
    if (path.endsWith('/inspect')) return state.plan
    if (path.endsWith('/import')) return { job_id: 'j1' }
    return {}
  }),
  apiGet: vi.fn(async () => ({ status: 'done', phase: 'registering', current: 3, total: 3, report, error: null })),
}))

const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }))
vi.stubGlobal('fetch', fetchMock)

afterEach(() => {
  cleanup()
  state.posts = []
  fetchMock.mockClear()
})

const file = () => new File(['0123456789'], 'Lloyds.zip', { type: 'application/zip' })

describe('ZipImportDialog', () => {
  it('uploads in chunks of the size the server asks for', async () => {
    const seen: number[] = []
    await uploadZip(5, file(), (sent) => seen.push(sent))
    const urls = fetchMock.mock.calls.map((c) => String((c as unknown[])[0]))
    expect(urls).toEqual([
      '/api/projects/5/zip-uploads/u1?offset=0',
      '/api/projects/5/zip-uploads/u1?offset=4',
      '/api/projects/5/zip-uploads/u1?offset=8',
    ])
    expect(seen[seen.length - 1]).toBe(10)
  })

  it('imports without asking when there is nothing to choose', async () => {
    state.plan = { ...report, needs_choice: false }
    const onImported = vi.fn()
    render(<ZipImportDialog projectId={5} file={file()} collectionId={7} onClose={() => {}} onImported={onImported} />)
    await waitFor(() => expect(onImported).toHaveBeenCalled())
    expect(state.posts.find((p) => p.path.endsWith('/import'))?.body).toEqual({ layout: 'flat', collection_id: 7 })
    expect(screen.getByText('3 pagine importate')).toBeInTheDocument()
    expect(screen.getAllByText(/non è un’immagine né un PDF/).length).toBeGreaterThan(0)
  })

  it('asks between one collection per folder and all together', async () => {
    state.plan = { ...report, needs_choice: true, folders: [{ name: 'Lettere', files: 1 }, { name: 'Registri', files: 2 }], loose_files: 1 }
    const onImported = vi.fn()
    render(<ZipImportDialog projectId={5} file={file()} collectionId={null} onClose={() => {}} onImported={onImported} />)
    expect(await screen.findByText('Lettere')).toBeInTheDocument()
    expect(state.posts.some((p) => p.path.endsWith('/import'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Importa' }))
    await waitFor(() => expect(onImported).toHaveBeenCalled())
    expect(state.posts.find((p) => p.path.endsWith('/import'))?.body).toEqual({ layout: 'collections', collection_id: null })
  })
})
