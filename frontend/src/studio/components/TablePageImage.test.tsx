// @vitest-environment jsdom
/**
 * La scansione accanto al foglio si adatta una volta per immagine. Lo Studio
 * si ri-renderizza da solo (polling della sessione) passando un `imageSize`
 * nuovo a ogni giro: lo zoom dell'annotatore non deve tornare a «adatta».
 */
import { afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import TablePageImage from './TablePageImage'

afterEach(cleanup)

beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 400 })
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => 400 })
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
})

it('keeps the zoom when the parent re-renders with an equal imageSize', () => {
  const view = (size: { w: number; h: number }) => <TablePageImage src="/p.jpg" pageId={1} imageSize={size} />
  const { rerender, container } = render(view({ w: 2000, h: 2000 }))
  const label = () => container.querySelector('.mono')?.textContent
  expect(label()).toBe('19%') // 400 / 2000 × 0,96
  fireEvent.click(screen.getAllByRole('button')[1]) // zoom in
  expect(label()).toBe('24%')
  rerender(view({ w: 2000, h: 2000 }))
  expect(label()).toBe('24%')
})
