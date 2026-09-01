// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'
import { readMigratedPreference } from './CloudControlModal'

describe('CloudControlModal preferences', () => {
  beforeEach(() => localStorage.clear())

  it('migrates legacy preference and removes the old key', () => {
    localStorage.setItem('lloyds.modal_template', 'mineru')

    expect(readMigratedPreference('tabularium.modal.template', 'lloyds.modal_template')).toBe('mineru')
    expect(localStorage.getItem('tabularium.modal.template')).toBe('mineru')
    expect(localStorage.getItem('lloyds.modal_template')).toBeNull()
  })

  it('prefers the current preference and removes legacy state', () => {
    localStorage.setItem('tabularium.modal.keep_warm', '1')
    localStorage.setItem('lloyds.modal_keep_warm', '0')

    expect(readMigratedPreference('tabularium.modal.keep_warm', 'lloyds.modal_keep_warm')).toBe('1')
    expect(localStorage.getItem('lloyds.modal_keep_warm')).toBeNull()
  })
})
