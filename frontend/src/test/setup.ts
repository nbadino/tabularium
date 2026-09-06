import '@testing-library/jest-dom/vitest'

// Handsontable osserva il layout del contenitore; jsdom non implementa le
// API browser che usa per adattare il foglio. Sono stub globali, non locali a
// un singolo test, perché Vitest può eseguire i file in parallelo.
{
  class ResizeObserverStub {
    constructor(private readonly callback: ResizeObserverCallback) {}
    observe(target: Element) {
      this.callback([{ target, contentRect: target.getBoundingClientRect() } as ResizeObserverEntry], this as unknown as ResizeObserver)
    }
    unobserve() {}
    disconnect() {}
  }
  window.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver
}

{
  class IntersectionObserverStub {
    constructor(private readonly callback: IntersectionObserverCallback) {}
    observe(target: Element) {
      this.callback([{ target, isIntersecting: true, intersectionRatio: 1 } as IntersectionObserverEntry], this as unknown as IntersectionObserver)
    }
    unobserve() {}
    disconnect() {}
  }
  window.IntersectionObserver = IntersectionObserverStub as unknown as typeof IntersectionObserver
}

// Node 24+ espone un `localStorage` proprio, inerte senza `--localstorage-file`.
// Essendo un global del runtime, oscura quello di jsdom: l'accesso restituisce
// undefined e ogni test che tocchi le preferenze fallisce, ma solo su quelle
// versioni di Node. Qui si ripristina l'implementazione di jsdom quando il
// global risulta assente o inservibile, così la suite non dipende dalla
// versione di Node installata.
{
  const usable = (store: unknown): boolean => {
    try {
      const s = store as Storage | undefined
      if (!s || typeof s.setItem !== 'function') return false
      s.setItem('__probe__', '1')
      s.removeItem('__probe__')
      return true
    } catch {
      return false
    }
  }

  if (!usable(globalThis.localStorage)) {
    const backing = new Map<string, string>()
    const shim: Storage = {
      get length() { return backing.size },
      key: (i: number) => Array.from(backing.keys())[i] ?? null,
      getItem: (k: string) => (backing.has(k) ? backing.get(k)! : null),
      setItem: (k: string, v: string) => void backing.set(String(k), String(v)),
      removeItem: (k: string) => void backing.delete(k),
      clear: () => backing.clear(),
    }
    Object.defineProperty(globalThis, 'localStorage', {
      value: shim, configurable: true, writable: true,
    })
    Object.defineProperty(window, 'localStorage', {
      value: shim, configurable: true, writable: true,
    })
  }
}
