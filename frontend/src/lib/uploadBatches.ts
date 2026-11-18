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
  }
}
