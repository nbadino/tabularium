// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'
import { readPreference, resolveSshEndpoint } from './CloudControlModal'

describe('CloudControlModal preferences', () => {
  beforeEach(() => localStorage.clear())

  it('reads a stored preference', () => {
    localStorage.setItem('tabularium.modal.template', 'mineru')
    expect(readPreference('tabularium.modal.template')).toBe('mineru')
  })

  it('returns null when nothing is stored', () => {
    expect(readPreference('tabularium.modal.keep_warm')).toBeNull()
  })
})

describe('resolveSshEndpoint', () => {
  const proxied = { ssh_host: 'ssh7.vast.ai', ssh_port: 34880 }

  it('uses the endpoint published by the provider when there is no override', () => {
    expect(resolveSshEndpoint(proxied, '', '')).toEqual({ host: 'ssh7.vast.ai', port: 34880 })
  })

  it('prefers the direct address the user pasted from the provider console', () => {
    expect(resolveSshEndpoint(proxied, ' 173.239.92.155 ', ' 41934 ')).toEqual({
      host: '173.239.92.155',
      port: 41934,
    })
  })

  it('never mixes half an override with half the published endpoint', () => {
    // Solo la porta: l'host del proxy con la porta diretta non è una
    // destinazione che esiste, quindi si resta interamente sul proxy.
    expect(resolveSshEndpoint(proxied, '', '41934')).toEqual({ host: 'ssh7.vast.ai', port: 34880 })
    expect(resolveSshEndpoint(proxied, '173.239.92.155', '')).toEqual({ host: 'ssh7.vast.ai', port: 34880 })
    expect(resolveSshEndpoint(proxied, '173.239.92.155', 'non-una-porta')).toEqual({
      host: 'ssh7.vast.ai',
      port: 34880,
    })
  })

  it('has nothing to offer when the instance is still starting', () => {
    expect(resolveSshEndpoint({ ssh_host: null, ssh_port: null }, '', '')).toBeNull()
  })
})
