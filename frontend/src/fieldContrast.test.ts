import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

// WCAG 1.4.11: a control's boundary needs 3:1 against its background at rest.
// The field border was --color-rule (#e5e3e1, about 1.2:1 on white).
const css = readFileSync('src/index.css', 'utf8')

function token(name: string): string {
  const match = css.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`))
  if (!match) throw new Error(`token ${name} not found`)
  return match[1]
}

function luminance(hex: string): number {
  const channel = (i: number) => {
    const v = parseInt(hex.slice(i, i + 2), 16) / 255
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5)
}

describe('field border', () => {
  it('is visible at rest (3:1 against the sheet)', () => {
    const block = css.match(/\.fld \{([^}]*)\}/)
    const border = block?.[1].match(/border:\s*1px solid var\(--([\w-]+)\)/)?.[1]
    expect(border).toBeTruthy()
    const [a, b] = [luminance(token(border!)), luminance(token('color-sheet'))]
    expect((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)).toBeGreaterThanOrEqual(3)
  })
})
