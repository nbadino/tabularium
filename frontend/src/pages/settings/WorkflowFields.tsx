/**
 * Vendor workflow controls, generated from the schema the API returns.
 *
 * The backend declares each model's workflow fields (`workflow_fields` in
 * `/api/system/model-settings/<id>`): kind, bounds, enum values, option hints, and
 * whether the field is essential. This module is the only renderer of a control, so
 * adding a model — or a knob to an existing model — never means writing JSX here. The
 * row around it (label, official value, reset) belongs to the parameter panel.
 */
import { t } from '../../i18n'

export type SettingValue = number | string | boolean | number[] | string[] | Record<string, number> | null

export interface WorkflowFieldSpec {
  key: string
  kind: 'bool' | 'int' | 'float' | 'enum' | 'version' | 'str' | 'str_list' | 'int_pair' | 'crop_box'
  default?: SettingValue
  minimum?: number | null
  maximum?: number | null
  step?: number | null
  values?: string[]
  nullable?: boolean
  label?: string
  hints?: [string, string][]
  max_len?: number | null
  max_items?: number | null
  /** Shown among the panel's essentials (declared by the model spec). */
  essential?: boolean
}

const FIELD = 'fld w-full'

/** Label of a vendor field: its i18n key when the schema gives one, else the vendor name. */
export function workflowLabel(field: WorkflowFieldSpec): string {
  return field.label ? t(field.label) : field.key
}

/** A value as the panel prints it next to "official:". */
export function workflowValueText(field: WorkflowFieldSpec, value: SettingValue | undefined): string {
  if (value == null) return t('settings.params.vendorAuto')
  if (field.kind === 'bool') return value ? t('settings.paddleEnabled') : t('settings.paddleDisabled')
  if (Array.isArray(value)) return value.join(', ')
  if (typeof value === 'object') return Object.entries(value).map(([k, v]) => `${k} ${v}`).join(' · ')
  return String(value)
}

function hintOf(field: WorkflowFieldSpec, value: string): string {
  const hint = field.hints?.find(([option]) => option === value)?.[1]
  return hint ? t(hint) : ''
}

interface ControlProps {
  field: WorkflowFieldSpec
  value: SettingValue | undefined
  onChange: (value: SettingValue) => void
  disabled: boolean
  /** Id of the control, for the row's `<label htmlFor>`; groups use it as their id. */
  id: string
  /** Accessible name for a group of inputs (pairs, crop box). */
  label: string
}

export function WorkflowControl({ field, value: raw, onChange, disabled, id, label }: ControlProps) {
  const value = raw ?? null
  if (field.kind === 'bool') {
    return (
      <select id={id} className={FIELD} value={value == null ? '' : String(value)} disabled={disabled}
        onChange={(event) => onChange(event.target.value === '' ? null : event.target.value === 'true')}>
        {field.nullable && <option value="">{t('settings.params.useOfficial')}</option>}
        <option value="true">{t('settings.paddleEnabled')}</option>
        <option value="false">{t('settings.paddleDisabled')}</option>
      </select>
    )
  }
  if (field.kind === 'enum') {
    return (
      <select id={id} className={FIELD} value={value == null ? '' : String(value)} disabled={disabled}
        onChange={(event) => onChange(event.target.value === '' ? null : event.target.value)}>
        {field.nullable && <option value="">{t('settings.params.useOfficial')}</option>}
        {(field.values ?? []).map((option) => {
          const hint = hintOf(field, option)
          return <option key={option} value={option}>{hint ? `${option} · ${hint}` : option}</option>
        })}
      </select>
    )
  }
  if (field.kind === 'int' || field.kind === 'float') {
    return (
      <input id={id} className={FIELD} type="number"
        min={field.minimum ?? undefined} max={field.maximum ?? undefined}
        step={field.step ?? (field.kind === 'int' ? 1 : 0.01)}
        value={typeof value === 'number' ? value : ''}
        placeholder={field.default == null ? t('settings.params.useOfficial') : String(field.default)}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value === '' ? null : Number(event.target.value))} />
    )
  }
  if (field.kind === 'version' || field.kind === 'str') {
    return (
      <input id={id} className={FIELD} type="text"
        value={typeof value === 'string' ? value : ''}
        placeholder={typeof field.default === 'string' ? field.default : ''}
        maxLength={field.max_len ?? undefined} disabled={disabled}
        onChange={(event) => onChange(event.target.value === '' ? null : event.target.value)} />
    )
  }
  if (field.kind === 'str_list') {
    return (
      <input id={id} className={FIELD} type="text"
        value={Array.isArray(value) ? value.join(', ') : ''}
        placeholder={Array.isArray(field.default) ? field.default.join(', ') : ''}
        disabled={disabled}
        onChange={(event) => {
          const items = event.target.value.split(',').map((item) => item.trim()).filter(Boolean)
          onChange(items.length ? items : null)
        }} />
    )
  }
  if (field.kind === 'int_pair') {
    const pair = (Array.isArray(value) ? value : Array.isArray(field.default) ? field.default : [0, 0]) as number[]
    return (
      <div id={id} role="group" aria-label={label} className="flex gap-2">
        {[0, 1].map((axis) => (
          <input key={axis} className={FIELD} type="number" aria-label={`${label} ${axis + 1}`}
            min={field.minimum ?? undefined} max={field.maximum ?? undefined}
            value={Number(pair[axis] ?? 0)} disabled={disabled}
            onChange={(event) => {
              const next = [...pair]
              next[axis] = Number(event.target.value)
              onChange(next)
            }} />
        ))}
      </div>
    )
  }
  if (field.kind === 'crop_box') {
    const box = (value && !Array.isArray(value) && typeof value === 'object' ? value : {}) as Record<string, number>
    return (
      <div id={id} role="group" aria-label={label} className="grid grid-cols-2 gap-2">
        {(['top', 'bottom', 'left', 'right'] as const).map((edge) => (
          <input key={edge} className={FIELD} type="number" aria-label={`${label} ${edge}`}
            min={0} max={0.99} step={0.01} value={box[edge] ?? 0} disabled={disabled}
            onChange={(event) => onChange({ ...box, [edge]: Number(event.target.value) })} />
        ))}
      </div>
    )
  }
  return null
}
