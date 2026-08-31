// @vitest-environment jsdom
/**
 * Lo splitter rende adattabili le tre colonne dello studio. Contratto:
 * trascinamento e frecce governano la larghezza del pannello adiacente,
 * il doppio click torna alla misura di riposo, i valori restano nel range.
 */
import { beforeAll, afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import Splitter from './Splitter'

afterEach(cleanup)

beforeAll(() => {
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
    Element.prototype.releasePointerCapture = () => {}
  }
})

function renderSplitter(onChange = vi.fn(), onReset = vi.fn(), side: 'left' | 'right' = 'left') {
  render(
    <Splitter
      value={240}
      min={176}
      max={420}
      side={side}
      label="Colonna pagine"
      onChange={onChange}
      onReset={onReset}
    />,
  )
  return screen.getByRole('separator', { name: 'Colonna pagine' })
}

describe('Splitter', () => {
  it('il trascinamento a destra allarga il pannello a sinistra del manico', () => {
    const onChange = vi.fn()
    const handle = renderSplitter(onChange, vi.fn(), 'left')
    fireEvent.pointerDown(handle, { button: 0, clientX: 100, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 140 })
    expect(onChange).toHaveBeenLastCalledWith(280)
    fireEvent.pointerUp(handle, { pointerId: 1 })
  })

  it('il trascinamento a destra restringe il pannello a destra del manico', () => {
    const onChange = vi.fn()
    const handle = renderSplitter(onChange, vi.fn(), 'right')
    fireEvent.pointerDown(handle, { button: 0, clientX: 100, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 60 })
    expect(onChange).toHaveBeenLastCalledWith(280)
    fireEvent.pointerUp(handle, { pointerId: 1 })
  })

  it('il valore resta nel range consentito', () => {
    const onChange = vi.fn()
    const handle = renderSplitter(onChange)
    fireEvent.pointerDown(handle, { button: 0, clientX: 100, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 1000 })
    expect(onChange).toHaveBeenLastCalledWith(420)
    fireEvent.pointerMove(handle, { clientX: -1000 })
    expect(onChange).toHaveBeenLastCalledWith(176)
    fireEvent.pointerUp(handle, { pointerId: 1 })
  })

  it('le frecce da tastiera muovono la larghezza a passi fissi', () => {
    const onChange = vi.fn()
    const handle = renderSplitter(onChange)
    fireEvent.keyDown(handle, { key: 'ArrowRight' })
    expect(onChange).toHaveBeenLastCalledWith(256)
    fireEvent.keyDown(handle, { key: 'ArrowLeft' })
    expect(onChange).toHaveBeenLastCalledWith(224)
  })

  it('il doppio click riporta alla larghezza di riposo', () => {
    const onReset = vi.fn()
    const handle = renderSplitter(vi.fn(), onReset)
    fireEvent.doubleClick(handle)
    expect(onReset).toHaveBeenCalledTimes(1)
  })

  it('un trascinamento col tasto destro non muove nulla', () => {
    const onChange = vi.fn()
    const handle = renderSplitter(onChange)
    fireEvent.pointerDown(handle, { button: 2, clientX: 100, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 200 })
    expect(onChange).not.toHaveBeenCalled()
  })
})
