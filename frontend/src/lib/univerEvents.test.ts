import { expect, it } from 'vitest'
import { commandAffectsGrid } from './univerEvents'
it('selezione, scroll e formattazione non ricostruiscono il modello di annotazione', () => {
  for (const id of ['sheet.operation.set-selections', 'sheet.operation.scroll', 'sheet.command.set-scroll-relative', 'sheet.mutation.set-row-height', 'sheet.command.undo']) expect(commandAffectsGrid(id)).toBe(false)
})
it('le mutazioni emesse anche da undo, incolla e merge continuano a persistere', () => {
  for (const id of ['sheet.mutation.set-range-values', 'sheet.mutation.move-range', 'sheet.mutation.add-worksheet-merge', 'sheet.mutation.remove-worksheet-merge', 'sheet.mutation.insert-row', 'sheet.mutation.remove-rows']) expect(commandAffectsGrid(id)).toBe(true)
})
