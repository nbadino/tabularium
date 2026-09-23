/**
 * Date che arrivano dal server.
 *
 * SQLite scrive `CURRENT_TIMESTAMP` in UTC e senza fuso
 * (`2026-09-21 18:08:45`): `new Date()` lo legge come ora *locale*, e ogni
 * orario risultava spostato dell'offset del fuso. Una data senza fuso si
 * legge qui come UTC; quelle con fuso esplicito restano come sono.
 */
const NAIVE = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/

export function parseServerDate(value: string | null | undefined): Date | null {
  if (!value) return null
  const text = value.trim()
  const date = new Date(NAIVE.test(text) ? `${text.replace(' ', 'T')}Z` : text)
  return Number.isNaN(date.getTime()) ? null : date
}

/** `23 set, 16:40` nella lingua dell'interfaccia. */
export function formatShortDate(value: string | null | undefined, locale: string): string {
  const date = parseServerDate(value)
  return date ? date.toLocaleString(locale, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : ''
}

/** `23 set 2026, 16:40` nella lingua dell'interfaccia. */
export function formatDateTime(value: string | null | undefined, locale: string): string {
  const date = parseServerDate(value)
  return date ? new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(date) : ''
}
