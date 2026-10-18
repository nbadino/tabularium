/** Mutazioni del modello persistito, da Univer 0.25.1. Selezione e scroll non serializzano il foglio. */
const DATA_MUTATIONS = new Set([
  'sheet.mutation.set-range-values', 'sheet.mutation.move-range', 'sheet.mutation.reorder-range',
  'sheet.mutation.add-worksheet-merge', 'sheet.mutation.remove-worksheet-merge',
  'sheet.mutation.insert-row', 'sheet.mutation.insert-col',
  'sheet.mutation.remove-rows', 'sheet.mutation.remove-col',
])
export function commandAffectsGrid(id: string): boolean { return DATA_MUTATIONS.has(id) }
