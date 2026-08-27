import { describe, expect, it, vi } from 'vitest'
import { LOCALES, getLocale, setLocale, t, tn } from './index'
import { it as itDict } from './it'

// Vitest gira in ambiente Node: forniamo solo il frammento DOM usato dal
// selettore di lingua, senza introdurre una dipendenza browser completa.
vi.stubGlobal('document', { documentElement: { lang: '' } })

/** Salva e ripristina la lingua attorno a ogni test. */
const withLocale = (lang: (typeof LOCALES)[number], fn: () => void) => {
  const prev = getLocale()
  setLocale(lang)
  try {
    fn()
  } finally {
    setLocale(prev)
  }
}

describe('i18n', () => {
  it('traduce chiavi semplici in tutte le lingue', () => {
    for (const l of LOCALES) {
      withLocale(l, () => {
        expect(t('nav.archive').length).toBeGreaterThan(0)
        expect(t('nav.projects').length).toBeGreaterThan(0)
        expect(t('common.chooseProject').length).toBeGreaterThan(0)
        expect(t('vocab.status.approved').length).toBeGreaterThan(0)
      })
    }
  })

  it('interpola i segnaposto {var}', () => {
    withLocale('it', () => {
      expect(t('home.progress', { pct: 42 })).toBe('Avanzamento 42%')
      expect(t('project.typeNameHint', { name: 'X' })).toContain('X')
      expect(t('errors.imageLoad', { url: 'u' })).toBe('impossibile caricare u')
    })
    withLocale('en', () => {
      expect(t('home.progress', { pct: 42 })).toBe('Progress 42%')
    })
  })

  it('restituisce la chiave se manca', () => {
    withLocale('it', () => {
      expect(t('xxx.none')).toBe('xxx.none')
    })
  })

  it('usa i plurali one/other correttamente', () => {
    for (const l of LOCALES) {
      withLocale(l, () => {
        const one = tn('common.pagesCount', 1)
        const many = tn('common.pagesCount', 3)
        expect(one).not.toBe(many)
        expect(one).toContain('1')
        expect(many).toContain('3')
      })
    }
    // italiano: 0 → plurale; francese: 0 → singolare
    withLocale('it', () => {
      expect(tn('common.pagesCount', 0)).toContain('0 pagine')
    })
    withLocale('fr', () => {
      expect(tn('common.pagesCount', 0)).toContain('0 page')
      expect(tn('common.pagesCount', 1)).toContain('1 page')
    })
  })

  it('risolve i nodi misti (plurale + sottochiavi) e le sottochiavi', () => {
    withLocale('it', () => {
      expect(tn('home.nextAnnotate', 1)).toBe('Annota 1 pagina')
      expect(tn('home.nextAnnotate', 4)).toBe('Annota 4 pagine')
      expect(t('home.nextAnnotateWhy').length).toBeGreaterThan(10)
      expect(t('home.nextAnnotateAction')).toBe('Apri lo studio')
    })
  })

  it('ogni chiave del dizionario italiano risolve in tutte le lingue', () => {
    // La struttura è forzata dai tipi (Dict); qui verifichiamo a runtime che
    // nessuna chiave resti non risolta per via di errori di shape o plurali.
    const walk = (node: Record<string, unknown>, prefix: string, onLeaf: (path: string) => void) => {
      for (const [k, v] of Object.entries(node)) {
        const path = prefix ? `${prefix}.${k}` : k
        if (typeof v === 'string') onLeaf(path)
        else if (v && typeof v === 'object' && 'one' in v && 'other' in v) onLeaf(path)
        else if (v && typeof v === 'object') walk(v as Record<string, unknown>, path, onLeaf)
      }
    }
    const paths: string[] = []
    walk(itDict, '', (p) => paths.push(p))
    expect(paths.length).toBeGreaterThan(400)
    for (const l of LOCALES) {
      withLocale(l, () => {
        for (const path of paths) {
          const value = t(path)
          expect(value, `chiave non risolta in ${l}: ${path}`).not.toBe(path)
        }
      })
    }
  })

  it('persiste la lingua e aggiorna document.lang', () => {
    // nel runner node senza DOM il `document` non esiste: skip implicito.
    withLocale('it', () => {
      setLocale('fr')
      expect(getLocale()).toBe('fr')
      if (typeof document !== 'undefined') {
        expect(document.documentElement.lang).toBe('fr')
      }
      setLocale('it')
      expect(getLocale()).toBe('it')
    })
  })
})
