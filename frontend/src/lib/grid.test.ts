import { describe, expect, it } from 'vitest'
import { emptyGrid, mergeRange, splitCell, resizeGrid } from './grid'

describe('table grid operations', () => {
  it('merges and splits a physical range without losing anchor text', () => {
    const base = emptyGrid(2, 2)
    base.cells[0].text = 'header'
    const merged = mergeRange(base, 0, 0, 0, 1)
    expect(merged?.cells).toHaveLength(3)
    expect(merged?.cells.find((c) => c.colspan === 2)?.text).toBe('header')
    const split = merged && splitCell(merged, 0, 1)
    expect(split?.cells).toHaveLength(4)
    expect(split?.cells.find((c) => c.r === 0 && c.c === 0)?.text).toBe('header')
  })

  it('keeps adjustable ruling positions when resizing', () => {
    const grid = emptyGrid(2, 2)
    grid.vlines![1] = 0.42
    const resized = resizeGrid(grid, 3, 2)
    expect(resized.vlines?.[1]).toBe(0.42)
    expect(resized.hlines).toHaveLength(4)
  })
})
