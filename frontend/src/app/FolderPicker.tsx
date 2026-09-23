/**
 * Scelta della cartella archivio navigando il computer del backend.
 *
 * Il percorso resta scrivibile a mano: questa finestra evita solo di doverlo
 * ricordare e digitare, che era il primo gesto di un utente nuovo e il più
 * facile da sbagliare. La rotta è riservata agli amministratori (mostra il
 * filesystem del server): chi non lo è vede solo il campo di testo.
 */
import { useEffect, useState } from 'react'
import { apiGet } from '../lib/api'
import { useI18n } from '../i18n'
import { ErrorNotice, Modal } from './ui'

interface BrowseOut {
  path: string
  parent: string | null
  dirs: Array<{ name: string; path: string }>
  files: number
  truncated: boolean
}

export function FolderPicker({
  start,
  onPick,
  onClose,
}: {
  start: string
  onPick: (path: string) => void
  onClose: () => void
}) {
  const { t } = useI18n()
  const [at, setAt] = useState<BrowseOut | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)

  const open = async (path?: string) => {
    setLoading(true)
    setError(null)
    try {
      setAt(await apiGet<BrowseOut>(`/system/fs/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`))
    } catch (e) {
      // Un percorso scritto a mano che non esiste non deve bloccare: si
      // riparte dalla cartella home.
      if (path) {
        try {
          setAt(await apiGet<BrowseOut>('/system/fs/browse'))
        } catch (inner) {
          setError(inner)
        }
      } else setError(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void open(start.trim() || undefined)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <Modal
      title={t('projects.picker.title')}
      onClose={onClose}
      wide
      footer={
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[12px] text-[color:var(--color-ink-2)]">
            {at ? t('projects.picker.files', { n: at.files }) : ''}
          </span>
          <button type="button" className="btn ml-auto" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="button" className="btn btn-primary" disabled={!at} onClick={() => at && onPick(at.path)}>
            {t('projects.picker.use')}
          </button>
        </div>
      }
    >
      <div className="space-y-2 p-3">
        {error != null && <ErrorNotice error={error} />}
        {at && (
          <>
            <div className="flex items-center gap-2">
              <button type="button" className="btn btn-sm" disabled={!at.parent || loading} onClick={() => at.parent && void open(at.parent)}>
                ↑ {t('projects.picker.up')}
              </button>
              <span className="mono min-w-0 flex-1 truncate text-[12px]" title={at.path}>{at.path}</span>
            </div>
            <p className="text-[11px] text-[color:var(--color-ink-3)]">{t('projects.picker.recursive')}</p>
            <ul className="max-h-[50vh] divide-y divide-[color:var(--color-rule)] overflow-auto border border-[color:var(--color-rule)]">
              {at.dirs.length === 0 && (
                <li className="p-2 text-[12px] text-[color:var(--color-ink-3)]">{t('projects.picker.noSubfolders')}</li>
              )}
              {at.dirs.map((dir) => (
                <li key={dir.path}>
                  <button
                    type="button"
                    disabled={loading}
                    onClick={() => void open(dir.path)}
                    className="block w-full px-2 py-1.5 text-left text-[13px] hover:bg-[color:var(--color-fill)]"
                  >
                    ▸ {dir.name}
                  </button>
                </li>
              ))}
            </ul>
            {at.truncated && <p className="text-[11px] text-[color:var(--color-warn)]">{t('projects.picker.truncated')}</p>}
          </>
        )}
      </div>
    </Modal>
  )
}
