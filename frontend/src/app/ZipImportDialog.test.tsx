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
  received: 0,
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
  apiGet: vi.fn(async (path: string) => {
    if (path.endsWith('/zip-uploads/u1')) return { received: state.received, size: 10, imported: false }
    if (path.endsWith('/collections')) {
      return {
        collections: [{ id: 7, name: 'Lettere', pages: 4, with_drafts: 0, by_status: {}, created_at: '', updated_at: '' }],
        unassigned: { key: 'none', pages: 0, with_drafts: 0, by_status: {} },
        total: { pages: 4, with_drafts: 0, by_status: {} },
      }
    }
    return { status: 'done', phase: 'registering', current: 3, total: 3, report, error: null }
  }),
}))

const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }))
vi.stubGlobal('fetch', fetchMock)

afterEach(() => {
  cleanup()
  state.posts = []
  fetchMock.mockClear()
})

const file = () => new File(['0123456789'], 'Lloyds.zip', { type: 'application/zip' })

describe('uploadZip retries', () => {
  it('sends a piece again after the connection drops', async () => {
    fetchMock.mockImplementationOnce(async () => { throw new TypeError('network') })
    state.received = 0
    const seen: Array<[number, boolean | undefined]> = []
    await uploadZip(5, file(), (sent, _size, retrying) => seen.push([sent, retrying]), undefined, [0, 0])
    const offsets = fetchMock.mock.calls.map((c) => String((c as unknown[])[0]).split('offset=')[1])
    expect(offsets).toEqual(['0', '0', '4', '8'])
    expect(seen).toContainEqual([0, true])
    expect(seen[seen.length - 1]).toEqual([10, undefined])
  })

  it('resumes from what the server holds when an answer was lost', async () => {
    // the piece at 4 arrived but its answer did not: the server holds 8 bytes
    fetchMock
      .mockImplementationOnce(async () => new Response('{}', { status: 200 }))
      .mockImplementationOnce(async () => new Response('{"detail":"bad gateway"}', { status: 502 }))
    state.received = 8
    await uploadZip(5, file(), () => {}, undefined, [0])
    const offsets = fetchMock.mock.calls.map((c) => String((c as unknown[])[0]).split('offset=')[1])
    expect(offsets).toEqual(['0', '4', '8'])
  })

  it('gives up on a refusal that a new attempt cannot fix, and after the last attempt', async () => {
    fetchMock.mockImplementationOnce(async () => new Response('{"detail":"too large"}', { status: 413 }))
    await expect(uploadZip(5, file(), () => {}, undefined, [0])).rejects.toThrow()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    fetchMock.mockClear()
    fetchMock.mockImplementation(async () => { throw new TypeError('network') })
    await expect(uploadZip(5, file(), () => {}, undefined, [0, 0])).rejects.toThrow()
    expect(fetchMock).toHaveBeenCalledTimes(3)
    fetchMock.mockImplementation(async () => new Response('{}', { status: 200 }))
  })
})

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

  it('asks which collection, proposing a new one named after the archive', async () => {
    state.plan = { ...report, root: 'Archivio/LSI1950', needs_choice: false }
    const onImported = vi.fn()
    render(<ZipImportDialog projectId={5} file={file()} collectionId={null} onClose={() => {}} onImported={onImported} />)
    expect(await screen.findByText('In quale collezione?')).toBeInTheDocument()
    expect(state.posts.some((p) => p.path.endsWith('/import'))).toBe(false)
    expect(screen.getByLabelText('Nome')).toHaveValue('LSI1950')
    fireEvent.click(screen.getByRole('button', { name: 'Importa' }))
    await waitFor(() => expect(onImported).toHaveBeenCalled())
    expect(state.posts.find((p) => p.path.endsWith('/import'))?.body).toEqual({ layout: 'flat', collection_name: 'LSI1950' })
    expect(screen.getByText('3 pagine importate')).toBeInTheDocument()
    expect(screen.getAllByText(/non è un’immagine né un PDF/).length).toBeGreaterThan(0)
  })

  it('preselects the collection being viewed, and can send none', async () => {
    state.plan = { ...report, needs_choice: false }
    const onImported = vi.fn()
    render(<ZipImportDialog projectId={5} file={file()} collectionId={7} onClose={() => {}} onImported={onImported} />)
    await screen.findByText('In quale collezione?')
    await waitFor(() => expect(screen.getByLabelText('Collezione esistente', { selector: 'select' })).toHaveValue('7'))
    fireEvent.click(screen.getByLabelText('Senza collezione'))
    fireEvent.click(screen.getByRole('button', { name: 'Importa' }))
    await waitFor(() => expect(onImported).toHaveBeenCalled())
    expect(state.posts.find((p) => p.path.endsWith('/import'))?.body).toEqual({ layout: 'flat', collection_id: null })
  })

  it('asks between one collection per folder and all together', async () => {
    state.plan = { ...report, needs_choice: true, folders: [{ name: 'Lettere', files: 1 }, { name: 'Registri', files: 2 }], loose_files: 1 }
    const onImported = vi.fn()
    render(<ZipImportDialog projectId={5} file={file()} collectionId={null} onClose={() => {}} onImported={onImported} />)
    expect(await screen.findByText('Lettere')).toBeInTheDocument()
    expect(state.posts.some((p) => p.path.endsWith('/import'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Importa' }))
    await waitFor(() => expect(onImported).toHaveBeenCalled())
    expect(state.posts.find((p) => p.path.endsWith('/import'))?.body).toEqual({ layout: 'collections' })
  })
})
