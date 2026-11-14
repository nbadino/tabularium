import { describe, expect, it } from 'vitest'
import { batchFiles, mergeReports } from './uploadBatches'

describe('batchFiles', () => {
  it('keeps order and splits at the byte limit', () => {
    const f = (size: number) => ({ size })
    const files = [f(4), f(4), f(3), f(10), f(1)]
    expect(batchFiles(files, 8).map((b) => b.map((x) => x.size))).toEqual([[4, 4], [3], [10], [1]])
  })
  it('returns no batches for no files', () => {
    expect(batchFiles([], 8)).toEqual([])
  })
})

describe('mergeReports', () => {
  it('sums counts and concatenates errors', () => {
    const r = mergeReports(
      { found_files: 2, registered: 1, duplicates: 1, unsupported: 0, errors: ['a'] },
      { found_files: 3, registered: 3, duplicates: 0, unsupported: 1, missing: 0, errors: ['b'] },
    )
    expect(r).toEqual({ found_files: 5, registered: 4, duplicates: 1, unsupported: 1, missing: 0, errors: ['a', 'b'] })
  })
})
