import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from './index'
import { labelName } from './labelName'

describe('labelName', () => {
  afterEach(() => setLocale('it'))

  it('translates the default classes', () => {
    setLocale('it')
    expect(labelName('Page-header')).toBe('Intestazione di pagina')
    setLocale('en')
    expect(labelName('Page-header')).toBe('Page header')
  })

  it('keeps a project class as it was named', () => {
    expect(labelName('Tonnellaggio')).toBe('Tonnellaggio')
  })
})
