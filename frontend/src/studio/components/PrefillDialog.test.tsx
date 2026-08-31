// @vitest-environment jsdom
/**
 * Test del rischio P0 segnalato dalla review: il prefill non deve più poter
 * cancellare lavoro esistente con un solo clic. Il dialog mostra i conteggi,
 * distingue le tre modalità e trasmette la scelta all'azione.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import PrefillDialog from './PrefillDialog'
import type { PrefillPageSummary } from '../prefill'

afterEach(() => cleanup())

function renderDialog(summary: PrefillPageSummary, onRun = vi.fn(), onClose = vi.fn()) {
  render(
    <PrefillDialog summary={summary} busy={false} onRun={onRun} onClose={onClose} />,
  )
  return { onRun, onClose }
}

describe('PrefillDialog', () => {
  it('pagina con bozze: propone «Sostituisci le bozze» e mostra i conteggi', () => {
    renderDialog({ blocks: 6, drafts: 3, tables: 1 })
    const drafts = screen.getByRole('radio', { name: /Sostituisci le bozze/ })
    expect(drafts).toBeChecked()
    expect(screen.getByText(/6 blocchi sulla pagina/)).toBeTruthy()
    expect(screen.getByText(/3 di questi sono bozze/)).toBeTruthy()
  })

  it('pagina con solo lavoro umano: il default è «Aggiungi», non la cancellazione', () => {
    renderDialog({ blocks: 4, drafts: 0, tables: 1 })
    expect(screen.getByRole('radio', { name: /Aggiungi/ })).toBeChecked()
  })

  it('«Sostituisci tutto» dichiara anche la distruzione delle griglie', () => {
    renderDialog({ blocks: 5, drafts: 2, tables: 2 })
    fireEvent.click(screen.getByRole('radio', { name: /Sostituisci tutto/ }))
    // 5 blocchi rimossi, 2 griglie tabellari con essi.
    expect(screen.getByText(/rimuoverà 5 blocchi/)).toBeTruthy()
    expect(screen.getByText(/griglie di 2 blocchi Table/)).toBeTruthy()
    // E avverte che tocca il lavoro umano.
    expect(screen.getByText(/Attenzione/)).toBeTruthy()
  })

  it('«Aggiungi» non promette di rimuovere nulla', () => {
    renderDialog({ blocks: 5, drafts: 2, tables: 1 })
    fireEvent.click(screen.getByRole('radio', { name: /Aggiungi/ }))
    // Il piano di sostituzione non è mostrato: merge non cancella.
    expect(screen.queryByText(/rimuoverà/)).toBeNull()
  })

  it('conferma trasmette la modalità scelta', () => {
    const { onRun } = renderDialog({ blocks: 5, drafts: 2, tables: 1 })
    fireEvent.click(screen.getByRole('radio', { name: /Sostituisci tutto/ }))
    fireEvent.click(screen.getByRole('button', { name: /Esegui prefill/ }))
    expect(onRun).toHaveBeenCalledWith('replace_all')
  })

  it('annulla chiude senza eseguire nulla', () => {
    const { onClose, onRun } = renderDialog({ blocks: 5, drafts: 2, tables: 1 })
    fireEvent.click(screen.getByRole('button', { name: /Annulla/ }))
    expect(onClose).toHaveBeenCalled()
    expect(onRun).not.toHaveBeenCalled()
  })
})
