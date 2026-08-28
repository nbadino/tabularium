"""Rilevamento della struttura di tabelle *senza filetti* (whitespace-aligned).

I registri navali Historic Shipping Index non hanno righe di riquadro: le colonne sono tenute
insieme dalla composizione tipografica (allineamenti costanti) e i campi sono
collegati da puntini di guida. I modelli di struttura tabellare addestrati su
tabelle moderne riquadrate (PubTabNet & simili) qui rendono male, mentre la
geometria della pagina è estremamente regolare e si sfrutta direttamente.

## Perché le righe vengono dai glifi e non dal profilo

La versione precedente ricavava il passo tipografico dall'autocorrelazione del
profilo di inchiostro sull'asse y e da lì le bande di testo. Misurato sulle
quattro scansioni reali in `test/`, quel percorso funzionava su **una pagina su
quattro**, e le tre diagnosi sono distinte:

| Pagina | Passo stimato | Passo vero | Rottura |
|---|---|---|---|
| `LSI_17186_015` | 40 px | 40 px | — |
| `LSI_8447_014` | 156 px | 39 px | autocorrelazione agganciata a un'**armonica** (4×) |
| `LSI_1974_039` | 117 px | 38 px | interlinea stretta: il profilo **non torna mai a zero** fra le righe |
| `LSIVS_11652_006` | 10 px | 39 px | inclinazione −1,45°: su 3500 px la riga **si spalma** per 90 px |

Nessuna delle tre si ripara ritoccando soglie: il profilo orizzontale è una
somma su tutta la larghezza, quindi qualunque cosa spalmi o saldi le righe lo
distrugge prima che la soglia possa vederle.

Le **componenti connesse** non hanno questo difetto: un glifo resta un oggetto
separato anche quando la sua banda si sovrappone a quella della riga vicina, e
porta con sé la propria posizione. Da lì:

1. **Linee di base** — istogramma dei *fondi* delle componenti. Il fondo è molto
   più stabile del centro, che dipende da ascendenti e discendenti; i picchi
   dell'istogramma sono le linee di base, e la loro distanza mediana è il passo.
   Niente autocorrelazione, quindi niente armoniche.
2. **Inclinazione** — cercata come lo **scorrimento** (`tan θ`) che rende più
   netto quell'istogramma. L'immagine **non viene ruotata**: la correzione si
   applica alle coordinate delle componenti, così i confini restituiti restano
   nel sistema di riferimento del ritaglio originale e nessuno deve saperlo.
3. **Gutter verticali** — colonne di pagina prive di inchiostro su tutte le
   righe: prova forte ma incompleta (i puntini di guida le riempiono).
4. **Bordi di parola ricorrenti** — istogramma dei bordi sinistri e destri delle
   parole su tutte le righe. Una colonna allineata a sinistra produce un picco
   di bordi sinistri, una allineata a destra un picco di bordi destri. Un
   confine di colonna sta fra un picco destro e il picco sinistro successivo.

I puntini di guida vengono soppressi prima del punto 4: sono inchiostro, quindi
saldano fra loro parole che appartengono a colonne diverse e cancellano proprio
i confini che servono.

Ogni confine esce con un **supporto** (su quante righe è attestato): il rilevatore
non inventa struttura, propone e dichiara quanto è sicuro. I confini che la
geometria non può provare — due colonne adiacenti senza gutter né allineamento
costante — restano all'annotatore, che li aggiunge trascinando.

`warnings` porta solo ciò che il rilevatore sa **misurare**. Oggi è
`skewed`: oltre ~0,2° di inclinazione un confine di riga orizzontale non può
più seguire la riga di testo, e la pagina va raddrizzata prima di annotarla.

Non c'è invece un avviso «questa non è una tabella ma una pagina a più colonne».
Le due ipotesi verificabili sono state misurate su `LSIVS_11652_006`, che è un
supplemento a cinque colonne di giornale, e **nessuna delle due funziona**: il
gutter di pagina più largo lì è 0,62 passi contro 1,07 passi del gutter *interno*
più largo di `LSI_17186_015`, che è una tabella sola; e le linee di base non si
disallineano, perché le cinque colonne sono composte sulla stessa griglia
tipografica (residuo mediano 0,125 passi, in mezzo a quello delle due pagine a
tabella singola). Un avviso che scatta sulle pagine sbagliate è peggio di nessun
avviso: insegna a ignorare gli avvisi. Il caso resta risolto dal flusso, dove è
l'utente a disegnare il blocco `Table` e su una pagina simile disegnerebbe cinque
blocchi `Column`.

Coordinate in uscita: `vlines`/`hlines` normalizzate 0–1 sul ritaglio, cioè la
convenzione già usata da `TableGrid` (`cols+1` e `rows+1` valori crescenti).

Dipendenze: solo `numpy` e `Pillow`, come il resto del backend. Le componenti
connesse sono implementate qui (`_components`) a run + union-find; l'esito è
verificato bit a bit contro `cv2.connectedComponentsWithStats` nei test.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image

# Soglie geometriche. Espresse in frazioni dell'altezza di riga dove possibile,
# così restano valide a risoluzioni di scansione diverse.
_MIN_XHEIGHT = 3           # altezza minima credibile per un glifo (px)
_MAX_SHEAR = 0.045         # ricerca inclinazione: ±0.045 ≈ ±2.6°
_SHEAR_REPORT = 0.0035     # oltre ~0.2° l'inclinazione va dichiarata
_ASCENDER = 0.72           # da linea di base a cima della riga, in passi
_DESCENDER = 0.26          # da linea di base a fondo della riga, in passi
_PEAK_SEPARATION = 0.62    # distanza minima fra due linee di base, in passi
_FUNDAMENTAL_FRAC = 0.60   # forza minima di un picco perché valga come fondamentale
_WORD_GAP_FRAC = 0.35      # gap fra parole della stessa cella, frazione del passo
_DOT_W_FRAC = 0.20         # larghezza massima di un puntino, frazione del passo
_DOT_H_FRAC = 0.20         # altezza massima di un puntino, frazione del passo
_PEAK_TOL_FRAC = 0.15      # tolleranza di clustering dei bordi, frazione del passo
_SNAP_SEARCH = 1.5         # quanto lontano cercare il varco di una riga, in passi
_SNAP_DISTANCE_COST = 0.5  # quanto costa allontanarsi dal prior, in px di varco
_MIN_GAP_PX = 3            # larghezza minima di un tratto di bianco credibile


@dataclass
class GridDetection:
    """Esito del rilevamento, con la diagnostica che lo rende verificabile."""

    rows: int
    cols: int
    vlines: list[float]
    hlines: list[float]
    column_support: list[int] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "cols": self.cols,
            "vlines": self.vlines,
            "hlines": self.hlines,
            "column_support": self.column_support,
            "diagnostics": self.diagnostics,
            "warnings": self.warnings,
        }


# --------------------------------------------------------------------------
# Binarizzazione
# --------------------------------------------------------------------------

def _otsu(gray: np.ndarray) -> int:
    """Soglia di Otsu. Una soglia fissa a 128 sbaglia sulle scansioni sbiadite.

    Sul corpus corrente Otsu cade fra 142 e 152, cioè sempre *sopra* 128: con la
    soglia fissa si perdeva sistematicamente l'inchiostro più chiaro dei tratti
    sottili, che è esattamente quello che tiene insieme un glifo.
    """
    hist = np.bincount(gray.reshape(-1), minlength=256).astype(float)
    total = hist.sum()
    if total <= 0:
        return 128
    levels = np.arange(256, dtype=float)
    w0 = np.cumsum(hist)
    w1 = total - w0
    valid = (w0 > 0) & (w1 > 0)
    if not valid.any():
        return 128
    sum0 = np.cumsum(hist * levels)
    total_sum = sum0[-1]
    mu0 = np.divide(sum0, w0, out=np.zeros_like(sum0), where=w0 > 0)
    mu1 = np.divide(total_sum - sum0, w1, out=np.zeros_like(sum0), where=w1 > 0)
    between = w0 * w1 * (mu0 - mu1) ** 2
    between[~valid] = -1.0
    return int(np.argmax(between))


# --------------------------------------------------------------------------
# Componenti connesse (8-connesse) a run + union-find, solo numpy
# --------------------------------------------------------------------------

def _components(ink: np.ndarray) -> np.ndarray:
    """Riquadri delle componenti connesse: colonne `[x0, x1, y0, y1, area]`.

    Estremi in convenzione semiaperta (`x1`/`y1` esclusi), come `cv2`.
    Scorre le righe una volta sola unendo i tratti che si sovrappongono a quelli
    della riga precedente; il costo è lineare nei tratti, non nei pixel.
    """
    height, width = ink.shape
    parent: list[int] = []

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    previous: list[tuple[int, int, int]] = []
    runs: list[tuple[int, int, int, int]] = []  # (y, x0, x1, label)

    for y in range(height):
        row = ink[y]
        if not row.any():
            previous = []
            continue
        delta = np.diff(row.astype(np.int8))
        starts = np.flatnonzero(delta == 1) + 1
        ends = np.flatnonzero(delta == -1) + 1
        if row[0]:
            starts = np.concatenate(([0], starts))
        if row[-1]:
            ends = np.concatenate((ends, [width]))

        current: list[tuple[int, int, int]] = []
        cursor = 0
        for x0, x1 in zip(starts, ends):
            label = -1
            # I tratti precedenti sono ordinati: si scorre in avanti una volta.
            while cursor < len(previous) and previous[cursor][1] < x0:
                cursor += 1
            probe = cursor
            while probe < len(previous) and previous[probe][0] <= x1:
                if label < 0:
                    label = previous[probe][2]
                else:
                    union(label, previous[probe][2])
                probe += 1
            if label < 0:
                label = len(parent)
                parent.append(label)
            current.append((int(x0), int(x1), label))
            runs.append((y, int(x0), int(x1), label))
        previous = current

    if not runs:
        return np.zeros((0, 5), dtype=np.int64)

    roots = np.array([find(run[3]) for run in runs])
    ys = np.array([run[0] for run in runs])
    xs0 = np.array([run[1] for run in runs])
    xs1 = np.array([run[2] for run in runs])
    _, index = np.unique(roots, return_inverse=True)

    count = int(index.max()) + 1
    big = np.iinfo(np.int64).max
    out = np.zeros((count, 5), dtype=np.int64)
    out[:, 0] = big
    out[:, 2] = big
    np.minimum.at(out[:, 0], index, xs0)
    np.maximum.at(out[:, 1], index, xs1)
    np.minimum.at(out[:, 2], index, ys)
    np.maximum.at(out[:, 3], index, ys + 1)
    np.add.at(out[:, 4], index, xs1 - xs0)
    return out


def _glyphs(comps: np.ndarray, height: int, width: int) -> np.ndarray:
    """Componenti plausibilmente *glifi*: via lo sporco, i filetti, le macchie."""
    if len(comps) == 0:
        return comps
    w = comps[:, 1] - comps[:, 0]
    h = comps[:, 3] - comps[:, 2]
    keep = (
        (comps[:, 4] >= 6)              # area: sotto è granello di scansione
        & (h >= _MIN_XHEIGHT)
        & (h <= max(4, height // 10))   # sopra è un filetto o una macchia
        & (w <= max(4, width // 4))     # una parola intera non è un glifo
    )
    return comps[keep]


def _glyph_height(glyphs: np.ndarray) -> float:
    """Altezza del glifo dominante: mediana delle altezze **pesata sull'area**.

    Senza il peso vincono i puntini di guida e la punteggiatura, che sono i più
    numerosi ma i più piccoli, e l'altezza di riga collassa a 3–4 px.
    """
    if len(glyphs) == 0:
        return float(_MIN_XHEIGHT)
    h = (glyphs[:, 3] - glyphs[:, 2]).astype(float)
    area = glyphs[:, 4].astype(float)
    order = np.argsort(h)
    h, area = h[order], area[order]
    cumulative = np.cumsum(area)
    if cumulative[-1] <= 0:
        return float(np.median(h))
    return float(h[int(np.searchsorted(cumulative, cumulative[-1] / 2.0))])


# --------------------------------------------------------------------------
# Inclinazione e linee di base
# --------------------------------------------------------------------------

def _baseline_histogram(
    bottoms: np.ndarray, centres: np.ndarray, shear: float, height: int, x_mid: float
) -> np.ndarray:
    """Istogramma dei fondi dei glifi, corretto dello scorrimento `shear`."""
    corrected = bottoms - (centres - x_mid) * shear
    idx = np.clip(np.rint(corrected).astype(int), 0, height - 1)
    return np.bincount(idx, minlength=height).astype(float)


def _estimate_shear(
    glyphs: np.ndarray, height: int, width: int, smooth: int
) -> float:
    """Scorrimento `tan θ` che rende più *netto* l'istogramma delle linee di base.

    Una pagina dritta concentra i fondi su poche righe; una inclinata li spalma.
    L'energia dell'istogramma (somma dei quadrati) misura esattamente questa
    concentrazione, quindi il massimo è la correzione giusta.
    """
    if len(glyphs) < 20:
        return 0.0
    bottoms = glyphs[:, 3].astype(float)
    centres = (glyphs[:, 0] + glyphs[:, 1]) / 2.0
    x_mid = width / 2.0
    kernel = np.ones(2 * smooth + 1)

    def sharpness(shear: float) -> float:
        hist = _baseline_histogram(bottoms, centres, shear, height, x_mid)
        return float(np.sum(np.convolve(hist, kernel, mode="same") ** 2))

    # Griglia grossa poi affinamento: la funzione ha un massimo netto e non
    # servono più di due passate.
    coarse = np.linspace(-_MAX_SHEAR, _MAX_SHEAR, 25)
    best = max(coarse, key=sharpness)
    fine = np.linspace(best - _MAX_SHEAR / 12, best + _MAX_SHEAR / 12, 13)
    return float(max(fine, key=sharpness))


def _pick_peaks(hist: np.ndarray, smooth: int, separation: int) -> list[int]:
    """Massimi dell'istogramma, dal più forte in giù, distanti almeno `separation`."""
    dense = np.convolve(hist, np.ones(2 * smooth + 1), mode="same")
    positive = dense[dense > 0]
    if positive.size == 0:
        return []
    # Soglia relativa al 95° percentile: una riga di testo vera raccoglie molti
    # fondi, il rumore di scansione qualcuno sparso.
    threshold = max(3.0, 0.12 * float(np.percentile(positive, 95)))

    peaks: list[int] = []
    taken = np.zeros(len(dense), dtype=bool)
    for i in np.argsort(dense)[::-1]:
        if dense[i] < threshold:
            break
        lo = max(0, int(i) - separation)
        if taken[lo : int(i) + separation + 1].any():
            continue
        peaks.append(int(i))
        taken[int(i)] = True
    peaks.sort()
    return peaks


def _pitch(hist: np.ndarray, glyph_height: float) -> int:
    """Passo tipografico: **fondamentale** dell'istogramma delle linee di base.

    L'autocorrelazione qui è affidabile dove sul profilo di inchiostro non lo
    era, perché l'istogramma dei fondi è un treno di impulsi e non una somma
    spalmata: le linee di base sono già isolate quando ci arriva.

    Resta il problema delle armoniche — un treno di periodo `P` correla anche a
    `2P`, `3P`, `4P` — e il massimo globale può cadere su una di quelle: era
    esattamente la rottura di `LSI_8447_014`, dove il passo veniva stimato 156 px
    invece di 39. Si prende quindi il **primo** massimo locale che raggiunge una
    frazione del migliore, cioè la fondamentale, non il massimo assoluto.
    """
    centred = hist - hist.mean()
    if not np.any(centred):
        return max(_MIN_XHEIGHT, int(round(glyph_height * 1.6)))
    ac = np.correlate(centred, centred, mode="full")[len(centred) - 1 :]
    if ac[0] <= 0:
        return max(_MIN_XHEIGHT, int(round(glyph_height * 1.6)))
    ac = ac / ac[0]

    lo = max(4, int(round(glyph_height * 0.6)))
    hi = min(int(round(glyph_height * 6)), len(ac) - 1)
    if hi <= lo + 2:
        return max(_MIN_XHEIGHT, int(round(glyph_height * 1.6)))
    window = ac[lo:hi]
    best = float(window.max())
    for i in range(1, len(window) - 1):
        if (
            window[i] >= _FUNDAMENTAL_FRAC * best
            and window[i] >= window[i - 1]
            and window[i] >= window[i + 1]
        ):
            return lo + i
    return lo + int(np.argmax(window))


def _baselines(hist: np.ndarray, glyph_height: float) -> tuple[list[int], int]:
    """Linee di base e passo tipografico.

    La distanza minima fra due linee di base è il **passo**, non l'altezza del
    glifo: legarla all'altezza sbaglia in entrambi i versi, perché il rapporto
    fra corpo e interlinea cambia da un'annata all'altra. Sul corpus corrente
    l'altezza dominante è 27–30 px con un passo di 39–40 px, quindi una
    separazione di `1,1 × altezza` cancella una riga su sei, e una di
    `0,55 × altezza` sdoppia ogni riga sui discendenti.
    """
    smoothed = np.convolve(hist, np.ones(5), mode="same")
    pitch = _pitch(smoothed, glyph_height)
    peaks = _pick_peaks(
        hist,
        max(1, int(round(pitch * 0.22))),
        max(3, int(round(pitch * _PEAK_SEPARATION))),
    )
    if len(peaks) < 3:
        return peaks, pitch
    # Il passo misurato sulle linee trovate è la verifica della stima: se le due
    # divergono molto, sono le linee a comandare, perché sono ciò che si vede.
    return peaks, max(_MIN_XHEIGHT, int(round(float(np.median(np.diff(peaks))))))


def _row_boundaries(peaks: list[int], pitch: int, height: int) -> list[int]:
    """Confini di riga: nel bianco fra il discendente di una e l'ascendente della successiva.

    Le estensioni sono frazioni del **passo** e non dell'altezza del glifo: il
    passo è ciò che separa davvero due linee di base, ed è l'unica misura che
    resta corretta quando corpo e interlinea non sono in proporzione fissa.
    """
    descender = pitch * _DESCENDER
    ascender = pitch * _ASCENDER
    bounds = [max(0, int(round(peaks[0] - ascender)))]
    for upper, lower in zip(peaks, peaks[1:]):
        lo = upper + descender
        hi = lower - ascender
        middle = (lo + hi) / 2.0 if hi > lo else (upper + lower) / 2.0
        bounds.append(int(round(min(max(middle, upper + 1), lower - 1))))
    bounds.append(min(height, int(round(peaks[-1] + descender)) + 1))
    return bounds


# --------------------------------------------------------------------------
# Colonne
# --------------------------------------------------------------------------

def _runs(mask: np.ndarray, min_len: int = 1) -> list[tuple[int, int]]:
    """Estremi [start, end) dei tratti consecutivi True lunghi almeno min_len."""
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, value in enumerate(mask):
        if value and start is None:
            start = i
        elif not value and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    if start is not None and len(mask) - start >= min_len:
        out.append((start, len(mask)))
    return out


def _shear_row_profile(
    ink: np.ndarray, start: int, end: int, shear: float, y_mid: float
) -> np.ndarray:
    """Profilo x di una banda, riportato al sistema raddrizzato per traslazione."""
    profile = ink[start:end].sum(axis=0).astype(float)
    if not shear:
        return profile
    offset = int(round(((start + end) / 2.0 - y_mid) * shear))
    if not offset:
        return profile
    profile = np.roll(profile, offset)
    if offset > 0:
        profile[:offset] = 0
    else:
        profile[offset:] = 0
    return profile


def _shear_profiles(
    ink: np.ndarray, bands: list[tuple[int, int]], shear: float
) -> tuple[np.ndarray, np.ndarray]:
    """Profilo x cumulato e conteggio delle righe occupate, entrambi raddrizzati.

    Il primo dice quanto inchiostro c'è a ogni x, il secondo su quante righe ce
    n'è: un gutter di *pagina* è una x dove il secondo è zero quasi ovunque, e
    quella è una domanda diversa da «quanto inchiostro».
    """
    width = ink.shape[1]
    y_mid = ink.shape[0] / 2.0
    total = np.zeros(width)
    occupied = np.zeros(width)
    for start, end in bands:
        profile = _shear_row_profile(ink, start, end, shear, y_mid)
        total += profile
        occupied += profile > 0
    return total, occupied


def _suppress_leaders(row_profile: np.ndarray, pitch: int) -> tuple[np.ndarray, int]:
    """Azzera i puntini di guida (e lo sporco) dal profilo x di una riga.

    Un puntino è un tratto stretto E basso: le lettere, anche strette come «i»,
    sono alte quanto la riga. Ritorna il profilo ripulito e quanti ne ha tolti.
    """
    cleaned = row_profile.copy()
    max_w = max(3, int(_DOT_W_FRAC * pitch))
    max_h = max(3, int(_DOT_H_FRAC * pitch))
    removed = 0
    for start, end in _runs(cleaned > 0, 1):
        if end - start <= max_w and cleaned[start:end].max() <= max_h:
            cleaned[start:end] = 0
            removed += 1
    return cleaned, removed


def _word_edges(
    ink: np.ndarray,
    bands: list[tuple[int, int]],
    pitch: int,
    suppress_leaders: bool,
    shear: float,
    y_mid: float,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Istogrammi dei bordi sinistri e destri delle parole su tutte le righe.

    Lo scorrimento si compensa **traslando il profilo di ogni riga** invece di
    ruotare l'immagine: la rotazione costerebbe un'interpolazione su tutto il
    ritaglio e sposterebbe i confini in un sistema di riferimento diverso da
    quello in cui vanno restituiti. Su una pagina inclinata di 1,5° i bordi di
    una colonna larga 3500 px si spalmano di 90 px e nessun picco sopravvive.
    """
    width = ink.shape[1]
    left = np.zeros(width)
    right = np.zeros(width)
    gap = max(4, int(_WORD_GAP_FRAC * pitch))
    dots = 0

    for start, end in bands:
        profile = _shear_row_profile(ink, start, end, shear, y_mid)
        if suppress_leaders:
            profile, removed = _suppress_leaders(profile, pitch)
            dots += removed
        merged: list[list[int]] = []
        for word_start, word_end in _runs(profile > 0, 2):
            if merged and word_start - merged[-1][1] < gap:
                merged[-1][1] = word_end
            else:
                merged.append([word_start, word_end])
        for word_start, word_end in merged:
            left[word_start] += 1
            right[min(word_end, width - 1)] += 1

    return left, right, dots


def _peaks(
    hist: np.ndarray, lo: int, hi: int, tol: int, min_count: float
) -> list[tuple[int, float]]:
    """Massimi locali dell'istogramma, dopo smoothing su ±tol."""
    smooth = np.convolve(hist, np.ones(2 * tol + 1), mode="same")
    out: list[tuple[int, float]] = []
    for x in range(lo, hi):
        value = smooth[x]
        if value < min_count:
            continue
        if value != smooth[max(0, x - tol) : x + tol + 1].max():
            continue
        if not out or x - out[-1][0] > 2 * tol:
            out.append((x, float(value)))
        elif value > out[-1][1]:
            out[-1] = (x, float(value))
    return out


def _column_bounds(
    ink: np.ndarray,
    bands: list[tuple[int, int]],
    pitch: int,
    min_support: float,
    suppress_leaders: bool,
    shear: float = 0.0,
) -> tuple[list[int], list[int], dict[str, Any]]:
    """Confini interni di colonna (px) con il rispettivo supporto."""
    n_rows = len(bands)
    # Profilo x sommato **riga per riga con la traslazione dello scorrimento**:
    # sommare l'immagine intera darebbe lo stesso smearing che il rilevatore sta
    # cercando di annullare, e i gutter sparirebbero proprio dove servono.
    xprof, _ = _shear_profiles(ink, bands, shear)
    noise = max(1.0, 0.01 * n_rows)

    # Il bordo del contenuto va cercato con una soglia alta: una colonna vera ha
    # inchiostro su molte righe, mentre i margini della scansione hanno sporco
    # sparso che con la sola soglia di rumore verrebbe scambiato per contenuto.
    solid = _runs(xprof > max(2.0, 0.05 * n_rows), max(5, pitch // 3))
    if not solid:
        return [], [], {"reason": "nessun contenuto"}
    x_min, x_max = solid[0][0], solid[-1][1]

    gutters = [
        (start + end) // 2
        for start, end in _runs(xprof <= noise, max(6, pitch // 4))
        if x_min < (start + end) // 2 < x_max
    ]

    left, right, dots = _word_edges(
        ink, bands, pitch, suppress_leaders, shear, ink.shape[0] / 2.0
    )
    tol = max(3, int(_PEAK_TOL_FRAC * pitch))
    need = min_support * n_rows
    left_peaks = _peaks(left, x_min, x_max, tol, need)
    right_peaks = _peaks(right, x_min, x_max, tol, need)

    events = sorted(
        [(x, "L", v) for x, v in left_peaks] + [(x, "R", v) for x, v in right_peaks]
    )
    bounds: list[tuple[int, int]] = []
    for (x1, kind1, v1), (x2, kind2, v2) in zip(events, events[1:]):
        # Una colonna finisce (bordi destri allineati) e la successiva comincia
        # (bordi sinistri allineati): il confine sta in mezzo.
        if kind1 == "R" and kind2 == "L" and x2 - x1 >= max(6, pitch // 6):
            bounds.append(((x1 + x2) // 2, int(min(v1, v2))))

    # Un gutter bianco vale più di un allineamento: è inchiostro assente su tutte
    # le righe, non una ricorrenza statistica. Entra come candidato a supporto
    # pieno e, in caso di collisione, è lui a vincere sulla posizione.
    candidates = [(x, s, False) for x, s in bounds]
    candidates += [(g, n_rows, True) for g in gutters]
    candidates.sort()

    merged: list[tuple[int, int, bool]] = []
    for x, s, is_gutter in candidates:
        # Due confini più vicini di ~3/4 di riga sono lo stesso confine visto da
        # segnali diversi: nessuna colonna di testo può essere più stretta di così
        # (un singolo carattere occupa già circa mezza altezza di riga).
        if merged and x - merged[-1][0] <= max(2 * tol, int(0.75 * pitch)):
            prev_x, prev_s, prev_gutter = merged[-1]
            # Stesso confine visto da due segnali: tieni la posizione più
            # affidabile e il supporto più alto, senza sdoppiare la colonna.
            keep_x = x if (is_gutter and not prev_gutter) else prev_x
            merged[-1] = (keep_x, max(prev_s, s), prev_gutter or is_gutter)
        else:
            merged.append((x, s, is_gutter))

    xs = [x for x, _, _ in merged]
    support = [s for _, s, _ in merged]
    diagnostics = {
        "content_x": [int(x_min), int(x_max)],
        "gutters": [int(g) for g in gutters],
        "leader_dots_suppressed": int(dots),
        "left_peaks": [[int(x), int(v)] for x, v in left_peaks],
        "right_peaks": [[int(x), int(v)] for x, v in right_peaks],
    }
    return xs, support, diagnostics


def _row_gaps(
    ink: np.ndarray,
    band: tuple[int, int],
    pitch: int,
    shear: float,
    y_mid: float,
    suppress_leaders: bool,
) -> list[tuple[int, int]]:
    """Varchi di bianco di una riga: `(centro, larghezza)`, nel sistema raddrizzato.

    Deliberatamente **non** si raggruppano prima le parole. La soglia di fusione
    (`_WORD_GAP_FRAC`, 0,35 passi ≈ 14 px) serve a decidere cosa è una parola,
    ma un varco fra due colonne di questi registri è largo una quindicina di
    pixel: usarla qui cancella proprio il varco che si sta cercando, prima di
    cercarlo. Si prendono quindi i tratti di bianco grezzi e sarà il punteggio
    a preferire quelli larghi.
    """
    profile = _shear_row_profile(ink, band[0], band[1], shear, y_mid)
    if suppress_leaders:
        profile, _ = _suppress_leaders(profile, pitch)
    return [
        ((start + end) // 2, end - start)
        for start, end in _runs(profile <= 0, _MIN_GAP_PX)
    ]


def snap_boundaries(
    ink: np.ndarray,
    bands: list[tuple[int, int]],
    vlines_px: list[float],
    pitch: int,
    shear: float,
    *,
    search: float = _SNAP_SEARCH,
    suppress_leaders: bool = True,
) -> tuple[list[list[int]], list[list[bool]]]:
    """Porta ogni confine, riga per riga, nel **varco di bianco** più adatto.

    Una colonna di questi registri non sta ferma: composta a mano e ripresa da
    una pagina curva, deriva scendendo. Misurato sul campione, ogni confine
    interno si sposta fra 25 e 77 px dall'alto al basso della pagina, cioè fra
    0,6 e 2,0 passi tipografici. Una cifra è larga una ventina di pixel: un
    taglio *dritto* alla mediana finisce dentro un numero su molte righe, e il
    valore si spezza fra due celle.

    Il rimedio non è una linea più precisa — nessuna retta può seguire una
    deriva — ma un confine che **piega**. Il confine dritto resta il prior, e
    su ogni riga si sposta nel varco migliore entro `search` passi, dove
    «migliore» pesa la larghezza contro la distanza dal prior: il varco fra due
    colonne è largo, quello fra due lettere no, e prendere semplicemente il più
    vicino finirebbe spesso dentro una parola.

    Ritorna, per ogni banda, i confini in pixel e un flag per confine che dice
    se un varco è stato davvero trovato. Dove non c'è (due colonne che su
    quella riga si toccano) il prior resta, e il flag lo dichiara `False`
    invece di far finta che sia una misura.
    """
    width = ink.shape[1]
    y_mid = ink.shape[0] / 2.0
    reach = max(pitch, int(round(search * pitch)))

    all_bounds: list[list[int]] = []
    all_found: list[list[bool]] = []
    for band in bands:
        gaps = _row_gaps(ink, band, pitch, shear, y_mid, suppress_leaders)

        bounds: list[int] = []
        found: list[bool] = []
        previous = 0
        for i in range(1, len(vlines_px) - 1):
            prior = int(round(vlines_px[i]))
            candidates = [
                (centre, gap_width)
                for centre, gap_width in gaps
                if abs(centre - prior) <= reach and centre > previous
            ]
            if candidates:
                chosen = max(
                    candidates,
                    key=lambda g: g[1] - _SNAP_DISTANCE_COST * abs(g[0] - prior),
                )[0]
                found.append(True)
            else:
                chosen = max(prior, previous + 1)
                found.append(False)
            chosen = min(width - 1, max(previous + 1, chosen))
            bounds.append(chosen)
            previous = chosen
        all_bounds.append(bounds)
        all_found.append(found)
    return all_bounds, all_found


# --------------------------------------------------------------------------
# Ingresso pubblico
# --------------------------------------------------------------------------

def detect_grid(
    image: Image.Image,
    *,
    min_support: float = 0.22,
    suppress_leaders: bool = True,
) -> GridDetection:
    """Rileva righe e colonne nel ritaglio di una tabella senza filetti.

    `min_support` è la frazione di righe su cui un allineamento deve ricorrere
    perché valga come confine di colonna: alzarlo dà meno colonne ma più solide.
    """
    gray = np.asarray(image.convert("L"))
    if gray.ndim != 2 or gray.size == 0:
        raise ValueError("immagine non valida per il rilevamento griglia")
    threshold = _otsu(gray)
    # `<=`: Otsu restituisce il livello *incluso* nella classe scura, la stessa
    # convenzione di `cv2.threshold(..., THRESH_OTSU)`. Con `<` un'immagine a due
    # soli livelli (0 e 255, cioè ogni test sintetico) perde tutto l'inchiostro.
    ink = gray <= threshold
    height, width = ink.shape

    glyphs = _glyphs(_components(ink), height, width)
    if len(glyphs) == 0:
        raise ValueError("nessun glifo rilevato nel ritaglio")
    glyph_height = _glyph_height(glyphs)

    smooth = max(1, int(round(glyph_height * 0.30)))
    shear = _estimate_shear(glyphs, height, width, smooth)
    hist = _baseline_histogram(
        glyphs[:, 3].astype(float),
        (glyphs[:, 0] + glyphs[:, 1]) / 2.0,
        shear,
        height,
        width / 2.0,
    )
    peaks, pitch = _baselines(hist, glyph_height)
    if not peaks:
        raise ValueError("nessuna riga di testo rilevata nel ritaglio")

    hlines_px = _row_boundaries(peaks, pitch, height)
    bands = [
        (max(0, lo), min(height, hi))
        for lo, hi in zip(hlines_px, hlines_px[1:])
        if hi > lo
    ]

    vbounds, support, diagnostics = _column_bounds(
        ink, bands, pitch, min_support, suppress_leaders, shear
    )
    x_min, x_max = diagnostics.get("content_x", [0, width])
    vlines_px = [max(0, x_min - pitch // 6), *vbounds, min(width, x_max + pitch // 6)]
    # I bordi esterni non sono "attestati": marcali con il numero di righe piene.
    column_support = [len(bands), *support, len(bands)]

    # Confini piegati: dove passa davvero il taglio su ciascuna riga. Servono a
    # disegnarli e a riempire le celle senza spezzare valori; non sono ancora
    # persistiti (il grid salvato porta solo le rette), quindi vivono nella
    # diagnostica della bozza.
    if len(vlines_px) > 2:
        row_bounds, row_proven = snap_boundaries(ink, bands, vlines_px, pitch, shear)
    else:
        row_bounds, row_proven = [], []
    unproven = sum(1 for flags in row_proven for f in flags if not f)

    warnings: list[str] = []
    if abs(shear) > _SHEAR_REPORT:
        warnings.append("skewed")
    # Nessun avviso «la colonna deriva»: deriva su tutte e quattro le pagine del
    # campione (dal 7% al 41% di tagli senza varco), quindi scatterebbe sempre e
    # si imparerebbe a ignorarlo — lo stesso motivo per cui non c'è
    # `multi_column`. La deriva non è una condizione della scansione, è una
    # proprietà per cella: `row_columns_unproven` la conta e `fill_cells` marca
    # le singole celle, così l'annotatore va dove serve invece di leggere un
    # allarme che vale per ogni pagina.

    diagnostics.update(
        {
            "pitch_px": int(pitch),
            "glyph_height_px": round(float(glyph_height), 1),
            "row_bands": len(bands),
            "glyphs": int(len(glyphs)),
            "otsu": int(threshold),
            "shear": round(float(shear), 5),
            "row_columns": [
                [round(x / width, 6) for x in bounds] for bounds in row_bounds
            ],
            "row_columns_proven": [list(flags) for flags in row_proven],
            "row_columns_unproven": int(unproven),
            "skew_deg": round(float(np.degrees(np.arctan(shear))), 3),
            "image_size": [int(width), int(height)],
        }
    )

    return GridDetection(
        rows=len(bands),
        cols=len(vlines_px) - 1,
        vlines=[round(x / width, 6) for x in vlines_px],
        hlines=[round(y / height, 6) for y in hlines_px],
        column_support=column_support,
        diagnostics=diagnostics,
        warnings=warnings,
    )


def empty_cells(rows: int, cols: int) -> list[dict[str, Any]]:
    """Celle 1x1 vuote che coprono l'intera griglia."""
    return [
        {"r": r, "c": c, "rowspan": 1, "colspan": 1, "text": ""}
        for r in range(rows)
        for c in range(cols)
    ]


# Il ritaglio di una cella va allargato e ingrandito prima di darlo al motore
# OCR: i riconoscitori sono addestrati su righe di testo con un po' d'aria
# attorno e a una altezza tipica, e su celle strette («Da», «57») senza questo
# trattamento tornano quasi sempre vuoti.
_CELL_PAD_FRAC = 0.25      # padding attorno alla cella, frazione del passo
_CELL_TARGET_H = 2.5       # altezza a cui portare il ritaglio, in passi


def fill_cells(
    image: Image.Image,
    vlines: list[float],
    hlines: list[float],
    engine: Any,
    *,
    pitch: int,
    min_score: float = 0.0,
    skip_blank: bool = True,
    shear: float = 0.0,
    snap: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Riempie le celle con l'OCR, **assegnando i valori** invece di tagliare a x fisso.

    È il punto in cui l'OCR di riga smette di essere controproducente: il
    riquadro arriva dalla griglia, quindi il motore non può più fondere colonne
    diverse in una stringa sola e ogni errore resta confinato alla sua cella.

    Ma il riquadro non può essere un rettangolo. Una colonna di questi registri
    deriva orizzontalmente scendendo — misurato: da 25 a 77 px, cioè fino a due
    passi tipografici — e una cifra è larga una ventina di pixel, quindi un
    taglio dritto finisce dentro un numero e ne manda metà nella cella accanto.
    Con `snap` il confine si sposta, riga per riga, nel varco fra parole più
    vicino: sul campione, dove un varco esiste (86% dei tagli) **nessuno dei
    1486 tagli spezza un valore**; dove non esiste il confine dritto resta e la
    cella viene contata fra le `uncertain`, perché è lì che va guardata.

    Le celle senza inchiostro non vengono nemmeno passate al motore: sono celle
    vuote legittime (`ecel` in OTSL) e interrogarle produrrebbe solo rumore.
    """
    gray = np.asarray(image.convert("L"))
    ink = gray <= _otsu(gray)
    height, width = ink.shape
    rows, cols = len(hlines) - 1, len(vlines) - 1
    pad = max(2, int(_CELL_PAD_FRAC * pitch))
    target_h = max(24, int(_CELL_TARGET_H * pitch))

    hlines_px = [round(v * height) for v in hlines]
    bands = [(a, b) for a, b in zip(hlines_px, hlines_px[1:])]
    vlines_px = [v * width for v in vlines]
    if snap and cols > 1 and bands:
        row_bounds, row_proven = snap_boundaries(
            ink, bands, vlines_px, pitch, shear
        )
    else:
        straight = [int(round(v)) for v in vlines_px[1:-1]]
        row_bounds = [straight for _ in bands]
        row_proven = [[True] * len(straight) for _ in bands]

    cells: list[dict[str, Any]] = []
    filled = blank = low = uncertain = 0
    scores: list[float] = []

    for r in range(rows):
        y0, y1 = hlines_px[r], hlines_px[r + 1]
        edges = [0, *row_bounds[r], width] if r < len(row_bounds) else [
            int(round(v)) for v in vlines_px
        ]
        # I bordi esterni restano quelli globali: delimitano il contenuto, non
        # separano due colonne, quindi non c'è varco da cercare.
        edges[0] = int(round(vlines_px[0]))
        edges[-1] = int(round(vlines_px[-1]))
        proven = row_proven[r] if r < len(row_proven) else [True] * max(0, cols - 1)

        for c in range(cols):
            x0, x1 = edges[c], edges[c + 1]
            # Una cella è incerta se uno dei suoi due confini interni non è
            # stato provato da un varco su questa riga.
            sure = (c == 0 or proven[c - 1]) and (c == cols - 1 or proven[c])
            text, score = "", 0.0
            if x1 - x0 >= 2 and y1 - y0 >= 2:
                has_ink = bool(ink[y0:y1, x0:x1].any())
                if not has_ink and skip_blank:
                    blank += 1
                else:
                    crop = image.crop(
                        (
                            max(0, x0 - pad),
                            max(0, y0 - pad),
                            min(width, x1 + pad),
                            min(height, y1 + pad),
                        )
                    )
                    if crop.width >= 2 and crop.height >= 2:
                        scale = target_h / crop.height
                        crop = crop.resize(
                            (max(8, int(crop.width * scale)), target_h), Image.LANCZOS
                        )
                        text, score = engine.recognize_line(crop)
                    if score < min_score:
                        low += 1
                        text = ""
                    elif text:
                        filled += 1
                        scores.append(score)
                        if not sure:
                            uncertain += 1
            cell: dict[str, Any] = {
                "r": r,
                "c": c,
                "rowspan": 1,
                "colspan": 1,
                "text": text,
            }
            if text and not sure:
                # Il confine di questa cella non è provato: va guardato.
                cell["uncertain"] = True
            cells.append(cell)

    stats = {
        "cells": rows * cols,
        "filled": filled,
        "blank": blank,
        "below_threshold": low,
        "uncertain": uncertain,
        "snapped": bool(snap and cols > 1),
        "mean_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
    }
    return cells, stats
