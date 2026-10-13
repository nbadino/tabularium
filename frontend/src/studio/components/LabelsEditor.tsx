/**
 * Editor delle classi del progetto.
 *
 * Serve a chi annota: vedere in un posto solo quali classi esistono, quali il
 * modello attivo **conosce già** (transfer gratuito) e quali sono token da
 * insegnare col fine-tuning, e aggiungere/rimuovere le classi custom del
 * progetto. I default non si toccano (modello additivo): qui si modificano solo
 * le custom, ed è questo che il PUT sostituisce.
 */
import { useEffect, useMemo, useState } from 'react'

import { apiPut } from '../../lib/api'
import { useI18n } from '../../i18n'
import { useModelNames } from '../../app/models/names'
import { Badge, ErrorNotice, Notice } from '../../app/ui'
import type { LabelDef, LabelInput, LabelSchema } from '../../lib/types'
import {
  aliasesToText,
  customsOf,
  draftError,
  emptyDraft,
  knownByModel,
  parseAliases,
  toInput,
} from '../../lib/labels'
import LabelFields, { LABEL_KIND_KEY } from './LabelFields'

const KIND_KEY = LABEL_KIND_KEY

export default function LabelsEditor({
  projectId,
  labels,
  activeAdapterId,
  onSaved,
}: {
  projectId: number
  labels: LabelDef[]
  activeAdapterId?: string | null
  onSaved: (labels: LabelDef[]) => void
}) {
  const { t } = useI18n()
  const modelName = useModelNames()

  const [customs, setCustoms] = useState<LabelInput[]>(() => customsOf(labels).map(toInput))
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState<LabelInput>(emptyDraft)
  const [aliasesText, setAliasesText] = useState('')
  const [formOpen, setFormOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)

  // Il pannello è la fonte di verità dopo un salvataggio: risincronizza le
  // custom quando arrivano nuove label dal server.
  useEffect(() => {
    setCustoms(customsOf(labels).map(toInput))
  }, [labels])

  const builtin = useMemo(() => labels.filter((l) => l.builtin), [labels])
  const takenNames = useMemo(
    () => [...labels.map((l) => l.name), ...customs.map((c) => c.name)].filter((n) => n !== editing),
    [labels, customs, editing],
  )

  const draftErr = draftError(draft, takenNames, editing)

  const resetForm = () => {
    setDraft(emptyDraft())
    setAliasesText('')
    setEditing(null)
    setFormOpen(false)
  }

  const commitDraft = () => {
    if (draftErr) return
    const clean = {
      ...draft,
      name: draft.name.trim(),
      color: draft.color.trim(),
      short: draft.short.trim(),
      prompt: draft.prompt.trim(),
      aliases: parseAliases(aliasesText),
    }
    setCustoms((current) =>
      editing ? current.map((c) => (c.name === editing ? clean : c)) : [...current, clean],
    )
    resetForm()
  }

  const removeCustom = (name: string) => {
    setCustoms((current) => current.filter((c) => c.name !== name))
    if (editing === name) resetForm()
  }

  const startEdit = (label: LabelInput) => {
    setDraft(label)
    setAliasesText(aliasesToText(label.aliases))
    setEditing(label.name)
    setFormOpen(true)
  }

  const save = async () => {
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      const res = await apiPut<LabelSchema>(`/projects/${projectId}/labels`, { labels: customs })
      onSaved(res.labels)
      setCustoms(customsOf(res.labels).map(toInput))
      setSaved(true)
    } catch (exc) {
      setError(exc)
    } finally {
      setBusy(false)
    }
  }

  const knownText = (label: LabelDef): string => {
    const names = label.known_models.map((id) => modelName(id) ?? id)
    return t('labels.knownBy', { models: names.join(', ') })
  }

  return (
    <div className="flex flex-col gap-4 p-3 text-[13px]">
      <p className="m-0 text-[12px] text-[color:var(--color-ink-2)]">{t('labels.intro')}</p>

      <section>
        <h3 className="lbl">{t('labels.customTitle')}</h3>
        {customs.length === 0 ? (
          <p className="m-0 text-[12px] text-[color:var(--color-ink-3)]">{t('labels.customEmpty')}</p>
        ) : (
          <ul className="ruled">
            {customs.map((c) => {
              const def = labels.find((l) => l.name === c.name)
              return (
                <li key={c.name} className="flex items-center gap-2 px-2 py-1.5">
                  <span
                    aria-hidden
                    className="h-3 w-3 shrink-0 rounded-sm border border-[color:var(--color-rule)]"
                    style={{ background: c.color }}
                  />
                  <span className="font-semibold">{c.name}</span>
                  <span className="text-[11px] text-[color:var(--color-ink-3)]">
                    {t(KIND_KEY[c.prompt_kind] ?? 'labels.kindText')}
                  </span>
                  {c.aliases.length > 0 && (
                    <span className="text-[11px] text-[color:var(--color-ink-3)]">
                      ↔ {c.aliases.join(', ')}
                    </span>
                  )}
                  <Badge tone="warn">{t('labels.originCustom')}</Badge>
                  {def && knownByModel(def, activeAdapterId) && (
                    <Badge tone="ok">{t('labels.knownHere')}</Badge>
                  )}
                  <span className="ml-auto flex gap-1">
                    <button type="button" className="btn btn-sm" onClick={() => startEdit(c)}>
                      {t('labels.edit')}
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => removeCustom(c.name)}
                    >
                      {t('labels.remove')}
                    </button>
                  </span>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      {formOpen ? (
        <section className="flex flex-col gap-3 border-t border-[color:var(--color-rule)] pt-3">
          <LabelFields
            draft={draft}
            onDraft={(patch) => setDraft((d) => ({ ...d, ...patch }))}
            aliasesText={aliasesText}
            onAliasesText={setAliasesText}
            draftErr={draftErr}
          />
          <div className="flex gap-2">
            <button type="button" className="btn btn-sm" onClick={commitDraft} disabled={!!draftErr}>
              {editing ? t('labels.edit') : t('labels.add')}
            </button>
            <button type="button" className="btn btn-sm" onClick={resetForm}>
              {t('labels.cancel')}
            </button>
          </div>
        </section>
      ) : (
        <button type="button" className="btn btn-sm self-start" onClick={() => setFormOpen(true)}>
          {t('labels.add')}
        </button>
      )}

      <section className="border-t border-[color:var(--color-rule)] pt-3">
        <h3 className="lbl">{t('palette.tab')}</h3>
        <ul className="ruled">
          {builtin.map((label) => (
            <li key={label.name} className="flex flex-wrap items-center gap-2 px-2 py-1.5">
              <span
                aria-hidden
                className="h-3 w-3 shrink-0 rounded-sm border border-[color:var(--color-rule)]"
                style={{ background: label.color }}
              />
              <span className="font-semibold">{label.name}</span>
              <Badge tone={label.origin === 'public' ? 'ok' : 'warn'}>
                {label.origin === 'public' ? t('labels.originPublic') : t('labels.originCustom')}
              </Badge>
              <Badge tone="neutral">{t('labels.builtin')}</Badge>
              {label.aliases.length > 0 && (
                <span className="text-[11px] text-[color:var(--color-ink-3)]">
                  ↔ {label.aliases.join(', ')}
                </span>
              )}
              <span className="text-[11px] text-[color:var(--color-ink-3)]">
                {label.known_models.length > 0 ? knownText(label) : t('labels.knownByNone')}
              </span>
            </li>
          ))}
        </ul>
      </section>

      {error ? <ErrorNotice error={error} onDismiss={() => setError(null)} /> : null}
      {saved ? <Notice tone="ok">{t('labels.saved')}</Notice> : null}

      <div className="flex gap-2">
        <button type="button" className="btn" onClick={save} disabled={busy}>
          {busy ? t('labels.saving') : t('labels.save')}
        </button>
      </div>
    </div>
  )
}
