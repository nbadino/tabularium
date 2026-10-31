"""Configurazione runtime.

Tutti i percorsi sono derivati da env var, mai hard-coded.

- TABULARIUM_ROOT: radice dei dati (progetti, db, crop, runs). Default: <repo>/data
  da un checkout, ~/Tabularium da un'installazione (`uv tool install tabularium`)
- TABULARIUM_HOST / TABULARIUM_PORT: bind del server API
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# --- Risorse e modalità di installazione ---------------------------------------
# Due modi di girare, con le stesse risorse (VERSION, frontend/dist, scripts/,
# conf/) in posti diversi:
# - checkout della repo: le risorse sono nella radice della repo;
# - pacchetto installato (wheel, `uv tool install`): il build le copia in
#   `app/_bundle/`, con lo stesso layout.
# Gli script che il backend consegna alle GPU remote (`scripts/cloud/…`) si
# leggono sempre da qui: il codice eseguito in cloud è quello installato.
_BUNDLE_DIR = Path(__file__).resolve().parent / "_bundle"
_CHECKOUT_DIR = Path(__file__).resolve().parents[2]
INSTALLED: bool = (_BUNDLE_DIR / "VERSION").is_file()
RESOURCE_DIR: Path = _BUNDLE_DIR if INSTALLED else _CHECKOUT_DIR
# Nome storico: resta un alias per chi legge le risorse della repo.
REPO_DIR: Path = RESOURCE_DIR

APP_NAME = "Tabularium"
VERSION = (RESOURCE_DIR / "VERSION").read_text(encoding="utf-8").strip()

# --- Radice dati -------------------------------------------------------------
# Da un checkout i dati stanno accanto al codice; installato, il pacchetto vive
# in un ambiente che un aggiornamento sostituisce, quindi i dati vanno nella
# home dell'utente.
_DEFAULT_ROOT = (Path.home() / "Tabularium") if INSTALLED else (_CHECKOUT_DIR / "data")

# Il .env va caricato dall'app stessa, non solo dai launcher: un avvio che
# bypassa scripts/run_backend.sh (uvicorn diretto, systemd, IDE) altrimenti
# parte senza i segreti — il vault si spegne, ogni credential risulta
# "assente" e la UI perde la chiave API salvata senza un errore visibile.
# `override=False`: le variabili già in ambiente vincono su quelle del file.
# Checkout: `<repo>/.env`. Installato: `.env` nella radice dati, l'unico posto
# che sopravvive a un aggiornamento del pacchetto.
_ENV_ROOT = Path(os.environ.get("TABULARIUM_ROOT", "").strip() or _DEFAULT_ROOT).expanduser()
ENV_FILE: Path = (_ENV_ROOT / ".env") if INSTALLED else (_CHECKOUT_DIR / ".env")
load_dotenv(ENV_FILE, override=False)


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
# Checkout di LLaMA-Factory e PaddleFormers, ognuno con il proprio ambiente:
# sono i toolchain ufficiali di GLM-OCR e PaddleOCR-VL (v. train_recipes.py).
# Un modello non si addestra con il toolchain di un altro: se la cartella
# manca, il preflight lo dice e non genera uno script che fallirà.
LLAMAFACTORY_DIR = os.environ.get("TABULARIUM_LLAMAFACTORY_DIR", "").strip()
PADDLEFORMERS_DIR = os.environ.get("TABULARIUM_PADDLEFORMERS_DIR", "").strip()

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

# Override globale esplicito del tetto pixel. Senza questa env, le ricette
# per-modello scelgono il default: MonkeyOCRv2 usa 1 MP come upstream; gli altri
# modelli mantengono il preprocessore nativo quando la recipe non dichiara un
# tetto. `0` disattiva il limite globale.
_max_pixels_env = os.environ.get("TABULARIUM_VLLM_MAX_PIXELS", "").strip()
VLLM_MAX_PIXELS: int | None = int(_max_pixels_env) if _max_pixels_env else 1_003_520
if VLLM_MAX_PIXELS is not None and VLLM_MAX_PIXELS <= 0:
    VLLM_MAX_PIXELS = None

# --- OCR per pseudo-labeling (rapidocr | paddleocr | auto) ----------------------
OCR_ENGINE = os.environ.get("TABULARIUM_OCR_ENGINE", "auto").strip()

# --- Frontend built --------------------------------------------------------------
FRONTEND_DIST = RESOURCE_DIR / "frontend" / "dist"

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
# Proxies whose X-Forwarded-For uvicorn trusts. The client address keys the login
# rate limit, so behind a proxy that is not on 127.0.0.1 (a Docker bridge gateway,
# another host) list it here, or every client looks like the proxy.
FORWARDED_ALLOW_IPS = os.environ.get("TABULARIUM_FORWARDED_ALLOW_IPS", "127.0.0.1").strip()
SESSION_COOKIE_SECURE = os.environ.get("TABULARIUM_SESSION_COOKIE_SECURE", "0").strip() != "0"
SSH_KNOWN_HOSTS = Path(os.environ.get("TABULARIUM_SSH_KNOWN_HOSTS", "").strip() or (ROOT_DIR / "known_hosts"))
BACKUP_RETENTION = max(1, int(os.environ.get("TABULARIUM_BACKUP_RETENTION", "10")))
VAULT_KEY = os.environ.get("TABULARIUM_VAULT_KEY", "").strip()


def ensure_vault_key() -> None:
    """Installato, nessuno lancia `setup_backend.sh`: la chiave del vault la
    genera il primo avvio, nel `.env` della radice dati (permessi 600).

    Da un checkout non si tocca niente: la genera lo script di setup, come
    sempre. La chiave non si rigenera mai se esiste: cambiarla renderebbe
    illeggibili i credential già salvati.
    """
    global VAULT_KEY
    if not INSTALLED or os.environ.get("TABULARIUM_VAULT_KEY", "").strip():
        return
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    with ENV_FILE.open("a", encoding="utf-8") as fh:
        fh.write(f"\n# Encrypts provider credentials saved from the UI. Keep this file.\nTABULARIUM_VAULT_KEY={key}\n")
    try:
        ENV_FILE.chmod(0o600)
    except OSError:
        pass
    os.environ["TABULARIUM_VAULT_KEY"] = key
    VAULT_KEY = key

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
    # `thumbs/` non è solo cache: l'import di un PDF ci scrive il PNG
    # sorgente (`scan.py`, `pil_img.save(pdf_image_path(page_id))`) prima di
    # confermare la pagina. Senza la cartella l'import muore con
    # `FileNotFoundError` a metà transazione e non registra nulla.
    (ROOT_DIR / "thumbs").mkdir(parents=True, exist_ok=True)
