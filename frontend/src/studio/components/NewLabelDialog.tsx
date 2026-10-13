/**
 * Creazione rapida di una classe dallo Studio.
 *
 * Il gesto è deliberato e ha un effetto **di progetto** — la classe vale su
 * tutte le pagine — quindi lo dice prima di crearla e lo conferma dopo. La
 * scrittura passa da `POST /labels` (una sola classe): non riscrive l'intera
 * lista, così due schede aperte non si sovrascrivono.
 */
import { useState } from 'react'

import { apiPost } from '../../lib/api'
import { useI18n } from '../../i18n'
import { ErrorNotice, Modal } from '../../app/ui'
import type { LabelDef, LabelInput, LabelSchema } from '../../lib/types'
import { draftError, emptyDraft, parseAliases } from '../../lib/labels'
import LabelFields from './LabelFields'

export default function NewLabelDialog({
  projectId,
  existingNames,
  onClose,
  onCreated,
}: {
  projectId: number
  existingNames: string[]
  onClose: () => void
  onCreated: (labels: LabelDef[], name: string) => void
}) {
  const { t } = useI18n()
  const [draft, setDraft] = useState<LabelInput>(emptyDraft)
  const [aliasesText, setAliasesText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const draftErr = draftError(draft, existingNames)
  const name = draft.name.trim()

  const create = async () => {
    if (draftErr) return
    setBusy(true)
    setError(null)
    try {
      const payload: LabelInput = {
        ...draft,
        name,
        color: draft.color.trim(),
        short: draft.short.trim(),
        prompt: draft.prompt.trim(),
        aliases: parseAliases(aliasesText),
      }
      const res = await apiPost<LabelSchema>(`/projects/${projectId}/labels`, payload)
      onCreated(res.labels, name)
    } catch (exc) {
      setError(exc)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={t('labels.newTitle')}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-sm" onClick={onClose} disabled={busy}>
            {t('labels.cancel')}
          </button>
          <button
            type="button"
            className="btn btn-sm btn-primary"
            onClick={create}
            disabled={busy || !!draftErr}
          >
            {busy ? t('labels.creating') : t('labels.create')}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-3 p-3">
        <p className="m-0 text-[12px] text-[color:var(--color-ink-2)]">{t('labels.newScope')}</p>
        <LabelFields
          draft={draft}
          onDraft={(patch) => setDraft((d) => ({ ...d, ...patch }))}
          aliasesText={aliasesText}
          onAliasesText={setAliasesText}
          draftErr={draftErr}
        />
        {error ? <ErrorNotice error={error} onDismiss={() => setError(null)} /> : null}
      </div>
    </Modal>
  )
}
