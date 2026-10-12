/**
 * Frazioni digitate → frazioni tipografiche: «8 1/8» diventa «8⅛».
 *
 * Il corpus e il modello scrivono le frazioni come un carattere solo; una
 * trascrizione che mescola «1/8» e «⅛» insegna due forme per la stessa cosa.
 * Dove il carattere non esiste (i ventesimi: «13 12/20», tonnellate e cwt)
 * la frazione si compone in apice/pedice: «13¹²⁄₂₀». Senza parte intera si
 * converte solo un glifo noto: «12/20» da solo, o una data «1/2/1909»,
 * restano come sono. I controlli aritmetici
 * (`backend/app/services/table_checks.py::number`) leggono tutte le forme.
 */
const GLYPHS: Record<string, string> = {
  '1/2': '½', '1/4': '¼', '3/4': '¾', '1/3': '⅓', '2/3': '⅔',
  '1/5': '⅕', '1/8': '⅛', '3/8': '⅜', '5/8': '⅝', '7/8': '⅞',
}

const SUP = '⁰¹²³⁴⁵⁶⁷⁸⁹'
const SUB = '₀₁₂₃₄₅₆₇₈₉'
const digits = (n: string, set: string) => n.replace(/\d/g, (d) => set[Number(d)])

export function toFractionGlyphs(text: string): string {
  return text.replace(
    /(?<![\d/])(?:(\d[\d,.]*)[  ]+)?(\d+)\/(\d+)(?![\d/])/g,
    (match, whole: string | undefined, num: string, den: string) => {
      const glyph = GLYPHS[`${num}/${den}`]
      if (glyph) return `${whole ?? ''}${glyph}`
      if (!whole || Number(den) === 0) return match
      return `${whole}${digits(num, SUP)}⁄${digits(den, SUB)}`
    },
  )
}
