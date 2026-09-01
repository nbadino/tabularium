/**
 * Guardia sull'API di Konva su cui poggia la selezione del canvas.
 *
 * `getType()` è il tipo di NODO, non la classe: per ogni forma vale 'Shape'.
 * Confrontarlo con 'Rect'/'Line' — come faceva `StudioCanvas.onMouseDown` — è
 * sempre falso, e il risultato era che premere una maniglia del transformer
 * (che è un `Rect`) deselezionava il blocco: le maniglie sparivano e il
 * ridimensionamento non partiva mai. Da lì la regola attuale, che non guarda
 * il tipo ma l'identità: si deseleziona solo se il bersaglio È lo stage.
 *
 * Le forme si leggono dai prototipi e non da istanze perché costruire una
 * `Konva.Shape` vuole un contesto canvas 2D, che jsdom non ha.
 */
import { describe, expect, it } from 'vitest'
import Konva from 'konva'

describe('tipi di nodo Konva', () => {
  it('getType() dice «Shape» per ogni forma, la classe la dice getClassName()', () => {
    // `getType()` ritorna `nodeType`, `getClassName()` ritorna `className`.
    expect(Konva.Rect.prototype.nodeType).toBe('Shape')
    expect(Konva.Line.prototype.nodeType).toBe('Shape')
    expect(Konva.Rect.prototype.className).toBe('Rect')
    expect(Konva.Line.prototype.className).toBe('Line')
    expect(Konva.Node.prototype.getType).toBeTypeOf('function')
    expect(Konva.Node.prototype.getClassName).toBeTypeOf('function')
  })

  it('solo lo stage ha nodeType «Stage»', () => {
    expect(Konva.Stage.prototype.nodeType).toBe('Stage')
    expect(Konva.Layer.prototype.nodeType).toBe('Layer')
    // Il transformer è un Group: le sue maniglie sono forme come le altre e
    // nessun confronto per classe può distinguerle da un blocco.
    expect(Konva.Transformer.prototype.className).toBe('Transformer')
  })
})
