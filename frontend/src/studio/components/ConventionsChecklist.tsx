import { useEffect, useState } from 'react'
import { apiGet, apiPut } from '../../lib/api'
import type { ConventionItem, ConventionsOut } from '../../lib/types'
import { ErrorNotice, Module } from '../../app/ui'
import { useI18n } from '../../i18n'

/** Le convenzioni predefinite hanno id noti: li tradiamo via i18n, con
 *  fallback sull'etichetta salvata nel progetto (per le convenzioni custom). */
const KNOWN_IDS: Record<string, string> = {
  soft_hyphen: 'conventions.c_soft_hyphen',
  sigle: 'conventions.c_sigle',
  corsivi: 'conventions.c_corsivi',
  colonne_fantasma: 'conventions.c_colonne_fantasma',
  filetti: 'conventions.c_filetti',
}

export default function ConventionsChecklist({ projectId }: { projectId: number }) {
  const { t } = useI18n()
  const [items, setItems] = useState<ConventionItem[] | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    setItems(null)
    apiGet<ConventionsOut>(`/projects/${projectId}/conventions`)
      .then((r) => setItems(r.conventions))
      .catch(setError)
  }, [projectId])

  const toggle = async (id: string) => {
    if (!items) return
    const next = items.map((i) => (i.id === id ? { ...i, checked: !i.checked } : i))
    setItems(next)
    try {
      await apiPut<ConventionsOut>(`/projects/${projectId}/conventions`, { conventions: next })
    } catch (e) {
      setError(e)
    }
  }

  const done = items?.filter((i) => i.checked).length ?? 0

  const labelOf = (item: ConventionItem): string => {
    const key = KNOWN_IDS[item.id]
    return key ? t(key) : item.label
  }

  return (
    <Module
      tab={t('conventions.tab')}
      quiet
      aux={items ? <span>{`${done}/${items.length}`}</span> : undefined}
    >
      {error != null && (
        <div className="mb-2">
          <ErrorNotice error={error} onDismiss={() => setError(null)} />
        </div>
      )}
      {!items ? (
        <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('common.loading')}</p>
      ) : (
        <>
          <p className="mb-2 text-[11px] text-[color:var(--color-ink-3)]">
            {t('conventions.intro')}
          </p>
          <ul className="space-y-1">
            {items.map((item) => (
              <li key={item.id}>
                <label className="flex cursor-pointer items-start gap-2 text-[12px]">
                  <input
                    type="checkbox"
                    checked={item.checked}
                    onChange={() => void toggle(item.id)}
                    className="mt-0.5 shrink-0"
                  />
                  <span
                    className={
                      item.checked
                        ? 'text-[color:var(--color-ink)]'
                        : 'text-[color:var(--color-ink-2)]'
                    }
                  >
                    {labelOf(item)}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </>
      )}
    </Module>
  )
}