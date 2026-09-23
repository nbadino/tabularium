"""L'adattatore RapidOCR parla con due pacchetti diversi.

`rapidocr` 3.x (l'unico installabile su Python 3.13) restituisce un oggetto
con `boxes`/`txts`/`scores`; `rapidocr_onnxruntime` 1.x restituisce
`(righe, tempi)` con righe `[box, testo, score]`. Qui si sostituisce il
motore con due finti della forma giusta: il test non scarica modelli e non
dipende da quale pacchetto sia installato.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

from app.services.ocr import OcrEngine

# Come il 3.x li consegna davvero: un array numpy, non una lista. Con una
# lista il test passerebbe anche con un `or` al posto del confronto con None,
# e in produzione salterebbe fuori «truth value of an array is ambiguous».
BOX = np.array([[10.0, 25.0], [128.0, 25.0], [128.0, 43.0], [10.0, 43.0]])


@dataclass
class _NewOutput:
    boxes: np.ndarray | None
    txts: tuple
    scores: tuple


class _New:
    """rapidocr 3.x: oggetto con liste parallele; in sola lettura niente box."""

    def __call__(self, _arr, use_det=None, use_cls=None, use_rec=None):
        if use_det is False:
            return _RecOutput(txts=("258,403",), scores=(0.93,))
        return _NewOutput(boxes=np.array([BOX]), txts=("139,545",), scores=(0.97,))


@dataclass
class _RecOutput:
    txts: tuple
    scores: tuple


class _Old:
    """rapidocr 1.x: `(righe, tempi)`, righe `[box, testo, score]`."""

    def __call__(self, _arr, use_det=None, use_cls=None, use_rec=None):
        if use_det is False:
            return ([[BOX.tolist(), "258,403", 0.93]], 0.1)
        return ([[BOX.tolist(), "139,545", 0.97]], 0.1)


def _engine(impl) -> OcrEngine:
    engine = OcrEngine(engine="rapidocr")
    engine._impl = impl  # noqa: SLF001 — sostituisce il motore, non lo inizializza
    return engine


def test_detect_reads_both_shapes():
    for impl in (_New(), _Old()):
        got = _engine(impl).detect(Image.new("RGB", (200, 60), "white"))
        assert got == [{"bbox": [10.0, 25.0, 128.0, 43.0], "text": "139,545", "score": 0.97}]


def test_recognize_line_reads_both_shapes():
    for impl in (_New(), _Old()):
        assert _engine(impl).recognize_line(Image.new("RGB", (120, 40), "white")) == ("258,403", 0.93)


def test_an_empty_read_is_empty_not_an_error():
    class _Nothing:
        """Senza testo il 3.x torna `boxes=None` — e talvolta un'uscita di sola
        lettura, che i riquadri non li ha proprio."""

        def __call__(self, _arr, use_det=None, use_cls=None, use_rec=None):
            return _RecOutput(txts=(), scores=())

    engine = _engine(_Nothing())
    assert engine.detect(Image.new("RGB", (40, 20), "white")) == []
    assert engine.recognize_line(Image.new("RGB", (40, 20), "white")) == ("", 0.0)
