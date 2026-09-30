/**
 * Frazioni digitate → frazioni tipografiche: «8 1/8» diventa «8⅛».
 *
 * Il corpus e il modello scrivono le frazioni come un carattere solo; una
 * trascrizione che mescola «1/8» e «⅛» insegna due forme per la stessa cosa.
 * Si convertono solo le frazioni che i controlli aritmetici conoscono
 * (`backend/app/services/table_checks.py::_FRACTIONS`): «13 12/20» o una data
 * «1/2/1909» restano come sono.
 */
const GLYPHS: Record<string, string> = {
  '1/2': '½', '1/4': '¼', '3/4': '¾', '1/3': '⅓', '2/3': '⅔',
  '1/5': '⅕', '1/8': '⅛', '3/8': '⅜', '5/8': '⅝', '7/8': '⅞',
}

export function toFractionGlyphs(text: string): string {
  return text.replace(
    /(?<![\d/])(?:(\d[\d,.]*)[  ]+)?(\d)\/(\d)(?![\d/])/g,
    (match, whole: string | undefined, num: string, den: string) => {
      const glyph = GLYPHS[`${num}/${den}`]
      return glyph ? `${whole ?? ''}${glyph}` : match
    },
  )
}
