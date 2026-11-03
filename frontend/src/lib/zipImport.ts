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

/**
 * Upload the archive in pieces: the live instance sits behind a proxy that refuses
 * bodies over 100 MB. `onProgress` gets the bytes sent so far.
 */
export async function uploadZip(
  projectId: number,
  file: File,
  onProgress: (sent: number, size: number) => void,
  signal?: AbortSignal,
): Promise<string> {
  const base = `/projects/${projectId}/zip-uploads`
  const { upload_id, chunk_size } = await apiPost<{ upload_id: string; chunk_size: number }>(base, {
    filename: file.name,
    size: file.size,
  })
  onProgress(0, file.size)
  for (let offset = 0; offset < file.size; offset += chunk_size) {
    const path = `${base}/${upload_id}?offset=${offset}`
    let res: Response
    try {
      res = await fetch(apiUrl(path), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/octet-stream', 'Accept-Language': getLocale() },
        body: file.slice(offset, offset + chunk_size),
        signal,
      })
    } catch (e) {
      throw new ApiError('PUT', path, null, e instanceof Error ? e.message : String(e))
    }
    if (!res.ok) throw new ApiError('PUT', path, res.status, await res.text())
    onProgress(Math.min(file.size, offset + chunk_size), file.size)
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
