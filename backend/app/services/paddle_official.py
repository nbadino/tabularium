"""Bridge al pipeline ufficiale PaddleOCRVL.

Il modulo viene importato solo nel worker del runtime Paddle: il backend
principale non deve avere PaddlePaddle/PaddleX tra le dipendenze core.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from .. import config
from . import paddle_runtime


_RUNNER = r'''
import json, sys
from paddleocr import PaddleOCRVL

image, url, model, out_dir = sys.argv[1:]
pipeline = PaddleOCRVL(
    vl_rec_backend="vllm-server",
    vl_rec_server_url=url,
    vl_rec_api_model_name=model,
    use_layout_detection=True,
)
result = next(iter(pipeline.predict(image)))
path = result.save_to_json(save_path=out_dir)
print(json.dumps({"path": str(path) if path else ""}))
'''


def _find_json(root: Path, hinted: str) -> Path:
    if hinted:
        path = Path(hinted)
        if path.exists():
            return path
    candidates = sorted(root.rglob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError("PaddleOCRVL non ha prodotto il JSON ufficiale")
    return candidates[0]


def _items(value) -> list[dict]:
    """Estrae i blocchi dalle varianti JSON delle release PaddleX."""
    found: list[dict] = []
    if isinstance(value, dict):
        bbox = value.get("bbox") or value.get("block_bbox") or value.get("coordinate")
        label = value.get("label") or value.get("block_label") or value.get("type")
        content = value.get("content") or value.get("block_content") or value.get("text") or ""
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4 and label:
            # PaddleX uses lowercase semantic labels; Tabularium stores the
            # public labels used by the dataset builder.
            labels = {
                "table": "Table",
                "text": "Text",
                "paragraph_title": "Title",
                "title": "Title",
                "number": "Issue-number",
                "header": "Page-header",
                "footer": "Page-footer",
                "formula": "Formula",
                "image": "Picture",
            }
            normalized = labels.get(str(label).lower(), str(label))
            found.append({"bbox": list(bbox), "label": normalized, "content": str(content)})
        for child in value.values():
            found.extend(_items(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_items(child))
    return found


def parse_result(payload: dict, width: int, height: int) -> list[dict]:
    items = _items(payload)
    if items:
        return items
    # Alcune versioni salvano solo markdownText: resta comunque il risultato
    # ufficiale del modello, senza inventare una griglia lato Tabularium.
    markdown = ""
    for key in ("markdownText", "markdown", "text"):
        if isinstance(payload.get(key), str):
            markdown = payload[key]
            break
    if markdown.strip():
        return [{"bbox": [0, 0, width, height], "label": "Text", "content": markdown}]
    return []


def parse_page(image_source, endpoint: str, model: str, width: int, height: int) -> list[dict]:
    if not paddle_runtime.ready():
        raise RuntimeError("runtime PaddleOCR non pronto: completa prima l'installazione")
    with tempfile.TemporaryDirectory(prefix="tabularium-paddle-", dir=config.ROOT_DIR) as tmp:
        # Pass the accepted master image when the caller has one. This keeps
        # the official pipeline on the same source used by the canvas/crops.
        if isinstance(image_source, Image.Image):
            input_path = Path(tmp) / "input.png"
            image_source.save(input_path, format="PNG")
        else:
            input_path = Path(image_source)
        result = subprocess.run(
            [str(paddle_runtime.python_bin()), "-c", _RUNNER, str(input_path), endpoint, model, tmp],
            capture_output=True,
            text=True,
            timeout=int(config.VLLM_TIMEOUT) + 300,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()[-4000:]
            raise RuntimeError(f"PaddleOCRVL full document fallito: {detail}")
        try:
            hinted = json.loads(result.stdout.strip().splitlines()[-1]).get("path", "")
            payload = json.loads(_find_json(Path(tmp), hinted).read_text(encoding="utf-8"))
        except (OSError, ValueError, IndexError) as exc:
            raise RuntimeError(f"output JSON PaddleOCRVL non valido: {exc}") from exc
    return parse_result(payload, width, height)
