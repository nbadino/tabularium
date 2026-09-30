import { expect, it } from 'vitest'
import { toFractionGlyphs } from './fractions'

it('converts typed fractions the sum checks know, and nothing else', () => {
  expect(toFractionGlyphs('8 1/8')).toBe('8⅛')
  expect(toFractionGlyphs('5,746 1/2')).toBe('5,746½')
  expect(toFractionGlyphs('3/4')).toBe('¾')
  expect(toFractionGlyphs('8⅜')).toBe('8⅜')
  expect(toFractionGlyphs('13 12/20')).toBe('13 12/20') // no glyph
  expect(toFractionGlyphs('2 1/7')).toBe('2 1/7') // no glyph
  expect(toFractionGlyphs('1/2/1909')).toBe('1/2/1909') // date
  expect(toFractionGlyphs('Gals.')).toBe('Gals.')
})
