import type { LabelDef } from '../../lib/types'
import type { DisplayBlock } from '../types'
import { Field, Module } from '../../app/ui'
import { IconTable, IconTrash } from '../../app/icons'
import { useI18n } from '../../i18n'

interface InspectorProps {
  block: DisplayBlock | null
  labels: LabelDef[]
  bboxPage: { x: number; y: number; w: number; h: number } | null
  onLabel: (id: string, label: string) => void
  onContent: (id: string, content: string) => void
  onConfirmed: (id: string, confirmed: boolean) => void
  onDelete: (id: string) => void
  onOpenTable?: () => void
}

export default function Inspector({
  block,
  labels,
  bboxPage,
  onLabel,
  onContent,
  onConfirmed,
  onDelete,
  onOpenTable,
}: InspectorProps) {
  const { t } = useI18n()
  if (!block) {
    return (
      <Module tab={t('inspector.tab')} quiet>
        <p className="text-[12px] text-[color:var(--color-ink-2)]">
          {t('inspector.noSelection')}
        </p>
      </Module>
    )
  }

  return (
    <Module
      tab={t('inspector.tab')}
      aux={
        <span className="mono">
          {block.kind === 'rect' ? t('inspector.rect') : t('inspector.polygon')}
        </span>
      }
    >
      <Field label={t('inspector.class')}>
        <select
          value={block.label}
          onChange={(e) => onLabel(block.id, e.target.value)}
          className="fld"
        >
          {labels.map((l) => (
            <option key={l.name} value={l.name}>
              {l.name}
            </option>
          ))}
        </select>
      </Field>

      {bboxPage && (
        <p className="mono mt-2 text-[11px] text-[color:var(--color-ink-2)]">
          {Math.round(bboxPage.w)} × {Math.round(bboxPage.h)} px
          <span className="text-[color:var(--color-ink-3)]">
            {' '}
            a ({Math.round(bboxPage.x)}, {Math.round(bboxPage.y)})
          </span>
        </p>
      )}

      <div className="mt-2">
        <Field
          label={t('inspector.transcription')}
          hint={t('inspector.transcriptionHint')}
        >
          <textarea
            value={block.content}
            onChange={(e) => onContent(block.id, e.target.value)}
            rows={5}
            placeholder={t('inspector.transcriptionPlaceholder')}
            className="fld resize-y"
          />
        </Field>
      </div>

      <div className="mt-2 flex flex-wrap gap-2">
        {block.label === 'Table' && onOpenTable && (
          <button type="button" onClick={onOpenTable} className="btn">
            <IconTable size={13} />
            {t('inspector.tableEditor')}
          </button>
        )}
        <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-300">
          <input type="checkbox" checked={block.confirmed} onChange={(e) => onConfirmed(block.id, e.target.checked)} className="accent-emerald-600" />
          {t('inspector.confirmed')}
        </label>

        <button
          type="button"
          onClick={() => onDelete(block.id)}
          className="btn btn-danger ml-auto"
        >
          <IconTrash size={13} />
          {t('inspector.deleteBlock')}
        </button>
      </div>
    </Module>
  )
}