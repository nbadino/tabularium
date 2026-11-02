import { afterEach, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { PageHeader } from './ui'
afterEach(cleanup)
it('shows the title and its action, with no folded guide', () => {
  render(<PageHeader title="Dataset" aux={<button>Esporta</button>} />)
  expect(screen.getByRole('heading', { name: 'Dataset' })).toBeVisible()
  expect(screen.getByRole('button', { name: 'Esporta' })).toBeVisible()
  expect(screen.queryByText('Guida')).toBeNull()
})
