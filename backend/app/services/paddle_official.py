"""Bridge al pipeline ufficiale PaddleOCRVL.

Il modulo viene importato solo nel worker del runtime Paddle: il backend
principale non deve avere PaddlePaddle/PaddleX tra le dipendenze core.
"""
from __future__ import annotations

import json
import numbers
import subprocess
import tempfile
from pathlib import Path

import requests

from PIL import Image

from .. import config
from . import paddle_runtime


_RUNNER = r'''
import json, sys, warnings
warnings.filterwarnings("ignore")
from paddleocr import PaddleOCRVL

image, url, model, out_dir, options_json, backend = sys.argv[1:]
predict_options = json.loads(options_json)
vl_rec_max_concurrency = predict_options.pop("vl_rec_max_concurrency", None)
constructor_options = {}
if vl_rec_max_concurrency is not None:
    constructor_options["vl_rec_max_concurrency"] = vl_rec_max_concurrency
pipeline = PaddleOCRVL(
    vl_rec_backend=backend,
    vl_rec_server_url=url,
    vl_rec_api_model_name=model,
    use_layout_detection=True,
    **constructor_options,
)
result = next(iter(pipeline.predict(image, **predict_options)))
path = result.save_to_json(save_path=out_dir)
print(json.dumps({"path": str(path) if path else ""}))
'''


def _axis_aligned_bbox(value) -> list | None:
    """Convert Paddle rect/quad/poly geometry to the app's xyxy box."""
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)):
        return None
    if len(value) == 4 and all(isinstance(v, numbers.Real) and not isinstance(v, bool) for v in value):
        return list(value)
    points = value
    if points and all(isinstance(v, numbers.Real) and not isinstance(v, bool) for v in points):
        if len(points) < 6 or len(points) % 2:
            return None
        points = list(zip(points[::2], points[1::2]))
    if not isinstance(points, (list, tuple)) or not points:
        return None
    if any(not isinstance(point, (list, tuple)) or len(point) < 2 for point in points):
        return None
    coordinates = [point[:2] for point in points]
    if any(
        any(not isinstance(coordinate, numbers.Real) or isinstance(coordinate, bool) for coordinate in point)
        for point in coordinates
    ):
        return None
    xs = [point[0] for point in coordinates]
    ys = [point[1] for point in coordinates]
    return [min(xs), min(ys), max(xs), max(ys)]


def _last_meaningful_line(text: str) -> str:
    """L'ultima riga che dice *cosa* è andato storto.

    PaddleX produce decine di righe fra warning, creazione dei modelli e
    traceback: incollarlo tutto nell'errore faceva leggere all'utente un muro
    di interni in cui la causa vera — spesso una sola riga — spariva. Si tiene
    l'ultima eccezione nominata, o l'ultima riga non vuota.
    """
    prefixes = (
        "RuntimeError",
        "ValueError",
        "TypeError",
        "ImportError",
        "ModuleNotFoundError",
        "FileNotFoundError",
        "OSError",
        "ConnectionError",
        "AssertionError",
        "KeyError",
    )
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith(prefixes):
            return line
    return lines[-1] if lines else ""


def _endpoint_alive(url: str, timeout: float = 3.0) -> bool:
    """Il server del modello risponde? Costa tre secondi, non tre minuti."""
    try:
        response = requests.get(url.rstrip("/") + "/models", timeout=timeout)
    except Exception:  # noqa: BLE001 - qualunque errore di trasporto è «non risponde»
        return False
    return response.status_code < 500


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
        bbox = _axis_aligned_bbox(value.get("bbox") or value.get("block_bbox") or value.get("coordinate"))
        label = value.get("label") or value.get("block_label") or value.get("type")
        content = value.get("content") or value.get("block_content") or value.get("text") or ""
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4 and label:
            # PaddleX usa label semantiche minuscole; Tabularium conserva le
            # label pubbliche del parsing (§2.7 di AGENTS.md), che sono quelle
            # che il modello base conosce e che il dataset builder sa
            # tradurre in prompt. Una label fuori tassonomia arriva fino al
            # DB e il report d'export la segnala come «classe senza prompt»:
            # per questo la mappa copre l'intera tassonomia di PP-DocLayout e
            # non solo le voci che capitavano nel primo campione.
            labels = {
                # titoli
                "doc_title": "Title",
                "paragraph_title": "Title",
                "title": "Title",
                # didascalie di figure, tabelle e grafici
                "figure_title": "Caption",
                "chart_title": "Caption",
                "table_title": "Caption",
                # testata e piede
                "number": "Issue-number",
                "header": "Page-header",
                "footer": "Page-footer",
                "footnote": "Footnote",
                "vision_footnote": "Footnote",
                # corpi di testo
                "text": "Text",
                "abstract": "Text",
                "content": "Text",
                "aside_text": "Text",
                "vertical_text": "Text",
                "reference": "List-item",
                # contenuti non testuali
                "table": "Table",
                "formula": "Formula",
                "image": "Picture",
                "figure": "Picture",
                "chart": "Picture",
                "seal": "Picture",
                "header_image": "Picture",
                "footer_image": "Picture",
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
    # PaddleOCRVL 3.7 serializes the same detections twice: the canonical
    # ordered blocks in `parsing_res_list`, and their layout-only projection in
    # `layout_det_res`. Walking the whole JSON tree counts both and duplicates
    # every page region. Prefer the producer's final parsing result whenever
    # it is present; keep the recursive fallback for older PaddleX schemas.
    root = payload.get("res") if isinstance(payload, dict) else None
    if not isinstance(root, dict):
        root = payload
    canonical = root.get("parsing_res_list") if isinstance(root, dict) else None
    items = _items(canonical if isinstance(canonical, list) else root)
    if items:
        return items
    # Alcune versioni salvano solo markdownText: resta comunque il risultato
    # ufficiale del modello, senza inventare una griglia lato Tabularium.
    markdown = ""
    markdown_source = root if isinstance(root, dict) else {}
    for key in ("markdownText", "markdown", "text"):
        if isinstance(markdown_source.get(key), str):
            markdown = markdown_source[key]
            break
    if markdown.strip():
        return [{"bbox": [0, 0, width, height], "label": "Text", "content": markdown}]
    return []


def _predict_options(vl_rec_backend: str = "vllm-server") -> dict:
    """Forward only settings documented by the official PaddleOCR-VL API."""
    from . import model_settings

    effective = model_settings.get_settings("paddleocr-vl")["effective"]
    options = {
        key: value
        for key, value in effective.get("workflow", {}).items()
        if value is not None
    }
    image = effective.get("image", {})
    # Paddle's mlx-vlm-server backend explicitly does not forward min/max
    # pixels to mlx-vlm. Sending them only produces warnings and suggests a
    # tuning effect that the Metal server cannot apply. vLLM supports them via
    # mm_processor_kwargs, so preserve those model-specific controls there.
    if vl_rec_backend != "mlx-vlm-server":
        if image.get("min_pixels") is not None:
            options["min_pixels"] = image["min_pixels"]
        if image.get("max_pixels") is not None:
            options["max_pixels"] = image["max_pixels"]
    generation = effective.get("generation", {})
    for key in ("temperature", "top_p", "repetition_penalty"):
        if generation.get(key) is not None:
            options[key] = generation[key]
    if generation.get("max_tokens") is not None:
        options["max_new_tokens"] = generation["max_tokens"]
    extra_args = {
        key: generation[key]
        for key in ("top_k", "presence_penalty", "frequency_penalty")
        if generation.get(key) is not None
    }
    if extra_args:
        options["vlm_extra_args"] = extra_args
    return options


def parse_page(
    image_source, endpoint: str, model: str, width: int, height: int,
    *, vl_rec_backend: str = "vllm-server",
) -> list[dict]:
    if not paddle_runtime.ready():
        # Due situazioni diverse, due istruzioni diverse: un'installazione in
        # corso chiede di riprovare, un runtime assente dice da dove arriva.
        # Il messaggio unico di prima («completa prima l'installazione»)
        # mandava a completare qualcosa che nessuno aveva iniziato.
        state = paddle_runtime.status().get("state")
        if state == "installing":
            raise RuntimeError(
                "runtime PaddleOCR in preparazione: il percorso ufficiale è "
                "disponibile fra qualche minuto, riprova"
            )
        raise RuntimeError(
            "runtime PaddleOCR non disponibile su questa macchina: il percorso "
            "ufficiale di PaddleOCR-VL lo prepara Tabularium alla prima messa in "
            "servizio del modello (Modelli → Avvia come server locale)"
        )
    # Il server del modello si verifica *prima* di avviare la pipeline: la sua
    # inizializzazione (layout + modelli PaddleX) costa decine di secondi, e
    # senza questo controllo si pagava tutto per poi leggere «Connection error»
    # da dentro un worker.
    if not _endpoint_alive(endpoint):
        raise RuntimeError(
            f"il server del modello non risponde su {endpoint}: avvialo dalla "
            "pagina Modelli (o scegli un provider remoto) e riprova"
        )
    with tempfile.TemporaryDirectory(prefix="tabularium-paddle-", dir=config.ROOT_DIR) as tmp:
        # Pass the accepted master image when the caller has one. This keeps
        # the official pipeline on the same source used by the canvas/crops.
        if isinstance(image_source, Image.Image):
            input_path = Path(tmp) / "input.png"
            image_source.save(input_path, format="PNG")
        else:
            input_path = Path(image_source)
        result = subprocess.run(
            [
                str(paddle_runtime.python_bin()), "-c", _RUNNER,
                str(input_path), endpoint, model, tmp,
                json.dumps(_predict_options(vl_rec_backend), separators=(",", ":")),
                vl_rec_backend,
            ],
            capture_output=True,
            text=True,
            timeout=int(config.VLLM_TIMEOUT) + 300,
        )
        if result.returncode:
            reason = _last_meaningful_line(result.stderr or result.stdout)
            raise RuntimeError(
                f"PaddleOCRVL non ha completato la pagina: {reason}"
                if reason
                else "PaddleOCRVL non ha completato la pagina (nessun dettaglio dal runtime)"
            )
        try:
            hinted = json.loads(result.stdout.strip().splitlines()[-1]).get("path", "")
            payload = json.loads(_find_json(Path(tmp), hinted).read_text(encoding="utf-8"))
        except (OSError, ValueError, IndexError) as exc:
            raise RuntimeError(f"output JSON PaddleOCRVL non valido: {exc}") from exc
    return parse_result(payload, width, height)
