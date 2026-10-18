import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import AnnotationEntry from './AnnotationEntry'
import TableEntry from './TableEntry'
import { projectAccess } from '../app/projectAccess'
import type { Project, User } from '../lib/types'

const h = vi.hoisted(() => ({ canEdit: false, calls: [] as string[] }))
vi.mock('../app/auth', () => ({ useAuth: () => ({ enabled: true, user: { id: 4, role: 'editor' } }) }))
vi.mock('../lib/api', () => ({
  apiGet: vi.fn(async (url: string) => {
    h.calls.push(url)
    if (url.startsWith('/blocks/')) return { id: 7, page_id: 19, project_id: 3 }
    const project = { id: 3, name: 'Condiviso', owner_id: 1, access: { role: h.canEdit ? 'editor' : 'viewer', source: 'member', can_edit: h.canEdit, can_manage: false } }
    return url === '/projects' ? { items: [project] } : project
  }),
  ApiError: class extends Error {},
}))
vi.mock('./AnnotationPage', () => ({ default: () => <p>Editor annotazioni montato</p> }))
vi.mock('./TableWorkspacePage', () => ({ default: () => <p>Editor tabella montato</p> }))
vi.mock('./ProjectReader', () => ({ default: () => <p>Consultazione montata</p> }))
afterEach(() => { cleanup(); h.calls = []; h.canEdit = false })

it('un editor globale con accesso viewer al progetto monta solo la consultazione', async () => {
  render(<MemoryRouter initialEntries={['/annotazione?project=3']}><AnnotationEntry /></MemoryRouter>)
  await screen.findByText('Consultazione montata')
  expect(screen.queryByText('Editor annotazioni montato')).not.toBeInTheDocument()
})

it('il progetto editor monta lo Studio dopo aver risolto il permesso', async () => {
  h.canEdit = true
  render(<MemoryRouter initialEntries={['/annotazione?project=3']}><AnnotationEntry /></MemoryRouter>)
  await screen.findByText('Editor annotazioni montato')
})

it('la tabella risolve il progetto dal blocco e ignora la query contraffatta', async () => {
  render(<MemoryRouter initialEntries={['/tabelle/7?project=999&page=888']}><Routes><Route path="/tabelle/:blockId" element={<TableEntry />} /></Routes></MemoryRouter>)
  const link = await screen.findByRole('link')
  expect(link).toHaveAttribute('href', '/annotazione?project=3&page=19')
  expect(h.calls).toEqual(['/blocks/7', '/projects/3'])
  expect(screen.queryByText('Editor tabella montato')).not.toBeInTheDocument()
})

it('il fallback non deduce mai il permesso membro dal ruolo globale', () => {
  expect(projectAccess({ id: 3, owner_id: 1 } as Project, { enabled: true, user: { id: 4, role: 'editor' } as User })).toBeNull()
  const access = { role: 'editor', source: 'member', can_edit: true, can_manage: false } as const
  expect(projectAccess({ id: 3, access } as Project, { enabled: true, user: { id: 4, role: 'viewer' } as User })).toEqual(access)
})
