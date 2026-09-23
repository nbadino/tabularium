"""API playground: analisi di una pagina con il modello servito via vLLM."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Literal

from pydantic import BaseModel

from ..db import connect
from ..services import inference as infmod
from ..services import labeling
from ..services import paddle_official
from ..services import pages as pagesvc
from ..services import auth as authsvc
from ..services.i18n import msg, parse_lang

router = APIRouter(
    tags=["playground"],
    dependencies=[Depends(authsvc.get_current_user)],
)

_TEXT_LABELS = {l.name for l in labeling.DEFAULT_LABELS if l.prompt_kind == "text"}


class ParseRequest(BaseModel):
    project_id: int
    page_id: int
    server_url: str | None = None
    model: str | None = None
    # `ocr` = motore OCR locale: righe di testo, senza modello servito. Serve
    # a provare una pagina anche quando nessun endpoint risponde.
    engine: Literal["model", "ocr"] = "model"


@router.post("/api/playground/parse")
def playground_parse(
    payload: ParseRequest,
    request: Request,
    user: dict = Depends(authsvc.get_current_user),
) -> dict:
    with connect() as conn:
        page = conn.execute("SELECT * FROM pages WHERE id=?", (payload.page_id,)).fetchone()
        project = conn.execute(
            "SELECT * FROM projects WHERE id=?", (payload.project_id,)
        ).fetchone()
    if page is None or project is None or page["project_id"] != payload.project_id:
        raise HTTPException(status_code=404, detail="pagina non trovata nel progetto")
    # project_id è nel body, non nel path: controllo manuale dell'accesso.
    authsvc.require_project_access(payload.project_id, user, write=False)

    image = pagesvc.load_source_image(page)
    if image is None:
        raise HTTPException(status_code=404, detail="immagine sorgente non disponibile")
    if payload.server_url is not None or payload.model is not None:
        raise HTTPException(status_code=400, detail="usa il profilo di inferenza approvato dall'amministratore")
    if payload.engine == "ocr":
        return _parse_with_ocr(page, image, request)
    client = infmod.get_vllm_client()

    # Il playground disegna il layout: non tutti i modelli lo sanno fare. Tre
    # percorsi possibili, in ordine di specificità:
    # 1. il percorso nativo «official» (PaddleOCR-VL: pipeline Paddle con
    #    PP-DocLayout + riconoscimento) — è quello che usa anche il prefill;
    # 2. il layout chiesto al modello (`two_stage`);
    # 3. la generazione end2end, che riporta bbox e contenuto in una volta.
    # Nessuno dei tre → si dice con un codice stabile, invece di incolpare
    # l'endpoint di una capacità che il modello non ha.
    from ..services.model_adapters import supported_prefill_modes
    from ..services.prefill import native_mode

    modes = supported_prefill_modes(client.adapter)
    try:
        native = native_mode(client.adapter)
    except ValueError:
        native = None
    use_official = native == "official"
    use_end2end = not use_official and not modes.get("supports_two_stage") and modes.get("supports_end2end")
    if not use_official and not use_end2end and not modes.get("supports_two_stage"):
        lang = parse_lang(request.headers.get("accept-language"))
        raise HTTPException(
            status_code=409,
            detail=msg("playground_needs_layout", lang, model=client.model or client.adapter.adapter_id),
        )

    w, h = page["width"], page["height"]
    try:
        if use_official:
            # `parse_page` restituisce i bbox in pixel dell'immagine: qui si
            # normalizzano come fa il prefill, così il resto del codice vede
            # sempre la stessa scala 0–1000.
            pred = paddle_official.parse_page(image, client.url, client.model, w, h)
            for it in pred:
                bbox = it.get("bbox") or []
                if len(bbox) == 4:
                    it["bbox"] = [
                        round(float(bbox[0]) / w * 1000),
                        round(float(bbox[1]) / h * 1000),
                        round(float(bbox[2]) / w * 1000),
                        round(float(bbox[3]) / h * 1000),
                    ]
        elif use_end2end:
            pred = client.end2end(image)
        else:
            pred = client.layout(image)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=f"vLLM non raggiungibile ({client.url}): {exc}",
        ) from exc

    items = []
    for it in pred:
        bbox = it.get("bbox") or []
        if len(bbox) != 4:
            continue
        label = it.get("label", "")
        bbox_px = [
            int(round(max(0.0, bbox[0]) / 1000 * w)),
            int(round(max(0.0, bbox[1]) / 1000 * h)),
            int(round(min(1000.0, bbox[2]) / 1000 * w)),
            int(round(min(1000.0, bbox[3]) / 1000 * h)),
        ]
        content = str(it.get("content") or "")
        if not content and (label in _TEXT_LABELS or label == "Table"):
            # Nel percorso end2end il contenuto è già nella risposta: rifare il
            # crop e una seconda chiamata al modello sarebbe lavoro doppio per
            # lo stesso testo.
            try:
                crop = pagesvc.crop_block_jpeg(page, bbox_px)
                if crop:
                    from io import BytesIO

                    from PIL import Image as PILImage

                    content = client.recognize(PILImage.open(BytesIO(crop)), label)
            except Exception:  # noqa: BLE001
                content = ""
        items.append(
            {"bbox_norm": [float(v) for v in bbox], "bbox_px": bbox_px, "label": label, "content": content}
        )
    items = [i for i in items if i["bbox_norm"] != [0.0, 0.0, 0.0, 0.0]]
    return {
        "ok": bool(items),
        "server": client.url,
        "model": client.model,
        "provider": client.provider,
        "width": w,
        "height": h,
        "items": items,
    }


def _parse_with_ocr(page, image, request: Request) -> dict:
    """La stessa risposta del percorso modello, fatta dall'OCR locale.

    L'OCR non conosce il layout: ogni riga è un blocco `Text`. È meno di
    quanto dia un modello, ma è ciò che la macchina sa fare senza endpoint,
    e dice subito se una pagina è leggibile.
    """
    from ..services import ocr as ocrmod

    engine = ocrmod.OcrEngine()
    if not engine.available:
        lang = parse_lang(request.headers.get("accept-language"))
        raise HTTPException(status_code=409, detail=msg("playground_ocr_unavailable", lang))
    w, h = page["width"], page["height"]
    sx = w / image.width if image.width else 1.0
    sy = h / image.height if image.height else 1.0
    items = []
    for row in engine.detect(image):
        x1, y1, x2, y2 = row["bbox"]
        bbox_px = [int(round(x1 * sx)), int(round(y1 * sy)), int(round(x2 * sx)), int(round(y2 * sy))]
        if bbox_px[2] <= bbox_px[0] or bbox_px[3] <= bbox_px[1]:
            continue
        items.append({
            "bbox_norm": [bbox_px[0] / w * 1000, bbox_px[1] / h * 1000, bbox_px[2] / w * 1000, bbox_px[3] / h * 1000],
            "bbox_px": bbox_px,
            "label": "Text",
            "content": row.get("text") or "",
        })
    return {
        "ok": bool(items),
        "server": None,
        "model": f"OCR · {engine.name}",
        "provider": "local",
        "width": w,
        "height": h,
        "items": items,
    }
