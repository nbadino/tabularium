// @vitest-environment jsdom
/**
 * Overlay dei confini: la selezione di un filetto è ciò che abilita
 * «Rifiuta confine». Un click che risalisse alla superficie la azzererebbe
 * subito, e il pulsante resterebbe disabilitato per sempre — verificato a
 * schermo, oltre che qui.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import TableGridOverlay from './TableGridOverlay'

afterEach(() => cleanup())

function renderOverlay() {
  const onMove = vi.fn()
  const onInsert = vi.fn()
  const onDrop = vi.fn()
  render(
    <TableGridOverlay
      cropUrl="/api/blocks/1/crop"
      vlines={[0, 0.25, 0.5, 0.75, 1]}
      hlines={[0, 0.5, 1]}
      rows={2}
      onMove={onMove}
      onInsert={onInsert}
      onDrop={onDrop}
    />,
  )
  return { onMove, onInsert, onDrop }
}

const line = (n: number) => screen.getByLabelText(`Filetto verticale ${n}`)
const reject = () => screen.getByRole('button', { name: 'Rifiuta confine' })

describe('TableGridOverlay', () => {
  it('il click su un filetto lo tiene selezionato e abilita il rifiuto', () => {
    const { onDrop } = renderOverlay()
    expect(reject()).toBeDisabled()

    fireEvent.pointerDown(line(2), { pointerId: 1 })
    // Il click segue sempre il pointerdown: è quello che prima azzerava la
    // selezione risalendo alla superficie.
    fireEvent.click(line(2))

    expect(reject()).toBeEnabled()
    fireEvent.click(reject())
    expect(onDrop).toHaveBeenCalledWith('v', 1)
  })

  it('i bordi del contenuto si spostano ma non si rifiutano', () => {
    const { onDrop } = renderOverlay()
    fireEvent.pointerDown(line(1), { pointerId: 1 })
    fireEvent.click(line(1))
    expect(reject()).toBeDisabled()
    expect(onDrop).not.toHaveBeenCalled()
  })

  it('un click sul vuoto deseleziona', () => {
    renderOverlay()
    fireEvent.pointerDown(line(2), { pointerId: 1 })
    fireEvent.click(line(2))
    expect(reject()).toBeEnabled()

    fireEvent.click(screen.getByAltText('Ritaglio della tabella sulla pagina'))
    expect(reject()).toBeDisabled()
  })

  it('la freccia sposta il filetto selezionato', () => {
    const { onMove } = renderOverlay()
    fireEvent.pointerDown(line(2), { pointerId: 1 })
    fireEvent.click(line(2))
    onMove.mockClear()
    // «Filetto verticale 2» è il confine all'indice 1: 0,25 sul ritaglio.
    // Una freccia sposta di un millesimo, con Shift di un centesimo.
    fireEvent.keyDown(line(2), { key: 'ArrowRight' })
    expect(onMove).toHaveBeenCalledWith('v', 1, 0.251)
    onMove.mockClear()
    fireEvent.keyDown(line(2), { key: 'ArrowLeft', shiftKey: true })
    expect(onMove).toHaveBeenCalledWith('v', 1, 0.24)
  })
})
