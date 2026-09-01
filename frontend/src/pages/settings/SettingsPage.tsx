/**
 * Impostazioni: cinque zone, una sola dominante per volta.
 *
 * Prima era una colonna unica di sette moduli impilati — account, generale,
 * card inferenza, profili compute, backup, modello per il prefill, ambiente —
 * dove tre di quei moduli scrivevano la stessa configurazione e nessuno
 * diceva quale fosse quella in vigore. La densità del mosaico regge un form
 * fitto, non sette argomenti diversi in fila.
 *
 * Qui vale la stessa grammatica del rail dello studio (v. DESIGN.md, §
 * Navigazione): le zone sono linguette, ne domina una, e la scelta resta
 * nell'URL (`?s=`) così un link porta esattamente dove serve.
 */
import { useI18n } from '../../i18n'
import { useAuth } from '../../app/auth'
import type { User } from '../../lib/types'
import { useSearchParams } from 'react-router'
import AccountSection from './AccountSection'
import ComputeSection from './ComputeSection'
import DataSection from './DataSection'
import EnvironmentSection from './EnvironmentSection'
import InstanceSection from './InstanceSection'

/** Ogni zona riceve solo ciò che cambia il suo comportamento: chi governa. */
export interface SectionProps {
  isAdmin: boolean
}

const SECTIONS: Array<{
  id: string
  labelKey: string
  Section: (props: SectionProps) => React.ReactElement
}> = [
  { id: 'account', labelKey: 'settings.tabAccount', Section: AccountSection },
  { id: 'istanza', labelKey: 'settings.tabInstance', Section: InstanceSection },
  { id: 'calcolo', labelKey: 'settings.tabCompute', Section: ComputeSection },
  { id: 'dati', labelKey: 'settings.tabData', Section: DataSection },
  { id: 'ambiente', labelKey: 'settings.tabEnv', Section: EnvironmentSection },
]

export default function SettingsPage() {
  const { t } = useI18n()
  const { user } = useAuth()
  const [params, setParams] = useSearchParams()

  const current = SECTIONS.find((s) => s.id === params.get('s')) ?? SECTIONS[0]
  const isAdmin = (user as User | null)?.role === 'admin'
  const Section = current.Section

  return (
    <div className="p-3">
      <div className="border-b border-[color:var(--color-rule-strong)] pb-3">
        <h1 className="text-[26px] font-bold leading-tight tracking-[-0.03em]">
          {t('settings.title')}
        </h1>
        <p className="mt-1 max-w-[78ch] text-[13px] text-[color:var(--color-ink-2)]">
          {t('settings.intro')}
        </p>
      </div>

      <nav
        aria-label={t('settings.sections')}
        className="-mx-3 flex overflow-x-auto border-b border-[color:var(--color-rule)]"
      >
        <span className="w-px shrink-0 bg-[color:var(--color-rule)]" />
        {SECTIONS.map((section) => (
          <button
            key={section.id}
            type="button"
            onClick={() => setParams(section.id === SECTIONS[0].id ? {} : { s: section.id })}
            aria-current={section.id === current.id ? 'page' : undefined}
            className="shrink-0"
          >
            <span className="navtab" data-active={section.id === current.id}>
              {t(section.labelKey)}
            </span>
          </button>
        ))}
      </nav>

      {/* `key`: il cambio di zona è uno scambio in posto, non un accumulo. */}
      <div key={current.id} className="swap pt-3">
        <Section isAdmin={isAdmin} />
      </div>
    </div>
  )
}
