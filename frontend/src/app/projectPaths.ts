/**
 * The project is the container of the work: every section of it lives under
 * `/progetti/:id`, so the URL says which project a screen is about and no page
 * needs a project selector of its own.
 */

export type ProjectSection = 'riconoscimenti' | 'studio' | 'fine-tuning' | 'avanzate' | `riconoscimenti/${number}` | `fine-tuning/${string}`

export function projectPath(
  id: number,
  section?: ProjectSection,
  search?: Record<string, string | number | null | undefined>,
): string {
  const base = section ? `/progetti/${id}/${section}` : `/progetti/${id}`
  const query = new URLSearchParams()
  for (const [k, v] of Object.entries(search ?? {})) if (v != null && v !== '') query.set(k, String(v))
  const qs = query.toString()
  return qs ? `${base}?${qs}` : base
}

/** The same section of another project; an item id (a run) belongs to the old one and is dropped. */
export function switchProjectPath(pathname: string, id: number): string {
  const rest = pathname.replace(/^\/progetti\/\d+\/?/, '').split('/').filter(Boolean)
  if (rest[0] === 'riconoscimenti') return projectPath(id, 'riconoscimenti')
  return rest.length ? `/progetti/${id}/${rest.join('/')}` : projectPath(id)
}

/** The project id in a `/progetti/:id/...` path, or null outside a project. */
export function projectIdOf(pathname: string): number | null {
  const m = /^\/progetti\/(\d+)(?:\/|$)/.exec(pathname)
  return m ? Number(m[1]) : null
}
