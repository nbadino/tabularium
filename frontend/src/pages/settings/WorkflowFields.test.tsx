// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { apiGet } from '../../lib/api'
import { WorkflowControl, type WorkflowFieldSpec } from './WorkflowFields'

vi.mock('../../lib/api', () => ({ apiGet: vi.fn() }))

const FIELD: WorkflowFieldSpec = { key: 'layout_model', kind: 'layout_model', default: 'official', label: 'settings.xiaomiLayoutModel' }

afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('layout model control', () => {
  it('offers the official weights and every trained layout run', async () => {
    vi.mocked(apiGet).mockResolvedValue({ items: [{ id: '4/run_1', project_id: 4, run_id: 'run_1' }] })
    const onChange = vi.fn()
    render(<WorkflowControl field={FIELD} value="official" onChange={onChange} disabled={false} id="lm" label="Layout" />)
    const select = screen.getByRole('combobox')
    await waitFor(() => expect(select.querySelectorAll('option')).toHaveLength(2))
    expect(apiGet).toHaveBeenCalledWith('/system/layout-checkpoints')
    fireEvent.change(select, { target: { value: '4/run_1' } })
    expect(onChange).toHaveBeenCalledWith('4/run_1')
  })

  it('keeps a saved run visible when it is no longer listed', async () => {
    vi.mocked(apiGet).mockResolvedValue({ items: [] })
    render(<WorkflowControl field={FIELD} value="9/run_gone" onChange={vi.fn()} disabled={false} id="lm" label="Layout" />)
    const select = screen.getByRole('combobox') as HTMLSelectElement
    await waitFor(() => expect(apiGet).toHaveBeenCalled())
    expect(select.value).toBe('9/run_gone')
  })
})
