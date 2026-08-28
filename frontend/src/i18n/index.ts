/**
 * i18n — infrastruttura di localizzazione dell'app.
 *
 * L'interfaccia è multilingua (italiano/inglese/francese); il backend parla
 * inglese negli identificatori. Questo modulo è una libreria leggera senza
 * dipendenze: tre dizionari tipati (it/en/fr), interpolazione `{var}`,
 * plurali per lingua e persistenza della scelta in localStorage.
 *
 * Lo stato è un piccolo store modulare: `useI18n()` sottoscrive i componenti
 * via `useSyncExternalStore`, così al cambio lingua tutto ciò che usa `t`/`tn`
 * — anche dentro helper come `vocab.ts` o `errors.ts` — si aggiorna.
 */
import { useSyncExternalStore } from 'react'
import { it, type Dict } from './it'
import { en } from './en'
import { fr } from './fr'

export type Locale = 'it' | 'en' | 'fr'
export type { Dict }

export const LOCALES: Locale[] = ['it', 'en', 'fr']

export const LOCALE_LABELS: Record<Locale, string> = {
  it: 'Italiano',
  en: 'English',
  fr: 'Français',
}

export const dicts: Record<Locale, Dict> = { it, en, fr }

const KEY = 'tabularium.locale'

function load(): Locale {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'en' || v === 'fr') return v
  } catch {
    /* storage non disponibile: restiamo su it */
  }
  return 'it'
}

let current: Locale = load()
const listeners = new Set<() => void>()

function subscribe(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

function getSnapshot(): Locale {
  return current
}

export function getLocale(): Locale {
  return current
}

export function setLocale(locale: Locale): void {
  if (locale === current) return
  current = locale
  try {
    localStorage.setItem(KEY, locale)
  } catch {
    /* storage non disponibile */
  }
  // Il modulo può essere importato anche in test Node o durante prerendering.
  if (typeof document !== 'undefined') document.documentElement.lang = locale
  listeners.forEach((fn) => fn())
}

/** Ordine di fallback: lingua corrente per prima, poi la base italiana. */
function fallbackOrder(): Locale[] {
  if (current === 'it') return ['it', 'en', 'fr']
  return [current, 'it', 'en', 'fr']
}

export type PluralLeaf = { one: string; other: string }
type Leaf = string | PluralLeaf
interface DictNode {
  [k: string]: Leaf | DictNode
}

function isLeaf(v: unknown): v is Leaf {
  if (typeof v === 'string') return true
  if (typeof v !== 'object' || v === null) return false
  const keys = Object.keys(v)
  // foglia plurale: sono ammesse SOLO le chiavi one/other. Un nodo con altre
  // chiavi (es. {one, other, why}) è un oggetto annidato, non una foglia.
  return (
    keys.length === 2 &&
    'one' in v &&
    'other' in v &&
    typeof (v as PluralLeaf).one === 'string' &&
    typeof (v as PluralLeaf).other === 'string'
  )
}

function getLeaf(dict: Dict, path: string[]): Leaf | undefined {
  let node: DictNode = dict as unknown as DictNode
  for (let i = 0; i < path.length; i++) {
    const k = path[i]
    const next = node[k]
    if (next == null) return undefined
    if (isLeaf(next)) {
      return i === path.length - 1 ? (next as Leaf) : undefined
    }
    node = next
  }
  return undefined
}

function interp(s: string, vars?: Record<string, string | number>): string {
  if (!vars) return s
  return s.replace(/\{(\w+)\}/g, (_, k: string) =>
    k in vars ? String(vars[k]) : `{${k}}`,
  )
}

function pluralForm(locale: Locale, n: number): 'one' | 'other' {
  // Il francese usa il singolare anche per lo zero; it/en no.
  if (locale === 'fr') return n <= 1 ? 'one' : 'other'
  return n === 1 ? 'one' : 'other'
}

/** Traduce una chiave (es. `home.nextStep`), con interpolazione opzionale. */
export function t(key: string, vars?: Record<string, string | number>): string {
  const path = key.split('.')
  for (const l of fallbackOrder()) {
    const leaf = getLeaf(dicts[l], path)
    if (typeof leaf === 'string') return interp(leaf, vars)
    if (leaf) return interp(leaf.other, vars)
  }
  return key
}

/** Traduce una chiave plurale: `n=1` usa la forma «one», altrimenti «other». */
export function tn(
  key: string,
  n: number,
  vars?: Record<string, string | number>,
): string {
  const path = key.split('.')
  const v = { ...vars, count: n }
  for (const l of fallbackOrder()) {
    const leaf = getLeaf(dicts[l], path)
    if (typeof leaf === 'string') return interp(leaf, v)
    if (leaf) return interp(leaf[pluralForm(l, n)], v)
  }
  return key
}

export interface I18n {
  locale: Locale
  setLocale: (locale: Locale) => void
  t: (key: string, vars?: Record<string, string | number>) => string
  tn: (key: string, n: number, vars?: Record<string, string | number>) => string
}

/** Sottoscrive il componente al cambio lingua e restituisce gli helper. */
export function useI18n(): I18n {
  const locale = useSyncExternalStore(subscribe, getSnapshot)
  return {
    locale,
    setLocale,
    t,
    tn,
  }
}
