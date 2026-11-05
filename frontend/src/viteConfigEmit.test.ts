import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

// `tsc -b` builds the composite node project. Without an outDir it wrote
// vite.config.js next to vite.config.ts, and Vite/Vitest load the .js first:
// an edit to vite.config.ts was silently ignored until the next `tsc -b`.
describe('tsconfig.node.json', () => {
  it('emits outside the folder where Vite looks for its config', () => {
    const config = JSON.parse(readFileSync('tsconfig.node.json', 'utf8'))
    const outDir: string | undefined = config.compilerOptions?.outDir
    expect(outDir).toBeTruthy()
    expect(outDir!.replace(/^\.\//, '')).toMatch(/^node_modules\//)
  })
})
