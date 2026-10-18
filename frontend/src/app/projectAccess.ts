/** I permessi effettivi arrivano dal server, il ruolo globale non sostituisce quello di progetto. */
import type { Project, ProjectAccess, User } from '../lib/types'
import { t } from '../i18n'

export function projectAccess(project: Project, auth: { enabled: boolean; user: User | null }): ProjectAccess | null {
  if (project.access) return project.access
  // Compatibilità col backend precedente solo per i casi già certi.
  if (!auth.enabled) return { role: 'owner', source: 'local', can_edit: true, can_manage: true }
  if (auth.user?.role === 'admin') return { role: 'owner', source: 'admin', can_edit: true, can_manage: true }
  if (project.owner_id === auth.user?.id) return { role: 'owner', source: 'owner', can_edit: true, can_manage: true }
  return null
}

export function accessLabel(access: ProjectAccess | null): string {
  if (!access) return t('team.accessUnknown')
  if (access.source === 'admin') return t('team.adminAccess')
  return t(access.role === 'owner' ? 'share.roleOwner' : access.role === 'editor' ? 'share.roleEditor' : 'share.roleViewer')
}
