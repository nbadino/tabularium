// @vitest-environment jsdom
/**
 * Test del foglio di calcolo (nuovo editor tabellare): ogni cella è
 * direttamente editabile, la navigazione da tastiera scende di riga e il
 * salvataggio parte anche in autosave — la struttura arriva dal prefill,
 * l'utente corregge solo il testo.
 */
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import SheetEditor from './SheetEditor'
import { emptyGrid } from '../../lib/grid'

// Handsontable misura il contenitore per adattare le colonne: jsdom non ha
// ResizeObserver né un layout reale.
beforeAll(() => {
  if (!('ResizeObserver' in window)) {
    class ResizeObserverStub {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    // @ts-expect-error - polyfill minimo per il solo scopo dei test
    window.ResizeObserver = ResizeObserverStub
  }
  if (!('IntersectionObserver' in window)) {
    class IntersectionObserverStub {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    // @ts-expect-error - polyfill minimo per il solo scopo dei test
    window.IntersectionObserver = IntersectionObserverStub
  }
})

afterEach(() => cleanup())

describe('SheetEditor', () => {
  it('ogni cella è un input editabile: il testo scritto arriva alla griglia', async () => {
    const onSave = vi.fn().mockResolvedValue('<fcel>A<nl>')
    render(<SheetEditor grid={emptyGrid(2, 3)} onSave={onSave} />)

    const a1 = screen.getByLabelText('Cella riga 1, colonna 1') as HTMLInputElement
    fireEvent.change(a1, { target: { value: 'A. C. Bedford' } })
    expect(a1.value).toBe('A. C. Bedford')

    await waitFor(() => expect(onSave).toHaveBeenCalled())
    const saved = onSave.mock.calls[0][0]
    expect(saved.cells[0].text).toBe('A. C. Bedford')
  })

  it('Invio scende di riga e seleziona la cella sotto', () => {
    render(<SheetEditor grid={emptyGrid(2, 3)} onSave={vi.fn().mockResolvedValue('')} />)
    const a1 = screen.getByLabelText('Cella riga 1, colonna 1')
    fireEvent.keyDown(a1, { key: 'Enter' })
    const a2 = screen.getByLabelText('Cella riga 2, colonna 1') as HTMLInputElement
    expect(document.activeElement).toBe(a2)
  })

  it('freccia destra a fine testo passa alla cella accanto, nel mezzo no', () => {
    render(<SheetEditor grid={emptyGrid(1, 3)} onSave={vi.fn().mockResolvedValue('')} />)
    const a1 = screen.getByLabelText('Cella riga 1, colonna 1') as HTMLInputElement
    fireEvent.change(a1, { target: { value: 'Kobe' } })
    a1.focus()
    // Nel mezzo del testo la freccia muove il cursore, non la cella.
    a1.setSelectionRange(2, 2)
    fireEvent.keyDown(a1, { key: 'ArrowRight' })
    expect(document.activeElement).toBe(a1)
    // A fine testo passa alla cella successiva.
    a1.setSelectionRange(4, 4)
    fireEvent.keyDown(a1, { key: 'ArrowRight' })
    const b1 = screen.getByLabelText('Cella riga 1, colonna 2') as HTMLInputElement
    expect(document.activeElement).toBe(b1)
  })

  it('il bottone Salva chiama onSave e mostra l’OTSL restituito', async () => {
    const onSave = vi.fn().mockResolvedValue('<fcel>A<lcel><nl>')
    render(<SheetEditor grid={emptyGrid(2, 2)} onSave={onSave} />)
    fireEvent.click(screen.getByRole('button', { name: /Salva griglia/ }))
    await waitFor(() => expect(screen.getByText(/<fcel>A/)).toBeTruthy())
  })

  it('più/meno righe e colonne ridimensionano il foglio', () => {
    render(<SheetEditor grid={emptyGrid(2, 2)} onSave={vi.fn().mockResolvedValue('')} />)
    fireEvent.click(screen.getByRole('button', { name: 'Aggiungi una riga' }))
    fireEvent.click(screen.getByRole('button', { name: 'Aggiungi una colonna' }))
    expect(screen.getByLabelText('Cella riga 3, colonna 3')).toBeTruthy()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    fireEvent.click(screen.getByRole('button', { name: 'Togli una riga' }))
    expect(screen.queryByLabelText('Cella riga 3, colonna 1')).toBeNull()
  })
})
