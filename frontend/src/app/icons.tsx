/**
 * Il sistema di segni.
 *
 * Disegnati, non presi in prestito da Unicode: una sola famiglia di tratto —
 * griglia 16, tratto 1.5, terminali e giunzioni squadrate — così i segni
 * appartengono allo stesso sistema dei filetti da 1px della pagina.
 *
 * Le icone sono sempre decorative: ogni controllo porta il proprio nome
 * scritto o un `aria-label`. Nessuna azione è affidata al solo segno.
 */
import type { SVGProps } from 'react'

interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'children'> {
  /** Dimensione in px del lato; default 14, la misura dei controlli densi. */
  size?: number
}

function Icon({
  size = 14,
  children,
  ...rest
}: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="square"
      strokeLinejoin="miter"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  )
}

/* --- navigazione ---------------------------------------------------------- */

/** Archivio: la pila di fogli registrati. */
export const IconArchive = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2 4h12v3H2z" />
    <path d="M3 7v6h10V7" />
    <path d="M6.5 9.5h3" />
  </Icon>
)

/** Progetti: cartelle in registro. */
export const IconProjects = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2 3.5h4.5L8 5.5h6v7H2z" />
    <path d="M2 8.5h12" />
  </Icon>
)

/** Annotazione: il pennino che traccia il blocco. */
export const IconAnnotate = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2 14v-3L10.5 2.5 13.5 5.5 5 14z" />
    <path d="M9 4l3 3" />
  </Icon>
)

/** Dataset: le righe JSONL impilate. */
export const IconDataset = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2 3h12v10H2z" />
    <path d="M2 6.3h12M2 9.6h12M6 3v10" />
  </Icon>
)

/** Training: il chip con le sue piste. */
export const IconTraining = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4.5 4.5h7v7h-7z" />
    <path d="M6.5 1.5v3M9.5 1.5v3M6.5 11.5v3M9.5 11.5v3M1.5 6.5h3M1.5 9.5h3M11.5 6.5h3M11.5 9.5h3" />
  </Icon>
)

/** Valutazione: la misura contro il bersaglio. */
export const IconEvaluate = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2 13.5h12" />
    <path d="M3.5 13.5v-4M7 13.5v-8M10.5 13.5v-6M14 13.5v-11" />
  </Icon>
)

/** Playground: la prova che parte. */
export const IconPlayground = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3 2.5l10 5.5-10 5.5z" />
  </Icon>
)

/* --- azioni --------------------------------------------------------------- */

export const IconUndo = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2.5 7.5h8a3 3 0 0 1 0 6H7" />
    <path d="M5.5 4.5l-3 3 3 3" />
  </Icon>
)

export const IconRedo = (p: IconProps) => (
  <Icon {...p}>
    <path d="M13.5 7.5h-8a3 3 0 0 0 0 6H9" />
    <path d="M10.5 4.5l3 3-3 3" />
  </Icon>
)

export const IconClose = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" />
  </Icon>
)

export const IconUp = (p: IconProps) => (
  <Icon {...p}>
    <path d="M8 13V3M3.5 7.5L8 3l4.5 4.5" />
  </Icon>
)

export const IconDown = (p: IconProps) => (
  <Icon {...p}>
    <path d="M8 3v10M3.5 8.5L8 13l4.5-4.5" />
  </Icon>
)

export const IconPrev = (p: IconProps) => (
  <Icon {...p}>
    <path d="M13 8H3M7.5 3.5L3 8l4.5 4.5" />
  </Icon>
)

export const IconNext = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3 8h10M8.5 3.5L13 8l-4.5 4.5" />
  </Icon>
)

export const IconTrash = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2.5 4h11" />
    <path d="M6 4V2.5h4V4" />
    <path d="M3.5 4l.7 9.5h7.6L12.5 4" />
    <path d="M6.5 6.5v5M9.5 6.5v5" />
  </Icon>
)

/** Tabella: la griglia con la cella unita. */
export const IconTable = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2 3h12v10H2z" />
    <path d="M2 6.3h12M2 9.6h12M6 3v10M10 6.3v6.7" />
  </Icon>
)

/** Prefill OCR: il segno generato dalla macchina, non dalla mano. */
export const IconPrefill = (p: IconProps) => (
  <Icon {...p}>
    <path d="M8 1.5l1.7 4.8L14.5 8l-4.8 1.7L8 14.5l-1.7-4.8L1.5 8l4.8-1.7z" />
  </Icon>
)

/** Flusso di lettura: la sequenza fra i blocchi. */
export const IconFlow = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2 4.5h6.5a2.5 2.5 0 0 1 0 5H5" />
    <path d="M7.5 7l-2.5 2.5L7.5 12" />
    <path d="M11.5 2.5l2.5 2-2.5 2" />
  </Icon>
)

export const IconPlus = (p: IconProps) => (
  <Icon {...p}>
    <path d="M8 3v10M3 8h10" />
  </Icon>
)

export const IconMinus = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3 8h10" />
  </Icon>
)

export const IconCheck = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2.5 8.5l3.5 3.5 7.5-8" />
  </Icon>
)

/** Chevron: apre e chiude una fisarmonica. */
export const IconChevron = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 6l4 4 4-4" />
  </Icon>
)

/** Allarme: il canale degli errori, distinto per forma e non solo per colore. */
export const IconAlert = (p: IconProps) => (
  <Icon {...p}>
    <path d="M8 1.5L15 14H1z" />
    <path d="M8 6v4" />
    <path d="M8 11.8v.5" />
  </Icon>
)

/** Avviso: la stessa famiglia, segno diverso. */
export const IconWarn = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2 2h12v12H2z" />
    <path d="M8 4.5v4.5" />
    <path d="M8 11v.5" />
  </Icon>
)

/** Scansione dell'archivio: la lente sul foglio. */
export const IconScan = (p: IconProps) => (
  <Icon {...p}>
    <path d="M1.5 4V1.5H4M12 1.5h2.5V4M14.5 12v2.5H12M4 14.5H1.5V12" />
    <path d="M4.5 8h7" />
  </Icon>
)

/** Salvataggio esplicito. */
export const IconSave = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2.5 2.5h9L13.5 5v8.5h-11z" />
    <path d="M5 2.5v4h5v-4" />
    <path d="M5 13.5v-4h6v4" />
  </Icon>
)

/** Copia negli appunti. */
export const IconCopy = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5.5 5.5h8v8h-8z" />
    <path d="M10.5 5.5v-3h-8v8h3" />
  </Icon>
)

/** Ambiente / macchina locale. */
export const IconEnv = (p: IconProps) => (
  <Icon {...p}>
    <path d="M1.5 3.5h13v5h-13z" />
    <path d="M1.5 9.5h13v3h-13z" />
    <path d="M3.5 6h2M3.5 11h2" />
  </Icon>
)

/** Cloud / Offloading remoto. */
export const IconCloud = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4.5 13.5h7a3 3 0 00.5-5.96 4 4 0 00-7.78-1.04A3 3 0 004.5 13.5z" />
  </Icon>
)

