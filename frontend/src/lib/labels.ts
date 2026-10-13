/**
 * Classi di annotazione: logica pura, senza React.
 *
 * Una classe ha un'**origine** (`public` se il modello base la conosce già,
 * `custom` se è un token da insegnare) e una lista di modelli che la emettono
 * nativamente. «Già nota» è una proprietà della coppia classe + modello, non
 * della classe in astratto: `knownByModel` risponde rispetto al modello attivo.
 *
 * Le classi `builtin` (schema di default) restano sempre; le custom si
 * aggiungono e si rimuovono, e sono l'unica cosa che il PUT sostituisce.
 */
import type { LabelDef, LabelInput } from './types'

export type LabelKind = LabelDef['prompt_kind']

export const LABEL_KINDS: LabelKind[] = ['text', 'table', 'formula', 'picture', 'structure']

/** Tipi che non hanno senso senza un prompt di riconoscimento. */
export const KINDS_REQUIRING_PROMPT: LabelKind[] = ['text', 'table', 'formula']

export function requiresPrompt(kind: LabelKind): boolean {
  return KINDS_REQUIRING_PROMPT.includes(kind)
}

export function isRemovable(label: LabelDef): boolean {
  return !label.builtin
}

/** Le sole classi custom del progetto: quelle che il PUT sostituisce. */
export function customsOf(labels: LabelDef[]): LabelDef[] {
  return labels.filter(isRemovable)
}

/**
 * Il modello `adapterId` emette già questa classe?
 * Senza modello attivo, «nota» = nota ad almeno un modello supportato.
 */
export function knownByModel(label: LabelDef, adapterId?: string | null): boolean {
  if (!adapterId) return label.known_models.length > 0
  return label.known_models.includes(adapterId)
}

export function toInput(label: LabelDef): LabelInput {
  return {
    name: label.name,
    color: label.color,
    short: label.short,
    prompt_kind: label.prompt_kind,
    prompt: label.prompt,
    aliases: label.aliases ?? [],
  }
}

export function emptyDraft(): LabelInput {
  return { name: '', color: '#4a5560', short: '', prompt_kind: 'text', prompt: '', aliases: [] }
}

/** I sinonimi come testo (separati da virgola) per un unico campo di input. */
export function aliasesToText(aliases: string[]): string {
  return aliases.join(', ')
}

export function parseAliases(text: string): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const raw of text.split(',')) {
    const token = raw.trim()
    if (!token || seen.has(token)) continue
    seen.add(token)
    out.push(token)
    if (out.length >= 20) break
  }
  return out
}

const NAME_RE = /^[A-Za-z][A-Za-z0-9 ._-]{0,39}$/
const COLOR_RE = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/

/**
 * Controllo leggero lato client: restituisce un codice (`name`, `color`,
 * `short`, `prompt`, `duplicate`) o `null`. Il backend resta la fonte di
 * verità; questo evita solo il viaggio a vuoto più ovvio.
 */
export function draftError(
  draft: LabelInput,
  existing: string[],
  editing?: string | null,
): string | null {
  const name = draft.name.trim()
  if (!NAME_RE.test(name)) return 'name'
  if (name !== editing && existing.includes(name)) return 'duplicate'
  if (!COLOR_RE.test(draft.color.trim())) return 'color'
  const short = draft.short.trim()
  if (short.length < 1 || short.length > 6) return 'short'
  if (requiresPrompt(draft.prompt_kind) && !draft.prompt.trim()) return 'prompt'
  return null
}
