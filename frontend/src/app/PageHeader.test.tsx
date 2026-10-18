import { afterEach, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { PageHeader } from './ui'
afterEach(cleanup)
it('lascia visibili titolo e azione, con la spiegazione chiusa finché si apre Guida', () => {
  render(<PageHeader title="Dataset" intro="Spiegazione facoltativa" aux={<button>Esporta</button>} />)
  expect(screen.getByRole('heading', { name: 'Dataset' })).toBeVisible()
  expect(screen.getByRole('button', { name: 'Esporta' })).toBeVisible()
  expect(screen.getByText('Spiegazione facoltativa')).not.toBeVisible()
  fireEvent.click(screen.getByText('Guida'))
  expect(screen.getByText('Spiegazione facoltativa')).toBeVisible()
})
