import { t } from './index'

/**
 * Display name of a region class. The default classes are translated; a class a
 * project added itself has no entry and keeps the name it was given. The stored
 * and exported name never changes.
 */
export function labelName(name: string): string {
  const key = `labelNames.${name}`
  const out = t(key)
  return out === key ? name : out
}
