/**
 * Movimento dei cambi di forma: ciò che cambia altezza o posto ci arriva
 * scorrendo invece di saltare. Web Animations API, solo `height` e
 * `transform`, e niente sotto `prefers-reduced-motion`.
 */
import { useLayoutEffect, useRef } from 'react'
import type { RefObject } from 'react'

/** La curva di tutto il sistema (v. DESIGN.md § Movimento). */
export const EASE_OUT = 'cubic-bezier(0.16, 1, 0.3, 1)'
const DURATION = 260

export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)
}

/**
 * Quando `shape` cambia (una riga che si chiude in una riga sola e viceversa)
 * l'elemento scorre dalla vecchia alla nuova altezza. L'altezza si annota a
 * ogni render, così la «vecchia» è quella che si vedeva davvero un attimo prima.
 */
export function useHeightTransition(ref: RefObject<HTMLElement | null>, shape: unknown): void {
  const height = useRef<number | null>(null)
  const lastShape = useRef(shape)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const next = el.getBoundingClientRect().height
    const prev = height.current
    height.current = next
    if (Object.is(lastShape.current, shape)) return
    lastShape.current = shape
    if (prev == null || Math.abs(prev - next) < 2 || prefersReducedMotion() || !el.animate) return
    el.style.overflow = 'hidden'
    const anim = el.animate([{ height: `${prev}px` }, { height: `${next}px` }], { duration: DURATION, easing: EASE_OUT })
    const done = () => {
      el.style.overflow = ''
    }
    anim.onfinish = done
    anim.oncancel = done
  })
}

/**
 * Le righe di una lista che cambiano posto (una bozza confermata, un blocco
 * tolto, un riordino) scivolano al posto nuovo: tecnica FLIP — si misura dopo
 * il cambio, si parte dalla posizione vecchia, si arriva a zero. Le righe si
 * riconoscono da `data-block`. La lista deve essere posizionata (`relative`):
 * `offsetTop` si misura così dalla lista e non da ciò che le sta sopra — lo
 * stream del modello, che crescendo farebbe «muovere» ogni riga a ogni token.
 */
export function useFlipList(ref: RefObject<HTMLElement | null>): void {
  const tops = useRef(new Map<string, number>())
  useLayoutEffect(() => {
    const list = ref.current
    if (!list) return
    const next = new Map<string, number>()
    const animate = !prefersReducedMotion()
    list.querySelectorAll<HTMLElement>(':scope > [data-block]').forEach((row) => {
      const id = row.dataset.block as string
      const top = row.offsetTop
      next.set(id, top)
      const old = tops.current.get(id)
      if (animate && old != null && Math.abs(old - top) > 1 && row.animate)
        row.animate([{ transform: `translateY(${old - top}px)` }, { transform: 'none' }], {
          duration: DURATION,
          easing: EASE_OUT,
        })
    })
    tops.current = next
  })
}
