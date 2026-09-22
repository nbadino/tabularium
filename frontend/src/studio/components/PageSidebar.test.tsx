// @vitest-environment jsdom
/** La lista delle pagine dice dove le somme non tornano, senza aprire le tabelle. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router'
import PageSidebar from './PageSidebar'
import type { PageItem } from '../../lib/types'

afterEach(() => cleanup())

const page = (id: number, pdfPage: number): PageItem => ({
  id, project_id: 1, rel_path: 'annual.pdf', abs_path: '/a/annual.pdf', source_kind: 'pdf',
  pdf_page: pdfPage, width: 1800, height: 2600, issue_date: null, issue_no: null,
  page_no: null, page_type: null, status: 'annotated', created_at: '',
})

describe('PageSidebar', () => {
  it('mostra le somme che non tornano, quelle giuste, e niente dove non ce ne sono', () => {
    render(
      <MemoryRouter>
        <PageSidebar
          projects={[]}
          projectId={1}
          pages={[page(4, 3), page(5, 4), page(6, 5)]}
          currentPage={null}
          onProjectChange={vi.fn()}
          onPageSelect={vi.fn()}
          sums={{ 4: { checks: 118, failed: 0, rows: 50, cols: 68 }, 5: { checks: 114, failed: 6, rows: 0, cols: 114 } }}
        />
      </MemoryRouter>,
    )
    // p. 5 ha solo somme di colonna, p. 4 dei due tipi: l'etichetta lo dice.
    expect(screen.getByText('6 somme di colonna ≠')).toHaveAttribute('class', expect.stringContaining('warn'))
    expect(screen.getByTitle('6 somme su 114 non tornano in questa pagina')).toBeTruthy()
    expect(screen.getByText('Row and column sums ok')).toBeTruthy()
    expect(screen.getAllByText(/≠|sums? ok/)).toHaveLength(2) // la terza pagina non ha tabelle
  })
})
