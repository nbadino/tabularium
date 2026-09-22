"""Bridge al pipeline ufficiale PaddleOCRVL.

Il modulo viene importato solo nel worker del runtime Paddle: il backend
principale non deve avere PaddlePaddle/PaddleX tra le dipendenze core.
"""
from __future__ import annotations

import json
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
        bbox = value.get("bbox") or value.get("block_bbox") or value.get("coordinate")
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
            [str(paddle_runtime.python_bin()), "-c", _RUNNER, str(input_path), endpoint, model, tmp],
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
