/**
 * Avatar testuali: iniziali e colore stabile da un'identità.
 *
 * Stessa tavolozza del canale live lato backend (`services/live.py`) così un
 * utente ha la stessa tinta nello Studio e nella finestra di condivisione.
 */

const PALETTE = [
  '#2563eb',
  '#059669',
  '#d97706',
  '#7c3aed',
  '#db2777',
  '#0891b2',
  '#65a30d',
  '#dc2626',
  '#4f46e5',
  '#0d9488',
]

/** Iniziali per l'avatar (max 2 caratteri). */
export function initialsOf(name: string): string {
  const parts = name.trim().split(/[\s._-]+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2)
  return `${parts[0][0]}${parts[1][0]}`
}

/** Colore stabile per un'identità (hash semplice, non crittografico). */
export function avatarColor(seed: string | number | null | undefined): string {
  const key = seed == null ? '' : String(seed)
  if (!key) return PALETTE[0]
  let hash = 0
  for (let i = 0; i < key.length; i += 1) {
    hash = (hash * 31 + key.charCodeAt(i)) | 0
  }
  return PALETTE[Math.abs(hash) % PALETTE.length]
}
