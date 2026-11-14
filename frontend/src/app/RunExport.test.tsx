// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom/vitest'
import { RunExport } from './RunExport'
import { attachmentFilename } from '../lib/api'
import type { RecognitionRun } from '../lib/types'

const download = vi.fn()
let collections: { id: number; name: string }[] = []

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  apiDownloadFile: (...args: unknown[]) => download(...args),
}))
vi.mock('./collections', () => ({
  UNASSIGNED: 'none',
  useCollections: () => ({ overview: { collections }, refresh: vi.fn() }),
}))

const run = { id: 24, project_id: 1, state: 'finished' } as RecognitionRun

beforeEach(() => {
  localStorage.clear()
  collections = []
  download.mockReset()
  URL.createObjectURL = vi.fn(() => 'blob:x')
  URL.revokeObjectURL = vi.fn()
})
afterEach(cleanup)

it('offers every format with what it is for, the searchable PDF first', () => {
  render(<RunExport run={run} busyRun={false} primary onError={vi.fn()} />)
  const formats = screen.getAllByRole('radio').slice(0, 5)
  expect(formats.map((radio) => radio.closest('label')?.querySelector('b')?.textContent)).toEqual([
    'PDF ricercabile', 'Markdown', 'Testo semplice', 'Foglio di calcolo', 'Dati completi',
  ])
  expect(screen.getByRole('radio', { name: /PDF ricercabile/ })).toBeChecked()
  expect(screen.getByRole('radio', { name: /Con le correzioni/ })).toBeChecked()
  expect(screen.getByText(/si cerca, si seleziona e si copia/)).toBeInTheDocument()
  // No collections, no collection picker.
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
})

it('names the button after the file and downloads the chosen text', async () => {
  const user = userEvent.setup()
  download.mockResolvedValue({ blob: new Blob(['# x']), filename: 'volume-raw.md' })
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  render(<RunExport run={run} busyRun={false} primary onError={vi.fn()} />)

  await user.click(screen.getByRole('radio', { name: /Markdown/ }))
  await user.click(screen.getByRole('radio', { name: /Come uscito dal modello/ }))
  await user.click(screen.getByRole('button', { name: 'Scarica .md' }))

  await waitFor(() => expect(click).toHaveBeenCalled())
  expect(download.mock.calls[0][0]).toBe('/projects/1/recognition-runs/24/export?scope=raw&format=markdown')
  expect((click.mock.contexts[0] as HTMLAnchorElement).download).toBe('volume-raw.md')
  // The format is remembered for the next export.
  cleanup()
  render(<RunExport run={run} busyRun={false} primary onError={vi.fn()} />)
  expect(screen.getByRole('radio', { name: /Markdown/ })).toBeChecked()
  click.mockRestore()
})

it('shows the progress while a long file is prepared', async () => {
  const user = userEvent.setup()
  let finish: (value: { blob: Blob; filename: string }) => void = () => {}
  download.mockImplementation((_path: string, onProgress: (bytes: number) => void) => {
    onProgress(3 * 1024 * 1024)
    return new Promise((resolve) => { finish = resolve })
  })
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  render(<RunExport run={run} busyRun={false} primary onError={vi.fn()} />)
  await user.click(screen.getByRole('button', { name: 'Scarica .pdf' }))
  const busy = await screen.findByRole('button', { name: 'Preparazione del file… 3 MB' })
  expect(busy).toBeDisabled()
  finish({ blob: new Blob(['%PDF']), filename: 'volume.pdf' })
  expect(await screen.findByRole('button', { name: 'Scarica .pdf' })).toBeEnabled()
  click.mockRestore()
})

it('waits for the run to finish before exporting', () => {
  render(<RunExport run={run} busyRun primary={false} onError={vi.fn()} />)
  expect(screen.getByRole('button', { name: 'Scarica .pdf' })).toBeDisabled()
  expect(screen.getByText('Potrai esportare quando il riconoscimento è terminato.')).toBeInTheDocument()
})

it('limits the export to one collection when the project has them', async () => {
  const user = userEvent.setup()
  collections = [{ id: 5, name: '1949' }]
  download.mockResolvedValue({ blob: new Blob(['x']), filename: null })
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  render(<RunExport run={run} busyRun={false} primary onError={vi.fn()} />)
  await user.selectOptions(screen.getByRole('combobox', { name: 'Pagine' }), '5')
  await user.click(screen.getByRole('button', { name: 'Scarica .pdf' }))
  await waitFor(() => expect(click).toHaveBeenCalled())
  expect(download.mock.calls[0][0]).toBe('/projects/1/recognition-runs/24/export?scope=reviewed&format=pdf&collection=5')
  expect((click.mock.contexts[0] as HTMLAnchorElement).download).toBe('tabularium-run-24.pdf')
  click.mockRestore()
})

it('reads the UTF-8 file name first', () => {
  expect(attachmentFilename(`attachment; filename="Citt_.pdf"; filename*=UTF-8''Citt%C3%A0.pdf`)).toBe('Città.pdf')
  expect(attachmentFilename('attachment; filename="plain.md"')).toBe('plain.md')
  expect(attachmentFilename(null)).toBeNull()
})
