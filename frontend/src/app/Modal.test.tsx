// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, expect, it } from 'vitest'
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
