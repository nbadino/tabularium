// @vitest-environment jsdom
/**
 * Structure guard switch: follows the profile until the project sets it, and
 * the reset returns it to the profile's default.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom/vitest'

vi.mock('../../lib/api', () => ({ apiGet: vi.fn(), apiPut: vi.fn() }))

import { apiGet, apiPut } from '../../lib/api'
import TableGuardToggle from './TableGuardToggle'
import StructureRepairsMenu from './StructureRepairsMenu'

const mockGet = vi.mocked(apiGet) as unknown as ReturnType<typeof vi.fn>
const mockPut = vi.mocked(apiPut) as unknown as ReturnType<typeof vi.fn>
const base = { profile_id: 'shipping-registers-1900s', profile_default: true, declares_columns: true }

afterEach(() => {
  cleanup()
  mockGet.mockReset()
  mockPut.mockReset()
})

describe('TableGuardToggle', () => {
  it('switches the guard off for the project and back to the profile default', async () => {
    mockGet.mockResolvedValue({ ...base, enabled: true, source: 'profile' })
    mockPut.mockResolvedValueOnce({ ...base, enabled: false, source: 'project' })
    mockPut.mockResolvedValueOnce({ ...base, enabled: true, source: 'profile' })
    render(<TableGuardToggle projectId={4} />)
    const box = await screen.findByRole('checkbox')
    expect(box).toBeChecked()
    expect(screen.getByText(/shipping-registers-1900s/)).toBeInTheDocument()

    await userEvent.click(box)
    await waitFor(() => expect(mockPut).toHaveBeenCalledWith('/projects/4/table-guard', { enabled: false }))
    await waitFor(() => expect(screen.getByRole('checkbox')).not.toBeChecked())

    await userEvent.click(screen.getByRole('button'))
    await waitFor(() => expect(mockPut).toHaveBeenLastCalledWith('/projects/4/table-guard', { enabled: null }))
    await waitFor(() => expect(screen.getByRole('checkbox')).toBeChecked())
  })
})

describe('StructureRepairsMenu', () => {
  it('lists every repair, 1-based, and renders nothing without repairs', () => {
    const { container, rerender } = render(<StructureRepairsMenu repairs={[]} />)
    expect(container).toBeEmptyDOMElement()
    rerender(
      <StructureRepairsMenu
        repairs={[
          { rule: 'glued_value_split', row: 4, col: 0, before: ['Ordu (D.I.M.) Tu', 'R', ''], after: ['Ordu (D.I.M.)', 'Tu', 'R'] },
          { rule: 'column_merged', col: 4, header: ['Gr', 'as'], rows: [] },
        ]}
      />,
    )
    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0].textContent).toContain('5')
    expect(items[0].textContent).toContain('Ordu (D.I.M.) Tu | R |  → Ordu (D.I.M.) | Tu | R')
    expect(items[1].textContent).toContain('Gr | as')
  })
})
