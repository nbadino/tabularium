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
    from .services import trainer
    trainer.reconcile_jobs()
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
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Accept-Language"],
    allow_credentials=True,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'",
    )
    return response

register_routers(app)

# --- Frontend built (single-process, M8) --------------------------------------
# Se `frontend/dist` esiste viene servito direttamente dal backend: l'utente
# avvia un solo processo. Route API (registrate prima) restano prioritarie;
# una catch-all serve index.html per i deep-link SPA.
_dist = config.FRONTEND_DIST
if _dist.exists():
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    if (_dist / "assets").exists():
        from fastapi.staticfiles import StaticFiles as _SF

        class _HashedAssets(_SF):
            """Asset con nome hashato: cache lunghissima e `immutable`. Il
            nome cambia a ogni build, quindi una voce vecchia non può mai
            servire codice nuovo."""

            def file_response(self, *args, **kwargs):  # type: ignore[override]
                resp = super().file_response(*args, **kwargs)
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
                return resp

        app.mount("/assets", _HashedAssets(directory=_dist / "assets"), name="assets")

    _index = _dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        # L'HTML va MAI ripreso dalla cache del browser: punta ad asset con
        # nome hashato e una voce vecchia era la causa del «vecchio JavaScript
        # a ogni build» — `no-store` lo rende impossibile: dopo questa voce
        # installata in cache, niente più hard refresh.
        no_store = {"Cache-Control": "no-store"}
        candidate = _dist / full_path
        if full_path and candidate.is_file() and candidate.is_relative_to(_dist):
            return FileResponse(candidate, headers=no_store)
        if _index.exists():
            return FileResponse(_index, media_type="text/html", headers=no_store)
        raise HTTPException(status_code=404, detail="not found")
