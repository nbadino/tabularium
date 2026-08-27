import { describe, expect, it } from 'vitest'
import { scaleRatio, toDisplay, toPage } from './coords'

describe('coordinate workspace', () => {
  it('round-trips source pixels through preview ratio', () => {
    const ratio = scaleRatio({ w: 800, h: 1200 }, { w: 2000, h: 3000 })
    expect(toPage(toDisplay({ x: 417, y: 921 }, ratio), ratio)).toEqual({ x: 417, y: 921 })
  })
})
