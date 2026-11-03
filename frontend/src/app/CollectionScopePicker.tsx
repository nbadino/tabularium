import type { CollectionsOverview } from '../lib/types'
import { useI18n, tn } from '../i18n'
import { verifiedCount } from './collections'

/** What the dataset build sends as `scope`. */
export interface ScopeChoice {
  kind: 'project' | 'collections'
  collection_ids: number[]
  include_unassigned: boolean
}

export const WHOLE_PROJECT: ScopeChoice = { kind: 'project', collection_ids: [], include_unassigned: false }

export function scopeIsEmpty(choice: ScopeChoice): boolean {
  return choice.kind === 'collections' && choice.collection_ids.length === 0 && !choice.include_unassigned
}

/**
 * Train on the whole project or on chosen collections. Shown only when the project
 * has collections: a project without them trains on everything, as before.
 */
export default function CollectionScopePicker({
  overview,
  value,
  onChange,
}: {
  overview: CollectionsOverview
  value: ScopeChoice
  onChange: (next: ScopeChoice) => void
}) {
  const { t } = useI18n()
  const toggle = (id: number) => {
    const ids = value.collection_ids.includes(id)
      ? value.collection_ids.filter((x) => x !== id)
      : [...value.collection_ids, id]
    onChange({ ...value, collection_ids: ids })
  }
  const row = (key: string, label: string, verified: number, checked: boolean, onToggle: () => void) => (
    <label key={key} className="flex items-center gap-2 border-b border-[color:var(--color-rule)] px-2 py-1 text-[12px] last:border-b-0">
      <input type="checkbox" checked={checked} onChange={onToggle} />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <span className="mono text-[color:var(--color-ink-2)]">{tn('scope.verified', verified)}</span>
    </label>
  )
  return (
    <fieldset className="mb-3">
      <legend className="lbl">{t('scope.legend')}</legend>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[13px]">
        <label className="flex items-center gap-2">
          <input type="radio" name="collection-scope" checked={value.kind === 'project'} onChange={() => onChange({ ...value, kind: 'project' })} />
          {t('scope.wholeProject')}
          <span className="mono text-[12px] text-[color:var(--color-ink-2)]">{tn('scope.verified', verifiedCount(overview.total.by_status))}</span>
        </label>
        <label className="flex items-center gap-2">
          <input type="radio" name="collection-scope" checked={value.kind === 'collections'} onChange={() => onChange({ ...value, kind: 'collections' })} />
          {t('scope.onlyThese')}
        </label>
      </div>
      {value.kind === 'collections' && (
        <div className="mt-2 max-w-[46ch] border border-[color:var(--color-rule)]">
          {overview.collections.map((c) => row(String(c.id), c.name, verifiedCount(c.by_status), value.collection_ids.includes(c.id), () => toggle(c.id)))}
          {overview.unassigned.pages > 0 && row(
            'none', t('collections.none'), verifiedCount(overview.unassigned.by_status), value.include_unassigned,
            () => onChange({ ...value, include_unassigned: !value.include_unassigned }),
          )}
        </div>
      )}
      {scopeIsEmpty(value) && <p className="mt-1 text-[12px] text-[color:var(--color-sig-text)]">{t('scope.empty')}</p>}
    </fieldset>
  )
}
