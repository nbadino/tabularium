import { LOCALES, LOCALE_LABELS, useI18n } from '../i18n'

/** Compact IT · EN · FR locale switcher usable inside menus and auth screens. */
export function LocaleSwitch({ className = '' }: { className?: string }) {
  const { t, locale, setLocale } = useI18n()
  return (
    <div
      className={`flex items-center px-1 py-0.5 ${className}`}
      role="group"
      aria-label={t('layout.language')}
    >
      {LOCALES.map((l, i) => [
        i > 0 && (
          <span key={`sep-${l}`} aria-hidden="true" className="px-0.5 text-[color:var(--color-ink-3)]">
            ·
          </span>
        ),
        <button
          key={l}
          type="button"
          onClick={() => setLocale(l)}
          aria-pressed={l === locale}
          aria-label={LOCALE_LABELS[l]}
          title={LOCALE_LABELS[l]}
          className="menu-choice"
        >
          {l.toUpperCase()}
        </button>,
      ])}
    </div>
  )
}
