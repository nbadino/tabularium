/**
 * I campi di una classe, riusati da ogni punto in cui se ne crea o modifica una
 * (l'editor del progetto e il gesto rapido nello Studio). Tengono solo la
 * presentazione: lo stato vive in chi li usa, così le due superfici restano
 * allineate senza duplicare il markup.
 */
import { useEffect, useState } from 'react'
import { Field } from '../../app/ui'
import { useI18n } from '../../i18n'
import type { LabelInput } from '../../lib/types'
import { LABEL_KINDS, requiresPrompt } from '../../lib/labels'

export const LABEL_ERR_KEY: Record<string, string> = {
  name: 'labels.errName',
  duplicate: 'labels.errDuplicate',
  color: 'labels.errColor',
  short: 'labels.errShort',
  prompt: 'labels.errPrompt',
}

export const LABEL_KIND_KEY: Record<string, string> = {
  text: 'labels.kindText',
  table: 'labels.kindTable',
  formula: 'labels.kindFormula',
  picture: 'labels.kindPicture',
  structure: 'labels.kindStructure',
}

export default function LabelFields({
  draft,
  onDraft,
  aliasesText,
  onAliasesText,
  draftErr,
}: {
  draft: LabelInput
  onDraft: (patch: Partial<LabelInput>) => void
  aliasesText: string
  onAliasesText: (text: string) => void
  draftErr: string | null
}) {
  const { t } = useI18n()
  const [touched, setTouched] = useState<Set<string>>(() => new Set())
  useEffect(() => {
    // Reset touched flags whenever the parent clears the draft back to empty.
    if (!draft.name && !draft.short && !draft.prompt) setTouched(new Set())
  }, [draft.name, draft.short, draft.prompt])
  const mark = (field: string) => setTouched((cur) => (cur.has(field) ? cur : new Set(cur).add(field)))
  const showErr = (field: string, value: string) =>
    draftErr === field && (touched.has(field) || value.length > 0)
  const pickerColor = /^#[0-9a-f]{6}$/i.test(draft.color) ? draft.color : '#4a5560'

  return (
    <>
      <div className="grid grid-cols-2 gap-3">
        <Field label={t('labels.name')} error={showErr('name', draft.name) ? t(LABEL_ERR_KEY.name) : null}>
          <input
            className="fld"
            value={draft.name}
            placeholder={t('labels.namePlaceholder')}
            onBlur={() => mark('name')}
            onChange={(e) => {
              mark('name')
              onDraft({ name: e.target.value })
            }}
          />
        </Field>
        <Field label={t('labels.short')} error={showErr('short', draft.short) ? t(LABEL_ERR_KEY.short) : null}>
          <input
            className="fld"
            value={draft.short}
            placeholder={t('labels.shortPlaceholder')}
            onBlur={() => mark('short')}
            onChange={(e) => {
              mark('short')
              onDraft({ short: e.target.value })
            }}
          />
        </Field>
        <Field label={t('labels.color')} error={showErr('color', draft.color) ? t(LABEL_ERR_KEY.color) : null}>
          <div className="flex items-center gap-2">
            <input
              type="color"
              aria-label={t('labels.color')}
              value={pickerColor}
              onChange={(e) => {
                mark('color')
                onDraft({ color: e.target.value })
              }}
              className="h-8 w-8 shrink-0 cursor-pointer rounded border border-[color:var(--color-rule-field)] bg-white p-0.5"
            />
            <input
              className="fld font-mono"
              value={draft.color}
              onBlur={() => mark('color')}
              onChange={(e) => {
                mark('color')
                onDraft({ color: e.target.value })
              }}
            />
          </div>
        </Field>
        <Field label={t('labels.kind')}>
          <select
            className="fld"
            value={draft.prompt_kind}
            onChange={(e) => onDraft({ prompt_kind: e.target.value as LabelInput['prompt_kind'] })}
          >
            {LABEL_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {t(LABEL_KIND_KEY[kind])}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Field
        label={t('labels.prompt')}
        hint={t('labels.promptHint')}
        error={showErr('prompt', draft.prompt) ? t(LABEL_ERR_KEY.prompt) : null}
      >
        <input
          className="fld"
          value={draft.prompt}
          disabled={!requiresPrompt(draft.prompt_kind)}
          placeholder={t('labels.promptPlaceholder')}
          onBlur={() => mark('prompt')}
          onChange={(e) => {
            mark('prompt')
            onDraft({ prompt: e.target.value })
          }}
        />
      </Field>
      <Field label={t('labels.aliases')} hint={t('labels.aliasesHint')}>
        <input
          className="fld"
          value={aliasesText}
          placeholder={t('labels.aliasesPlaceholder')}
          onChange={(e) => onAliasesText(e.target.value)}
        />
      </Field>
      {draftErr === 'duplicate' && (
        <p className="m-0 text-[11px] text-[color:var(--color-sig-text)]">{t(LABEL_ERR_KEY.duplicate)}</p>
      )}
    </>
  )
}
