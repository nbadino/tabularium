// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { Modal } from './ui'

afterEach(cleanup)

it('la compilazione mantiene il focus e Escape usa lo stato corrente', () => {
  function Form() {
    const [open, setOpen] = useState(false)
    const [name, setName] = useState('')
    const [closedValue, setClosedValue] = useState('')
    return <>
      <button onClick={() => setOpen(true)}>Apri</button>
      <output>{closedValue}</output>
      {open && <Modal title="Progetto" onClose={() => { setClosedValue(name); setOpen(false) }}>
        <label>Nome<input data-autofocus value={name} onChange={(e) => setName(e.target.value)} /></label>
      </Modal>}
    </>
  }
  render(<Form />)
  const opener = screen.getByRole('button', { name: 'Apri' })
  opener.focus()
  fireEvent.click(opener)
  const input = screen.getByRole('textbox', { name: 'Nome' })
  expect(input).toHaveFocus()
  fireEvent.change(input, { target: { value: 'Archivio 1904' } })
  expect(input).toHaveFocus()
  fireEvent.keyDown(input, { key: 'Escape' })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.getByRole('status')).toHaveTextContent('Archivio 1904')
  expect(opener).toHaveFocus()
})

it('un Escape già consumato dall’editor annidato non chiude la modale', () => {
  render(
    <Modal title="Tabella" onClose={() => {}}>
      <input data-autofocus aria-label="Cella" onKeyDown={(event) => event.preventDefault()} />
    </Modal>,
  )
  fireEvent.keyDown(screen.getByRole('textbox', { name: 'Cella' }), { key: 'Escape' })
  expect(screen.getByRole('dialog', { name: 'Tabella' })).toBeInTheDocument()
})

it('un editor esplicito ignora Esc e il clic sul backdrop', () => {
  const onClose = vi.fn()
  render(
    <Modal title="Tabella" onClose={onClose} closeOnBackdrop={false} closeOnEscape={false}>
      <input data-autofocus aria-label="Cella" />
    </Modal>,
  )
  fireEvent.keyDown(screen.getByRole('textbox', { name: 'Cella' }), { key: 'Escape' })
  fireEvent.mouseDown(screen.getByRole('dialog', { name: 'Tabella' }).parentElement!)
  expect(onClose).not.toHaveBeenCalled()
  expect(screen.getByRole('dialog', { name: 'Tabella' })).toBeInTheDocument()
})

it('keeps compact status controls in the dialog title row', () => {
  render(
    <Modal title="Tabella" onClose={() => {}} headerAux={<label><input type="checkbox" /> Blocco</label>}>
      <p>Foglio</p>
    </Modal>,
  )
  expect(screen.getByRole('dialog', { name: 'Tabella' })).toContainElement(
    screen.getByRole('checkbox', { name: 'Blocco' }),
  )
})
