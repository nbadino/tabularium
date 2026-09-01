"""Allineamento e rettifica geometrica delle pagine.

La rettifica neurale è **una sola**: il preprocessore ufficiale di MonkeyOCRv2
(v. ``monkey_preprocess.py``), cioè lo stesso stadio che ``core_runner.py`` del
repo esegue su ogni pagina prima del parsing. I motori alternativi che erano
integrati qui (UVDoc via PaddleOCR, DocScanner-L) sono usciti: erano surrogati
scelti prima che quello del modello fosse raggiungibile, e tenerli in parallelo
significava poter preparare le pagine in un modo diverso da come il modello si
aspetta di riceverle. Il vecchio modello cubico ``page-dewarp`` era già stato
rimosso: sulle pagine multi-colonna interpretava il testo come geometria del
foglio e generava onde, crop o bande ai bordi.

Restano gli strumenti che non sono rettifiche automatiche alternative:
``deskew`` (sola rotazione, la ripiega sempre sicura) e le correzioni manuali
``perspective``/``mesh``, che applicano una geometria disegnata dall'utente.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from PIL import Image

LEVELS = ("basic", "medium", "high")


def describe_levels() -> list[dict]:
    """`medium` e `high` sono lo stesso motore: il preprocessore ufficiale non
    ha varianti di qualità, quindi i due livelli restano solo per compatibilità
    dell'API di allineamento."""
    return [
        {"id": "basic", "label": "deskew.levelBasic", "desc": "Solo rotazione (il più sicuro)"},
        {"id": "medium", "label": "deskew.levelMedium", "desc": "Preprocessore ufficiale MonkeyOCRv2"},
        {"id": "high", "label": "deskew.levelHigh", "desc": "Preprocessore ufficiale MonkeyOCRv2"},
    ]


_last_engine = "deskew"
_last_diagnostics: dict = {}
_transform_lock = threading.RLock()


def last_engine() -> str:
    """Motore usato dall'ultima richiesta (utile per feedback diagnostico)."""
    return _last_engine


def last_diagnostics() -> dict:
    """Dettagli dell'ultimo tentativo, senza nascondere il fallback."""
    return dict(_last_diagnostics)


@dataclass
class TransformResult:
    image: Image.Image
    requested_engine: str
    actual_engine: str
    angle: float = 0.0
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    diagnostics: dict = field(default_factory=dict)


def align_page(image: Image.Image, level: str = "medium", strength: float = 1.0) -> tuple[Image.Image, float]:
    """Allinea la pagina: `basic` ruota soltanto, `medium`/`high` applicano il
    preprocessore ufficiale.

    A differenza dei vecchi motori, il preprocessore non riceve una pagina già
    ruotata: `core_runner.py` gli passa l'originale e la rettifica include la
    rotazione, quindi un deskew prima o dopo sarebbe una deformazione in più.
    L'angolo restituito è perciò 0 quando il modello lavora, ed è quello del
    deskew solo quando si ripiega su di esso.
    """
    global _last_engine, _last_diagnostics
    from .images import deskew

    _last_diagnostics = {"requested_level": level}
    strength = max(0.0, min(float(strength), 2.0))
    if level in LEVELS and level != "basic" and strength > 0:
        from . import monkey_preprocess

        rectified = monkey_preprocess.preprocess(image)
        if rectified is not None:
            _last_engine = "monkeyocr"
            _last_diagnostics["device"] = monkey_preprocess.DEVICE
            return rectified, 0.0
        _last_diagnostics["monkeyocr_error"] = (
            monkey_preprocess.last_error() or "monkeyocr_preprocessor_unavailable"
        )
        _last_diagnostics["fallback"] = "deskew"

    # Ripiego sempre sicuro: la rotazione non introduce geometria nuova.
    aligned, angle = deskew(image)
    _last_engine = "deskew"
    _last_diagnostics["deskew_angle"] = angle
    return aligned, angle


def run_transform(
    image: Image.Image,
    engine: str,
    *,
    perspective_points: list[list[float]] | None = None,
    mesh_grid: list[list[list[float]]] | None = None,
) -> TransformResult:
    """Esegue un motore esplicito e dichiara ogni fallback o fallimento."""
    with _transform_lock:
        return _run_transform_locked(
            image,
            engine,
            perspective_points=perspective_points,
            mesh_grid=mesh_grid,
        )


def _run_transform_locked(
    image: Image.Image,
    engine: str,
    *,
    perspective_points: list[list[float]] | None = None,
    mesh_grid: list[list[list[float]]] | None = None,
) -> TransformResult:
    """Implementazione serializzata: la diagnostica dell'ultimo tentativo è
    globale, e il preprocessore ufficiale è un sottoprocesso solo."""
    global _last_engine, _last_diagnostics
    from .images import deskew, mesh_rectify, perspective_rectify

    _last_engine = engine
    _last_diagnostics = {"requested_engine": engine}
    if engine == "deskew":
        out, angle = deskew(image)
        _last_engine = "deskew"
        _last_diagnostics["deskew_angle"] = angle
        return TransformResult(out, engine, "deskew", angle=angle, diagnostics=last_diagnostics())
    if engine == "perspective":
        try:
            out = perspective_rectify(image, perspective_points or [])
            return TransformResult(out, engine, "perspective", diagnostics=last_diagnostics())
        except ValueError as exc:
            _last_diagnostics["perspective_error"] = str(exc)
            return TransformResult(image, engine, "none", error=str(exc), diagnostics=last_diagnostics())
    if engine == "mesh":
        try:
            out = mesh_rectify(image, mesh_grid or [])
            return TransformResult(out, engine, "mesh", diagnostics=last_diagnostics())
        except ValueError as exc:
            _last_diagnostics["mesh_error"] = str(exc)
            return TransformResult(image, engine, "none", error=str(exc), diagnostics=last_diagnostics())
    if engine == "monkeyocr":
        # Percorso ufficiale MonkeyOCRv2: `core_runner.py` passa la pagina al
        # `Preprocessor` così com'è, senza deskew prima né dopo, e ne usa
        # l'uscita per tutti gli stadi successivi. Qui non applichiamo nemmeno
        # il guard anti-crop: il preprocessore restituisce una tela della stessa
        # dimensione dell'ingresso, quindi non c'è ritaglio da sorvegliare.
        from . import monkey_preprocess

        candidate = monkey_preprocess.preprocess(image)
        if candidate is None:
            error = monkey_preprocess.last_error() or "monkeyocr_preprocessor_unavailable"
            _last_diagnostics["monkeyocr_error"] = error
            aligned, angle = deskew(image)
            _last_engine = "deskew"
            _last_diagnostics["fallback"] = "deskew"
            return TransformResult(
                aligned,
                engine,
                "deskew",
                angle=angle,
                warnings=["neural_fallback_deskew"],
                error=error,
                diagnostics=last_diagnostics(),
            )
        _last_engine = engine
        _last_diagnostics["device"] = monkey_preprocess.DEVICE
        return TransformResult(candidate, engine, engine, diagnostics=last_diagnostics())

    return TransformResult(image, engine, "none", error="transform_engine_invalid", diagnostics=last_diagnostics())
