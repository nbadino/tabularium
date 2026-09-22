// @vitest-environment jsdom
/** «Sto caricando»: il movimento è un segno in più, non l'unico. La frase
 *  resta leggibile e lo stato resta annunciabile da uno screen reader. */
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { Loading, LoadingGrid } from './ui'

afterEach(() => cleanup())

describe('Loading', () => {
  it('dice la frase e porta il filo in corsa', () => {
    render(<Loading label="Loading grid…" />)
    expect(screen.getByText('Loading grid…')).toBeTruthy()
    expect(screen.getByRole('status', { name: 'Loading grid…' })).toHaveClass('load-rail')
  })

  it('lo scheletro ha la forma della griglia e resta annunciato', () => {
    const { container } = render(<LoadingGrid label="Loading grid…" rows={3} />)
    expect(screen.getByRole('status', { name: 'Loading grid…' })).toBeTruthy()
    expect(container.querySelectorAll('.load-cell')).toHaveLength(12) // 3 righe × 4 colonne
    // Le colonne partono sfalsate: il velo attraversa la riga, non lampeggia.
    const delays = [...container.querySelectorAll('.load-cell')].map(
      (cell) => (cell as HTMLElement).style.getPropertyValue('--load-delay'),
    )
    expect(delays.slice(0, 4)).toEqual(['0ms', '80ms', '160ms', '240ms'])
  })
})
