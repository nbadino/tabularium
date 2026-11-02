// @vitest-environment jsdom
/** The Studio header: where am I, one way to finish the page, J/K to move. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router'
import StudioHeader, { isTeamWork } from './StudioHeader'

afterEach(cleanup)

function setup(over: Partial<Parameters<typeof StudioHeader>[0]> = {}) {
  const props = {
    backTo: '/progetti/5',
    pageName: 'p2.png',
    index: 1,
    total: 5,
    finish: { kind: 'verify' as const, onClick: vi.fn(), disabled: false },
    onPrev: vi.fn(),
    onNext: vi.fn(),
    sidebarOpen: true,
    onToggleSidebar: vi.fn(),
    ...over,
  }
  render(<MemoryRouter><StudioHeader {...props} /></MemoryRouter>)
  return props
}

describe('StudioHeader', () => {
  it('says where you are and leads back to the pages', () => {
    setup()
    expect(screen.getByRole('link', { name: /Pagine/ })).toHaveAttribute('href', '/progetti/5')
    expect(screen.getByText('p2.png')).toBeInTheDocument()
    expect(screen.getByText('Pagina 2 di 5')).toBeInTheDocument()
  })

  it('finishes the page with one action, also from the keyboard', () => {
    const props = setup()
    fireEvent.click(screen.getByRole('button', { name: /Verifica e vai alla successiva/ }))
    fireEvent.keyDown(window, { key: 'Enter', ctrlKey: true })
    expect(props.finish?.onClick).toHaveBeenCalledTimes(2)
  })

  it('an approved page only moves on', () => {
    setup({ finish: { kind: 'next', onClick: vi.fn(), disabled: false } })
    expect(screen.getByRole('button', { name: /Vai alla successiva/ })).toBeInTheDocument()
  })

  it('J and K move between pages, but not while typing', () => {
    const props = setup()
    fireEvent.keyDown(window, { key: 'j' })
    fireEvent.keyDown(window, { key: 'k' })
    const input = document.createElement('textarea')
    document.body.appendChild(input)
    fireEvent.keyDown(input, { key: 'j' })
    expect(props.onNext).toHaveBeenCalledTimes(1)
    expect(props.onPrev).toHaveBeenCalledTimes(1)
    input.remove()
  })
})

describe('isTeamWork', () => {
  it('is off for a project of one person with unassigned pages', () => {
    expect(isTeamWork({ members_count: 1 }, { work: null })).toBe(false)
    expect(isTeamWork(undefined, undefined)).toBe(false)
  })
  it('is on with more members or an assigned page', () => {
    expect(isTeamWork({ members_count: 3 }, { work: null })).toBe(true)
    expect(isTeamWork({ members_count: 1 }, { work: { assignee_id: 4, reviewer_id: null } })).toBe(true)
  })
})
