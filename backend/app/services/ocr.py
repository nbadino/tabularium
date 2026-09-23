"""Motore OCR per pseudo-labeling.

Import lazy di RapidOCR (preferito: leggero, onnxruntime) o PaddleOCR.
Ritorna riquadri di testo in pixel immagine (= pixel pagina) con score.

Di RapidOCR esistono due pacchetti: `rapidocr` (3.x, l'attuale, unico che si
installi su Python 3.13) e `rapidocr_onnxruntime` (1.x, fermo a Python 3.12).
Le chiamate sono le stesse; cambia solo la forma del risultato — tuple nel
vecchio, un oggetto con `boxes`/`txts`/`scores` nel nuovo. Si usa quello che
l'ambiente ha, così un'installazione esistente continua a funzionare.
"""
from __future__ import annotations

import importlib.util

from PIL import Image

from .. import config


def available_engine() -> str | None:
    """Nome del motore OCR installato (o il forcing da config)."""
    forced = config.OCR_ENGINE.strip().lower()
    if forced in ("rapidocr", "paddleocr"):
        return forced
    for name, module in (("rapidocr", "rapidocr"), ("rapidocr", "rapidocr_onnxruntime"), ("paddleocr", "paddleocr")):
        try:
            # Non importare il runtime qui: questo endpoint viene interrogato
            # all'apertura di Annotation e PaddleOCR può inizializzare plugin
            # pesanti o bloccarsi su una installazione incompleta. Il runtime
            # viene importato solo da OcrEngine._ensure(), quando l'utente
            # avvia davvero il prefill.
            if importlib.util.find_spec(module) is None:
                continue
            return name
        except (ImportError, ModuleNotFoundError, ValueError):
            continue
    return None


def _rapid_rows(got) -> list[tuple]:
    """Righe `(box, testo, score)` dall'uscita di RapidOCR, 1.x o 3.x."""
    if hasattr(got, "txts"):  # 3.x: un oggetto con le tre liste parallele.
        # Su una pagina senza testo `boxes` è None, e l'uscita può essere
        # perfino di un altro tipo che i riquadri non ce li ha proprio: si
        # guarda l'attributo `txts`, non il valore di `boxes`, altrimenti una
        # pagina bianca finisce nel ramo del pacchetto vecchio.
        # `boxes` è un array numpy: niente `or` per il default, o si finisce
        # su «truth value of an array is ambiguous» appena il testo c'è.
        boxes = getattr(got, "boxes", None)
        return list(zip(() if boxes is None else boxes, got.txts or (), got.scores or ()))
    result = got[0] if isinstance(got, tuple) else got  # 1.x: (righe, tempi)
    return list(result or [])


class OcrEngine:
    def __init__(self, engine: str | None = None) -> None:
        self.name = engine or available_engine() or ""
        self._impl = None

    @property
    def available(self) -> bool:
        return bool(self.name)

    def _ensure(self) -> None:
        if self._impl is not None:
            return
        if self.name == "rapidocr":
            try:
                from rapidocr import RapidOCR
            except ImportError:
                from rapidocr_onnxruntime import RapidOCR

            self._impl = RapidOCR()
            return

        import inspect

        from paddleocr import PaddleOCR

        # PaddleOCR ha cambiato firma fra 2.x e 3.x: `use_angle_cls` è diventato
        # `use_textline_orientation` e `show_log` è stato rimosso del tutto. La
        # 3.x solleva ValueError sui parametri che non conosce, quindi passare
        # il set della 2.x fa fallire l'intero prefill. Si tengono solo i
        # parametri che la versione installata dichiara davvero.
        preferred = {
            "lang": "en",
            "use_textline_orientation": True,  # 3.x
            "use_angle_cls": True,  # 2.x
            "show_log": False,  # 2.x
        }
        accepted = set(inspect.signature(PaddleOCR.__init__).parameters)
        self._impl = PaddleOCR(**{k: v for k, v in preferred.items() if k in accepted})

    def detect(self, image: Image.Image) -> list[dict]:
        """Ritorna [{bbox:[x1,y1,x2,y2] px, text, score}], liste vuote se pulite."""
        self._ensure()
        import numpy as np

        arr = np.asarray(image.convert("RGB"))
        if self.name == "rapidocr":
            out = []
            for box, text, score in _rapid_rows(self._impl(arr)):
                xs = [float(p[0]) for p in box]
                ys = [float(p[1]) for p in box]
                out.append(
                    {
                        "bbox": [min(xs), min(ys), max(xs), max(ys)],
                        "text": text or "",
                        "score": float(score or 0),
                    }
                )
            return out

        # PaddleOCR 3.x: result è lista di pagine con dt_polys/rec_texts/rec_scores
        res = self._impl.predict(arr)
        out = []
        for page in res or []:
            polys = page.get("dt_polys") or []
            texts = page.get("rec_texts") or []
            scores = page.get("rec_scores") or []
            for poly, text, score in zip(polys, texts, scores):
                p = np.asarray(poly, dtype=float)
                xs, ys = p[:, 0], p[:, 1]
                out.append(
                    {
                        "bbox": [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())],
                        "text": text or "",
                        "score": float(score or 0),
                    }
                )
        return out

    def recognize_line(self, image: Image.Image) -> tuple[str, float]:
        """Legge un ritaglio che contiene *una sola* riga di testo già ritagliata.

        Serve per il riempimento cella-per-cella delle tabelle: lì il riquadro
        è già noto dalla griglia, quindi far girare anche il rilevatore è sia
        inutile sia dannoso (su ritagli stretti spesso non trova nulla e la
        cella torna vuota). Dove il motore lo permette si salta la detection.
        """
        self._ensure()
        import numpy as np

        arr = np.asarray(image.convert("RGB"))
        if self.name == "rapidocr":
            got = self._impl(arr, use_det=False, use_cls=False, use_rec=True)
            txts = getattr(got, "txts", None)
            if txts is not None:  # rapidocr 3.x: niente riquadri in sola lettura
                scores = getattr(got, "scores", None) or (0,)
                return (str(txts[0]).strip(), float(scores[0] or 0)) if txts else ("", 0.0)
            result = got[0] if isinstance(got, tuple) else got
            if not result:
                return "", 0.0
            first = result[0]
            # rapidocr 1.x resta su [box, testo, score] anche in sola recognition.
            text = first[1] if len(first) > 2 else first[0]
            score = first[2] if len(first) > 2 else first[1]
            return str(text or "").strip(), float(score or 0)

        # PaddleOCR non espone una modalità di sola recognition stabile fra le
        # versioni: si ripiega sul percorso completo e si concatena l'esito.
        detections = self.detect(image)
        if not detections:
            return "", 0.0
        detections.sort(key=lambda d: d["bbox"][0])
        text = " ".join(d["text"].strip() for d in detections if d["text"].strip())
        score = sum(d["score"] for d in detections) / len(detections)
        return text.strip(), float(score)
