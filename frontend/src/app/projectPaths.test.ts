import { describe, expect, it } from 'vitest'
import { projectPath, switchProjectPath } from './projectPaths'

describe('project paths', () => {
  it('builds the project sections', () => {
    expect(projectPath(7)).toBe('/progetti/7')
    expect(projectPath(7, 'riconoscimenti')).toBe('/progetti/7/riconoscimenti')
    expect(projectPath(7, 'studio', { page: 3 })).toBe('/progetti/7/studio?page=3')
  })

  it('switching project keeps the section but not the item inside it', () => {
    expect(switchProjectPath('/progetti/2/fine-tuning/addestra', 9)).toBe('/progetti/9/fine-tuning/addestra')
    expect(switchProjectPath('/progetti/2/riconoscimenti/24', 9)).toBe('/progetti/9/riconoscimenti')
    expect(switchProjectPath('/progetti/2/studio', 9)).toBe('/progetti/9/studio')
    expect(switchProjectPath('/progetti/2', 9)).toBe('/progetti/9')
  })
})
