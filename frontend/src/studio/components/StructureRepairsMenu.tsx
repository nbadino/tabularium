import type { TableStructureRepair } from '../../lib/types'
import { Menu } from '../../app/ui'
import { useI18n } from '../../i18n'

/**
 * What the structure guard (backend `domain/table_guard.py`) changed in the
 * model's draft of this table. Rows and columns are shown 1-based; nothing is
 * shown when the guard changed nothing.
 */
export default function StructureRepairsMenu({ repairs }: { repairs: TableStructureRepair[] | undefined }) {
  const { t } = useI18n()
  if (!repairs || repairs.length === 0) return null
  const line = (r: TableStructureRepair) => {
    const at = { row: (r.row ?? 0) + 1, col: (r.col ?? 0) + 1, header: (r.header ?? []).join(' | ') }
    const base = t(`table.repair_${r.rule}`, at)
    return r.before && r.after && r.before.length > 0 ? `${base}: ${r.before.join(' | ')} → ${r.after.join(' | ')}` : base
  }
  return (
    <Menu
      label={<span className="font-semibold text-[color:var(--color-warn)]">{t('table.structureRepairs', { n: repairs.length })}</span>}
      align="left"
      className="z-20"
    >
      <p className="px-2 py-1 text-[11px] text-[color:var(--color-ink-3)]">{t('table.structureRepairsIntro')}</p>
      <ul className="max-h-64 max-w-[36rem] overflow-auto px-2 pb-1 text-[11px]">
        {repairs.map((r, i) => (
          <li key={i} className="mono py-0.5">{line(r)}</li>
        ))}
      </ul>
    </Menu>
  )
}
