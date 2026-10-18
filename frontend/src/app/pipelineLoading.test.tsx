import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { apiGet } from '../lib/api'
import { usePipelineState } from './pipeline'
vi.mock('../lib/api', () => ({ apiGet: vi.fn(async () => ({})) }))
afterEach(() => { cleanup(); vi.clearAllMocks() })
it('Training non richiede di nuovo lo stato del training né run inutilizzati', async () => {
  const { result } = renderHook(() => usePipelineState(3, { training: false, runs: false }))
  await waitFor(() => expect(result.current.dataset).toEqual({}))
  expect(vi.mocked(apiGet).mock.calls.map(([url]) => url)).toEqual(['/projects/3/workflow', '/projects/3/datasets'])
})
