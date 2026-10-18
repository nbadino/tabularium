import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import {
  connectPage,
  disconnectPage,
  notifyEditing,
  onAnnotationsChanged,
  useLive,
  type AnnotationsChanged,
} from './live'

/** WebSocket finto: il test guida a mano open/message/close. */
class FakeWS {
  static instances: FakeWS[] = []
  readyState = 0
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: ((e: { code: number }) => void) | null = null
  onerror: (() => void) | null = null

  constructor(public url: string) {
    FakeWS.instances.push(this)
  }

  send(data: string): void {
    this.sent.push(data)
  }

  close(): void {
    this.readyState = 3
    this.onclose?.({ code: 1000 })
  }

  open(): void {
    this.readyState = 1
    this.onopen?.()
  }

  emit(obj: unknown): void {
    this.onmessage?.({ data: JSON.stringify(obj) })
  }
}

/** Ultima istanza creata (il test è sincrono: una sola connessione per volta). */
function lastFakeWS(): FakeWS {
  return FakeWS.instances[FakeWS.instances.length - 1]
}

beforeEach(() => {
  FakeWS.instances = []
  ;(globalThis as unknown as { WebSocket: unknown }).WebSocket = FakeWS
})

afterEach(() => {
  disconnectPage()
  vi.useRealTimers()
})

describe('live client', () => {
  it('apre il canale, riceve hello e presence', () => {
    const { result } = renderHook(() => useLive())
    act(() => connectPage(1))
    const ws = lastFakeWS()
    expect(result.current.status).toBe('connecting')

    act(() => ws.open())
    expect(result.current.status).toBe('open')

    act(() =>
      ws.emit({
        type: 'hello',
        page_id: 1,
        self: { conn_id: 'me' },
        can_write: true,
        participants: [
          { conn_id: 'me', user_id: 1, username: 'me', color: '#000' },
          { conn_id: 'o', user_id: 2, username: 'bella', color: '#111' },
        ],
      }),
    )
    expect(result.current.selfId).toBe('me')
    expect(result.current.canWrite).toBe(true)
    expect(result.current.participants.map((p) => p.connId)).toEqual(['me', 'o'])

    act(() => ws.emit({ type: 'presence', participants: [{ conn_id: 'o', user_id: 2, username: 'bella', color: '#111' }] }))
    expect(result.current.participants).toHaveLength(1)
  })

  it('propaga annotations_changed ai listener', () => {
    const events: AnnotationsChanged[] = []
    const off = onAnnotationsChanged((e) => events.push(e))
    act(() => connectPage(1))
    const ws = lastFakeWS()
    act(() => ws.open())
    act(() =>
      ws.emit({
        type: 'annotations_changed',
        page_id: 1,
        revision: 7,
        action: 'sync',
        author: { id: 2, username: 'bella' },
      }),
    )
    expect(events).toHaveLength(1)
    expect(events[0]).toMatchObject({ pageId: 1, revision: 7, authorId: 2, authorName: 'bella' })
    off()
  })

  it('segnala la scrittura agli altri quando è aperto', () => {
    act(() => connectPage(1))
    const ws = lastFakeWS()
    act(() => ws.open())
    act(() => notifyEditing(true))
    expect(ws.sent.some((m: string) => m.includes('"editing"') && m.includes('true'))).toBe(true)
  })

  it('non si riconnette su chiusura applicativa (permesso/inesistente)', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useLive())
    act(() => connectPage(3))
    const ws = lastFakeWS()
    const before = FakeWS.instances.length
    act(() => {
      ws.readyState = 3
      ws.onclose?.({ code: 4401 })
    })
    act(() => vi.advanceTimersByTime(6000))
    expect(result.current.status).toBe('closed')
    expect(FakeWS.instances.length).toBe(before)
  })

  it('si riconnette dopo una chiusura inattesa', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useLive())
    act(() => connectPage(4))
    const ws = lastFakeWS()
    const before = FakeWS.instances.length
    act(() => {
      ws.readyState = 3
      ws.onclose?.({ code: 1006 })
    })
    expect(result.current.status).toBe('connecting')
    act(() => vi.advanceTimersByTime(1100))
    expect(FakeWS.instances.length).toBe(before + 1)
  })
})
