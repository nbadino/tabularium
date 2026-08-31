// @vitest-environment jsdom
/**
 * Test del rischio P1 segnalato dalla review: la gara fra autosave (debounce
 * 700 ms) e cambio pagina. La protezione è esplicita: `dirty`/`getDirty` per
 * decidere, `flush()` per attendere il salvataggio prima di navigare, e un
 * salvataggio d'emergenza allo smontaggio.
 */
import { afterEach, describe, expect, it, vi, beforeEach } from 'vitest'
import { cleanup, renderHook, act } from '@testing-library/react'
import type { BlockOut, PageItem } from '../lib/types'
import { useAnnotationState } from './useAnnotationState'

const apiPut = vi.fn()
vi.mock('../lib/api', () => ({
  apiPut: (...args: unknown[]) => apiPut(...args),
}))

vi.mock('./useHistory', async (importOriginal) => {
  // useHistory è già collaudato: qui serve solo che esponga l'API reattiva.
  return await importOriginal()
})

function page(id: number): PageItem {
  return {
    id,
    project_id: 1,
    rel_path: `p${id}.png`,
    abs_path: `/tmp/p${id}.png`,
    source_kind: 'image',
    pdf_page: null,
    width: 1000,
    height: 1400,
    issue_date: null,
    issue_no: null,
    page_no: null,
    page_type: 'index',
    status: 'new',
    created_at: '2026-01-01T00:00:00',
  }
}

function blockOut(id: number): BlockOut {
  return {
    id,
    page_id: 1,
    label: 'Text',
    kind: 'rect',
    points: [[0, 0], [10, 10]],
    content: '',
    order_idx: 1,
    confirmed: false,
    prefill_source: null,
    updated_at: '',
  } as unknown as BlockOut
}

beforeEach(() => {
  apiPut.mockReset()
  apiPut.mockResolvedValue({ items: [blockOut(101)] })
})

afterEach(() => cleanup())

describe('useAnnotationState — flush e cambio pagina', () => {
  it('una modifica rende lo stato sporco, flush lo pulisce e salva sulla pagina giusta', async () => {
    const { result } = renderHook(() => useAnnotationState(page(1), 1, []))

    expect(result.current.dirty).toBe(false)
    expect(result.current.getDirty()).toBe(false)

    act(() => {
      result.current.addBlock({
        kind: 'rect',
        points: [{ x: 0, y: 0 }, { x: 100, y: 100 }],
        label: 'Text',
      })
    })
    expect(result.current.dirty).toBe(true)
    expect(result.current.getDirty()).toBe(true)

    await act(async () => {
      await result.current.flush()
    })
    expect(apiPut).toHaveBeenCalledTimes(1)
    expect(apiPut).toHaveBeenCalledWith('/pages/1/annotations', expect.anything())
    expect(result.current.dirty).toBe(false)
    expect(result.current.getDirty()).toBe(false)
  })

  it('flush su stato pulito non chiama il server', async () => {
    const { result } = renderHook(() => useAnnotationState(page(1), 1, []))
    await act(async () => {
      await result.current.flush()
    })
    expect(apiPut).not.toHaveBeenCalled()
  })

  it('il cambio pagina dopo flush non perde le modifiche né le attribuisce alla pagina nuova', async () => {
    const initial = page(1)
    const { result, rerender } = renderHook(
      ({ p }: { p: PageItem }) => useAnnotationState(p, 1, []),
      { initialProps: { p: initial } },
    )

    act(() => {
      result.current.addBlock({
        kind: 'rect',
        points: [{ x: 0, y: 0 }, { x: 100, y: 100 }],
        label: 'Text',
      })
    })

    // È la sequenza della UI: flush esplicito, poi cambio pagina.
    await act(async () => {
      await result.current.flush()
    })
    rerender({ p: page(2) })

    // L'unico PUT va alla pagina 1: nessun salvataggio tardivo sulla 2.
    expect(apiPut).toHaveBeenCalledTimes(1)
    expect(apiPut).toHaveBeenCalledWith('/pages/1/annotations', expect.anything())
  })

  it('lo smontaggio con lavoro sporco salva in emergenza', async () => {
    const { result, unmount } = renderHook(() => useAnnotationState(page(3), 1, []))
    act(() => {
      result.current.addBlock({
        kind: 'rect',
        points: [{ x: 0, y: 0 }, { x: 100, y: 100 }],
        label: 'Text',
      })
    })
    unmount()
    // Fire-and-forget: il PUT parte, anche se nessuno lo attende più.
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(apiPut).toHaveBeenCalledWith('/pages/3/annotations', expect.anything())
  })
})
