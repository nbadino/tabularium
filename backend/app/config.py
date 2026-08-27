"""Configurazione runtime.

Tutti i percorsi sono derivati da env var, mai hard-coded.

- LLOYDS_ROOT: radice dei dati (progetti, db, crop, runs). Default: <repo>/data
- LLOYDS_HOST / LLOYDS_PORT: bind del server API
"""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "Lloyds Lab"
VERSION = "0.1.0"

# --- Radice dati -------------------------------------------------------------
_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data"


def _root() -> Path:
    env = os.environ.get("LLOYDS_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return _DEFAULT_ROOT.resolve()


def _refresh() -> None:
    global ROOT_DIR, DATA_DIR, DB_PATH
    ROOT_DIR = _root()
    DATA_DIR = ROOT_DIR / "projects"
    DB_PATH = ROOT_DIR / "lloyds.db"


ROOT_DIR: Path = _root()
DATA_DIR: Path = ROOT_DIR / "projects"
DB_PATH: Path = ROOT_DIR / "lloyds.db"

# --- Server ------------------------------------------------------------------
HOST = os.environ.get("LLOYDS_HOST", "127.0.0.1")
PORT = int(os.environ.get("LLOYDS_PORT", "8787"))

# --- Training -----------------------------------------------------------------
# Checkout del repo ufficiale MonkeyOCRv2 (con parsing/train/ms-swift).
TRAIN_REPO = os.environ.get("LLOYDS_TRAIN_REPO", "").strip()
# Environment dedicato al training (conda) o python eseguibile esplicito.
TRAIN_ENV = os.environ.get("LLOYDS_TRAIN_ENV", "monkeyocrv2-train").strip()
TRAIN_PYTHON = os.environ.get("LLOYDS_TRAIN_PYTHON", "").strip()

# --- Inferenza (vLLM, endpoint OpenAI-compatibile) ------------------------------
VLLM_URL = os.environ.get("LLOYDS_VLLM_URL", "http://127.0.0.1:8888/v1").strip()
VLLM_MODEL = os.environ.get("LLOYDS_VLLM_MODEL", "MonkeyOCRv2").strip()
VLLM_API_KEY = os.environ.get("LLOYDS_VLLM_API_KEY", "").strip()
VLLM_TIMEOUT = int(os.environ.get("LLOYDS_VLLM_TIMEOUT", "180"))
# Extra headers in formato JSON se specificati (es. per ngrok o custom cloud proxy)
_headers_env = os.environ.get("LLOYDS_VLLM_EXTRA_HEADERS", "").strip()
try:
    import json
    VLLM_EXTRA_HEADERS = json.loads(_headers_env) if _headers_env else {}
except Exception:
    VLLM_EXTRA_HEADERS = {}

# Tetto opzionale ai pixel inviati al modello, equivalente della env
# `MOCR2_MAX_PIXELS` del repo ufficiale. Non impostato = nessuna riduzione,
# che è il comportamento di default di `parsing/core_runner.py`.
VLLM_MAX_PIXELS = (
    int(os.environ["LLOYDS_VLLM_MAX_PIXELS"])
    if os.environ.get("LLOYDS_VLLM_MAX_PIXELS", "").strip()
    else None
)

# --- OCR per pseudo-labeling (rapidocr | paddleocr | auto) ----------------------
OCR_ENGINE = os.environ.get("LLOYDS_OCR_ENGINE", "auto").strip()

# --- Frontend built --------------------------------------------------------------
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def ensure_dirs() -> None:
    """Crea le directory necessarie al primo avvio."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)