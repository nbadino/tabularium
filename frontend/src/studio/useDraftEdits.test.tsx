import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { useDraftEdits } from './useDraftEdits'
afterEach(cleanup)
it('flush salva l’ultima trascrizione anche prima del debounce e attende la risposta', async () => {
  let finish!: () => void
  const write = vi.fn(() => new Promise<void>((resolve) => { finish = resolve }))
  const { result } = renderHook(() => useDraftEdits(write, vi.fn()))
  act(() => { result.current.schedule(7, 'Aur'); result.current.schedule(7, 'Aurora') })
  let completed = false
  const flush = result.current.flush().then(() => { completed = true })
  await act(async () => { await Promise.resolve() })
  expect(write).toHaveBeenCalledWith(7, 'Aurora'); expect(completed).toBe(false)
  await act(async () => { finish(); await flush })
  expect(result.current.dirty).toBe(false)
})
it('una scrittura fallita resta sporca e impedisce la consegna', async () => {
  const write = vi.fn().mockRejectedValue(new Error('offline'))
  const { result } = renderHook(() => useDraftEdits(write, vi.fn()))
  act(() => result.current.schedule(7, 'Aurora'))
  await act(async () => { await expect(result.current.flush()).rejects.toThrow('offline') })
  expect(result.current.dirty).toBe(true)
  write.mockResolvedValue(undefined)
  await act(async () => { await result.current.flush() })
  expect(result.current.dirty).toBe(false)
})
