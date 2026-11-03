import { getLocale } from '../i18n'
import { ApiError, apiGet, apiPost } from './api'
import { apiUrl } from './endpoints'

/** Why an entry of the archive was not imported (codes from `services/zip_import.py`). */
export type ZipSkipReason =
  | 'unsafe_path' | 'symlink' | 'not_regular' | 'encrypted' | 'nested_archive'
  | 'not_image' | 'too_large' | 'suspicious_ratio' | 'duplicate_entry' | 'corrupt'

export const ZIP_SKIP_REASONS: ZipSkipReason[] = [
  'not_image', 'nested_archive', 'too_large', 'suspicious_ratio', 'encrypted',
  'symlink', 'not_regular', 'unsafe_path', 'duplicate_entry', 'corrupt',
]

/** What the archive holds, read before anything is extracted. */
export interface ZipPlan {
  /** Wrapping folders that were opened (`Lloyds` for `Lloyds/…`), empty if none. */
  root: string
  files: number
  folders: { name: string; files: number }[]
  loose_files: number
  /** Two or more folders: the user chooses collections per folder or all together. */
  needs_choice: boolean
  skipped: { name: string; reason: ZipSkipReason }[]
  skipped_counts: Partial<Record<ZipSkipReason, number>>
  skipped_total: number
  ignored: number
}

export interface ZipReport extends ZipPlan {
  registered: number
  duplicates: number
  unsupported: number
  errors: string[]
  collections: { id: number; name: string; created: boolean; pages: number }[]
  unassigned_pages: number
}

export interface ZipJob {
  status: 'running' | 'done' | 'failed'
  phase: 'extracting' | 'registering'
  current: number
  total: number
  report: ZipReport | null
  error: string | null
}

export type ZipLayout = 'collections' | 'flat'

/** Where a flat import goes: a new collection, an existing one, or none. */
export type ZipDestination =
  | { kind: 'new'; name: string }
  | { kind: 'existing'; id: number }
  | { kind: 'none' }

/** A collection name from the archive: its wrapping folder, or the file name. */
export function defaultCollectionName(plan: ZipPlan, fileName: string): string {
  const folder = plan.root.split('/').filter(Boolean).pop()
  return (folder || fileName.replace(/\.zip$/i, '')).slice(0, 120)
}

export function isZip(file: File): boolean {
  return file.name.toLowerCase().endsWith('.zip')
}

/** Waits before each new attempt at a piece that failed: about two minutes in all. */
export const RETRY_DELAYS_MS = [1000, 2000, 5000, 10000, 20000, 30000, 60000]

/** Statuses another attempt can fix: an interrupted piece, a piece out of order
 *  (its answer was lost), a timeout, a rate limit, or the server or proxy failing. */
const RETRYABLE = (status: number) => status === 400 || status === 408 || status === 409 || status === 429 || status >= 500

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms)
    signal?.addEventListener('abort', () => { window.clearTimeout(timer); reject(new DOMException('aborted', 'AbortError')) }, { once: true })
  })

/**
 * Upload the archive in pieces: the live instance sits behind a proxy that refuses
 * bodies over 100 MB. A piece that fails (the connection drops, the proxy times out)
 * is sent again after a pause, from the byte the server says it has, so a network
 * hiccup near the end of a large archive does not throw the upload away.
 * `onProgress` gets the bytes the server holds and whether it is retrying.
 */
export async function uploadZip(
  projectId: number,
  file: File,
  onProgress: (sent: number, size: number, retrying?: boolean) => void,
  signal?: AbortSignal,
  delays: number[] = RETRY_DELAYS_MS,
): Promise<string> {
  const base = `/projects/${projectId}/zip-uploads`
  const { upload_id, chunk_size } = await apiPost<{ upload_id: string; chunk_size: number }>(base, {
    filename: file.name,
    size: file.size,
  })
  onProgress(0, file.size)
  let offset = 0
  let failures = 0
  while (offset < file.size) {
    const path = `${base}/${upload_id}?offset=${offset}`
    let error: ApiError
    let res: Response | null = null
    try {
      res = await fetch(apiUrl(path), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/octet-stream', 'Accept-Language': getLocale() },
        body: file.slice(offset, offset + chunk_size),
        signal,
      })
    } catch (e) {
      if (signal?.aborted) throw e
      error = new ApiError('PUT', path, null, e instanceof Error ? e.message : String(e))
    }
    if (res?.ok) {
      offset = Math.min(file.size, offset + chunk_size)
      failures = 0
      onProgress(offset, file.size)
      continue
    }
    if (res) {
      error = new ApiError('PUT', path, res.status, await res.text())
      if (!RETRYABLE(res.status)) throw error
    }
    if (failures >= delays.length) throw error!
    onProgress(offset, file.size, true)
    await sleep(delays[failures], signal)
    failures += 1
    // Ask where the upload stands: the piece may have arrived with its answer lost.
    try {
      const status = await apiGet<{ received: number }>(`${base}/${upload_id}`)
      offset = status.received
    } catch {
      /* the server is still unreachable: the next attempt tries the same piece */
    }
  }
  return upload_id
}

export function inspectZip(projectId: number, uploadId: string): Promise<ZipPlan> {
  return apiPost<ZipPlan>(`/projects/${projectId}/zip-uploads/${uploadId}/inspect`)
}

export async function importZip(
  projectId: number,
  uploadId: string,
  layout: ZipLayout,
  destination: ZipDestination,
  onProgress: (job: ZipJob) => void,
  pollMs = 500,
): Promise<ZipReport> {
  const target = layout === 'collections'
    ? {}
    : destination.kind === 'new'
      ? { collection_name: destination.name }
      : { collection_id: destination.kind === 'existing' ? destination.id : null }
  const { job_id } = await apiPost<{ job_id: string }>(
    `/projects/${projectId}/zip-uploads/${uploadId}/import`,
    { layout, ...target },
  )
  for (;;) {
    const job = await apiGet<ZipJob>(`/projects/${projectId}/zip-jobs/${job_id}`)
    onProgress(job)
    if (job.status === 'done' && job.report) return job.report
    if (job.status === 'failed') throw new Error(job.error ?? 'import failed')
    await new Promise((resolve) => window.setTimeout(resolve, pollMs))
  }
}
