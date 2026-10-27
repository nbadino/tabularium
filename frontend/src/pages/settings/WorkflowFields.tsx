/**
 * Vendor workflow controls, generated from the schema the API returns.
 *
 * The backend declares each model's workflow fields (`workflow_fields` in
 * `/api/system/model-settings/<id>`): kind, bounds, enum values, option hints. This
 * component is the only renderer, so adding a model — or a knob to an existing model —
 * never means writing JSX here. Labels are i18n keys when the schema provides one,
 * otherwise the key itself, which is what the panel has always shown for vendor names.
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
}

interface Props {
  fields: WorkflowFieldSpec[]
  values: Record<string, SettingValue> | undefined
  onChange: (key: string, value: SettingValue) => void
  disabled: boolean
}

const LABEL = 'block border-t border-[color:var(--color-rule)] pt-2'
const FIELD = 'fld mt-1 w-full'

function labelOf(field: WorkflowFieldSpec): string {
  return field.label ? t(field.label) : field.key
}

function hintOf(field: WorkflowFieldSpec, value: string): string {
  const hint = field.hints?.find(([option]) => option === value)?.[1]
  return hint ? t(hint) : ''
}

function Numeric({
  field, value, disabled, onChange, integer,
}: {
  field: WorkflowFieldSpec
  value: SettingValue
  disabled: boolean
  onChange: (next: SettingValue) => void
  integer: boolean
}) {
  const current = typeof value === 'number' ? value : ''
  return (
    <input
      className={FIELD}
      type="number"
      min={field.minimum ?? undefined}
      max={field.maximum ?? undefined}
      step={field.step ?? (integer ? 1 : 0.01)}
      value={current}
      placeholder={field.default == null ? t('settings.modelAuto') : String(field.default)}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value === '' ? null : Number(event.target.value))}
    />
  )
}

export default function WorkflowFields({ fields, values, onChange, disabled }: Props) {
  return (
    <>
      {fields.map((field) => {
        const value = values?.[field.key] ?? null
        const label = labelOf(field)
        if (field.kind === 'bool') {
          return (
            <label key={field.key} className={LABEL}>
              <span className="lbl">{label}</span>
              <select
                className={FIELD}
                value={value == null ? '' : String(value)}
                disabled={disabled}
                onChange={(event) => onChange(field.key, event.target.value === '' ? null : event.target.value === 'true')}
              >
                {field.nullable && <option value="">{t('settings.modelAuto')}</option>}
                <option value="true">{t('settings.paddleEnabled')}</option>
                <option value="false">{t('settings.paddleDisabled')}</option>
              </select>
            </label>
          )
        }
        if (field.kind === 'enum') {
          return (
            <label key={field.key} className={LABEL}>
              <span className="lbl">{label}</span>
              <select
                className={FIELD}
                value={value == null ? '' : String(value)}
                disabled={disabled}
                onChange={(event) => onChange(field.key, event.target.value === '' ? null : event.target.value)}
              >
                {field.nullable && <option value="">{t('settings.modelAuto')}</option>}
                {(field.values ?? []).map((option) => {
                  const hint = hintOf(field, option)
                  return <option key={option} value={option}>{hint ? `${option} · ${hint}` : option}</option>
                })}
              </select>
            </label>
          )
        }
        if (field.kind === 'int' || field.kind === 'float') {
          return (
            <label key={field.key} className={LABEL}>
              <span className="lbl">{label}</span>
              <Numeric field={field} value={value} disabled={disabled} integer={field.kind === 'int'} onChange={(next) => onChange(field.key, next)} />
            </label>
          )
        }
        if (field.kind === 'version' || field.kind === 'str') {
          return (
            <label key={field.key} className={LABEL}>
              <span className="lbl">{label}</span>
              <input
                className={FIELD}
                type="text"
                value={typeof value === 'string' ? value : ''}
                placeholder={typeof field.default === 'string' ? field.default : ''}
                maxLength={field.max_len ?? undefined}
                disabled={disabled}
                onChange={(event) => onChange(field.key, event.target.value === '' ? null : event.target.value)}
              />
            </label>
          )
        }
        if (field.kind === 'str_list') {
          return (
            <label key={field.key} className={LABEL}>
              <span className="lbl">{label}</span>
              <input
                className={FIELD}
                type="text"
                value={Array.isArray(value) ? value.join(', ') : ''}
                placeholder={Array.isArray(field.default) ? field.default.join(', ') : ''}
                disabled={disabled}
                onChange={(event) => {
                  const items = event.target.value.split(',').map((item) => item.trim()).filter(Boolean)
                  onChange(field.key, items.length ? items : null)
                }}
              />
            </label>
          )
        }
        if (field.kind === 'int_pair') {
          const pair = (Array.isArray(value) ? value : Array.isArray(field.default) ? field.default : [0, 0]) as number[]
          return (
            <label key={field.key} className={LABEL}>
              <span className="lbl">{label}</span>
              <div className="mt-1 flex gap-2">
                {[0, 1].map((axis) => (
                  <input
                    key={axis}
                    className={FIELD}
                    type="number"
                    min={field.minimum ?? undefined}
                    max={field.maximum ?? undefined}
                    value={Number(pair[axis] ?? 0)}
                    disabled={disabled}
                    onChange={(event) => {
                      const next = [...pair]
                      next[axis] = Number(event.target.value)
                      onChange(field.key, next)
                    }}
                  />
                ))}
              </div>
            </label>
          )
        }
        if (field.kind === 'crop_box') {
          const box = (value && !Array.isArray(value) && typeof value === 'object' ? value : {}) as Record<string, number>
          return (
            <label key={field.key} className={LABEL}>
              <span className="lbl">{label}</span>
              <div className="mt-1 grid grid-cols-2 gap-2">
                {(['top', 'bottom', 'left', 'right'] as const).map((edge) => (
                  <input
                    key={edge}
                    className={FIELD}
                    type="number"
                    min={0}
                    max={0.99}
                    step={0.01}
                    value={box[edge] ?? 0}
                    disabled={disabled}
                    onChange={(event) => onChange(field.key, { ...box, [edge]: Number(event.target.value) })}
                  />
                ))}
              </div>
            </label>
          )
        }
        return null
      })}
    </>
  )
}
