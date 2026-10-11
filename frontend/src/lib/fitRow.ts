import { useLayoutEffect } from 'react'
import type { RefObject } from 'react'

/**
 * Tiene i figli di `ref` su una riga sola stringendo per gradi.
 *
 * Prova i livelli 0…`steps` in ordine e si ferma al primo in cui tutti i figli
 * visibili stanno alla stessa altezza. Il livello è scritto come elenco
 * cumulativo in `data-fit` («1 2 3» al livello 3): il CSS decide cosa togliere a
 * ogni gradino con `[data-fit~='n']`. Misurare invece di fissare soglie in pixel
 * regge le tre lingue, che hanno etichette di lunghezze diverse. Se nemmeno
 * l'ultimo livello basta, la riga va a capo come prima.
 */
export function useFitOneRow(ref: RefObject<HTMLElement | null>, steps: number, deps: unknown[]): void {
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const oneRow = () => {
      const tops = Array.from(el.children as HTMLCollectionOf<HTMLElement>)
        .filter((c) => c.offsetParent !== null)
        .map((c) => c.offsetTop)
      return tops.length === 0 || Math.max(...tops) - Math.min(...tops) < 4
    }
    const fit = () => {
      for (let level = 0; level <= steps; level++) {
        el.dataset.fit = Array.from({ length: level }, (_, i) => i + 1).join(' ')
        if (oneRow()) return
      }
    }
    fit()
    // Al primo avvio la misura può cadere prima dei caratteri: con il font di
    // ripiego le etichette hanno un'altra larghezza, e la barra non cambia
    // dimensione quando arrivano. Si rimisura a caratteri pronti.
    let alive = true
    void document.fonts?.ready.then(() => alive && fit())
    if (typeof ResizeObserver === 'undefined')
      return () => {
        alive = false
      }
    // Solo i cambi di larghezza: l'altezza cambia proprio perché `fit` funziona.
    let width = el.clientWidth
    const ro = new ResizeObserver(() => {
      if (el.clientWidth === width) return
      width = el.clientWidth
      fit()
    })
    ro.observe(el)
    return () => {
      alive = false
      ro.disconnect()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}
