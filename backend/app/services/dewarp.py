"""Allineamento e rettifica geometrica delle pagine.

Il vecchio modello cubico ``page-dewarp`` è stato rimosso: sulle pagine
multi-colonna può interpretare il testo come geometria del foglio e generare
onde, crop o bande ai bordi. ``medium`` e ``high`` usano UVDoc quando il
runtime opzionale PaddleOCR è installato; altrimenti ritornano il solo deskew,
che è sempre un'operazione conservativa.
"""
from __future__ import annotations

import tempfile
import os
from pathlib import Path

from PIL import Image

LEVELS = ("basic", "medium", "high")


def describe_levels() -> list[dict]:
    return [
        {"id": "basic", "label": "deskew.levelBasic", "desc": "Solo rotazione (il più sicuro)"},
        {"id": "medium", "label": "deskew.levelMedium", "desc": "UVDoc con controllo anti-crop"},
        {"id": "high", "label": "deskew.levelHigh", "desc": "UVDoc HQ con controllo anti-crop"},
    ]


_uvdoc_model = None
_last_engine = "deskew"


def last_engine() -> str:
    """Motore usato dall'ultima richiesta (utile per feedback diagnostico)."""
    return _last_engine


def _looks_clipped(source: Image.Image, candidate: Image.Image) -> bool:
    """Rileva quando il modello ha spinto contenuto contro un bordo.

    Il solo rapporto d'aspetto non basta: UVDoc può restituire una pagina con
    lo stesso rapporto ma già tagliata. Confrontiamo l'inchiostro nelle fasce
    esterne dopo un resize diagnostico uniforme alla tela sorgente.
    """
    import numpy as np

    size = source.size
    src = np.asarray(source.convert("L").resize(size, Image.Resampling.BILINEAR))
    dst = np.asarray(candidate.convert("L").resize(size, Image.Resampling.BILINEAR))
    threshold = 180
    band_x = max(4, size[0] // 50)
    band_y = max(4, size[1] // 50)
    bands = (
        (src[:band_y, :], dst[:band_y, :]),
        (src[-band_y:, :], dst[-band_y:, :]),
        (src[:, :band_x], dst[:, :band_x]),
        (src[:, -band_x:], dst[:, -band_x:]),
    )
    for source_band, candidate_band in bands:
        source_ink = float((source_band < threshold).mean())
        candidate_ink = float((candidate_band < threshold).mean())
        if source_ink < 0.10 and candidate_ink > max(0.16, source_ink + 0.06):
            return True

    # Una pagina ritagliata tende inoltre ad avere l'inchiostro a contatto
    # con un bordo che nella scansione aveva un margine bianco. Questo
    # intercetta anche titoli sottili, che il confronto delle sole densità
    # può non vedere.
    def margins(arr):
        ink = arr < threshold
        ys, xs = np.where(ink)
        if not len(xs):
            return (1.0, 1.0, 1.0, 1.0)
        return (
            float(xs.min() / arr.shape[1]),
            float((arr.shape[1] - 1 - xs.max()) / arr.shape[1]),
            float(ys.min() / arr.shape[0]),
            float((arr.shape[0] - 1 - ys.max()) / arr.shape[0]),
        )

    source_margins = margins(src)
    candidate_margins = margins(dst)
    for source_margin, candidate_margin in zip(source_margins, candidate_margins):
        if source_margin > 0.005 and candidate_margin < source_margin * 0.25:
            return True
    return False


def _fit_without_crop(candidate: Image.Image, source_size: tuple[int, int], source: Image.Image | None = None) -> Image.Image | None:
    """Riporta l'output alla tela originale senza stretch o crop."""
    source_ratio = source_size[0] / source_size[1]
    candidate_ratio = candidate.width / candidate.height
    if abs(candidate_ratio - source_ratio) / source_ratio > 0.10:
        return None
    if source is not None and _looks_clipped(source, candidate):
        return None
    fitted = Image.new("RGB", source_size, "white")
    candidate = candidate.convert("RGB")
    candidate.thumbnail(source_size, Image.Resampling.LANCZOS)
    fitted.paste(candidate, ((source_size[0] - candidate.width) // 2, (source_size[1] - candidate.height) // 2))
    return fitted


def _fit_model_output(candidate: Image.Image, image: Image.Image) -> Image.Image | None:
    """Fit output made from a padded input without shrinking the page."""
    source_ratio = image.width / image.height
    candidate_ratio = candidate.width / candidate.height
    if abs(candidate_ratio - source_ratio) / source_ratio > 0.10:
        return None
    if candidate_ratio > source_ratio:
        wanted = max(1, round(candidate.height * source_ratio))
        left = (candidate.width - wanted) // 2
        candidate = candidate.crop((left, 0, left + wanted, candidate.height))
    elif candidate_ratio < source_ratio:
        wanted = max(1, round(candidate.width / source_ratio))
        top = (candidate.height - wanted) // 2
        candidate = candidate.crop((0, top, candidate.width, top + wanted))
    return _fit_without_crop(candidate, image.size, source=image)


def _uvdoc_dewarp(image: Image.Image) -> Image.Image | None:
    """Esegue UVDoc via PaddleOCR 3.x, se il runtime opzionale è presente."""
    global _uvdoc_model
    try:
        from paddleocr import TextImageUnwarping  # noqa: PLC0415
    except ImportError:
        return None

    tmp = Path(tempfile.mkdtemp(prefix="uvdoc_"))
    try:
        if _uvdoc_model is None:
            device = os.environ.get("LLOYDS_UVDOC_DEVICE", "cpu")
            _uvdoc_model = TextImageUnwarping(model_name="UVDoc", device=device)
        # UVDoc tende a usare il bordo della foto come bordo della pagina.
        # Una cornice bianca evita che la curvatura venga risolta tagliando il
        # contenuto; la cornice viene usata solo come area di sicurezza e
        # rimossa geometricamente prima del fit finale sulla tela originale.
        source = image.convert("RGB")
        pad = max(32, int(min(source.size) * 0.08))
        padded = Image.new("RGB", (source.width + 2 * pad, source.height + 2 * pad), "white")
        padded.paste(source, (pad, pad))
        inp = tmp / "input.png"
        padded.save(inp, format="PNG")
        results = _uvdoc_model.predict(str(inp), batch_size=1)
        result = next(iter(results), None)
        if result is None:
            return None
        visual = getattr(result, "img", None)
        if isinstance(visual, dict):
            visual = visual.get("doctr_img")
            if visual is None:
                visual = getattr(result, "img", {}).get("img")
            if visual is None:
                # Nelle release PaddleOCR 3.7 il risultato dell'unwarping è
                # esposto come result.img["res"]: usa l'array originale, senza
                # passare dal writer JPEG/PNG e perdere dettaglio.
                visual = getattr(result, "img", {}).get("res")
        # PaddleX espone ``img`` come dizionario, ma il valore può essere una
        # struttura interna diversa tra release. Il writer ufficiale è il
        # contratto stabile dell'API, quindi usalo come seconda via.
        if visual is None:
            result.save_to_img(save_path=str(tmp))
            generated = [p for p in tmp.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"} and p != inp]
            if not generated:
                return None
            visual = generated[0]
        import numpy as np
        if isinstance(visual, Image.Image):
            candidate = visual
        elif isinstance(visual, (str, Path)):
            candidate = Image.open(visual).convert("RGB")
        else:
            candidate = Image.fromarray(np.asarray(visual).astype("uint8"))
        # L'input passato a UVDoc contiene una cornice bianca: il modello la
        # conserva nella tela di output, ma la sua proporzione non coincide
        # con quella della scansione originale. Se la lasciassimo nel fit,
        # Pillow dovrebbe ridurre tutta la pagina per farla entrare e
        # introdurrebbe margini bianchi visibili. Rimuoviamo quindi soltanto
        # l'eccedenza geometrica della cornice, in modo simmetrico; il bordo
        # reale della pagina resta intatto.
        return _fit_model_output(candidate, image)
    except Exception:  # noqa: BLE001
        return None
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def align_page(image: Image.Image, level: str = "medium", strength: float = 1.0) -> tuple[Image.Image, float]:
    """Allinea la pagina; medium/high usano UVDoc solo se disponibile e valido."""
    global _last_engine
    from .images import deskew

    aligned, angle = deskew(image)
    _last_engine = "deskew"
    strength = max(0.0, min(float(strength), 2.0))
    if level not in LEVELS or level == "basic" or strength <= 0:
        return aligned, angle

    out = None
    # DocScanner è integrato ma, essendo addestrato su fotografie/documenti
    # deformati diversi da queste scansioni d'archivio, resta opt-in finché
    # non viene validato sul dataset corrente. UVDoc è il default riproducibile.
    if level == "high" and os.environ.get("LLOYDS_DOCSCANNER_ENABLE", "0").lower() in {"1", "true", "yes"}:
        try:
            from . import docscanner
            candidate = docscanner.rectify(aligned)
            if candidate is not None:
                out = _fit_model_output(candidate, aligned)
        except Exception:  # noqa: BLE001
            out = None
        if out is not None:
            _last_engine = "docscanner"
    if out is None:
        out = _uvdoc_dewarp(aligned)
    if out is not None:
        # UVDoc può reintrodurre una piccola rotazione residua. Questo secondo
        # passaggio è solo rotazionale: non aggiunge una nuova deformazione.
        out, residual_angle = deskew(out)
        if _last_engine != "docscanner":
            _last_engine = "uvdoc"
        return out, angle + residual_angle
    # Fallback sicuro se il modello non trova una geometria affidabile.
    return aligned, angle
