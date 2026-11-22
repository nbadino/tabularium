// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import KnownHeaderDialog from './KnownHeaderDialog'

afterEach(() => cleanup())

describe('KnownHeaderDialog', () => {
  it('uses proper Italian singular/plural for selection dimensions (1 riga × 2 colonne)', () => {
    render(
      <KnownHeaderDialog
        projectId={1}
        mode="save"
        template={{ rows: 1, cols: 2, cells: [{ r: 0, c: 0, rowspan: 1, colspan: 1, text: 'A' }, { r: 0, c: 1, rowspan: 1, colspan: 1, text: 'B' }] }}
        headers={[]}
        onHeadersChange={vi.fn()}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByText('Selezione: 1 riga × 2 colonne')).toBeInTheDocument()
  })
})
