// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { Select } from './ui'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

const OPTIONS = [
  { value: 'Text', label: 'Text' },
  { value: 'Table', label: 'Table' },
]

describe('Select', () => {
  it('sceglie una voce, ripiega e poi chiude', () => {
    vi.useFakeTimers()
    const onChange = vi.fn()
    render(<Select value="Text" options={OPTIONS} onChange={onChange} ariaLabel="Classe" />)
    const details = screen.getByRole('combobox').closest('details')!
    fireEvent.click(screen.getByRole('combobox'))
    expect(details.open).toBe(true)

    fireEvent.click(screen.getByRole('option', { name: 'Table' }))
    expect(onChange).toHaveBeenCalledWith('Table')
    // Resta aperto mentre si ripiega, poi si chiude.
    expect(details.dataset.closing).toBeDefined()
    act(() => vi.advanceTimersByTime(450))
    expect(details.open).toBe(false)
    expect(details.dataset.closing).toBeUndefined()
  })

  it('la freccia apre, la voce già scelta non rilancia onChange', () => {
    const onChange = vi.fn()
    render(<Select value="Text" options={OPTIONS} onChange={onChange} />)
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'ArrowDown' })
    expect(screen.getByRole('combobox').closest('details')!.open).toBe(true)
    expect(screen.getByRole('option', { name: 'Text' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(screen.getByRole('option', { name: 'Text' }))
    expect(onChange).not.toHaveBeenCalled()
  })

  it('una lettera porta alla voce che comincia così, e non esce dalla lista', async () => {
    const onWindowKey = vi.fn()
    window.addEventListener('keydown', onWindowKey)
    render(
      <Select
        value="Text"
        options={[...OPTIONS, { value: 'Title', label: 'Title' }]}
        onChange={() => {}}
      />,
    )
    const combo = screen.getByRole('combobox')
    fireEvent.click(combo)
    // `toggle` arriva dopo l'apertura, in un task a parte (anche nel browser).
    await waitFor(() => expect(combo).toHaveAttribute('aria-expanded', 'true'))
    screen.getByRole('option', { name: 'Text' }).focus()
    fireEvent.keyDown(screen.getByRole('option', { name: 'Text' }), { key: 't' })
    expect(screen.getByRole('option', { name: 'Table' })).toHaveFocus()
    fireEvent.keyDown(screen.getByRole('option', { name: 'Table' }), { key: 't' })
    expect(screen.getByRole('option', { name: 'Title' })).toHaveFocus()
    expect(onWindowKey).not.toHaveBeenCalled()
    window.removeEventListener('keydown', onWindowKey)
  })

  it('disabilitato non si apre', () => {
    render(<Select value="Text" options={OPTIONS} onChange={() => {}} disabled />)
    fireEvent.click(screen.getByRole('combobox'))
    expect(screen.getByRole('combobox').closest('details')!.open).toBe(false)
  })
})
