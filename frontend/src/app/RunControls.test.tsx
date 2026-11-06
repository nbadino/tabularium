// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router'
import type { RecognitionRun } from '../lib/types'
import { ActiveRunsIndicator, RecognitionRunControls } from './RunControls'

const api = vi.hoisted(() => ({ apiGet: vi.fn(), apiPost: vi.fn() }))
vi.mock('../lib/api', () => api)

const run = (over: Partial<RecognitionRun> = {}): RecognitionRun => ({
  id: 5, project_id: 4, state: 'running', engine: 'model', mode: 'replace_drafts', model_mode: 'native',
  model_name: 'm', adapter_id: 'a', provider: 'llamaindex', endpoint: null, stop_policy: 'none',
  total_pages: 557, completed_pages: 9, succeeded_pages: 9, failed_pages: 0, error: null,
  created_at: '', started_at: null, heartbeat_at: null, ended_at: null, paused: false, ...over,
})

describe('RecognitionRunControls', () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); api.apiGet.mockReset(); api.apiPost.mockReset() })

  it('pauses a running session and offers to resume it', async () => {
    api.apiPost.mockResolvedValue(run({ paused: true }))
    const onChange = vi.fn()
    render(<RecognitionRunControls run={run()} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Metti in pausa' }))
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ paused: true })))
    expect(api.apiPost).toHaveBeenCalledWith('/projects/4/recognition-runs/5/pause')
    cleanup()
    render(<RecognitionRunControls run={run({ paused: true })} onChange={onChange} />)
    expect(screen.getByRole('button', { name: 'Riprendi' })).toBeInTheDocument()
  })

  it('stops only after confirmation', async () => {
    const ask = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    api.apiPost.mockResolvedValue(run({ state: 'cancelled' }))
    render(<RecognitionRunControls run={run()} onChange={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Ferma' }))
    await waitFor(() => expect(ask).toHaveBeenCalledTimes(1))
    expect(api.apiPost).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Ferma' }))
    await waitFor(() => expect(api.apiPost).toHaveBeenCalledWith('/projects/4/recognition-runs/5/cancel'))
  })

  it('shows nothing for a session that has ended', () => {
    const { container } = render(<RecognitionRunControls run={run({ state: 'finished' })} onChange={() => {}} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('ActiveRunsIndicator', () => {
  afterEach(() => { cleanup(); api.apiGet.mockReset() })

  it('appears in the header only while a session is running', async () => {
    api.apiGet.mockResolvedValue({ items: [run(), run({ id: 4, state: 'finished' })] })
    render(<MemoryRouter><ActiveRunsIndicator /></MemoryRouter>)
    expect(await screen.findByText('Riconoscimento 9/557')).toBeInTheDocument()
    expect(api.apiGet).toHaveBeenCalledWith('/recognition-runs')
  })

  it('stays hidden with no session in progress', async () => {
    api.apiGet.mockResolvedValue({ items: [run({ state: 'finished' })] })
    const { container } = render(<MemoryRouter><ActiveRunsIndicator /></MemoryRouter>)
    await waitFor(() => expect(api.apiGet).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })
})
