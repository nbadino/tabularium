/**
 * Where the routes of the old section-based navigation now live. Links,
 * bookmarks and e-mails pointing at `/annotazione?project=2&page=9` keep
 * working: they land in the same place inside the project.
 */
import { projectPath } from './projectPaths'

const FINE_TUNING: Record<string, string> = {
  '/fine-tuning': 'fine-tuning',
  '/dataset': 'fine-tuning/dati',
  '/training': 'fine-tuning/addestra',
  '/valutazione': 'fine-tuning/valuta',
  '/benchmark': 'fine-tuning/confronta',
}

export const LEGACY_PATHS = ['/archivio', '/riconosci', '/annotazione', '/risultati', ...Object.keys(FINE_TUNING)]

export function legacyTarget(pathname: string, search: string, activeId: number | null): string {
  const params = new URLSearchParams(search)
  const id = Number(params.get('project')) || activeId
  params.delete('project')
  if (!id) return '/progetti'
  const rest = () => {
    const qs = params.toString()
    return qs ? `?${qs}` : ''
  }
  if (pathname === '/archivio') return projectPath(id)
  if (pathname === '/riconosci') return `${projectPath(id, 'riconoscimenti')}${rest()}`
  if (pathname === '/annotazione') return `${projectPath(id, 'studio')}${rest()}`
  if (pathname === '/risultati') {
    const run = Number(params.get('run'))
    params.delete('run')
    return `${run ? projectPath(id, `riconoscimenti/${run}`) : projectPath(id, 'riconoscimenti')}${rest()}`
  }
  const section = FINE_TUNING[pathname]
  if (section) return `/progetti/${id}/${section}${rest()}`
  return '/progetti'
}
