/**
 * Il progetto attivo, uno solo per tutta l'app.
 *
 * Sei pagine chiedevano «scegli progetto» e tutte e sei si aprivano vuote:
 * la stessa decisione, ripetuta. Qui la scelta si ricorda e ogni pagina
 * riparte da dove eri. Il selettore resta ovunque — cambiare archivio a
 * metà lavoro deve restare possibile — ma non è più un pedaggio d'ingresso.
 */
import { useEffect, useRef, useState } from 'react'
import { apiGet } from '../lib/api'
import type { Project } from '../lib/types'

const KEY = 'tabularium.activeProject'

export function readActiveProject(): number | null {
  try {
    const raw = Number(localStorage.getItem(KEY))
    return Number.isInteger(raw) && raw > 0 ? raw : null
  } catch {
    return null
  }
}

export function writeActiveProject(id: number | null): void {
  try {
    if (id == null) localStorage.removeItem(KEY)
    else localStorage.setItem(KEY, String(id))
  } catch {
    /* storage non disponibile: si resta senza memoria, non si rompe nulla */
  }
}

/**
 * Quale progetto aprire: quello ricordato se esiste ancora, altrimenti
 * quello con più pagine — il corpus su cui si sta davvero lavorando.
 */
export function pickActive(items: Project[]): number | null {
  const remembered = readActiveProject()
  const chosen =
    items.find((p) => p.id === remembered) ??
    [...items].sort((a, b) => b.pages_count - a.pages_count)[0]
  return chosen?.id ?? null
}

/**
 * Carica l'elenco dei progetti e apre subito quello attivo.
 *
 * `onPick` riceve la scelta iniziale esattamente come se l'avesse fatta
 * l'utente: gli effetti collaterali della pagina restano scritti in un
 * posto solo, il suo handler di selezione.
 */
export function useProjects(
  onPick: (pid: number) => void,
  onError?: (e: unknown) => void,
): Project[] {
  const [projects, setProjects] = useState<Project[]>([])
  const pick = useRef(onPick)
  const fail = useRef(onError)
  pick.current = onPick
  fail.current = onError

  useEffect(() => {
    let alive = true
    apiGet<{ items: Project[] }>('/projects')
      .then((r) => {
        if (!alive) return
        setProjects(r.items)
        const id = pickActive(r.items)
        if (id != null) pick.current(id)
      })
      .catch((e) => {
        if (alive) fail.current?.(e)
      })
    return () => {
      alive = false
    }
  }, [])

  return projects
}
