/**
 * Vocabolario dell'interfaccia.
 *
 * Il backend parla inglese (identificatori e log); l'interfaccia parla
 * italiano, inglese o francese. Nessun enum grezzo raggiunge lo schermo:
 * ogni etichetta passa da qui — e dietro le quinte dal dizionario i18n
 * (`src/i18n/`), così al cambio lingua si aggiorna tutto.
 */

import { t, tn } from '../i18n'

/** Stato di una pagina dell'archivio, nel vocabolario del prodotto. */
export function statusLabel(s: string): string {
  const key = `vocab.status.${s}`
  const label = t(key)
  return label !== key ? label : s
}

/**
 * Il rango di uno stato lungo il percorso della pagina: serve a ordinare la
 * rassegna e a capire quale sia il prossimo passo. `review` sta fuori dalla
 * progressione perché è un ritorno indietro, non un avanzamento.
 */
export const STATUS_RANK: Record<string, number> = {
  new: 0,
  annotated: 1,
  qa: 2,
  approved: 3,
  exported: 4,
  review: -1,
}

/**
 * Come si legge uno stato senza colore. Ogni stato ha una parola e un segno
 * distinto: chi non distingue i colori, o legge con uno screen reader, riceve
 * la stessa informazione di chiunque altro.
 */
export type StatusTone = 'neutral' | 'progress' | 'ok' | 'warn' | 'sig'

export const STATUS_TONE: Record<string, StatusTone> = {
  new: 'neutral',
  annotated: 'progress',
  qa: 'warn',
  approved: 'ok',
  exported: 'ok',
  review: 'sig',
}

/** Tipo editoriale della pagina, nel vocabolario del giornale. */
export function pageTypeLabel(type: string | null | undefined): string {
  if (!type) return '—'
  const label = t(`vocab.pageType.${type}`)
  return label !== `vocab.pageType.${type}` ? label : type
}

/** Stato di una run di training. */
export function runStateLabel(s: string): string {
  if (s === '—') return t('vocab.runState.none')
  const label = t(`vocab.runState.${s}`)
  return label !== `vocab.runState.${s}` ? label : s
}

/**
 * Un run terminato «finished» e uno «stopped» non sono la stessa cosa e non
 * devono apparire uguali: il primo ha esaurito le epoche, il secondo è stato
 * fermato a mano e il checkpoint potrebbe essere parziale.
 */
export const RUN_STATE_TONE: Record<string, StatusTone> = {
  running: 'progress',
  finished: 'ok',
  failed: 'sig',
  stopped: 'warn',
  starting: 'progress',
}

/** Famiglie di campioni del dataset ms-swift. */
export function familyLabel(f: string): string {
  const label = t(`vocab.family.${f}`)
  return label !== `vocab.family.${f}` ? label : f
}

/** Unità su cui si taglia lo split train/val. */
export function splitStrategyLabel(k: string): string {
  return t(`vocab.splitStrategy.${k}`)
}

/** Tipo di addestramento. */
export function trainTypeLabel(k: string): string {
  return t(`vocab.trainType.${k}`)
}

/**
 * Plurale multilingue: `n=1` usa la forma «one» della chiave i18n.
 * Usa chiavi con forma {one, other}, es. `tn('common.pagesCount', 3)`.
 */
export function pluralN(key: string, n: number): string {
  return tn(key, n)
}

/** Comodo alias: `pages(n)` = forma plurale di «pagina». */
export function pages(n: number): string {
  return tn('common.pagesCount', n)
}

/** Comodo alias: `projects(n)` = forma plurale di «progetto». */
export function projects(n: number): string {
  return tn('common.projectsCount', n)
}

/** Comodo alias: `lines(n)` = forma plurale di «riga». */
export function lines(n: number): string {
  return tn('common.linesCount', n)
}

/** Comodo alias: `blocks(n)` = forma plurale di «blocco». */
export function blocks(n: number): string {
  return tn('common.blocksCount', n)
}

/** Comodo alias: `warnings(n)` = forma plurale di «avviso». */
export function warnings(n: number): string {
  return tn('common.warningsCount', n)
}