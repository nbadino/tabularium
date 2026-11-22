import type { ScanReport } from './types'

/**
 * Cloudflare rejects request bodies over 100 MB with 413 before they reach
 * the server, so a folder import is sent in batches well below that.
 */
export const UPLOAD_BATCH_BYTES = 64 * 1024 * 1024

/** Groups files in order into batches of at most `limit` bytes (an oversized file gets its own batch). */
export function batchFiles<T extends { size: number }>(files: T[], limit = UPLOAD_BATCH_BYTES): T[][] {
  const batches: T[][] = []
  let current: T[] = []
  let bytes = 0
  for (const file of files) {
    if (current.length && bytes + file.size > limit) {
      batches.push(current)
      current = []
      bytes = 0
    }
    current.push(file)
    bytes += file.size
  }
  if (current.length) batches.push(current)
  return batches
}

export function mergeReports(a: ScanReport, b: ScanReport): ScanReport {
  return {
    found_files: a.found_files + b.found_files,
    registered: a.registered + b.registered,
    duplicates: a.duplicates + b.duplicates,
    unsupported: a.unsupported + b.unsupported,
    missing: (a.missing ?? 0) + (b.missing ?? 0),
    errors: [...a.errors, ...b.errors],
    already_present: [...(a.already_present ?? []), ...(b.already_present ?? [])],
    failed: [...(a.failed ?? []), ...(b.failed ?? [])],
  }
}

const collator = new Intl.Collator('en', { numeric: true, sensitivity: 'base' })

/** Natural order of file names: `LSI_5294_9` before `LSI_5294_10`. */
export function compareNames(a: string, b: string): number {
  return collator.compare(a, b) || (a < b ? -1 : a > b ? 1 : 0)
}

type Named = { name: string; webkitRelativePath?: string }

/** The files of a folder in name order (by their path inside the folder), so a gap is easy to see. */
export function sortFilesByName<T extends Named>(files: T[]): T[] {
  const key = (f: T) => f.webkitRelativePath || f.name
  return [...files].sort((a, b) => compareNames(key(a), key(b)))
}

/** Statuses worth sending a batch again for: timeouts, overload, Cloudflare 52x, a locked database. */
export const RETRYABLE_STATUS = new Set([408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524])
/** Waits between attempts of one batch: about a minute in all before giving it up. */
export const RETRY_DELAYS_MS = [2000, 5000, 15000, 30000]

/**
 * Sends one batch, again after a network error or a retryable status. Retrying is safe:
 * the server skips files whose content it already has. Returns the last response, or
 * throws the last network error.
 */
export async function sendWithRetry(
  send: () => Promise<Response>,
  { delays = RETRY_DELAYS_MS, sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms)) }: { delays?: number[]; sleep?: (ms: number) => Promise<void> } = {},
): Promise<Response> {
  for (let attempt = 0; ; attempt++) {
    try {
      const response = await send()
      if (response.ok || !RETRYABLE_STATUS.has(response.status) || attempt >= delays.length) return response
    } catch (error) {
      if (attempt >= delays.length) throw error
    }
    await sleep(delays[attempt])
  }
}

/** Files the project already has (by the check's positions) and the ones still to send. */
export function splitByCheck<T>(files: T[], presentIndexes: number[]): { present: T[]; missing: T[] } {
  const present = new Set(presentIndexes)
  return {
    present: files.filter((_f, i) => present.has(i)),
    missing: files.filter((_f, i) => !present.has(i)),
  }
}

/** How many files one check request may name (the server accepts up to 50,000). */
export const CHECK_CHUNK = 5000
