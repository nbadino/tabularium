import { useEffect, useState } from 'react'
import { apiGet, apiPut } from '../../lib/api'
import type { TableHintOut } from '../../lib/types'
import { ErrorNotice, SkeletonLines } from '../../app/ui'
import { useI18n } from '../../i18n'

/**
 * Hint di schema tabella del progetto (v. backend `services/table_hint.py`).
 *
 * Testo libero accodato al prompt di riconoscimento tabella dei modelli che lo
 * accettano. Il riquadro in basso dice se il modello attivo lo usa: i motori a
 * prompt di protocollo (Paddle, MinerU, Qwen `qwenvl html`) lo ignorano, e
 * dirlo evita di attribuire al hint un miglioramento che non c'è stato.
 */
export default function TableHintEditor({ projectId }: { projectId: number }) {
  const { t } = useI18n()
  const [state, setState] = useState<TableHintOut | null>(null)
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    setState(null)
    setSaved(false)
    apiGet<TableHintOut>(`/projects/${projectId}/table-hint`)
      .then((r) => {
        setState(r)
        setDraft(r.hint)
      })
      .catch(setError)
  }, [projectId])

  const save = async () => {
    setError(null)
    try {
      const r = await apiPut<TableHintOut>(`/projects/${projectId}/table-hint`, { hint: draft })
      setState(r)
      setDraft(r.hint)
      setSaved(true)
    } catch (e) {
      setError(e)
    }
  }

  return (
    <div className="p-3">
      {error != null && (
        <div className="mb-2">
          <ErrorNotice error={error} onDismiss={() => setError(null)} />
        </div>
      )}
      {!state ? (
        <SkeletonLines lines={4} label={t('common.loading')} />
      ) : (
        <>
          <p className="mb-2 text-[11px] text-[color:var(--color-ink-3)]">{t('tableHint.intro')}</p>
          <textarea
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value)
              setSaved(false)
            }}
            rows={6}
            placeholder={t('tableHint.placeholder')}
            className="fld w-full resize-y font-mono text-[12px]"
          />
          <div className="mt-2 flex items-center gap-2">
            <button type="button" onClick={() => void save()} className="btn btn-sm btn-primary">
              {t('tableHint.save')}
            </button>
            {saved && (
              <span className="text-[11px] text-[color:var(--color-ink-3)]">
                {t('tableHint.saved')}
              </span>
            )}
          </div>
          <p className="mt-3 text-[11px] text-[color:var(--color-ink-3)]">
            {state.active_model == null
              ? t('tableHint.noModel')
              : state.accepts_hint
                ? t('tableHint.accepts', { model: state.active_model })
                : t('tableHint.ignores', { model: state.active_model })}
          </p>
        </>
      )}
    </div>
  )
}
