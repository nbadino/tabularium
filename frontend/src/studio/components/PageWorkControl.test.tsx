import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import PageWorkControl from './PageWorkControl'
import type { PageItem, PageWork } from '../../lib/types'
const h = vi.hoisted(() => ({ work: {} as PageWork, post: vi.fn() }))
vi.mock('../../app/auth', () => ({ useAuth: () => ({ enabled: true, user: { id: 2 } }) }))
vi.mock('../../app/live', () => ({ onAnnotationsChanged: () => () => {} }))
vi.mock('../../app/confirm', () => ({ useConfirm: () => async () => true }))
vi.mock('../../lib/api', () => ({ apiGet: async () => h.work, apiPost: (...args: unknown[]) => h.post(...args), apiPut: vi.fn(), ApiError: class extends Error {} }))
afterEach(() => { cleanup(); vi.clearAllMocks() })
const page = { id: 8, project_id: 1, rel_path: 'page.png' } as PageItem
it('la consegna attende tutte le scritture e usa la revisione effettivamente salvata', async () => {
  h.work = { page_id: 8, version: 4, can_submit: true, state: 'annotation', assignee_id: 2, reviewer_id: 3 } as PageWork
  let finish!: (revision: number) => void
  const before = vi.fn(() => new Promise<number>((resolve) => { finish = resolve }))
  h.post.mockResolvedValue({ ...h.work, state: 'pending', version: 5 })
  render(<PageWorkControl page={page} revision={2} beforeAction={before} onChanged={vi.fn()} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Invia al revisore' }))
  const buttons = screen.getAllByRole('button', { name: 'Invia al revisore' })
  fireEvent.click(buttons[buttons.length - 1])
  expect(h.post).not.toHaveBeenCalled()
  finish(7)
  await waitFor(() => expect(h.post).toHaveBeenCalledWith('/pages/8/work/submit', { expected_version: 4, annotation_revision: 7 }))
})
it('la richiesta di correzioni richiede una nota e non concede comandi di assegnazione al revisore', async () => {
  h.work = { page_id: 8, version: 2, state: 'pending', can_review: true, can_assign: false, assignee_id: 2, reviewer_id: 3 } as PageWork
  render(<PageWorkControl page={page} revision={0} beforeAction={async () => 0} onChanged={vi.fn()} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Revisiona' }))
  expect(screen.getByRole('button', { name: 'Richiedi correzioni' })).toBeDisabled()
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Correggere la data' } })
  expect(screen.getByRole('button', { name: 'Richiedi correzioni' })).toBeEnabled()
  expect(screen.queryByRole('button', { name: 'Salva assegnazione' })).not.toBeInTheDocument()
})
it('al cambio pagina non mostra le azioni della pagina precedente', async () => {
  h.work = { page_id: 8, version: 1, can_submit: true, state: 'annotation' } as PageWork
  const props = { revision: 0, beforeAction: async () => 0, onChanged: vi.fn() }
  const view = render(<PageWorkControl page={page} {...props} />)
  await screen.findByRole('button', { name: 'Invia al revisore' })
  view.rerender(<PageWorkControl page={{ ...page, id: 9 }} {...props} />)
  expect(screen.queryByRole('button', { name: 'Invia al revisore' })).not.toBeInTheDocument()
})
