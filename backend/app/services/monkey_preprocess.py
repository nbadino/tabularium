"""Preprocessore ufficiale di MonkeyOCRv2-Parsing.

`parsing/core_runner.py` del repo ufficiale non manda mai la pagina grezza al
VLM: costruisce un `Preprocessor(model_path)` e passa ogni pagina da
`preprocess_images()` prima del layout e del riconoscimento. È lo stadio che il
CLI ufficiale disattiva con `--skip-preprocess`, documentato come *"this may
lead to worse accuracy but faster speed"*: sono due reti (U2NET per la maschera
di pagina + rettificatore a griglia con `grid_sample`) i cui pesi arrivano già
nel checkpoint, `preprocessor1.pth` e `preprocessor2.pth`.

Perché è un *motore di trasformazione pagina* e non uno stadio nascosto prima
dell'inferenza: il preprocessore cambia la geometria dell'immagine, e i bbox che
il modello restituisce (normalizzati 0–1000) valgono sull'immagine che ha
ricevuto. Applicarlo di nascosto dentro `inference.py` farebbe atterrare i
blocchi su pixel diversi da quelli mostrati nel canvas. Passando invece dal
master di pagina (v. `pages.maybe_auto_deskew`) vale il contratto già esistente
— «prefill, crop ed export osservano gli stessi pixel del canvas» — ed è esatta-
mente ciò che fa il pipeline ufficiale, che salva le pagine preprocessate e da
lì in poi lavora solo su quelle.

Esecuzione: in sottoprocesso nel runtime vLLM (`data/vllm-runtime`), lo stesso
ambiente che esegue `parsing/serve.py`. È l'unico già allineato ai
`parsing/requirements.txt` ufficiali (serve `timm`, che l'ambiente del backend
non ha) e evita di installare torch/timm accanto all'applicazione.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from .. import config
from . import local_runtime, vendor_repos

# Il preprocessore gira di default su CPU: quando il modello è in servizio, vLLM
# tiene il 90% della VRAM (v. `scripts/serve_model.sh`) e non resterebbe spazio.
# Le due reti lavorano su un input 512x512 fisso, quindi il costo su CPU è
# contenuto e indipendente dalla risoluzione della scansione.
DEVICE = os.environ.get("TABULARIUM_MONKEY_PREPROCESS_DEVICE", "cpu").strip() or "cpu"

ADAPTER_ID = "monkeyocrv2-parsing"

_last_error: str | None = None

_SCRIPT = """
import sys
sys.path.insert(0, sys.argv[1])
from PIL import Image
from modeling.modeling_preprocessor import Preprocessor

pre = Preprocessor(sys.argv[2], device=sys.argv[3], batch_size=1)
out = pre.preprocess_image(Image.open(sys.argv[4]))
out.convert("RGB").save(sys.argv[5], format="PNG", compress_level=4)
print("__PREPROCESS_DONE__")
"""


def last_error() -> str | None:
    """Motivo dell'ultimo fallimento, per la diagnostica della trasformazione."""
    return _last_error


def _repo_dir() -> Path:
    configured = config.TRAIN_REPO
    return Path(configured).expanduser() if configured else vendor_repos.monkeyocrv2_repo_dir()


def parsing_dir() -> Path:
    return _repo_dir() / "parsing"


def weights_dir() -> Path:
    """Checkpoint MonkeyOCRv2-Parsing: contiene anche i due pesi del preprocessore."""
    return config.MODELS_DIR / ADAPTER_ID


def python_bin() -> Path | None:
    """Interprete che esegue il preprocessore, con la stessa precedenza del serving."""
    for configured in (config.SERVE_PYTHON, config.TRAIN_PYTHON):
        if configured:
            candidate = Path(configured).expanduser()
            if candidate.exists():
                return candidate
    candidate = local_runtime.python_bin()
    return candidate if candidate.exists() else None


def available() -> bool:
    """Vero solo se ci sono codice ufficiale, pesi e un interprete che li regge."""
    weights = weights_dir()
    return (
        (parsing_dir() / "modeling" / "modeling_preprocessor.py").is_file()
        and (weights / "preprocessor1.pth").is_file()
        and (weights / "preprocessor2.pth").is_file()
        and python_bin() is not None
    )


def preprocess(image: Image.Image, timeout: float = 600.0) -> Image.Image | None:
    """Applica il preprocessore ufficiale a una pagina.

    Ritorna `None` (e valorizza `last_error()`) se l'ambiente non è pronto o il
    sottoprocesso fallisce: il chiamante ripiega, non si blocca.
    """
    global _last_error
    _last_error = None
    if not available():
        _last_error = "monkeyocr_preprocessor_unavailable"
        return None

    interpreter = python_bin()
    tmp = Path(tempfile.mkdtemp(prefix="monkeypre_"))
    try:
        src = tmp / "in.png"
        dst = tmp / "out.png"
        image.convert("RGB").save(src, format="PNG", compress_level=1)
        proc = subprocess.run(
            [
                str(interpreter),
                "-u",
                "-c",
                _SCRIPT,
                str(parsing_dir()),
                str(weights_dir()),
                DEVICE,
                str(src),
                str(dst),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0 or not dst.exists():
            # Le ultime righe bastano a distinguere OOM, peso mancante e
            # dipendenza assente; il traceback intero non serve nell'UI.
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
            _last_error = " / ".join(tail) or f"exit {proc.returncode}"
            return None
        with Image.open(dst) as out:
            return out.convert("RGB").copy()
    except subprocess.TimeoutExpired:
        _last_error = "monkeyocr_preprocessor_timeout"
        return None
    except Exception as exc:  # noqa: BLE001
        _last_error = f"{type(exc).__name__}: {exc}"
        return None
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
