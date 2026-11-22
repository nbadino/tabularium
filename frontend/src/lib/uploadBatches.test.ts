import { describe, expect, it, vi } from 'vitest'
import { batchFiles, compareNames, mergeReports, sendWithRetry, sortFilesByName, splitByCheck } from './uploadBatches'

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
  it('sums counts and concatenates errors, skipped and failed names', () => {
    const r = mergeReports(
      { found_files: 2, registered: 1, duplicates: 1, unsupported: 0, errors: ['a'], already_present: ['x.tif'] },
      { found_files: 3, registered: 3, duplicates: 0, unsupported: 1, missing: 0, errors: ['b'], failed: ['y.tif'] },
    )
    expect(r).toEqual({
      found_files: 5, registered: 4, duplicates: 1, unsupported: 1, missing: 0, errors: ['a', 'b'],
      already_present: ['x.tif'], failed: ['y.tif'],
    })
  })
})

describe('name order', () => {
  it('sorts numbers inside names naturally', () => {
    const names = ['LSI_5294_10.tif', 'LSI_5294_9.tif', 'LSI_5290_220.tif', 'lsi_5294_001.tif']
    expect([...names].sort(compareNames)).toEqual(['LSI_5290_220.tif', 'lsi_5294_001.tif', 'LSI_5294_9.tif', 'LSI_5294_10.tif'])
  })
  it('sorts files by their path inside the chosen folder', () => {
    const files = [
      { name: 'b.tif', webkitRelativePath: '1930/LSI_5433/b.tif' },
      { name: 'a.tif', webkitRelativePath: '1930/LSI_5439/a.tif' },
      { name: 'c.tif', webkitRelativePath: '1930/LSI_5428/c.tif' },
    ]
    expect(sortFilesByName(files).map((f) => f.name)).toEqual(['c.tif', 'b.tif', 'a.tif'])
  })
})

describe('splitByCheck', () => {
  it('uses positions, so two files with one name stay apart', () => {
    const files = ['a', 'b', 'a2', 'c']
    expect(splitByCheck(files, [0, 3])).toEqual({ present: ['a', 'c'], missing: ['b', 'a2'] })
  })
})

describe('sendWithRetry', () => {
  const res = (status: number) => new Response('x', { status })
  const sleep = vi.fn(async () => {})

  it('sends again after a Cloudflare timeout and a network error', async () => {
    const send = vi.fn()
      .mockResolvedValueOnce(res(524))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(res(200))
    const r = await sendWithRetry(send, { delays: [1, 1, 1], sleep })
    expect(r.status).toBe(200)
    expect(send).toHaveBeenCalledTimes(3)
  })
  it('does not retry a client error', async () => {
    const send = vi.fn().mockResolvedValue(res(404))
    expect((await sendWithRetry(send, { delays: [1, 1], sleep })).status).toBe(404)
    expect(send).toHaveBeenCalledTimes(1)
  })
  it('gives up after the last delay with the last answer', async () => {
    const send = vi.fn().mockResolvedValue(res(503))
    expect((await sendWithRetry(send, { delays: [1, 1], sleep })).status).toBe(503)
    expect(send).toHaveBeenCalledTimes(3)
  })
  it('throws the last network error when every attempt fails', async () => {
    const send = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(sendWithRetry(send, { delays: [1], sleep })).rejects.toThrow('Failed to fetch')
  })
})
