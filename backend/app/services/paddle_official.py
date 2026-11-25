"""Bridge al pipeline ufficiale PaddleOCRVL.

Il modulo viene importato solo nel worker del runtime Paddle: il backend
principale non deve avere PaddlePaddle/PaddleX tra le dipendenze core.
"""
from __future__ import annotations

import atexit
from collections import OrderedDict
import hashlib
import json
import numbers
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import requests

from PIL import Image

from .. import config
from . import paddle_runtime
from .model_labels import PADDLE_LABELS


_DEFAULT_SUBPROCESS_RUN = subprocess.run
_LAYOUT_PREFIX = "__TABULARIUM_LAYOUT__ "


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
    pipeline_version="v1.6",
    **constructor_options,
)
result = next(iter(pipeline.predict(image, **predict_options)))
path = result.save_to_json(save_path=out_dir)
print(json.dumps({"path": str(path) if path else ""}))
'''


# PP-DocLayoutV3 alone, as Xiaomi-OCR-0's region mode runs it
# (`mcp_ocr_server._layout_regions`): the detector's own reading order, no sorting.
_LAYOUT_RUNNER = r"""
import json, os, sys, warnings
warnings.filterwarnings("ignore")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
import numpy as np
from PIL import Image
from paddlex import create_model

image, threshold, model_dir = sys.argv[1], float(sys.argv[2]), sys.argv[3]
# `model_dir`: a fine-tuned export (`best_model/inference`); empty = the official weights.
model = create_model("PP-DocLayoutV3", threshold=threshold, **({"model_dir": model_dir} if model_dir else {}))
result = list(model.predict(np.asarray(Image.open(image).convert("RGB"))))[0]
boxes = result.get("boxes") if hasattr(result, "get") else None
if boxes is None:
    boxes = (result.json.get("res") or result.json).get("boxes", [])
print(json.dumps([
    {"label": str(b.get("label", "")), "coordinate": [float(v) for v in b.get("coordinate", [])]}
    for b in boxes
]))
"""


# Persistent PP-DocLayoutV3 worker: imports PaddleX once, caches detector instances by
# threshold, and reads raw RGB frames over stdin (`width * height * 3` bytes) so pages
# avoid the ~7 s cold subprocess + PNG disk round-trip.
_LAYOUT_DAEMON = r"""
import json, os, sys, warnings
warnings.filterwarnings("ignore")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("FLAGS_minloglevel", "2")
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")

import numpy as np
from paddlex import create_model

PREFIX = "__TABULARIUM_LAYOUT__ "
models = {}
stdin = sys.stdin.buffer
stdout = sys.stdout

def get_model(threshold: float, model_dir: str):
    key = (round(float(threshold), 6), model_dir)
    mdl = models.get(key)
    if mdl is None:
        # `model_dir`: a fine-tuned export (`best_model/inference`); empty = the official weights.
        extra = {"model_dir": model_dir} if model_dir else {}
        try:
            mdl = create_model("PP-DocLayoutV3", threshold=key[0], cpu_threads=4, **extra)
        except TypeError:
            mdl = create_model("PP-DocLayoutV3", threshold=key[0], **extra)
        models[key] = mdl
    return mdl

while True:
    header_line = stdin.readline()
    if not header_line:
        break
    try:
        header = json.loads(header_line.decode("utf-8"))
        width = int(header["width"])
        height = int(header["height"])
        threshold = float(header["threshold"])
        model_dir = str(header.get("model_dir") or "")
        nbytes = int(header["nbytes"])
        buf = bytearray()
        while len(buf) < nbytes:
            chunk = stdin.read(nbytes - len(buf))
            if not chunk:
                raise EOFError("truncated frame on stdin")
            buf.extend(chunk)
        arr = np.frombuffer(buf, dtype=np.uint8).reshape((height, width, 3))
        result = list(get_model(threshold, model_dir).predict(arr))[0]
        boxes = result.get("boxes") if hasattr(result, "get") else None
        if boxes is None:
            boxes = (result.json.get("res") or result.json).get("boxes", [])
        out = [
            {"label": str(b.get("label", "")), "coordinate": [float(v) for v in b.get("coordinate", [])]}
            for b in boxes
        ]
        stdout.write(PREFIX + json.dumps({"ok": True, "boxes": out}) + "\n")
        stdout.flush()
    except EOFError:
        break
    except Exception as exc:
        stdout.write(PREFIX + json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}) + "\n")
        stdout.flush()
"""

_LAYOUT_POOL_COND = threading.Condition()
_IDLE_LAYOUT_WORKERS: list[subprocess.Popen] = []
_ALL_LAYOUT_WORKERS: list[subprocess.Popen] = []
_WORKER_REQUEST_COUNTS: dict[int, int] = {}
_BUSY_LAYOUT_WORKERS = 0
_MAX_LAYOUT_WORKERS = 1
_LAYOUT_WORKER_MAX_REQUESTS = 50
_LAYOUT_CACHE_LOCK = threading.Lock()
_LAYOUT_CACHE: OrderedDict[tuple[int, int, float, str, str], list[dict]] = OrderedDict()
_LAYOUT_CACHE_MAX = 8
# One install attempt per process: when it does not give a working runtime,
# retrying on every page would rebuild the environment hundreds of times.
_INSTALL_ATTEMPTED = False

# `import paddle` on a CPU without the instructions its wheel was built for.
_ILLEGAL_INSTRUCTION = (-4, 132)


def _require_runtime() -> None:
    """Make sure the PaddleOCR runtime is installed, installing it if needed.

    The official path runs the Paddle pipeline (PP-DocLayoutV3 and the PaddleX
    parser) on this machine even when the VLM is remote (a rented GPU behind a
    tunnel, a custom endpoint). Installing it only when the model is downloaded
    or served here left those users with every page failing; the first page now
    waits for the install instead. `ensure_ready` is serialised, so the pages
    that follow wait for the same install rather than starting another one.
    """
    global _INSTALL_ATTEMPTED
    reason = paddle_runtime.cpu_unsupported_reason()
    if reason:
        raise RuntimeError(reason)
    if paddle_runtime.ready():
        return
    if _INSTALL_ATTEMPTED:
        raise RuntimeError(
            "runtime PaddleOCR non utilizzabile dopo l'installazione: vedi il log di "
            "installazione nella pagina Modelli"
        )
    _INSTALL_ATTEMPTED = True
    try:
        paddle_runtime.ensure_ready()
    except Exception as exc:  # noqa: BLE001 - the install error is the useful message
        raise RuntimeError(f"runtime PaddleOCR non installabile su questa macchina: {exc}") from exc
    if not paddle_runtime.ready(force=True):
        raise RuntimeError(
            "runtime PaddleOCR installato ma non utilizzabile: vedi il log di installazione "
            "nella pagina Modelli"
        )


def _serialize_layout_boxes(boxes: list[dict], width: int, height: int) -> list[dict]:
    regions = []
    for box in boxes:
        if len(box.get("coordinate") or []) != 4:
            continue
        x1, y1, x2, y2 = box["coordinate"]
        x1, x2 = (max(0.0, min(v, float(width))) for v in (x1, x2))
        y1, y2 = (max(0.0, min(v, float(height))) for v in (y1, y2))
        if x2 <= x1 or y2 <= y1:
            continue
        regions.append({
            "label": box["label"],
            "bbox": [
                int(x1 / width * 1000),
                int(y1 / height * 1000),
                int(x2 / width * 1000),
                int(y2 / height * 1000),
            ],
        })
    return regions


def _detect_layout_oneshot(image: Image.Image, *, threshold: float, model_dir: str | None = None) -> list[dict]:
    width, height = image.size
    with tempfile.TemporaryDirectory(prefix="tabularium-layout-", dir=config.ROOT_DIR) as tmp:
        path = Path(tmp) / "page.png"
        image.convert("RGB").save(path, format="PNG")
        result = subprocess.run(
            [str(paddle_runtime.python_bin()), "-c", _LAYOUT_RUNNER, str(path), str(threshold), model_dir or ""],
            capture_output=True, text=True, timeout=int(config.VLLM_TIMEOUT) + 300,
        )
    if result.returncode:
        raise _runner_failure(result, "PP-DocLayoutV3 non ha completato la pagina")
    try:
        boxes = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f"output PP-DocLayoutV3 non valido: {exc}") from exc
    return _serialize_layout_boxes(boxes, width, height)


def _terminate_proc_quietly(proc: subprocess.Popen) -> None:
    try:
        if proc.stdin is not None:
            proc.stdin.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=3.0)
    except Exception:  # noqa: BLE001
        try:
            kill = getattr(proc, "kill", None)
            if callable(kill):
                kill()
        except Exception:  # noqa: BLE001
            pass


def _shutdown_layout_worker() -> None:
    """Terminate all persistent PP-DocLayoutV3 worker subprocesses if running."""
    with _LAYOUT_POOL_COND:
        procs = list(_ALL_LAYOUT_WORKERS)
        _IDLE_LAYOUT_WORKERS.clear()
        _ALL_LAYOUT_WORKERS.clear()
        _WORKER_REQUEST_COUNTS.clear()
    for proc in procs:
        _terminate_proc_quietly(proc)


def _clear_layout_cache() -> None:
    with _LAYOUT_CACHE_LOCK:
        _LAYOUT_CACHE.clear()


atexit.register(_shutdown_layout_worker)


def _spawn_layout_worker() -> subprocess.Popen:
    return subprocess.Popen(
        [str(paddle_runtime.python_bin()), "-u", "-c", _LAYOUT_DAEMON],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _acquire_layout_worker() -> subprocess.Popen:
    global _BUSY_LAYOUT_WORKERS
    to_reap: list[subprocess.Popen] = []
    spawn_new = False
    proc: subprocess.Popen | None = None
    with _LAYOUT_POOL_COND:
        while True:
            while _IDLE_LAYOUT_WORKERS:
                cand = _IDLE_LAYOUT_WORKERS.pop(0)
                if cand.poll() is None:
                    _BUSY_LAYOUT_WORKERS += 1
                    proc = cand
                    break
                if cand in _ALL_LAYOUT_WORKERS:
                    _ALL_LAYOUT_WORKERS.remove(cand)
                _WORKER_REQUEST_COUNTS.pop(id(cand), None)
                to_reap.append(cand)
            if proc is not None:
                break
            if _BUSY_LAYOUT_WORKERS < _MAX_LAYOUT_WORKERS:
                _BUSY_LAYOUT_WORKERS += 1
                spawn_new = True
                break
            _LAYOUT_POOL_COND.wait()
    for dead in to_reap:
        _terminate_proc_quietly(dead)
    if spawn_new:
        try:
            proc = _spawn_layout_worker()
            with _LAYOUT_POOL_COND:
                _ALL_LAYOUT_WORKERS.append(proc)
        except BaseException:
            with _LAYOUT_POOL_COND:
                _BUSY_LAYOUT_WORKERS = max(0, _BUSY_LAYOUT_WORKERS - 1)
                _LAYOUT_POOL_COND.notify()
            raise
    assert proc is not None
    return proc


def _release_layout_worker(
    proc: subprocess.Popen,
    *,
    healthy: bool,
    request_ran: bool = False,
) -> None:
    global _BUSY_LAYOUT_WORKERS
    reap = False
    with _LAYOUT_POOL_COND:
        _BUSY_LAYOUT_WORKERS = max(0, _BUSY_LAYOUT_WORKERS - 1)
        reqs = _WORKER_REQUEST_COUNTS.get(id(proc), 0)
        if request_ran:
            reqs += 1
            _WORKER_REQUEST_COUNTS[id(proc)] = reqs
        recycle = reqs >= _LAYOUT_WORKER_MAX_REQUESTS
        if healthy and not recycle and proc.poll() is None:
            _IDLE_LAYOUT_WORKERS.append(proc)
        else:
            _WORKER_REQUEST_COUNTS.pop(id(proc), None)
            if proc in _ALL_LAYOUT_WORKERS:
                _ALL_LAYOUT_WORKERS.remove(proc)
            reap = True
        _LAYOUT_POOL_COND.notify()
    if reap:
        _terminate_proc_quietly(proc)


def _query_layout_worker_locked(
    proc: subprocess.Popen, width: int, height: int, threshold: float, raw_rgb: bytes, model_dir: str | None = None,
) -> list[dict]:
    header = (
        json.dumps({
            "width": width, "height": height, "threshold": float(threshold),
            "model_dir": model_dir or "", "nbytes": len(raw_rgb),
        }) + "\n"
    ).encode("utf-8")
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(header)
    proc.stdin.write(raw_rgb)
    proc.stdin.flush()

    payload: dict | None = None
    while True:
        raw_line = proc.stdout.readline()
        if not raw_line:
            rc = proc.poll()
            stderr_text = ""
            if proc.stderr is not None:
                try:
                    stderr_text = proc.stderr.read().decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    stderr_text = ""
            raise EOFError(stderr_text or f"worker exited with code {rc}")
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        if line.startswith(_LAYOUT_PREFIX):
            payload = json.loads(line[len(_LAYOUT_PREFIX) :])
            break
        if line.startswith("{") and line.endswith("}"):
            try:
                candidate = json.loads(line)
                if isinstance(candidate, dict) and "ok" in candidate:
                    payload = candidate
                    break
            except ValueError:
                pass

    if not payload or not payload.get("ok"):
        err = (payload or {}).get("error") or "unknown error"
        raise RuntimeError(f"PP-DocLayoutV3 non ha completato la pagina: {err}")
    return payload.get("boxes") or []


def detect_layout(image: Image.Image, *, threshold: float, model_dir: str | None = None) -> list[dict]:
    """PP-DocLayoutV3 regions as `{label, bbox}` with the bbox in 0-1000 integers.

    `model_dir` loads a fine-tuned export instead of the official weights.

    The scaling is the vendor's (`layout_detect_paddlex.serialize_regions`): clamp to the
    image, drop empty boxes, `int(x / width * 1000)`. Uses a persistent warm worker with
    raw RGB framing and a bounded in-memory LRU cache; falls back transparently to
    `subprocess.run` when tests monkeypatch `paddle_official.subprocess.run`.
    """
    _require_runtime()
    if subprocess.run is not _DEFAULT_SUBPROCESS_RUN:
        return _detect_layout_oneshot(image, threshold=threshold, model_dir=model_dir)

    rgb = image if image.mode == "RGB" else image.convert("RGB")
    width, height = rgb.size
    raw_rgb = rgb.tobytes()
    t_key = round(float(threshold), 6)
    digest = hashlib.blake2b(raw_rgb, digest_size=16).hexdigest()
    cache_key = (width, height, t_key, model_dir or "", digest)

    with _LAYOUT_CACHE_LOCK:
        cached = _LAYOUT_CACHE.get(cache_key)
        if cached is not None:
            _LAYOUT_CACHE.move_to_end(cache_key)
            return [{"label": r["label"], "bbox": list(r["bbox"])} for r in cached]

    boxes: list[dict] | None = None
    last_exc: Exception | None = None
    for _attempt in range(2):
        proc = _acquire_layout_worker()
        healthy = False
        request_ran = False
        try:
            boxes = _query_layout_worker_locked(proc, width, height, threshold, raw_rgb, model_dir)
            healthy = True
            request_ran = True
            break
        except RuntimeError:
            healthy = True
            request_ran = True
            raise
        except Exception as exc:  # noqa: BLE001 - respawn once on broken pipe / exit
            last_exc = exc
        finally:
            _release_layout_worker(proc, healthy=healthy, request_ran=request_ran)

    if boxes is None:
        return _detect_layout_oneshot(rgb, threshold=threshold, model_dir=model_dir) if last_exc is not None else []

    regions = _serialize_layout_boxes(boxes, width, height)
    with _LAYOUT_CACHE_LOCK:
        _LAYOUT_CACHE[cache_key] = [{"label": r["label"], "bbox": list(r["bbox"])} for r in regions]
        while len(_LAYOUT_CACHE) > _LAYOUT_CACHE_MAX:
            _LAYOUT_CACHE.popitem(last=False)
    return regions


def prefetch_layout(image: Image.Image, *, threshold: float, model_dir: str | None = None) -> None:
    """Warm the layout cache on a background thread while the GPU is busy."""
    if subprocess.run is not _DEFAULT_SUBPROCESS_RUN:
        return
    rgb = (image if image.mode == "RGB" else image.convert("RGB")).copy()

    def _run() -> None:
        try:
            detect_layout(rgb, threshold=threshold, model_dir=model_dir)
        except Exception:  # noqa: BLE001 - best-effort prefetch only
            pass

    threading.Thread(target=_run, name="paddle-layout-prefetch", daemon=True).start()


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


def _runner_failure(result: subprocess.CompletedProcess, what: str) -> RuntimeError:
    """The error for a runtime subprocess that failed, with its cause when known."""
    if result.returncode in _ILLEGAL_INSTRUCTION:
        return RuntimeError(
            paddle_runtime.cpu_unsupported_reason()
            or f"{what}: la CPU non supporta le istruzioni richieste da PaddlePaddle (illegal instruction)"
        )
    reason = _last_meaningful_line(result.stderr or result.stdout)
    return RuntimeError(f"{what}: {reason}" if reason else f"{what} (nessun dettaglio dal runtime)")


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
            # non solo le voci che capitavano nel primo campione. La mappa vive
            # in `model_labels.py` (fonte unica, condivisa con le classi note).
            labels = PADDLE_LABELS
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
    # The model server is checked first: installing the runtime for a server
    # that does not answer would only delay the error.
    if not _endpoint_alive(endpoint):
        raise RuntimeError(
            f"il server del modello non risponde su {endpoint}: avvialo dalla "
            "pagina Modelli (o scegli un provider remoto) e riprova"
        )
    _require_runtime()
    # The pipeline itself starts only now: its initialisation (layout and the
    # PaddleX models) costs tens of seconds, wasted if the server were down.
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
            raise _runner_failure(result, "PaddleOCRVL non ha completato la pagina")
        try:
            hinted = json.loads(result.stdout.strip().splitlines()[-1]).get("path", "")
            payload = json.loads(_find_json(Path(tmp), hinted).read_text(encoding="utf-8"))
        except (OSError, ValueError, IndexError) as exc:
            raise RuntimeError(f"output JSON PaddleOCRVL non valido: {exc}") from exc
    return parse_result(payload, width, height)
