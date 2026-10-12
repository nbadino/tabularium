import { expect, it } from 'vitest'
import { toFractionGlyphs } from './fractions'

it('converts typed fractions, and leaves dates and bare ratios alone', () => {
  expect(toFractionGlyphs('8 1/8')).toBe('8⅛')
  expect(toFractionGlyphs('5,746 1/2')).toBe('5,746½')
  expect(toFractionGlyphs('3/4')).toBe('¾')
  expect(toFractionGlyphs('8⅜')).toBe('8⅜')
  expect(toFractionGlyphs('13 12/20')).toBe('13¹²⁄₂₀') // no glyph: stacked
  expect(toFractionGlyphs('125,419 9/20')).toBe('125,419⁹⁄₂₀')
  expect(toFractionGlyphs('2 1/7')).toBe('2¹⁄₇')
  expect(toFractionGlyphs('12/20')).toBe('12/20') // no whole part
  expect(toFractionGlyphs('4 1/0')).toBe('4 1/0')
  expect(toFractionGlyphs('13¹²⁄₂₀')).toBe('13¹²⁄₂₀')
  expect(toFractionGlyphs('1/2/1909')).toBe('1/2/1909') // date
  expect(toFractionGlyphs('Gals.')).toBe('Gals.')
})
