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
