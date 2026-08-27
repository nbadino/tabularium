import { useCallback, useRef, useState } from 'react'

/** Finestra temporale entro cui due modifiche con lo stesso `coalesceKey`
 * confluiscono in un unico passo di storia. */
const COALESCE_MS = 1000

/**
 * Semplice storico undo/redo (limite 50 passi) per liste di blocchi.
 *
 * Con `set(value, { coalesceKey })` le modifiche ravvicinate che condividono
 * lo stesso tasto (es. la stessa cella di trascrizione durante la digitazione)
 * si fondono in un solo passo: così «Annulla» riporta indietro un pensiero,
 * non una lettera alla volta.
 */
export function useHistory<T>(initial: T) {
  const [state, setState] = useState<{
    past: T[]
    present: T
    future: T[]
  }>({ past: [], present: initial, future: [] })

  // Ultimo tasto di fusione usato e quando: serve per decidere se la prossima
  // modifica continuano lo stesso gesto (digitazione, drag) o ne apre uno nuovo.
  const coalesceRef = useRef<{ key: string; at: number } | null>(null)

  const set = useCallback(
    (val: T | ((prev: T) => T), opts?: { coalesceKey?: string }) => {
      setState((s) => {
        const next =
          typeof val === 'function'
            ? (val as (prev: T) => T)(s.present)
            : val
        if (next === s.present) return s

        const capped = s.past.slice(-49)
        const key = opts?.coalesceKey
        let past = [...capped, s.present]
        if (key != null) {
          const last = coalesceRef.current
          if (last && last.key === key && Date.now() - last.at < COALESCE_MS) {
            past = capped // stesso gesto: niente nuovo passo
          }
          coalesceRef.current = { key, at: Date.now() }
        } else {
          coalesceRef.current = null
        }
        return { past, present: next, future: [] }
      })
    },
    [],
  )

  const undo = useCallback(() => {
    coalesceRef.current = null
    setState((s) =>
      s.past.length === 0
        ? s
        : {
            past: s.past.slice(0, -1),
            present: s.past[s.past.length - 1],
            future: [s.present, ...s.future],
          },
    )
  }, [])

  const redo = useCallback(() => {
    coalesceRef.current = null
    setState((s) =>
      s.future.length === 0
        ? s
        : {
            past: [...s.past, s.present],
            present: s.future[0],
            future: s.future.slice(1),
          },
    )
  }, [])

  /** Sostituisce il presente senza creare passi di storia e senza svuotarli:
   * pensato per aggiornamenti «invisibili» come il mapping degli id di server
   * dopo il salvataggio. */
  const replacePresent = useCallback(
    (val: T | ((prev: T) => T)) => {
      setState((s) => {
        const next =
          typeof val === 'function'
            ? (val as (prev: T) => T)(s.present)
            : val
        if (next === s.present) return s
        return { past: s.past, present: next, future: [] }
      })
    },
    [],
  )

  const reset = useCallback((val: T) => {
    coalesceRef.current = null
    setState({ past: [], present: val, future: [] })
  }, [])

  return {
    present: state.present,
    set,
    replacePresent,
    undo,
    redo,
    reset,
    canUndo: state.past.length > 0,
    canRedo: state.future.length > 0,
  }
}
