"""API playground: analisi di una pagina con il modello servito via vLLM."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import connect
from ..services import inference as infmod
from ..services import labeling
from ..services import pages as pagesvc
from ..services import auth as authsvc

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


@router.post("/api/playground/parse")
def playground_parse(
    payload: ParseRequest,
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
    client = infmod.get_vllm_client()

    try:
        pred = client.layout(image)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=f"vLLM non raggiungibile ({client.url}): {exc}",
        ) from exc

    w, h = page["width"], page["height"]
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
        content = ""
        if label in _TEXT_LABELS or label == "Table":
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
        "width": w,
        "height": h,
        "items": items,
    }
