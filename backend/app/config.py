"""Configurazione runtime.

Tutti i percorsi sono derivati da env var, mai hard-coded.

- TABULARIUM_ROOT: radice dei dati (progetti, db, crop, runs). Default: <repo>/data
- TABULARIUM_HOST / TABULARIUM_PORT: bind del server API
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

APP_NAME = "Tabularium"
VERSION = "0.1.0"

# --- Radice del checkout ------------------------------------------------------
# Serve agli script che il backend consegna a macchine remote
# (`scripts/cloud/…`): il codice eseguito in cloud è quello di questo
# checkout, non una copia scaricata altrove.
REPO_DIR: Path = Path(__file__).resolve().parents[2]

# Il .env va caricato dall'app stessa, non solo dai launcher: un avvio che
# bypassa scripts/run_backend.sh (uvicorn diretto, systemd, IDE) altrimenti
# parte senza i segreti — il vault si spegne, ogni credential risulta
# "assente" e la UI perde la chiave API salvata senza un errore visibile.
# `override=False`: le variabili già in ambiente vincono su quelle del file.
load_dotenv(REPO_DIR / ".env", override=False)

# --- Radice dati -------------------------------------------------------------
_DEFAULT_ROOT = REPO_DIR / "data"


def _root() -> Path:
    env = os.environ.get("TABULARIUM_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return _DEFAULT_ROOT.resolve()


def _refresh() -> None:
    global ROOT_DIR, DATA_DIR, DB_PATH
    ROOT_DIR = _root()
    DATA_DIR = ROOT_DIR / "projects"
    DB_PATH = ROOT_DIR / "tabularium.db"


ROOT_DIR: Path = _root()
DATA_DIR: Path = ROOT_DIR / "projects"
DB_PATH: Path = ROOT_DIR / "tabularium.db"
# Pesi dei modelli scaricati dal registro modelli (uno per adapter_id).
MODELS_DIR: Path = Path(
    os.environ.get("TABULARIUM_MODELS_DIR", "").strip() or (ROOT_DIR / "models")
).expanduser()

# --- Server ------------------------------------------------------------------
HOST = os.environ.get("TABULARIUM_HOST", "127.0.0.1")
PORT = int(os.environ.get("TABULARIUM_PORT", "8787"))

# --- Training -----------------------------------------------------------------
# Checkout del repo ufficiale MonkeyOCRv2 (con parsing/train/ms-swift).
TRAIN_REPO = os.environ.get("TABULARIUM_TRAIN_REPO", "").strip()
# Environment dedicato al training (conda) o python eseguibile esplicito.
TRAIN_ENV = os.environ.get("TABULARIUM_TRAIN_ENV", "monkeyocrv2-train").strip()
TRAIN_PYTHON = os.environ.get("TABULARIUM_TRAIN_PYTHON", "").strip()

# --- Serving locale (model_registry.py / serve_manager.py) --------------------
# Python esplicito per servire i modelli generici via `vllm serve` (MinerU2.5,
# dots.ocr, ...). MonkeyOCRv2 non lo usa: il suo `serve_command` delega allo
# script dedicato `scripts/serve_model.sh`, che ha già il proprio env. Vuoto =
# assume che `vllm`/`python` siano già sul PATH del processo backend.
SERVE_PYTHON = os.environ.get("TABULARIUM_SERVE_PYTHON", "").strip()

# DFlash: speculative decoding ufficiale di MonkeyOCRv2 (README, news
# 2026.07.24), dato per "up to 2x faster inference". Il draft (~170 MB) viene
# scaricato alla prima messa in servizio. `0` lo disattiva e riporta il serving
# al comando senza `-d`.
MONKEY_DFLASH = os.environ.get("TABULARIUM_MONKEY_DFLASH", "1").strip() not in {"0", "false", "no"}

# --- Inferenza (vLLM, endpoint OpenAI-compatibile) ------------------------------
VLLM_URL = os.environ.get("TABULARIUM_VLLM_URL", "http://127.0.0.1:8888/v1").strip()
VLLM_MODEL = os.environ.get("TABULARIUM_VLLM_MODEL", "MonkeyOCRv2").strip()
VLLM_API_KEY = os.environ.get("TABULARIUM_VLLM_API_KEY", "").strip()
VLLM_TIMEOUT = int(os.environ.get("TABULARIUM_VLLM_TIMEOUT", "180"))
# Extra headers in formato JSON se specificati (es. per ngrok o custom cloud proxy)
_headers_env = os.environ.get("TABULARIUM_VLLM_EXTRA_HEADERS", "").strip()
try:
    import json
    VLLM_EXTRA_HEADERS = json.loads(_headers_env) if _headers_env else {}
except Exception:
    VLLM_EXTRA_HEADERS = {}

# Tetto ai pixel inviati al modello, equivalente della env `MOCR2_MAX_PIXELS`
# del repo ufficiale. Il default 1.003.520 è quello di `parsing/parse.py`
# (`--max-pixels`), che `configure_runtime()` propaga come `MOCR2_MAX_PIXELS` a
# TUTTE le chiamate (layout, testo, tabella, end2end): con `min_pixels=1003520`
# già imposto dal layout ufficiale, la pagina arriva al VLM esattamente a 1 MP.
# `0` disattiva il tetto e riproduce il comportamento senza `MOCR2_MAX_PIXELS`.
_max_pixels_env = os.environ.get("TABULARIUM_VLLM_MAX_PIXELS", "").strip()
VLLM_MAX_PIXELS: int | None = int(_max_pixels_env) if _max_pixels_env else 1_003_520
if VLLM_MAX_PIXELS is not None and VLLM_MAX_PIXELS <= 0:
    VLLM_MAX_PIXELS = None

# --- OCR per pseudo-labeling (rapidocr | paddleocr | auto) ----------------------
OCR_ENGINE = os.environ.get("TABULARIUM_OCR_ENGINE", "auto").strip()

# --- Frontend built --------------------------------------------------------------
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

# --- Autenticazione (self-hosted) ---------------------------------------------------
# `on` (default): login richiesto per ogni API; il primo avvio mostra la schermata
# di setup che crea l'amministratore. `off`: modalità locale single-user storica,
# nessun controllo (i test usano questa modalità).
AUTH_MODE = os.environ.get("TABULARIUM_AUTH", "on").strip().lower()

# Durata delle sessioni di login in giorni (cookie HttpOnly + session in SQLite).
SESSION_TTL_DAYS = int(os.environ.get("TABULARIUM_SESSION_TTL_DAYS", "30"))

# Nome del cookie di sessione (HttpOnly + SameSite=Strict: la UI è same-origin,
# quindi niente CSRF cross-site; i client API possono usare Authorization: Bearer).
SESSION_COOKIE = os.environ.get("TABULARIUM_SESSION_COOKIE", "tab_session")
# In HTTPS deployments set this to 1. Local HTTP development keeps it off so
# the browser can use the cookie on 127.0.0.1.
SESSION_COOKIE_SECURE = os.environ.get("TABULARIUM_SESSION_COOKIE_SECURE", "0").strip() != "0"
SSH_KNOWN_HOSTS = Path(os.environ.get("TABULARIUM_SSH_KNOWN_HOSTS", "").strip() or (ROOT_DIR / "known_hosts"))
BACKUP_RETENTION = max(1, int(os.environ.get("TABULARIUM_BACKUP_RETENTION", "10")))
VAULT_KEY = os.environ.get("TABULARIUM_VAULT_KEY", "").strip()

# Registrazione chiusa di default: un'istanza self-hosted non deve accettare
# account da chiunque la raggiunga. L'admin la apre dalle Impostazioni.
REGISTRATION_OPEN = os.environ.get("TABULARIUM_REGISTRATION_OPEN", "0").strip() != "0"


def auth_enabled() -> bool:
    """True quando le API richiedono l'autenticazione."""
    return AUTH_MODE == "on"


def ensure_dirs() -> None:
    """Crea le directory necessarie al primo avvio."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
