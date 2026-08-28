"""Tabularium — entry point FastAPI.

Avvio:  uvicorn app.main:app --port 8787
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config
from .api import register_routers
from .db import init_db
from .services.i18n import localize_detail, parse_lang


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title=config.APP_NAME, version=config.VERSION, lifespan=lifespan)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Localizza i dettagli d'errore nella lingua della richiesta."""
    detail = exc.detail
    if isinstance(detail, str) and not request.url.path.startswith("/api"):
        # fuori dalle API (SPA/index) restiamo sul testo storico
        detail = exc.detail
    elif isinstance(detail, str):
        detail = localize_detail(detail, parse_lang(request.headers.get("accept-language")))
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})

# CORS permissivo: app locale a uso singolo/multiutente in LAN.
# In produzione conviene restringere ad origini esplicite.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

register_routers(app)

# --- Frontend built (single-process, M8) --------------------------------------
# Se `frontend/dist` esiste viene servito direttamente dal backend: l'utente
# avvia un solo processo. Route API (registrate prima) restano prioritarie;
# una catch-all serve index.html per i deep-link SPA.
_dist = config.FRONTEND_DIST
if _dist.exists():
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    if (_dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    _index = _dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        # L'HTML non va mai trattenuto dal browser: punta ad asset con nome
        # hashato e senza lui una nuova build resterebbe invisibile (i pulsanti
        # «non compaiono» finché non si forza il refresh).
        no_cache = {"Cache-Control": "no-cache"}
        candidate = _dist / full_path
        if full_path and candidate.is_file() and candidate.is_relative_to(_dist):
            return FileResponse(candidate, headers=no_cache)
        if _index.exists():
            return FileResponse(_index, media_type="text/html", headers=no_cache)
        raise HTTPException(status_code=404, detail="not found")