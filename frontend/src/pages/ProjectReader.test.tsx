import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import ProjectReader from './ProjectReader'
import type { Project } from '../lib/types'
const h = vi.hoisted(() => ({ urls: [] as string[] }))
vi.mock('../lib/api', () => ({
  apiGet: vi.fn(async (url: string) => {
    h.urls.push(url)
    if (url.endsWith('/pages')) return { items: [{ id: 8, project_id: 3, rel_path: 'pagina.png' }] }
    if (url.endsWith('/annotations')) return { items: [{ id: 7, label: 'Table', confirmed: true, order_idx: 0 }] }
    return { grid: { rows: 2, cols: 2, cells: [
      { r: 0, c: 0, rowspan: 1, colspan: 2, text: '<script>merged header</script>' },
      { r: 1, c: 0, rowspan: 1, colspan: 1, text: 'Aurora' },
      { r: 1, c: 1, rowspan: 1, colspan: 1, text: 'Genoa' },
    ] } }
  }),
  ApiError: class extends Error {},
}))
vi.mock('../app/live', () => ({ connectPage: vi.fn(), disconnectPage: vi.fn(), onAnnotationsChanged: () => () => {} }))
afterEach(() => { cleanup(); h.urls = [] })
const project = { id: 3, name: 'Registro' } as Project
it('mostra il testo e le celle unite salvate senza montare controlli di modifica', async () => {
  render(<MemoryRouter initialEntries={['/annotazione?project=3&page=8']}><ProjectReader project={project} projects={[project]} /></MemoryRouter>)
  expect(await screen.findByRole('cell', { name: '<script>merged header</script>' })).toHaveAttribute('colspan', '2')
  expect(screen.getByRole('cell', { name: 'Aurora' })).toBeInTheDocument()
  expect(document.querySelector('script')).toBeNull()
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  expect(h.urls).toEqual(['/projects/3/pages', '/pages/8/annotations', '/blocks/7/table'])
})
it('una pagina estranea non viene aperta né sostituita silenziosamente', async () => {
  render(<MemoryRouter initialEntries={['/annotazione?project=3&page=999']}><ProjectReader project={project} projects={[project]} /></MemoryRouter>)
  await screen.findByText('La pagina richiesta non è disponibile in questo progetto. Scegli una pagina dall’elenco.')
  expect(h.urls).toEqual(['/projects/3/pages'])
})
